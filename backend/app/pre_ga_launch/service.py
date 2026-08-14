"""Server-derived candidate/consumption/evaluation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.audit import OperatorAuditRepository
from app.operator_auth.contracts import RequestSecurityContext
from app.pre_ga_launch.contracts import (
    ConsumePreGaLaunchCandidateRequest,
    CreatePreGaLaunchCandidateRequest,
    LaunchOperationalSnapshotV1,
    PreGaLaunchSubjectV1,
)
from app.pre_ga_launch.models import PreGaLaunchCandidate, PreGaLaunchControl, PreGaLaunchGateUse
from app.pre_ga_launch.repository import LaunchRepository, request_digest
from app.pre_ga_launch.subject import (
    LaunchSubjectError,
    build_launch_subject,
    build_launch_subject_snapshot,
)
from app.release.contracts import (
    ContentAddressedEvidenceRef,
    ReleaseEvidenceManifestV1,
    ReleaseQualificationTargetV1,
)
from app.release.evidence import (
    ContentAddressedEvidenceStore,
    ReleaseEvidenceIntegrityError,
    verify_evidence_object,
)
from app.release.trust import (
    ReleaseEvidenceTrustError,
    ReleaseTrustSetV1,
    attestation_object_digest,
)
from app.assistant.domain.digests import sha256_canonical_json


class PreGaLaunchError(RuntimeError):
    def __init__(self, safe_code: str, *, status_code: int = 422) -> None:
        self.safe_code = safe_code
        self.status_code = status_code
        super().__init__(safe_code)


@dataclass(frozen=True)
class PreGaLaunchAuthorization:
    launched: bool
    reason_code: str | None
    control_revision: int
    active_subject_digest: str | None


@dataclass(frozen=True)
class PreGaLaunchCandidateResult:
    candidate: PreGaLaunchCandidate
    replayed: bool = False


@dataclass(frozen=True)
class LaunchConsumptionResult:
    control: PreGaLaunchControl
    gate_use: PreGaLaunchGateUse
    replayed: bool = False


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _manifest_payload(
    store: ContentAddressedEvidenceStore,
    ref: ContentAddressedEvidenceRef,
    trust: ReleaseTrustSetV1,
) -> tuple[ReleaseEvidenceManifestV1, dict[str, Any]]:
    try:
        raw = store.read_evidence_object(ref.evidence_kind, ref.manifest_digest)
        payload = json.loads(raw.decode("utf-8"))
        manifest, attestation = verify_evidence_object(payload, trust)
        if manifest.evidence_kind != ref.evidence_kind:
            raise ReleaseEvidenceIntegrityError("evidence_kind_mismatch")
        if attestation_object_digest(attestation) != ref.attestation_digest:
            raise ReleaseEvidenceIntegrityError("attestation_digest_mismatch")
        for artifact in manifest.artifact_refs:
            if len(store.read_artifact(artifact.sha256_digest)) != artifact.byte_size:
                raise ReleaseEvidenceIntegrityError("artifact_size_mismatch")
    except (OSError, ValueError, ReleaseEvidenceIntegrityError, ReleaseEvidenceTrustError):
        raise PreGaLaunchError("launch_evidence_invalid") from None
    if manifest.manifest_digest != ref.manifest_digest:
        raise PreGaLaunchError("launch_evidence_invalid")
    if isinstance(attestation, dict):
        key_id = attestation.get("keyId")
    else:
        key_id = getattr(attestation, "key_id", None)
    if not isinstance(key_id, str):
        raise PreGaLaunchError("launch_evidence_invalid")
    return manifest, payload


_TARGET_MANIFEST_FIELDS: tuple[str, ...] = (
    "qualification_target_digest",
    "build_revision",
    "image_set_digest",
    "deployed_artifact_set_digest",
    "schema_family",
    "schema_revision",
    "schema_application_fingerprint",
    "schema_control_fingerprint",
    "schema_identity_contract_version",
    "schema_contract_material_digest",
    "schema_seed_contract_digest",
    "schema_runtime_contract_version",
    "schema_checkpoint_codec_version",
    "schema_capability_feature_digest",
    "operator_auth_contract_version",
    "rollout_revision_id",
    "rollout_revision_digest",
    "runtime_closure_digest",
    "profile_version_id",
    "profile_content_digest",
    "model_id",
    "model_identity_digest",
    "package_closure_digest",
    "capability_closure_digest",
    "seed_manifest_digest",
    "worker_runtime_contract_version",
    "worker_checkpoint_codec_version",
    "worker_capability_feature_digest",
    "create_entry_contract_digest",
    "write_policy_digest",
    "write_cohort_digest",
    "reconciliation_contract_version",
    "dependency_lock_set_digest",
    "scenario_set_digest",
    "required_assertion_set_digest",
    "runner_contract_version",
    "runner_identity_digest",
    "evidence_trust_set_digest",
)


def _semantic_failure_codes(
    target: ReleaseQualificationTargetV1,
    automated: ReleaseEvidenceManifestV1,
    rehearsal: ReleaseEvidenceManifestV1,
) -> list[str]:
    failures: list[str] = []
    for field in _TARGET_MANIFEST_FIELDS:
        expected = getattr(target, field)
        for label, manifest in (("automated", automated), ("rehearsal", rehearsal)):
            if str(getattr(manifest, field)) != str(expected):
                failures.append(f"launch_evidence_{label}_{field}_mismatch")
    if automated.evidence_kind != "automated_qualification":
        failures.append("launch_evidence_automated_kind_mismatch")
    if rehearsal.evidence_kind != "production_rehearsal":
        failures.append("launch_evidence_rehearsal_kind_mismatch")
    if automated.schema_deployment_class != "rehearsal":
        failures.append("launch_evidence_automated_deployment_class_mismatch")
    if rehearsal.schema_deployment_class != "rehearsal":
        failures.append("launch_evidence_rehearsal_deployment_class_mismatch")
    if automated.schema_runtime_identity_digest != rehearsal.schema_runtime_identity_digest:
        failures.append("launch_evidence_schema_runtime_identity_mismatch")
    if automated.qualification_infrastructure_identity != rehearsal.qualification_infrastructure_identity:
        failures.append("qualification_infrastructure_mismatch")
    return list(dict.fromkeys(failures))


class PreGaLaunchService:
    def __init__(
        self,
        db: Session,
        *,
        evidence_store: ContentAddressedEvidenceStore | None = None,
        trust_set: ReleaseTrustSetV1 | None = None,
        target_provider: Callable[[], Any] | None = None,
        subject_provider: Callable[[PreGaLaunchCandidate], PreGaLaunchSubjectV1] | None = None,
        audit_repository: OperatorAuditRepository | None = None,
        audit_context: RequestSecurityContext | None = None,
    ) -> None:
        self.db = db
        self.repo = LaunchRepository(db)
        self.evidence_store = evidence_store
        self.trust_set = trust_set
        self.target_provider = target_provider
        self.subject_provider = subject_provider
        self.audit_repository = audit_repository
        self.audit_context = audit_context

    def _require_dependencies(self) -> None:
        if self.evidence_store is None or self.trust_set is None or self.target_provider is None:
            raise PreGaLaunchError("launch_evidence_invalid")

    def _snapshot(self, *, observed_at) -> LaunchOperationalSnapshotV1:
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.models import AssistantChatRun

        unknown = self.db.query(AssistantCapabilityCall).filter(AssistantCapabilityCall.status == "unknown").count()
        reconciliation = self.db.query(AssistantCapabilityCall).filter(AssistantCapabilityCall.status == "needs_reconciliation").count()
        active = self.db.query(AssistantChatRun).filter(AssistantChatRun.status.in_(("queued", "running", "recovering", "waiting_approval", "waiting_input", "cancelling"))).count()
        return LaunchOperationalSnapshotV1.build(
            unknown_capability_call_count=unknown,
            needs_reconciliation_count=reconciliation,
            active_run_count=active,
            observed_at=observed_at,
        )

    def _current_evidence_subject(
        self,
        request: CreatePreGaLaunchCandidateRequest,
    ) -> tuple[ReleaseQualificationTargetV1, tuple[ReleaseEvidenceManifestV1, ReleaseEvidenceManifestV1], PreGaLaunchSubjectV1, list[str]]:
        self._require_dependencies()
        automated, _ = _manifest_payload(self.evidence_store, request.automated_evidence_ref, self.trust_set)
        rehearsal, _ = _manifest_payload(self.evidence_store, request.rehearsal_evidence_ref, self.trust_set)
        try:
            target = self.target_provider()
            if not isinstance(target, ReleaseQualificationTargetV1):
                target = ReleaseQualificationTargetV1.model_validate(target)
        except LaunchSubjectError as exc:
            raise PreGaLaunchError(exc.safe_code) from None
        except Exception:
            raise PreGaLaunchError("launch_target_unavailable") from None
        failures = _semantic_failure_codes(target, automated, rehearsal)
        try:
            subject = build_launch_subject(target, automated, rehearsal)
        except LaunchSubjectError as exc:
            failures.append(exc.safe_code)
            subject = build_launch_subject_snapshot(target, automated, rehearsal)
        failures = list(dict.fromkeys(failures))
        return target, (automated, rehearsal), subject, failures

    def _candidate_refs(self, candidate: PreGaLaunchCandidate) -> tuple[ContentAddressedEvidenceRef, ContentAddressedEvidenceRef]:
        try:
            return (
                ContentAddressedEvidenceRef.model_validate(candidate.automated_evidence_ref_json),
                ContentAddressedEvidenceRef.model_validate(candidate.rehearsal_evidence_ref_json),
            )
        except ValueError:
            raise PreGaLaunchError("launch_evidence_invalid") from None

    def _candidate_subject(self, candidate: PreGaLaunchCandidate) -> PreGaLaunchSubjectV1:
        self._require_dependencies()
        automated_ref, rehearsal_ref = self._candidate_refs(candidate)
        automated, _ = _manifest_payload(self.evidence_store, automated_ref, self.trust_set)
        rehearsal, _ = _manifest_payload(self.evidence_store, rehearsal_ref, self.trust_set)
        if automated.manifest_digest != candidate.automated_evidence_manifest_digest or rehearsal.manifest_digest != candidate.rehearsal_evidence_manifest_digest:
            raise PreGaLaunchError("launch_evidence_invalid")
        try:
            target = self.target_provider()
            if not isinstance(target, ReleaseQualificationTargetV1):
                target = ReleaseQualificationTargetV1.model_validate(target)
            subject = build_launch_subject(target, automated, rehearsal)
        except PreGaLaunchError:
            raise
        except LaunchSubjectError:
            raise PreGaLaunchError("launch_subject_stale") from None
        except Exception:
            raise PreGaLaunchError("launch_subject_stale") from None
        return subject

    def _subject_for_candidate(self, candidate: PreGaLaunchCandidate) -> PreGaLaunchSubjectV1:
        # The injected port is useful for deterministic unit tests, but durable
        # evidence is always verified first so it cannot bypass the trust gate.
        verified = self._candidate_subject(candidate)
        if self.subject_provider is None:
            return verified
        try:
            provided = self.subject_provider(candidate)
        except Exception:
            raise PreGaLaunchError("launch_subject_stale") from None
        if not isinstance(provided, PreGaLaunchSubjectV1):
            raise PreGaLaunchError("launch_subject_stale")
        return provided

    def _append_audit(self, *, principal: OperatorPrincipal, request_id: UUID, request_digest_value: str, outcome: str, reason_code: str | None = None, metadata: dict[str, str | int | bool | None] | None = None) -> None:
        if self.audit_repository is None:
            return
        context = self.audit_context or RequestSecurityContext(
            request_id=str(request_id),
            request_digest=request_digest_value,
            user_agent_digest=sha256_canonical_json({"domain": "mindatlas:pre-ga-launch-audit-user-agent:v1"}),
            network_digest=sha256_canonical_json({"domain": "mindatlas:pre-ga-launch-audit-network:v1"}),
        )
        self.audit_repository.append(
            event_type="control_plane_mutation_committed",
            outcome=outcome,  # type: ignore[arg-type]
            context=context,
            operator_id=principal.operator_id,
            session_id=principal.session_id,
            reason_code=reason_code,
            metadata=metadata or {},
        )

    def create_candidate(
        self,
        request: CreatePreGaLaunchCandidateRequest,
        *,
        principal: OperatorPrincipal,
    ) -> PreGaLaunchCandidateResult:
        self.repo.lock_launch()
        digest = request_digest(
            action="create_candidate",
            operator_id=principal.operator_id,
            request_fields={
                "automatedEvidenceRef": request.automated_evidence_ref.model_dump(mode="json", by_alias=True),
                "rehearsalEvidenceRef": request.rehearsal_evidence_ref.model_dump(mode="json", by_alias=True),
                "reason": request.reason,
            },
        )
        existing = self.repo.find_candidate_by_request_id(request.request_id, for_update=True)
        if existing is not None:
            if existing.creation_request_digest != digest:
                raise PreGaLaunchError("launch_request_reuse_conflict", status_code=409)
            return PreGaLaunchCandidateResult(existing, replayed=True)
        target, manifests, subject, failures = self._current_evidence_subject(request)
        automated, rehearsal = manifests
        observed_at = self.repo.database_now()
        snapshot = self._snapshot(observed_at=observed_at)
        if not all(item.passed for item in (*automated.assertion_results, *rehearsal.assertion_results)):
            failures.append("qualification_assertion_failed")
        if snapshot.unknown_capability_call_count:
            failures.append("unknown_capability_calls_present")
        if snapshot.needs_reconciliation_count:
            failures.append("reconciliation_required")
        if snapshot.active_run_count:
            failures.append("active_runs_present")
        failures = list(dict.fromkeys(failures))
        target_payload = target.model_dump(mode="json", by_alias=True)
        subject_payload = subject.model_dump(mode="json", by_alias=True)
        candidate = PreGaLaunchCandidate(
            candidate_kind="pre_ga_launch",
            creation_request_id=request.request_id,
            creation_request_digest=digest,
            created_by_operator_id=principal.operator_id,
            created_by_session_id=principal.session_id,
            reason=request.reason,
            qualification_target_json=target_payload,
            qualification_target_digest=target.qualification_target_digest,
            subject_json=subject_payload,
            subject_digest=subject.subject_digest,
            build_revision=target.build_revision,
            image_set_digest=target.image_set_digest,
            deployed_artifact_set_digest=target.deployed_artifact_set_digest,
            schema_family=target.schema_family,
            schema_revision=target.schema_revision,
            schema_runtime_identity_digest=target.production_schema_runtime_identity_digest,
            deployment_class=target.production_schema_deployment_class,
            operator_auth_contract_version=target.operator_auth_contract_version,
            rollout_revision_id=target.rollout_revision_id,
            rollout_revision_digest=target.rollout_revision_digest,
            runtime_closure_digest=target.runtime_closure_digest,
            profile_version_id=target.profile_version_id,
            profile_content_digest=target.profile_content_digest,
            model_id=target.model_id,
            model_identity_digest=target.model_identity_digest,
            package_closure_digest=target.package_closure_digest,
            capability_closure_digest=target.capability_closure_digest,
            seed_manifest_digest=target.seed_manifest_digest,
            worker_runtime_contract_version=target.worker_runtime_contract_version,
            worker_checkpoint_codec_version=target.worker_checkpoint_codec_version,
            worker_capability_feature_digest=target.worker_capability_feature_digest,
            create_entry_contract_digest=target.create_entry_contract_digest,
            write_policy_digest=target.write_policy_digest,
            write_cohort_digest=target.write_cohort_digest,
            reconciliation_contract_version=target.reconciliation_contract_version,
            dependency_lock_set_digest=target.dependency_lock_set_digest,
            scenario_set_digest=target.scenario_set_digest,
            required_assertion_set_digest=target.required_assertion_set_digest,
            runner_contract_version=target.runner_contract_version,
            runner_identity_digest=target.runner_identity_digest,
            evidence_trust_set_digest=target.evidence_trust_set_digest,
            automated_evidence_ref_json=request.automated_evidence_ref.model_dump(mode="json", by_alias=True),
            automated_evidence_manifest_digest=automated.manifest_digest,
            automated_attestation_digest=request.automated_evidence_ref.attestation_digest,
            rehearsal_evidence_ref_json=request.rehearsal_evidence_ref.model_dump(mode="json", by_alias=True),
            rehearsal_evidence_manifest_digest=rehearsal.manifest_digest,
            rehearsal_attestation_digest=request.rehearsal_evidence_ref.attestation_digest,
            operational_snapshot_json=snapshot.model_dump(mode="json", by_alias=True),
            operational_snapshot_digest=snapshot.snapshot_digest,
            unknown_call_count=snapshot.unknown_capability_call_count,
            needs_reconciliation_count=snapshot.needs_reconciliation_count,
            active_run_count=snapshot.active_run_count,
            passed=not failures,
            safe_failure_codes=failures,
            observed_at=observed_at,
            issued_at=observed_at,
            expires_at=observed_at + timedelta(hours=24),
        )
        self.repo.insert_candidate(candidate)
        self._append_audit(
            principal=principal,
            request_id=request.request_id,
            request_digest_value=digest,
            outcome="succeeded",
            reason_code=failures[0] if failures else None,
            metadata={
                "action": "pre_ga_launch_candidate_created",
                "candidatePassed": bool(candidate.passed),
            },
        )
        self.db.commit()
        return PreGaLaunchCandidateResult(candidate)

    def consume_candidate(self, candidate_id: UUID, request: ConsumePreGaLaunchCandidateRequest, *, principal: OperatorPrincipal) -> LaunchConsumptionResult:
        self.repo.lock_launch()
        digest = request_digest(action="consume_candidate", operator_id=principal.operator_id, request_fields={"candidateId": str(candidate_id), "expectedControlRevision": request.expected_control_revision, "reason": request.reason})
        replay = self.repo.find_gate_use_by_request_id(request.request_id, for_update=True)
        if replay is not None:
            if replay.consumption_request_digest != digest:
                raise PreGaLaunchError("launch_request_reuse_conflict", status_code=409)
            control = self.repo.lock_control()
            return LaunchConsumptionResult(control, replay, replayed=True)
        candidate = self.db.get(PreGaLaunchCandidate, candidate_id)
        if candidate is None:
            raise PreGaLaunchError("launch_candidate_missing")
        if not candidate.passed:
            raise PreGaLaunchError("launch_candidate_not_passing")
        now = self.repo.database_now()
        if now >= _utc_datetime(candidate.expires_at):
            raise PreGaLaunchError("launch_candidate_expired")
        control = self.repo.lock_control()
        if control.revision != request.expected_control_revision:
            raise PreGaLaunchError("launch_control_conflict", status_code=409)
        try:
            current = self._subject_for_candidate(candidate)
        except PreGaLaunchError:
            raise
        if current.subject_digest != candidate.subject_digest:
            raise PreGaLaunchError("launch_subject_stale")
        snapshot = self._snapshot(observed_at=now)
        if snapshot.unknown_capability_call_count or snapshot.needs_reconciliation_count or snapshot.active_run_count:
            raise PreGaLaunchError("launch_operational_state_changed")
        use = PreGaLaunchGateUse(
            id=uuid5(NAMESPACE_URL, f"mindatlas:pre-ga-launch-use:{request.request_id}"),
            candidate_id=candidate.id,
            subject_digest=candidate.subject_digest,
            operator_id=principal.operator_id,
            session_id=principal.session_id,
            consumption_request_id=request.request_id,
            consumption_request_digest=digest,
            reason=request.reason,
            expected_control_revision=control.revision,
            resulting_control_revision=control.revision + 1,
            used_at=now,
        )
        try:
            control = self.repo.append_use_and_advance_control(
                use=use,
                expected_revision=control.revision,
                subject_digest=candidate.subject_digest,
                candidate_id=candidate.id,
                used_at=now,
            )
        except RuntimeError as exc:
            if str(exc) == "launch_control_conflict":
                raise PreGaLaunchError("launch_control_conflict", status_code=409) from None
            raise
        self._append_audit(
            principal=principal,
            request_id=request.request_id,
            request_digest_value=digest,
            outcome="succeeded",
            metadata={
                "action": "pre_ga_launch_candidate_consumed",
                "controlRevision": int(control.revision),
            },
        )
        self.db.commit()
        return LaunchConsumptionResult(control, use)

    def evaluate_current_launch(self) -> PreGaLaunchAuthorization:
        self.repo.lock_launch()
        control = self.repo.lock_control()
        if control.revision == 0 or control.active_candidate_id is None or control.active_subject_digest is None:
            return PreGaLaunchAuthorization(False, "launch_control_missing", control.revision, None)
        candidate = self.db.get(PreGaLaunchCandidate, control.active_candidate_id)
        if candidate is None or candidate.subject_digest != control.active_subject_digest:
            return PreGaLaunchAuthorization(False, "launch_subject_stale", control.revision, control.active_subject_digest)
        use = self.db.get(PreGaLaunchGateUse, control.active_gate_use_id)
        if (
            use is None
            or use.candidate_id != candidate.id
            or use.subject_digest != candidate.subject_digest
            or use.resulting_control_revision != control.revision
        ):
            return PreGaLaunchAuthorization(False, "launch_subject_stale", control.revision, control.active_subject_digest)
        try:
            current = self._subject_for_candidate(candidate)
        except PreGaLaunchError as exc:
            if exc.safe_code == "launch_evidence_invalid":
                return PreGaLaunchAuthorization(False, "launch_evidence_unavailable", control.revision, control.active_subject_digest)
            return PreGaLaunchAuthorization(False, "launch_subject_unavailable", control.revision, control.active_subject_digest)
        if current.subject_digest != candidate.subject_digest:
            return PreGaLaunchAuthorization(False, "launch_subject_stale", control.revision, control.active_subject_digest)
        return PreGaLaunchAuthorization(True, None, control.revision, control.active_subject_digest)


__all__ = [
    "LaunchConsumptionResult",
    "PreGaLaunchAuthorization",
    "PreGaLaunchCandidateResult",
    "PreGaLaunchError",
    "PreGaLaunchService",
]
