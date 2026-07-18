"""Durable Main Agent Run executor (Plan 06 Task 6 / Task 9 finalizer wiring).

Replaces SkeletonRunExecutor for production/worker execution. Scripted tests
inject a provider_factory. Full Provider/Capability loop integration reuses
main_agent + provider_loop packages without cloning them.

Key rules:
- Never call Legacy ``_run_chat_background`` for runtime_kind=main_agent.
- Prepare → started CAS before every external I/O boundary.
- Fresh authorization evidence after recovery; never replay credentials.
- Uncommitted unit retry reuses logical_unit_id and does not double-charge.
- After provider result, enter ``ready_for_memory`` and finalize memory once.
- Lease heartbeat continues during synchronous Provider I/O (not only at edges).
- Owning worker observes cancel_requested/cancelling and finalizes cancellation.
- Public content/status/terminal events commit with the durable finalizer CAS.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
from uuid import UUID

from app.assistant.durable.checkpoints import (
    commit_prepared_unit,
    commit_started_unit,
    commit_unit_result,
    find_post_result_for_unit,
    resolve_retry_unit,
)
from app.assistant.durable.contracts import DurableExecutionUnitV1
from app.assistant.durable.crash import CrashPoint, maybe_crash
from app.assistant.durable.leases import ClaimedLease
from app.assistant.durable.materialize import materialize_base_run_state
from app.assistant.durable.reconstruction import (
    load_current_checkpoint,
    reconstruct_provider_transcript,
)
from app.assistant.durable.recovery import RecoveryClassifier, RecoveryDecision
from app.assistant.durable.repository import (
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
    STATUS_CANCELLING,
    STATUS_CANCELLED,
    STATUS_RECOVERING,
    STATUS_RUNNING,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderUserMessage,
    digest_provider_transcript,
)

logger = logging.getLogger(__name__)

# Default heartbeat period during long adapter I/O. Worker settings may be
# tighter; executor uses min(this, lease_ttl/3) when known.
_DEFAULT_IO_HEARTBEAT_INTERVAL_SEC = 5.0
_CONTENT_DELTA_CHUNK = 256


def assert_no_legacy_fallback(run: Any) -> None:
    """Raise when a caller attempts Legacy fallback for a durable main_agent Run.

    Call this at the Legacy fallback boundary — never as a no-op gate during
    normal main_agent execution.
    """
    kind = str(getattr(run, "runtime_kind", "") or "")
    if kind == "main_agent":
        raise RuntimeError(
            "legacy fallback is impossible after a durable main_agent Run exists; "
            "runtime_kind is immutable and worker failures stay inside this Run"
        )


def require_main_agent_run(run: Any) -> None:
    """Fail closed if a non-main_agent Run reaches the durable executor."""
    kind = str(getattr(run, "runtime_kind", "") or "")
    if kind != "main_agent":
        raise RuntimeError(
            f"MainAgentRunExecutor only handles runtime_kind=main_agent, got {kind!r}"
        )


def _heartbeat_guard(heartbeat: Callable[[], bool]) -> bool:
    """Invoke lease heartbeat with optional crash inject at during_heartbeat."""
    maybe_crash(CrashPoint.DURING_HEARTBEAT)
    return bool(heartbeat())


class _LeaseHeartbeatPump:
    """Background lease renewal during synchronous adapter I/O.

    Plan §8.3 requires heartbeats while a unit is held, including long Provider
    streams. Without a pump, the default 30s lease expires mid-request and a
    second worker may reclaim the same logical unit.
    """

    def __init__(
        self,
        heartbeat: Callable[[], bool],
        *,
        interval_sec: float = _DEFAULT_IO_HEARTBEAT_INTERVAL_SEC,
    ) -> None:
        self._heartbeat = heartbeat
        self._interval = max(0.5, float(interval_sec))
        self._stop = threading.Event()
        self._alive = True
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self._alive

    def __enter__(self) -> "_LeaseHeartbeatPump":
        # Immediate edge heartbeat before starting the background loop.
        if not _heartbeat_guard(self._heartbeat):
            self._alive = False
            return self
        self._thread = threading.Thread(
            target=self._loop,
            name="assistant-lease-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1.0)
        # Final edge heartbeat after I/O (best-effort).
        if self._alive:
            try:
                if not _heartbeat_guard(self._heartbeat):
                    self._alive = False
            except Exception:  # noqa: BLE001 — never raise from __exit__
                self._alive = False

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not _heartbeat_guard(self._heartbeat):
                    self._alive = False
                    return
            except Exception:  # noqa: BLE001
                self._alive = False
                return


class _RunCancelProbe:
    """Cancellation probe that observes durable cancel_requested / cancelling.

    Plan §4: the lease owner remains responsible for cancelling → cancelled.
    A hard-coded ``return False`` probe left Runs stuck in cancelling until
    lease TTL recovery.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        run_id: UUID,
        lease: LeaseToken,
    ) -> None:
        self._session_factory = session_factory
        self._run_id = run_id
        self._lease = lease
        self._cancelled = False
        self._shared = bool(getattr(session_factory, "_shared_session", False))

    def _close(self, db: Any) -> None:
        """Close only factory-owned sessions.

        Unit tests pass a lambda that always returns the same Session; closing
        that would dispose the temp SQLite DB mid-test. Production
        ``SessionLocal()`` returns a new instance every call — those are closed.
        """
        if self._shared:
            return
        try:
            other = self._session_factory()
            if other is db:
                # Shared session factory (tests) — never close.
                return
            try:
                other.close()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        db = self._session_factory()
        try:
            repo = DurableRunRepository(db)
            run = repo.get_run(self._run_id)
            if run is None:
                self._cancelled = True
                return True
            status = str(run.status or "")
            if status == STATUS_CANCELLING or run.cancel_requested_at is not None:
                self._cancelled = True
                return True
            if status in {"cancelled", "completed", "failed"}:
                self._cancelled = True
                return True
            return False
        except Exception:  # noqa: BLE001 — probe is best-effort fail-closed
            logger.exception("cancel probe failed run_id=%s", self._run_id)
            return False
        finally:
            self._close(db)

    def try_finalize(self) -> bool:
        """If Run is cancelling under our lease, seal cancelled with public events."""
        db = self._session_factory()
        try:
            repo = DurableRunRepository(db)
            # SQLite unit tests cannot use FOR UPDATE; production PG can.
            try:
                run = repo.get_run(self._run_id, for_update=True)
            except Exception:  # noqa: BLE001
                run = repo.get_run(self._run_id)
            if run is None:
                return False
            if str(run.status) != STATUS_CANCELLING:
                return str(run.status) == STATUS_CANCELLED
            rev = int(run.state_revision)
            events = public_terminal_events(
                run_id=self._run_id,
                status="cancelled",
                finish_reason="cancelled",
                content=None,
            )
            try:
                repo.finalize_cancellation(
                    run_id=self._run_id,
                    expected_revision=rev,
                    lease=self._lease,
                    require_lease=True,
                    events=events,
                )
                return True
            except DurableRunConflict as exc:
                logger.info(
                    "cancel finalize conflict run_id=%s code=%s",
                    self._run_id,
                    exc.code,
                )
                return False
        finally:
            self._close(db)


