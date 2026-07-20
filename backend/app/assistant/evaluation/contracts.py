"""Frozen evaluation contracts (Plan 09 Task 3 public contracts).

Normative ownership:
- EvaluationRepository is the only writer of evaluation tables.
- EvalExecutionIdentity.owner_kind is always ``test`` (execution namespace).
- EvalSubjectRef holds the separate subject ownership identity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, NoReturn
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract

EvalSubjectKind = Literal[
    "skill_draft",
    "skill_version",
    "main_agent_profile_draft",
    "main_agent_profile_version",
    "legacy_baseline",
]

EvalRunMode = Literal["interactive_scripted", "dataset_scripted", "dataset_live"]

EvalRunStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]

EvalCapabilityOutcome = Literal[
    "succeeded_isolated",
    "simulated",
    "denied",
    "failed",
]

PublishGateDecision = Literal["passed", "failed", "waived_non_safety"]

PublishGateAction = Literal[
    "skill_publish",
    "skill_catalog_enable",
    "profile_publish",
    "profile_runtime_enable",
]

EVAL_OBJECT_KEY_PREFIX = "skill-eval"
EVAL_OWNER_KIND = "test"
EVAL_EVENT_NAMESPACE = "evaluation"
EVAL_ARTIFACT_NAMESPACE = "evaluation"

_SHA256_LEN = 64


def _require_sha256(value: str, *, field_name: str) -> str:
    text = (value or "").strip().lower()
    if len(text) != _SHA256_LEN or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{field_name} must be a 64-char lowercase hex digest")
    return text


class EvalSubjectRef(FrozenContract):
    """Immutable subject identity for evaluation and publish gates."""

    schema_version: Literal[1] = 1
    kind: EvalSubjectKind
    aggregate_id: UUID
    version_id: UUID
    content_digest: str
    resolved_binding_digest: str

    @field_validator("content_digest", "resolved_binding_digest")
    @classmethod
    def _digest(cls, value: str, info) -> str:  # noqa: ANN001
        return _require_sha256(value, field_name=info.field_name)


class PublishGateSubject(FrozenContract):
    """Exact subject closure a publish gate must recompute against."""

    schema_version: Literal[1] = 1
    subject: EvalSubjectRef
    profile_digest: str
    catalog_digest: str
    runtime_contract_version: int
    policy_version: str
    threshold_version: str
    dataset_version_ids: tuple[UUID, ...]
    build_revision: str

    @field_validator("profile_digest", "catalog_digest")
    @classmethod
    def _digest(cls, value: str, info) -> str:  # noqa: ANN001
        return _require_sha256(value, field_name=info.field_name)

    @field_validator("runtime_contract_version")
    @classmethod
    def _contract(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("runtime_contract_version must be > 0")
        return int(value)

    @field_validator("dataset_version_ids")
    @classmethod
    def _dataset_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if not value:
            raise ValueError("dataset_version_ids must be non-empty")
        return tuple(value)


class EvalExecutionIdentity(FrozenContract):
    """Execution namespace identity. owner_kind is always test."""

    schema_version: Literal[1] = 1
    eval_run_id: UUID
    eval_case_id: UUID
    namespace_id: UUID
    owner_kind: Literal["test"] = EVAL_OWNER_KIND
    subject_kind: EvalSubjectKind
    subject_aggregate_id: UUID
    subject_version_id: UUID

    @field_validator("owner_kind")
    @classmethod
    def _owner(cls, value: str) -> str:
        if value != EVAL_OWNER_KIND:
            raise ValueError("owner_kind must be 'test'")
        return value


class CreatePublishGateRequest(FrozenContract):
    """Client gate request: evidence refs + optional non-safety waiver only.

    No passed/decision/metric/assertion fields — server derives the decision.
    """

    schema_version: Literal[1] = 1
    request_id: UUID
    subject: PublishGateSubject
    qualifying_eval_run_ids: tuple[UUID, ...]
    requested_non_safety_waiver_codes: tuple[str, ...] = ()
    waiver_reason: str | None = None

    @model_validator(mode="after")
    def _waiver_shape(self) -> CreatePublishGateRequest:
        codes = self.requested_non_safety_waiver_codes
        reason = self.waiver_reason
        if not codes:
            if reason is not None:
                raise ValueError("waiver_reason must be null when no waiver codes")
            return self
        if reason is None or not str(reason).strip():
            raise ValueError("waiver_reason required when waiver codes present")
        if len(str(reason)) > 2000:
            raise ValueError("waiver_reason exceeds 2000 chars")
        return self

    @field_validator("qualifying_eval_run_ids")
    @classmethod
    def _runs(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if not value:
            raise ValueError("qualifying_eval_run_ids must be non-empty")
        return tuple(value)


class RuntimeIsolationContext(FrozenContract):
    """Mandatory isolation envelope for every evaluation workbench run."""

    schema_version: Literal[1] = 1
    namespace_id: UUID
    owner_kind: Literal["test"] = EVAL_OWNER_KIND
    subject_digest: str
    dataset_version_ids: tuple[UUID, ...]
    memory_mode: Literal["empty", "fixture"]
    data_mode: Literal["fixture", "read_snapshot"]
    data_snapshot_id: UUID | None = None
    snapshot_projection_policy_digest: str | None = None
    side_effect_mode: Literal["simulate_only"] = "simulate_only"
    event_namespace: Literal["evaluation"] = EVAL_EVENT_NAMESPACE
    artifact_namespace: Literal["evaluation"] = EVAL_ARTIFACT_NAMESPACE

    @field_validator("owner_kind")
    @classmethod
    def _owner(cls, value: str) -> str:
        if value != EVAL_OWNER_KIND:
            raise ValueError("owner_kind must be 'test'")
        return value

    @field_validator("subject_digest")
    @classmethod
    def _subject_digest(cls, value: str) -> str:
        return _require_sha256(value, field_name="subject_digest")

    @field_validator("snapshot_projection_policy_digest")
    @classmethod
    def _policy_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_sha256(value, field_name="snapshot_projection_policy_digest")

    @model_validator(mode="after")
    def _data_mode_shape(self) -> RuntimeIsolationContext:
        if self.data_mode == "fixture":
            if self.data_snapshot_id is not None or self.snapshot_projection_policy_digest is not None:
                raise ValueError(
                    "data_mode=fixture requires null data_snapshot_id and "
                    "snapshot_projection_policy_digest"
                )
        else:
            if self.data_snapshot_id is None or self.snapshot_projection_policy_digest is None:
                raise ValueError(
                    "data_mode=read_snapshot requires data_snapshot_id and "
                    "snapshot_projection_policy_digest"
                )
        return self


class DatasetCaseSnapshot(FrozenContract):
    """Normalized bounded case snapshot stored on drafts / versions."""

    schema_version: Literal[1] = 1
    case_key: str
    ordinal: int
    locale: str
    input_messages: tuple[dict[str, Any], ...]
    fixture_refs: tuple[str, ...] = ()
    expected_mode: str
    acceptable_skill_keys: tuple[str, ...] = ()
    forbidden_skill_keys: tuple[str, ...] = ()
    acceptable_capability_paths: tuple[tuple[str, ...], ...] = ()
    forbidden_side_effect_classes: tuple[str, ...] = ()
    expect_completion: bool = True
    tags: tuple[str, ...] = ()
    notes: str = ""
    ceilings: dict[str, int] = Field(default_factory=dict)

    @field_validator("case_key")
    @classmethod
    def _case_key(cls, value: str) -> str:
        text = (value or "").strip()
        if not text or len(text) > 128:
            raise ValueError("case_key must be 1..128 chars")
        return text

    @field_validator("ordinal")
    @classmethod
    def _ordinal(cls, value: int) -> int:
        if int(value) < 0:
            raise ValueError("ordinal must be >= 0")
        return int(value)


def is_evaluation_object_key(object_key: str | None) -> bool:
    """Return True when key is under the evaluation namespace prefix."""
    if not object_key:
        return False
    return str(object_key).startswith(f"{EVAL_OBJECT_KEY_PREFIX}/")


def build_evaluation_object_key(*, eval_run_id: UUID, content_sha256: str) -> str:
    """Server-generated evaluation object key. Never accept client keys."""
    digest = _require_sha256(content_sha256, field_name="content_sha256")
    return f"{EVAL_OBJECT_KEY_PREFIX}/{eval_run_id}/{digest}"


def assert_evaluation_object_key(object_key: str) -> str:
    if not is_evaluation_object_key(object_key):
        raise ValueError(
            f"object_key must start with '{EVAL_OBJECT_KEY_PREFIX}/', got {object_key!r}"
        )
    return object_key


def assert_not_evaluation_object_key(object_key: str | None) -> None:
    """Production Artifact APIs must reject evaluation namespace keys."""
    if is_evaluation_object_key(object_key):
        raise ValueError("production artifact APIs reject evaluation object keys")


def assert_not_evaluation_id(*, entity: str, value: UUID | str | None) -> None:
    """Reject an identifier already known to be evaluation-scoped.

    Callers that have already determined the value belongs to the evaluation
    namespace (via repository lookup or explicit metadata) use this helper to
    raise a uniform production-side rejection. Prefer
    ``reject_if_evaluation_id`` when a Session is available and membership is
    not yet known.
    """
    if value is None:
        return
    raise ValueError(f"production {entity} APIs reject evaluation identifiers")


def _as_uuid(value: UUID | str | None) -> UUID | None:
    if value is None:
        return None
    try:
        from uuid import UUID as _UUID

        return value if isinstance(value, _UUID) else _UUID(str(value))
    except Exception:
        return None


def _probe_eval_row(session: Any, model: Any, row_id: UUID) -> bool:
    """Membership probe that must not poison the outer DB transaction.

    PostgreSQL aborts the current transaction on ``relation does not exist``.
    Plan 08-ledger suites (and any DB without Plan 09 eval tables) hit that
    path when production writers call ``reject_if_evaluation_id``. Catching
    the exception alone is insufficient — use a SAVEPOINT so only the nested
    transaction rolls back and the outer session remains usable.
    """
    begin_nested = getattr(session, "begin_nested", None)
    if callable(begin_nested):
        try:
            with begin_nested():
                row = session.get(model, row_id)
                return row is not None
        except Exception:
            # Nested rollback already restored outer txn usability.
            return False
    # Session without nested-transaction support (tests/mocks): best-effort.
    try:
        row = session.get(model, row_id)
        return row is not None
    except Exception:
        return False


def is_evaluation_run_id(session: Any, run_id: UUID | str | None) -> bool:
    """Return True when ``run_id`` exists in evaluation run tables."""
    rid = _as_uuid(run_id)
    if rid is None:
        return False
    try:
        from app.assistant.evaluation.models import AssistantSkillEvalRun
    except Exception:
        return False
    return _probe_eval_row(session, AssistantSkillEvalRun, rid)


def is_evaluation_capability_call_id(session: Any, call_id: UUID | str | None) -> bool:
    """Return True when ``call_id`` exists in evaluation capability-call tables."""
    cid = _as_uuid(call_id)
    if cid is None:
        return False
    try:
        from app.assistant.evaluation.models import AssistantSkillEvalCapabilityCall
    except Exception:
        return False
    return _probe_eval_row(session, AssistantSkillEvalCapabilityCall, cid)


def is_evaluation_event_id(session: Any, event_id: UUID | str | None) -> bool:
    """Return True when ``event_id`` exists in evaluation event tables."""
    eid = _as_uuid(event_id)
    if eid is None:
        return False
    try:
        from app.assistant.evaluation.models import AssistantSkillEvalEvent
    except Exception:
        return False
    return _probe_eval_row(session, AssistantSkillEvalEvent, eid)


def reject_if_evaluation_id(
    session: Any,
    *,
    entity: str,
    value: UUID | str | None,
) -> None:
    """Raise when ``value`` is an evaluation-namespace ID for production APIs.

    Wired into production Run/Event/CapabilityCall get-by-id helpers so eval
    UUIDs that live only in eval tables surface as not-found style rejections
    (ValueError; HTTP entrypoints map to 404 via
    ``reraise_evaluation_id_as_not_found`` / ``is_evaluation_identifier_rejection``).
    """
    if value is None:
        return
    entity_key = str(entity or "run").strip().lower()
    if entity_key in {"run", "eval_run", "assistant_chat_run"}:
        if is_evaluation_run_id(session, value):
            assert_not_evaluation_id(entity="run", value=value)
        return
    if entity_key in {"event", "run_event", "eval_event"}:
        if is_evaluation_event_id(session, value):
            assert_not_evaluation_id(entity="event", value=value)
        return
    if entity_key in {"capability_call", "call", "eval_capability_call"}:
        if is_evaluation_capability_call_id(session, value):
            assert_not_evaluation_id(entity="capability_call", value=value)
        return
    # Unknown entity: if it matches an eval run id, still reject (defense).
    if is_evaluation_run_id(session, value):
        assert_not_evaluation_id(entity=entity_key or "id", value=value)


# Marker substring raised by assert_not_evaluation_id / reject_if_evaluation_id.
# Production HTTP entrypoints match this to map rejection → 404 (not 500).
EVALUATION_IDENTIFIER_REJECTION_MARKER = "evaluation identifiers"


def is_evaluation_identifier_rejection(exc: BaseException) -> bool:
    """True when ``exc`` is the production reject of an evaluation-namespace ID."""
    return isinstance(exc, ValueError) and EVALUATION_IDENTIFIER_REJECTION_MARKER in str(
        exc
    )


def reraise_evaluation_id_as_not_found(
    exc: BaseException,
    *,
    not_found: BaseException,
) -> NoReturn:
    """If ``exc`` rejects an evaluation ID, raise ``not_found``; else re-raise ``exc``.

    Shared by production HTTP entrypoints that call Run/Call get-by-id helpers
    which raise ValueError for eval-namespace IDs. Callers supply a typed
    404-style exception (ApiException, DurableInterruptApiError, …).
    """
    if is_evaluation_identifier_rejection(exc):
        raise not_found from exc
    raise exc
