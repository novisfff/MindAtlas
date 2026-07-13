"""add agent skill contract tables

Revision ID: acf208493c87
Revises: a7b8c9d0e1f2
Create Date: 2026-07-13 18:57:17.869810

Plan 01 append-only Skill package / Main Agent Profile persistence.
PostgreSQL-only triggers and deferred guards are verified in Task 10.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "acf208493c87"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


_SHA256 = r"^[0-9a-f]{64}$"
_DOWNGRADE_BLOCKED = (
    "MINDATLAS_PLAN01_DOWNGRADE_BLOCKED_NATIVE_DATA: export or back up native "
    "Skill/Main Agent history and remove it with an explicitly audited procedure "
    "before downgrade"
)

# Derived origins may be discarded on downgrade; administrator/non-derived origins block.
# Package versions: block api/import (legacy allowed).
# Profile versions: allow bootstrap/legacy; block administrator origin api.
PACKAGE_VERSION_DOWNGRADE_ALLOWED_ORIGINS = frozenset({"legacy"})
PACKAGE_VERSION_DOWNGRADE_BLOCKED_ORIGINS = frozenset({"api", "import"})
PROFILE_VERSION_DOWNGRADE_ALLOWED_ORIGINS = frozenset({"bootstrap", "legacy"})
PROFILE_VERSION_DOWNGRADE_BLOCKED_ORIGINS = frozenset({"api"})

# Resolved binding snapshots must carry these digests equal to row columns.
RESOLVED_BINDING_REQUIRED_SNAPSHOT_DIGEST_KEYS = (
    "resolutionDigest",
    "dependencyClosureDigest",
    "bindingContractDigest",
)


def package_version_origin_blocks_downgrade(origin: str) -> bool:
    """True when a skill package version origin is non-derived administrator data."""
    return origin in PACKAGE_VERSION_DOWNGRADE_BLOCKED_ORIGINS


def profile_version_origin_blocks_downgrade(origin: str) -> bool:
    """True when a Main Agent Profile version origin is non-derived administrator data.

    Pure derived history (bootstrap, legacy) may be discarded; block api.
    """
    return origin not in PROFILE_VERSION_DOWNGRADE_ALLOWED_ORIGINS


def resolved_snapshot_digest_mismatch(
    snapshot: dict | None,
    *,
    resolution_digest: str | None,
    dependency_closure_digest: str | None,
    binding_contract_digest: str | None,
    input_schema_digest: str | None,
    output_schema_digest: str | None,
) -> bool:
    """True when a resolved binding snapshot fails required digest equality.

    For resolved bindings, resolutionDigest / dependencyClosureDigest /
    bindingContractDigest keys must exist and equal row columns (not only when
    present). Input/output schema digests keep pairing semantics.
    """
    if snapshot is None:
        return True
    if snapshot.get("inputSchemaDigest") != input_schema_digest:
        return True
    if snapshot.get("outputSchemaDigest") != output_schema_digest:
        return True
    required = {
        "resolutionDigest": resolution_digest,
        "dependencyClosureDigest": dependency_closure_digest,
        "bindingContractDigest": binding_contract_digest,
    }
    for key, expected in required.items():
        if key not in snapshot:
            return True
        if snapshot.get(key) != expected:
            return True
    return False


_IMMUTABLE_TABLES = (
    "assistant_skill_package_alias",
    "assistant_skill_version",
    "assistant_skill_resource_blob",
    "assistant_skill_version_resource",
    "assistant_skill_capability_binding",
    "assistant_skill_capability_dependency",
    "assistant_main_agent_profile_version",
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Revision columns on existing mutable execution identities
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_tool",
        sa.Column(
            "config_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "ai_model",
        sa.Column(
            "runtime_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "ai_credential",
        sa.Column(
            "runtime_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_assistant_tool_config_revision_positive",
        "assistant_tool",
        "config_revision > 0",
    )
    op.create_check_constraint(
        "ck_ai_model_runtime_revision_positive",
        "ai_model",
        "runtime_revision > 0",
    )
    op.create_check_constraint(
        "ck_ai_credential_runtime_revision_positive",
        "ai_credential",
        "runtime_revision > 0",
    )

    # ------------------------------------------------------------------
    # Aggregate: assistant_skill_package (pointers added after versions)
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_package",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=False),
        sa.Column("draft_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("published_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("legacy_skill_id", UUID(as_uuid=True), nullable=True),
        sa.Column("migration_state", sa.String(length=32), nullable=False),
        sa.Column("legacy_source_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "catalog_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["legacy_skill_id"],
            ["assistant_skill.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
        sa.UniqueConstraint("legacy_skill_id"),
        sa.CheckConstraint(
            "migration_state IN ('shadow','native','cutover')",
            name="ck_assistant_skill_package_migration_state",
        ),
        sa.CheckConstraint(
            "catalog_enabled = false",
            name="ck_assistant_skill_package_catalog_disabled",
        ),
        sa.CheckConstraint(
            f"legacy_source_digest IS NULL OR legacy_source_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_package_legacy_source_digest",
        ),
    )
    op.create_index(
        "ix_assistant_skill_package_canonical_name",
        "assistant_skill_package",
        ["canonical_name"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_package_legacy_skill_id",
        "assistant_skill_package",
        ["legacy_skill_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Immutable alias namespace
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_package_alias",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("skill_package_id", UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(length=512), nullable=False),
        sa.Column("normalized_alias", sa.String(length=512), nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_package_id"],
            ["assistant_skill_package.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias"),
        sa.CheckConstraint(
            "alias_type IN ('canonical','legacy','custom')",
            name="ck_assistant_skill_package_alias_type",
        ),
    )
    op.create_index(
        "ix_assistant_skill_package_alias_skill_package_id",
        "assistant_skill_package_alias",
        ["skill_package_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_package_alias_normalized_alias",
        "assistant_skill_package_alias",
        ["normalized_alias"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Immutable skill versions
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_version",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("skill_package_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(length=255), nullable=False),
        sa.Column("version_source", sa.String(length=32), nullable=False),
        sa.Column("source_draft_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("skill_md", sa.Text(), nullable=False),
        sa.Column("mindatlas_yaml", sa.Text(), nullable=True),
        sa.Column("frontmatter", sa.JSON(), nullable=False),
        sa.Column("extension_manifest", sa.JSON(), nullable=True),
        sa.Column("resource_index", sa.JSON(), nullable=False),
        sa.Column("skill_md_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("resource_index_digest", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("binding_set_digest", sa.String(length=64), nullable=True),
        sa.Column("version_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_package_id"],
            ["assistant_skill_package.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_version_id"],
            ["assistant_skill_version.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "sequence_no > 0",
            name="ck_assistant_skill_version_sequence_positive",
        ),
        sa.CheckConstraint(
            "version_source IN ('save','publish')",
            name="ck_assistant_skill_version_source",
        ),
        sa.CheckConstraint(
            "origin IN ('api','import','legacy')",
            name="ck_assistant_skill_version_origin",
        ),
        sa.CheckConstraint(
            "(version_source = 'save' AND source_draft_version_id IS NULL "
            "AND binding_set_digest IS NULL AND version_digest IS NULL) OR "
            "(version_source = 'publish' AND source_draft_version_id IS NOT NULL "
            "AND binding_set_digest IS NOT NULL AND version_digest IS NOT NULL)",
            name="ck_assistant_skill_version_source_shape",
        ),
        sa.CheckConstraint(
            f"skill_md_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_version_skill_md_digest",
        ),
        sa.CheckConstraint(
            f"manifest_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_version_manifest_digest",
        ),
        sa.CheckConstraint(
            f"resource_index_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_version_resource_index_digest",
        ),
        sa.CheckConstraint(
            f"content_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_version_content_digest",
        ),
        sa.CheckConstraint(
            f"binding_set_digest IS NULL OR binding_set_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_version_binding_set_digest",
        ),
        sa.CheckConstraint(
            f"version_digest IS NULL OR version_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_version_version_digest",
        ),
    )
    op.create_index(
        "uq_assistant_skill_version_seq",
        "assistant_skill_version",
        ["skill_package_id", "sequence_no"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_skill_version_draft_content",
        "assistant_skill_version",
        ["skill_package_id", "content_digest"],
        unique=True,
        postgresql_where=sa.text("version_source = 'save'"),
    )
    op.create_index(
        "ix_assistant_skill_version_package_created",
        "assistant_skill_version",
        ["skill_package_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_version_skill_package_id",
        "assistant_skill_version",
        ["skill_package_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_version_source_draft_version_id",
        "assistant_skill_version",
        ["source_draft_version_id"],
        unique=False,
    )

    # Package draft/published pointers now that version table exists.
    op.create_foreign_key(
        "fk_assistant_skill_package_draft_version_id",
        "assistant_skill_package",
        "assistant_skill_version",
        ["draft_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_assistant_skill_package_published_version_id",
        "assistant_skill_package",
        "assistant_skill_version",
        ["published_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_assistant_skill_package_draft_version_id",
        "assistant_skill_package",
        ["draft_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_package_published_version_id",
        "assistant_skill_package",
        ["published_version_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Content-addressed blobs + version resource references
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_resource_blob",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_assistant_skill_resource_blob_size_positive",
        ),
        sa.CheckConstraint(
            f"sha256 ~ '{_SHA256}'",
            name="ck_assistant_skill_resource_blob_sha256",
        ),
    )
    op.create_index(
        "uq_assistant_skill_resource_blob_sha_size",
        "assistant_skill_resource_blob",
        ["sha256", "byte_size"],
        unique=True,
    )

    op.create_table(
        "assistant_skill_version_resource",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("skill_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("blob_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "executable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["assistant_skill_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"],
            ["assistant_skill_resource_blob.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "resource_kind IN ('scripts','references','assets','other')",
            name="ck_assistant_skill_version_resource_kind",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_assistant_skill_version_resource_size_positive",
        ),
        sa.CheckConstraint(
            "executable = false",
            name="ck_assistant_skill_version_resource_not_executable",
        ),
        sa.CheckConstraint(
            f"sha256 ~ '{_SHA256}'",
            name="ck_assistant_skill_version_resource_sha256",
        ),
    )
    op.create_index(
        "uq_assistant_skill_version_resource_path",
        "assistant_skill_version_resource",
        ["skill_version_id", "path"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_skill_version_resource_skill_version_id",
        "assistant_skill_version_resource",
        ["skill_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_version_resource_blob_id",
        "assistant_skill_version_resource",
        ["blob_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Capability bindings + dependency closure
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_capability_binding",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("skill_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("capability_type", sa.String(length=32), nullable=False),
        sa.Column("capability_key", sa.String(length=255), nullable=False),
        sa.Column("resolution_status", sa.String(length=32), nullable=False),
        sa.Column("target_identity", sa.String(length=512), nullable=True),
        sa.Column("resolved_tool_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_workflow_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_agent_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_revision", sa.Integer(), nullable=True),
        sa.Column("input_schema_digest", sa.String(length=64), nullable=True),
        sa.Column("output_schema_digest", sa.String(length=64), nullable=True),
        sa.Column("config_digest", sa.String(length=64), nullable=True),
        sa.Column("executable_revision", sa.String(length=255), nullable=True),
        sa.Column("resolution_digest", sa.String(length=64), nullable=True),
        sa.Column("dependency_closure_digest", sa.String(length=64), nullable=True),
        sa.Column("binding_contract_digest", sa.String(length=64), nullable=True),
        sa.Column("resolution_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["assistant_skill_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_tool_id"],
            ["assistant_tool.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_workflow_version_id"],
            ["assistant_workflow_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_agent_version_id"],
            ["assistant_agent_profile_version.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_assistant_skill_capability_binding_ordinal",
        ),
        sa.CheckConstraint(
            "capability_type IN ('tool','workflow','agent')",
            name="ck_assistant_skill_capability_binding_type",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('unresolved','resolved')",
            name="ck_assistant_skill_capability_binding_status",
        ),
        sa.CheckConstraint(
            "resolved_revision IS NULL OR resolved_revision > 0",
            name="ck_assistant_skill_capability_binding_revision_positive",
        ),
        sa.CheckConstraint(
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
            "    ("
            "      capability_type = 'tool'"
            "      AND resolved_tool_id IS NULL"
            "      AND resolved_workflow_version_id IS NULL"
            "      AND resolved_agent_version_id IS NULL"
            "      AND resolved_revision IS NULL"
            "      AND executable_revision IS NOT NULL"
            "      AND target_identity LIKE 'system-tool:%'"
            "    ) OR ("
            "      capability_type = 'tool'"
            "      AND resolved_tool_id IS NOT NULL"
            "      AND resolved_workflow_version_id IS NULL"
            "      AND resolved_agent_version_id IS NULL"
            "      AND resolved_revision IS NOT NULL"
            "    ) OR ("
            "      capability_type = 'workflow'"
            "      AND resolved_tool_id IS NULL"
            "      AND resolved_workflow_version_id IS NOT NULL"
            "      AND resolved_agent_version_id IS NULL"
            "      AND resolved_revision IS NULL"
            "    ) OR ("
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
        # Snapshot must pair output schema body + digest when resolved.
        sa.CheckConstraint(
            "resolution_status = 'unresolved' OR ("
            "  (resolution_snapshot::jsonb) ? 'outputSchema'"
            "  AND (resolution_snapshot::jsonb) ? 'outputSchemaDigest'"
            "  AND (resolution_snapshot::jsonb) ? 'inputSchema'"
            "  AND (resolution_snapshot::jsonb) ? 'inputSchemaDigest'"
            "  AND ((resolution_snapshot::jsonb)->>'outputSchemaDigest') = output_schema_digest"
            "  AND ((resolution_snapshot::jsonb)->>'inputSchemaDigest') = input_schema_digest"
            ")",
            name="ck_assistant_skill_capability_binding_snapshot_schema_pair",
        ),
        sa.CheckConstraint(
            f"input_schema_digest IS NULL OR input_schema_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_binding_input_digest",
        ),
        sa.CheckConstraint(
            f"output_schema_digest IS NULL OR output_schema_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_binding_output_digest",
        ),
        sa.CheckConstraint(
            f"config_digest IS NULL OR config_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_binding_config_digest",
        ),
        sa.CheckConstraint(
            f"resolution_digest IS NULL OR resolution_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_binding_resolution_digest",
        ),
        sa.CheckConstraint(
            f"dependency_closure_digest IS NULL OR dependency_closure_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_binding_closure_digest",
        ),
        sa.CheckConstraint(
            f"binding_contract_digest IS NULL OR binding_contract_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_binding_contract_digest",
        ),
    )
    op.create_index(
        "uq_assistant_skill_capability_binding_key",
        "assistant_skill_capability_binding",
        ["skill_version_id", "capability_type", "capability_key"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_skill_capability_binding_version_ordinal",
        "assistant_skill_capability_binding",
        ["skill_version_id", "ordinal"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_binding_skill_version_id",
        "assistant_skill_capability_binding",
        ["skill_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_binding_resolved_tool_id",
        "assistant_skill_capability_binding",
        ["resolved_tool_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_binding_resolved_workflow_version_id",
        "assistant_skill_capability_binding",
        ["resolved_workflow_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_binding_resolved_agent_version_id",
        "assistant_skill_capability_binding",
        ["resolved_agent_version_id"],
        unique=False,
    )

    op.create_table(
        "assistant_skill_capability_dependency",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("dependency_path", sa.String(length=512), nullable=False),
        sa.Column("dependency_type", sa.String(length=32), nullable=False),
        sa.Column("target_identity", sa.String(length=512), nullable=False),
        sa.Column("resolved_tool_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_workflow_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_agent_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_model_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_revision", sa.Integer(), nullable=True),
        sa.Column("input_schema_digest", sa.String(length=64), nullable=True),
        sa.Column("output_schema_digest", sa.String(length=64), nullable=True),
        sa.Column("resolution_digest", sa.String(length=64), nullable=False),
        sa.Column("dependency_digest", sa.String(length=64), nullable=False),
        sa.Column("resolution_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["assistant_skill_capability_binding.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_tool_id"],
            ["assistant_tool.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_workflow_version_id"],
            ["assistant_workflow_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_agent_version_id"],
            ["assistant_agent_profile_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_model_id"],
            ["ai_model.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_assistant_skill_capability_dependency_ordinal",
        ),
        sa.CheckConstraint(
            "dependency_type IN ('system_tool','remote_tool','workflow','agent','model')",
            name="ck_assistant_skill_capability_dependency_type",
        ),
        sa.CheckConstraint(
            "target_revision IS NULL OR target_revision > 0",
            name="ck_assistant_skill_capability_dependency_revision_positive",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            f"input_schema_digest IS NULL OR input_schema_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_dependency_input_digest",
        ),
        sa.CheckConstraint(
            f"output_schema_digest IS NULL OR output_schema_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_dependency_output_digest",
        ),
        sa.CheckConstraint(
            f"resolution_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_dependency_resolution_digest",
        ),
        sa.CheckConstraint(
            f"dependency_digest ~ '{_SHA256}'",
            name="ck_assistant_skill_capability_dependency_digest",
        ),
    )
    op.create_index(
        "uq_assistant_skill_capability_dependency_ordinal",
        "assistant_skill_capability_dependency",
        ["binding_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_skill_capability_dependency_path",
        "assistant_skill_capability_dependency",
        ["binding_id", "dependency_path"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_skill_capability_dependency_binding_id",
        "assistant_skill_capability_dependency",
        ["binding_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_dependency_resolved_tool_id",
        "assistant_skill_capability_dependency",
        ["resolved_tool_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_dependency_resolved_workflow_version_id",
        "assistant_skill_capability_dependency",
        ["resolved_workflow_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_dependency_resolved_agent_version_id",
        "assistant_skill_capability_dependency",
        ["resolved_agent_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_skill_capability_dependency_resolved_model_id",
        "assistant_skill_capability_dependency",
        ["resolved_model_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Main Agent Profile aggregate + versions
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_main_agent_profile",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("draft_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("published_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("migration_state", sa.String(length=32), nullable=False),
        sa.Column("legacy_skill_id", UUID(as_uuid=True), nullable=True),
        sa.Column("legacy_source_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "runtime_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["legacy_skill_id"],
            ["assistant_skill.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_key"),
        sa.UniqueConstraint("legacy_skill_id"),
        sa.CheckConstraint(
            "migration_state IN ('bootstrap','shadow','native','cutover')",
            name="ck_assistant_main_agent_profile_migration_state",
        ),
        sa.CheckConstraint(
            "runtime_enabled = false",
            name="ck_assistant_main_agent_profile_runtime_disabled",
        ),
        sa.CheckConstraint(
            f"legacy_source_digest IS NULL OR legacy_source_digest ~ '{_SHA256}'",
            name="ck_assistant_main_agent_profile_legacy_source_digest",
        ),
    )
    op.create_index(
        "ix_assistant_main_agent_profile_profile_key",
        "assistant_main_agent_profile",
        ["profile_key"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_main_agent_profile_legacy_skill_id",
        "assistant_main_agent_profile",
        ["legacy_skill_id"],
        unique=False,
    )
    op.create_index(
        "uq_assistant_main_agent_profile_default",
        "assistant_main_agent_profile",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )

    op.create_table(
        "assistant_main_agent_profile_version",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(length=255), nullable=False),
        sa.Column("version_source", sa.String(length=32), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("source_draft_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["assistant_main_agent_profile.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_version_id"],
            ["assistant_main_agent_profile_version.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "sequence_no > 0",
            name="ck_assistant_main_agent_profile_version_sequence_positive",
        ),
        sa.CheckConstraint(
            "version_source IN ('save','publish')",
            name="ck_assistant_main_agent_profile_version_source",
        ),
        sa.CheckConstraint(
            "origin IN ('bootstrap','api','legacy')",
            name="ck_assistant_main_agent_profile_version_origin",
        ),
        sa.CheckConstraint(
            "(version_source = 'save' AND source_draft_version_id IS NULL) OR "
            "(version_source = 'publish' AND source_draft_version_id IS NOT NULL)",
            name="ck_assistant_main_agent_profile_version_source_shape",
        ),
        sa.CheckConstraint(
            f"content_digest ~ '{_SHA256}'",
            name="ck_assistant_main_agent_profile_version_content_digest",
        ),
    )
    op.create_index(
        "uq_assistant_main_agent_profile_version_seq",
        "assistant_main_agent_profile_version",
        ["profile_id", "sequence_no"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_main_agent_profile_version_draft_content",
        "assistant_main_agent_profile_version",
        ["profile_id", "content_digest"],
        unique=True,
        postgresql_where=sa.text("version_source = 'save'"),
    )
    op.create_index(
        "ix_assistant_main_agent_profile_version_created",
        "assistant_main_agent_profile_version",
        ["profile_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_main_agent_profile_version_profile_id",
        "assistant_main_agent_profile_version",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_main_agent_profile_version_source_draft_version_id",
        "assistant_main_agent_profile_version",
        ["source_draft_version_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_assistant_main_agent_profile_draft_version_id",
        "assistant_main_agent_profile",
        "assistant_main_agent_profile_version",
        ["draft_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_assistant_main_agent_profile_published_version_id",
        "assistant_main_agent_profile",
        "assistant_main_agent_profile_version",
        ["published_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_assistant_main_agent_profile_draft_version_id",
        "assistant_main_agent_profile",
        ["draft_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_main_agent_profile_published_version_id",
        "assistant_main_agent_profile",
        ["published_version_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # PostgreSQL immutability + ownership + revision guards
    # ------------------------------------------------------------------
    _create_immutability_triggers()
    _create_ownership_guards()
    _create_blob_resource_guard()
    _create_binding_closure_guard()
    _create_revision_guards()


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_reject_immutable_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'MINDATLAS_PLAN01_IMMUTABLE_ROW: % on % is not allowed',
                TG_OP, TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_update
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_immutable_mutation();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_immutable_mutation();
            """
        )


