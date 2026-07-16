"""Strict durable Checkpoint / Provider-message / grant / frame codec (Plan 06 Task 2).

Canonical JSON + fixed digests. Never guesses or silently drops unknown fields.
Unknown schema versions signal needs_reconciliation before constructing runtime objects.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, NoReturn
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.assistant.domain.digests import (
    JsonValue,
    canonical_json_bytes,
    sha256_canonical_json,
)
from app.assistant.durable.contracts import (
    DurableAgentCheckpointV1,
    DurableAgentCheckpointV2,
    DurableExecutionUnitV2,
    DurableGrantSetV1,
    DurableNextActionV2,
    DurableProviderMessageRecordV1,
)
from app.assistant.policy.budgets import (
    BudgetLedgerState,
    deserialize_ledger_state,
    serialize_ledger_state,
)
from app.assistant.policy.contracts import (
    EffectiveCapabilityGrant,
    EffectiveRunPolicySnapshot,
)
from app.assistant.policy.obligations import (
    ObligationLedgerState,
    deserialize_obligation_ledger_state,
    serialize_obligation_ledger_state,
)
from app.assistant.policy.recursion import CapabilityCallFrame
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderCompletionInstructionMessage,
    ProviderContextUpdateMessage,
    ProviderMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderToolMessage,
    ProviderUserMessage,
    provider_message_payload,
)

# ---------------------------------------------------------------------------
# Limits / registry
# ---------------------------------------------------------------------------

MAX_CODEC_JSON_DEPTH: Final[int] = 64
MAX_CODEC_JSON_BYTES: Final[int] = 256 * 1024
SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1, 2})

# Exact keys (case-insensitive) that must never appear in durable JSON.
# Intentionally exact — do not substring-match legitimate fields like
# maxPromptTokens, credentialId, credentialRuntimeRevision.
_FORBIDDEN_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "apikey",
        "api_key",
        "api-key",
        "password",
        "passwd",
        "secret",
        "token",
        "accesstoken",
        "access_token",
        "access-token",
        "refreshtoken",
        "refresh_token",
        "refresh-token",
        "credential",
        "credentials",
        "authorization",
        "bearer",
        "fernet",
        "fernetkey",
        "fernet_key",
        "fernet-key",
        "privatekey",
        "private_key",
        "private-key",
        "decrypted",
        "decryptedcredential",
        "decrypted_credential",
        "decrypted-credential",
        "clientsecret",
        "client_secret",
        "client-secret",
        "accesskey",
        "access_key",
        "access-key",
        "sessionkey",
        "session_key",
        "session-key",
        "rawsecret",
        "raw_secret",
    }
)
# Suffix patterns for nested secret blobs (e.g. fooApiKey) — not bare "Token(s)".
_FORBIDDEN_SECRET_KEY_RE = re.compile(
    r"(?i)^(("
    r"(.*_)?(api[_-]?key|password|passwd|client[_-]?secret|fernet[_-]?key|"
    r"private[_-]?key|decrypted[_-]?credential|session[_-]?key)"
    r")|("
    r"secret|token|credential|credentials|authorization|bearer|decrypted"
    r"))$"
)

_FORBIDDEN_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        # DB / clients
        "Session",
        "AsyncSession",
        "Client",
        "OpenAI",
        "AsyncOpenAI",
        "httpx",
        "ClientSession",
        # Process-local ledgers / ports
        "BudgetLedger",
        "ObligationLedger",
        "ProcessLocalCapabilityCallFramePort",
        "NoOpCapabilityCallFramePort",
        "CapabilityGateway",
        "CapabilityRuntimePorts",
        "ProviderLoopPorts",
        "ProviderAdapter",
        "ToolsProvider",
        "ToolDispatcher",
        "SiblingExecutionPort",
        "CancellationPort",
        "ManifestEffectLifecyclePort",
        "NoOpManifestEffectLifecyclePort",
        "ProviderRoundBudgetGuard",
        "ProviderCompletionGuard",
        "CapabilityCallReservationPort",
        "CapabilityCallOwnerResolver",
        "CurrentCapabilityDescriptorVerifier",
        "ProviderAuthorizationEvidenceFactory",
        "ProviderLoopEventSink",
        "RoundContextProvider",
        "NoOpRoundContextProvider",
        "Future",
        "Task",
        "Thread",
        "Lock",
        "RLock",
        "Event",
        "module",
        "function",
        "method",
        "builtin_function_or_method",
        "type",
        # Plan 07 ephemeral / Legacy runtime families
        "EphemeralWorkflowContext",
        "WorkflowState",
        "HumanLoopRuntime",
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

# Classification / descriptor substitution keys that must never appear on grants.
_GRANT_FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "classificationRevision",
        "classification_revision",
        "classificationRulesetDigest",
        "classification_ruleset_digest",
        "descriptorDigest",
        "descriptor_digest",
        "behaviorDigest",
        "behavior_digest",
        "sideEffect",
        "side_effect",
        "classification",
        "rulesetDigest",
        "ruleset_digest",
    }
)

_PROVIDER_MESSAGE_ADAPTER: TypeAdapter[ProviderMessage] = TypeAdapter(ProviderMessage)
_CHECKPOINT_V1_ADAPTER: TypeAdapter[DurableAgentCheckpointV1] = TypeAdapter(
    DurableAgentCheckpointV1
)
_CHECKPOINT_V2_ADAPTER: TypeAdapter[DurableAgentCheckpointV2] = TypeAdapter(
    DurableAgentCheckpointV2
)
_GRANT_ADAPTER: TypeAdapter[EffectiveCapabilityGrant] = TypeAdapter(
    EffectiveCapabilityGrant
)
_GRANT_SET_ADAPTER: TypeAdapter[DurableGrantSetV1] = TypeAdapter(DurableGrantSetV1)
_FRAME_ADAPTER: TypeAdapter[CapabilityCallFrame] = TypeAdapter(CapabilityCallFrame)
_POLICY_ADAPTER: TypeAdapter[EffectiveRunPolicySnapshot] = TypeAdapter(
    EffectiveRunPolicySnapshot
)
_MESSAGE_RECORD_ADAPTER: TypeAdapter[DurableProviderMessageRecordV1] = TypeAdapter(
    DurableProviderMessageRecordV1
)

_PROTECTED_ROLE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "instructionType",
        "instruction_type",
        "contextType",
        "context_type",
        "guardStateDigest",
        "guard_state_digest",
        "promptBuildDigest",
        "prompt_build_digest",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DurableCodecError(ValueError):
    """Strict durable codec failure (reject; do not guess)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class NeedsReconciliationError(DurableCodecError):
    """Unknown/unsupported schema version or codec drift.

    Raised *before* any runtime Checkpoint / Provider / Gateway object is
    constructed so callers can CAS the Run into needs_reconciliation.
    """

    def __init__(self, message: str, *, schema_version: Any = None) -> None:
        self.schema_version = schema_version
        super().__init__("needs_reconciliation", message)


