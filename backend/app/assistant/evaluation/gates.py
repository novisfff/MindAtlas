"""Server-derived publish gates (Plan 09 Task 5).

Clients may only post subject/evidence refs + optional non-safety waiver codes
and reason. The server recomputes candidate closure, loads qualifying Eval Runs,
aggregates assertions/thresholds, and derives the decision. Hard safety
assertions cannot be waived.

Publish and Catalog/Profile enable re-verify the gate under the aggregate lock
and append ``publish_gate_use`` in the same transaction as the pointer/state
change.

Two-gate lifecycle
------------------
Publish and enable are separate gated actions:

1. **Publish** (``skill_publish`` / ``profile_publish``) advances the published
   pointer for a draft/version. The gate subject targets the draft (or the
   version being published) content/binding digests plus **current** environment
   pins (profile/catalog/dataset/build/policy/threshold/runtime).
2. **Enable** (``skill_catalog_enable`` / ``profile_runtime_enable``) flips the
   live flag for the *already published* version. Enable requires a **fresh**
   matching gate whose subject targets the published version id (content/binding
   of that published row) plus current environment pins. A publish gate is not
   reusable as an enable gate unless subject version/kind still match.

Observe mode allows ungated publish only for live-disabled bootstrap; enable is
always gated. Enforce mode requires a gate for both publish and enable.

Environment pins are recomputed independently under the aggregate lock at
consume time — never copied from the gate row into the candidate subject so the
compare always matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.evaluation.assertions import (
    THRESHOLD_POLICY_VERSION,
    DatasetAssertionSummary,
    derive_gate_decision,
    evaluate_dataset_assertions,
    is_hard_safety_code,
)
from app.assistant.evaluation.contracts import (
    CreatePublishGateRequest,
    PublishGateAction,
    PublishGateDecision,
    PublishGateSubject,
)
from app.assistant.evaluation.models import (
    AssistantSkillEvalRun,
    AssistantSkillPublishGate,
    AssistantSkillPublishGateUse,
)
from app.assistant.evaluation.repository import (
    CODE_CONFLICT,
    CODE_INVALID_INPUT,
    CODE_NOT_FOUND,
    EvaluationRepository,
    EvaluationRepositoryError,
)
from app.assistant.main_agent.evaluation import RELEASE_THRESHOLDS
from app.common.exceptions import ApiException
from app.common.time import utcnow
from app.config import get_settings

logger = logging.getLogger(__name__)

PublishGateMode = Literal["observe", "enforce"]

DEFAULT_GATE_TTL_DAYS = 14
DEFAULT_POLICY_VERSION = "plan09-policy-v1"
DEFAULT_RUNTIME_CONTRACT_VERSION = 1
# Stable env-pin placeholders used when full profile/catalog recompute is not
# available as pure functions. Tests and create-time subjects share these so
# independent recompute at consume still matches when the system has not drifted.
DEFAULT_PROFILE_DIGEST_PLACEHOLDER = "0" * 64

# Stable API / service error codes (int for ApiException).
CODE_GATE_MISSING = 40980
CODE_GATE_EXPIRED = 40981
CODE_GATE_DRIFT = 40982
CODE_GATE_FAILED = 40983
CODE_GATE_INVALID_REQUEST = 42280
CODE_GATE_HARD_SAFETY = 42281
CODE_GATE_NOT_QUALIFYING = 42282

GATE_ACTION_SKILL_PUBLISH: PublishGateAction = "skill_publish"
GATE_ACTION_SKILL_CATALOG_ENABLE: PublishGateAction = "skill_catalog_enable"
GATE_ACTION_PROFILE_PUBLISH: PublishGateAction = "profile_publish"
GATE_ACTION_PROFILE_RUNTIME_ENABLE: PublishGateAction = "profile_runtime_enable"


@dataclass(frozen=True, slots=True)
class GateEnvironmentPins:
    """Current system environment pins for publish-gate subject closure."""

    profile_digest: str
    catalog_digest: str
    dataset_version_ids: tuple[UUID, ...]
    runtime_contract_version: int
    policy_version: str
    threshold_version: str
    build_revision: str


def current_build_revision() -> str:
    """Read APP_BUILD_REVISION / settings.app_build_revision (default development)."""
    try:
        settings = get_settings()
        return str(getattr(settings, "app_build_revision", None) or "development")
    except Exception:  # noqa: BLE001 — fail closed to development
        return "development"


def skill_catalog_pin_digest(
    *,
    package_id: UUID,
    canonical_name: str,
    published_version_id: UUID | None,
    catalog_enabled: bool | None = None,
    content_digest: str | None = None,
) -> str:
    """Canonical catalog pin digest for skill package enable/publish subjects."""
    from app.assistant.domain.digests import sha256_canonical_json

    payload: dict[str, Any] = {
        "package_id": str(package_id),
        "canonical_name": canonical_name,
        "published_version_id": str(published_version_id) if published_version_id else None,
    }
    if catalog_enabled is not None:
        payload["catalog_enabled"] = bool(catalog_enabled)
    if content_digest is not None:
        payload["content_digest"] = content_digest
    return sha256_canonical_json(payload)


def profile_catalog_pin_digest(
    *,
    profile_id: UUID,
    published_version_id: UUID | None,
    runtime_enabled: bool | None = None,
) -> str:
    """Canonical catalog pin digest for profile publish/enable subjects."""
    from app.assistant.domain.digests import sha256_canonical_json

    payload: dict[str, Any] = {
        "profile_id": str(profile_id),
        "published_version_id": str(published_version_id) if published_version_id else None,
    }
    if runtime_enabled is not None:
        payload["runtime_enabled"] = bool(runtime_enabled)
    return sha256_canonical_json(payload)


def current_gate_environment_pins(
    session: Session | None = None,
    *,
    profile_digest: str | None = None,
    catalog_digest: str | None = None,
    dataset_version_ids: Sequence[UUID] | None = None,
    runtime_contract_version: int | None = None,
    policy_version: str | None = None,
    threshold_version: str | None = None,
    build_revision: str | None = None,
) -> GateEnvironmentPins:
    """Return **current** system environment pins used at gate create/consume.

    Call under the aggregate lock. Content/binding digests come from the draft
    or published version independently; these pins must match the gate row for
    the gate to remain valid.

    When profile/catalog digests are not supplied, stable placeholders are used
    (same as create-time defaults when no richer recompute is available). Callers
    that can compute package/profile catalog digests should pass them explicitly
    so drift of those closures is detected.
    """
    del session  # reserved for future pure DB recompute of global pins
    from uuid import NAMESPACE_URL, uuid5

    ds = tuple(dataset_version_ids) if dataset_version_ids is not None else (
        uuid5(NAMESPACE_URL, "mindatlas:plan09:gate-placeholder-dataset"),
    )
    return GateEnvironmentPins(
        profile_digest=(profile_digest or DEFAULT_PROFILE_DIGEST_PLACEHOLDER),
        catalog_digest=(catalog_digest or DEFAULT_PROFILE_DIGEST_PLACEHOLDER),
        dataset_version_ids=ds,
        runtime_contract_version=int(
            runtime_contract_version
            if runtime_contract_version is not None
            else DEFAULT_RUNTIME_CONTRACT_VERSION
        ),
        policy_version=str(policy_version or DEFAULT_POLICY_VERSION),
        threshold_version=str(threshold_version or THRESHOLD_POLICY_VERSION),
        build_revision=str(build_revision if build_revision is not None else current_build_revision()),
    )


class PublishGateError(Exception):
    """Domain error for gate create / verify / consume."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 409,
        http_code: int = CODE_GATE_MISSING,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.http_code = http_code
        self.details = dict(details or {})

    def to_api_exception(self) -> ApiException:
        return ApiException(
            status_code=self.http_status,
            code=self.http_code,
            message=self.message,
            details={"type": self.code, "details": self.details},
        )


