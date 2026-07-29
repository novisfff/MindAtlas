"""Trusted Assistant system bootstrap inside the initialization transaction.

Stages the build-owned Skill + Profile V2 + prepared Main-Agent rollout without
committing and without activating. The InitializationCoordinator is the sole
commit owner; this module only flushes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
from app.assistant.durable.worker_registry import (
    RUNTIME_CONTRACT_VERSION,
    default_capability_feature_digest,
)
from app.assistant.runtime.contracts import (
    ASSISTANT_ROLLOUT_NAMESPACE,
    AssistantRuntimeSubject,
    NewRolloutEvent,
    PreparedRolloutRevision,
    require_sha256,
)
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.assistant.runtime.seed import (
    VerifiedAssistantSystemSeed,
    load_verified_assistant_system_seed,
    system_tool_seed_contract_digest,
)
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillCapabilityBinding,
    AssistantSkillCapabilityDependency,
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
    AssistantSkillResourceBlob,
    AssistantSkillVersion,
    AssistantSkillVersionResource,
)
from app.assistant.skills.resolution import (
    CapabilityReferenceResolver,
    binding_set_digest_from_bindings,
    compute_system_tool_contract_set_digest,
    version_digest_from_parts,
)
from app.assistant.skills.schemas import (
    DEFAULT_MAIN_AGENT_DISPLAY_NAME,
    DEFAULT_MAIN_AGENT_PROFILE_KEY,
    MainAgentProfileSnapshotV2,
)
from app.assistant.skills.contracts import (
    ParsedSkillPackage,
    is_reserved_skill_lookup_name,
    normalize_skill_lookup_name,
    validate_canonical_skill_name,
)
from app.common.time import utcnow
from app.operator_auth.models import OperatorAccount
from app.system_settings.initialization_service import SystemInitializationService


class AssistantBootstrapRejected(RuntimeError):
    """Fresh-state or bootstrap staging rejection with a stable reason code."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = str(reason_code)
        super().__init__(message or self.reason_code)


@dataclass(frozen=True)
class AssistantBootstrapFreshPermit:
    """In-process, non-serializable fresh-state permit for one locked init tx.

    Single-use: ``stage_bootstrap`` consumes the permit. Not serializable and
    not forgeable from request input — only returned by
    ``lock_and_verify_fresh_preconditions``.
    """

    seed: VerifiedAssistantSystemSeed
    _state: dict[str, bool] = field(
        default_factory=lambda: {"consumed": False},
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class StageAssistantBootstrapRequest:
    operator_id: UUID
    operator_session_id: UUID | None
    model_id: UUID
    build_revision: str
    fresh_permit: AssistantBootstrapFreshPermit


@dataclass(frozen=True)
class PreparedAssistantBootstrap:
    skill_package_id: UUID
    skill_version_id: UUID
    profile_id: UUID
    profile_version_id: UUID
    rollout_revision_id: UUID
    rollout_revision_digest: str
    rollout_control_revision: int
    seed_manifest_digest: str


@dataclass(frozen=True)
class _BootstrapClosureView:
    """Minimal closure view for bootstrap evidence (Task 5 owns the full builder)."""

    rollout_revision_id: UUID
    rollout_revision_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    closure_digest: str


def _credential_config_digest(*, base_url: str, runtime_revision: int) -> str:
    parts = urlsplit((base_url or "").strip())
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "scheme": parts.scheme or None,
            "host": parts.hostname,
            "port": parts.port,
            "path": parts.path or None,
            "runtimeRevision": int(runtime_revision or 1),
        }
    )


def _frontmatter_json(parsed: ParsedSkillPackage) -> dict[str, Any]:
    return parsed.frontmatter.model_dump(by_alias=True, exclude_none=False)


def _manifest_json(parsed: ParsedSkillPackage) -> dict[str, Any] | None:
    if parsed.manifest is None:
        return None
    return parsed.manifest.model_dump(by_alias=True, exclude_none=False)


def _resource_index_json(parsed: ParsedSkillPackage) -> list[dict[str, Any]]:
    return [
        {
            "path": entry.path,
            "kind": entry.resource_kind,
            "mediaType": entry.media_type,
            "size": entry.byte_size,
            "sha256": entry.sha256,
        }
        for entry in parsed.resource_index
    ]


def _display_name_for(parsed: ParsedSkillPackage) -> str:
    if parsed.manifest and parsed.manifest.display_name:
        return parsed.manifest.display_name
    return parsed.canonical_name


