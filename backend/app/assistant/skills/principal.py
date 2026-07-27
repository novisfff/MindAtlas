"""Skill package admin principal and role contracts (Plan 09).

No real authenticated principal dependency exists in the product yet. Service
methods still require a verified ``OperatorPrincipal`` value so a direct service
call cannot bypass the future router guard. Trusted-dev/test mounts may mint a
principal only behind an explicit environment guard; that path is not release
evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from app.common.schemas import CamelModel


PrincipalRole = Literal["operator", "viewer"]


class OperatorPrincipal(CamelModel):
    """Server-verified principal for Plan 09 admin mutations.

    Privilege is carried by the concrete ``role`` field. Callers must not pass
    an ``isAdmin`` boolean; service methods inspect this value only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str = Field(min_length=1, max_length=128)
    role: PrincipalRole
    display_name: str | None = Field(default=None, max_length=256)

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"

    def audit_actor(self) -> str:
        """Stable actor string for ``archived_by`` / ``disabled_by`` evidence."""
        return self.principal_id[:128]