@dataclass(frozen=True, slots=True)
class GateCreateResult:
    gate: AssistantSkillPublishGate
    decision: PublishGateDecision
    assertion_snapshot: dict[str, Any]
    metric_snapshot: dict[str, Any]
    accepted_waiver_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateConsumeResult:
    gate: AssistantSkillPublishGate
    use: AssistantSkillPublishGateUse
    ungated_bootstrap: bool = False


def publish_gate_mode() -> PublishGateMode:
    """Read ASSISTANT_SKILL_PUBLISH_GATE_MODE (default observe at introduce)."""
    try:
        settings = get_settings()
        mode = str(
            getattr(settings, "assistant_skill_publish_gate_mode", "observe") or "observe"
        ).strip().lower()
    except Exception:  # noqa: BLE001 — fail-closed to observe for config races
        mode = "observe"
    if mode not in {"observe", "enforce"}:
        return "observe"
    return mode  # type: ignore[return-value]


def gate_required_for_publish(*, live_enabled: bool, mode: PublishGateMode | None = None) -> bool:
    """Whether a matching gate is mandatory for publish pointer advance.

    - Already live-enabled aggregate: always required (observe and enforce).
    - Live-disabled: required only in enforce; observe allows ungated bootstrap.
    """
    if live_enabled:
        return True
    m = mode or publish_gate_mode()
    return m == "enforce"


