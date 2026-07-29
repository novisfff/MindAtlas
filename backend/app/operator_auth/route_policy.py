"""Parent routers and verified route-policy dependencies.

Every application route is mounted under exactly one parent whose dependency
callable carries a stable ``__route_policy__`` marker. Inventory tests walk the
FastAPI dependency graph for that attribute — never path-name inference alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.common.exceptions import ApiException
from app.config import Settings, get_settings
from app.database import get_db
from app.operator_auth.audit import OperatorAuditRepository
from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.dependencies import (
    CODE_OPERATOR_ROLE_REQUIRED,
    request_security_context,
    require_csrf,
    require_viewer_principal,
)

POLICY_PUBLIC: Final = "public"
POLICY_CREDENTIAL_EXCHANGE: Final = "credential_exchange"
POLICY_SETUP_INITIALIZATION: Final = "setup_initialization"
POLICY_PROTECTED_BROWSER: Final = "protected_browser"
POLICY_AUTHENTICATED_MACHINE: Final = "authenticated_machine"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def require_public_route_policy() -> None:
    """Marker-only policy for anonymous safe reads (liveness / init status)."""
    return None


require_public_route_policy.__route_policy__ = POLICY_PUBLIC  # type: ignore[attr-defined]


def require_credential_exchange_policy() -> None:
    """Marker-only policy for password login (handler enforces origin + secret)."""
    return None


require_credential_exchange_policy.__route_policy__ = POLICY_CREDENTIAL_EXCHANGE  # type: ignore[attr-defined]


def require_setup_policy() -> None:
    """Marker-only policy for Setup-token initialization (handler enforces Setup)."""
    return None


require_setup_policy.__route_policy__ = POLICY_SETUP_INITIALIZATION  # type: ignore[attr-defined]


def require_browser_route_policy(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    principal: OperatorPrincipal = Depends(require_viewer_principal),
) -> Iterator[OperatorPrincipal]:
    """Browser control-plane policy.

    Safe methods require a viewer principal (session cookie). Unsafe methods
    require Operator + CSRF and stage a generic
    ``control_plane_mutation_committed`` audit row on the same request Session
    before the endpoint runs.

    ``require_viewer_principal`` is taken via ``Depends`` so FastAPI caches the
    session resolution for endpoint-level deps and does not re-touch/commit the
    session after the audit row is staged.

    The event is pending in the shared SQLAlchemy transaction:
    - if the endpoint/service commits, the mutation and event commit together;
    - if it only flushes, successful dependency teardown commits both;
    - if the endpoint raises, teardown rolls back both.
    """
    method = (request.method or "").upper()
    if method not in _UNSAFE_METHODS:
        yield principal
        return

    if principal.role != "operator":
        raise ApiException(
            status_code=403,
            code=CODE_OPERATOR_ROLE_REQUIRED,
            message="operator_role_required",
        )
    # CSRF runs after the cached principal resolution; pass principal explicitly
    # so this plain-function call does not re-enter session resolution.
    require_csrf(request, db=db, settings=settings, principal=principal)

    context = request_security_context(request, settings)
    route = request.scope.get("route")
    route_name = getattr(route, "name", None) or ""
    if not isinstance(route_name, str):
        route_name = str(route_name)
    # Bound route names; never store raw path/query/body/headers.
    if len(route_name) > 128:
        route_name = route_name[:128]

    OperatorAuditRepository(db).append(
        event_type="control_plane_mutation_committed",
        outcome="succeeded",
        context=context,
        operator_id=principal.operator_id,
        session_id=principal.session_id,
        metadata={
            "method": method,
            "routeName": route_name,
        },
    )

    try:
        yield principal
    except Exception:
        db.rollback()
        raise
    else:
        # Flush-only endpoints leave the staged audit (and domain rows) pending.
        # Services that already committed leave no open work; commit is then a
        # no-op or opens/closes an empty transaction harmlessly.
        try:
            if db.in_transaction():
                db.commit()
        except Exception:
            db.rollback()
            raise


require_browser_route_policy.__route_policy__ = POLICY_PROTECTED_BROWSER  # type: ignore[attr-defined]


def require_openclaw_machine_principal(
    request: Request,
    db: Session = Depends(get_db),
):
    """Resolve OpenClaw Bearer authority into ``OpenClawRuntimeAuditContext``.

    Never returns or accepts an ``OperatorPrincipal``. Session cookies alone
    cannot satisfy this dependency.
    """
    from app.openclaw_integration.service import OpenClawIntegrationService

    return OpenClawIntegrationService(db).authorize_runtime_request(request)


def require_authenticated_machine_policy(
    context=Depends(require_openclaw_machine_principal),  # noqa: ANN001
):
    """Router-level machine policy marker wrapping OpenClaw Bearer auth."""
    return context


require_authenticated_machine_policy.__route_policy__ = POLICY_AUTHENTICATED_MACHINE  # type: ignore[attr-defined]


def public_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(require_public_route_policy)])


def credential_exchange_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(require_credential_exchange_policy)])


def setup_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(require_setup_policy)])


def protected_browser_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(require_browser_route_policy)])


def machine_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(require_authenticated_machine_policy)])


__all__ = [
    "POLICY_AUTHENTICATED_MACHINE",
    "POLICY_CREDENTIAL_EXCHANGE",
    "POLICY_PROTECTED_BROWSER",
    "POLICY_PUBLIC",
    "POLICY_SETUP_INITIALIZATION",
    "credential_exchange_router",
    "machine_router",
    "protected_browser_router",
    "public_router",
    "require_authenticated_machine_policy",
    "require_browser_route_policy",
    "require_credential_exchange_policy",
    "require_openclaw_machine_principal",
    "require_public_route_policy",
    "require_setup_policy",
    "setup_router",
]
