"""Append-only Agent Skill package and Main Agent Profile persistence (Plan 01).

Immutable child rows define ``created_at`` only. Aggregate roots use TimestampMixin.
No ORM cascade may delete immutable history.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


# Portable length checks for ORM/SQLite create_all. Full lowercase-hex regex
# is enforced by the PostgreSQL migration (Task 10 gate).
def _sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(f"length({column}) = 64", name=name)


def _nullable_sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR length({column}) = 64",
        name=name,
    )


class AssistantSkillPackage(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable Skill package aggregate root (pointers + catalog metadata only)."""

    __tablename__ = "assistant_skill_package"

    canonical_name = Column(String(64), nullable=False, unique=True, index=True)
    display_name = Column(String(128), nullable=False)
    description = Column(String(1024), nullable=False, default="")
    draft_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_version.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_assistant_skill_package_draft_version_id",
        ),
        nullable=True,
        index=True,
    )
    published_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_version.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_assistant_skill_package_published_version_id",
        ),
        nullable=True,
        index=True,
    )
    legacy_skill_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    migration_state = Column(String(32), nullable=False)
    legacy_source_digest = Column(String(64), nullable=True)
    catalog_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_system = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    draft_version = relationship(
        "AssistantSkillVersion",
        foreign_keys=[draft_version_id],
        uselist=False,
        post_update=True,
    )
    published_version = relationship(
        "AssistantSkillVersion",
        foreign_keys=[published_version_id],
        uselist=False,
        post_update=True,
    )
    aliases = relationship(
        "AssistantSkillPackageAlias",
        back_populates="skill_package",
        foreign_keys="AssistantSkillPackageAlias.skill_package_id",
        # No delete-orphan: aliases are append-only immutable history.
    )
    versions = relationship(
        "AssistantSkillVersion",
        back_populates="skill_package",
        foreign_keys="AssistantSkillVersion.skill_package_id",
        # No cascade delete: version history is append-only.
    )

    __table_args__ = (
        CheckConstraint(
            "migration_state IN ('shadow','native','cutover')",
            name="ck_assistant_skill_package_migration_state",
        ),
        CheckConstraint(
            "catalog_enabled = false",
            name="ck_assistant_skill_package_catalog_disabled",
        ),
        _nullable_sha256_check(
            "legacy_source_digest",
            name="ck_assistant_skill_package_legacy_source_digest",
        ),
    )


class AssistantSkillPackageAlias(UuidPrimaryKeyMixin, Base):
    """Immutable alias row in the shared canonical/legacy/custom namespace."""

    __tablename__ = "assistant_skill_package_alias"

    skill_package_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_package.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    alias = Column(String(512), nullable=False)
    normalized_alias = Column(String(512), nullable=False, unique=True, index=True)
    alias_type = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    skill_package = relationship(
        "AssistantSkillPackage",
        back_populates="aliases",
        foreign_keys=[skill_package_id],
    )

    __table_args__ = (
        CheckConstraint(
            "alias_type IN ('canonical','legacy','custom')",
            name="ck_assistant_skill_package_alias_type",
        ),
    )


