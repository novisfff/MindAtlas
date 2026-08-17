"""Call-owned approval binding and safe card rendering (Plan 08 Task 5)."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from app.assistant.capability_calls.contracts import (
    CapabilityCallApprovalBindingV1,
    SafeApprovalCardV1,
)
from app.assistant.domain.digests import sha256_canonical_json

_SECRET_FIELD_HINTS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "access_key",
    }
)


def compute_approval_binding_digest(
    *,
    call_id: UUID,
    logical_call_key: str,
    owner_digest: str,
    binding_contract_digest: str,
    input_digest: str,
    target_version_id: UUID | None,
    target_digest: str,
    descriptor_digest: str,
    authorization_digest: str,
    principal_digest: str,
    request_revision: int,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "capability_call_approval_binding",
            "callId": str(call_id),
            "logicalCallKey": logical_call_key,
            "ownerDigest": owner_digest,
            "bindingContractDigest": binding_contract_digest,
            "inputDigest": input_digest,
            "targetVersionId": str(target_version_id) if target_version_id else None,
            "targetDigest": target_digest,
            "descriptorDigest": descriptor_digest,
            "authorizationDigest": authorization_digest,
            "principalDigest": principal_digest,
            "requestRevision": int(request_revision),
        }
    )


def build_approval_binding(
    *,
    call_id: UUID,
    logical_call_key: str,
    owner_digest: str,
    binding_contract_digest: str,
    input_digest: str,
    target_digest: str,
    descriptor_digest: str,
    authorization_digest: str,
    principal_digest: str,
    request_revision: int = 1,
    target_version_id: UUID | None = None,
    approval_binding_digest: str | None = None,
) -> CapabilityCallApprovalBindingV1:
    digest = approval_binding_digest or compute_approval_binding_digest(
        call_id=call_id,
        logical_call_key=logical_call_key,
        owner_digest=owner_digest,
        binding_contract_digest=binding_contract_digest,
        input_digest=input_digest,
        target_version_id=target_version_id,
        target_digest=target_digest,
        descriptor_digest=descriptor_digest,
        authorization_digest=authorization_digest,
        principal_digest=principal_digest,
        request_revision=request_revision,
    )
    return CapabilityCallApprovalBindingV1(
        call_id=call_id,
        logical_call_key=logical_call_key,
        owner_digest=owner_digest,
        binding_contract_digest=binding_contract_digest,
        input_digest=input_digest,
        target_version_id=target_version_id,
        target_digest=target_digest,
        descriptor_digest=descriptor_digest,
        authorization_digest=authorization_digest,
        principal_digest=principal_digest,
        request_revision=request_revision,
        approval_binding_digest=digest,
    )


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _SECRET_FIELD_HINTS:
        return True
    return any(hint in lowered for hint in _SECRET_FIELD_HINTS)


def redact_mapping(value: Any, *, depth: int = 0) -> Any:
    """Redact secret-like keys before persistence/API/events."""
    if depth > 6:
        return "[truncated]"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            sk = str(k)
            if _is_secret_key(sk):
                out[sk] = "[redacted]"
            else:
                out[sk] = redact_mapping(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_mapping(item, depth=depth + 1) for item in value[:32]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def render_safe_approval_card(
    *,
    action_label: str,
    object_type: str,
    side_effect_class: str,
    owner_label: str,
    target_label: str,
    fields: Mapping[str, Any] | None,
    execution_mode: str,
) -> SafeApprovalCardV1:
    safe = redact_mapping(fields or {})
    summaries: list[str] = []
    if isinstance(safe, dict):
        for key, val in list(safe.items())[:20]:
            summaries.append(f"{key}={val!r}"[:200])
    is_external = side_effect_class in {"write_external", "unknown"}
    retryable = execution_mode in {
        "pure_replayable",
        "read_replayable",
        "local_transactional",
        "external_idempotent",
    }
    reconcilable = execution_mode in {
        "external_idempotent",
        "external_reconcilable",
    }
    return SafeApprovalCardV1(
        action_label=action_label,
        object_type=object_type,
        side_effect_class=side_effect_class,
        is_external=is_external,
        owner_label=owner_label,
        target_label=target_label,
        field_summaries=tuple(summaries),
        retryable=retryable,
        reconcilable=reconcilable,
    )


def authorize_call_after_approval(
    *,
    repo: Any,
    call_id: UUID,
    expected_call_revision: int,
    expected_run_revision: int,
    lease: Any,
    approval_binding: CapabilityCallApprovalBindingV1,
    expected_authorization_digest: str,
) -> Any:
    """Reject the legacy call-only mutation surface.

    Approval is now an aggregate mutation of the exact persisted
    ``capability_call`` Interrupt plus its linked Call.  Keeping this symbol as
    a fail-closed shim makes stale imports safe while preventing a caller from
    authorizing a Call without resolving that Interrupt in the same operation.
    """
    del (
        repo,
        call_id,
        expected_call_revision,
        expected_run_revision,
        lease,
        approval_binding,
        expected_authorization_digest,
    )
    from app.assistant.capability_calls.repository import CapabilityCallConflict

    raise CapabilityCallConflict(
        "approval_boundary_required",
        "use decide_call_owned to resolve the exact persisted approval Interrupt",
    )


def close_non_approved_call(
    *,
    repo: Any,
    call_id: UUID,
    expected_call_revision: int,
    expected_run_revision: int,
    lease: Any,
    outcome: str,
) -> Any:
    """Reject the legacy Call-only close surface (fail closed)."""
    del (
        repo,
        call_id,
        expected_call_revision,
        expected_run_revision,
        lease,
        outcome,
    )
    from app.assistant.capability_calls.repository import CapabilityCallConflict

    raise CapabilityCallConflict(
        "approval_boundary_required",
        "use decide_call_owned to resolve the exact persisted approval Interrupt",
    )


__all__ = [
    "build_approval_binding",
    "compute_approval_binding_digest",
    "redact_mapping",
    "render_safe_approval_card",
]
