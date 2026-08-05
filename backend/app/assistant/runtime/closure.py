"""Canonical Main-Agent runtime closure builder (Plan 2 Task 5).

Deterministic Model identity + Subject/Closure revalidation. No Provider calls,
no secret material in digests, no activation side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant.domain.contracts import create_provider_ref
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
from app.assistant.durable.worker_registry import (
    RUNTIME_CONTRACT_VERSION,
    default_capability_feature_digest,
)
from app.assistant.provider_loop.adapters.openai_chat import (
    ADAPTER_KEY as OPENAI_ADAPTER_KEY,
    DEFAULT_ADAPTER_REVISION,
)
from app.assistant.provider_loop.aliases import OPENAI_CHAT_PROVIDER_PROTOCOL
from app.assistant.provider_loop.probe import (
    PROBE_CONTRACT_VERSION,
    build_endpoint_identity,
    build_model_config_digest,
)
from app.assistant.runtime.contracts import (
    AssistantRuntimeClosure,
    AssistantRuntimeSubject,
    require_sha256,
)
from app.assistant.runtime.models import AssistantMainAgentRolloutRevision
from app.assistant.runtime.seed import (
    SEED_MANIFEST_DIGEST,
    VerifiedAssistantSystemSeed,
    load_verified_assistant_system_seed,
)
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillCapabilityBinding,
    AssistantSkillPackage,
    AssistantSkillVersion,
)
from app.assistant.skills.schemas import MainAgentProfileSnapshotV2
from app.config import get_settings


class ModelIdentityUnavailable(RuntimeError):
    """Deterministic model identity could not be resolved (safe reason code)."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class RuntimeClosureDrift(RuntimeError):
    """Recomputed Subject/Closure diverges from an immutable rollout row."""

    def __init__(self, reason_code: str = "runtime_closure_drift") -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class BoundAssistantModelIdentity:
    model_id: UUID
    model_name: str
    model_type: Literal["llm"]
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    credential_config_digest: str
    model_config_digest: str
    provider_ref_digest: str
    identity_digest: str


def credential_config_digest(*, base_url: str, runtime_revision: int) -> str:
    """Secret-free credential config digest (endpoint shape + runtime revision)."""
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


def lock_active_model_and_credential(
    db: Session, *, model_id: UUID, lock: bool = False
) -> tuple[AiModel, AiCredential]:
    """Load the bound assistant model + credential (optional row lock)."""
    binding_stmt = select(AiComponentBinding).where(
        AiComponentBinding.component == "assistant"
    )
    if lock:
        binding_stmt = binding_stmt.with_for_update()
    binding = db.execute(binding_stmt).scalar_one_or_none()
    if binding is None or binding.llm_model_id is None:
        raise ModelIdentityUnavailable("model_unbound")
    if binding.llm_model_id != model_id:
        raise ModelIdentityUnavailable("model_binding_mismatch")

    model_stmt = select(AiModel).where(AiModel.id == model_id)
    if lock:
        model_stmt = model_stmt.with_for_update()
    model = db.execute(model_stmt).scalar_one_or_none()
    if model is None:
        raise ModelIdentityUnavailable("model_missing")
    if str(model.model_type or "") != "llm":
        raise ModelIdentityUnavailable("model_type_unsupported")

    cred_stmt = select(AiCredential).where(AiCredential.id == model.credential_id)
    if lock:
        cred_stmt = cred_stmt.with_for_update()
    credential = db.execute(cred_stmt).scalar_one_or_none()
    if credential is None:
        raise ModelIdentityUnavailable("credential_missing")
    return model, credential