class AssistantSkillVersion(UuidPrimaryKeyMixin, Base):
    """Immutable Skill package version (draft save or publish)."""

    __tablename__ = "assistant_skill_version"

    skill_package_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_package.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False)
    version_name = Column(String(255), nullable=False)
    version_source = Column(String(32), nullable=False)
    source_draft_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_version.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    origin = Column(String(32), nullable=False)
    skill_md = Column(Text, nullable=False)
    mindatlas_yaml = Column(Text, nullable=True)
    frontmatter = Column(JSON, nullable=False)
    extension_manifest = Column(JSON, nullable=True)
    resource_index = Column(JSON, nullable=False)
    skill_md_digest = Column(String(64), nullable=False)
    manifest_digest = Column(String(64), nullable=False)
    resource_index_digest = Column(String(64), nullable=False)
    content_digest = Column(String(64), nullable=False)
    binding_set_digest = Column(String(64), nullable=True)
    version_digest = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    skill_package = relationship(
        "AssistantSkillPackage",
        back_populates="versions",
        foreign_keys=[skill_package_id],
    )
    source_draft_version = relationship(
        "AssistantSkillVersion",
        remote_side="AssistantSkillVersion.id",
        foreign_keys=[source_draft_version_id],
        uselist=False,
    )
    resources = relationship(
        "AssistantSkillVersionResource",
        back_populates="skill_version",
        foreign_keys="AssistantSkillVersionResource.skill_version_id",
    )
    bindings = relationship(
        "AssistantSkillCapabilityBinding",
        back_populates="skill_version",
        foreign_keys="AssistantSkillCapabilityBinding.skill_version_id",
    )

    __table_args__ = (
        CheckConstraint("sequence_no > 0", name="ck_assistant_skill_version_sequence_positive"),
        CheckConstraint(
            "version_source IN ('save','publish')",
            name="ck_assistant_skill_version_source",
        ),
        CheckConstraint(
            "origin IN ('api','import','legacy')",
            name="ck_assistant_skill_version_origin",
        ),
        CheckConstraint(
            "(version_source = 'save' AND source_draft_version_id IS NULL "
            "AND binding_set_digest IS NULL AND version_digest IS NULL) OR "
            "(version_source = 'publish' AND source_draft_version_id IS NOT NULL "
            "AND binding_set_digest IS NOT NULL AND version_digest IS NOT NULL)",
            name="ck_assistant_skill_version_source_shape",
        ),
        _sha256_check("skill_md_digest", name="ck_assistant_skill_version_skill_md_digest"),
        _sha256_check("manifest_digest", name="ck_assistant_skill_version_manifest_digest"),
        _sha256_check(
            "resource_index_digest",
            name="ck_assistant_skill_version_resource_index_digest",
        ),
        _sha256_check("content_digest", name="ck_assistant_skill_version_content_digest"),
        _nullable_sha256_check(
            "binding_set_digest",
            name="ck_assistant_skill_version_binding_set_digest",
        ),
        _nullable_sha256_check(
            "version_digest",
            name="ck_assistant_skill_version_version_digest",
        ),
        Index(
            "uq_assistant_skill_version_seq",
            "skill_package_id",
            "sequence_no",
            unique=True,
        ),
        Index(
            "uq_assistant_skill_version_draft_content",
            "skill_package_id",
            "content_digest",
            unique=True,
            postgresql_where=text("version_source = 'save'"),
            sqlite_where=text("version_source = 'save'"),
        ),
        Index(
            "ix_assistant_skill_version_package_created",
            "skill_package_id",
            "created_at",
        ),
    )


class AssistantSkillResourceBlob(UuidPrimaryKeyMixin, Base):
    """Content-addressed immutable resource bytes (shared across drafts/packages)."""

    __tablename__ = "assistant_skill_resource_blob"

    sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False)
    content = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    references = relationship(
        "AssistantSkillVersionResource",
        back_populates="blob",
        foreign_keys="AssistantSkillVersionResource.blob_id",
    )

    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_assistant_skill_resource_blob_size_positive"),
        _sha256_check("sha256", name="ck_assistant_skill_resource_blob_sha256"),
        Index(
            "uq_assistant_skill_resource_blob_sha_size",
            "sha256",
            "byte_size",
            unique=True,
        ),
    )


class AssistantSkillVersionResource(UuidPrimaryKeyMixin, Base):
    """Immutable per-version resource metadata pointing at a content-addressed blob."""

    __tablename__ = "assistant_skill_version_resource"

    skill_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_version.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    path = Column(String(1024), nullable=False)
    resource_kind = Column(String(32), nullable=False)
    media_type = Column(String(255), nullable=False)
    byte_size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    blob_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_resource_blob.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    executable = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    skill_version = relationship(
        "AssistantSkillVersion",
        back_populates="resources",
        foreign_keys=[skill_version_id],
    )
    blob = relationship(
        "AssistantSkillResourceBlob",
        back_populates="references",
        foreign_keys=[blob_id],
    )

    __table_args__ = (
        CheckConstraint(
            "resource_kind IN ('scripts','references','assets','other')",
            name="ck_assistant_skill_version_resource_kind",
        ),
        CheckConstraint(
            "byte_size > 0",
            name="ck_assistant_skill_version_resource_size_positive",
        ),
        CheckConstraint(
            "executable = false",
            name="ck_assistant_skill_version_resource_not_executable",
        ),
        _sha256_check("sha256", name="ck_assistant_skill_version_resource_sha256"),
        Index(
            "uq_assistant_skill_version_resource_path",
            "skill_version_id",
            "path",
            unique=True,
        ),
    )


