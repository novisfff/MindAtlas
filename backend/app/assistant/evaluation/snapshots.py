"""Snapshot projection policy for evaluation read_snapshot mode (Plan 09 Task 4).

``read_snapshot`` is default-disabled. When explicitly authorized, a separately
authorized builder creates an immutable evaluation-owned projection from a
versioned per-source field allowlist. Hard-denied fields are rejected regardless
of allowlist entries. Wildcards and "all columns except..." are forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.evaluation.contracts import RuntimeIsolationContext

# Hard-denied regardless of allowlist (plan §Test Runtime Isolation Contract).
HARD_DENIED_FIELD_MARKERS: frozenset[str] = frozenset(
    {
        "password",
        "api_key",
        "apikey",
        "secret",
        "token",
        "authorization",
        "cookie",
        "credential",
        "encrypted",
        "private_key",
        "presigned",
        "signed_url",
        "access_key",
        "private_email",
        "ssn",
        "raw_header",
        "set_cookie",
    }
)

# Explicit canary values used in tests to prove secrets never leak into evidence.
SECRET_CANARY_VALUES: frozenset[str] = frozenset(
    {
        "CANARY_API_KEY_DO_NOT_LEAK",
        "CANARY_AUTHORIZATION_BEARER_DO_NOT_LEAK",
        "CANARY_COOKIE_SESSION_DO_NOT_LEAK",
        "CANARY_SIGNED_URL_DO_NOT_LEAK",
        "CANARY_ENCRYPTED_SECRET_DO_NOT_LEAK",
        "CANARY_PRIVATE_IDENTITY_DO_NOT_LEAK",
    }
)

# Default-disabled: builders must opt in with explicit authorization.
READ_SNAPSHOT_DEFAULT_ENABLED = False

MAX_SNAPSHOT_ROWS = 100
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_FIELD_VALUE_LEN = 4096


class SnapshotPolicyError(ValueError):
    """Snapshot projection failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SnapshotProjectionPolicy:
    """Versioned per-source field allowlist. No wildcards."""

    source_type: str
    policy_version: str
    allowed_fields: tuple[str, ...]
    max_rows: int = MAX_SNAPSHOT_ROWS
    max_bytes: int = MAX_SNAPSHOT_BYTES
    actor_scope: str = "evaluation"
    source_revision: str = "1"
    ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        if not self.source_type or not str(self.source_type).strip():
            raise SnapshotPolicyError("invalid_source", "source_type required")
        if not self.policy_version:
            raise SnapshotPolicyError("invalid_version", "policy_version required")
        if not self.allowed_fields:
            raise SnapshotPolicyError("empty_allowlist", "allowed_fields must be non-empty")
        for name in self.allowed_fields:
            if name in {"*", "...", "ALL", "all"}:
                raise SnapshotPolicyError(
                    "wildcard_forbidden",
                    "wildcards and 'all columns except' are forbidden",
                )
            if _field_is_hard_denied(name):
                raise SnapshotPolicyError(
                    "hard_denied_allowlist",
                    f"allowlist cannot include hard-denied field {name!r}",
                )
        if self.max_rows <= 0 or self.max_bytes <= 0:
            raise SnapshotPolicyError("invalid_ceiling", "max_rows/max_bytes must be > 0")

    @property
    def policy_digest(self) -> str:
        return sha256_canonical_json(
            {
                "source_type": self.source_type,
                "policy_version": self.policy_version,
                "allowed_fields": list(self.allowed_fields),
                "max_rows": self.max_rows,
                "max_bytes": self.max_bytes,
                "actor_scope": self.actor_scope,
                "source_revision": self.source_revision,
                "ttl_seconds": self.ttl_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class EvaluationDataSnapshot:
    """Immutable evaluation-owned projection. Runner never queries production."""

    snapshot_id: UUID
    source_type: str
    policy_digest: str
    source_revision: str
    rows: tuple[dict[str, Any], ...]
    content_digest: str
    created_at: datetime
    expires_at: datetime
    byte_size: int

    def as_store_value(self) -> dict[str, Any]:
        return {
            "snapshot_id": str(self.snapshot_id),
            "source_type": self.source_type,
            "policy_digest": self.policy_digest,
            "source_revision": self.source_revision,
            "rows": list(self.rows),
            "content_digest": self.content_digest,
            "byte_size": self.byte_size,
        }


# Registry of versioned policies (explicit allowlists only).
_POLICY_REGISTRY: dict[str, SnapshotProjectionPolicy] = {}


def register_snapshot_policy(policy: SnapshotProjectionPolicy) -> SnapshotProjectionPolicy:
    key = f"{policy.source_type}@{policy.policy_version}"
    _POLICY_REGISTRY[key] = policy
    return policy


def get_snapshot_policy(source_type: str, policy_version: str) -> SnapshotProjectionPolicy:
    key = f"{source_type}@{policy_version}"
    policy = _POLICY_REGISTRY.get(key)
    if policy is None:
        raise SnapshotPolicyError(
            "unknown_policy",
            f"no SnapshotProjectionPolicy for {key!r}",
        )
    return policy


def _field_is_hard_denied(name: str) -> bool:
    lower = str(name).lower()
    return any(marker in lower for marker in HARD_DENIED_FIELD_MARKERS)


def payload_contains_hard_denied_keys(payload: Mapping[str, Any] | None) -> list[str]:
    """Return hard-denied key names found (case-insensitive substring match)."""
    if not payload:
        return []
    hits: list[str] = []
    for key in payload.keys():
        if _field_is_hard_denied(str(key)):
            hits.append(str(key))
    return hits


def payload_contains_secret_canaries(value: Any) -> list[str]:
    """Detect canary secret values anywhere in a nested structure."""
    hits: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            for canary in SECRET_CANARY_VALUES:
                if canary in node:
                    hits.append(canary)
        elif isinstance(node, Mapping):
            for k, v in node.items():
                if isinstance(k, str):
                    for canary in SECRET_CANARY_VALUES:
                        if canary in k:
                            hits.append(canary)
                walk(v)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                walk(item)

    walk(value)
    return hits


def assert_payload_safe(payload: Mapping[str, Any] | None, *, context: str) -> None:
    hits = payload_contains_hard_denied_keys(payload)
    if hits:
        raise ValueError(f"{context} contains hard-denied fields: {hits}")
    canaries = payload_contains_secret_canaries(payload)
    if canaries:
        raise ValueError(f"{context} contains secret canaries: {canaries}")


def assert_evidence_safe(value: Any, *, context: str) -> None:
    """Second defense: event/Artifact/assertion/gate writers re-check canaries."""
    if isinstance(value, Mapping):
        assert_payload_safe(value, context=context)
        return
    canaries = payload_contains_secret_canaries(value)
    if canaries:
        raise ValueError(f"{context} contains secret canaries: {canaries}")


def project_row(
    source_row: Mapping[str, Any],
    *,
    policy: SnapshotProjectionPolicy,
) -> dict[str, Any]:
    """Copy only allowlisted fields; hard-deny credentials/private values."""
    projected: dict[str, Any] = {}
    for field_name in policy.allowed_fields:
        if field_name not in source_row:
            continue
        if _field_is_hard_denied(field_name):
            raise SnapshotPolicyError(
                "hard_denied_field",
                f"hard-denied field {field_name!r} cannot be projected",
            )
        value = source_row[field_name]
        if isinstance(value, str) and len(value) > MAX_FIELD_VALUE_LEN:
            raise SnapshotPolicyError(
                "field_too_large",
                f"field {field_name!r} exceeds max length",
            )
        # Reject canary/secret values even if field name slipped through.
        if isinstance(value, str):
            for canary in SECRET_CANARY_VALUES:
                if canary in value:
                    raise SnapshotPolicyError(
                        "secret_canary",
                        f"field {field_name!r} contains hard-denied secret value",
                    )
            lower_val = value.lower()
            if any(
                marker in lower_val
                for marker in ("bearer ", "eyj", "-----begin", "presigned")
            ):
                # Heuristic fail-closed for credential-shaped strings.
                raise SnapshotPolicyError(
                    "credential_shaped_value",
                    f"field {field_name!r} looks like a credential",
                )
        projected[field_name] = value
    # Any hard-denied keys present on source (even if not allowlisted) must not
    # leak — we simply never copy them. Unknown keys are dropped.
    return projected


def build_evaluation_snapshot(
    *,
    source_type: str,
    policy_version: str,
    source_rows: Sequence[Mapping[str, Any]],
    source_revision: str | None = None,
    authorized: bool = False,
    now: datetime | None = None,
    snapshot_id: UUID | None = None,
) -> EvaluationDataSnapshot:
    """Build immutable evaluation-owned projection. Default-disabled without auth."""
    if not authorized and not READ_SNAPSHOT_DEFAULT_ENABLED:
        raise SnapshotPolicyError(
            "read_snapshot_disabled",
            "read_snapshot is default-disabled; explicit authorization required",
        )
    policy = get_snapshot_policy(source_type, policy_version)
    if len(source_rows) > policy.max_rows:
        raise SnapshotPolicyError(
            "row_ceiling",
            f"source has {len(source_rows)} rows; max is {policy.max_rows}",
        )
    projected_rows: list[dict[str, Any]] = []
    for row in source_rows:
        # Fail if source tries to pass hard-denied keys that match allowlist
        # (already blocked by policy construction) or credential-shaped values.
        denied_present = payload_contains_hard_denied_keys(row)
        # Presence on source is fine; we just never project them. But if an
        # allowlisted field somehow maps to a denied name, project_row raises.
        del denied_present
        projected_rows.append(project_row(row, policy=policy))

    content = {
        "source_type": source_type,
        "policy_digest": policy.policy_digest,
        "source_revision": source_revision or policy.source_revision,
        "rows": projected_rows,
    }
    content_digest = sha256_canonical_json(content)
    # Approximate byte size via canonical encoding length of rows.
    import json

    encoded = json.dumps(projected_rows, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > policy.max_bytes:
        raise SnapshotPolicyError(
            "byte_ceiling",
            f"projection is {len(encoded)} bytes; max is {policy.max_bytes}",
        )
    # Final canary pass on the projection itself.
    assert_evidence_safe(projected_rows, context="snapshot.rows")

    current = now or datetime.now(timezone.utc)
    expires = current + timedelta(seconds=int(policy.ttl_seconds))
    return EvaluationDataSnapshot(
        snapshot_id=snapshot_id or uuid4(),
        source_type=source_type,
        policy_digest=policy.policy_digest,
        source_revision=source_revision or policy.source_revision,
        rows=tuple(projected_rows),
        content_digest=content_digest,
        created_at=current,
        expires_at=expires,
        byte_size=len(encoded),
    )


def isolation_requires_snapshot(ctx: RuntimeIsolationContext) -> bool:
    return ctx.data_mode == "read_snapshot"


def assert_isolation_snapshot_fields(ctx: RuntimeIsolationContext) -> None:
    """Fixture mode nulls snapshot fields; read_snapshot requires both."""
    if ctx.data_mode == "fixture":
        if ctx.data_snapshot_id is not None or ctx.snapshot_projection_policy_digest is not None:
            raise SnapshotPolicyError(
                "fixture_shape",
                "data_mode=fixture requires null snapshot fields",
            )
        return
    if ctx.data_snapshot_id is None or ctx.snapshot_projection_policy_digest is None:
        raise SnapshotPolicyError(
            "snapshot_shape",
            "data_mode=read_snapshot requires data_snapshot_id and policy digest",
        )


# Register a minimal safe policy for interactive fixture tests.
register_snapshot_policy(
    SnapshotProjectionPolicy(
        source_type="entry_summary",
        policy_version="v1",
        allowed_fields=("id", "title", "type_key", "created_at"),
        max_rows=50,
        max_bytes=64 * 1024,
        actor_scope="evaluation",
        source_revision="1",
        ttl_seconds=3600,
    )
)


__all__ = [
    "HARD_DENIED_FIELD_MARKERS",
    "MAX_FIELD_VALUE_LEN",
    "MAX_SNAPSHOT_BYTES",
    "MAX_SNAPSHOT_ROWS",
    "READ_SNAPSHOT_DEFAULT_ENABLED",
    "SECRET_CANARY_VALUES",
    "EvaluationDataSnapshot",
    "SnapshotPolicyError",
    "SnapshotProjectionPolicy",
    "assert_evidence_safe",
    "assert_isolation_snapshot_fields",
    "assert_payload_safe",
    "build_evaluation_snapshot",
    "get_snapshot_policy",
    "isolation_requires_snapshot",
    "payload_contains_hard_denied_keys",
    "payload_contains_secret_canaries",
    "project_row",
    "register_snapshot_policy",
]