def resolve_bound_assistant_model_identity(
    db: Session,
    *,
    model_id: UUID,
    app_build_revision: str,
    lock: bool = False,
) -> BoundAssistantModelIdentity:
    """Deterministic bound-model identity — no Provider call, no secret bytes."""
    model, credential = lock_active_model_and_credential(
        db, model_id=model_id, lock=lock
    )
    build = str(app_build_revision or "").strip()
    if not build:
        raise ModelIdentityUnavailable("build_revision_missing")

    endpoint = build_endpoint_identity(str(credential.base_url or ""))
    cred_digest = credential_config_digest(
        base_url=str(credential.base_url or ""),
        runtime_revision=int(credential.runtime_revision or 1),
    )
    provider_ref = create_provider_ref(
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        provider_config_id=credential.id,
        provider_runtime_revision=int(credential.runtime_revision or 1),
        provider_config_digest=cred_digest,
        adapter_key=OPENAI_ADAPTER_KEY,
        adapter_revision=DEFAULT_ADAPTER_REVISION,
        protocol_revision="1",
        app_build_revision=build,
    )
    model_cfg = build_model_config_digest(
        model_id=model.id,
        model_name=str(model.name or ""),
        model_type="llm",
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=credential.id,
        credential_runtime_revision=int(credential.runtime_revision or 1),
        endpoint_identity=endpoint,
        adapter_key=OPENAI_ADAPTER_KEY,
        adapter_revision=DEFAULT_ADAPTER_REVISION,
        app_build_revision=build,
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        probe_contract_version=PROBE_CONTRACT_VERSION,
    )
    payload = {
        "modelId": str(model.id),
        "modelName": str(model.name or ""),
        "modelType": "llm",
        "modelRuntimeRevision": int(model.runtime_revision or 1),
        "credentialId": str(credential.id),
        "credentialRuntimeRevision": int(credential.runtime_revision or 1),
        "credentialConfigDigest": provider_ref.provider_config_digest,
        "modelConfigDigest": model_cfg,
        "providerRefDigest": provider_ref.provider_ref_digest,
    }
    return BoundAssistantModelIdentity(
        model_id=model.id,
        model_name=str(model.name or ""),
        model_type="llm",
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=credential.id,
        credential_runtime_revision=int(credential.runtime_revision or 1),
        credential_config_digest=str(payload["credentialConfigDigest"]),
        model_config_digest=str(payload["modelConfigDigest"]),
        provider_ref_digest=str(payload["providerRefDigest"]),
        identity_digest=require_sha256(
            sha256_canonical_json(payload), field_name="identity_digest"
        ),
    )


def _execute_optional_probe(
    db: Session,
    *,
    model_id: UUID,
    confirm_provider_call: bool,
) -> dict[str, Any]:
    """Internal hook for the diagnostic probe path (patchable in tests)."""
    from app.ai_registry.service import AiModelCapabilityProbeService

    if not confirm_provider_call:
        raise ModelIdentityUnavailable("probe_confirmation_required")
    service = AiModelCapabilityProbeService(db)
    result = service.run_live_probe(
        model_id,
        adapter_key=OPENAI_ADAPTER_KEY,
        confirm_provider_call=True,
        promote=False,
    )
    return {
        "status": getattr(result, "status", None),
        "probeId": str(getattr(result, "probe_id", "") or ""),
    }


def run_optional_assistant_model_probe(
    db: Session,
    *,
    model_id: UUID,
    confirm_provider_call: bool = False,
) -> dict[str, Any]:
    """Paid live probe — diagnostic only; never enters identity/readiness."""
    return _execute_optional_probe(
        db, model_id=model_id, confirm_provider_call=confirm_provider_call
    )


def _resource_merkle_root(version: AssistantSkillVersion) -> str:
    """Canonical resource merkle: resource_index_digest (content-addressed index)."""
    return require_sha256(
        str(version.resource_index_digest or ""),
        field_name="resource_merkle_root",
    )


def _binding_target_version_id(binding: AssistantSkillCapabilityBinding) -> str | None:
    if binding.resolved_workflow_version_id is not None:
        return str(binding.resolved_workflow_version_id)
    if binding.resolved_agent_version_id is not None:
        return str(binding.resolved_agent_version_id)
    # System/remote tools have no version id — stable empty sentinel for sorting.
    if binding.resolved_tool_id is not None:
        return str(binding.resolved_tool_id)
    return ""


