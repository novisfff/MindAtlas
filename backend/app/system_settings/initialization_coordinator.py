"""Atomic Operator-owned system initialization transaction.

The coordinator is the sole commit owner for first-account + core staging + the
clean initialization marker. Lower services only stage rows (``commit=False``).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.operator_auth.audit import OperatorAuditRepository
from app.operator_auth.contracts import RequestSecurityContext, SetupAuthorization
from app.operator_auth.models import OperatorAccount
from app.operator_auth.repository import OperatorRepository
from app.system_settings.initialization_service import SystemInitializationService
from app.system_settings.schemas import (
    InitializationCompletionResponse,
    InitializeSystemRequest,
)
from app.system_settings.service import SystemLocale


@dataclass(frozen=True)
class InitializationCommitResult:
    """Durable outcome of the outer initialization transaction (pre-session)."""

    operator_account_id: UUID
    locale: SystemLocale
    llm_model_id: UUID
    credential_id: UUID

    def to_response(self) -> InitializationCompletionResponse:
        return InitializationCompletionResponse(
            initialized=True,
            locale=self.locale,
        )


class InitializationCoordinator:
    """Lock → seed operator → stage core → marker → single commit."""

    def __init__(
        self,
        db: Session,
        *,
        repository: OperatorRepository | None = None,
        audit: OperatorAuditRepository | None = None,
    ) -> None:
        self.db = db
        self.repository = repository or OperatorRepository(db)
        self.audit = audit or OperatorAuditRepository(
            db, operator_repository=self.repository
        )

    def stage_initial_account(self, password: str) -> OperatorAccount:
        """Stage the singleton enabled operator account (no commit)."""
        return self.repository.seed_account(
            password=password,
            role="operator",
            enabled=True,
        )

    def initialize(
        self,
        request: InitializeSystemRequest,
        *,
        setup_authorization: SetupAuthorization,
        request_context: RequestSecurityContext,
    ) -> InitializationCommitResult:
        """Run the singleton initialization transaction.

        ``setup_authorization`` must already have been validated by the HTTP
        boundary; it is accepted here only as a typed proof, never as a secret.
        """
        if not setup_authorization.validated:
            raise ApiException(
                status_code=401,
                code=40112,
                message="invalid_setup_authorization",
            )

        system_service = SystemInitializationService(self.db)
        account: OperatorAccount | None = None
        core = None

        try:
            self.repository.lock_initialization()
            self._assert_clean_uninitialized(system_service)
            account = self.stage_initial_account(request.operator_password)
            core = system_service.stage_core_initialization(request)
            system_service.stage_initialization_marker(
                locale=core.locale,
                source="user",
            )
            self.audit.append(
                event_type="operator_account_initialized",
                outcome="succeeded",
                context=request_context,
                operator_id=account.id,
                session_id=None,
            )
            self.db.commit()
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40970,
                message="system_already_initialized",
            ) from exc
        except RuntimeError as exc:
            self.db.rollback()
            if str(exc) == "system_already_initialized":
                raise ApiException(
                    status_code=409,
                    code=40970,
                    message="system_already_initialized",
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

        # Post-commit side effects only — never part of the atomic unit of work.
        system_service.after_commit()

        assert account is not None and core is not None
        return InitializationCommitResult(
            operator_account_id=account.id,
            locale=core.locale,
            llm_model_id=core.llm_model_id,
            credential_id=core.credential_id,
        )

    def _assert_clean_uninitialized(
        self, system_service: SystemInitializationService
    ) -> None:
        """Reject when a clean marker or singleton operator already exists."""
        if system_service.is_initialized():
            raise ApiException(
                status_code=409,
                code=40970,
                message="system_already_initialized",
            )
        try:
            self.repository.assert_uninitialized()
        except RuntimeError as exc:
            if str(exc) == "system_already_initialized":
                raise ApiException(
                    status_code=409,
                    code=40970,
                    message="system_already_initialized",
                ) from exc
            raise
