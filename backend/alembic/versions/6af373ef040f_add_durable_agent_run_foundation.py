"""add durable agent run foundation

Revision ID: 6af373ef040f
Revises: 9ed6f561a381
Create Date: 2026-07-15 13:28:02.035995

Plan 06 Task 1: durable Main Agent Run aggregate columns, immutable child tables,
worker registration, Artifact GC outbox, L1/L2 memory foundation, event keys,
partial active-run uniqueness, immutability/purge triggers, pointer ownership
guards, upgrade preflight against duplicate active Runs, and downgrade refusal
when durable Main Agent data exists without maintenance acknowledgment.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "6af373ef040f"
down_revision = "9ed6f561a381"
branch_labels = None
depends_on = None


_SHA256 = r"^[0-9a-f]{64}$"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN06_DOWNGRADE_BLOCKED_DURABLE_DATA"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN06_DOWNGRADE_ACK_PURGE_DURABLE_DATA"
DUPLICATE_ACTIVE_TOKEN = "MINDATLAS_PLAN06_DUPLICATE_ACTIVE_RUN"

ACTIVE_STATUS_SQL = (
    "'queued','running','recovering','waiting_approval','waiting_input',"
    "'cancelling','needs_reconciliation'"
)

_IMMUTABLE_TABLES = (
    "assistant_run_manifest_revision",
    "assistant_run_provider_message",
    "assistant_run_policy_revision",
    "assistant_run_budget_revision",
    "assistant_run_obligation_revision",
    "assistant_run_checkpoint",
    "assistant_run_artifact",
)


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Preflight: refuse duplicate active Runs for any conversation
    # ------------------------------------------------------------------
    dup = conn.execute(
        sa.text(
            f"""
            SELECT conversation_id, COUNT(*) AS n
            FROM assistant_chat_run
            WHERE status IN ({ACTIVE_STATUS_SQL})
            GROUP BY conversation_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).fetchone()
    if dup is not None:
        raise RuntimeError(
            f"{DUPLICATE_ACTIVE_TOKEN}: conversation_id={dup[0]} has {dup[1]} "
            "active runs; resolve manually before Plan 06 upgrade (no winner is chosen)"
        )

    # ------------------------------------------------------------------
    # Child tables first (aggregate pointer FKs added later)
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_worker_registration",
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("app_build_revision", sa.String(length=160), nullable=False),
        sa.Column("runtime_contract_version", sa.Integer(), nullable=False),
        sa.Column("supported_checkpoint_codec_versions", sa.JSON(), nullable=False),
        sa.Column("capability_feature_digest", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draining_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hostname_label", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("worker_id"),
        sa.CheckConstraint(
            "runtime_contract_version > 0",
            name="ck_assistant_worker_registration_contract_positive",
        ),
        sa.CheckConstraint(
            f"capability_feature_digest ~ '{_SHA256}'",
            name="ck_assistant_worker_registration_feature_digest",
        ),
    )

    op.create_table(
        "assistant_run_manifest_revision",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("parent_digest", sa.String(length=64), nullable=True),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["assistant_run_manifest_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_assistant_run_manifest_revision_positive",
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_run_manifest_schema_version_positive",
        ),
        sa.CheckConstraint(
            f"manifest_digest ~ '{_SHA256}'",
            name="ck_assistant_run_manifest_digest",
        ),
        sa.CheckConstraint(
            f"parent_digest IS NULL OR parent_digest ~ '{_SHA256}'",
            name="ck_assistant_run_manifest_parent_digest",
        ),
    )
    op.create_index(
        "uq_assistant_run_manifest_revision_run_rev",
        "assistant_run_manifest_revision",
        ["run_id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_manifest_revision_run_digest",
        "assistant_run_manifest_revision",
        ["run_id", "manifest_digest"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_manifest_revision_run_id",
        "assistant_run_manifest_revision",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_policy_revision",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("parent_digest", sa.String(length=64), nullable=True),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["assistant_run_policy_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_assistant_run_policy_revision_positive",
        ),
        sa.CheckConstraint(
            f"policy_digest ~ '{_SHA256}'",
            name="ck_assistant_run_policy_digest",
        ),
        sa.CheckConstraint(
            f"parent_digest IS NULL OR parent_digest ~ '{_SHA256}'",
            name="ck_assistant_run_policy_parent_digest",
        ),
    )
    op.create_index(
        "uq_assistant_run_policy_revision_run_rev",
        "assistant_run_policy_revision",
        ["run_id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_policy_revision_run_digest",
        "assistant_run_policy_revision",
        ["run_id", "policy_digest"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_policy_revision_run_id",
        "assistant_run_policy_revision",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_budget_revision",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("parent_digest", sa.String(length=64), nullable=True),
        sa.Column("budget_digest", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["assistant_run_budget_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_assistant_run_budget_revision_positive",
        ),
        sa.CheckConstraint(
            f"budget_digest ~ '{_SHA256}'",
            name="ck_assistant_run_budget_digest",
        ),
        sa.CheckConstraint(
            f"parent_digest IS NULL OR parent_digest ~ '{_SHA256}'",
            name="ck_assistant_run_budget_parent_digest",
        ),
    )
    op.create_index(
        "uq_assistant_run_budget_revision_run_rev",
        "assistant_run_budget_revision",
        ["run_id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_budget_revision_run_digest",
        "assistant_run_budget_revision",
        ["run_id", "budget_digest"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_budget_revision_run_id",
        "assistant_run_budget_revision",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_obligation_revision",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("parent_digest", sa.String(length=64), nullable=True),
        sa.Column("obligation_digest", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["assistant_run_obligation_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_assistant_run_obligation_revision_positive",
        ),
        sa.CheckConstraint(
            f"obligation_digest ~ '{_SHA256}'",
            name="ck_assistant_run_obligation_digest",
        ),
        sa.CheckConstraint(
            f"parent_digest IS NULL OR parent_digest ~ '{_SHA256}'",
            name="ck_assistant_run_obligation_parent_digest",
        ),
    )
    op.create_index(
        "uq_assistant_run_obligation_revision_run_rev",
        "assistant_run_obligation_revision",
        ["run_id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_obligation_revision_run_digest",
        "assistant_run_obligation_revision",
        ["run_id", "obligation_digest"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_obligation_revision_run_id",
        "assistant_run_obligation_revision",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_provider_message",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "provider_round",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "payload_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("payload_discriminator", sa.String(length=64), nullable=True),
        sa.Column("payload_body", sa.JSON(), nullable=False),
        sa.Column("protection_kind", sa.String(length=32), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("obligation_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_revision_id"],
            ["assistant_run_manifest_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_revision_id"],
            ["assistant_run_policy_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["obligation_revision_id"],
            ["assistant_run_obligation_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_assistant_run_provider_message_ordinal",
        ),
        sa.CheckConstraint(
            "provider_round >= 0",
            name="ck_assistant_run_provider_message_round",
        ),
        sa.CheckConstraint(
            "payload_version > 0",
            name="ck_assistant_run_provider_message_payload_version",
        ),
        sa.CheckConstraint(
            "role IN ("
            "'system','runtime_instruction','runtime_context','runtime_completion',"
            "'user','assistant','tool'"
            ")",
            name="ck_assistant_run_provider_message_role",
        ),
        sa.CheckConstraint(
            "protection_kind IN ('public','protected','internal')",
            name="ck_assistant_run_provider_message_protection",
        ),
        sa.CheckConstraint(
            "("
            "  role IN ('system','user','assistant','tool')"
            "  AND ("
            "    role <> 'system'"
            "    OR (protection_kind <> 'protected' AND payload_discriminator IS NULL)"
            "  )"
            ") OR ("
            "  role = 'runtime_instruction'"
            "  AND protection_kind = 'protected'"
            "  AND policy_revision_id IS NOT NULL"
            "  AND obligation_revision_id IS NULL"
            ") OR ("
            "  role = 'runtime_context'"
            "  AND protection_kind = 'protected'"
            "  AND policy_revision_id IS NOT NULL"
            "  AND obligation_revision_id IS NULL"
            ") OR ("
            "  role = 'runtime_completion'"
            "  AND protection_kind = 'protected'"
            "  AND policy_revision_id IS NOT NULL"
            "  AND obligation_revision_id IS NOT NULL"
            ")",
            name="ck_assistant_run_provider_message_role_links",
        ),
        sa.CheckConstraint(
            f"content_digest ~ '{_SHA256}'",
            name="ck_assistant_run_provider_message_content_digest",
        ),
    )
    op.create_index(
        "uq_assistant_run_provider_message_ordinal",
        "assistant_run_provider_message",
        ["run_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_provider_message_tool_call",
        "assistant_run_provider_message",
        ["run_id", "tool_call_id"],
        unique=True,
        postgresql_where=sa.text("tool_call_id IS NOT NULL"),
    )
    op.create_index(
        "ix_assistant_run_provider_message_run_id",
        "assistant_run_provider_message",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_checkpoint",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("expected_state_revision", sa.Integer(), nullable=False),
        sa.Column("committed_state_revision", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("manifest_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("budget_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("obligation_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_message_ordinal", sa.Integer(), nullable=False),
        sa.Column("provider_transcript_digest", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("logical_unit_id", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("state_payload", sa.JSON(), nullable=False),
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_revision_id"],
            ["assistant_run_manifest_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_revision_id"],
            ["assistant_run_policy_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["budget_revision_id"],
            ["assistant_run_budget_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["obligation_revision_id"],
            ["assistant_run_obligation_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_assistant_run_checkpoint_sequence",
        ),
        sa.CheckConstraint(
            "expected_state_revision >= 0",
            name="ck_assistant_run_checkpoint_expected_revision",
        ),
        sa.CheckConstraint(
            "committed_state_revision >= 0",
            name="ck_assistant_run_checkpoint_committed_revision",
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_run_checkpoint_schema_version",
        ),
        sa.CheckConstraint(
            "phase IN ("
            "'ready_for_provider','dispatching_calls','waiting',"
            "'ready_for_completion','ready_for_memory','terminal'"
            ")",
            name="ck_assistant_run_checkpoint_phase",
        ),
        sa.CheckConstraint(
            f"provider_transcript_digest ~ '{_SHA256}'",
            name="ck_assistant_run_checkpoint_transcript_digest",
        ),
        sa.CheckConstraint(
            f"state_digest ~ '{_SHA256}'",
            name="ck_assistant_run_checkpoint_state_digest",
        ),
    )
    op.create_index(
        "uq_assistant_run_checkpoint_sequence",
        "assistant_run_checkpoint",
        ["run_id", "sequence"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_checkpoint_committed_revision",
        "assistant_run_checkpoint",
        ["run_id", "committed_state_revision"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_run_checkpoint_state_digest",
        "assistant_run_checkpoint",
        ["run_id", "state_digest"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_checkpoint_run_id",
        "assistant_run_checkpoint",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_artifact",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("display_label", sa.String(length=255), nullable=True),
        sa.Column("storage_kind", sa.String(length=16), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("inline_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "storage_kind IN ('inline','object')",
            name="ck_assistant_run_artifact_storage_kind",
        ),
        sa.CheckConstraint(
            "byte_size >= 0",
            name="ck_assistant_run_artifact_byte_size",
        ),
        sa.CheckConstraint(
            "("
            "  storage_kind = 'inline'"
            "  AND inline_bytes IS NOT NULL"
            "  AND object_key IS NULL"
            ") OR ("
            "  storage_kind = 'object'"
            "  AND inline_bytes IS NULL"
            "  AND object_key IS NOT NULL"
            ")",
            name="ck_assistant_run_artifact_storage_xor",
        ),
        sa.CheckConstraint(
            f"content_sha256 ~ '{_SHA256}'",
            name="ck_assistant_run_artifact_content_sha256",
        ),
    )
    op.create_index(
        "uq_assistant_run_artifact_content",
        "assistant_run_artifact",
        ["run_id", "content_sha256", "byte_size"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_artifact_run_id",
        "assistant_run_artifact",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "assistant_run_artifact_gc",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("bucket_name", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','deleted','failed')",
            name="ck_assistant_run_artifact_gc_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_assistant_run_artifact_gc_attempts",
        ),
        sa.CheckConstraint(
            f"content_sha256 ~ '{_SHA256}'",
            name="ck_assistant_run_artifact_gc_content_sha256",
        ),
    )
    op.create_index(
        "uq_assistant_run_artifact_gc_object",
        "assistant_run_artifact_gc",
        ["bucket_name", "object_key", "content_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_run_artifact_gc_status_next",
        "assistant_run_artifact_gc",
        ["status", "next_attempt_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Extend assistant_chat_run
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "runtime_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("runtime_contract_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("required_app_build_revision", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("current_manifest_revision_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("current_policy_revision_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("current_checkpoint_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("current_budget_revision_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("current_obligation_revision_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "recovery_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("failure_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "memory_commit_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'not_applicable'"),
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("memory_committed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill: every existing Run is legacy without synthesizing durable state.
    # server_default already applied runtime_kind/state_revision/etc. on add.
    op.execute(
        sa.text(
            """
            UPDATE assistant_chat_run
            SET runtime_kind = 'legacy',
                runtime_contract_version = NULL,
                required_app_build_revision = NULL,
                state_revision = 0,
                current_manifest_revision_id = NULL,
                current_policy_revision_id = NULL,
                current_checkpoint_id = NULL,
                current_budget_revision_id = NULL,
                current_obligation_revision_id = NULL,
                lease_owner = NULL,
                lease_generation = 0,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                next_attempt_at = NULL,
                recovery_count = 0,
                deadline_at = NULL,
                failure_code = NULL,
                memory_commit_status = 'not_applicable',
                memory_committed_at = NULL
            """
        )
    )

    # Replace status check with complete locked state set.
    op.drop_constraint(
        "ck_assistant_chat_run_status",
        "assistant_chat_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_status",
        "assistant_chat_run",
        "status IN ("
        "'queued','running','recovering','waiting_approval','waiting_input',"
        "'cancelling','needs_reconciliation','completed','failed','cancelled'"
        ")",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_kind",
        "assistant_chat_run",
        "runtime_kind IN ('legacy','main_agent')",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_state_revision",
        "assistant_chat_run",
        "state_revision >= 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_lease_generation",
        "assistant_chat_run",
        "lease_generation >= 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_recovery_count",
        "assistant_chat_run",
        "recovery_count >= 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_contract_version",
        "assistant_chat_run",
        "runtime_contract_version IS NULL OR runtime_contract_version > 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_memory_commit_status",
        "assistant_chat_run",
        "memory_commit_status IN ('not_applicable','pending','committed','failed')",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_kind_shape",
        "assistant_chat_run",
        "("
        "  runtime_kind = 'legacy'"
        "  AND runtime_contract_version IS NULL"
        ") OR ("
        "  runtime_kind = 'main_agent'"
        "  AND runtime_contract_version IS NOT NULL"
        "  AND required_app_build_revision IS NOT NULL"
        ")",
    )

    # Pointer FKs after child tables exist (ON DELETE SET NULL).
    op.create_foreign_key(
        "fk_assistant_chat_run_current_manifest_revision_id",
        "assistant_chat_run",
        "assistant_run_manifest_revision",
        ["current_manifest_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_chat_run_current_policy_revision_id",
        "assistant_chat_run",
        "assistant_run_policy_revision",
        ["current_policy_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_chat_run_current_checkpoint_id",
        "assistant_chat_run",
        "assistant_run_checkpoint",
        ["current_checkpoint_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_chat_run_current_budget_revision_id",
        "assistant_chat_run",
        "assistant_run_budget_revision",
        ["current_budget_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_chat_run_current_obligation_revision_id",
        "assistant_chat_run",
        "assistant_run_obligation_revision",
        ["current_obligation_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "uq_assistant_chat_run_active_conversation",
        "assistant_chat_run",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({ACTIVE_STATUS_SQL})"),
    )
    op.create_index(
        "ix_assistant_chat_run_lease_claim",
        "assistant_chat_run",
        ["status", "next_attempt_at", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Extend assistant_chat_run_event
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_chat_run_event",
        sa.Column("event_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "assistant_chat_run_event",
        sa.Column(
            "payload_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "assistant_chat_run_event",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'public'"),
        ),
    )
    # Backfill existing events as public/payload_version=1 (server defaults).
    op.create_check_constraint(
        "ck_assistant_chat_run_event_payload_version",
        "assistant_chat_run_event",
        "payload_version > 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_event_visibility",
        "assistant_chat_run_event",
        "visibility IN ('public','internal')",
    )
    op.create_index(
        "uq_assistant_chat_run_event_key",
        "assistant_chat_run_event",
        ["run_id", "event_key"],
        unique=True,
        postgresql_where=sa.text("event_key IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # L1 / L2 memory additions
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_conversation_l1_memory",
        sa.Column("last_applied_run_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_assistant_l1_memory_last_applied_run_id",
        "assistant_conversation_l1_memory",
        "assistant_chat_run",
        ["last_applied_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "assistant_conversation_skill_l2_memory",
        sa.Column("skill_package_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assistant_conversation_skill_l2_memory",
        sa.Column("memory_namespace", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "assistant_conversation_skill_l2_memory",
        sa.Column("facts_v2", sa.JSON(), nullable=True),
    )
    op.add_column(
        "assistant_conversation_skill_l2_memory",
        sa.Column("last_applied_run_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_assistant_l2_memory_skill_package_id",
        "assistant_conversation_skill_l2_memory",
        "assistant_skill_package",
        ["skill_package_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_l2_memory_last_applied_run_id",
        "assistant_conversation_skill_l2_memory",
        "assistant_chat_run",
        ["last_applied_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Existing rows remain Legacy (NULL package/namespace); facts untouched.
    op.create_check_constraint(
        "ck_assistant_l2_memory_package_namespace_shape",
        "assistant_conversation_skill_l2_memory",
        "("
        "  skill_package_id IS NULL AND memory_namespace IS NULL"
        ") OR ("
        "  skill_package_id IS NOT NULL"
        "  AND memory_namespace IS NOT NULL"
        "  AND length(trim(memory_namespace)) > 0"
        ")",
    )
    # Replace unconditional unique index with split Legacy/native partial uniques.
    op.drop_index(
        "ix_assistant_l2_memory_conversation_skill",
        table_name="assistant_conversation_skill_l2_memory",
    )
    op.create_index(
        "uq_assistant_l2_memory_legacy_conversation_skill",
        "assistant_conversation_skill_l2_memory",
        ["conversation_id", "skill_name"],
        unique=True,
        postgresql_where=sa.text("skill_package_id IS NULL"),
    )
    op.create_index(
        "uq_assistant_l2_memory_native_package_namespace",
        "assistant_conversation_skill_l2_memory",
        ["conversation_id", "skill_package_id", "memory_namespace"],
        unique=True,
        postgresql_where=sa.text("skill_package_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # PostgreSQL immutability + pointer ownership guards
    # ------------------------------------------------------------------
    _create_immutability_triggers()
    _create_pointer_ownership_guard()
    _create_provider_message_same_run_guard()
    _create_checkpoint_same_run_guard()


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_reject_durable_run_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            allow_purge text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_IMMUTABLE_ROW: UPDATE on % is not allowed',
                    TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- DELETE allowed only when local purge flag is set (conversation delete).
            allow_purge := current_setting('mindatlas.allow_durable_run_purge', true);
            IF allow_purge IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_IMMUTABLE_ROW: DELETE on % requires '
                    'SET LOCAL mindatlas.allow_durable_run_purge = ''on''',
                    TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN OLD;
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
            EXECUTE PROCEDURE mindatlas_reject_durable_run_mutation();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_durable_run_mutation();
            """
        )


def _create_pointer_ownership_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_durable_run_pointer_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner uuid;
        BEGIN
            IF NEW.current_manifest_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_manifest_revision
                 WHERE id = NEW.current_manifest_revision_id;
                IF owner IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: current_manifest_revision_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF owner <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: manifest revision must belong to run'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.current_policy_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_policy_revision
                 WHERE id = NEW.current_policy_revision_id;
                IF owner IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: current_policy_revision_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF owner <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: policy revision must belong to run'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.current_budget_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_budget_revision
                 WHERE id = NEW.current_budget_revision_id;
                IF owner IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: current_budget_revision_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF owner <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: budget revision must belong to run'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.current_obligation_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_obligation_revision
                 WHERE id = NEW.current_obligation_revision_id;
                IF owner IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: current_obligation_revision_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF owner <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: obligation revision must belong to run'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.current_checkpoint_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_checkpoint
                 WHERE id = NEW.current_checkpoint_id;
                IF owner IS NULL THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: current_checkpoint_id missing'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF owner <> NEW.id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: checkpoint must belong to run'
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
        CREATE CONSTRAINT TRIGGER trg_assistant_chat_run_pointer_guard
        AFTER INSERT OR UPDATE OF
            current_manifest_revision_id,
            current_policy_revision_id,
            current_budget_revision_id,
            current_obligation_revision_id,
            current_checkpoint_id
        ON assistant_chat_run
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_durable_run_pointer_guard();
        """
    )


def _create_provider_message_same_run_guard() -> None:
    """Linked revision rows must belong to the same Run as the message."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_provider_message_same_run_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner uuid;
        BEGIN
            SELECT run_id INTO owner
              FROM assistant_run_manifest_revision
             WHERE id = NEW.manifest_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_POINTER_OWNERSHIP: provider message manifest revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.policy_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_policy_revision
                 WHERE id = NEW.policy_revision_id;
                IF owner IS NULL OR owner <> NEW.run_id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: provider message policy revision mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            IF NEW.obligation_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner
                  FROM assistant_run_obligation_revision
                 WHERE id = NEW.obligation_revision_id;
                IF owner IS NULL OR owner <> NEW.run_id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN06_POINTER_OWNERSHIP: provider message obligation revision mismatch'
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
        CREATE CONSTRAINT TRIGGER trg_assistant_run_provider_message_same_run
        AFTER INSERT OR UPDATE ON assistant_run_provider_message
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_provider_message_same_run_guard();
        """
    )


def _create_checkpoint_same_run_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_checkpoint_same_run_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner uuid;
        BEGIN
            SELECT run_id INTO owner FROM assistant_run_manifest_revision
             WHERE id = NEW.manifest_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_POINTER_OWNERSHIP: checkpoint manifest revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT run_id INTO owner FROM assistant_run_policy_revision
             WHERE id = NEW.policy_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_POINTER_OWNERSHIP: checkpoint policy revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT run_id INTO owner FROM assistant_run_budget_revision
             WHERE id = NEW.budget_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_POINTER_OWNERSHIP: checkpoint budget revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT run_id INTO owner FROM assistant_run_obligation_revision
             WHERE id = NEW.obligation_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN06_POINTER_OWNERSHIP: checkpoint obligation revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_run_checkpoint_same_run
        AFTER INSERT OR UPDATE ON assistant_run_checkpoint
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_checkpoint_same_run_guard();
        """
    )


def _has_durable_main_agent_data(conn) -> bool:
    main_agent_runs = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_chat_run WHERE runtime_kind = 'main_agent'"
        )
    ).scalar()
    if int(main_agent_runs or 0) > 0:
        return True
    for table in (
        "assistant_run_manifest_revision",
        "assistant_run_provider_message",
        "assistant_run_policy_revision",
        "assistant_run_budget_revision",
        "assistant_run_obligation_revision",
        "assistant_run_checkpoint",
        "assistant_run_artifact",
        "assistant_run_artifact_gc",
        "assistant_worker_registration",
    ):
        count = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()
        if int(count or 0) > 0:
            return True
    # Non-null durable pointers on legacy rows also block (unexpected).
    pointers = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM assistant_chat_run
            WHERE current_manifest_revision_id IS NOT NULL
               OR current_policy_revision_id IS NOT NULL
               OR current_checkpoint_id IS NOT NULL
               OR current_budget_revision_id IS NOT NULL
               OR current_obligation_revision_id IS NOT NULL
            """
        )
    ).scalar()
    return int(pointers or 0) > 0


def downgrade() -> None:
    conn = op.get_bind()
    has_data = _has_durable_main_agent_data(conn)
    ack = os.environ.get(DOWNGRADE_ACK_ENV, "").strip()
    if has_data:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: durable Main Agent Run/history/worker/"
            "artifact data exists; complete an explicit export-and-purge maintenance "
            f"procedure, then set {DOWNGRADE_ACK_ENV}=1 only after data is gone "
            "(ack alone is insufficient while durable rows remain)"
        )
    if ack != "1":
        # Even with no durable data, require acknowledgment for a destructive schema drop.
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: set {DOWNGRADE_ACK_ENV}=1 to acknowledge "
            "Plan 06 schema removal after confirming no durable Main Agent data remains"
        )

    # Drop triggers / functions first.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_chat_run_pointer_guard ON assistant_chat_run"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_run_provider_message_same_run "
        "ON assistant_run_provider_message"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_run_checkpoint_same_run "
        "ON assistant_run_checkpoint"
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete ON {table}")

    op.execute("DROP FUNCTION IF EXISTS mindatlas_durable_run_pointer_guard()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_provider_message_same_run_guard()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_checkpoint_same_run_guard()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_durable_run_mutation()")

    # Drop pointer FKs before child tables.
    op.drop_constraint(
        "fk_assistant_chat_run_current_manifest_revision_id",
        "assistant_chat_run",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_current_policy_revision_id",
        "assistant_chat_run",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_current_checkpoint_id",
        "assistant_chat_run",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_current_budget_revision_id",
        "assistant_chat_run",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_current_obligation_revision_id",
        "assistant_chat_run",
        type_="foreignkey",
    )

    # L2 / L1 reverse
    op.drop_index(
        "uq_assistant_l2_memory_native_package_namespace",
        table_name="assistant_conversation_skill_l2_memory",
    )
    op.drop_index(
        "uq_assistant_l2_memory_legacy_conversation_skill",
        table_name="assistant_conversation_skill_l2_memory",
    )
    op.create_index(
        "ix_assistant_l2_memory_conversation_skill",
        "assistant_conversation_skill_l2_memory",
        ["conversation_id", "skill_name"],
        unique=True,
    )
    op.drop_constraint(
        "ck_assistant_l2_memory_package_namespace_shape",
        "assistant_conversation_skill_l2_memory",
        type_="check",
    )
    op.drop_constraint(
        "fk_assistant_l2_memory_last_applied_run_id",
        "assistant_conversation_skill_l2_memory",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_l2_memory_skill_package_id",
        "assistant_conversation_skill_l2_memory",
        type_="foreignkey",
    )
    op.drop_column("assistant_conversation_skill_l2_memory", "last_applied_run_id")
    op.drop_column("assistant_conversation_skill_l2_memory", "facts_v2")
    op.drop_column("assistant_conversation_skill_l2_memory", "memory_namespace")
    op.drop_column("assistant_conversation_skill_l2_memory", "skill_package_id")

    op.drop_constraint(
        "fk_assistant_l1_memory_last_applied_run_id",
        "assistant_conversation_l1_memory",
        type_="foreignkey",
    )
    op.drop_column("assistant_conversation_l1_memory", "last_applied_run_id")

    # Events reverse
    op.drop_index(
        "uq_assistant_chat_run_event_key",
        table_name="assistant_chat_run_event",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_event_visibility",
        "assistant_chat_run_event",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_event_payload_version",
        "assistant_chat_run_event",
        type_="check",
    )
    op.drop_column("assistant_chat_run_event", "visibility")
    op.drop_column("assistant_chat_run_event", "payload_version")
    op.drop_column("assistant_chat_run_event", "event_key")

    # Run columns reverse
    op.drop_index(
        "ix_assistant_chat_run_lease_claim",
        table_name="assistant_chat_run",
    )
    op.drop_index(
        "uq_assistant_chat_run_active_conversation",
        table_name="assistant_chat_run",
    )
    for ck in (
        "ck_assistant_chat_run_runtime_kind_shape",
        "ck_assistant_chat_run_memory_commit_status",
        "ck_assistant_chat_run_runtime_contract_version",
        "ck_assistant_chat_run_recovery_count",
        "ck_assistant_chat_run_lease_generation",
        "ck_assistant_chat_run_state_revision",
        "ck_assistant_chat_run_runtime_kind",
    ):
        op.drop_constraint(ck, "assistant_chat_run", type_="check")

    op.drop_constraint(
        "ck_assistant_chat_run_status",
        "assistant_chat_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_status",
        "assistant_chat_run",
        "status IN ("
        "'queued','running','waiting_approval','cancelling',"
        "'completed','failed','cancelled'"
        ")",
    )

    for col in (
        "memory_committed_at",
        "memory_commit_status",
        "failure_code",
        "deadline_at",
        "recovery_count",
        "next_attempt_at",
        "heartbeat_at",
        "lease_expires_at",
        "lease_generation",
        "lease_owner",
        "current_obligation_revision_id",
        "current_budget_revision_id",
        "current_checkpoint_id",
        "current_policy_revision_id",
        "current_manifest_revision_id",
        "state_revision",
        "required_app_build_revision",
        "runtime_contract_version",
        "runtime_kind",
    ):
        op.drop_column("assistant_chat_run", col)

    # Drop child tables (pointers already detached).
    op.drop_table("assistant_run_artifact_gc")
    op.drop_table("assistant_run_artifact")
    op.drop_table("assistant_run_checkpoint")
    op.drop_table("assistant_run_provider_message")
    op.drop_table("assistant_run_obligation_revision")
    op.drop_table("assistant_run_budget_revision")
    op.drop_table("assistant_run_policy_revision")
    op.drop_table("assistant_run_manifest_revision")
    op.drop_table("assistant_worker_registration")
