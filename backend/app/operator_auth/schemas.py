"""JSON request/response models for operator auth HTTP routes.

Never serialize passwords, session IDs, Operator IDs, raw tokens, digests,
password revision, key IDs, or request fingerprints on unauthenticated surfaces.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.common.schemas import CamelModel
from app.operator_auth.contracts import OperatorRole


class OperatorLoginRequest(CamelModel):
    password: str = Field(min_length=1, max_length=1024)


class OperatorSessionResponse(CamelModel):
    authenticated: bool
    role: OperatorRole | None = None
    idle_expires_at: datetime | None = Field(default=None, alias="idleExpiresAt")
    absolute_expires_at: datetime | None = Field(default=None, alias="absoluteExpiresAt")


class OperatorPasswordChangeRequest(CamelModel):
    current_password: str = Field(alias="currentPassword", min_length=1, max_length=1024)
    new_password: str = Field(alias="newPassword", min_length=1, max_length=1024)


class OperatorRevokeAllRequest(CamelModel):
    reason: str = Field(min_length=1, max_length=256)


class OperatorAuthAvailabilityResponse(CamelModel):
    available: bool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