def _create_ownership_guards() -> None:
    # Skill package draft/published pointer ownership + source shape.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_skill_package_pointer_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            draft_pkg uuid;
            draft_src text;
            pub_pkg uuid;
            pub_src text;
        BEGIN
            IF NEW.draft_version_id IS NOT NULL THEN
                SELECT skill_package_id, version_source
                  INTO draft_pkg, draft_src
                  FROM assistant_skill_version
                 WHERE id = NEW.draft_version_id;
                IF draft_pkg IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_OWNERSHIP: draft_version_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF draft_pkg <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_OWNERSHIP: draft_version_id must belong to package'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF draft_src <> 'save' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_SOURCE: draft_version_id must reference save'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.published_version_id IS NOT NULL THEN
                SELECT skill_package_id, version_source
                  INTO pub_pkg, pub_src
                  FROM assistant_skill_version
                 WHERE id = NEW.published_version_id;
                IF pub_pkg IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_OWNERSHIP: published_version_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF pub_pkg <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_OWNERSHIP: published_version_id must belong to package'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF pub_src <> 'publish' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_SOURCE: published_version_id must reference publish'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_skill_package_pointer_guard
        AFTER INSERT OR UPDATE OF draft_version_id, published_version_id
        ON assistant_skill_package
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_skill_package_pointer_guard();
        """
    )

    # Publish source_draft_version_id must be same-package save row.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_skill_version_source_draft_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            src_pkg uuid;
            src_src text;
        BEGIN
            IF NEW.version_source = 'publish' THEN
                IF NEW.source_draft_version_id IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_SOURCE_DRAFT: publish requires source_draft_version_id'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT skill_package_id, version_source
                  INTO src_pkg, src_src
                  FROM assistant_skill_version
                 WHERE id = NEW.source_draft_version_id;
                IF src_pkg IS NULL OR src_pkg <> NEW.skill_package_id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_SOURCE_DRAFT: source_draft_version_id must belong to package'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF src_src <> 'save' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_SOURCE_DRAFT: source_draft_version_id must name save'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_skill_version_source_draft_guard
        AFTER INSERT OR UPDATE OF source_draft_version_id, version_source, skill_package_id
        ON assistant_skill_version
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_skill_version_source_draft_guard();
        """
    )

    # Main Agent Profile pointer ownership.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_main_agent_pointer_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            draft_profile uuid;
            draft_src text;
            pub_profile uuid;
            pub_src text;
        BEGIN
            IF NEW.draft_version_id IS NOT NULL THEN
                SELECT profile_id, version_source
                  INTO draft_profile, draft_src
                  FROM assistant_main_agent_profile_version
                 WHERE id = NEW.draft_version_id;
                IF draft_profile IS NULL OR draft_profile <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_OWNERSHIP: draft_version_id must belong to profile'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF draft_src <> 'save' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_SOURCE: draft_version_id must reference save'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.published_version_id IS NOT NULL THEN
                SELECT profile_id, version_source
                  INTO pub_profile, pub_src
                  FROM assistant_main_agent_profile_version
                 WHERE id = NEW.published_version_id;
                IF pub_profile IS NULL OR pub_profile <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_OWNERSHIP: published_version_id must belong to profile'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF pub_src <> 'publish' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_POINTER_SOURCE: published_version_id must reference publish'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_main_agent_profile_pointer_guard
        AFTER INSERT OR UPDATE OF draft_version_id, published_version_id
        ON assistant_main_agent_profile
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_main_agent_pointer_guard();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_main_agent_source_draft_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            src_profile uuid;
            src_src text;
        BEGIN
            IF NEW.version_source = 'publish' THEN
                IF NEW.source_draft_version_id IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_SOURCE_DRAFT: publish requires source_draft_version_id'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT profile_id, version_source
                  INTO src_profile, src_src
                  FROM assistant_main_agent_profile_version
                 WHERE id = NEW.source_draft_version_id;
                IF src_profile IS NULL OR src_profile <> NEW.profile_id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_SOURCE_DRAFT: source_draft_version_id must belong to profile'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF src_src <> 'save' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_SOURCE_DRAFT: source_draft_version_id must name save'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_main_agent_profile_version_source_draft_guard
        AFTER INSERT OR UPDATE OF source_draft_version_id, version_source, profile_id
        ON assistant_main_agent_profile_version
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_main_agent_source_draft_guard();
        """
    )


def _create_blob_resource_guard() -> None:
    # Each resource row's sha256/byte_size must equal its blob; every blob must
    # be referenced by at least one resource at commit (no orphan blobs).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_skill_resource_blob_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            blob_sha text;
            blob_size integer;
            orphan_count integer;
            mismatch_count integer;
        BEGIN
            SELECT COUNT(*) INTO mismatch_count
              FROM assistant_skill_version_resource r
              JOIN assistant_skill_resource_blob b ON b.id = r.blob_id
             WHERE r.sha256 <> b.sha256 OR r.byte_size <> b.byte_size;
            IF mismatch_count > 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_BLOB_METADATA: version resource sha256/byte_size must equal blob'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            SELECT COUNT(*) INTO orphan_count
              FROM assistant_skill_resource_blob b
             WHERE NOT EXISTS (
                SELECT 1 FROM assistant_skill_version_resource r WHERE r.blob_id = b.id
             );
            IF orphan_count > 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_BLOB_ORPHAN: every blob must be referenced by a version resource'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_skill_version_resource_blob_guard
        AFTER INSERT OR UPDATE OR DELETE ON assistant_skill_version_resource
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_skill_resource_blob_guard();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_skill_resource_blob_orphan_guard
        AFTER INSERT OR UPDATE OR DELETE ON assistant_skill_resource_blob
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_skill_resource_blob_guard();
        """
    )


