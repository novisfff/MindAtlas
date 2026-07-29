"""Shared assistant readiness evaluator (Plan 2 Task 5).

One evaluator for observational readiness and (later) admission. No writes,
no Provider calls, no activation, no Worker registration.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai_registry.models import AiComponentBinding, AiModel
from app.assistant.durable.worker_registry import WorkerRegistry
from app.assistant.runtime.closure import (
    AssistantRuntimeClosureBuilder,
    RuntimeClosureDrift,
)
from app.assistant.runtime.contracts import (
    RUNTIME_READINESS_REASON_CODES,
    AssistantReadinessSnapshot,
    AssistantRuntimeClosure,
)
from app.assistant.runtime.models import AssistantMainAgentRolloutControl
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.assistant.runtime.seed import (
    SystemSeedInvalid,
    load_verified_assistant_system_seed,
)
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
)
from app.assistant.skills.schemas import MainAgentProfileSnapshotV2
from app.config import Settings, get_settings
from app.operator_auth.dependencies import load_session_mac_key_ring
from app.operator_auth.models import OperatorAccount
from app.operator_auth.service import OperatorAuthService
from app.operator_auth.tokens import SessionMacKeyRing
from app.system_settings.initialization_service import SystemInitializationService


class RuntimeSchemaCompatibility(Protocol):
    def is_compatible(self, db: Session) -> bool: ...


def read_single_alembic_version(db: Session) -> str | None:
    """Return the sole alembic_version row, or None if missing/ambiguous."""
    try:
        rows = db.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    except Exception:
        return None
    if len(rows) != 1:
        return None
    value = rows[0][0]
    return str(value) if value is not None else None


class Plan2AlembicHeadCompatibility:
    """Interim Plan 2 schema gate; Plan 3 replaces with pre_ga_v1 identity."""

    expected_head = "b6e2d4f8a901"

    def is_compatible(self, db: Session) -> bool:
        return read_single_alembic_version(db) == self.expected_head


class _DefaultSeedProbe:
    def is_valid(self) -> bool:
        try:
            load_verified_assistant_system_seed()
            return True
        except SystemSeedInvalid:
            return False
        except Exception:
            return False


class _DefaultInitializationProbe:
    def is_initialized(self, db: Session) -> bool:
        return SystemInitializationService(db).is_initialized()


class _DefaultOperatorProbe:
    def operator_exists(self, db: Session) -> bool:
        row = (
            db.query(OperatorAccount)
            .filter(OperatorAccount.singleton_key == "operator")
            .one_or_none()
        )
        return row is not None and bool(row.enabled)


class _DefaultProfileProbe:
    def has_published_v2(self, db: Session) -> bool:
        profile = (
            db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.published_version_id.isnot(None))
            .first()
        )
        if profile is None or profile.published_version_id is None:
            return False
        version = db.get(
            AssistantMainAgentProfileVersion, profile.published_version_id
        )
        if version is None:
            return False
        try:
            MainAgentProfileSnapshotV2.model_validate(version.snapshot or {})
        except Exception:
            return False
        return True


class _DefaultModelProbe:
    def has_active_assistant_binding(self, db: Session) -> bool:
        binding = (
            db.query(AiComponentBinding)
            .filter(AiComponentBinding.component == "assistant")
            .one_or_none()
        )
        if binding is None or binding.llm_model_id is None:
            return False
        model = db.get(AiModel, binding.llm_model_id)
        return model is not None and str(model.model_type or "") == "llm"


_KEY_RING_UNSET = object()


class AssistantReadinessService:
    """Observational readiness + lockable admission evaluator."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | Any | None = None,
        schema_compatibility: RuntimeSchemaCompatibility | None = None,
        key_ring: SessionMacKeyRing | None | object = _KEY_RING_UNSET,
        seed_probe: Any | None = None,
        initialization_probe: Any | None = None,
        operator_probe: Any | None = None,
        profile_probe: Any | None = None,
        model_probe: Any | None = None,
        closure_builder: AssistantRuntimeClosureBuilder | None = None,
    ) -> None:
        self.db = db
        self.settings = settings if settings is not None else get_settings()
        self.schema_compatibility = (
            schema_compatibility
            if schema_compatibility is not None
            else Plan2AlembicHeadCompatibility()
        )
        # Mirror build_operator_auth_service → load_session_mac_key_ring(settings).
        # Omitted key_ring loads from settings; explicit None stays injectable for tests.
        if key_ring is _KEY_RING_UNSET:
            self.key_ring = load_session_mac_key_ring(self.settings)
        else:
            self.key_ring = key_ring  # type: ignore[assignment]
        self.seed_probe = seed_probe if seed_probe is not None else _DefaultSeedProbe()
        self.initialization_probe = (
            initialization_probe
            if initialization_probe is not None
            else _DefaultInitializationProbe()
        )
        self.operator_probe = (
            operator_probe if operator_probe is not None else _DefaultOperatorProbe()
        )
        self.profile_probe = (
            profile_probe if profile_probe is not None else _DefaultProfileProbe()
        )
        self.model_probe = (
            model_probe if model_probe is not None else _DefaultModelProbe()
        )
        self.repo = AssistantRuntimeRepository(db)
        self.closure_builder = closure_builder or AssistantRuntimeClosureBuilder(db)

    def evaluate(self) -> AssistantReadinessSnapshot:
        """Observational readiness: no writes, no locks required."""
        # begin_nested is a no-op savepoint for isolation; never commits.
        try:
            nested = self.db.begin_nested()
        except Exception:
            nested = None
        try:
            control = self.repo.get_control()
            return self._evaluate(control=control, lock=False)
        finally:
            if nested is not None:
                try:
                    nested.rollback()
                except Exception:
                    pass

    def evaluate_locked(
        self,
        *,
        control: AssistantMainAgentRolloutControl,
    ) -> AssistantReadinessSnapshot:
        """Admission/activation path: control already selected FOR UPDATE."""
        return self._evaluate(control=control, lock=True)

    def evaluate_activation_candidate_locked(
        self,
        *,
        control: AssistantMainAgentRolloutControl,
        candidate: AssistantRuntimeClosure,
    ) -> AssistantReadinessSnapshot:
        """Activation path: evaluate a candidate closure instead of the active pointer.

        Ignores ``rollout_inactive`` (candidate may become the first active) and
        ``new_runs_disabled`` (Operator may switch while emergency ceilings stay
        closed). Still requires initialization, Operator/auth, seed, Profile,
        Model, schema, and a compatible Worker for the candidate.
        """
        return self._evaluate(
            control=control,
            lock=True,
            candidate=candidate,
            ignore_rollout_inactive=True,
            ignore_new_runs_disabled=True,
        )

    def _evaluate(
        self,
        *,
        control: AssistantMainAgentRolloutControl | None,
        lock: bool,
        candidate: AssistantRuntimeClosure | None = None,
        ignore_rollout_inactive: bool = False,
        ignore_new_runs_disabled: bool = False,
    ) -> AssistantReadinessSnapshot:
        # Note: schema_incompatible is listed after worker_unavailable in the
        # public reason-code tuple, but structural schema failure short-circuits
        # first so a broken migration never fabricates downstream IDs.
        if not self.schema_compatibility.is_compatible(self.db):
            return self._blocked("schema_incompatible")
        if not self.initialization_probe.is_initialized(self.db):
            return self._blocked("system_not_initialized")
        if not self.operator_probe.operator_exists(self.db):
            return self._blocked("operator_missing")
        availability = OperatorAuthService(
            self.db, key_ring=self.key_ring, auto_commit=False
        ).availability()
        if not availability.available:
            return self._blocked("operator_auth_unavailable")
        if not self.seed_probe.is_valid():
            return self._blocked("system_seed_invalid")
        if not self.profile_probe.has_published_v2(self.db):
            return self._blocked("profile_unpublished")
        if not self.model_probe.has_active_assistant_binding(self.db):
            return self._blocked("model_unbound")
        if candidate is None:
            if control is None or control.active_rollout_revision_id is None:
                if ignore_rollout_inactive:
                    return self._blocked("rollout_inactive")
                return self._blocked("rollout_inactive")
            try:
                closure = self.closure_builder.build(
                    rollout_revision_id=control.active_rollout_revision_id,
                    lock=lock,
                )
            except RuntimeClosureDrift:
                return self._blocked(
                    "runtime_closure_drift",
                    active_rollout_revision_id=control.active_rollout_revision_id,
                )
            except Exception:
                return self._blocked(
                    "runtime_closure_drift",
                    active_rollout_revision_id=control.active_rollout_revision_id,
                )
        else:
            closure = candidate

        reasons: set[str] = set()
        if not ignore_new_runs_disabled:
            if not bool(getattr(self.settings, "assistant_new_runs_enabled", True)):
                reasons.add("new_runs_disabled")
            if control is not None and not bool(control.new_runs_enabled):
                reasons.add("new_runs_disabled")
        workers = self._compatible_workers(closure)
        if not workers:
            reasons.add("worker_unavailable")
        ordered = tuple(
            code for code in RUNTIME_READINESS_REASON_CODES if code in reasons
        )
        return AssistantReadinessSnapshot(
            ready=not ordered,
            reason_codes=ordered,
            active_rollout_revision_id=closure.rollout_revision_id,
            profile_version_id=closure.profile_version_id,
            model_id=closure.model_id,
            compatible_worker_ids=tuple(row.worker_id for row in workers),
            build_revision=str(
                getattr(self.settings, "app_build_revision", "") or ""
            ),
        )

    def _blocked(
        self,
        reason_code: str,
        *,
        active_rollout_revision_id: UUID | None = None,
    ) -> AssistantReadinessSnapshot:
        if reason_code not in RUNTIME_READINESS_REASON_CODES:
            raise ValueError(f"unknown readiness reason: {reason_code}")
        return AssistantReadinessSnapshot(
            ready=False,
            reason_codes=(reason_code,),
            active_rollout_revision_id=active_rollout_revision_id,
            profile_version_id=None,
            model_id=None,
            compatible_worker_ids=(),
            build_revision=str(
                getattr(self.settings, "app_build_revision", "") or ""
            ),
        )

    def _compatible_workers(self, closure: AssistantRuntimeClosure) -> list[Any]:
        from app.assistant.durable.worker_registry import WorkerCompatibility

        # Canonical path: readiness and claim share WorkerCompatibility.
        # find_compatible_workers already orders by worker_id ASC.
        return WorkerRegistry(self.db).find_compatible_workers(
            WorkerCompatibility.from_closure(closure),
            limit=50,
        )