class AssistantSkillCapabilityBinding(UuidPrimaryKeyMixin, Base):
    """Immutable capability binding row owned by a Skill version."""

    __tablename__ = "assistant_skill_capability_binding"

    skill_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_version.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    capability_type = Column(String(32), nullable=False)
    capability_key = Column(String(255), nullable=False)
    resolution_status = Column(String(32), nullable=False)
    target_identity = Column(String(512), nullable=True)
    resolved_tool_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_tool.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolved_workflow_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow_version.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolved_agent_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile_version.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolved_revision = Column(Integer, nullable=True)
    input_schema_digest = Column(String(64), nullable=True)
    output_schema_digest = Column(String(64), nullable=True)
    config_digest = Column(String(64), nullable=True)
    executable_revision = Column(String(255), nullable=True)
    resolution_digest = Column(String(64), nullable=True)
    dependency_closure_digest = Column(String(64), nullable=True)
    binding_contract_digest = Column(String(64), nullable=True)
    resolution_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    skill_version = relationship(
        "AssistantSkillVersion",
        back_populates="bindings",
        foreign_keys=[skill_version_id],
    )
    dependencies = relationship(
        "AssistantSkillCapabilityDependency",
        back_populates="binding",
        foreign_keys="AssistantSkillCapabilityDependency.binding_id",
    )

    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_assistant_skill_capability_binding_ordinal"),
        CheckConstraint(
            "capability_type IN ('tool','workflow','agent')",
            name="ck_assistant_skill_capability_binding_type",
        ),
        CheckConstraint(
            "resolution_status IN ('unresolved','resolved')",
            name="ck_assistant_skill_capability_binding_status",
        ),
        CheckConstraint(
            "resolved_revision IS NULL OR resolved_revision > 0",
            name="ck_assistant_skill_capability_binding_revision_positive",
        ),
        # Unresolved drafts: no typed FKs / complete digest set / snapshot.
        # Resolved: target_identity + full digest set + snapshot; one target shape.
        CheckConstraint(
            "("
            "  resolution_status = 'unresolved'"
            "  AND target_identity IS NULL"
            "  AND resolved_tool_id IS NULL"
            "  AND resolved_workflow_version_id IS NULL"
            "  AND resolved_agent_version_id IS NULL"
            "  AND resolved_revision IS NULL"
            "  AND input_schema_digest IS NULL"
            "  AND output_schema_digest IS NULL"
            "  AND config_digest IS NULL"
            "  AND executable_revision IS NULL"
            "  AND resolution_digest IS NULL"
            "  AND dependency_closure_digest IS NULL"
            "  AND binding_contract_digest IS NULL"
            "  AND resolution_snapshot IS NULL"
            ") OR ("
            "  resolution_status = 'resolved'"
            "  AND target_identity IS NOT NULL"
            "  AND input_schema_digest IS NOT NULL"
            "  AND output_schema_digest IS NOT NULL"
            "  AND resolution_digest IS NOT NULL"
            "  AND dependency_closure_digest IS NOT NULL"
            "  AND binding_contract_digest IS NOT NULL"
            "  AND resolution_snapshot IS NOT NULL"
            "  AND ("
            # code-native system tool
            "    ("
            "      capability_type = 'tool'"
            "      AND resolved_tool_id IS NULL"
            "      AND resolved_workflow_version_id IS NULL"
            "      AND resolved_agent_version_id IS NULL"
            "      AND resolved_revision IS NULL"
            "      AND executable_revision IS NOT NULL"
            "      AND target_identity LIKE 'system-tool:%'"
            "    ) OR ("
            # remote tool
            "      capability_type = 'tool'"
            "      AND resolved_tool_id IS NOT NULL"
            "      AND resolved_workflow_version_id IS NULL"
            "      AND resolved_agent_version_id IS NULL"
            "      AND resolved_revision IS NOT NULL"
            "    ) OR ("
            # workflow
            "      capability_type = 'workflow'"
            "      AND resolved_tool_id IS NULL"
            "      AND resolved_workflow_version_id IS NOT NULL"
            "      AND resolved_agent_version_id IS NULL"
            "      AND resolved_revision IS NULL"
            "    ) OR ("
            # agent
            "      capability_type = 'agent'"
            "      AND resolved_tool_id IS NULL"
            "      AND resolved_workflow_version_id IS NULL"
            "      AND resolved_agent_version_id IS NOT NULL"
            "      AND resolved_revision IS NULL"
            "    )"
            "  )"
            ")",
            name="ck_assistant_skill_capability_binding_resolution_shape",
        ),
        _nullable_sha256_check(
            "input_schema_digest",
            name="ck_assistant_skill_capability_binding_input_digest",
        ),
        _nullable_sha256_check(
            "output_schema_digest",
            name="ck_assistant_skill_capability_binding_output_digest",
        ),
        _nullable_sha256_check(
            "config_digest",
            name="ck_assistant_skill_capability_binding_config_digest",
        ),
        _nullable_sha256_check(
            "resolution_digest",
            name="ck_assistant_skill_capability_binding_resolution_digest",
        ),
        _nullable_sha256_check(
            "dependency_closure_digest",
            name="ck_assistant_skill_capability_binding_closure_digest",
        ),
        _nullable_sha256_check(
            "binding_contract_digest",
            name="ck_assistant_skill_capability_binding_contract_digest",
        ),
        Index(
            "uq_assistant_skill_capability_binding_key",
            "skill_version_id",
            "capability_type",
            "capability_key",
            unique=True,
        ),
        Index(
            "ix_assistant_skill_capability_binding_version_ordinal",
            "skill_version_id",
            "ordinal",
        ),
    )


