"""API-safe schemas for Agent Skill package aggregates and version history."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.assistant.skills.contracts import ParsedSkillPackage
from app.common.schemas import CamelModel


SkillPackageMigrationState = Literal["shadow", "native", "cutover"]
VersionSource = Literal["save", "publish"]
AliasType = Literal["canonical", "legacy", "custom"]
ResourceKind = Literal["scripts", "references", "assets", "other"]


class SkillResourceInput(CamelModel):
    """JSON API resource entry: path + base64 content only."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=None,
        extra="forbid",
    )

    path: str
    content_base64: str = Field(alias="contentBase64")


class SkillPackageJsonCreateRequest(CamelModel):
    """Router-facing create body. Media types/IDs/digests are forbidden."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    skill_md: str = Field(alias="skillMd")
    mindatlas_yaml: str | None = Field(default=None, alias="mindatlasYaml")
    resources: list[SkillResourceInput] = Field(default_factory=list)
    version_name: str | None = Field(default=None, alias="versionName")


class SkillPackageJsonSaveRequest(CamelModel):
    """Router-facing draft save body. Media types/IDs/digests are forbidden."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    skill_md: str = Field(alias="skillMd")
    mindatlas_yaml: str | None = Field(default=None, alias="mindatlasYaml")
    resources: list[SkillResourceInput] = Field(default_factory=list)
    version_name: str | None = Field(default=None, alias="versionName")


class CreateSkillPackageCommand(CamelModel):
    """Service command after router-side parse of package files."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    parsed: ParsedSkillPackage
    version_name: str | None = None
    origin: Literal["api", "import"] = "api"


class SaveSkillDraftCommand(CamelModel):
    """Service command to append or re-point a draft version."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    package_id: UUID
    parsed: ParsedSkillPackage
    version_name: str | None = None
    origin: Literal["api", "import", "legacy"] = "api"


class PublishSkillVersionCommand(CamelModel):
    """Publish requires an explicit draft version id; never resolves latest."""

    model_config = ConfigDict(extra="forbid")

    draft_version_id: UUID


class PublishMainAgentProfileCommand(CamelModel):
    """Publish requires an explicit draft version id; never resolves latest."""

    model_config = ConfigDict(extra="forbid")

    draft_version_id: UUID


class SkillResourceMetadata(CamelModel):
    """Resource metadata without bytes."""

    path: str
    resource_kind: ResourceKind
    media_type: str
    byte_size: int
    sha256: str
    executable: bool = False


class SkillVersionSummary(CamelModel):
    id: UUID
    skill_package_id: UUID
    sequence_no: int
    version_name: str
    version_source: VersionSource
    origin: str
    content_digest: str
    skill_md_digest: str
    manifest_digest: str
    resource_index_digest: str
    binding_set_digest: str | None = None
    version_digest: str | None = None
    source_draft_version_id: UUID | None = None
    created_at: datetime | None = None


class SkillVersionDetail(SkillVersionSummary):
    frontmatter: dict[str, Any]
    extension_manifest: dict[str, Any] | None = None
    resource_index: list[dict[str, Any]]
    resources: list[SkillResourceMetadata] = Field(default_factory=list)
    # skill_md / mindatlas_yaml text are available on detail for authoring UIs;
    # resource *bytes* are never included.
    skill_md: str
    mindatlas_yaml: str | None = None


class SkillPackageAliasSummary(CamelModel):
    id: UUID
    alias: str
    normalized_alias: str
    alias_type: AliasType
    created_at: datetime | None = None


class SkillPackageSummary(CamelModel):
    id: UUID
    canonical_name: str
    display_name: str
    description: str
    migration_state: SkillPackageMigrationState
    catalog_enabled: bool
    is_system: bool
    draft_version: SkillVersionSummary | None = None
    published_version: SkillVersionSummary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillPackageDetail(SkillPackageSummary):
    aliases: list[SkillPackageAliasSummary] = Field(default_factory=list)
    legacy_skill_id: UUID | None = None
    legacy_source_digest: str | None = None


__all__ = [
    "AliasType",
    "CreateSkillPackageCommand",
    "PublishMainAgentProfileCommand",
    "PublishSkillVersionCommand",
    "ResourceKind",
    "SaveSkillDraftCommand",
    "SkillPackageAliasSummary",
    "SkillPackageDetail",
    "SkillPackageJsonCreateRequest",
    "SkillPackageJsonSaveRequest",
    "SkillPackageMigrationState",
    "SkillPackageSummary",
    "SkillResourceInput",
    "SkillResourceMetadata",
    "SkillVersionDetail",
    "SkillVersionSummary",
    "VersionSource",
]
