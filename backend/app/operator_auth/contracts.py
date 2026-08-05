"""Immutable operator-auth contracts exported to Plans 2–4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

OperatorRole = Literal["viewer", "operator"]


@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: UUID
    role: OperatorRole
    session_id: UUID
    authentication_method: Literal["password_session"] = "password_session"

    @property
    def principal_id(self) -> str:
        return str(self.operator_id)

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"

    def audit_actor(self) -> str:
        return str(self.operator_id)


@dataclass(frozen=True)
class OperatorAuthAvailability:
    available: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class IssuedSession:
    principal: OperatorPrincipal
    session_cookie_value: str
    csrf_cookie_value: str
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool


@dataclass(frozen=True)
class SetupAuthorization:
    """Marker that Setup Token validation succeeded for this request.

    Never carries the raw Setup Token.
    """

    validated: bool = True


@dataclass(frozen=True)
class RequestSecurityContext:
    """Safe request metadata digests for audit and session binding.

    Never carries raw IP, User-Agent, cookies, or Authorization material.
    """

    request_id: str
    request_digest: str
    user_agent_digest: str
    network_digest: str