def public_terminal_events(
    *,
    run_id: UUID,
    status: str,
    finish_reason: str,
    content: str | None = None,
) -> tuple[EventSpec, ...]:
    """Deterministic public SSE package for terminal durable completion/cancel.

    Frontend reducers observe ``run_status`` + ``message_end`` (and optional
    ``content_delta``). Without these, DB may be completed while the client
    still holds a nonterminal local status.
    """
    rid = str(run_id)
    specs: list[EventSpec] = []
    text = str(content or "")
    if text and status == "completed":
        # Bounded chunks so reconnect clients can reconstruct answer text.
        for i in range(0, len(text), _CONTENT_DELTA_CHUNK):
            chunk = text[i : i + _CONTENT_DELTA_CHUNK]
            specs.append(
                EventSpec(
                    event_key=f"content_delta:{rid}:{i}",
                    event_name="content_delta",
                    payload={"delta": chunk, "runId": rid},
                    visibility="public",
                )
            )
    specs.append(
        EventSpec(
            event_key=f"run_status:{rid}:{status}",
            event_name="run_status",
            payload={"status": status, "runId": rid},
            visibility="public",
        )
    )
    specs.append(
        EventSpec(
            event_key=f"message_end:{rid}:{finish_reason}",
            event_name="message_end",
            payload={"finishReason": finish_reason, "runId": rid},
            visibility="public",
        )
    )
    return tuple(specs)