def _fail(code: str, message: str) -> NoReturn:
    raise DurableCodecError(code, message)


# ---------------------------------------------------------------------------
# JSON sanitizer (depth/size/NaN/bytes/cycles/secrets/ephemerals)
# ---------------------------------------------------------------------------


def _reject_ephemeral_instance(value: Any) -> None:
    type_name = type(value).__name__
    if type_name in _FORBIDDEN_TYPE_NAMES:
        _fail("ephemeral_rejected", f"{type_name} cannot enter durable codec state")
    if callable(value) and not isinstance(value, type):
        if type_name in {"method", "function", "builtin_function_or_method"}:
            _fail("ephemeral_rejected", "callbacks cannot enter durable codec state")


def sanitize_json_value(
    value: Any,
    *,
    path: str = "$",
    depth: int = 0,
    seen: set[int] | None = None,
    max_depth: int = MAX_CODEC_JSON_DEPTH,
) -> JsonValue:
    """Validate a value is narrow JSON; reject secrets, ephemerals, cycles, NaN."""
    if depth > max_depth:
        _fail("json_depth_exceeded", f"JSON depth exceeds {max_depth} at {path}")

    if seen is None:
        seen = set()

    # Ephemeral / arbitrary runtime objects (not plain containers/primitives).
    if not isinstance(
        value, (type(None), bool, int, float, str, bytes, bytearray, dict, list, tuple)
    ):
        _reject_ephemeral_instance(value)
        # Raw UUID is stringified for portability; callers should prefer strings.
        if isinstance(value, UUID):
            return str(value)
        _fail(
            "unsupported_type",
            f"unsupported JSON value type {type(value)!r} at {path}",
        )

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            _fail("non_finite_float", f"NaN/Infinity are not valid JSON values at {path}")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        _fail("bytes_rejected", f"bytes are not valid JSON values at {path}")

    obj_id = id(value)
    if obj_id in seen:
        _fail("cycle_rejected", f"cyclic structure at {path}")
    seen.add(obj_id)
    try:
        if isinstance(value, Mapping):
            out: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    _fail("invalid_key", f"mapping keys must be strings at {path}")
                key_norm = key.replace("-", "_").lower()
                if key_norm in _FORBIDDEN_SECRET_KEYS or _FORBIDDEN_SECRET_KEY_RE.search(
                    key
                ):
                    _fail(
                        "secret_key_rejected",
                        f"forbidden secret/credential key {key!r} at {path}",
                    )
                out[key] = sanitize_json_value(
                    item,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                    seen=seen,
                    max_depth=max_depth,
                )
            return out
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [
                sanitize_json_value(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    seen=seen,
                    max_depth=max_depth,
                )
                for index, item in enumerate(value)
            ]
    finally:
        seen.discard(obj_id)

    _fail("unsupported_type", f"unsupported JSON value type {type(value)!r} at {path}")