class AssistantSystemBootstrapper:
    """Stage trusted system Skill/Profile/prepared rollout (flush only)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runtime_repo = AssistantRuntimeRepository(db)

    # ------------------------------------------------------------------
    # Fresh preconditions
    # ------------------------------------------------------------------

    def lock_and_verify_fresh_preconditions(self) -> AssistantBootstrapFreshPermit:
        """Verify uninitialized + no operator/published profile/active rollout.

        Must run under the initialization lock, before the Operator is staged.
        Loads the verified build-owned seed only after fresh checks pass.
        """
        system_service = SystemInitializationService(self.db)
        if system_service.is_initialized():
            raise AssistantBootstrapRejected("system_already_initialized")

        operator = (
            self.db.query(OperatorAccount)
            .filter(OperatorAccount.singleton_key == "operator")
            .one_or_none()
        )
        if operator is not None:
            raise AssistantBootstrapRejected("operator_already_exists")

        published_profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.published_version_id.isnot(None))
            .first()
        )
        if published_profile is not None:
            raise AssistantBootstrapRejected("profile_already_published")

        control = self.runtime_repo.get_control()
        if control is not None and control.active_rollout_revision_id is not None:
            raise AssistantBootstrapRejected("rollout_already_active")

        seed = load_verified_assistant_system_seed()
        return AssistantBootstrapFreshPermit(seed=seed)

    def _require_locked_fresh_permit(
        self, permit: AssistantBootstrapFreshPermit
    ) -> None:
        if not isinstance(permit, AssistantBootstrapFreshPermit):
            raise AssistantBootstrapRejected("fresh_permit_invalid")
        if not isinstance(getattr(permit, "seed", None), VerifiedAssistantSystemSeed):
            raise AssistantBootstrapRejected("fresh_permit_invalid")
        state = getattr(permit, "_state", None)
        if not isinstance(state, dict) or state.get("consumed") is not False:
            raise AssistantBootstrapRejected("fresh_permit_invalid")

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    def stage_bootstrap(
        self, request: StageAssistantBootstrapRequest
    ) -> PreparedAssistantBootstrap:
        """Stage system Skill, Profile, prepared rollout + evidence (no commit)."""
        self._require_locked_fresh_permit(request.fresh_permit)
        seed = request.fresh_permit.seed

        operator = self.db.get(OperatorAccount, request.operator_id)
        if operator is None or operator.singleton_key != "operator":
            raise AssistantBootstrapRejected("operator_mismatch")

        self._assert_build_compatible(seed, request.build_revision)
        bindings = self._resolve_exact_system_bindings(seed)
        package, version = self._stage_system_skill(seed, bindings)
        profile, profile_version = self._stage_system_profile(
            seed.profile, package_id=package.id
        )
        model_identity_digest = self._resolve_model_identity_digest(
            model_id=request.model_id,
            build_revision=request.build_revision,
        )
        subject = self._build_subject(
            profile_version=profile_version,
            model_id=request.model_id,
            model_identity_digest=model_identity_digest,
            package=package,
            version=version,
            seed=seed,
            build_revision=request.build_revision,
        )
        bootstrap_request_id = uuid5(
            ASSISTANT_ROLLOUT_NAMESPACE,
            (
                f"system-bootstrap:{seed.manifest.manifest_digest}:"
                f"{request.operator_id}"
            ),
        )
        revision_id = uuid5(
            ASSISTANT_ROLLOUT_NAMESPACE,
            f"revision:{bootstrap_request_id}",
        )
        rollout = self.runtime_repo.create_prepared_revision(
            PreparedRolloutRevision.from_subject(
                subject=subject,
                revision_id=revision_id,
                prepared_by_operator_id=request.operator_id,
                prepared_reason="system_bootstrap",
            )
        )
        closure = self._build_closure_view(subject=subject, rollout=rollout)
        control = self.runtime_repo.get_or_create_control_for_update()
        if control.active_rollout_revision_id is not None:
            raise AssistantBootstrapRejected("rollout_already_active")
        safe_evidence = self._safe_bootstrap_evidence(
            closure=closure,
            seed=seed,
            profile_version=profile_version,
            skill_version=version,
            request=request,
        )
        # Events are append-only — evidence must be present at insert time.
        self.runtime_repo.append_control_event(
            NewRolloutEvent.prepared_from_bootstrap(
                rollout=rollout,
                control_revision=control.state_revision,
                request_id=bootstrap_request_id,
                seed_manifest_digest=seed.manifest.manifest_digest,
                operator_id=request.operator_id,
                bootstrap_evidence=safe_evidence,
            )
        )
        # Permit is single-use.
        request.fresh_permit._state["consumed"] = True
        return PreparedAssistantBootstrap(
            skill_package_id=package.id,
            skill_version_id=version.id,
            profile_id=profile.id,
            profile_version_id=profile_version.id,
            rollout_revision_id=rollout.id,
            rollout_revision_digest=str(rollout.revision_digest),
            rollout_control_revision=int(control.state_revision),
            seed_manifest_digest=seed.manifest.manifest_digest,
        )

    def _assert_build_compatible(
        self, seed: VerifiedAssistantSystemSeed, build_revision: str
    ) -> None:
        revision = (build_revision or "").strip()
        if not revision:
            raise AssistantBootstrapRejected("build_revision_missing")
        build = seed.manifest.build_compatibility
        if build.runtime_contract_version != RUNTIME_CONTRACT_VERSION:
            raise AssistantBootstrapRejected("runtime_contract_version_mismatch")
        if build.checkpoint_codec_version != CURRENT_CHECKPOINT_CODEC_VERSION:
            raise AssistantBootstrapRejected("checkpoint_codec_version_mismatch")
        if build.capability_feature_digest != default_capability_feature_digest():
            raise AssistantBootstrapRejected("capability_feature_digest_mismatch")

    def _resolve_exact_system_bindings(self, seed: VerifiedAssistantSystemSeed):
        if seed.parsed_skill.manifest is None:
            raise AssistantBootstrapRejected("skill_manifest_missing")
        declarations = tuple(seed.parsed_skill.manifest.capabilities)
        resolved = CapabilityReferenceResolver(self.db).resolve_many(declarations)
        expected_by_key = {
            item.key: item.target_contract_digest for item in seed.capability_bindings
        }
        live_set_digest = compute_system_tool_contract_set_digest()
        for binding in resolved:
            snapshot = binding.resolution_snapshot or {}
            set_digest = snapshot.get("systemToolContractSetDigest")
            if not isinstance(set_digest, str) or not set_digest:
                # System-tool bindings store the set digest as config_digest.
                set_digest = binding.config_digest or live_set_digest
            seed_contract_digest = system_tool_seed_contract_digest(
                key=binding.capability_key,
                target_identity=binding.target_identity,
                input_schema_digest=binding.input_schema_digest,
                output_schema_digest=binding.output_schema_digest,
                system_tool_contract_set_digest=str(set_digest),
            )
            if expected_by_key.get(binding.capability_key) != seed_contract_digest:
                raise AssistantBootstrapRejected("system_binding_digest_mismatch")
        return resolved

    def _stage_system_skill(self, seed: VerifiedAssistantSystemSeed, bindings):
        parsed = seed.parsed_skill
        try:
            canonical = validate_canonical_skill_name(parsed.canonical_name)
        except ValueError as exc:
            raise AssistantBootstrapRejected("skill_name_invalid") from exc
        if is_reserved_skill_lookup_name(canonical):
            raise AssistantBootstrapRejected("skill_name_reserved")

        existing = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name == canonical)
            .one_or_none()
        )
        if existing is not None:
            raise AssistantBootstrapRejected("skill_package_already_exists")

        package = AssistantSkillPackage(
            canonical_name=canonical,
            display_name=_display_name_for(parsed),
            description=parsed.frontmatter.description,
            migration_state="native",
            catalog_enabled=True,
            is_system=True,
            aggregate_revision=0,
            catalog_enabled_at=utcnow(),
            catalog_enabled_by="system:bootstrap",
        )
        self.db.add(package)
        self.db.flush()

        self._reserve_canonical_alias(package_id=package.id, canonical_name=canonical)
        if parsed.manifest is not None:
            for raw in list(parsed.manifest.legacy_aliases or ()):
                self._reserve_legacy_alias(package_id=package.id, alias=raw)

        draft = self._insert_skill_draft(package=package, parsed=parsed, sequence_no=1)
        package.draft_version_id = draft.id
        self.db.flush()

        set_digest = binding_set_digest_from_bindings(bindings)
        ver_digest = version_digest_from_parts(
            content_digest=str(draft.content_digest),
            binding_set_digest=set_digest,
        )
        publish = AssistantSkillVersion(
            skill_package_id=package.id,
            sequence_no=2,
            version_name="publish-system-bootstrap-1",
            version_source="publish",
            source_draft_version_id=draft.id,
            # Skill version CK allows api|import|legacy only; record bootstrap channel
            # via extension_manifest while keeping a legal durable origin.
            origin="api",
            skill_md=draft.skill_md,
            mindatlas_yaml=draft.mindatlas_yaml,
            frontmatter=draft.frontmatter,
            extension_manifest={
                **(dict(draft.extension_manifest or {})),
                "systemBootstrap": True,
                "seedManifestDigest": seed.manifest.manifest_digest,
            },
            resource_index=draft.resource_index,
            skill_md_digest=draft.skill_md_digest,
            manifest_digest=draft.manifest_digest,
            resource_index_digest=draft.resource_index_digest,
            content_digest=draft.content_digest,
            binding_set_digest=set_digest,
            version_digest=ver_digest,
        )
        self.db.add(publish)
        self.db.flush()

        # Copy resource references from draft.
        resource_rows = (
            self.db.query(AssistantSkillVersionResource, AssistantSkillResourceBlob)
            .join(
                AssistantSkillResourceBlob,
                AssistantSkillResourceBlob.id == AssistantSkillVersionResource.blob_id,
            )
            .filter(AssistantSkillVersionResource.skill_version_id == draft.id)
            .all()
        )
        for resource, blob in sorted(resource_rows, key=lambda item: item[0].path):
            self.db.add(
                AssistantSkillVersionResource(
                    skill_version_id=publish.id,
                    path=resource.path,
                    resource_kind=resource.resource_kind,
                    media_type=resource.media_type,
                    byte_size=resource.byte_size,
                    sha256=resource.sha256,
                    blob_id=blob.id,
                    executable=False,
                )
            )

        for ordinal, resolved in enumerate(
            sorted(bindings, key=lambda b: (b.capability_type, b.capability_key))
        ):
            binding_row = AssistantSkillCapabilityBinding(
                skill_version_id=publish.id,
                ordinal=ordinal,
                capability_type=resolved.capability_type,
                capability_key=resolved.capability_key,
                resolution_status="resolved",
                target_identity=resolved.target_identity,
                resolved_tool_id=resolved.resolved_tool_id,
                resolved_workflow_version_id=resolved.resolved_workflow_version_id,
                resolved_agent_version_id=resolved.resolved_agent_version_id,
                resolved_revision=resolved.resolved_revision,
                input_schema_digest=resolved.input_schema_digest,
                output_schema_digest=resolved.output_schema_digest,
                config_digest=resolved.config_digest,
                executable_revision=resolved.executable_revision,
                resolution_digest=resolved.resolution_digest,
                dependency_closure_digest=resolved.dependency_closure_digest,
                binding_contract_digest=resolved.binding_contract_digest,
                resolution_snapshot=resolved.resolution_snapshot,
            )
            self.db.add(binding_row)
            self.db.flush()
            for dep in sorted(resolved.dependencies, key=lambda d: d.ordinal):
                self.db.add(
                    AssistantSkillCapabilityDependency(
                        binding_id=binding_row.id,
                        ordinal=dep.ordinal,
                        dependency_path=dep.dependency_path,
                        dependency_type=dep.dependency_type,
                        target_identity=dep.target_identity,
                        resolved_tool_id=dep.resolved_tool_id,
                        resolved_workflow_version_id=dep.resolved_workflow_version_id,
                        resolved_agent_version_id=dep.resolved_agent_version_id,
                        resolved_model_id=dep.resolved_model_id,
                        target_revision=dep.target_revision,
                        input_schema_digest=dep.input_schema_digest,
                        output_schema_digest=dep.output_schema_digest,
                        resolution_digest=dep.resolution_digest,
                        dependency_digest=dep.dependency_digest,
                        resolution_snapshot=dep.resolution_snapshot,
                    )
                )
        self.db.flush()

        package.published_version_id = publish.id
        package.catalog_enabled = True
        package.aggregate_revision = 1
        package.last_admin_request_id = f"system-bootstrap-skill:{seed.manifest.manifest_digest[:32]}"
        package.last_admin_request_digest = require_sha256(
            sha256_canonical_json(
                {
                    "action": "system_bootstrap_skill",
                    "canonicalName": canonical,
                    "versionDigest": ver_digest,
                    "seedManifestDigest": seed.manifest.manifest_digest,
                }
            ),
            field_name="admin_request_digest",
        )
        self.db.flush()
        return package, publish

    def _insert_skill_draft(
        self,
        *,
        package: AssistantSkillPackage,
        parsed: ParsedSkillPackage,
        sequence_no: int,
    ) -> AssistantSkillVersion:
        version = AssistantSkillVersion(
            skill_package_id=package.id,
            sequence_no=sequence_no,
            version_name="draft-system-bootstrap-1",
            version_source="save",
            source_draft_version_id=None,
            origin="api",
            skill_md=(
                parsed.skill_md_bytes.decode("utf-8")
                if isinstance(parsed.skill_md_bytes, (bytes, bytearray))
                else (parsed.skill_md_bytes or "")
            ),
            mindatlas_yaml=(
                parsed.mindatlas_yaml_bytes.decode("utf-8")
                if isinstance(parsed.mindatlas_yaml_bytes, (bytes, bytearray))
                else parsed.mindatlas_yaml_bytes
            ),
            frontmatter=_frontmatter_json(parsed),
            extension_manifest=_manifest_json(parsed),
            resource_index=_resource_index_json(parsed),
            skill_md_digest=parsed.skill_md_digest,
            manifest_digest=parsed.manifest_digest,
            resource_index_digest=parsed.resource_index_digest,
            content_digest=parsed.content_digest,
            binding_set_digest=None,
            version_digest=None,
        )
        self.db.add(version)
        self.db.flush()
        self._insert_resources(version=version, parsed=parsed)
        return version

    def _insert_resources(
        self, *, version: AssistantSkillVersion, parsed: ParsedSkillPackage
    ) -> None:
        for item in sorted(parsed.resources, key=lambda r: r.path):
            blob = self._get_or_create_blob(
                sha256=item.sha256,
                content=item.content,
                byte_size=item.byte_size,
            )
            self.db.add(
                AssistantSkillVersionResource(
                    skill_version_id=version.id,
                    path=item.path,
                    resource_kind=item.resource_kind,
                    media_type=item.media_type,
                    byte_size=item.byte_size,
                    sha256=item.sha256,
                    blob_id=blob.id,
                    executable=False,
                )
            )
        self.db.flush()

    def _get_or_create_blob(
        self, *, sha256: str, content: bytes, byte_size: int
    ) -> AssistantSkillResourceBlob:
        if byte_size != len(content):
            raise AssistantBootstrapRejected("resource_size_mismatch")
        existing = (
            self.db.query(AssistantSkillResourceBlob)
            .filter(
                AssistantSkillResourceBlob.sha256 == sha256,
                AssistantSkillResourceBlob.byte_size == byte_size,
            )
            .one_or_none()
        )
        if existing is not None:
            if bytes(existing.content) != content:
                raise AssistantBootstrapRejected("resource_blob_collision")
            return existing
        blob = AssistantSkillResourceBlob(
            sha256=sha256,
            byte_size=byte_size,
            content=content,
        )
        self.db.add(blob)
        self.db.flush()
        return blob

    def _reserve_canonical_alias(self, *, package_id: UUID, canonical_name: str) -> None:
        normalized = normalize_skill_lookup_name(canonical_name)
        row = AssistantSkillPackageAlias(
            skill_package_id=package_id,
            alias=canonical_name,
            normalized_alias=normalized,
            alias_type="canonical",
        )
        self.db.add(row)
        self.db.flush()

    def _reserve_legacy_alias(self, *, package_id: UUID, alias: str) -> None:
        normalized = normalize_skill_lookup_name(alias)
        if is_reserved_skill_lookup_name(alias):
            raise AssistantBootstrapRejected("skill_alias_reserved")
        row = AssistantSkillPackageAlias(
            skill_package_id=package_id,
            alias=alias,
            normalized_alias=normalized,
            alias_type="legacy",
        )
        self.db.add(row)
        self.db.flush()

    def _stage_system_profile(
        self, profile_snapshot: MainAgentProfileSnapshotV2, *, package_id: UUID
    ):
        # package_id is accepted for future allowlist scopes; seed uses all_published.
        _ = package_id
        existing = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.profile_key == DEFAULT_MAIN_AGENT_PROFILE_KEY)
            .one_or_none()
        )
        if existing is not None:
            raise AssistantBootstrapRejected("profile_already_exists")

        payload = profile_snapshot.normalized_payload()
        digest = profile_snapshot.content_digest()

        profile = AssistantMainAgentProfile(
            profile_key=DEFAULT_MAIN_AGENT_PROFILE_KEY,
            display_name=DEFAULT_MAIN_AGENT_DISPLAY_NAME,
            is_default=True,
            migration_state="bootstrap",
            runtime_enabled=True,
            aggregate_revision=0,
        )
        self.db.add(profile)
        self.db.flush()

        draft = AssistantMainAgentProfileVersion(
            profile_id=profile.id,
            sequence_no=1,
            version_name="draft-system-bootstrap-1",
            version_source="save",
            origin="bootstrap",
            source_draft_version_id=None,
            snapshot=payload,
            content_digest=digest,
            source_ref={
                "origin": "system_bootstrap",
                "seedContractDigest": None,  # filled below via evidence only
            },
        )
        self.db.add(draft)
        self.db.flush()
        profile.draft_version_id = draft.id

        publish = AssistantMainAgentProfileVersion(
            profile_id=profile.id,
            sequence_no=2,
            version_name="publish-system-bootstrap-1",
            version_source="publish",
            origin="bootstrap",
            source_draft_version_id=draft.id,
            snapshot=payload,
            content_digest=digest,
            source_ref={
                "origin": "system_bootstrap",
            },
        )
        self.db.add(publish)
        self.db.flush()

        profile.published_version_id = publish.id
        profile.runtime_enabled = True
        profile.migration_state = "bootstrap"
        profile.aggregate_revision = 1
        profile.last_admin_request_id = f"system-bootstrap-profile:{digest[:32]}"
        profile.last_admin_request_digest = require_sha256(
            sha256_canonical_json(
                {
                    "action": "system_bootstrap_profile",
                    "profileKey": DEFAULT_MAIN_AGENT_PROFILE_KEY,
                    "contentDigest": digest,
                }
            ),
            field_name="admin_request_digest",
        )
        self.db.flush()
        return profile, publish

    def _resolve_model_identity_digest(
        self, *, model_id: UUID, build_revision: str
    ) -> str:
        """Deterministic non-secret model identity (no decrypt, no probe)."""
        binding = (
            self.db.query(AiComponentBinding)
            .filter(AiComponentBinding.component == "assistant")
            .one_or_none()
        )
        if binding is None or binding.llm_model_id is None:
            raise AssistantBootstrapRejected("model_unbound")
        if binding.llm_model_id != model_id:
            raise AssistantBootstrapRejected("model_binding_mismatch")

        model = self.db.get(AiModel, model_id)
        if model is None:
            raise AssistantBootstrapRejected("model_missing")
        if str(model.model_type or "") != "llm":
            raise AssistantBootstrapRejected("model_type_unsupported")
        credential = self.db.get(AiCredential, model.credential_id)
        if credential is None:
            raise AssistantBootstrapRejected("credential_missing")

        credential_config_digest = _credential_config_digest(
            base_url=str(credential.base_url or ""),
            runtime_revision=int(credential.runtime_revision or 1),
        )
        return require_sha256(
            sha256_canonical_json(
                {
                    "schemaVersion": 1,
                    "kind": "bound_assistant_model_identity",
                    "modelId": str(model.id),
                    "modelName": str(model.name or ""),
                    "modelType": "llm",
                    "modelRuntimeRevision": int(model.runtime_revision or 1),
                    "credentialId": str(credential.id),
                    "credentialRuntimeRevision": int(credential.runtime_revision or 1),
                    "credentialConfigDigest": credential_config_digest,
                    "buildRevision": str(build_revision),
                }
            ),
            field_name="model_identity_digest",
        )

    def _build_subject(
        self,
        *,
        profile_version: AssistantMainAgentProfileVersion,
        model_id: UUID,
        model_identity_digest: str,
        package: AssistantSkillPackage,
        version: AssistantSkillVersion,
        seed: VerifiedAssistantSystemSeed,
        build_revision: str,
    ) -> AssistantRuntimeSubject:
        package_entry = {
            "packageId": str(package.id),
            "canonicalName": str(package.canonical_name),
            "versionId": str(version.id),
            "versionDigest": str(version.version_digest),
            "contentDigest": str(version.content_digest),
            "bindingSetDigest": str(version.binding_set_digest),
            "isSystem": True,
            "catalogEnabled": True,
        }
        package_closure = (package_entry,)
        package_closure_digest = require_sha256(
            sha256_canonical_json(list(package_closure)),
            field_name="package_closure_digest",
        )
        capability_closure_digest = require_sha256(
            str(version.binding_set_digest),
            field_name="capability_closure_digest",
        )
        return AssistantRuntimeSubject(
            profile_version_id=profile_version.id,
            profile_content_digest=str(profile_version.content_digest),
            model_id=model_id,
            model_identity_digest=model_identity_digest,
            package_closure=package_closure,
            package_closure_digest=package_closure_digest,
            capability_closure_digest=capability_closure_digest,
            seed_manifest_digest=seed.manifest.manifest_digest,
            build_revision=build_revision,
            runtime_contract_version=RUNTIME_CONTRACT_VERSION,
            checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
            capability_feature_digest=default_capability_feature_digest(),
        )

    def _build_closure_view(
        self, *, subject: AssistantRuntimeSubject, rollout: Any
    ) -> _BootstrapClosureView:
        closure_digest = require_sha256(
            sha256_canonical_json(
                {
                    "schemaVersion": 1,
                    "rolloutRevisionId": str(rollout.id),
                    "rolloutRevisionDigest": str(rollout.revision_digest),
                    "profileVersionId": str(subject.profile_version_id),
                    "profileContentDigest": subject.profile_content_digest,
                    "modelId": str(subject.model_id),
                    "modelIdentityDigest": subject.model_identity_digest,
                    "packageClosureDigest": subject.package_closure_digest,
                    "capabilityClosureDigest": subject.capability_closure_digest,
                    "seedManifestDigest": subject.seed_manifest_digest,
                    "buildRevision": subject.build_revision,
                    "runtimeContractVersion": subject.runtime_contract_version,
                    "checkpointCodecVersion": subject.checkpoint_codec_version,
                    "capabilityFeatureDigest": subject.capability_feature_digest,
                }
            ),
            field_name="closure_digest",
        )
        return _BootstrapClosureView(
            rollout_revision_id=rollout.id,
            rollout_revision_digest=str(rollout.revision_digest),
            profile_version_id=subject.profile_version_id,
            profile_content_digest=subject.profile_content_digest,
            model_id=subject.model_id,
            model_identity_digest=subject.model_identity_digest,
            package_closure_digest=subject.package_closure_digest,
            capability_closure_digest=subject.capability_closure_digest,
            seed_manifest_digest=subject.seed_manifest_digest,
            build_revision=subject.build_revision,
            runtime_contract_version=subject.runtime_contract_version,
            checkpoint_codec_version=subject.checkpoint_codec_version,
            capability_feature_digest=subject.capability_feature_digest,
            closure_digest=closure_digest,
        )

    def _safe_bootstrap_evidence(
        self,
        *,
        closure: _BootstrapClosureView,
        seed: VerifiedAssistantSystemSeed,
        profile_version: AssistantMainAgentProfileVersion,
        skill_version: AssistantSkillVersion,
        request: StageAssistantBootstrapRequest,
    ) -> dict[str, Any]:
        """Safe-only bootstrap evidence for the append-only prepared event.

        Publish-gate tables only admit skill/profile publish|enable actions, so
        system_bootstrap evidence is recorded on the rollout event (and scalar
        operator audit metadata from the coordinator). No secrets.
        """
        return {
            "seedManifestDigest": seed.manifest.manifest_digest,
            "seedContractDigest": seed.manifest.seed_contract_digest,
            "profileVersionId": str(profile_version.id),
            "profileContentDigest": str(profile_version.content_digest),
            "skillVersionId": str(skill_version.id),
            "skillVersionDigest": str(skill_version.version_digest),
            "rolloutRevisionId": str(closure.rollout_revision_id),
            "rolloutRevisionDigest": closure.rollout_revision_digest,
            "modelId": str(request.model_id),
            "modelIdentityDigest": closure.model_identity_digest,
            "buildRevision": request.build_revision,
            "closureDigest": closure.closure_digest,
            "origin": "system_bootstrap",
        }


__all__ = (
    "AssistantBootstrapFreshPermit",
    "AssistantBootstrapRejected",
    "AssistantSystemBootstrapper",
    "PreparedAssistantBootstrap",
    "StageAssistantBootstrapRequest",
)