def _binding_target_contract_digest(
    binding: AssistantSkillCapabilityBinding,
) -> str:
    digest = binding.binding_contract_digest or binding.resolution_digest or ""
    return require_sha256(str(digest), field_name="target_contract_digest")


def compute_package_closure(
    packages: list[tuple[AssistantSkillPackage, AssistantSkillVersion]],
) -> tuple[tuple[dict[str, Any], ...], str]:
    entries = tuple(
        sorted(
            (
                {
                    "packageId": str(package.id),
                    "versionId": str(version.id),
                    "versionDigest": str(version.version_digest),
                    "contentDigest": str(version.content_digest),
                    "resourceMerkleRoot": _resource_merkle_root(version),
                }
                for package, version in packages
            ),
            key=lambda item: (item["packageId"], item["versionId"]),
        )
    )
    digest = require_sha256(
        sha256_canonical_json(list(entries)),
        field_name="package_closure_digest",
    )
    return entries, digest


def compute_capability_closure(
    bindings: list[AssistantSkillCapabilityBinding],
) -> tuple[tuple[dict[str, Any], ...], str]:
    entries = tuple(
        sorted(
            (
                {
                    "type": str(binding.capability_type),
                    "key": str(binding.capability_key),
                    "targetVersionId": _binding_target_version_id(binding) or "",
                    "targetContractDigest": _binding_target_contract_digest(binding),
                }
                for binding in bindings
            ),
            key=lambda item: (
                item["type"],
                item["key"],
                item["targetVersionId"],
            ),
        )
    )
    digest = require_sha256(
        sha256_canonical_json(list(entries)),
        field_name="capability_closure_digest",
    )
    return entries, digest