def _enforce_size(value: JsonValue, *, path: str = "$") -> JsonValue:
    raw = canonical_json_bytes(value)
    if len(raw) > MAX_CODEC_JSON_BYTES:
        _fail(
            "json_size_exceeded",
            f"canonical JSON size {len(raw)} exceeds {MAX_CODEC_JSON_BYTES} at {path}",
        )
    return value


def _canonical_payload(value: Any) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        _fail("invalid_payload", "codec payload must be a JSON object")
    sanitized = sanitize_json_value(value)
    if not isinstance(sanitized, dict):
        _fail("invalid_payload", "codec payload must be a JSON object")
    _enforce_size(sanitized)
    return sanitized


def _dump_contract(model: Any) -> dict[str, JsonValue]:
    _reject_ephemeral_instance(model)
    if not hasattr(model, "model_dump"):
        _fail("unsupported_type", f"expected FrozenContract, got {type(model)!r}")
    raw = model.model_dump(mode="json", by_alias=True)
    return _canonical_payload(raw)


# ---------------------------------------------------------------------------
# Provider messages
# ---------------------------------------------------------------------------


def encode_provider_message(message: ProviderMessage) -> dict[str, JsonValue]:
    """Encode using the exact Plan 03 payload shape (role discriminator)."""
    _reject_ephemeral_instance(message)
    if not isinstance(
        message,
        (
            ProviderSystemMessage,
            ProviderRuntimeInstructionMessage,
            ProviderContextUpdateMessage,
            ProviderCompletionInstructionMessage,
            ProviderUserMessage,
            ProviderAssistantMessage,
            ProviderToolMessage,
        ),
    ):
        _fail(
            "unsupported_type",
            f"not a ProviderMessage union member: {type(message)!r}",
        )
    payload = provider_message_payload(message)
    return _canonical_payload(payload)