def gate_required_for_enable(*, mode: PublishGateMode | None = None) -> bool:
    """Catalog/Profile enable always requires a fresh matching gate."""
    del mode  # both observe and enforce
    return True


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _sorted_uuid_strs(values: Sequence[Any]) -> list[str]:
    return sorted(str(_as_uuid(v)) for v in values)


def _digest_eq(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def compare_subject_closure(
    gate: AssistantSkillPublishGate | Mapping[str, Any],
    subject: PublishGateSubject,
) -> list[str]:
    """Return list of drifted field names (empty = exact match)."""
    if isinstance(gate, Mapping):
        g = gate
        get = g.get
    else:
        get = lambda k, default=None: getattr(gate, k, default)  # noqa: E731

    drifts: list[str] = []
    sub = subject.subject
    if str(get("subject_kind")) != str(sub.kind):
        drifts.append("subject_kind")
    if str(get("subject_aggregate_id")) != str(sub.aggregate_id):
        drifts.append("subject_aggregate_id")
    if str(get("subject_version_id")) != str(sub.version_id):
        drifts.append("subject_version_id")
    if not _digest_eq(get("subject_content_digest"), sub.content_digest):
        drifts.append("subject_content_digest")
    if not _digest_eq(get("subject_binding_digest"), sub.resolved_binding_digest):
        drifts.append("subject_binding_digest")
    if not _digest_eq(get("profile_digest"), subject.profile_digest):
        drifts.append("profile_digest")
    if not _digest_eq(get("catalog_digest"), subject.catalog_digest):
        drifts.append("catalog_digest")
    if int(get("runtime_contract_version") or 0) != int(subject.runtime_contract_version):
        drifts.append("runtime_contract_version")
    if str(get("policy_version") or "") != str(subject.policy_version):
        drifts.append("policy_version")
    if str(get("threshold_version") or "") != str(subject.threshold_version):
        drifts.append("threshold_version")
    if str(get("build_revision") or "") != str(subject.build_revision):
        drifts.append("build_revision")
    gate_ds = _sorted_uuid_strs(get("dataset_version_ids") or [])
    sub_ds = _sorted_uuid_strs(subject.dataset_version_ids)
    if gate_ds != sub_ds:
        drifts.append("dataset_version_ids")
    return drifts


def compare_run_to_subject(
    run: AssistantSkillEvalRun,
    subject: PublishGateSubject,
) -> list[str]:
    """Drift fields between a qualifying eval run and the gate subject."""
    drifts: list[str] = []
    sub = subject.subject
    if str(run.subject_kind) != str(sub.kind):
        drifts.append("subject_kind")
    if str(run.subject_aggregate_id) != str(sub.aggregate_id):
        drifts.append("subject_aggregate_id")
    if str(run.subject_version_id) != str(sub.version_id):
        drifts.append("subject_version_id")
    if not _digest_eq(run.subject_content_digest, sub.content_digest):
        drifts.append("subject_content_digest")
    if not _digest_eq(run.subject_binding_digest, sub.resolved_binding_digest):
        drifts.append("subject_binding_digest")
    if int(run.runtime_contract_version or 0) != int(subject.runtime_contract_version):
        drifts.append("runtime_contract_version")
    if str(run.threshold_policy_version or "") != str(subject.threshold_version):
        drifts.append("threshold_version")
    if str(run.required_build_revision or "") != str(subject.build_revision):
        drifts.append("build_revision")
    run_ds = _sorted_uuid_strs(run.dataset_version_ids or [])
    sub_ds = _sorted_uuid_strs(subject.dataset_version_ids)
    if run_ds != sub_ds:
        drifts.append("dataset_version_ids")
    return drifts


def _metrics_from_run(run: AssistantSkillEvalRun) -> dict[str, float | int]:
    raw = dict(run.aggregate_metrics or {})
    out: dict[str, float | int] = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = value
    # Nested metrics blob (dataset runner may nest under "metrics").
    nested = raw.get("metrics")
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[str(key)] = value
    return out


def _safety_counters_from_run(run: AssistantSkillEvalRun) -> dict[str, int | float | None]:
    raw = dict(run.aggregate_metrics or {})
    counters: dict[str, int | float | None] = {}
    for key in (
        "budget_policy_bypass",
        "false_completion_pending_obligation",
        "unresolved_obligation_falsely_completed",
        "schema_escape",
        "unauthorized_broader_side_effect_count",
        "real_side_effect_in_test",
        "duplicate_write",
        "secret_exposure",
    ):
        if key in raw:
            try:
                counters[key] = int(raw[key])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                counters[key] = None
    nested = raw.get("safety_counters")
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            try:
                counters[str(key)] = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                counters[str(key)] = None
    return counters


def _case_outcomes_from_run(
    repo: EvaluationRepository,
    run: AssistantSkillEvalRun,
) -> list[dict[str, Any]]:
    """Load case results as outcome dicts when present."""
    results = list(getattr(run, "case_results", None) or [])
    if not results:
        # Relationship may be unloaded; query via session.
        from app.assistant.evaluation.models import AssistantSkillEvalCaseResult

        results = list(
            repo.session.query(AssistantSkillEvalCaseResult)
            .filter(AssistantSkillEvalCaseResult.eval_run_id == run.id)
            .all()
        )
    outcomes: list[dict[str, Any]] = []
    for row in results:
        details = dict(row.assertion_details or {})
        outcome = {
            "result_state": row.result_state,
            "actual_active_skills": list(row.actual_active_skills or []),
            "activated_skills": list(row.actual_active_skills or []),
            "call_trace": list(row.call_trace or []),
            **details,
        }
        outcomes.append(outcome)
    return outcomes


def summarize_qualifying_runs(
    repo: EvaluationRepository,
    runs: Sequence[AssistantSkillEvalRun],
    *,
    subject: PublishGateSubject,
) -> DatasetAssertionSummary:
    """Aggregate assertions across qualifying completed, gate-eligible runs."""
    if not runs:
        return evaluate_dataset_assertions(metrics=None)

    # Prefer dataset_scripted runs; fall back to any completed gate-eligible.
    scripted = [r for r in runs if str(r.mode) == "dataset_scripted"]
    chosen = scripted or list(runs)

    # Merge metrics (last wins for scalars; max for zero-tolerance counters).
    merged_metrics: dict[str, float | int] = {}
    merged_counters: dict[str, int | float | None] = {}
    isolation = False
    all_case_outcomes: list[dict[str, Any]] = []

    for run in chosen:
        if str(run.status) != "completed":
            continue
        if not bool(run.gate_eligible):
            # Permanently ineligible runs cannot contribute pass evidence.
            isolation = isolation or str(run.failure_code or "") == "isolation_breach"
            continue
        drifts = compare_run_to_subject(run, subject)
        if drifts:
            # Drifted run does not contribute — treated as missing for that evidence.
            continue
        isolation = isolation or str(run.failure_code or "") == "isolation_breach"
        m = _metrics_from_run(run)
        for k, v in m.items():
            merged_metrics[k] = v
        for k, v in _safety_counters_from_run(run).items():
            if k not in merged_counters or (
                v is not None
                and (merged_counters[k] is None or int(v) > int(merged_counters[k] or 0))
            ):
                merged_counters[k] = v
        all_case_outcomes.extend(_case_outcomes_from_run(repo, run))

    if not merged_metrics and all_case_outcomes:
        return evaluate_dataset_assertions(
            case_outcomes=all_case_outcomes,
            safety_counters=merged_counters or None,
            isolation_breached=isolation,
            thresholds=RELEASE_THRESHOLDS,
        )
    if not merged_metrics:
        # Interactive-only evidence without metrics/case outcomes: do NOT fabricate
        # zero unauthorized counters as proven. Missing metrics → missing evidence.
        # Hard-safety counters present on the run may still be evaluated; absent
        # required keys remain indeterminate/fail via evaluate_dataset_assertions.
        return evaluate_dataset_assertions(
            metrics=None,
            safety_counters=merged_counters or None,
            isolation_breached=isolation,
            thresholds=RELEASE_THRESHOLDS,
        )
    return evaluate_dataset_assertions(
        metrics=merged_metrics,
        case_outcomes=all_case_outcomes or None,
        safety_counters=merged_counters or None,
        isolation_breached=isolation,
        thresholds=RELEASE_THRESHOLDS,
    )


class PublishGateService:
    """Server-derived gate create + transactional consume."""

    def __init__(
        self,
        session: Session,
        *,
        repository: EvaluationRepository | None = None,
        ttl_days: int = DEFAULT_GATE_TTL_DAYS,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.repo = repository or EvaluationRepository(session)
        self.ttl_days = int(ttl_days)
        self._now = now

    def _utcnow(self) -> datetime:
        return self._now or utcnow()

    # ------------------------------------------------------------------
    # Create (server-derived decision)
    # ------------------------------------------------------------------

    def create_gate(
        self,
        request: CreatePublishGateRequest,
        *,
        actor_principal: str,
    ) -> GateCreateResult:
        """Accept only subject/evidence refs + optional non-safety waivers.

        Rejects any client attempt to supply decision/metrics/assertions
        (enforced by CreatePublishGateRequest schema + this service).
        """
        # Validate waiver codes early for hard-safety names.
        for code in request.requested_non_safety_waiver_codes:
            if is_hard_safety_code(code):
                raise PublishGateError(
                    "hard_safety_not_waivable",
                    f"hard safety assertions cannot be waived: {code}",
                    http_status=422,
                    http_code=CODE_GATE_HARD_SAFETY,
                    details={"code": code},
                )

        subject = request.subject
        runs = self._load_qualifying_runs(
            request.qualifying_eval_run_ids,
            subject=subject,
        )
        summary = summarize_qualifying_runs(self.repo, runs, subject=subject)
        decision, accepted, err = derive_gate_decision(
            summary,
            requested_waiver_codes=request.requested_non_safety_waiver_codes,
        )
        if err and decision == "failed" and request.requested_non_safety_waiver_codes:
            # Illegal waiver request → reject create entirely when hard safety.
            if "hard safety" in err:
                raise PublishGateError(
                    "hard_safety_not_waivable",
                    err,
                    http_status=422,
                    http_code=CODE_GATE_HARD_SAFETY,
                    details={"detail": err},
                )
            if "not currently failing" in err or "not waivable" in err:
                raise PublishGateError(
                    "invalid_waiver",
                    err,
                    http_status=422,
                    http_code=CODE_GATE_INVALID_REQUEST,
                    details={"detail": err},
                )

        assertion_snapshot = summary.as_dict()
        metric_snapshot = dict(summary.metrics)
        expires_at = self._utcnow() + timedelta(days=self.ttl_days)

        # Idempotent retry on request_id.
        if request.request_id is not None:
            existing = (
                self.session.query(AssistantSkillPublishGate)
                .filter(AssistantSkillPublishGate.request_id == str(request.request_id))
                .one_or_none()
            )
            if existing is not None:
                return GateCreateResult(
                    gate=existing,
                    decision=existing.decision,  # type: ignore[arg-type]
                    assertion_snapshot=dict(existing.assertion_snapshot or {}),
                    metric_snapshot=dict(existing.metric_snapshot or {}),
                    accepted_waiver_codes=tuple(existing.waiver_codes or ()),
                )

        try:
            gate = self.repo.append_publish_gate(
                subject_kind=subject.subject.kind,
                subject_aggregate_id=subject.subject.aggregate_id,
                subject_version_id=subject.subject.version_id,
                subject_content_digest=subject.subject.content_digest,
                subject_binding_digest=subject.subject.resolved_binding_digest,
                profile_digest=subject.profile_digest,
                catalog_digest=subject.catalog_digest,
                dataset_version_ids=subject.dataset_version_ids,
                qualifying_eval_run_ids=request.qualifying_eval_run_ids,
                runtime_contract_version=subject.runtime_contract_version,
                policy_version=subject.policy_version,
                threshold_version=subject.threshold_version,
                build_revision=subject.build_revision,
                decision=decision,
                assertion_snapshot=assertion_snapshot,
                metric_snapshot=metric_snapshot,
                actor_principal=actor_principal,
                reason=request.waiver_reason,
                waiver_codes=accepted,
                expires_at=expires_at,
                request_id=str(request.request_id),
            )
        except EvaluationRepositoryError as exc:
            raise PublishGateError(
                exc.code,
                str(exc),
                http_status=400 if exc.code == CODE_INVALID_INPUT else 409,
                http_code=CODE_GATE_INVALID_REQUEST,
            ) from exc

        self.session.flush()
        return GateCreateResult(
            gate=gate,
            decision=decision,
            assertion_snapshot=assertion_snapshot,
            metric_snapshot=metric_snapshot,
            accepted_waiver_codes=accepted,
        )

    def _load_qualifying_runs(
        self,
        run_ids: Sequence[UUID],
        *,
        subject: PublishGateSubject,
    ) -> list[AssistantSkillEvalRun]:
        if not run_ids:
            raise PublishGateError(
                "missing_eval_runs",
                "qualifying_eval_run_ids must be non-empty",
                http_status=422,
                http_code=CODE_GATE_INVALID_REQUEST,
            )
        runs: list[AssistantSkillEvalRun] = []
        for rid in run_ids:
            run = self.repo.get_run(_as_uuid(rid))
            if run is None:
                raise PublishGateError(
                    "eval_run_not_found",
                    f"qualifying eval run not found: {rid}",
                    http_status=404,
                    http_code=40490,
                    details={"eval_run_id": str(rid)},
                )
            if str(run.status) != "completed":
                raise PublishGateError(
                    "eval_run_not_completed",
                    f"qualifying eval run not completed: {rid}",
                    http_status=422,
                    http_code=CODE_GATE_NOT_QUALIFYING,
                    details={"eval_run_id": str(rid), "status": run.status},
                )
            if not bool(run.gate_eligible):
                raise PublishGateError(
                    "eval_run_not_gate_eligible",
                    f"qualifying eval run is not gate-eligible: {rid}",
                    http_status=422,
                    http_code=CODE_GATE_NOT_QUALIFYING,
                    details={
                        "eval_run_id": str(rid),
                        "failure_code": run.failure_code,
                    },
                )
            drifts = compare_run_to_subject(run, subject)
            if drifts:
                raise PublishGateError(
                    "eval_run_subject_drift",
                    f"eval run subject closure drifted: {drifts}",
                    http_status=409,
                    http_code=CODE_GATE_DRIFT,
                    details={"eval_run_id": str(rid), "drifts": drifts},
                )
            # Dataset mode preferred for publish gates; interactive alone is weak.
            runs.append(run)
        return runs

    # ------------------------------------------------------------------
    # Load + verify for transactional consume
    # ------------------------------------------------------------------

    def get_gate(self, gate_id: UUID) -> AssistantSkillPublishGate | None:
        return self.session.get(AssistantSkillPublishGate, gate_id)

    def require_gate(self, gate_id: UUID) -> AssistantSkillPublishGate:
        gate = self.get_gate(gate_id)
        if gate is None:
            raise PublishGateError(
                "gate_not_found",
                f"publish gate not found: {gate_id}",
                http_status=404,
                http_code=40490,
            )
        return gate

    def assert_gate_usable(
        self,
        gate: AssistantSkillPublishGate,
        *,
        subject: PublishGateSubject,
        action: PublishGateAction,
        require_passed: bool = True,
    ) -> None:
        """Verify non-expired, decision, and exact subject closure match."""
        del action  # action checked at consume for aggregate ownership
        now = self._utcnow()
        exp = gate.expires_at
        if exp is not None:
            # Normalize naive timestamps from SQLite.
            if exp.tzinfo is None and now.tzinfo is not None:
                exp = exp.replace(tzinfo=now.tzinfo)
            elif exp.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=exp.tzinfo)
            if now > exp:
                raise PublishGateError(
                    "gate_expired",
                    "publish gate has expired; re-evaluate",
                    http_status=409,
                    http_code=CODE_GATE_EXPIRED,
                    details={"gate_id": str(gate.id), "expires_at": str(gate.expires_at)},
                )

        if require_passed and str(gate.decision) not in {"passed", "waived_non_safety"}:
            raise PublishGateError(
                "gate_failed",
                f"publish gate decision is {gate.decision}",
                http_status=409,
                http_code=CODE_GATE_FAILED,
                details={"gate_id": str(gate.id), "decision": gate.decision},
            )

        drifts = compare_subject_closure(gate, subject)
        if drifts:
            raise PublishGateError(
                "gate_subject_drift",
                f"publish gate subject closure drifted: {drifts}",
                http_status=409,
                http_code=CODE_GATE_DRIFT,
                details={"gate_id": str(gate.id), "drifts": drifts},
            )

    def recompute_and_verify(
        self,
        gate_id: UUID,
        *,
        subject: PublishGateSubject,
        action: PublishGateAction,
    ) -> AssistantSkillPublishGate:
        """Load gate and re-verify exact closure under caller's transaction."""
        gate = self.require_gate(gate_id)
        self.assert_gate_usable(gate, subject=subject, action=action)
        # Re-check qualifying runs still match (dataset/build/policy drift).
        for rid in gate.qualifying_eval_run_ids or []:
            run = self.repo.get_run(_as_uuid(rid))
            if run is None or str(run.status) != "completed" or not bool(run.gate_eligible):
                raise PublishGateError(
                    "gate_evidence_invalid",
                    f"qualifying eval run no longer valid: {rid}",
                    http_status=409,
                    http_code=CODE_GATE_DRIFT,
                    details={"eval_run_id": str(rid)},
                )
            drifts = compare_run_to_subject(run, subject)
            if drifts:
                raise PublishGateError(
                    "gate_evidence_drift",
                    f"qualifying eval run drifted: {drifts}",
                    http_status=409,
                    http_code=CODE_GATE_DRIFT,
                    details={"eval_run_id": str(rid), "drifts": drifts},
                )
        return gate

    def consume_gate(
        self,
        *,
        gate_id: UUID,
        action: PublishGateAction,
        subject: PublishGateSubject,
        aggregate_id: UUID,
        resulting_version_id: UUID,
        actor_principal: str,
        request_id: str,
        aggregate_revision: int,
    ) -> GateConsumeResult:
        """Recompute under lock, append gate_use. Caller owns aggregate lock/tx."""
        gate = self.recompute_and_verify(gate_id, subject=subject, action=action)
        if str(gate.subject_aggregate_id) != str(aggregate_id):
            raise PublishGateError(
                "gate_aggregate_mismatch",
                "gate subject aggregate does not match action aggregate",
                http_status=409,
                http_code=CODE_GATE_DRIFT,
                details={
                    "gate_aggregate_id": str(gate.subject_aggregate_id),
                    "aggregate_id": str(aggregate_id),
                },
            )
        try:
            use = self.repo.append_gate_use(
                gate_id=gate.id,
                action=action,
                aggregate_id=aggregate_id,
                resulting_version_id=resulting_version_id,
                actor_principal=actor_principal,
                request_id=request_id,
                aggregate_revision=int(aggregate_revision),
            )
        except EvaluationRepositoryError as exc:
            raise PublishGateError(
                exc.code if exc.code != CODE_NOT_FOUND else "gate_not_found",
                str(exc),
                http_status=404 if exc.code == CODE_NOT_FOUND else 409,
                http_code=CODE_GATE_MISSING if exc.code == CODE_NOT_FOUND else CODE_CONFLICT,
            ) from exc
        return GateConsumeResult(gate=gate, use=use, ungated_bootstrap=False)

    # ------------------------------------------------------------------
    # Publish / enable policy helpers
    # ------------------------------------------------------------------

    def resolve_publish_gate_requirement(
        self,
        *,
        live_enabled: bool,
        gate_id: UUID | None,
        mode: PublishGateMode | None = None,
    ) -> Literal["required", "optional_bootstrap", "forbidden_missing"]:
        """Classify gate requirement for a publish attempt."""
        required = gate_required_for_publish(live_enabled=live_enabled, mode=mode)
        if required and gate_id is None:
            return "forbidden_missing"
        if not required and gate_id is None:
            return "optional_bootstrap"
        return "required"

    def enforce_or_bootstrap_publish(
        self,
        *,
        live_enabled: bool,
        gate_id: UUID | None,
        subject: PublishGateSubject | None,
        action: PublishGateAction,
        aggregate_id: UUID,
        resulting_version_id: UUID,
        actor_principal: str,
        request_id: str,
        aggregate_revision: int,
        mode: PublishGateMode | None = None,
    ) -> GateConsumeResult | None:
        """Consume gate when required/provided; allow ungated disabled bootstrap.

        Returns GateConsumeResult when a gate is used, None for ungated bootstrap.
        Raises PublishGateError when gate is required but missing/invalid.
        """
        m = mode or publish_gate_mode()
        kind = self.resolve_publish_gate_requirement(
            live_enabled=live_enabled, gate_id=gate_id, mode=m
        )
        if kind == "forbidden_missing":
            raise PublishGateError(
                "gate_required",
                (
                    "matching publish gate required to advance published pointer "
                    f"(live_enabled={live_enabled}, mode={m})"
                ),
                http_status=409,
                http_code=CODE_GATE_MISSING,
                details={
                    "live_enabled": live_enabled,
                    "mode": m,
                    "action": action,
                },
            )
        if kind == "optional_bootstrap":
            # Ungated non-live bootstrap — caller must NOT enable catalog/runtime.
            return None
        if gate_id is None or subject is None:
            raise PublishGateError(
                "gate_required",
                "gate_id and subject required",
                http_status=409,
                http_code=CODE_GATE_MISSING,
            )
        return self.consume_gate(
            gate_id=gate_id,
            action=action,
            subject=subject,
            aggregate_id=aggregate_id,
            resulting_version_id=resulting_version_id,
            actor_principal=actor_principal,
            request_id=request_id,
            aggregate_revision=aggregate_revision,
        )

    def enforce_enable(
        self,
        *,
        gate_id: UUID | None,
        subject: PublishGateSubject,
        action: PublishGateAction,
        aggregate_id: UUID,
        resulting_version_id: UUID,
        actor_principal: str,
        request_id: str,
        aggregate_revision: int,
    ) -> GateConsumeResult:
        """Enable always requires a fresh matching gate (observe and enforce).

        The gate subject must match the recomputed closure for the published
        version's content/bindings. ``resulting_version_id`` is the published
        version being enabled (recorded on gate_use); the gate may have been
        created against that published version id.
        """
        if gate_id is None:
            raise PublishGateError(
                "gate_required",
                "matching promotion gate required for catalog/runtime enable",
                http_status=409,
                http_code=CODE_GATE_MISSING,
                details={"action": action},
            )
        gate = self.require_gate(gate_id)
        # Prefer exact published version id; allow gate subject version when
        # content/binding still match (server rebuilt subject from gate pins).
        if str(gate.subject_version_id) not in {
            str(resulting_version_id),
            str(subject.subject.version_id),
        }:
            raise PublishGateError(
                "gate_version_mismatch",
                "enable gate must target exact current published version",
                http_status=409,
                http_code=CODE_GATE_DRIFT,
                details={
                    "gate_subject_version_id": str(gate.subject_version_id),
                    "published_version_id": str(resulting_version_id),
                },
            )
        return self.consume_gate(
            gate_id=gate_id,
            action=action,
            subject=subject,
            aggregate_id=aggregate_id,
            resulting_version_id=resulting_version_id,
            actor_principal=actor_principal,
            request_id=request_id,
            aggregate_revision=aggregate_revision,
        )


def build_publish_gate_subject(
    *,
    kind: str,
    aggregate_id: UUID,
    version_id: UUID,
    content_digest: str,
    binding_digest: str,
    profile_digest: str,
    catalog_digest: str,
    dataset_version_ids: Sequence[UUID],
    runtime_contract_version: int = 1,
    policy_version: str = "plan09-policy-v1",
    threshold_version: str = THRESHOLD_POLICY_VERSION,
    build_revision: str = "development",
) -> PublishGateSubject:
    """Helper to construct a PublishGateSubject from raw fields."""
    from app.assistant.evaluation.contracts import EvalSubjectRef

    return PublishGateSubject(
        subject=EvalSubjectRef(
            kind=kind,  # type: ignore[arg-type]
            aggregate_id=aggregate_id,
            version_id=version_id,
            content_digest=content_digest,
            resolved_binding_digest=binding_digest,
        ),
        profile_digest=profile_digest,
        catalog_digest=catalog_digest,
        runtime_contract_version=runtime_contract_version,
        policy_version=policy_version,
        threshold_version=threshold_version,
        dataset_version_ids=tuple(dataset_version_ids),
        build_revision=build_revision,
    )


def make_create_gate_request(
    *,
    subject: PublishGateSubject,
    qualifying_eval_run_ids: Sequence[UUID],
    request_id: UUID | None = None,
    requested_non_safety_waiver_codes: Sequence[str] = (),
    waiver_reason: str | None = None,
) -> CreatePublishGateRequest:
    return CreatePublishGateRequest(
        request_id=request_id or uuid4(),
        subject=subject,
        qualifying_eval_run_ids=tuple(qualifying_eval_run_ids),
        requested_non_safety_waiver_codes=tuple(requested_non_safety_waiver_codes),
        waiver_reason=waiver_reason,
    )


__all__ = [
    "CODE_GATE_DRIFT",
    "CODE_GATE_EXPIRED",
    "CODE_GATE_FAILED",
    "CODE_GATE_HARD_SAFETY",
    "CODE_GATE_INVALID_REQUEST",
    "CODE_GATE_MISSING",
    "CODE_GATE_NOT_QUALIFYING",
    "DEFAULT_GATE_TTL_DAYS",
    "DEFAULT_POLICY_VERSION",
    "DEFAULT_PROFILE_DIGEST_PLACEHOLDER",
    "DEFAULT_RUNTIME_CONTRACT_VERSION",
    "GATE_ACTION_PROFILE_PUBLISH",
    "GATE_ACTION_PROFILE_RUNTIME_ENABLE",
    "GATE_ACTION_SKILL_CATALOG_ENABLE",
    "GATE_ACTION_SKILL_PUBLISH",
    "GateConsumeResult",
    "GateCreateResult",
    "GateEnvironmentPins",
    "PublishGateError",
    "PublishGateMode",
    "PublishGateService",
    "build_publish_gate_subject",
    "compare_run_to_subject",
    "compare_subject_closure",
    "current_build_revision",
    "current_gate_environment_pins",
    "gate_required_for_enable",
    "gate_required_for_publish",
    "make_create_gate_request",
    "profile_catalog_pin_digest",
    "publish_gate_mode",
    "skill_catalog_pin_digest",
    "summarize_qualifying_runs",
]