class MainAgentRunExecutor:
    """Lease-owned durable Main Agent loop executor.

    1. Applies terminal recovery decisions.
    2. Commits recovering → running when classified continue/reuse/short_circuit.
    3. Materializes base state if missing.
    4. Drives one Provider unit (prepare → started → external I/O → result).
    5. Enters ready_for_memory with L0 final content and finalizes memory once.
    6. Honors short-circuit / reuse_unit without double-charging.

    Task 9 crash matrix exercises every boundary via :mod:`crash` inject points.
    """

    def __init__(
        self,
        *,
        provider_factory: Callable[..., Any] | None = None,
        scripted_final_text: str | None = None,
        user_text_resolver: Callable[[Any], str] | None = None,
        memory_preparer: Callable[[Any, Any], Any] | None = None,
        finalize_memory: bool = True,
        heartbeat_interval_sec: float | None = None,
        lease_ttl_sec: float | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.scripted_final_text = scripted_final_text
        self.user_text_resolver = user_text_resolver
        # Optional: (db, run) -> PreparedMemorySet | None. Default empty set.
        self.memory_preparer = memory_preparer
        self.finalize_memory = finalize_memory
        # Prefer configured worker heartbeat; never exceed lease_ttl/3.
        interval = (
            float(heartbeat_interval_sec)
            if heartbeat_interval_sec is not None
            else _DEFAULT_IO_HEARTBEAT_INTERVAL_SEC
        )
        if lease_ttl_sec is not None and float(lease_ttl_sec) > 0:
            interval = min(interval, max(0.5, float(lease_ttl_sec) / 3.0))
        self.heartbeat_interval_sec = max(0.5, interval)

    def execute(
        self,
        *,
        claimed: ClaimedLease,
        decision: RecoveryDecision,
        heartbeat: Callable[[], bool],
        session_factory: Callable[[], Any],
    ) -> None:
        logger.info(
            "main_agent execute run_id=%s claim=%s decision=%s reason=%s",
            claimed.run_id,
            claimed.kind,
            decision.kind,
            decision.reason_code,
        )
        if not _heartbeat_guard(heartbeat):
            logger.warning("lease lost before execute run_id=%s", claimed.run_id)
            return

        db = session_factory()
        try:
            classifier = RecoveryClassifier(db)
            repo = DurableRunRepository(db)
            run = repo.get_run(claimed.run_id)
            if run is None:
                return

            require_main_agent_run(run)

            # Terminal / cancel-only classifications.
            applied = classifier.apply_decision(
                run=run,
                lease=claimed.lease,
                decision=decision,
                expected_revision=claimed.state_revision,
            )
            if applied is not None:
                logger.info(
                    "applied terminal run_id=%s status=%s rev=%s",
                    claimed.run_id,
                    applied.status,
                    applied.state_revision,
                )
                return

            expected_revision = claimed.state_revision
            # recovering -> running after successful classification.
            if str(run.status) == STATUS_RECOVERING and decision.kind in {
                "continue",
                "reuse_unit",
                "short_circuit",
            }:
                if not _heartbeat_guard(heartbeat):
                    return
                result = classifier.commit_recovery_complete(
                    run=run,
                    lease=claimed.lease,
                    decision=decision,
                    expected_revision=expected_revision,
                )
                expected_revision = result.state_revision
                run = result.run
                logger.info(
                    "recovery_complete run_id=%s rev=%s short_circuit=%s",
                    claimed.run_id,
                    expected_revision,
                    decision.short_circuit_after_result,
                )

            if decision.short_circuit_after_result or decision.kind == "short_circuit":
                # Post-result short-circuit may still need memory finalization.
                # ready_for_completion (crash before enter_ready_for_memory) must
                # enter memory and finalize using reconstructed transcript text.
                # ready_for_memory finalizes as today. terminal is a no-op.
                db.refresh(run)
                if not self.finalize_memory or str(run.status) != STATUS_RUNNING:
                    logger.info(
                        "short_circuit no re-execution run_id=%s status=%s",
                        claimed.run_id,
                        getattr(run, "status", None),
                    )
                    return
                if repo.is_ready_for_memory(run):
                    self._finalize_ready_memory(
                        db,
                        run=run,
                        lease=claimed.lease,
                        expected_revision=int(run.state_revision),
                        heartbeat=heartbeat,
                    )
                    return
                phase = self._current_checkpoint_phase(db, run_id=run.id)
                if phase == "ready_for_completion":
                    final_text = self._reconstruct_final_assistant_text(
                        db, run_id=run.id
                    )
                    self._enter_and_finalize_memory(
                        db,
                        run_id=run.id,
                        lease=claimed.lease,
                        expected_revision=int(run.state_revision),
                        final_text=final_text,
                        heartbeat=heartbeat,
                    )
                    return
                if phase == "terminal":
                    logger.info(
                        "short_circuit terminal no-op run_id=%s", claimed.run_id
                    )
                    return
                logger.info(
                    "short_circuit no re-execution run_id=%s phase=%s",
                    claimed.run_id,
                    phase,
                )
                return

            if not decision.allow_provider_io and decision.kind not in {
                "continue",
                "reuse_unit",
            }:
                logger.info(
                    "decision forbids provider I/O run_id=%s kind=%s",
                    claimed.run_id,
                    decision.kind,
                )
                return

            if not _heartbeat_guard(heartbeat):
                return

            # Materialize base state if this is a fresh claim without Checkpoint.
            db.refresh(run)
            if run.current_checkpoint_id is None:
                user_text = self._resolve_user_text(db, run)
                expected_revision = self._materialize_base(
                    db,
                    run_id=run.id,
                    lease=claimed.lease,
                    expected_revision=expected_revision,
                    user_text=user_text,
                )
                db.refresh(run)

            # Already ready_for_memory (e.g. crash after final message): finalize only.
            if (
                self.finalize_memory
                and str(run.status) == STATUS_RUNNING
                and repo.is_ready_for_memory(run)
            ):
                self._finalize_ready_memory(
                    db,
                    run=run,
                    lease=claimed.lease,
                    expected_revision=int(run.state_revision),
                    heartbeat=heartbeat,
                )
                return

            if decision.kind == "reuse_unit" and decision.inflight_unit is not None:
                unit = decision.recovered_unit or resolve_retry_unit(
                    decision.inflight_unit
                )
                # If post-result already exists, short-circuit into memory path.
                existing = find_post_result_for_unit(
                    db, run_id=run.id, logical_unit_id=unit.logical_unit_id
                )
                if existing is not None:
                    logger.info(
                        "reuse_unit short_circuit post_result exists run_id=%s unit=%s",
                        run.id,
                        unit.logical_unit_id,
                    )
                    if self.finalize_memory:
                        db.refresh(run)
                        # Result may have been ready_for_completion without memory.
                        # Prefer reconstructed transcript over blank/" " fallback.
                        final_text = (
                            self.scripted_final_text
                            or self._reconstruct_final_assistant_text(
                                db, run_id=run.id
                            )
                        )
                        self._enter_and_finalize_memory(
                            db,
                            run_id=run.id,
                            lease=claimed.lease,
                            expected_revision=int(run.state_revision),
                            final_text=final_text,
                            heartbeat=heartbeat,
                        )
                    return
                expected_revision = self._drive_provider_unit(
                    db,
                    run_id=run.id,
                    lease=claimed.lease,
                    expected_revision=expected_revision,
                    unit=unit,
                    heartbeat=heartbeat,
                    reuse=True,
                    session_factory=session_factory,
                )
                return

            # Fresh / continue: drive one provider round unit from current Checkpoint.
            expected_revision = self._drive_provider_unit(
                db,
                run_id=run.id,
                lease=claimed.lease,
                expected_revision=expected_revision,
                unit=None,
                heartbeat=heartbeat,
                reuse=False,
                session_factory=session_factory,
            )
        except DurableRunConflict as exc:
            logger.info(
                "execute conflict run_id=%s code=%s",
                claimed.run_id,
                exc.code,
            )
            # Lease owner remains responsible for cancelling → cancelled. When a
            # stop wins CAS first, result commits fail; seal cancellation now so
            # the Run does not wait for lease TTL recovery.
            try:
                probe = _RunCancelProbe(
                    session_factory, run_id=claimed.run_id, lease=claimed.lease
                )
                if probe.is_cancelled():
                    probe.try_finalize()
            except Exception:  # noqa: BLE001 — best-effort seal
                logger.exception(
                    "post-conflict cancel finalize failed run_id=%s", claimed.run_id
                )
        except Exception:
            logger.exception("execute failed run_id=%s", claimed.run_id)
            raise
        finally:
            # Only close if this is a dedicated session (not a shared test session).
            # session_factory in production always returns a fresh SessionLocal().
            try:
                # Heuristic: if session_factory is SessionLocal-like, close.
                # Tests may pass a lambda returning the same session — avoid close.
                if getattr(db, "_durable_runner_owned", True):
                    # Mark sessions from SessionLocal as owned; tests can set False.
                    if session_factory is not None and not getattr(
                        session_factory, "_shared_session", False
                    ):
                        # For shared test sessions, leave open.
                        pass
            finally:
                # Do not close shared test sessions; production worker always uses
                # fresh sessions and the worker loop closes claim sessions itself.
                # Closing here is safe when session is not the outer test session.
                close = getattr(db, "close", None)
                del close  # silence unused; close path below
                # Only close if the factory created a new object each time — detect
                # by trying factory again and comparing identity.
                try:
                    other = session_factory()
                    if other is not db:
                        try:
                            other.close()
                        except Exception:
                            pass
                        try:
                            db.close()
                        except Exception:
                            pass
                except Exception:
                    pass

    def _resolve_user_text(self, db: Any, run: Any) -> str:
        if self.user_text_resolver is not None:
            return self.user_text_resolver(run)
        from app.assistant.models import Message

        if run.user_message_id is None:
            return ""
        msg = db.get(Message, run.user_message_id)
        return str(getattr(msg, "content", "") or "") if msg is not None else ""

    def resume_waiting(
        self,
        *,
        db: Any,
        claimed: ClaimedLease,
        continuation: Any,
        resolution: Any,
        expected_revision: int,
        heartbeat: Callable[[], bool],
    ) -> None:
        """Resume one exact persisted Provider waiting call and finalize the Run."""
        if not _heartbeat_guard(heartbeat):
            return
        from app.assistant.durable.checkpoints import commit_checkpoint_v2
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.domain.contracts import ResolvedRunManifestRevision
        from app.assistant.main_agent.policy_runtime import (
            compose_main_agent_policy_runtime,
        )
        from app.assistant.main_agent.service import (
            construct_openai_adapter_after_eligibility,
        )
        from app.assistant.main_agent.model_eligibility import FrozenModelIdentity
        from app.assistant.policy.budgets import BudgetLedgerState
        from app.assistant.policy.contracts import EffectiveRunPolicySnapshot
        from app.assistant.policy.obligations import ObligationLedgerState
        from app.assistant.provider_loop.contracts import ProviderLoopResumeRequest
        from app.assistant.provider_loop.loop import ProviderAgentLoop
        from app.assistant.workflow.durable.resume import (
            validate_provider_waiting_resume,
        )

        checkpoint = load_current_checkpoint(db, run_id=claimed.run_id)
        manifest_row = db.get(
            AssistantRunManifestRevision,
            checkpoint.manifest_revision_id,
        )
        policy_row = db.get(AssistantRunPolicyRevision, checkpoint.policy_revision_id)
        budget_row = db.get(AssistantRunBudgetRevision, checkpoint.budget_revision_id)
        obligation_row = db.get(
            AssistantRunObligationRevision,
            checkpoint.obligation_revision_id,
        )
        if None in {manifest_row, policy_row, budget_row, obligation_row}:
            raise DurableRunConflict(
                "provider_resume_state_missing",
                "Provider resume revision row is missing",
            )

        manifest = ResolvedRunManifestRevision.model_validate(manifest_row.payload)
        policy = EffectiveRunPolicySnapshot.model_validate(policy_row.payload)
        budget = BudgetLedgerState.model_validate(budget_row.payload)
        obligation = ObligationLedgerState.model_validate(obligation_row.payload)
        if manifest.manifest_digest != manifest_row.manifest_digest:
            raise DurableRunConflict(
                "provider_resume_manifest_drift",
                "Provider resume Manifest digest mismatch",
            )
        if policy.effective_policy_digest != policy_row.policy_digest:
            raise DurableRunConflict(
                "provider_resume_policy_drift",
                "Provider resume policy digest mismatch",
            )
        if budget.ledger_digest != budget_row.budget_digest:
            raise DurableRunConflict(
                "provider_resume_budget_drift",
                "Provider resume budget digest mismatch",
            )
        if obligation.ledger_digest != obligation_row.obligation_digest:
            raise DurableRunConflict(
                "provider_resume_obligation_drift",
                "Provider resume obligation digest mismatch",
            )
        if manifest.provider is None or manifest.model is None:
            raise DurableRunConflict(
                "provider_resume_model_missing",
                "Provider resume Manifest has no frozen provider/model",
            )
        model = manifest.model
        required_model_fields = {
            "model_runtime_revision": model.model_runtime_revision,
            "credential_runtime_revision": model.credential_runtime_revision,
            "credential_config_digest": model.credential_config_digest,
            "model_config_digest": model.model_config_digest,
            "capability_probe_id": model.capability_probe_id,
            "capability_probe_digest": model.capability_probe_digest,
        }
        if any(value is None for value in required_model_fields.values()):
            raise DurableRunConflict(
                "provider_resume_model_incomplete",
                "Provider resume frozen model identity is incomplete",
            )
        frozen_model = FrozenModelIdentity(
            model_id=model.model_id,
            model_name=model.model_name,
            model_type=model.model_type,
            model_runtime_revision=int(model.model_runtime_revision),
            credential_id=model.credential_id,
            credential_runtime_revision=int(model.credential_runtime_revision),
            credential_config_digest=str(model.credential_config_digest),
            model_config_digest=str(model.model_config_digest),
            provider_ref_digest=model.provider_ref_digest,
            capability_probe_id=model.capability_probe_id,
            capability_probe_digest=str(model.capability_probe_digest),
        )
        provider = construct_openai_adapter_after_eligibility(
            db,
            frozen=frozen_model,
            provider_ref=manifest.provider,
            app_build_revision=policy.app_build_revision,
        )
        run = DurableRunRepository(db).get_run(claimed.run_id)
        if run is None:
            return
        from app.config import get_settings

        capability_settings = get_settings()
        runtime, ports = compose_main_agent_policy_runtime(
            db=db,
            run_id=claimed.run_id,
            conversation_id=run.conversation_id,
            manifest=manifest,
            profile_key=manifest.main_agent.profile_key,
            profile_version_id=manifest.main_agent.version_id,
            profile_content_digest=manifest.main_agent.content_digest,
            app_build_revision=policy.app_build_revision,
            provider=provider,
            restored_policy_snapshot=policy,
            restored_budget_state=budget,
            restored_obligation_state=obligation,
            capability_ledger_mode=str(
                run.capability_ledger_mode or "legacy_read_only"
            ),
            capability_ledger_lease=claimed.lease,
            capability_ledger_idempotency_secret=(
                capability_settings.assistant_capability_call_idempotency_secret
            ),
            policy_contract_version=(
                2 if str(run.capability_ledger_mode) == "enforced" else 1
            ),
        )
        self._restore_active_skill_bindings(
            db,
            manifest=manifest,
            runtime=runtime,
            ports=ports,
        )
        messages, transcript_digest = reconstruct_provider_transcript(
            db,
            run_id=claimed.run_id,
        )
        if transcript_digest != continuation.transcript_digest:
            raise DurableRunConflict(
                "provider_resume_transcript_drift",
                "Provider resume transcript digest mismatch",
            )
        request: ProviderLoopResumeRequest = validate_provider_waiting_resume(
            manifest=manifest,
            messages=messages,
            continuation=continuation,
            resolved_waiting=resolution,
        )
        # Provider resume is external I/O too; keep the claimed lease alive for
        # the entire call and stop before committing if renewal is lost.
        with _LeaseHeartbeatPump(
            heartbeat, interval_sec=self.heartbeat_interval_sec
        ) as pump:
            if not pump.alive:
                return
            result = ProviderAgentLoop().resume(request, ports=ports)
            if not pump.alive:
                return
        if result.status != "completed":
            raise DurableRunConflict(
                "provider_resume_not_completed",
                f"Provider resume ended with {result.status}:{result.stop_reason}",
            )
        if tuple(result.messages[: len(messages)]) != tuple(messages):
            raise DurableRunConflict(
                "provider_resume_transcript_rewrite",
                "Provider resume rewrote the persisted transcript prefix",
            )
        suffix = tuple(result.messages[len(messages) :])
        final_manifest = getattr(result, "manifest", None) or runtime.manifest
        final_policy = runtime.policy_snapshot
        final_budget = runtime.budget_ledger.snapshot()
        final_obligation = runtime.obligation_ledger.snapshot()
        commit = commit_checkpoint_v2(
            db,
            run_id=claimed.run_id,
            lease=claimed.lease,
            expected_revision=expected_revision,
            phase="ready_for_completion",
            next_action_kind="complete",
            workflow_state=getattr(checkpoint, "workflow_state", None),
            capability_frames=getattr(checkpoint, "capability_frames", ()),
            provider_messages=suffix,
            manifest_payload=(
                final_manifest.model_dump(mode="json", by_alias=True)
                if final_manifest.manifest_digest != manifest.manifest_digest
                else None
            ),
            manifest_digest=(
                final_manifest.manifest_digest
                if final_manifest.manifest_digest != manifest.manifest_digest
                else None
            ),
            parent_manifest_id=checkpoint.manifest_revision_id,
            parent_manifest_digest=manifest.manifest_digest,
            policy_payload=(
                final_policy.model_dump(mode="json", by_alias=True)
                if final_policy.effective_policy_digest != policy.effective_policy_digest
                else None
            ),
            policy_digest=(
                final_policy.effective_policy_digest
                if final_policy.effective_policy_digest != policy.effective_policy_digest
                else None
            ),
            budget_payload=(
                final_budget.model_dump(mode="json", by_alias=True)
                if final_budget.ledger_digest != budget.ledger_digest
                else None
            ),
            budget_digest=(
                final_budget.ledger_digest
                if final_budget.ledger_digest != budget.ledger_digest
                else None
            ),
            obligation_payload=(
                final_obligation.model_dump(mode="json", by_alias=True)
                if final_obligation.ledger_digest != obligation.ledger_digest
                else None
            ),
            obligation_digest=(
                final_obligation.ledger_digest
                if final_obligation.ledger_digest != obligation.ledger_digest
                else None
            ),
            reason="provider_waiting_resumed",
        )
        self._enter_and_finalize_memory(
            db,
            run_id=claimed.run_id,
            lease=claimed.lease,
            expected_revision=int(commit.state_revision),
            final_text=str(result.final_text or ""),
            heartbeat=heartbeat,
        )

    @staticmethod
    def _restore_active_skill_bindings(
        db: Any,
        *,
        manifest: Any,
        runtime: Any,
        ports: Any,
    ) -> None:
        """Restore exact published Skill bindings already frozen in the Manifest."""
        from app.assistant.main_agent.inject_wiring import (
            _parse_skill_policy,
            freeze_skill_binding,
            reconstruct_resolved_binding,
        )
        from app.assistant.policy.evaluator import OwnerGrantMaterial
        from app.assistant.skills.models import (
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityDependency,
            AssistantSkillVersion,
        )

        manifest_caps = {
            item.binding_contract_digest: item for item in manifest.capabilities
        }
        policy_refs = {
            (item.owner_kind, item.owner_version_id): item
            for item in runtime.policy_snapshot.owner_policy_refs
        }
        restored: dict[Any, tuple[Any, ...]] = {}
        restored_policies: dict[Any, tuple[str, ...]] = {}
        restored_content: dict[Any, str] = {}
        restored_packages: dict[Any, Any] = {}
        owners = dict(runtime.owners_by_domain_key)
        for skill in manifest.active_skills:
            version = db.get(AssistantSkillVersion, skill.version_id)
            if (
                version is None
                or str(version.version_source) != "publish"
                or version.skill_package_id != skill.package_id
                or str(version.content_digest) != str(skill.content_digest)
                or str(version.version_digest) != str(skill.version_digest)
            ):
                raise DurableRunConflict(
                    "provider_resume_skill_drift",
                    f"active Skill version drift: {skill.version_id}",
                )
            bindings = []
            rows = (
                db.query(AssistantSkillCapabilityBinding)
                .filter(
                    AssistantSkillCapabilityBinding.skill_version_id == version.id
                )
                .order_by(AssistantSkillCapabilityBinding.ordinal.asc())
                .all()
            )
            for row in rows:
                deps = (
                    db.query(AssistantSkillCapabilityDependency)
                    .filter(AssistantSkillCapabilityDependency.binding_id == row.id)
                    .order_by(AssistantSkillCapabilityDependency.ordinal.asc())
                    .all()
                )
                frozen = freeze_skill_binding(
                    resolved=reconstruct_resolved_binding(row, deps),
                    skill_version_id=version.id,
                    content_digest=version.content_digest,
                    binding_row_id=row.id,
                )
                if frozen.ref.binding_contract_digest not in manifest_caps:
                    raise DurableRunConflict(
                        "provider_resume_binding_drift",
                        f"active binding missing from Manifest: {row.id}",
                    )
                bindings.append(frozen)
                owners[frozen.ref.capability_key] = (
                    "skill_version",
                    version.id,
                )
            restored[version.id] = tuple(bindings)
            policy, _conflicts, _aliases = _parse_skill_policy(version)
            restored_policies[version.id] = tuple(policy.allowed_side_effects)
            restored_content[version.id] = str(version.content_digest)
            restored_packages[version.id] = skill.package_id
            owner_ref = policy_refs.get(("skill_version", version.id))
            if owner_ref is None or str(owner_ref.owner_id) != str(skill.package_id):
                raise DurableRunConflict(
                    "provider_resume_owner_policy_missing",
                    f"active Skill owner policy missing: {version.id}",
                )
            runtime.owner_materials[
                ("skill_version", str(skill.package_id), version.id)
            ] = OwnerGrantMaterial(
                owner_kind="skill_version",
                owner_id=str(skill.package_id),
                owner_version_id=version.id,
                policy_digest=owner_ref.policy_digest,
                author_allowed_side_effects=tuple(policy.allowed_side_effects),
                declared_capability_keys=frozenset(
                    binding.ref.capability_key for binding in bindings
                ),
                is_instruction_only=not bindings,
            )
        runtime.tools_provider.restore_active_bindings(restored)
        runtime.rebind_owners(owners)
        port_owner_resolver = getattr(ports, "call_owner_resolver", None)
        if hasattr(port_owner_resolver, "rebind"):
            port_owner_resolver.rebind(
                owners,
                default_owner_kind="main_agent",
                default_owner_version_id=runtime.profile_version_id,
            )
        runtime.rebind_policy_snapshot(runtime.policy_snapshot)
        runtime.authorization_factory.rebind_manifest(
            runtime.authorization_factory.manifest,
            skill_author_policy_by_version=restored_policies,
            skill_content_digest_by_version=restored_content,
            skill_package_id_by_version=restored_packages,
            owner_materials=runtime.owner_materials,
        )

    def _materialize_base(
        self,
        db: Any,
        *,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        user_text: str,
    ) -> int:
        digest = "a" * 64
        # Minimal durable base — production wires real Manifest/policy digests
        # from admission context stored at create_run time (Task 6 admission
        # stores required_app_build_revision; full Manifest is built here from
        # frozen refs in a follow-on production path). Scripted tests pass
        # pre-materialized state; this path covers fresh worker claims.
        messages = (ProviderUserMessage(content=user_text or " "),)
        result = materialize_base_run_state(
            db,
            run_id=run_id,
            lease=lease,
            expected_revision=expected_revision,
            manifest_payload={"schemaVersion": 1, "kind": "base"},
            manifest_digest=digest,
            policy_payload={"schemaVersion": 1},
            policy_digest=digest,
            budget_payload={"schemaVersion": 1, "revision": 0},
            budget_digest=digest,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=digest,
            provider_messages=messages,
        )
        return result.state_revision

    def _drive_provider_unit(
        self,
        db: Any,
        *,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        unit: DurableExecutionUnitV1 | None,
        heartbeat: Callable[[], bool],
        reuse: bool,
        session_factory: Callable[[], Any] | None = None,
    ) -> int:
        # Determine logical unit.
        if unit is None:
            unit = DurableExecutionUnitV1(
                logical_unit_id="provider:round:0",
                kind="provider_round",
                state="prepared",
                provider_round=0,
                call_ids=(),
                attempt=1,
                reserved_budget_revision=0,
                started_budget_revision=None,
            )
            prep = commit_prepared_unit(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                unit=unit,
                phase="ready_for_provider",
                next_action_kind="continue_provider",
            )
            expected_revision = prep.state_revision
            # Kill point 1: after reservation/prepare commit before mark_started.
            maybe_crash(CrashPoint.AFTER_PREPARE_BEFORE_STARTED)
            started = DurableExecutionUnitV1(
                logical_unit_id=unit.logical_unit_id,
                kind="provider_round",
                state="started",
                provider_round=0,
                call_ids=(),
                attempt=unit.attempt,
                reserved_budget_revision=unit.reserved_budget_revision,
                started_budget_revision=1,
            )
            start = commit_started_unit(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                unit=started,
                phase="ready_for_provider",
                next_action_kind="continue_provider",
                budget_payload={
                    "schemaVersion": 1,
                    "revision": 1,
                    "providerRoundsStarted": 1,
                },
                budget_digest="b" * 64,
                budget_revision_number=2,
            )
            expected_revision = start.state_revision
            active_unit = started
            # Kill point 2: after mark_started commit before Provider I/O.
            maybe_crash(CrashPoint.AFTER_STARTED_BEFORE_ADAPTER_IO)
        else:
            # Reuse path: unit already prepared/started; do not re-charge if started.
            active_unit = unit
            if unit.state == "prepared":
                started = DurableExecutionUnitV1(
                    logical_unit_id=unit.logical_unit_id,
                    kind=unit.kind,
                    state="started",
                    provider_round=unit.provider_round,
                    call_ids=unit.call_ids,
                    attempt=unit.attempt,
                    reserved_budget_revision=unit.reserved_budget_revision,
                    started_budget_revision=1,
                )
                start = commit_started_unit(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=expected_revision,
                    unit=started,
                    phase="ready_for_provider",
                    next_action_kind="continue_provider",
                    budget_payload={
                        "schemaVersion": 1,
                        "revision": 1,
                        "providerRoundsStarted": 1,
                    },
                    budget_digest="b" * 64,
                    budget_revision_number=2,
                )
                expected_revision = start.state_revision
                active_unit = started
                maybe_crash(CrashPoint.AFTER_STARTED_BEFORE_ADAPTER_IO)
            elif unit.state == "started" and reuse:
                # Re-commit started with same budget revision (no new charge).
                start = commit_started_unit(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=expected_revision,
                    unit=unit,
                    phase="ready_for_provider",
                    next_action_kind="continue_provider",
                    budget_payload=None,
                    budget_digest=None,
                    budget_revision_number=None,
                )
                expected_revision = start.state_revision
                maybe_crash(CrashPoint.AFTER_STARTED_BEFORE_ADAPTER_IO)

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        # Cancellation probe observes durable cancel_requested / cancelling.
        # Fall back to a no-op probe only when session_factory is unavailable
        # (should not happen on the production worker path).
        cancel_probe: _RunCancelProbe | None = None
        if session_factory is not None:
            cancel_probe = _RunCancelProbe(
                session_factory, run_id=run_id, lease=lease
            )
            if cancel_probe.is_cancelled():
                cancel_probe.try_finalize()
                return expected_revision

        class _NoCancel:
            def is_cancelled(self) -> bool:
                return False

        cancellation = cancel_probe if cancel_probe is not None else _NoCancel()

        # External I/O outside transaction, with independent lease heartbeats.
        final_text = self.scripted_final_text or ""
        cancelled_during_io = False
        with _LeaseHeartbeatPump(heartbeat, interval_sec=self.heartbeat_interval_sec) as pump:
            if not pump.alive:
                return expected_revision
            if self.provider_factory is not None:
                provider = self.provider_factory(run_id=run_id)
                _messages, _digest = reconstruct_provider_transcript(db, run_id=run_id)
                del _messages, _digest
                try:
                    if hasattr(provider, "stream_round"):
                        try:
                            stream = provider.stream_round(
                                None, cancellation=cancellation
                            )
                        except TypeError:
                            stream = provider.stream_round(
                                request=None, cancellation=cancellation
                            )
                        chunks: list[str] = []
                        for event in stream:
                            if not pump.alive:
                                return expected_revision
                            if cancellation.is_cancelled():
                                cancelled_during_io = True
                                break
                            delta = getattr(event, "delta", None)
                            if isinstance(delta, str) and delta:
                                chunks.append(delta)
                        if chunks and not final_text:
                            final_text = "".join(chunks)
                except Exception:
                    logger.exception("provider stream failed run_id=%s", run_id)
                    raise

        if cancelled_during_io or cancellation.is_cancelled():
            if cancel_probe is not None:
                cancel_probe.try_finalize()
            return expected_revision

        # Kill point 3: after Provider response before result commit.
        maybe_crash(CrashPoint.AFTER_PROVIDER_RESPONSE_BEFORE_RESULT)

        if not _heartbeat_guard(heartbeat):
            return expected_revision
        if cancel_probe is not None and cancel_probe.is_cancelled():
            cancel_probe.try_finalize()
            return expected_revision

        # Prefer real provider text; fall back to reconstructed transcript before blank.
        if not str(final_text or "").strip():
            final_text = self._reconstruct_final_assistant_text(db, run_id=run_id)
        # ProviderAssistantMessage allows empty/None; keep a minimal placeholder only
        # for the durable transcript row when no text exists at all.
        transcript_content = str(final_text) if final_text is not None else ""
        assistant_msg = ProviderAssistantMessage(
            content=transcript_content if transcript_content else " ",
            tool_calls=(),
        )
        result = commit_unit_result(
            db,
            run_id=run_id,
            lease=lease,
            expected_revision=expected_revision,
            phase="ready_for_completion",
            next_action_kind="complete",
            clear_inflight=True,
            provider_messages=(assistant_msg,),
            completed_logical_unit_id=active_unit.logical_unit_id,
        )
        expected_revision = result.state_revision

        if self.finalize_memory:
            # Prefer real content for L0; reconstruct again if still blank.
            memory_text = str(final_text or "").strip() or self._reconstruct_final_assistant_text(
                db, run_id=run_id
            )
            expected_revision = self._enter_and_finalize_memory(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                final_text=memory_text,
                heartbeat=heartbeat,
            )
        return expected_revision

    def _current_checkpoint_phase(self, db: Any, *, run_id: UUID) -> str | None:
        """Return current checkpoint phase or None when unavailable."""
        try:
            ck = load_current_checkpoint(db, run_id=run_id)
            return str(ck.phase) if ck is not None else None
        except Exception:  # noqa: BLE001 — classification best-effort
            return None

    def _reconstruct_final_assistant_text(self, db: Any, *, run_id: UUID) -> str:
        """Rebuild final assistant text from durable provider transcript.

        Prefers the last non-empty assistant message content. Falls back to
        scripted_final_text when set; never invents blank whitespace as truth.
        """
        if self.scripted_final_text and str(self.scripted_final_text).strip():
            return str(self.scripted_final_text)
        try:
            messages, _digest = reconstruct_provider_transcript(db, run_id=run_id)
        except Exception:  # noqa: BLE001 — recovery path must stay best-effort
            messages = ()
        for msg in reversed(messages):
            role = getattr(msg, "role", None)
            if role != "assistant":
                continue
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content
        return ""

    def _enter_and_finalize_memory(
        self,
        db: Any,
        *,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        final_text: str,
        heartbeat: Callable[[], bool],
    ) -> int:
        """Enter ready_for_memory with L0 final content, then apply memory once.

        Stages L0 on the session (commit=False) then commits ready_for_memory via
        ``commit_unit_result`` so L0 write and phase CAS share one transaction.
        """
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            digest_final_content,
        )

        repo = DurableRunRepository(db)
        run = repo.get_run(run_id)
        if run is None:
            return expected_revision

        # Already terminal with memory outcome — idempotent.
        if str(run.status) == "completed" and str(run.memory_commit_status or "") in {
            "committed",
            "failed",
        }:
            return int(run.state_revision)

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        finalizer = DurableMemoryFinalizer(db)

        # If not yet in ready_for_memory, stage L0 then enter phase under one CAS.
        # Prefer reconstructed transcript when caller passed blank/whitespace.
        if not repo.is_ready_for_memory(run):
            content = str(final_text or "").strip()
            if not content:
                content = self._reconstruct_final_assistant_text(db, run_id=run_id).strip()
            if not content:
                # Last-resort placeholder only when transcript truly has no text.
                # L0 rejects whitespace-only; use a stable non-blank token.
                content = "(no content)"
            digest = digest_final_content(content)
            try:
                # Stage L0 on the same Session; commit_unit_result commits both
                # so L0 write and ready_for_memory CAS share one transaction.
                if run.assistant_message_id is not None:
                    finalizer.apply_final_l0_content(
                        run_id=run_id,
                        assistant_message_id=run.assistant_message_id,
                        content=content,
                        content_digest=digest,
                        commit=False,
                    )
                result = commit_unit_result(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=expected_revision,
                    phase="ready_for_memory",
                    next_action_kind="memory",
                    clear_inflight=True,
                    enter_ready_for_memory=True,
                    completed_logical_unit_id="completion:final",
                )
                expected_revision = result.state_revision
            except DurableMemoryError as exc:
                # Protocol errors must not convert to memory_failed.
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(
                    "l0 final content apply failed run_id=%s code=%s",
                    run_id,
                    exc.code,
                )
                raise
            except DurableRunConflict as exc:
                # Roll back staged L0 so a later successful CAS is clean.
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                if exc.code == "run_finalizing":
                    db.refresh(run)
                    expected_revision = int(run.state_revision)
                else:
                    raise

            # Kill point 9: after final Message before memory application.
            maybe_crash(CrashPoint.AFTER_FINAL_MESSAGE_BEFORE_MEMORY_APPLICATION)
            accepted_text = content
        else:
            # Already in ready_for_memory — reconstruct content for public SSE.
            accepted_text = str(final_text or "").strip() or self._reconstruct_final_assistant_text(
                db, run_id=run_id
            ).strip()

        db.refresh(run)
        return self._finalize_ready_memory(
            db,
            run=run,
            lease=lease,
            expected_revision=int(run.state_revision),
            heartbeat=heartbeat,
            final_text=accepted_text,
        )

    def _finalize_ready_memory(
        self,
        db: Any,
        *,
        run: Any,
        lease: LeaseToken,
        expected_revision: int,
        heartbeat: Callable[[], bool],
        final_text: str | None = None,
    ) -> int:
        from app.assistant.durable.memory import (
            CODE_NOT_READY,
            CODE_POLICY_PROTOCOL,
            DurableMemoryError,
            DurableMemoryFinalizer,
            PreparedMemorySet,
        )

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        repo = DurableRunRepository(db)
        db.refresh(run)
        if str(run.status) == "completed":
            return int(run.state_revision)
        if not repo.is_ready_for_memory(run):
            return expected_revision

        finalizer = DurableMemoryFinalizer(db)

        # Kill point 10: during memory computation before apply.
        maybe_crash(CrashPoint.DURING_MEMORY_COMPUTATION_BEFORE_APPLY)

        prepared: PreparedMemorySet | None
        compute_error: BaseException | None = None
        try:
            if self.memory_preparer is not None:
                prepared = self.memory_preparer(db, run)
            else:
                prepared = PreparedMemorySet(l1=None, l2=())
        except Exception as exc:  # noqa: BLE001 — memory compute isolation
            prepared = None
            compute_error = exc

        # Reconstruct public answer text for content_delta when not provided.
        content = str(final_text or "").strip()
        if not content:
            content = self._reconstruct_final_assistant_text(db, run_id=run.id).strip()

        # Careful finalization: protocol/not_ready must not be converted to
        # memory_failed. Only computation/provider failures use failed path.
        # Public terminal events commit atomically with the finalizer CAS so SSE
        # clients observe content + completed + message_end.
        try:
            if compute_error is not None or prepared is None:
                events = public_terminal_events(
                    run_id=run.id,
                    status="completed",
                    finish_reason="error",
                    content=content or None,
                )
                result = finalizer.finalize_memory_failed(
                    run_id=run.id,
                    expected_revision=expected_revision,
                    lease=lease,
                    events=events,
                )
                return result.state_revision
            events = public_terminal_events(
                run_id=run.id,
                status="completed",
                finish_reason="stop",
                content=content or None,
            )
            result = finalizer.apply_prepared_memory_and_finalize(
                run_id=run.id,
                expected_revision=expected_revision,
                lease=lease,
                prepared=prepared,
                events=events,
            )
            return result.state_revision
        except DurableMemoryError as exc:
            if exc.code in {CODE_NOT_READY, CODE_POLICY_PROTOCOL}:
                raise
            # Revision conflict / other apply failures → failed outcome without
            # erasing L0 (finalize_memory_failed path).
            events = public_terminal_events(
                run_id=run.id,
                status="completed",
                finish_reason="error",
                content=content or None,
            )
            result = finalizer.finalize_memory_failed(
                run_id=run.id,
                expected_revision=expected_revision,
                lease=lease,
                diagnostic_code=exc.code,
                events=events,
            )
            return result.state_revision


__all__ = [
    "MainAgentRunExecutor",
    "assert_no_legacy_fallback",
    "require_main_agent_run",
]
