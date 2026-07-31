"""Digest-locked Assistant system seed (Plan 2 Task 3).

Build-owned Profile V2 + mindatlas-universal Skill package, locked by
manifestDigest / seedContractDigest. The loader accepts only the embedded
package path — no path, URL, environment, or request override.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_bytes, sha256_canonical_json
from app.assistant.domain.json_schema import binding_schema_digest
from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
from app.assistant.durable.worker_registry import (
    RUNTIME_CONTRACT_VERSION,
    default_capability_feature_digest,
)
from app.assistant.runtime.contracts import require_sha256
from app.assistant.runtime.system_seed.expected import (
    SEED_CONTRACT_DIGEST,
    SEED_MANIFEST_DIGEST,
)
from app.assistant.skills.contracts import ParsedSkillPackage
from app.assistant.skills.package_io import detect_media_type, parse_skill_directory_files
from app.assistant.skills.resolution import (
    compute_system_tool_contract_set_digest,
    system_tool_schemas,
)
from app.assistant.skills.schemas import MainAgentProfileSnapshotV2
from app.assistant_config.registry import ToolRegistry

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SYSTEM_SEED_DIR: Final[Path] = Path(__file__).resolve().parent / "system_seed"
PROFILE_PATH: Final[Path] = SYSTEM_SEED_DIR / "main-agent-profile.v2.json"
SKILL_DIRECTORY: Final[Path] = SYSTEM_SEED_DIR / "skills" / "mindatlas-universal"
MANIFEST_PATH: Final[Path] = SYSTEM_SEED_DIR / "manifest.v1.json"

SEED_CAPABILITY_KEYS: Final[tuple[str, ...]] = (
    "create_entry",
    "get_entry_detail",
    "search_entries",
)
FORBIDDEN_SEED_WRITE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "update_entry",
        "merge_entry",
        "create_relation",
        "relation_followup",
    }
)


class SystemSeedInvalid(ValueError):
    """Fail-closed system seed verification error."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class SeedArtifact(FrozenContract):
    relative_path: str
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str

    @field_validator("relative_path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("relative_path must be a non-empty string")
        path = value.replace("\\", "/")
        if path != value:
            raise ValueError("relative_path must use POSIX separators")
        if path.startswith("/") or path.startswith("\\"):
            raise ValueError("absolute paths are forbidden in seed artifacts")
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("relative_path must be a normalized relative path")
        if path != Path(path).as_posix():
            raise ValueError("relative_path must be normalized")
        return path

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("media_type must be non-empty")
        return value

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        return require_sha256(value, field_name="sha256")


class SeedCapabilityBinding(FrozenContract):
    type: Literal["tool"]
    key: Literal["create_entry", "get_entry_detail", "search_entries"]
    target_contract_digest: str

    @field_validator("target_contract_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_sha256(value, field_name="target_contract_digest")


class SeedBuildCompatibility(FrozenContract):
    runtime_contract_version: int = Field(gt=0)
    checkpoint_codec_version: int = Field(gt=0)
    capability_feature_digest: str

    @field_validator("capability_feature_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_sha256(value, field_name="capability_feature_digest")


class AssistantSystemSeedManifest(FrozenContract):
    schema_version: Literal[1]
    profile_artifact: SeedArtifact
    skill_artifacts: tuple[SeedArtifact, ...]
    capability_bindings: tuple[SeedCapabilityBinding, ...]
    model_binding_slots: tuple[Literal["assistant"], ...]
    build_compatibility: SeedBuildCompatibility
    seed_contract_digest: str
    manifest_digest: str

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_schema_version(cls, value: Any) -> int:
        if value is not True and value == 1 and type(value) is int:
            return 1
        raise ValueError("schemaVersion must be exactly integer 1")

    @field_validator("seed_contract_digest", "manifest_digest")
    @classmethod
    def _validate_digest(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field_name=info.field_name)

    @field_validator("model_binding_slots")
    @classmethod
    def _validate_model_slots(
        cls, value: tuple[Literal["assistant"], ...]
    ) -> tuple[Literal["assistant"], ...]:
        if value != ("assistant",):
            raise ValueError("model_binding_slots must be exactly ('assistant',)")
        return value

    @model_validator(mode="after")
    def _validate_canonical_lists(self) -> AssistantSystemSeedManifest:
        skill_paths = [item.relative_path for item in self.skill_artifacts]
        if skill_paths != sorted(skill_paths):
            raise ValueError("skill_artifacts must be sorted by relative_path")
        if len(skill_paths) != len(set(skill_paths)):
            raise ValueError("skill_artifacts must not contain duplicate paths")

        all_paths = [self.profile_artifact.relative_path, *skill_paths]
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("seed artifacts must not contain duplicate paths")
        for path in all_paths:
            if path.startswith("/") or ".." in path.split("/"):
                raise ValueError("seed artifact paths must stay inside system_seed")

        binding_keys = [item.key for item in self.capability_bindings]
        if binding_keys != sorted(binding_keys):
            raise ValueError("capability_bindings must be sorted by key")
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError("capability_bindings must not contain duplicate keys")
        if tuple(binding_keys) != SEED_CAPABILITY_KEYS:
            raise ValueError(
                "capability_bindings must be exactly "
                f"{SEED_CAPABILITY_KEYS!r}, got {tuple(binding_keys)!r}"
            )
        for key in binding_keys:
            if key in FORBIDDEN_SEED_WRITE_KEYS:
                raise ValueError(f"forbidden capability binding {key!r}")
        return self


@dataclass(frozen=True)
class VerifiedAssistantSystemSeed:
    manifest: AssistantSystemSeedManifest
    profile: MainAgentProfileSnapshotV2
    parsed_skill: ParsedSkillPackage
    capability_bindings: tuple[SeedCapabilityBinding, ...]


def read_seed_skill_files(root: Path) -> dict[str, bytes]:
    """Read skill package files from an on-disk directory (no symlinks)."""
    if not root.is_dir():
        raise SystemSeedInvalid("skill_directory_missing", f"missing skill directory: {root}")
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemSeedInvalid("seed_symlink_forbidden")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files[relative] = path.read_bytes()
    if "SKILL.md" not in files:
        raise SystemSeedInvalid("skill_md_missing", "SKILL.md is required in seed skill package")
    return files


def system_tool_seed_contract_digest(
    *,
    key: str,
    target_identity: str,
    input_schema_digest: str,
    output_schema_digest: str,
    system_tool_contract_set_digest: str,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "type": "tool",
            "key": key,
            "targetIdentity": target_identity,
            "inputSchemaDigest": input_schema_digest,
            "outputSchemaDigest": output_schema_digest,
            "systemToolContractSetDigest": system_tool_contract_set_digest,
        }
    )


def resolve_system_tool_contract_digests(
    keys: tuple[str, ...] = SEED_CAPABILITY_KEYS,
) -> list[dict[str, str]]:
    definitions = {item.name: item for item in ToolRegistry.list_system_tool_definitions()}
    contract_set_digest = compute_system_tool_contract_set_digest()
    bindings: list[dict[str, str]] = []
    for key in sorted(keys):
        if key not in definitions:
            raise SystemSeedInvalid("system_tool_missing", f"system tool missing: {key}")
        if key in FORBIDDEN_SEED_WRITE_KEYS:
            raise SystemSeedInvalid(
                "forbidden_capability_binding",
                f"forbidden capability binding: {key}",
            )
        input_schema, output_schema = system_tool_schemas(key)
        bindings.append(
            {
                "type": "tool",
                "key": key,
                "targetContractDigest": system_tool_seed_contract_digest(
                    key=key,
                    target_identity=f"system-tool:{key}",
                    input_schema_digest=binding_schema_digest(input_schema),
                    output_schema_digest=binding_schema_digest(output_schema),
                    system_tool_contract_set_digest=contract_set_digest,
                ),
            }
        )
    return bindings


def _relative_under_seed(path: Path, *, seed_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(seed_root.resolve()).as_posix()
    except ValueError as exc:
        raise SystemSeedInvalid(
            "artifact_outside_system_seed",
            f"artifact path escapes system_seed: {path}",
        ) from exc
    if relative.startswith("../") or relative == ".." or "/../" in f"/{relative}/":
        raise SystemSeedInvalid(
            "artifact_outside_system_seed",
            f"artifact path escapes system_seed: {path}",
        )
    return relative


def artifact_for(path: Path, *, seed_root: Path) -> dict[str, object]:
    if path.is_symlink():
        raise SystemSeedInvalid("seed_symlink_forbidden")
    if not path.is_file():
        raise SystemSeedInvalid("artifact_missing", f"missing seed artifact: {path}")
    content = path.read_bytes()
    relative = _relative_under_seed(path, seed_root=seed_root)
    return {
        "relativePath": relative,
        "mediaType": detect_media_type(relative, content),
        "byteSize": len(content),
        "sha256": sha256_bytes(content),
    }


def canonical_seed_artifacts(
    profile_path: Path,
    skill_directory: Path,
    *,
    seed_root: Path,
) -> list[dict[str, object]]:
    """Skill package artifacts only (profile is separate), sorted by relative path."""
    _ = profile_path  # profile is recorded separately as profileArtifact
    if skill_directory.is_symlink():
        raise SystemSeedInvalid("seed_symlink_forbidden")
    if not skill_directory.is_dir():
        raise SystemSeedInvalid(
            "skill_directory_missing",
            f"missing skill directory: {skill_directory}",
        )
    artifacts: list[dict[str, object]] = []
    for path in sorted(skill_directory.rglob("*")):
        if path.is_symlink():
            raise SystemSeedInvalid("seed_symlink_forbidden")
        if path.is_file():
            artifacts.append(artifact_for(path, seed_root=seed_root))
    artifacts.sort(key=lambda item: str(item["relativePath"]))
    return artifacts


def binding_contracts_as_payload(
    bindings: tuple[SeedCapabilityBinding, ...] | list[dict[str, str]],
) -> list[dict[str, str]]:
    if bindings and isinstance(bindings[0], SeedCapabilityBinding):
        return [
            {
                "type": item.type,
                "key": item.key,
                "targetContractDigest": item.target_contract_digest,
            }
            for item in bindings  # type: ignore[union-attr]
        ]
    return list(bindings)  # type: ignore[arg-type]


def compute_seed_contract_digest(
    *,
    profile: MainAgentProfileSnapshotV2,
    parsed_skill: ParsedSkillPackage,
    capability_bindings: tuple[SeedCapabilityBinding, ...] | list[dict[str, str]],
    runtime_contract_version: int = RUNTIME_CONTRACT_VERSION,
    checkpoint_codec_version: int = CURRENT_CHECKPOINT_CODEC_VERSION,
    capability_feature_digest: str | None = None,
) -> str:
    feature_digest = (
        capability_feature_digest
        if capability_feature_digest is not None
        else default_capability_feature_digest()
    )
    contract_payload = {
        "schemaVersion": 1,
        "profileContentDigest": profile.content_digest(),
        "skillContentDigest": parsed_skill.content_digest,
        "skillManifestDigest": parsed_skill.manifest_digest,
        "skillResourceIndexDigest": parsed_skill.resource_index_digest,
        "capabilityBindings": binding_contracts_as_payload(capability_bindings),
        "runtimeContractVersion": runtime_contract_version,
        "checkpointCodecVersion": checkpoint_codec_version,
        "capabilityFeatureDigest": feature_digest,
    }
    return sha256_canonical_json(contract_payload)


def build_seed_payload(
    *,
    seed_root: Path | None = None,
) -> tuple[dict[str, object], str, str]:
    """Build the canonical manifest payload and digests from source artifacts.

    ``seed_root`` is only for the offline builder (tests / --write / --check).
    The runtime loader never accepts an override and always uses SYSTEM_SEED_DIR.
    """
    root = seed_root if seed_root is not None else SYSTEM_SEED_DIR
    profile_path = root / "main-agent-profile.v2.json"
    skill_directory = root / "skills" / "mindatlas-universal"

    profile_bytes = profile_path.read_bytes()
    profile = MainAgentProfileSnapshotV2.model_validate_json(profile_bytes)
    parsed_skill = parse_skill_directory_files(
        read_seed_skill_files(skill_directory),
        expected_root_name=None,
    )
    binding_contracts = resolve_system_tool_contract_digests(SEED_CAPABILITY_KEYS)
    skill_artifacts = canonical_seed_artifacts(
        profile_path,
        skill_directory,
        seed_root=root,
    )
    feature_digest = default_capability_feature_digest()
    seed_contract_digest = compute_seed_contract_digest(
        profile=profile,
        parsed_skill=parsed_skill,
        capability_bindings=binding_contracts,
        capability_feature_digest=feature_digest,
    )
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "profileArtifact": artifact_for(profile_path, seed_root=root),
        "skillArtifacts": skill_artifacts,
        "capabilityBindings": binding_contracts,
        "modelBindingSlots": ["assistant"],
        "buildCompatibility": {
            "runtimeContractVersion": RUNTIME_CONTRACT_VERSION,
            "checkpointCodecVersion": CURRENT_CHECKPOINT_CODEC_VERSION,
            "capabilityFeatureDigest": feature_digest,
        },
        "seedContractDigest": seed_contract_digest,
    }
    manifest_digest = sha256_canonical_json(payload)
    payload["manifestDigest"] = manifest_digest
    return payload, manifest_digest, seed_contract_digest


def _resolve_seed_artifact_path(relative_path: str) -> Path:
    """Resolve a manifest relative path strictly under SYSTEM_SEED_DIR."""
    if relative_path.startswith("/") or ".." in relative_path.split("/"):
        raise SystemSeedInvalid(
            "artifact_outside_system_seed",
            f"artifact path escapes system_seed: {relative_path}",
        )
    candidate = (SYSTEM_SEED_DIR / relative_path).resolve()
    try:
        candidate.relative_to(SYSTEM_SEED_DIR.resolve())
    except ValueError as exc:
        raise SystemSeedInvalid(
            "artifact_outside_system_seed",
            f"artifact path escapes system_seed: {relative_path}",
        ) from exc
    if candidate.is_symlink():
        raise SystemSeedInvalid("seed_symlink_forbidden")
    return candidate


def verify_every_artifact(manifest: AssistantSystemSeedManifest) -> None:
    expected = [manifest.profile_artifact, *manifest.skill_artifacts]
    seen: set[str] = set()
    for artifact in expected:
        if artifact.relative_path in seen:
            raise SystemSeedInvalid("duplicate_artifact_path")
        seen.add(artifact.relative_path)
        path = _resolve_seed_artifact_path(artifact.relative_path)
        if not path.is_file():
            raise SystemSeedInvalid(
                "artifact_missing",
                f"missing seed artifact: {artifact.relative_path}",
            )
        content = path.read_bytes()
        actual_sha = sha256_bytes(content)
        if actual_sha != artifact.sha256:
            raise SystemSeedInvalid(
                "artifact_digest_mismatch",
                f"digest mismatch for {artifact.relative_path}",
            )
        if len(content) != artifact.byte_size:
            raise SystemSeedInvalid(
                "artifact_size_mismatch",
                f"size mismatch for {artifact.relative_path}",
            )
        actual_media = detect_media_type(artifact.relative_path, content)
        if actual_media != artifact.media_type:
            raise SystemSeedInvalid(
                "artifact_media_type_mismatch",
                f"media type mismatch for {artifact.relative_path}",
            )


@lru_cache(maxsize=1)
def load_verified_assistant_system_seed() -> VerifiedAssistantSystemSeed:
    """Load and verify the build-owned embedded system seed.

    Accepts no path, URL, environment override, request payload, or fallback.
    """
    if not MANIFEST_PATH.is_file():
        raise SystemSeedInvalid("manifest_missing", f"missing manifest: {MANIFEST_PATH}")
    if MANIFEST_PATH.is_symlink():
        raise SystemSeedInvalid("seed_symlink_forbidden")

    manifest_payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    if not isinstance(manifest_payload, dict):
        raise SystemSeedInvalid("manifest_invalid", "manifest must be a JSON object")

    try:
        manifest = AssistantSystemSeedManifest.model_validate(manifest_payload)
    except Exception as exc:  # pydantic ValidationError
        raise SystemSeedInvalid("manifest_invalid", str(exc)) from exc

    without_self = dict(manifest_payload)
    claimed_manifest_digest = without_self.pop("manifestDigest", None)
    if not isinstance(claimed_manifest_digest, str):
        raise SystemSeedInvalid("manifest_digest_missing")
    if sha256_canonical_json(without_self) != claimed_manifest_digest:
        raise SystemSeedInvalid("manifest_digest_mismatch")
    if claimed_manifest_digest != SEED_MANIFEST_DIGEST:
        raise SystemSeedInvalid("build_manifest_digest_mismatch")
    if manifest.manifest_digest != claimed_manifest_digest:
        raise SystemSeedInvalid("manifest_digest_mismatch")

    verify_every_artifact(manifest)

    if not PROFILE_PATH.is_file() or PROFILE_PATH.is_symlink():
        raise SystemSeedInvalid("profile_missing")
    profile = MainAgentProfileSnapshotV2.model_validate_json(PROFILE_PATH.read_bytes())
    if profile.control_capability_keys != (
        "skill.search",
        "skill.inject",
        "skill.read_resource",
        "artifact.read",
    ):
        raise SystemSeedInvalid("profile_control_keys_mismatch")

    parsed_skill = parse_skill_directory_files(
        read_seed_skill_files(SKILL_DIRECTORY),
        expected_root_name=None,
    )
    if parsed_skill.canonical_name != "mindatlas-universal":
        raise SystemSeedInvalid("skill_name_mismatch")
    if parsed_skill.manifest is None:
        raise SystemSeedInvalid("skill_manifest_missing")
    skill_keys = {cap.key for cap in parsed_skill.manifest.capabilities}
    if skill_keys & FORBIDDEN_SEED_WRITE_KEYS:
        raise SystemSeedInvalid("forbidden_capability_binding")

    # Recompute live system-tool digests and compare to committed bindings.
    live_bindings = resolve_system_tool_contract_digests(SEED_CAPABILITY_KEYS)
    committed = binding_contracts_as_payload(manifest.capability_bindings)
    if live_bindings != committed:
        raise SystemSeedInvalid("capability_binding_drift")

    build = manifest.build_compatibility
    if build.runtime_contract_version != RUNTIME_CONTRACT_VERSION:
        raise SystemSeedInvalid("runtime_contract_version_mismatch")
    if build.checkpoint_codec_version != CURRENT_CHECKPOINT_CODEC_VERSION:
        raise SystemSeedInvalid("checkpoint_codec_version_mismatch")
    if build.capability_feature_digest != default_capability_feature_digest():
        raise SystemSeedInvalid("capability_feature_digest_mismatch")

    computed_contract = compute_seed_contract_digest(
        profile=profile,
        parsed_skill=parsed_skill,
        capability_bindings=manifest.capability_bindings,
        runtime_contract_version=build.runtime_contract_version,
        checkpoint_codec_version=build.checkpoint_codec_version,
        capability_feature_digest=build.capability_feature_digest,
    )
    if computed_contract != manifest.seed_contract_digest:
        raise SystemSeedInvalid("seed_contract_digest_mismatch")
    if computed_contract != SEED_CONTRACT_DIGEST:
        raise SystemSeedInvalid("build_seed_contract_digest_mismatch")

    return VerifiedAssistantSystemSeed(
        manifest=manifest,
        profile=profile,
        parsed_skill=parsed_skill,
        capability_bindings=manifest.capability_bindings,
    )


def clear_verified_assistant_system_seed_cache() -> None:
    """Test helper: drop the process-local verified-seed cache."""
    load_verified_assistant_system_seed.cache_clear()


__all__ = [
    "FORBIDDEN_SEED_WRITE_KEYS",
    "MANIFEST_PATH",
    "PROFILE_PATH",
    "SEED_CAPABILITY_KEYS",
    "SEED_CONTRACT_DIGEST",
    "SEED_MANIFEST_DIGEST",
    "SKILL_DIRECTORY",
    "SYSTEM_SEED_DIR",
    "AssistantSystemSeedManifest",
    "SeedArtifact",
    "SeedBuildCompatibility",
    "SeedCapabilityBinding",
    "SystemSeedInvalid",
    "VerifiedAssistantSystemSeed",
    "artifact_for",
    "build_seed_payload",
    "canonical_seed_artifacts",
    "clear_verified_assistant_system_seed_cache",
    "compute_seed_contract_digest",
    "load_verified_assistant_system_seed",
    "read_seed_skill_files",
    "resolve_system_tool_contract_digests",
    "system_tool_seed_contract_digest",
    "verify_every_artifact",
]