def decode_provider_message(payload: Mapping[str, Any]) -> ProviderMessage:
    """Decode a ProviderMessage; reject protected downcast and secret keys."""
    data = _canonical_payload(payload)
    role = data.get("role")
    if role is None:
        _fail("missing_role", "provider message requires role discriminator")

    # Reject runtime_* fields downcast onto role=system.
    if role == "system":
        for marker in _PROTECTED_ROLE_MARKERS:
            if marker in data:
                _fail(
                    "protected_downcast",
                    "runtime_instruction|runtime_context|runtime_completion "
                    "cannot be downcast to system",
                )
        # Only content is allowed on system.
        allowed = {"role", "content"}
        extra = set(data) - allowed
        if extra:
            _fail(
                "protected_downcast",
                f"system message has unexpected fields {sorted(extra)!r} "
                "(possible protected-role downcast)",
            )

    try:
        message = _PROVIDER_MESSAGE_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("provider_message_invalid", str(exc)) from exc
    return message


def encode_provider_message_record(
    record: DurableProviderMessageRecordV1,
) -> dict[str, JsonValue]:
    return _dump_contract(record)


def decode_provider_message_record(
    payload: Mapping[str, Any],
) -> DurableProviderMessageRecordV1:
    data = _canonical_payload(payload)
    # Pre-check protected downcast on nested message body.
    body = data.get("message")
    if isinstance(body, Mapping):
        decode_provider_message(body)  # raises on downcast/secrets
    try:
        return _MESSAGE_RECORD_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("provider_message_record_invalid", str(exc)) from exc


# ---------------------------------------------------------------------------
# Grants / frames / policy / ledgers
# ---------------------------------------------------------------------------


def encode_grant(grant: EffectiveCapabilityGrant) -> dict[str, JsonValue]:
    return _dump_contract(grant)


def decode_grant(payload: Mapping[str, Any]) -> EffectiveCapabilityGrant:
    data = _canonical_payload(payload)
    forbidden = set(data) & _GRANT_FORBIDDEN_KEYS
    if forbidden:
        _fail(
            "grant_classification_substitution",
            f"grant payload contains forbidden classification/descriptor keys "
            f"{sorted(forbidden)!r}",
        )
    try:
        return _GRANT_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("grant_invalid", str(exc)) from exc


def encode_grant_set(grant_set: DurableGrantSetV1) -> dict[str, JsonValue]:
    return _dump_contract(grant_set)


def decode_grant_set(payload: Mapping[str, Any]) -> DurableGrantSetV1:
    data = _canonical_payload(payload)
    grants = data.get("grants")
    if isinstance(grants, list):
        for index, item in enumerate(grants):
            if isinstance(item, Mapping):
                forbidden = set(item) & _GRANT_FORBIDDEN_KEYS
                if forbidden:
                    _fail(
                        "grant_classification_substitution",
                        f"grant[{index}] contains forbidden keys {sorted(forbidden)!r}",
                    )
    try:
        return _GRANT_SET_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("grant_set_invalid", str(exc)) from exc


def encode_capability_frame(frame: CapabilityCallFrame) -> dict[str, JsonValue]:
    return _dump_contract(frame)


def decode_capability_frame(payload: Mapping[str, Any]) -> CapabilityCallFrame:
    data = _canonical_payload(payload)
    try:
        return _FRAME_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("frame_invalid", str(exc)) from exc


def encode_policy_snapshot(
    snapshot: EffectiveRunPolicySnapshot,
) -> dict[str, JsonValue]:
    return _dump_contract(snapshot)


def decode_policy_snapshot(
    payload: Mapping[str, Any],
) -> EffectiveRunPolicySnapshot:
    data = _canonical_payload(payload)
    try:
        return _POLICY_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("policy_snapshot_invalid", str(exc)) from exc


def encode_budget_ledger_state(state: BudgetLedgerState) -> dict[str, JsonValue]:
    _reject_ephemeral_instance(state)
    if not isinstance(state, BudgetLedgerState):
        _fail("unsupported_type", f"expected BudgetLedgerState, got {type(state)!r}")
    return _canonical_payload(serialize_ledger_state(state))


def decode_budget_ledger_state(payload: Mapping[str, Any]) -> BudgetLedgerState:
    data = _canonical_payload(payload)
    try:
        return deserialize_ledger_state(data)
    except (ValidationError, ValueError, TypeError) as exc:
        raise DurableCodecError("budget_ledger_invalid", str(exc)) from exc


