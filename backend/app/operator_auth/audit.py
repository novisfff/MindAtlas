"""Append-only operator audit staging with allowlisted event types.

Never accepts arbitrary request bodies. Metadata values are limited to
scalar JSON-safe primitives (str / int / bool / None).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal, Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from app.operator_auth.contracts import RequestSecurityContext
from app.operator_auth.models import OperatorAuditEvent

# Allowlisted control-plane audit event types (Task 3 plan).
OPERATOR_AUDIT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "operator_account_initialized",
        "login_succeeded",
        "login_rejected",
        "login_locked",
        "session_created",
        "session_revoked",
        "session_expired",
        "session_key_revoked",
        "logout",
        "password_changed",
        "revoke_all",
        "setup_rejected",
        "csrf_rejected",
        "rbac_rejected",
        "control_plane_mutation_committed",
    }
)

OperatorAuditEventType = Literal[
    "operator_account_initialized",
    "login_succeeded",
    "login_rejected",
    "login_locked",
    "session_created",
    "session_revoked",
    "session_expired",
    "session_key_revoked",
    "logout",
    "password_changed",
    "revoke_all",
    "setup_rejected",
    "csrf_rejected",
    "rbac_rejected",
    "control_plane_mutation_committed",
]

OperatorAuditOutcome = Literal["succeeded", "rejected", "failed"]

_SAFE_METADATA_TYPES = (str, int, bool, type(None))
_EMPTY_METADATA: Mapping[str, str | int | bool | None] = MappingProxyType({})


def _validate_metadata(
    metadata: Mapping[str, object],
) -> dict[str, str | int | bool | None]:
    cleaned: dict[str, str | int | bool | None] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        if not isinstance(value, _SAFE_METADATA_TYPES):
            raise ValueError(
                "metadata values must be str, int, bool, or None "
                f"(got {type(value).__name__} for {key!r})"
            )
        # Reject bool-as-int surprises is fine; bool is explicitly allowed.
        # Reject oversized keys/values to keep audit rows bounded.
        if len(key) > 64:
            raise ValueError("metadata key too long")
        if isinstance(value, str) and len(value) > 256:
            raise ValueError("metadata string value too long")
        cleaned[key] = value  # type: ignore[assignment]
    if len(cleaned) > 32:
        raise ValueError("metadata has too many keys")
    return cleaned


class OperatorAuditRepository:
    """Stages append-only audit rows into the caller's SQLAlchemy transaction."""

    def __init__(
        self,
        db: Session,
        *,
        operator_repository: object | None = None,
    ) -> None:
        self.db = db
        # Optional shared repository for database_now(); falls back to local.
        self._operator_repository = operator_repository

    def _now(self):
        repo = self._operator_repository
        if repo is not None and hasattr(repo, "database_now"):
            return repo.database_now()  # type: ignore[no-any-return]
        # Lazy import to avoid cycles when only audit is used.
        from app.operator_auth.repository import OperatorRepository

        return OperatorRepository(self.db).database_now()

    def append(
        self,
        *,
        event_type: OperatorAuditEventType | str,
        outcome: OperatorAuditOutcome,
        context: RequestSecurityContext,
        operator_id: UUID | None,
        session_id: UUID | None,
        reason_code: str | None = None,
        metadata: Mapping[str, str | int | bool | None] = _EMPTY_METADATA,
    ) -> OperatorAuditEvent:
        if event_type not in OPERATOR_AUDIT_EVENT_TYPES:
            raise ValueError(f"unsupported audit event_type: {event_type!r}")
        if outcome not in ("succeeded", "rejected", "failed"):
            raise ValueError(f"unsupported audit outcome: {outcome!r}")
        if reason_code is not None:
            if not isinstance(reason_code, str) or len(reason_code) > 64:
                raise ValueError("reason_code must be a short string")
        safe_metadata = _validate_metadata(metadata)

        row = OperatorAuditEvent(
            event_type=event_type,
            outcome=outcome,
            occurred_at=self._now(),
            operator_id=operator_id,
            session_id=session_id,
            request_id=context.request_id,
            request_digest=context.request_digest,
            user_agent_digest=context.user_agent_digest,
            network_digest=context.network_digest,
            reason_code=reason_code,
            metadata_json=dict(safe_metadata),
        )
        self.db.add(row)
        self.db.flush()
        return row
