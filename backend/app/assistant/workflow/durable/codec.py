"""Strict durable Workflow plan/state codec (Plan 07 Task 1).

Canonical JSON + digests. Rejects secrets, ephemerals (including
EphemeralWorkflowContext), Legacy runtime objects, excess size/depth, and
unknown plan/state schema versions via NeedsReconciliationError before runtime
object construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import TypeAdapter, ValidationError

from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.durable.codec import (
    MAX_CODEC_JSON_BYTES,
    MAX_CODEC_JSON_DEPTH,
    DurableCodecError,
    NeedsReconciliationError,
    _dump_contract,
    _enforce_size,
    _fail,
    _peek_schema_version,
    _reject_ephemeral_instance,
    sanitize_json_value,
)
from app.assistant.workflow.durable.contracts import (
    SUPPORTED_EXECUTION_PLAN_CONTRACT_VERSIONS,
    SUPPORTED_PAUSE_PROPOSAL_CONTRACT_VERSIONS,
    SUPPORTED_WORKFLOW_STATE_SCHEMA_VERSIONS,
    DurableExecutionPlanV1,
    DurablePauseProposalV1,
    DurableWorkflowStateV1,
)

# Mirror outer codec limits for workflow payloads (same hard bounds).
MAX_WORKFLOW_CODEC_JSON_DEPTH: Final[int] = MAX_CODEC_JSON_DEPTH
MAX_WORKFLOW_CODEC_JSON_BYTES: Final[int] = MAX_CODEC_JSON_BYTES

# Default inline projection threshold for node/request payloads (Artifact path).
DEFAULT_INLINE_JSON_BYTES: Final[int] = 8 * 1024

_FORBIDDEN_WORKFLOW_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "EphemeralWorkflowContext",
        "WorkflowState",
        "HumanLoopRuntime",
        "Session",
        "AsyncSession",
        "CapabilityGateway",
        "ArtifactStore",
        "EventSink",
        "CancellationProbe",
        "Clock",
        "ProviderResolver",
        "DurableNodeAdapterRegistry",
        "ExactRuntimeDependencyResolver",
        "LangGraphEngine",
        "CompiledStateGraph",
        "Pregel",
    }
)

_PLAN_ADAPTER: TypeAdapter[DurableExecutionPlanV1] = TypeAdapter(DurableExecutionPlanV1)
_STATE_ADAPTER: TypeAdapter[DurableWorkflowStateV1] = TypeAdapter(DurableWorkflowStateV1)
_PROPOSAL_ADAPTER: TypeAdapter[DurablePauseProposalV1] = TypeAdapter(
    DurablePauseProposalV1
)


def _reject_workflow_ephemeral(value: Any) -> None:
    type_name = type(value).__name__
    if type_name in _FORBIDDEN_WORKFLOW_TYPE_NAMES:
        _fail("ephemeral_rejected", f"{type_name} cannot enter durable workflow codec")
    _reject_ephemeral_instance(value)


def _canonical_payload(value: Any) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        _fail("invalid_payload", "codec payload must be a JSON object")
    sanitized = sanitize_json_value(
        value, max_depth=MAX_WORKFLOW_CODEC_JSON_DEPTH
    )
    if not isinstance(sanitized, dict):
        _fail("invalid_payload", "codec payload must be a JSON object")
    # Enforce size with workflow limit.
    raw = __import__("app.assistant.domain.digests", fromlist=["canonical_json_bytes"]).canonical_json_bytes(
        sanitized
    )
    if len(raw) > MAX_WORKFLOW_CODEC_JSON_BYTES:
        _fail(
            "json_size_exceeded",
            f"canonical JSON size {len(raw)} exceeds {MAX_WORKFLOW_CODEC_JSON_BYTES}",
        )
    return sanitized


def _peek_contract_version(payload: Mapping[str, Any]) -> Any:
    if "contractVersion" in payload:
        return payload["contractVersion"]
    if "contract_version" in payload:
        return payload["contract_version"]
    return None


# ---------------------------------------------------------------------------
# Artifact projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DurableJsonProjection:
    """Result of projecting a JSON payload for durable storage."""

    storage_kind: Literal["inline", "artifact_required"]
    payload: dict[str, JsonValue] | None
    content_digest: str
    byte_size: int
    reason_code: str | None = None


def project_json_for_durable_storage(
    value: Mapping[str, Any] | dict[str, Any],
    *,
    max_inline_bytes: int = DEFAULT_INLINE_JSON_BYTES,
) -> DurableJsonProjection:
    """Project JSON for durable storage: inline when small, else Artifact-required.

    Does not write Artifacts; callers upload when storage_kind=artifact_required.
    """
    if max_inline_bytes < 1:
        raise ValueError("max_inline_bytes must be >= 1")
    _reject_workflow_ephemeral(value)
    if not isinstance(value, Mapping):
        _fail("invalid_payload", "projection requires a JSON object")
    sanitized = sanitize_json_value(value, max_depth=MAX_WORKFLOW_CODEC_JSON_DEPTH)
    if not isinstance(sanitized, dict):
        _fail("invalid_payload", "projection requires a JSON object")
    from app.assistant.domain.digests import canonical_json_bytes

    raw = canonical_json_bytes(sanitized)
    digest = sha256_canonical_json(sanitized)
    size = len(raw)
    if size <= max_inline_bytes:
        return DurableJsonProjection(
            storage_kind="inline",
            payload=sanitized,
            content_digest=digest,
            byte_size=size,
            reason_code=None,
        )
    return DurableJsonProjection(
        storage_kind="artifact_required",
        payload=None,
        content_digest=digest,
        byte_size=size,
        reason_code="inline_limit_exceeded",
    )


# ---------------------------------------------------------------------------
# Execution plan
# ---------------------------------------------------------------------------


def encode_execution_plan(plan: DurableExecutionPlanV1) -> dict[str, JsonValue]:
    _reject_workflow_ephemeral(plan)
    if not isinstance(plan, DurableExecutionPlanV1):
        _fail(
            "unsupported_type",
            f"expected DurableExecutionPlanV1, got {type(plan)!r}",
        )
    return _dump_contract(plan)


def decode_execution_plan(payload: Mapping[str, Any]) -> DurableExecutionPlanV1:
    version = _peek_contract_version(payload)
    if version is None:
        raise NeedsReconciliationError(
            "execution plan payload missing contractVersion",
            schema_version=None,
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise NeedsReconciliationError(
            f"unsupported execution plan contractVersion type {type(version)!r}",
            schema_version=version,
        )
    if version not in SUPPORTED_EXECUTION_PLAN_CONTRACT_VERSIONS:
        raise NeedsReconciliationError(
            f"unsupported execution plan contractVersion={version}; "
            f"supported={sorted(SUPPORTED_EXECUTION_PLAN_CONTRACT_VERSIONS)}",
            schema_version=version,
        )
    data = _canonical_payload(payload)
    try:
        return _PLAN_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("execution_plan_invalid", str(exc)) from exc


# ---------------------------------------------------------------------------
# Workflow state
# ---------------------------------------------------------------------------


def encode_workflow_state(state: DurableWorkflowStateV1) -> dict[str, JsonValue]:
    _reject_workflow_ephemeral(state)
    if not isinstance(state, DurableWorkflowStateV1):
        _fail(
            "unsupported_type",
            f"expected DurableWorkflowStateV1, got {type(state)!r}",
        )
    return _dump_contract(state)


def decode_workflow_state(payload: Mapping[str, Any]) -> DurableWorkflowStateV1:
    version = _peek_schema_version(payload)
    if version is None:
        raise NeedsReconciliationError(
            "workflow state payload missing schemaVersion",
            schema_version=None,
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise NeedsReconciliationError(
            f"unsupported workflow state schemaVersion type {type(version)!r}",
            schema_version=version,
        )
    if version not in SUPPORTED_WORKFLOW_STATE_SCHEMA_VERSIONS:
        raise NeedsReconciliationError(
            f"unsupported workflow state schemaVersion={version}; "
            f"supported={sorted(SUPPORTED_WORKFLOW_STATE_SCHEMA_VERSIONS)}",
            schema_version=version,
        )
    data = _canonical_payload(payload)
    try:
        return _STATE_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("workflow_state_invalid", str(exc)) from exc


def workflow_state_digest(state: DurableWorkflowStateV1) -> str:
    return sha256_canonical_json(encode_workflow_state(state))


# ---------------------------------------------------------------------------
# Pause proposal
# ---------------------------------------------------------------------------


def encode_pause_proposal(proposal: DurablePauseProposalV1) -> dict[str, JsonValue]:
    _reject_workflow_ephemeral(proposal)
    if not isinstance(proposal, DurablePauseProposalV1):
        _fail(
            "unsupported_type",
            f"expected DurablePauseProposalV1, got {type(proposal)!r}",
        )
    return _dump_contract(proposal)


def decode_pause_proposal(payload: Mapping[str, Any]) -> DurablePauseProposalV1:
    version = _peek_contract_version(payload)
    if version is None:
        raise NeedsReconciliationError(
            "pause proposal payload missing contractVersion",
            schema_version=None,
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise NeedsReconciliationError(
            f"unsupported pause proposal contractVersion type {type(version)!r}",
            schema_version=version,
        )
    if version not in SUPPORTED_PAUSE_PROPOSAL_CONTRACT_VERSIONS:
        raise NeedsReconciliationError(
            f"unsupported pause proposal contractVersion={version}; "
            f"supported={sorted(SUPPORTED_PAUSE_PROPOSAL_CONTRACT_VERSIONS)}",
            schema_version=version,
        )
    data = _canonical_payload(payload)
    try:
        return _PROPOSAL_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("pause_proposal_invalid", str(exc)) from exc


__all__ = [
    "DEFAULT_INLINE_JSON_BYTES",
    "MAX_WORKFLOW_CODEC_JSON_BYTES",
    "MAX_WORKFLOW_CODEC_JSON_DEPTH",
    "DurableJsonProjection",
    "decode_execution_plan",
    "decode_pause_proposal",
    "decode_workflow_state",
    "encode_execution_plan",
    "encode_pause_proposal",
    "encode_workflow_state",
    "project_json_for_durable_storage",
    "workflow_state_digest",
]