def _create_binding_closure_guard() -> None:
    # At commit, each resolved binding's resolution_snapshot.dependencyClosure
    # (or dependency_closure / closureIndex) must match owned dependency rows
    # by (ordinal, path, dependencyDigest). Cryptographic recomputation stays
    # in application tests; this enforces relational completeness only.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_binding_closure_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            rec record;
            snap jsonb;
            idx jsonb;
            idx_count integer;
            dep_count integer;
            missing integer;
            extra integer;
        BEGIN
            FOR rec IN
                SELECT id, resolution_status, resolution_snapshot,
                       dependency_closure_digest, binding_contract_digest,
                       input_schema_digest, output_schema_digest,
                       resolution_digest
                  FROM assistant_skill_capability_binding
                 WHERE resolution_status = 'resolved'
            LOOP
                -- Snapshot digest fields must equal row columns.
                IF rec.resolution_snapshot IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_CLOSURE: resolved binding missing snapshot'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                snap := rec.resolution_snapshot::jsonb;

                -- Resolved bindings require digest keys to exist and equal columns
                -- (not only when present). Input/output pairing stays required.
                IF (NOT (snap ? 'inputSchemaDigest'))
                   OR (NOT (snap ? 'outputSchemaDigest'))
                   OR (NOT (snap ? 'resolutionDigest'))
                   OR (NOT (snap ? 'dependencyClosureDigest'))
                   OR (NOT (snap ? 'bindingContractDigest'))
                   OR (snap->>'inputSchemaDigest') IS DISTINCT FROM rec.input_schema_digest
                   OR (snap->>'outputSchemaDigest') IS DISTINCT FROM rec.output_schema_digest
                   OR (snap->>'resolutionDigest') IS DISTINCT FROM rec.resolution_digest
                   OR (snap->>'dependencyClosureDigest')
                        IS DISTINCT FROM rec.dependency_closure_digest
                   OR (snap->>'bindingContractDigest')
                        IS DISTINCT FROM rec.binding_contract_digest
                THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_CLOSURE: snapshot digests must equal binding columns'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                idx := COALESCE(
                    snap->'dependencyClosure',
                    snap->'dependency_closure',
                    snap->'closureIndex',
                    '[]'::jsonb
                );
                IF jsonb_typeof(idx) <> 'array' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_CLOSURE: dependencyClosure must be an array'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                SELECT COUNT(*) INTO idx_count FROM jsonb_array_elements(idx);
                SELECT COUNT(*) INTO dep_count
                  FROM assistant_skill_capability_dependency d
                 WHERE d.binding_id = rec.id;

                IF idx_count <> dep_count THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_CLOSURE: dependency row count must match closure index'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                SELECT COUNT(*) INTO missing
                  FROM jsonb_array_elements(idx) WITH ORDINALITY AS e(elem, ord)
                 WHERE NOT EXISTS (
                    SELECT 1
                      FROM assistant_skill_capability_dependency d
                     WHERE d.binding_id = rec.id
                       AND d.ordinal = COALESCE(
                            (e.elem->>'ordinal')::integer,
                            (e.ord - 1)::integer
                       )
                       AND d.dependency_path = COALESCE(
                            e.elem->>'path',
                            e.elem->>'dependencyPath',
                            e.elem->>'dependency_path'
                       )
                       AND d.dependency_digest = COALESCE(
                            e.elem->>'dependencyDigest',
                            e.elem->>'dependency_digest'
                       )
                 );
                IF missing > 0 THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_CLOSURE: missing dependency row for closure index entry'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                SELECT COUNT(*) INTO extra
                  FROM assistant_skill_capability_dependency d
                 WHERE d.binding_id = rec.id
                   AND NOT EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(idx) WITH ORDINALITY AS e(elem, ord)
                     WHERE d.ordinal = COALESCE(
                            (e.elem->>'ordinal')::integer,
                            (e.ord - 1)::integer
                       )
                       AND d.dependency_path = COALESCE(
                            e.elem->>'path',
                            e.elem->>'dependencyPath',
                            e.elem->>'dependency_path'
                       )
                       AND d.dependency_digest = COALESCE(
                            e.elem->>'dependencyDigest',
                            e.elem->>'dependency_digest'
                       )
                 );
                IF extra > 0 THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN01_CLOSURE: extra dependency row not in closure index'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_skill_capability_binding_closure_guard
        AFTER INSERT OR UPDATE OR DELETE ON assistant_skill_capability_binding
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_binding_closure_guard();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_skill_capability_dependency_closure_guard
        AFTER INSERT OR UPDATE OR DELETE ON assistant_skill_capability_dependency
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_binding_closure_guard();
        """
    )


def _create_revision_guards() -> None:
    # Remote Tool: execution-sensitive fields must advance config_revision by exactly 1.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_tool_config_revision_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            exec_changed boolean;
            rev_delta integer;
        BEGIN
            exec_changed := (
                NEW.kind IS DISTINCT FROM OLD.kind
                OR NEW.endpoint_url IS DISTINCT FROM OLD.endpoint_url
                OR NEW.http_method IS DISTINCT FROM OLD.http_method
                OR NEW.headers IS DISTINCT FROM OLD.headers
                OR NEW.query_params IS DISTINCT FROM OLD.query_params
                OR NEW.body_type IS DISTINCT FROM OLD.body_type
                OR NEW.body_content IS DISTINCT FROM OLD.body_content
                OR NEW.auth_type IS DISTINCT FROM OLD.auth_type
                OR NEW.auth_header_name IS DISTINCT FROM OLD.auth_header_name
                OR NEW.auth_scheme IS DISTINCT FROM OLD.auth_scheme
                OR NEW.api_key_encrypted IS DISTINCT FROM OLD.api_key_encrypted
                OR NEW.timeout_seconds IS DISTINCT FROM OLD.timeout_seconds
                OR NEW.payload_wrapper IS DISTINCT FROM OLD.payload_wrapper
                OR NEW.input_params IS DISTINCT FROM OLD.input_params
            );
            rev_delta := NEW.config_revision - OLD.config_revision;

            IF exec_changed AND rev_delta <> 1 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: tool execution change must advance config_revision by exactly 1'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF (NOT exec_changed) AND rev_delta <> 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: tool config_revision-only change is rejected'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF rev_delta < 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: config_revision cannot decrease'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_tool_config_revision_guard
        BEFORE UPDATE ON assistant_tool
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_tool_config_revision_guard();
        """
    )

    # AiModel: name/type changes advance runtime_revision by exactly 1.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_model_runtime_revision_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            exec_changed boolean;
            rev_delta integer;
        BEGIN
            exec_changed := (
                NEW.name IS DISTINCT FROM OLD.name
                OR NEW.model_type IS DISTINCT FROM OLD.model_type
                OR NEW.credential_id IS DISTINCT FROM OLD.credential_id
            );
            rev_delta := NEW.runtime_revision - OLD.runtime_revision;

            IF exec_changed AND rev_delta <> 1 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: model execution change must advance runtime_revision by exactly 1'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF (NOT exec_changed) AND rev_delta <> 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: model runtime_revision-only change is rejected'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF rev_delta < 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: runtime_revision cannot decrease'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_model_runtime_revision_guard
        BEFORE UPDATE ON ai_model
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_model_runtime_revision_guard();
        """
    )

    # AiCredential: base_url / api key slot advance runtime_revision by exactly 1.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_credential_runtime_revision_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            exec_changed boolean;
            rev_delta integer;
        BEGIN
            exec_changed := (
                NEW.base_url IS DISTINCT FROM OLD.base_url
                OR NEW.api_key_encrypted IS DISTINCT FROM OLD.api_key_encrypted
            );
            rev_delta := NEW.runtime_revision - OLD.runtime_revision;

            IF exec_changed AND rev_delta <> 1 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: credential execution change must advance runtime_revision by exactly 1'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF (NOT exec_changed) AND rev_delta <> 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: credential runtime_revision-only change is rejected'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF rev_delta < 0 THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_REVISION: runtime_revision cannot decrease'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_credential_runtime_revision_guard
        BEFORE UPDATE ON ai_credential
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_credential_runtime_revision_guard();
        """
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Read-only preflight BEFORE any destructive DDL.
    package_native = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_skill_package "
            "WHERE migration_state IN ('native','cutover')"
        )
    ).scalar()
    if package_native and int(package_native) > 0:
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    # Non-derived administrator origins only (legacy package history is discardable).
    package_origin = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_skill_version "
            "WHERE origin IN ('api','import')"
        )
    ).scalar()
    if package_origin and int(package_origin) > 0:
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    profile_native = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_main_agent_profile "
            "WHERE migration_state IN ('native','cutover')"
        )
    ).scalar()
    if profile_native and int(profile_native) > 0:
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    # Non-derived administrator origin only: allow pure derived bootstrap/legacy;
    # block api (and any future non-allowed origin).
    profile_origin = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_main_agent_profile_version "
            "WHERE origin NOT IN ('bootstrap','legacy')"
        )
    ).scalar()
    if profile_origin and int(profile_origin) > 0:
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    # Drop triggers / functions first (reverse of upgrade).
    for table in (
        "ai_credential",
        "ai_model",
        "assistant_tool",
    ):
        if table == "assistant_tool":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_config_revision_guard ON {table}")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_runtime_revision_guard ON {table}")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_capability_dependency_closure_guard "
        "ON assistant_skill_capability_dependency"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_capability_binding_closure_guard "
        "ON assistant_skill_capability_binding"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_resource_blob_orphan_guard "
        "ON assistant_skill_resource_blob"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_version_resource_blob_guard "
        "ON assistant_skill_version_resource"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_main_agent_profile_version_source_draft_guard "
        "ON assistant_main_agent_profile_version"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_main_agent_profile_pointer_guard "
        "ON assistant_main_agent_profile"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_version_source_draft_guard "
        "ON assistant_skill_version"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_package_pointer_guard "
        "ON assistant_skill_package"
    )

    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete ON {table}")

    for fn in (
        "mindatlas_credential_runtime_revision_guard",
        "mindatlas_model_runtime_revision_guard",
        "mindatlas_tool_config_revision_guard",
        "mindatlas_binding_closure_guard",
        "mindatlas_skill_resource_blob_guard",
        "mindatlas_main_agent_source_draft_guard",
        "mindatlas_main_agent_pointer_guard",
        "mindatlas_skill_version_source_draft_guard",
        "mindatlas_skill_package_pointer_guard",
        "mindatlas_reject_immutable_mutation",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")

    # Drop tables in reverse dependency order.
    op.drop_constraint(
        "fk_assistant_main_agent_profile_published_version_id",
        "assistant_main_agent_profile",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_main_agent_profile_draft_version_id",
        "assistant_main_agent_profile",
        type_="foreignkey",
    )
    op.drop_table("assistant_main_agent_profile_version")
    op.drop_table("assistant_main_agent_profile")
    op.drop_table("assistant_skill_capability_dependency")
    op.drop_table("assistant_skill_capability_binding")
    op.drop_table("assistant_skill_version_resource")
    op.drop_table("assistant_skill_resource_blob")

    op.drop_constraint(
        "fk_assistant_skill_package_published_version_id",
        "assistant_skill_package",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_skill_package_draft_version_id",
        "assistant_skill_package",
        type_="foreignkey",
    )
    op.drop_table("assistant_skill_version")
    op.drop_table("assistant_skill_package_alias")
    op.drop_table("assistant_skill_package")

    op.drop_constraint(
        "ck_ai_credential_runtime_revision_positive",
        "ai_credential",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_model_runtime_revision_positive",
        "ai_model",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_tool_config_revision_positive",
        "assistant_tool",
        type_="check",
    )
    op.drop_column("ai_credential", "runtime_revision")
    op.drop_column("ai_model", "runtime_revision")
    op.drop_column("assistant_tool", "config_revision")