def encode_obligation_ledger_state(
    state: ObligationLedgerState,
) -> dict[str, JsonValue]:
    _reject_ephemeral_instance(state)
    if not isinstance(state, ObligationLedgerState):
        _fail(
            "unsupported_type",
            f"expected ObligationLedgerState, got {type(state)!r}",
        )
    return _canonical_payload(serialize_obligation_ledger_state(state))


def decode_obligation_ledger_state(
    payload: Mapping[str, Any],
) -> ObligationLedgerState:
    data = _canonical_payload(payload)
    try:
        return deserialize_obligation_ledger_state(data)
    except (ValidationError, ValueError, TypeError) as exc:
        raise DurableCodecError("obligation_ledger_invalid", str(exc)) from exc


# ---------------------------------------------------------------------------
# Checkpoint codec + migration registry
# ---------------------------------------------------------------------------


AnyCheckpoint = DurableAgentCheckpointV1 | DurableAgentCheckpointV2


def encode_checkpoint_v1(checkpoint: DurableAgentCheckpointV1) -> dict[str, JsonValue]:
    _reject_ephemeral_instance(checkpoint)
    if not isinstance(checkpoint, DurableAgentCheckpointV1):
        _fail(
            "unsupported_type",
            f"expected DurableAgentCheckpointV1, got {type(checkpoint)!r}",
        )
    if checkpoint.schema_version != 1:
        _fail("schema_version", "encode_checkpoint_v1 requires schema_version=1")
    return _dump_contract(checkpoint)


def decode_checkpoint_v1(payload: Mapping[str, Any]) -> DurableAgentCheckpointV1:
    """Decode Checkpoint v1 only. Unknown versions must use decode_checkpoint."""
    data = _canonical_payload(payload)
    version = data.get("schemaVersion", data.get("schema_version"))
    if version is None:
        _fail("missing_schema_version", "checkpoint requires schemaVersion")
    if version != 1:
        raise NeedsReconciliationError(
            f"decode_checkpoint_v1 received schemaVersion={version!r}",
            schema_version=version,
        )
    try:
        return _CHECKPOINT_V1_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("checkpoint_invalid", str(exc)) from exc


def encode_checkpoint_v2(checkpoint: DurableAgentCheckpointV2) -> dict[str, JsonValue]:
    _reject_ephemeral_instance(checkpoint)
    if not isinstance(checkpoint, DurableAgentCheckpointV2):
        _fail(
            "unsupported_type",
            f"expected DurableAgentCheckpointV2, got {type(checkpoint)!r}",
        )
    if checkpoint.schema_version != 2:
        _fail("schema_version", "encode_checkpoint_v2 requires schema_version=2")
    return _dump_contract(checkpoint)


def decode_checkpoint_v2(payload: Mapping[str, Any]) -> DurableAgentCheckpointV2:
    """Decode Checkpoint v2 only."""
    data = _canonical_payload(payload)
    version = data.get("schemaVersion", data.get("schema_version"))
    if version is None:
        _fail("missing_schema_version", "checkpoint requires schemaVersion")
    if version != 2:
        raise NeedsReconciliationError(
            f"decode_checkpoint_v2 received schemaVersion={version!r}",
            schema_version=version,
        )
    try:
        return _CHECKPOINT_V2_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise DurableCodecError("checkpoint_invalid", str(exc)) from exc


def _peek_schema_version(payload: Mapping[str, Any]) -> Any:
    """Read schema version without constructing runtime Checkpoint objects."""
    if not isinstance(payload, Mapping):
        _fail("invalid_payload", "checkpoint payload must be a JSON object")
    # Lightweight key inspection only — no model_validate.
    if "schemaVersion" in payload:
        return payload["schemaVersion"]
    if "schema_version" in payload:
        return payload["schema_version"]
    return None