class AssistantSkillCapabilityDependency(UuidPrimaryKeyMixin, Base):
    """Immutable dependency-closure row owned by a capability binding."""

    __tablename__ = "assistant_skill_capability_dependency"

    binding_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_capability_binding.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    dependency_path = Column(String(512), nullable=False)
    dependency_type = Column(String(32), nullable=False)
    target_identity = Column(String(512), nullable=False)
    resolved_tool_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_tool.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolved_workflow_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow_version.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolved_agent_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile_version.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolved_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_model.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    target_revision = Column(Integer, nullable=True)
    input_schema_digest = Column(String(64), nullable=True)
    output_schema_digest = Column(String(64), nullable=True)
    resolution_digest = Column(String(64), nullable=False)
    dependency_digest = Column(String(64), nullable=False)
    resolution_snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    binding = relationship(
        "AssistantSkillCapabilityBinding",
        back_populates="dependencies",
        foreign_keys=[binding_id],
    )

    __table_args__ = (
        CheckConstraint(
            "ordinal >= 0",
            name="ck_assistant_skill_capability_dependency_ordinal",
        ),
        CheckConstraint(
            "dependency_type IN ('system_tool','remote_tool','workflow','agent','model')",
            name="ck_assistant_skill_capability_dependency_type",
        ),
        CheckConstraint(
            "target_revision IS NULL OR target_revision > 0",
            name="ck_assistant_skill_capability_dependency_revision_positive",
        ),
        CheckConstraint(
            "("
            "  dependency_type = 'system_tool'"
            "  AND resolved_tool_id IS NULL"
            "  AND resolved_workflow_version_id IS NULL"
            "  AND resolved_agent_version_id IS NULL"
            "  AND resolved_model_id IS NULL"
            ") OR ("
            "  dependency_type = 'remote_tool'"
            "  AND resolved_tool_id IS NOT NULL"
            "  AND resolved_workflow_version_id IS NULL"
            "  AND resolved_agent_version_id IS NULL"
            "  AND resolved_model_id IS NULL"
            "  AND target_revision IS NOT NULL"
            ") OR ("
            "  dependency_type = 'workflow'"
            "  AND resolved_tool_id IS NULL"
            "  AND resolved_workflow_version_id IS NOT NULL"
            "  AND resolved_agent_version_id IS NULL"
            "  AND resolved_model_id IS NULL"
            ") OR ("
            "  dependency_type = 'agent'"
            "  AND resolved_tool_id IS NULL"
            "  AND resolved_workflow_version_id IS NULL"
            "  AND resolved_agent_version_id IS NOT NULL"
            "  AND resolved_model_id IS NULL"
            ") OR ("
            "  dependency_type = 'model'"
            "  AND resolved_tool_id IS NULL"
            "  AND resolved_workflow_version_id IS NULL"
            "  AND resolved_agent_version_id IS NULL"
            "  AND resolved_model_id IS NOT NULL"
            "  AND target_revision IS NOT NULL"
            ")",
            name="ck_assistant_skill_capability_dependency_fk_shape",
        ),
        _nullable_sha256_check(
            "input_schema_digest",
            name="ck_assistant_skill_capability_dependency_input_digest",
        ),
        _nullable_sha256_check(
            "output_schema_digest",
            name="ck_assistant_skill_capability_dependency_output_digest",
        ),
        _sha256_check(
            "resolution_digest",
            name="ck_assistant_skill_capability_dependency_resolution_digest",
        ),
        _sha256_check(
            "dependency_digest",
            name="ck_assistant_skill_capability_dependency_digest",
        ),
        Index(
            "uq_assistant_skill_capability_dependency_ordinal",
            "binding_id",
            "ordinal",
            unique=True,
        ),
        Index(
            "uq_assistant_skill_capability_dependency_path",
            "binding_id",
            "dependency_path",
            unique=True,
        ),
    )