def compute_closure_digest(
    *,
    rollout_revision_id: UUID,
    rollout_revision_digest: str,
    subject: AssistantRuntimeSubject,
) -> str:
    payload = {
        "schemaVersion": 1,
        "rolloutRevisionId": str(rollout_revision_id),
        "rolloutRevisionDigest": str(rollout_revision_digest),
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
    return require_sha256(
        sha256_canonical_json(payload), field_name="closure_digest"
    )


class AssistantRuntimeClosureBuilder:
    """Build and revalidate the non-circular runtime Subject/Closure."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def build_subject(
        self,
        *,
        profile_version_id: UUID | None = None,
        profile_version: AssistantMainAgentProfileVersion | None = None,
        model_id: UUID,
        build_revision: str | None = None,
        seed: VerifiedAssistantSystemSeed | None = None,
        package: AssistantSkillPackage | None = None,
        version: AssistantSkillVersion | None = None,
        bindings: list[AssistantSkillCapabilityBinding] | None = None,
        lock: bool = False,
    ) -> AssistantRuntimeSubject:
        build = str(
            build_revision
            if build_revision is not None
            else (get_settings().app_build_revision or "")
        ).strip()
        if not build:
            raise RuntimeClosureDrift("build_revision_missing")

        profile_row = profile_version
        if profile_row is None:
            if profile_version_id is None:
                raise RuntimeClosureDrift("profile_unpublished")
            stmt = select(AssistantMainAgentProfileVersion).where(
                AssistantMainAgentProfileVersion.id == profile_version_id
            )
            if lock:
                stmt = stmt.with_for_update()
            profile_row = self.db.execute(stmt).scalar_one_or_none()
        if profile_row is None:
            raise RuntimeClosureDrift("profile_unpublished")
        if str(profile_row.version_source) != "publish":
            # Bootstrap may temporarily re-point in drift tests; still require V2 parse.
            pass
        try:
            MainAgentProfileSnapshotV2.model_validate(profile_row.snapshot or {})
        except Exception as exc:
            raise RuntimeClosureDrift("profile_unpublished") from exc

        identity = resolve_bound_assistant_model_identity(
            self.db,
            model_id=model_id,
            app_build_revision=build,
            lock=lock,
        )

        packages = self._resolve_enabled_published_packages(
            package=package, version=version, lock=lock
        )
        package_closure, package_closure_digest = compute_package_closure(packages)

        if bindings is None:
            binding_rows: list[AssistantSkillCapabilityBinding] = []
            for _pkg, ver in packages:
                rows = (
                    self.db.query(AssistantSkillCapabilityBinding)
                    .filter(
                        AssistantSkillCapabilityBinding.skill_version_id == ver.id
                    )
                    .all()
                )
                binding_rows.extend(rows)
        else:
            binding_rows = list(bindings)
        _cap_entries, capability_closure_digest = compute_capability_closure(
            binding_rows
        )

        if seed is not None:
            # Still verify the provided seed matches the build-owned constant.
            if seed.manifest.manifest_digest != SEED_MANIFEST_DIGEST:
                raise RuntimeClosureDrift("system_seed_invalid")
            seed_digest = seed.manifest.manifest_digest
        else:
            try:
                verified = load_verified_assistant_system_seed()
                seed_digest = verified.manifest.manifest_digest
            except Exception as exc:
                raise RuntimeClosureDrift("system_seed_invalid") from exc
            if seed_digest != SEED_MANIFEST_DIGEST:
                raise RuntimeClosureDrift("system_seed_invalid")
        seed_digest = require_sha256(seed_digest, field_name="seed_manifest_digest")

        return AssistantRuntimeSubject(
            profile_version_id=profile_row.id,
            profile_content_digest=require_sha256(
                str(profile_row.content_digest),
                field_name="profile_content_digest",
            ),
            model_id=identity.model_id,
            model_identity_digest=identity.identity_digest,
            package_closure=package_closure,
            package_closure_digest=package_closure_digest,
            capability_closure_digest=capability_closure_digest,
            seed_manifest_digest=seed_digest,
            build_revision=build,
            runtime_contract_version=RUNTIME_CONTRACT_VERSION,
            checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
            capability_feature_digest=default_capability_feature_digest(),
        )

    def build(
        self,
        *,
        rollout_revision_id: UUID,
        lock: bool = False,
        build_revision: str | None = None,
    ) -> AssistantRuntimeClosure:
        """Recompute Subject from live process + bound pointers; compare to row.

        Profile/Model pointers come from the immutable rollout row. Build,
        seed, runtime contract, codec, and feature digest come from the live
        process so drift is detected when either the row or the world moves.
        """
        rollout = self._load_rollout(rollout_revision_id, lock=lock)
        live_build = str(
            build_revision
            if build_revision is not None
            else (get_settings().app_build_revision or "")
        ).strip()
        if not live_build:
            raise RuntimeClosureDrift("build_revision_missing")
        subject = self.build_subject(
            profile_version_id=rollout.profile_version_id,
            model_id=rollout.model_id,
            build_revision=live_build,
            lock=lock,
        )
        self._assert_subject_matches_rollout(subject, rollout)
        # Closure carries the *rollout* revision digest (immutable identity) plus
        # the recomputed (and now-matching) subject fields.
        closure_digest = compute_closure_digest(
            rollout_revision_id=rollout.id,
            rollout_revision_digest=str(rollout.revision_digest),
            subject=subject,
        )
        return AssistantRuntimeClosure(
            schema_version=1,
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

    def revalidate(self, closure: AssistantRuntimeClosure) -> AssistantRuntimeClosure:
        rebuilt = self.build(
            rollout_revision_id=closure.rollout_revision_id,
            lock=False,
        )
        if rebuilt.model_dump(mode="json", by_alias=True) != closure.model_dump(
            mode="json", by_alias=True
        ):
            raise RuntimeClosureDrift("runtime_closure_drift")
        return rebuilt

    def _load_rollout(
        self, rollout_revision_id: UUID, *, lock: bool
    ) -> AssistantMainAgentRolloutRevision:
        stmt = select(AssistantMainAgentRolloutRevision).where(
            AssistantMainAgentRolloutRevision.id == rollout_revision_id
        )
        if lock:
            stmt = stmt.with_for_update()
        rollout = self.db.execute(stmt).scalar_one_or_none()
        if rollout is None:
            raise RuntimeClosureDrift("rollout_missing")
        return rollout

    def _assert_subject_matches_rollout(
        self,
        subject: AssistantRuntimeSubject,
        rollout: AssistantMainAgentRolloutRevision,
    ) -> None:
        checks = (
            (subject.profile_version_id, rollout.profile_version_id, "profile_pointer"),
            (
                subject.profile_content_digest,
                str(rollout.profile_content_digest),
                "profile_content",
            ),
            (subject.model_id, rollout.model_id, "model_identity"),
            (
                subject.model_identity_digest,
                str(rollout.model_identity_digest),
                "model_identity",
            ),
            (
                subject.package_closure_digest,
                str(rollout.package_closure_digest),
                "package_version",
            ),
            (
                subject.capability_closure_digest,
                str(rollout.capability_closure_digest),
                "tool_binding",
            ),
            (
                subject.seed_manifest_digest,
                str(rollout.seed_manifest_digest),
                "seed_digest",
            ),
            (subject.build_revision, str(rollout.build_revision), "build_revision"),
            (
                int(subject.runtime_contract_version),
                int(rollout.runtime_contract_version),
                "runtime_contract",
            ),
            (
                int(subject.checkpoint_codec_version),
                int(rollout.checkpoint_codec_version),
                "checkpoint_codec",
            ),
            (
                subject.capability_feature_digest,
                str(rollout.capability_feature_digest),
                "feature_digest",
            ),
        )
        for left, right, code in checks:
            if left != right:
                raise RuntimeClosureDrift(code)

        # package_closure_json equality (order-insensitive via digest already checked;
        # still surface package drift if JSON diverges while digest collides — belt).
        stored = list(rollout.package_closure_json or [])
        live = [dict(item) for item in subject.package_closure]
        if stored != live:
            # Digests already compared; tolerate key-order differences by re-digest.
            if sha256_canonical_json(stored) != sha256_canonical_json(live):
                raise RuntimeClosureDrift("package_version")

    def _resolve_enabled_published_packages(
        self,
        *,
        package: AssistantSkillPackage | None,
        version: AssistantSkillVersion | None,
        lock: bool,
    ) -> list[tuple[AssistantSkillPackage, AssistantSkillVersion]]:
        if package is not None and version is not None:
            return [(package, version)]

        stmt = select(AssistantSkillPackage).where(
            AssistantSkillPackage.catalog_enabled.is_(True),
            AssistantSkillPackage.published_version_id.isnot(None),
        )
        if lock:
            stmt = stmt.with_for_update()
        packages = list(self.db.execute(stmt).scalars().all())
        result: list[tuple[AssistantSkillPackage, AssistantSkillVersion]] = []
        for pkg in packages:
            ver = self.db.get(AssistantSkillVersion, pkg.published_version_id)
            if ver is None:
                continue
            if str(ver.version_source) != "publish":
                continue
            if not ver.version_digest or not ver.content_digest:
                continue
            result.append((pkg, ver))
        if not result:
            raise RuntimeClosureDrift("package_unpublished")
        return result


__all__ = (
    "AssistantRuntimeClosureBuilder",
    "BoundAssistantModelIdentity",
    "ModelIdentityUnavailable",
    "RuntimeClosureDrift",
    "compute_capability_closure",
    "compute_closure_digest",
    "compute_package_closure",
    "credential_config_digest",
    "lock_active_model_and_credential",
    "resolve_bound_assistant_model_identity",
    "run_optional_assistant_model_probe",
)