def decode_checkpoint(payload: Mapping[str, Any]) -> AnyCheckpoint:
    """Decode any supported Checkpoint version without forcing migration.

    Unknown future versions raise NeedsReconciliationError *before* constructing
    nested runtime contracts.
    """
    version = _peek_schema_version(payload)
    if version is None:
        raise NeedsReconciliationError(
            "checkpoint payload missing schemaVersion",
            schema_version=None,
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise NeedsReconciliationError(
            f"unsupported schemaVersion type {type(version)!r}",
            schema_version=version,
        )
    if version not in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS:
        raise NeedsReconciliationError(
            f"unsupported checkpoint schemaVersion={version}; "
            f"supported={sorted(SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS)}",
            schema_version=version,
        )
    # Only after version gate may we sanitize + construct.
    if version == 1:
        return decode_checkpoint_v1(payload)
    if version == 2:
        return decode_checkpoint_v2(payload)
    raise NeedsReconciliationError(
        f"no decoder registered for schemaVersion={version}",
        schema_version=version,
    )


def migrate_checkpoint_v1_to_v2(
    checkpoint: DurableAgentCheckpointV1 | Mapping[str, Any],
) -> DurableAgentCheckpointV2:
    """Lossless Checkpoint v1 → v2 migration.

    Maps each v1 execution unit into the corresponding v2 kind, preserves every
    other v1 field/digest meaning, and fills new fields with null/empty values.
    """
    if isinstance(checkpoint, Mapping):
        v1 = decode_checkpoint_v1(checkpoint)
    elif isinstance(checkpoint, DurableAgentCheckpointV1):
        v1 = checkpoint
    else:
        _fail(
            "unsupported_type",
            f"migrate_checkpoint_v1_to_v2 expected DurableAgentCheckpointV1, "
            f"got {type(checkpoint)!r}",
        )

    inflight_v2: DurableExecutionUnitV2 | None = None
    if v1.inflight_unit is not None:
        u = v1.inflight_unit
        inflight_v2 = DurableExecutionUnitV2(
            logical_unit_id=u.logical_unit_id,
            kind=u.kind,  # v1 kinds are a subset of v2
            state=u.state,
            provider_round=u.provider_round,
            call_ids=u.call_ids,
            attempt=u.attempt,
            reserved_budget_revision=u.reserved_budget_revision,
            started_budget_revision=u.started_budget_revision,
        )

    next_v2 = DurableNextActionV2(
        kind=v1.next_action.kind,  # v1 action kinds are a subset of v2
        reason_code=v1.next_action.reason_code,
        detail=v1.next_action.detail,
    )

    return DurableAgentCheckpointV2(
        run_id=v1.run_id,
        phase=v1.phase,
        manifest_revision_id=v1.manifest_revision_id,
        policy_revision_id=v1.policy_revision_id,
        budget_revision_id=v1.budget_revision_id,
        obligation_revision_id=v1.obligation_revision_id,
        provider_message_ordinal=v1.provider_message_ordinal,
        provider_transcript_digest=v1.provider_transcript_digest,
        provider_loop_continuation=v1.provider_loop_continuation,
        inflight_unit=inflight_v2,
        capability_frames=v1.capability_frames,
        artifact_ids=v1.artifact_ids,
        visible_text_artifact_id=v1.visible_text_artifact_id,
        next_action=next_v2,
        workflow_state=None,
        active_capability_continuation=None,
        pending_interrupt_id=None,
        budget_suspension=None,
    )


def _migrate_payload_v1_to_v2(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Registry migrator: canonical v1 payload dict → canonical v2 payload dict."""
    v2 = migrate_checkpoint_v1_to_v2(payload)
    return encode_checkpoint_v2(v2)


# Migration registry: (from_version, to_version) -> migrator returning payload dict.
CheckpointMigrator = Callable[[dict[str, JsonValue]], dict[str, JsonValue]]
_MIGRATION_REGISTRY: dict[tuple[int, int], CheckpointMigrator] = {
    (1, 2): _migrate_payload_v1_to_v2,
}


def register_checkpoint_migration(
    *,
    from_version: int,
    to_version: int,
    migrator: CheckpointMigrator,
) -> None:
    if from_version == to_version:
        raise ValueError("from_version and to_version must differ")
    _MIGRATION_REGISTRY[(from_version, to_version)] = migrator


def migrate_checkpoint(payload: Mapping[str, Any]) -> AnyCheckpoint:
    """Migrate payload to the latest supported version, then decode.

    Unknown source versions signal needs_reconciliation before any runtime
    object construction (including intermediate migrators that would build models).
    """
    version = _peek_schema_version(payload)
    if version is None:
        raise NeedsReconciliationError(
            "checkpoint payload missing schemaVersion",
            schema_version=None,
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise NeedsReconciliationError(
            f"unsupported schemaVersion type {type(version)!r}",
            schema_version=version,
        )
    target = max(SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS)
    if version == target:
        return decode_checkpoint(payload)
    if version not in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS and (
        version,
        target,
    ) not in _MIGRATION_REGISTRY:
        # Future/unknown: fail closed before sanitize/construct.
        raise NeedsReconciliationError(
            f"no migration path from schemaVersion={version} to {target}",
            schema_version=version,
        )
    # Also require a registered path when source is supported but not latest.
    if version in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS and (
        version,
        target,
    ) not in _MIGRATION_REGISTRY:
        # Allow stepwise hops below.
        has_step = any(
            src == version and dst <= target for (src, dst) in _MIGRATION_REGISTRY
        )
        if not has_step:
            raise NeedsReconciliationError(
                f"no migration path from schemaVersion={version} to {target}",
                schema_version=version,
            )
    current = version
    # Sanitize only after we know a path exists.
    data = _canonical_payload(payload)
    while current != target:
        migrator = _MIGRATION_REGISTRY.get((current, target))
        if migrator is None:
            # Stepwise single-version hops.
            step = None
            for (src, dst), fn in _MIGRATION_REGISTRY.items():
                if src == current and dst <= target:
                    step = (dst, fn)
                    break
            if step is None:
                raise NeedsReconciliationError(
                    f"no migration step from schemaVersion={current}",
                    schema_version=current,
                )
            next_version, migrator = step
            data = migrator(data)
            current = next_version
        else:
            data = migrator(data)
            current = target
    return decode_checkpoint(data)


def checkpoint_state_digest(checkpoint: AnyCheckpoint) -> str:
    """SHA-256 of the canonical Checkpoint payload (state_digest column)."""
    if isinstance(checkpoint, DurableAgentCheckpointV2):
        return sha256_canonical_json(encode_checkpoint_v2(checkpoint))
    if isinstance(checkpoint, DurableAgentCheckpointV1):
        return sha256_canonical_json(encode_checkpoint_v1(checkpoint))
    _fail(
        "unsupported_type",
        f"checkpoint_state_digest expected DurableAgentCheckpointV1|V2, "
        f"got {type(checkpoint)!r}",
    )


__all__ = [
    "MAX_CODEC_JSON_BYTES",
    "MAX_CODEC_JSON_DEPTH",
    "SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS",
    "AnyCheckpoint",
    "DurableCodecError",
    "NeedsReconciliationError",
    "checkpoint_state_digest",
    "decode_budget_ledger_state",
    "decode_capability_frame",
    "decode_checkpoint",
    "decode_checkpoint_v1",
    "decode_checkpoint_v2",
    "decode_grant",
    "decode_grant_set",
    "decode_obligation_ledger_state",
    "decode_policy_snapshot",
    "decode_provider_message",
    "decode_provider_message_record",
    "encode_budget_ledger_state",
    "encode_capability_frame",
    "encode_checkpoint_v1",
    "encode_checkpoint_v2",
    "encode_grant",
    "encode_grant_set",
    "encode_obligation_ledger_state",
    "encode_policy_snapshot",
    "encode_provider_message",
    "encode_provider_message_record",
    "migrate_checkpoint",
    "migrate_checkpoint_v1_to_v2",
    "register_checkpoint_migration",
    "sanitize_json_value",
]
