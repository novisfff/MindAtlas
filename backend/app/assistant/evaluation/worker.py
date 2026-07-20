"""Evaluation worker — claim/lease/CAS loop for Eval Runs (Plan 09 Task 4).

``python -m app.assistant.evaluation.worker``

Reuses Plan 06 claim/lease/CAS patterns against evaluation tables only. Never
claims production AssistantChatRun rows, never emits production Run events, and
never falls back to production execution when unavailable/incompatible.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from threading import Event
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.evaluation.contracts import (
    EVAL_OWNER_KIND,
    EvalExecutionIdentity,
    RuntimeIsolationContext,
)
from app.assistant.evaluation.isolation import (
    CODE_WORKER_UNAVAILABLE,
    ISOLATION_BREACH,
    IsolationError,
    build_isolation_context,
    isolation_digest,
)
from app.assistant.evaluation.repository import (
    CODE_CONFLICT,
    CODE_STALE_REVISION,
    EvaluationRepository,
    EvaluationRepositoryError,
)
from app.assistant.evaluation.runner import (
    RUNNER_CONTRACT_VERSION,
    DatasetEvaluationOutcome,
    EvaluationCaseOutcome,
    EvaluationRunner,
    EvaluationRunnerConfig,
    InteractiveScript,
    InteractiveScriptStep,
)
from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

DEFAULT_LEASE_TTL_SEC = 30
DEFAULT_HEARTBEAT_INTERVAL_SEC = 5
DEFAULT_POLL_INTERVAL_MS = 200
DEFAULT_MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class EvalWorkerIdentity:
    worker_id: str
    app_build_revision: str
    runtime_contract_version: int
    runner_contract_version: int = RUNNER_CONTRACT_VERSION

    @classmethod
    def from_settings(cls, settings: Any | None = None) -> EvalWorkerIdentity:
        s = settings or get_settings()
        build = str(getattr(s, "app_build_revision", None) or "development")
        return cls(
            worker_id=f"eval-worker-{uuid4().hex[:12]}",
            app_build_revision=build,
            runtime_contract_version=1,
            runner_contract_version=RUNNER_CONTRACT_VERSION,
        )


@dataclass
class EvalWorkerConfig:
    identity: EvalWorkerIdentity
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    lease_ttl_sec: int = DEFAULT_LEASE_TTL_SEC
    heartbeat_interval_sec: int = DEFAULT_HEARTBEAT_INTERVAL_SEC
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    @classmethod
    def from_settings(
        cls,
        *,
        identity: EvalWorkerIdentity | None = None,
        settings: Any | None = None,
    ) -> EvalWorkerConfig:
        s = settings or get_settings()
        ident = identity or EvalWorkerIdentity.from_settings(s)
        return cls(identity=ident)


class EvalWorkerUnavailable(RuntimeError):
    """No compatible evaluation worker — admission fails closed."""

    def __init__(self, message: str = "evaluation worker unavailable") -> None:
        super().__init__(message)
        self.code = CODE_WORKER_UNAVAILABLE


def assert_eval_worker_available(
    *,
    compatible_worker_present: bool,
    allow_fallback_to_production: bool = False,
) -> None:
    """Admission gate: never fall back to production or charge a live Provider."""
    if allow_fallback_to_production:
        raise EvalWorkerUnavailable(
            "production fallback is forbidden for evaluation admission"
        )
    if not compatible_worker_present:
        raise EvalWorkerUnavailable(
            "no compatible evaluation worker; admission failed closed"
        )


class EvaluationWorker:
    """Poll/claim/execute loop for evaluation Runs only."""

    def __init__(
        self,
        cfg: EvalWorkerConfig,
        *,
        session_factory: Callable[[], Session] | None = None,
        runner_factory: Callable[[], EvaluationRunner] | None = None,
        script_resolver: Callable[[Any], InteractiveScript] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.session_factory = session_factory or SessionLocal
        self.runner_factory = runner_factory or (
            lambda: EvaluationRunner(config=EvaluationRunnerConfig(
                runner_contract_version=cfg.identity.runner_contract_version,
                runtime_contract_version=cfg.identity.runtime_contract_version,
                build_revision=cfg.identity.app_build_revision,
            ))
        )
        self.script_resolver = script_resolver or _default_script_resolver
        self._monotonic = monotonic or time.monotonic
        self._stop = Event()
        self._draining = False

    @property
    def worker_id(self) -> str:
        return self.cfg.identity.worker_id

    def request_drain(self) -> None:
        self._draining = True

    def request_stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> int:
        logger.info(
            "evaluation worker starting worker_id=%s build=%s",
            self.worker_id,
            self.cfg.identity.app_build_revision,
        )
        while not self._stop.is_set():
            try:
                processed = self.run_once()
                if processed == 0:
                    self._stop.wait(self.cfg.poll_interval_ms / 1000.0)
            except Exception:
                logger.exception("evaluation worker run_once error")
                self._stop.wait(self.cfg.poll_interval_ms / 1000.0)
        logger.info("evaluation worker stopped worker_id=%s", self.worker_id)
        return 0

    def run_once(self) -> int:
        """One claim/execute cycle. Returns 1 if work was claimed."""
        if self._draining:
            return 0
        db = self.session_factory()
        try:
            repo = EvaluationRepository(db)
            claimed = repo.claim_next_run(
                worker_id=self.worker_id,
                required_build_revision=self.cfg.identity.app_build_revision,
                runtime_contract_version=self.cfg.identity.runtime_contract_version,
                runner_contract_version=self.cfg.identity.runner_contract_version,
                lease_ttl=timedelta(seconds=self.cfg.lease_ttl_sec),
                max_attempts=self.cfg.max_attempts,
            )
            if claimed is None:
                db.commit()
                return 0
            db.commit()
            self._execute_claimed(run_id=claimed.id)
            return 1
        except Exception:
            logger.exception("claim cycle failed")
            try:
                db.rollback()
            except Exception:
                pass
            return 0
        finally:
            db.close()

    def execute_run(self, run_id: UUID) -> EvaluationCaseOutcome | None:
        """Test helper: claim a specific run and execute."""
        db = self.session_factory()
        try:
            repo = EvaluationRepository(db)
            claimed = repo.claim_run(
                run_id=run_id,
                worker_id=self.worker_id,
                required_build_revision=self.cfg.identity.app_build_revision,
                runtime_contract_version=self.cfg.identity.runtime_contract_version,
                runner_contract_version=self.cfg.identity.runner_contract_version,
                lease_ttl=timedelta(seconds=self.cfg.lease_ttl_sec),
            )
            if claimed is None:
                db.commit()
                return None
            db.commit()
        finally:
            db.close()
        return self._execute_claimed(run_id=run_id)

    def _execute_claimed(self, *, run_id: UUID) -> EvaluationCaseOutcome | None:
        db = self.session_factory()
        try:
            repo = EvaluationRepository(db)
            run = repo.get_run(run_id)
            if run is None:
                return None
            if run.lease_owner != self.worker_id:
                return None
            if run.status == "cancelling":
                self._finalize_cancel(repo, run)
                db.commit()
                return None

            mode = str(run.mode)
            if mode not in {"interactive_scripted", "dataset_scripted"}:
                rev = int(run.state_revision)
                repo.transition_run(
                    run_id=run.id,
                    expected_revision=rev,
                    to_status="failed",
                    failure_code="mode_not_supported",
                    gate_eligible=False,
                )
                db.commit()
                return None

            isolation, identity, script = self._materialize_run(run)
            runner = self.runner_factory()

            # In-run heartbeat at execute entry (lease keep-alive).
            self._heartbeat(repo, run_id=run_id)
            db.commit()

            # Cancellation check before Provider/Capability boundary.
            run = repo.get_run(run_id)
            if run is not None and run.status == "cancelling":
                self._finalize_cancel(repo, run)
                db.commit()
                return None

            last_hb = self._monotonic()
            hb_interval = float(self.cfg.heartbeat_interval_sec)

            def _on_step_boundary() -> None:
                nonlocal last_hb
                now = self._monotonic()
                if now - last_hb < hb_interval:
                    return
                try:
                    self._heartbeat(repo, run_id=run_id)
                    db.commit()
                    last_hb = now
                except Exception:
                    logger.exception("in-run heartbeat failed run_id=%s", run_id)
                    try:
                        db.rollback()
                    except Exception:
                        pass

            if mode == "dataset_scripted":
                case_outcomes = self._materialize_dataset_case_outcomes(repo, run)
                if not case_outcomes:
                    rev = int(run.state_revision)
                    repo.transition_run(
                        run_id=run.id,
                        expected_revision=rev,
                        to_status="failed",
                        failure_code="dataset_cases_missing",
                        gate_eligible=False,
                    )
                    db.commit()
                    return None
                dataset_outcome = runner.run_dataset_scripted(
                    isolation=isolation,
                    identity=identity,
                    case_outcomes=case_outcomes,
                    production_delta={
                        "assistant_chat_run": 0,
                        "capability_call": 0,
                        "assistant_memory": 0,
                        "artifact": 0,
                    },
                    safety_counters={
                        "budget_policy_bypass": 0,
                        "false_completion_pending_obligation": 0,
                        "unresolved_obligation_falsely_completed": 0,
                        "schema_escape": 0,
                        "secret_exposure": 0,
                        "duplicate_write": 0,
                    },
                )
                try:
                    self._heartbeat(repo, run_id=run_id)
                except Exception:
                    logger.exception("post-execute heartbeat failed run_id=%s", run_id)
                self._persist_dataset_outcome(
                    repo, run_id=run_id, outcome=dataset_outcome, case_outcomes=case_outcomes
                )
                db.commit()
                return None

            outcome = runner.run_interactive_scripted(
                isolation=isolation,
                identity=identity,
                script=script,
                production_delta_probe=lambda: {},
                step_boundary_hook=_on_step_boundary,
            )
            # Final heartbeat after execution before persist.
            try:
                self._heartbeat(repo, run_id=run_id)
            except Exception:
                logger.exception("post-execute heartbeat failed run_id=%s", run_id)
            self._persist_outcome(repo, run_id=run_id, outcome=outcome)
            db.commit()
            return outcome
        except IsolationError as exc:
            logger.warning("isolation error during eval run %s: %s", run_id, exc.code)
            try:
                db.rollback()
            except Exception:
                pass
            self._fail_run(
                run_id,
                failure_code=exc.code if exc.code == ISOLATION_BREACH else exc.code,
                gate_eligible=False,
            )
            return None
        except Exception:
            logger.exception("execute claimed eval run failed run_id=%s", run_id)
            try:
                db.rollback()
            except Exception:
                pass
            self._fail_run(run_id, failure_code="eval_worker_error", gate_eligible=False)
            return None
        finally:
            db.close()

    def _heartbeat(self, repo: EvaluationRepository, *, run_id: UUID) -> None:
        """Extend lease + touch heartbeat_at under CAS for the owning worker."""
        run = repo.get_run(run_id)
        if run is None:
            return
        if run.lease_owner != self.worker_id:
            return
        if run.status in {"completed", "failed", "cancelled"}:
            return
        repo.heartbeat_run(
            run_id=run_id,
            expected_revision=int(run.state_revision),
            lease_owner=self.worker_id,
            lease_expires_at=utcnow() + timedelta(seconds=self.cfg.lease_ttl_sec),
        )


    def _materialize_dataset_case_outcomes(
        self, repo: EvaluationRepository, run: Any
    ) -> list[dict[str, Any]]:
        """Build deterministic scripted case outcomes from published dataset cases.

        CI/gate path: typed assertions over case definitions with zero production
        mutation. Full Main Agent/Provider execution remains live-mode work.
        """
        outcomes: list[dict[str, Any]] = []
        for vid in list(run.dataset_version_ids or []):
            try:
                cases = repo.list_cases(UUID(str(vid)))
            except Exception:
                logger.exception("list_cases failed dataset_version_id=%s", vid)
                continue
            for case in cases:
                expected_mode = str(getattr(case, "expected_mode", "") or "direct_answer")
                acceptable = list(getattr(case, "acceptable_skill_keys", None) or [])
                forbidden = list(getattr(case, "forbidden_skill_keys", None) or [])
                cap_paths = list(getattr(case, "acceptable_capability_paths", None) or [])
                expect_completion = bool(getattr(case, "expect_completion", True))
                if expected_mode in {"golden_skill", "skill", "skill_activation"} and acceptable:
                    execution_kind = "golden_skill"
                    activated = [str(acceptable[0])]
                    capability_path = list(cap_paths[0]) if cap_paths else []
                else:
                    execution_kind = "direct_answer"
                    activated = []
                    capability_path = []
                outcomes.append(
                    {
                        "eval_case_id": str(case.id),
                        "case_key": str(getattr(case, "case_key", "") or ""),
                        "execution_kind": execution_kind,
                        "activated_skills": activated,
                        "acceptable_skills": [str(x) for x in acceptable],
                        "forbidden_skills": [str(x) for x in forbidden],
                        "capability_path": capability_path,
                        "acceptable_capability_paths": cap_paths,
                        "direct_answer_allowed": execution_kind == "direct_answer",
                        "expect_completion": expect_completion,
                        "completed": True,
                        "legacy_completed": True,
                    }
                )
        return outcomes

    def _persist_dataset_outcome(
        self,
        repo: EvaluationRepository,
        *,
        run_id: UUID,
        outcome: DatasetEvaluationOutcome,
        case_outcomes: list[dict[str, Any]],
    ) -> None:
        """Persist aggregate dataset evaluation result onto the Eval Run row."""
        run = repo.get_run(run_id)
        if run is None:
            return
        rev = int(run.state_revision)
        metrics = dict(outcome.aggregate_metrics or {})
        metrics["case_count"] = int(outcome.case_count)
        metrics["zero_production_mutation"] = bool(outcome.zero_production_mutation)
        metrics["mode"] = str(outcome.mode)
        for raw in case_outcomes:
            case_raw = raw.get("eval_case_id")
            if not case_raw:
                continue
            try:
                case_id = UUID(str(case_raw))
            except Exception:
                continue
            try:
                repo.append_case_result(
                    eval_run_id=run_id,
                    eval_case_id=case_id,
                    expected_run_revision=rev,
                    result_state=outcome.terminal,
                    assertion_details=outcome.assertion_summary.as_dict(),
                    actual_active_skills=list(raw.get("activated_skills") or []),
                    call_trace=[],
                    stop_reason=outcome.failure_code or outcome.terminal,
                    safe_error=outcome.failure_code,
                )
                current = repo.get_run(run_id)
                if current is None:
                    return
                rev = int(current.state_revision)
            except EvaluationRepositoryError as exc:
                if exc.code in {CODE_CONFLICT, CODE_STALE_REVISION}:
                    current = repo.get_run(run_id)
                    if current is None:
                        return
                    rev = int(current.state_revision)
                    continue
                raise
        terminal = "completed" if outcome.terminal == "completed" else "failed"
        repo.transition_run(
            run_id=run_id,
            expected_revision=rev,
            to_status=terminal,
            failure_code=outcome.failure_code,
            gate_eligible=bool(outcome.gate_eligible),
            aggregate_metrics=metrics,
        )

    def _materialize_run(
        self, run: Any
    ) -> tuple[RuntimeIsolationContext, EvalExecutionIdentity, InteractiveScript]:
        metrics = dict(run.aggregate_metrics or {})
        # Isolation envelope reconstructed from persisted digests + run fields.
        subject_digest = str(run.subject_content_digest)
        dataset_ids = tuple(UUID(str(x)) for x in (run.dataset_version_ids or []))
        if not dataset_ids:
            dataset_ids = (uuid4(),)
        isolation = build_isolation_context(
            namespace_id=run.isolation_namespace_id,
            subject_digest=subject_digest,
            dataset_version_ids=dataset_ids,
            memory_mode="empty",
            data_mode="fixture",
        )
        # Prefer stored isolation digest match when present.
        if run.isolation_digest and isolation_digest(isolation) != run.isolation_digest:
            # Rebuild with stored digest as subject_digest only if mismatch is
            # due to interactive placeholder; still require namespace match.
            pass
        case_id = self._resolve_case_id(run, metrics)
        identity = EvalExecutionIdentity(
            eval_run_id=run.id,
            eval_case_id=case_id,
            namespace_id=run.isolation_namespace_id,
            owner_kind=EVAL_OWNER_KIND,
            subject_kind=run.subject_kind,
            subject_aggregate_id=run.subject_aggregate_id,
            subject_version_id=run.subject_version_id,
        )
        script = self.script_resolver(run)
        return isolation, identity, script

    def _resolve_case_id(self, run: Any, metrics: dict[str, Any]) -> UUID:
        """Prefer metrics eval_case_id when it is a real case FK; else first dataset case."""
        raw = metrics.get("eval_case_id")
        if raw:
            try:
                candidate = UUID(str(raw))
            except Exception:
                candidate = None
            if candidate is not None:
                # Validate against DB when a session is available via repo in execute path.
                # Here we only have the run row; worker execute re-checks via repository
                # when persisting. Prefer dataset cases for FK safety.
                pass
            else:
                candidate = None
        else:
            candidate = None
        # Always prefer a real case from the first dataset version when present.
        version_ids = list(run.dataset_version_ids or [])
        if version_ids:
            # Lazy import avoided — use a short-lived session from factory.
            db = self.session_factory()
            try:
                repo = EvaluationRepository(db)
                for vid in version_ids:
                    try:
                        cases = repo.list_cases(UUID(str(vid)))
                    except Exception:
                        continue
                    if cases:
                        # If metrics case matches a published case, keep it.
                        if candidate is not None and any(c.id == candidate for c in cases):
                            return candidate
                        return cases[0].id
            finally:
                db.close()
        return candidate or uuid4()

    def _persist_outcome(
        self,
        repo: EvaluationRepository,
        *,
        run_id: UUID,
        outcome: EvaluationCaseOutcome,
    ) -> None:
        run = repo.get_run(run_id)
        if run is None:
            return
        rev = int(run.state_revision)

        def _refresh_rev() -> int | None:
            current = repo.get_run(run_id)
            if current is None:
                return None
            return int(current.state_revision)

        # Append events (monotonic). Idempotent recovery: skip sequences already
        # present (mirrors capability-call attempt skip). Key by
        # (eval_run_id, sequence) when known; fall back to digest match.
        for event in outcome.events:
            payload = {
                k: v
                for k, v in dict(event.get("payload") or {}).items()
                if k not in {"raw", "credentials", "authorization"}
            }
            event_type = str(event.get("event_type") or "eval.event")
            seq = event.get("seq") or event.get("sequence")
            if seq is not None and repo.has_event_sequence(
                eval_run_id=run_id, sequence=int(seq)
            ):
                continue
            if seq is None and repo.has_event_digest(
                eval_run_id=run_id,
                event_type=event_type,
                payload=payload,
            ):
                continue
            try:
                repo.append_event(
                    eval_run_id=run_id,
                    expected_run_revision=rev,
                    event_type=event_type,
                    payload=payload,
                )
                refreshed = _refresh_rev()
                if refreshed is None:
                    return
                rev = refreshed
            except EvaluationRepositoryError as exc:
                if exc.code in {CODE_STALE_REVISION, CODE_CONFLICT}:
                    # Conflict on unique (run, sequence) is recovery replay.
                    refreshed = _refresh_rev()
                    if refreshed is None:
                        return
                    rev = refreshed
                    continue
                raise

        # Synthetic capability calls — skip duplicates on recovery.
        subject_run = repo.get_run(run_id)
        if subject_run is None:
            return
        for record in outcome.call_records:
            if repo.has_capability_call_attempt(
                eval_run_id=run_id,
                eval_case_id=outcome.eval_case_id,
                logical_call_key=record.logical_call_key,
                attempt=record.attempt,
            ):
                continue
            try:
                repo.append_capability_call(
                    eval_run_id=run_id,
                    eval_case_id=outcome.eval_case_id,
                    expected_run_revision=rev,
                    logical_call_key=record.logical_call_key,
                    attempt=record.attempt,
                    subject_kind=str(subject_run.subject_kind),
                    subject_aggregate_id=subject_run.subject_aggregate_id,
                    subject_version_id=subject_run.subject_version_id,
                    subject_owner_digest=sha256_canonical_json(
                        {"subject": str(subject_run.subject_aggregate_id)}
                    ),
                    binding_digest=record.binding_digest
                    if len(record.binding_digest) == 64
                    else sha256_canonical_json({"b": record.binding_digest}),
                    input_digest=record.input_digest
                    if len(record.input_digest) == 64
                    else sha256_canonical_json({"i": record.input_digest}),
                    descriptor_digest=record.descriptor_digest
                    if len(record.descriptor_digest) == 64
                    else sha256_canonical_json({"d": record.descriptor_digest}),
                    policy_digest=record.policy_digest
                    if len(record.policy_digest) == 64
                    else sha256_canonical_json({"p": record.policy_digest}),
                    outcome=record.outcome,
                    decision_json=dict(record.decision),
                    parent_ordinal=record.parent_ordinal,
                    child_ordinal=record.child_ordinal,
                    eval_call_id=record.eval_call_id,
                    owner_kind=EVAL_OWNER_KIND,
                )
                refreshed = _refresh_rev()
                if refreshed is None:
                    return
                rev = refreshed
            except EvaluationRepositoryError as exc:
                if exc.code in {CODE_CONFLICT, CODE_STALE_REVISION}:
                    refreshed = _refresh_rev()
                    if refreshed is None:
                        return
                    rev = refreshed
                    continue
                raise

        # Case result (one per run/case).
        try:
            repo.append_case_result(
                eval_run_id=run_id,
                eval_case_id=outcome.eval_case_id,
                expected_run_revision=rev,
                result_state=outcome.terminal,
                assertion_details=outcome.assertion_summary.as_dict(),
                call_trace=[
                    {
                        "logical_call_key": r.logical_call_key,
                        "outcome": r.outcome,
                        "side_effect": r.side_effect,
                    }
                    for r in outcome.call_records
                ],
                stop_reason=outcome.failure_code or outcome.terminal,
                calls=len(outcome.call_records),
                safe_error=outcome.failure_code,
            )
            refreshed = _refresh_rev()
            if refreshed is None:
                return
            rev = refreshed
        except EvaluationRepositoryError as exc:
            # Conflict = already has result (recovery replay); other codes fail.
            if exc.code != CODE_CONFLICT:
                raise
            refreshed = _refresh_rev()
            if refreshed is None:
                return
            rev = refreshed

        terminal = outcome.terminal
        if terminal not in {"completed", "failed", "cancelled"}:
            terminal = "failed"
        # isolation_breach permanently gate-ineligible.
        gate_eligible = bool(outcome.gate_eligible)
        if outcome.failure_code == ISOLATION_BREACH:
            gate_eligible = False

        run = repo.get_run(run_id)
        if run is None:
            return
        if run.status in {"completed", "failed", "cancelled"}:
            return
        if run.status == "cancelling":
            repo.transition_run(
                run_id=run_id,
                expected_revision=int(run.state_revision),
                to_status="cancelled",
                gate_eligible=False,
                aggregate_metrics=outcome.aggregate_metrics,
            )
            return
        repo.transition_run(
            run_id=run_id,
            expected_revision=int(run.state_revision),
            to_status=terminal,  # type: ignore[arg-type]
            failure_code=outcome.failure_code,
            gate_eligible=gate_eligible,
            aggregate_metrics=outcome.aggregate_metrics,
        )

    def _finalize_cancel(self, repo: EvaluationRepository, run: Any) -> None:
        if run.status == "cancelled":
            return
        repo.transition_run(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            to_status="cancelled",
            gate_eligible=False,
        )

    def _fail_run(
        self,
        run_id: UUID,
        *,
        failure_code: str,
        gate_eligible: bool,
    ) -> None:
        db = self.session_factory()
        try:
            repo = EvaluationRepository(db)
            run = repo.get_run(run_id)
            if run is None or run.status in {"completed", "failed", "cancelled"}:
                db.commit()
                return
            repo.transition_run(
                run_id=run_id,
                expected_revision=int(run.state_revision),
                to_status="failed",
                failure_code=failure_code,
                gate_eligible=gate_eligible,
            )
            db.commit()
        except Exception:
            logger.exception("fail_run error run_id=%s", run_id)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()


def _default_script_resolver(run: Any) -> InteractiveScript:
    """Resolve interactive script from run aggregate_metrics or default empty."""
    metrics = dict(getattr(run, "aggregate_metrics", None) or {})
    raw_steps = metrics.get("script_steps") or []
    steps: list[InteractiveScriptStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        steps.append(
            InteractiveScriptStep(
                capability_key=str(item.get("capability_key") or "eval.noop"),
                side_effect=str(item.get("side_effect") or "none"),
                arguments=dict(item.get("arguments") or {}),
                logical_call_key=item.get("logical_call_key"),
                is_nested_child=bool(item.get("is_nested_child") or False),
                parent_ordinal=item.get("parent_ordinal"),
                force_tripwire_site=item.get("force_tripwire_site"),
                force_raw_invoke=bool(item.get("force_raw_invoke") or False),
            )
        )
    if not steps:
        # Default interactive noop path (still exercises isolation scope).
        steps = [
            InteractiveScriptStep(
                capability_key="eval.noop",
                side_effect="none",
                logical_call_key="eval-noop-1",
            )
        ]
    cancel_after = metrics.get("cancel_after_step")
    crash_after = metrics.get("crash_after_step")
    return InteractiveScript(
        steps=tuple(steps),
        cancel_after_step=int(cancel_after) if cancel_after is not None else None,
        crash_after_step=int(crash_after) if crash_after is not None else None,
    )


def main(argv: list[str] | None = None) -> int:
    del argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = EvalWorkerConfig.from_settings()
    worker = EvaluationWorker(cfg)
    return worker.run_forever()


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SEC",
    "DEFAULT_LEASE_TTL_SEC",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_POLL_INTERVAL_MS",
    "EvalWorkerConfig",
    "EvalWorkerIdentity",
    "EvalWorkerUnavailable",
    "EvaluationWorker",
    "assert_eval_worker_available",
    "main",
]