def project_public_readiness(snapshot: AssistantReadinessSnapshot) -> dict[str, Any]:
    """Public /ready projection: ready + stable reason codes only."""
    return {
        "ready": bool(snapshot.ready),
        "reasonCodes": list(snapshot.reason_codes),
    }


def project_authenticated_readiness(
    snapshot: AssistantReadinessSnapshot,
) -> dict[str, Any]:
    """Authenticated readiness projection may expose safe IDs + worker diagnostics."""
    return {
        "ready": bool(snapshot.ready),
        "reasonCodes": list(snapshot.reason_codes),
        "activeRolloutRevisionId": (
            str(snapshot.active_rollout_revision_id)
            if snapshot.active_rollout_revision_id is not None
            else None
        ),
        "profileVersionId": (
            str(snapshot.profile_version_id)
            if snapshot.profile_version_id is not None
            else None
        ),
        "modelId": str(snapshot.model_id) if snapshot.model_id is not None else None,
        "compatibleWorkerIds": list(snapshot.compatible_worker_ids),
        "buildRevision": snapshot.build_revision,
    }


__all__ = (
    "AssistantReadinessService",
    "Plan2AlembicHeadCompatibility",
    "RuntimeSchemaCompatibility",
    "project_authenticated_readiness",
    "project_public_readiness",
    "read_single_alembic_version",
)
