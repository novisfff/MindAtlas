"""Atomic Operator-owned system initialization transaction.

The coordinator is the sole commit owner for first-account + core staging +
trusted assistant bootstrap + the clean initialization marker. Lower services
only stage rows (``commit=False`` / flush only).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.runtime.bootstrap import (
    AssistantBootstrapRejected,
    AssistantSystemBootstrapper,
    PreparedAssistantBootstrap,
    StageAssistantBootstrapRequest,
)
from app.common.exceptions import ApiException
from app.config import get_settings
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
    prepared_rollout_revision_id: UUID
    rollout_control_revision: int
    seed_manifest_digest: str

    def to_response(self) -> InitializationCompletionResponse:
        return InitializationCompletionResponse(
            initialized=True,
            locale=self.locale,
            assistant_bootstrap="pending_worker",
            prepared_rollout_revision_id=self.prepared_rollout_revision_id,
            rollout_control_revision=self.rollout_control_revision,
        )


class InitializationCoordinator:
    """Lock → fresh permit → operator → core → bootstrap → marker → single commit."""

    def __init__(
        self,
        db: Session,
        *,
        repository: OperatorRepository | None = None,
        audit: OperatorAuditRepository | None = None,
        assistant_bootstrapper: AssistantSystemBootstrapper | None = None,
    ) -> None:
        self.db = db
        self.repository = repository or OperatorRepository(db)
        self.audit = audit or OperatorAuditRepository(
            db, operator_repository=self.repository
        )
        self.assistant_bootstrapper = assistant_bootstrapper or AssistantSystemBootstrapper(
            db
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
        assistant: PreparedAssistantBootstrap | None = None

        try:
            self.repository.lock_initialization()
            self._assert_clean_uninitialized(system_service)
            # Fresh permit BEFORE operator is staged (Task 4 contract).
            fresh_permit = (
                self.assistant_bootstrapper.lock_and_verify_fresh_preconditions()
            )
            account = self.stage_initial_account(request.operator_password)
            core = system_service.stage_core_initialization(request)
            assistant = self.assistant_bootstrapper.stage_bootstrap(
                StageAssistantBootstrapRequest(
                    operator_id=account.id,
                    operator_session_id=None,
                    model_id=core.llm_model_id,
                    build_revision=get_settings().app_build_revision,
                    fresh_permit=fresh_permit,
                )
            )
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
                metadata={
                    "assistantBootstrap": "prepared",
                    "rolloutRevisionDigest": assistant.rollout_revision_digest,
                    "rolloutControlRevision": int(assistant.rollout_control_revision),
                    "seedManifestDigest": assistant.seed_manifest_digest,
                },
            )
            self.db.commit()
        except ApiException:
            self.db.rollback()
            raise
        except AssistantBootstrapRejected as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40970,
                message=str(exc.reason_code),
            ) from exc
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

        assert account is not None and core is not None and assistant is not None
        return InitializationCommitResult(
            operator_account_id=account.id,
            locale=core.locale,
            llm_model_id=core.llm_model_id,
            credential_id=core.credential_id,
            prepared_rollout_revision_id=assistant.rollout_revision_id,
            rollout_control_revision=int(assistant.rollout_control_revision),
            seed_manifest_digest=assistant.seed_manifest_digest,
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