class AssistantMainAgentProfile(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable Main Agent Profile aggregate root."""

    __tablename__ = "assistant_main_agent_profile"

    profile_key = Column(String(64), nullable=False, unique=True, index=True)
    display_name = Column(String(128), nullable=False)
    is_default = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    draft_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_main_agent_profile_version.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_assistant_main_agent_profile_draft_version_id",
        ),
        nullable=True,
        index=True,
    )
    published_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_main_agent_profile_version.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_assistant_main_agent_profile_published_version_id",
        ),
        nullable=True,
        index=True,
    )
    migration_state = Column(String(32), nullable=False)
    legacy_skill_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    legacy_source_digest = Column(String(64), nullable=True)
    runtime_enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    draft_version = relationship(
        "AssistantMainAgentProfileVersion",
        foreign_keys=[draft_version_id],
        uselist=False,
        post_update=True,
    )
    published_version = relationship(
        "AssistantMainAgentProfileVersion",
        foreign_keys=[published_version_id],
        uselist=False,
        post_update=True,
    )
    versions = relationship(
        "AssistantMainAgentProfileVersion",
        back_populates="profile",
        foreign_keys="AssistantMainAgentProfileVersion.profile_id",
    )

    __table_args__ = (
        CheckConstraint(
            "migration_state IN ('bootstrap','shadow','native','cutover')",
            name="ck_assistant_main_agent_profile_migration_state",
        ),
        CheckConstraint(
            "runtime_enabled = false",
            name="ck_assistant_main_agent_profile_runtime_disabled",
        ),
        _nullable_sha256_check(
            "legacy_source_digest",
            name="ck_assistant_main_agent_profile_legacy_source_digest",
        ),
        Index(
            "uq_assistant_main_agent_profile_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default = true"),
            sqlite_where=text("is_default = 1"),
        ),
    )


class AssistantMainAgentProfileVersion(UuidPrimaryKeyMixin, Base):
    """Immutable Main Agent Profile version snapshot."""

    __tablename__ = "assistant_main_agent_profile_version"

    profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_profile.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False)
    version_name = Column(String(255), nullable=False)
    version_source = Column(String(32), nullable=False)
    origin = Column(String(32), nullable=False)
    source_draft_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_profile_version.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    snapshot = Column(JSON, nullable=False)
    content_digest = Column(String(64), nullable=False)
    source_ref = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    profile = relationship(
        "AssistantMainAgentProfile",
        back_populates="versions",
        foreign_keys=[profile_id],
    )
    source_draft_version = relationship(
        "AssistantMainAgentProfileVersion",
        remote_side="AssistantMainAgentProfileVersion.id",
        foreign_keys=[source_draft_version_id],
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "sequence_no > 0",
            name="ck_assistant_main_agent_profile_version_sequence_positive",
        ),
        CheckConstraint(
            "version_source IN ('save','publish')",
            name="ck_assistant_main_agent_profile_version_source",
        ),
        CheckConstraint(
            "origin IN ('bootstrap','api','legacy')",
            name="ck_assistant_main_agent_profile_version_origin",
        ),
        CheckConstraint(
            "(version_source = 'save' AND source_draft_version_id IS NULL) OR "
            "(version_source = 'publish' AND source_draft_version_id IS NOT NULL)",
            name="ck_assistant_main_agent_profile_version_source_shape",
        ),
        _sha256_check(
            "content_digest",
            name="ck_assistant_main_agent_profile_version_content_digest",
        ),
        Index(
            "uq_assistant_main_agent_profile_version_seq",
            "profile_id",
            "sequence_no",
            unique=True,
        ),
        Index(
            "uq_assistant_main_agent_profile_version_draft_content",
            "profile_id",
            "content_digest",
            unique=True,
            postgresql_where=text("version_source = 'save'"),
            sqlite_where=text("version_source = 'save'"),
        ),
        Index(
            "ix_assistant_main_agent_profile_version_created",
            "profile_id",
            "created_at",
        ),
    )
