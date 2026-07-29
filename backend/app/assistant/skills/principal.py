"""Skill package admin principal re-export (canonical OperatorPrincipal).

The only production HTTP constructor of ``OperatorPrincipal`` is successful
validation of a durable password session. Skill admin / eval services consume
the same frozen dataclass via compatibility properties ``principal_id``,
``is_operator``, and ``audit_actor()``.
"""

from __future__ import annotations

from app.operator_auth.contracts import OperatorPrincipal, OperatorRole

__all__ = ["OperatorPrincipal", "OperatorRole"]
