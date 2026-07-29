"""Prepared rollout, activation CAS, and durable new-runs kill-switch (Plan 2 Task 6).

Initialization bootstrap prepares only. Activation is a separate Operator+CSRF
mutation that revalidates under locks and uses control state_revision CAS plus
request-id idempotency.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.evaluation.models import (
    AssistantSkillPublishGate,
    AssistantSkillPublishGateUse,
)
from app.assistant.runtime.closure import (
    AssistantRuntimeClosureBuilder,
    RuntimeClosureDrift,
)
from app.assistant.runtime.contracts import (
    ActivatedRolloutResult,
    ActivateRolloutRequest,
    AssistantRuntimeClosure,
    AssistantRuntimeSubject,
    NewRolloutEvent,
    PreparedRolloutResult,
    PreparedRolloutRevision,
    PrepareRolloutRequest,
    RuntimeActivationRejected,
    RuntimeControlConflict,
    RuntimeControlResult,
    RuntimeGateEvidenceMissing,
    RolloutNotPrepared,
    SetNewRunsEnabledRequest,
    digest_activation_request,
    digest_new_runs_request,
    digest_prepare_request,
    rollout_revision_id_for_request,
)
from app.assistant.runtime.models import (
    AssistantMainAgentRolloutEvent,
    AssistantMainAgentRolloutRevision,
)
from app.assistant.runtime.readiness import AssistantReadinessService
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillPackage,
    AssistantSkillVersion,
)
from app.assistant.skills.schemas import MainAgentProfileSnapshotV2
from app.config import Settings, get_settings
from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.tokens import SessionMacKeyRing

# Re-export exceptions for callers/tests that import from activation.
__all__ = (
    "AssistantRuntimeActivationService",
    "RolloutNotPrepared",
    "RuntimeActivationRejected",
    "RuntimeGateEvidenceMissing",
)


class AssistantRuntimeActivationService:
    """Operator control-plane mutations for prepare / activate / new-runs."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | Any | None = None,
        repo: AssistantRuntimeRepository | None = None,
        closure_builder: AssistantRuntimeClosureBuilder | None = None,
        readiness: AssistantReadinessService | None = None,
        key_ring: SessionMacKeyRing | None = None,
    ) -> None:
        self.db = db
        self.settings = settings if settings is not None else get_settings()
        self.repo = repo or AssistantRuntimeRepository(db)
        self.closure_builder = closure_builder or AssistantRuntimeClosureBuilder(db)
        if readiness is not None:
            self.readiness = readiness
        else:
            readiness_kwargs: dict[str, Any] = {
                "settings": self.settings,
                "closure_builder": self.closure_builder,
            }
            if key_ring is not None:
                readiness_kwargs["key_ring"] = key_ring
            self.readiness = AssistantReadinessService(db, **readiness_kwargs)

    # ------------------------------------------------------------------
    # Prepare
    # ------------------------------------------------------------------

    def prepare(
        self,
        request: PrepareRolloutRequest,
        *,
        principal: OperatorPrincipal,
    ) -> PreparedRolloutResult:
        request_digest = digest_prepare_request(request)
        self.repo.lock_request_id(request.request_id)
        replay = self.repo.replay_or_conflict(
            request_id=request.request_id,
            request_digest=request_digest,
        )
        if replay is not None:
            return PreparedRolloutResult.model_validate(replay.result_json)

        profile_version = self._lock_published_profile_v2(request.profile_version_id)
        self._require_current_publish_gate_use(profile_version)
        build = str(getattr(self.settings, "app_build_revision", "") or "").strip()
        if not build:
            raise RuntimeActivationRejected("build_revision_missing")
        try:
            subject = self.closure_builder.build_subject(
                profile_version_id=profile_version.id,
                model_id=request.model_id,
                build_revision=build,
                lock=True,
            )
        except RuntimeClosureDrift as exc:
            raise RuntimeActivationRejected(exc.reason_code) from exc
        self._require_package_gate_uses(subject)
        revision_id = rollout_revision_id_for_request(request.request_id)
        revision = self.repo.create_prepared_revision(
            PreparedRolloutRevision.from_subject(
                subject=subject,
                revision_id=revision_id,
                prepared_by_operator_id=principal.operator_id,
                prepared_reason=request.reason,
            )
        )
        try:
            closure = self.closure_builder.build(
                rollout_revision_id=revision.id,
                lock=True,
            )
        except RuntimeClosureDrift as exc:
            raise RuntimeActivationRejected(exc.reason_code) from exc
        control = self.repo.get_or_create_control_for_update()
        result = PreparedRolloutResult.from_rows(revision, control)
        self.repo.append_control_event(
            NewRolloutEvent(
                action="prepared",
                from_rollout_revision_id=control.active_rollout_revision_id,
                to_rollout_revision_id=revision.id,
                control_revision=control.state_revision,
                request_id=request.request_id,
                request_digest=request_digest,
                operator_id=principal.operator_id,
                operator_session_id=principal.session_id,
                reason=request.reason,
                evidence_digest=closure.closure_digest,
                result_json=result.model_dump(mode="json", by_alias=True),
            )
        )
        self.db.commit()
        return result

    # ------------------------------------------------------------------
    # Activate
    # ------------------------------------------------------------------

    def activate(
        self,
        revision_id: UUID,
        request: ActivateRolloutRequest,
        *,
        principal: OperatorPrincipal,
    ) -> ActivatedRolloutResult:
        request_digest = digest_activation_request(revision_id, request)
        self.repo.lock_request_id(request.request_id)
        replay = self.repo.replay_or_conflict(
            request_id=request.request_id,
            request_digest=request_digest,
        )
        if replay is not None:
            return ActivatedRolloutResult.model_validate(replay.result_json)

        control = self.repo.get_or_create_control_for_update()
        if int(control.state_revision) != int(request.expected_control_revision):
            raise RuntimeControlConflict(
                f"expected control state_revision={request.expected_control_revision}"
            )
        target = self.repo.get_revision_for_update(revision_id)
        if target is None:
            raise RolloutNotPrepared()
        try:
            closure = self.closure_builder.build(
                rollout_revision_id=target.id,
                lock=True,
            )
        except RuntimeClosureDrift as exc:
            raise RuntimeActivationRejected(exc.reason_code) from exc
        self._require_current_gate_evidence(target, closure)
        snapshot = self.readiness.evaluate_activation_candidate_locked(
            control=control,
            candidate=closure,
        )
        if "worker_unavailable" in snapshot.reason_codes:
            raise RuntimeActivationRejected("worker_unavailable")
        if snapshot.reason_codes:
            raise RuntimeActivationRejected(snapshot.reason_codes[0])

        first_activation = control.active_rollout_revision_id is None
        effective_new_runs = (
            True if first_activation else bool(control.new_runs_enabled)
        )
        previous_id = control.active_rollout_revision_id
        updated = self.repo.compare_and_set_control(
            expected_state_revision=request.expected_control_revision,
            active_rollout_revision_id=target.id,
            new_runs_enabled=effective_new_runs,
        )
        result = ActivatedRolloutResult.from_rows(updated, target)
        self.repo.append_activation_events(
            previous_revision_id=previous_id,
            target_revision_id=target.id,
            request=request,
            request_digest=request_digest,
            principal=principal,
            result=result,
            evidence_digest=closure.closure_digest,
        )
        self.db.commit()
        return result

    # ------------------------------------------------------------------
    # Durable new-runs switch
    # ------------------------------------------------------------------

    def set_new_runs_enabled(
        self,
        request: SetNewRunsEnabledRequest,
        *,
        principal: OperatorPrincipal,
    ) -> RuntimeControlResult:
        request_digest = digest_new_runs_request(request)
        self.repo.lock_request_id(request.request_id)
        replay = self.repo.replay_or_conflict(
            request_id=request.request_id,
            request_digest=request_digest,
        )
        if replay is not None:
            return RuntimeControlResult.model_validate(replay.result_json)

        control = self.repo.get_or_create_control_for_update()
        if int(control.state_revision) != int(request.expected_control_revision):
            raise RuntimeControlConflict(
                f"expected control state_revision={request.expected_control_revision}"
            )
        # Capture pre-CAS values for the event (SQLAlchemy may expire the row).
        previous_active = control.active_rollout_revision_id
        previous_state = int(control.state_revision)
        previous_enabled = bool(control.new_runs_enabled)
        previous_view = type(
            "ControlSnapshot",
            (),
            {
                "active_rollout_revision_id": previous_active,
                "state_revision": previous_state,
                "new_runs_enabled": previous_enabled,
            },
        )()
        updated = self.repo.compare_and_set_control(
            expected_state_revision=request.expected_control_revision,
            active_rollout_revision_id=previous_active,
            new_runs_enabled=request.enabled,
        )
        result = RuntimeControlResult.from_row(updated)
        self.repo.append_control_event(
            NewRolloutEvent.for_new_runs_switch(
                previous=previous_view,
                updated=updated,
                request=request,
                request_digest=request_digest,
                principal=principal,
                result=result,
            )
        )
        self.db.commit()
        return result

    # ------------------------------------------------------------------
    # Gate / profile evidence
    # ------------------------------------------------------------------

    def _lock_published_profile_v2(
        self, profile_version_id: UUID
    ) -> AssistantMainAgentProfileVersion:
        stmt = (
            select(AssistantMainAgentProfileVersion)
            .where(AssistantMainAgentProfileVersion.id == profile_version_id)
            .with_for_update()
        )
        version = self.db.execute(stmt).scalar_one_or_none()
        if version is None:
            raise RuntimeActivationRejected("profile_unpublished")
        if str(version.version_source) != "publish":
            raise RuntimeActivationRejected("profile_unpublished")
        try:
            MainAgentProfileSnapshotV2.model_validate(version.snapshot or {})
        except Exception as exc:
            raise RuntimeActivationRejected("profile_unpublished") from exc

        profile = self.db.execute(
            select(AssistantMainAgentProfile)
            .where(AssistantMainAgentProfile.id == version.profile_id)
            .with_for_update()
        ).scalar_one_or_none()
        if profile is None or profile.published_version_id != version.id:
            raise RuntimeActivationRejected("profile_unpublished")
        return version

    def _require_current_publish_gate_use(
        self, profile_version: AssistantMainAgentProfileVersion
    ) -> None:
        """Normal prepare requires current profile publish gate-use.

        Bootstrap origin profile versions are accepted via seed/bootstrap
        evidence instead of publish-gate pins (Task 4/5 exception).
        """
        if self._is_bootstrap_profile_version(profile_version):
            return
        if not self._has_gate_use_for_version(
            resulting_version_id=profile_version.id,
            actions=("profile_publish", "profile_runtime_enable"),
        ):
            raise RuntimeGateEvidenceMissing("profile_gate_use_missing")

    def _require_package_gate_uses(self, subject: AssistantRuntimeSubject) -> None:
        """Require package publish/enable gate-use unless bootstrap/system package."""
        for entry in subject.package_closure:
            version_id_raw = entry.get("versionId") or entry.get("version_id")
            package_id_raw = entry.get("packageId") or entry.get("package_id")
            if version_id_raw is None:
                continue
            version_id = UUID(str(version_id_raw))
            package = None
            if package_id_raw is not None:
                package = self.db.get(AssistantSkillPackage, UUID(str(package_id_raw)))
            version = self.db.get(AssistantSkillVersion, version_id)
            if version is None:
                raise RuntimeGateEvidenceMissing("package_version_missing")
            if self._is_bootstrap_skill_version(version, package):
                continue
            if not self._has_gate_use_for_version(
                resulting_version_id=version_id,
                actions=("skill_publish", "skill_catalog_enable"),
            ):
                raise RuntimeGateEvidenceMissing("package_gate_use_missing")

    def _require_current_gate_evidence(
        self,
        target: AssistantMainAgentRolloutRevision,
        closure: AssistantRuntimeClosure,
    ) -> None:
        """Activation accepts bootstrap seed evidence OR current publish gate-use."""
        if self._has_bootstrap_prepared_evidence(target):
            # Seed digest must still match the recomputed closure.
            if str(target.seed_manifest_digest) != str(closure.seed_manifest_digest):
                raise RuntimeActivationRejected("system_seed_invalid")
            return
        profile_version = self.db.get(
            AssistantMainAgentProfileVersion, target.profile_version_id
        )
        if profile_version is None:
            raise RuntimeActivationRejected("profile_unpublished")
        if self._is_bootstrap_profile_version(profile_version):
            return
        if not self._has_gate_use_for_version(
            resulting_version_id=profile_version.id,
            actions=("profile_publish", "profile_runtime_enable"),
        ):
            raise RuntimeGateEvidenceMissing("profile_gate_use_missing")
        # Re-check package pins for non-bootstrap packages in the stored closure.
        for entry in list(target.package_closure_json or []):
            version_id_raw = entry.get("versionId") or entry.get("version_id")
            package_id_raw = entry.get("packageId") or entry.get("package_id")
            if version_id_raw is None:
                continue
            version_id = UUID(str(version_id_raw))
            package = None
            if package_id_raw is not None:
                package = self.db.get(AssistantSkillPackage, UUID(str(package_id_raw)))
            version = self.db.get(AssistantSkillVersion, version_id)
            if version is None:
                raise RuntimeGateEvidenceMissing("package_version_missing")
            if self._is_bootstrap_skill_version(version, package):
                continue
            if not self._has_gate_use_for_version(
                resulting_version_id=version_id,
                actions=("skill_publish", "skill_catalog_enable"),
            ):
                raise RuntimeGateEvidenceMissing("package_gate_use_missing")

    def _has_bootstrap_prepared_evidence(
        self, target: AssistantMainAgentRolloutRevision
    ) -> bool:
        if str(target.prepared_reason or "") == "system_bootstrap":
            return True
        events = (
            self.db.query(AssistantMainAgentRolloutEvent)
            .filter(
                AssistantMainAgentRolloutEvent.to_rollout_revision_id == target.id,
                AssistantMainAgentRolloutEvent.action == "prepared",
            )
            .all()
        )
        for event in events:
            if str(event.reason or "") == "system_bootstrap":
                return True
            result = event.result_json or {}
            evidence = result.get("bootstrapEvidence") or result.get("bootstrap_evidence")
            if isinstance(evidence, dict) and evidence.get("origin") == "system_bootstrap":
                return True
        return False

    @staticmethod
    def _is_bootstrap_profile_version(
        version: AssistantMainAgentProfileVersion,
    ) -> bool:
        if str(version.origin or "") == "bootstrap":
            return True
        source_ref = version.source_ref or {}
        if isinstance(source_ref, dict) and source_ref.get("origin") == "system_bootstrap":
            return True
        return False

    @staticmethod
    def _is_bootstrap_skill_version(
        version: AssistantSkillVersion,
        package: AssistantSkillPackage | None,
    ) -> bool:
        if package is not None and bool(getattr(package, "is_system", False)):
            return True
        ext = version.extension_manifest or {}
        if isinstance(ext, dict) and ext.get("systemBootstrap") is True:
            return True
        return False

    def _has_gate_use_for_version(
        self,
        *,
        resulting_version_id: UUID,
        actions: tuple[str, ...],
    ) -> bool:
        row = (
            self.db.query(AssistantSkillPublishGateUse)
            .filter(
                AssistantSkillPublishGateUse.resulting_version_id == resulting_version_id,
                AssistantSkillPublishGateUse.action.in_(list(actions)),
            )
            .first()
        )
        if row is None:
            return False
        # Optional: ensure the linked gate still exists (pin is existence of use).
        gate = self.db.get(AssistantSkillPublishGate, row.gate_id)
        return gate is not None
