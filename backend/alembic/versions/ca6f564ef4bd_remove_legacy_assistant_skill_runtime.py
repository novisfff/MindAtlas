"""remove legacy assistant skill runtime

Revision ID: ca6f564ef4bd
Revises: 6417df0243be
Create Date: 2026-07-23 09:42:44.980113

Plan 10 Deploy B2: destructive cleanup of legacy L2 skill_name identity and
``assistant_human_approval`` after archive/preflight gates pass.

Does **not** drop ``assistant_skill`` in this revision (residual — too entangled
with package ``legacy_skill_id``, inventory, and legacy adapter paths).
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "ca6f564ef4bd"
down_revision = "6417df0243be"
branch_labels = None
depends_on = None

B2_MAINTENANCE_ACK_ENV = "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"
B2_TEST_OVERRIDE_ENV = "MINDATLAS_PLAN10_B2_TEST_OVERRIDE"
PREFLIGHT_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_PREFLIGHT_BLOCKED"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_DOWNGRADE_BLOCKED"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN10_B2_DOWNGRADE_ACK"


def _maintenance_ack_present() -> bool:
    ack = str(os.environ.get(B2_MAINTENANCE_ACK_ENV, "") or "").strip()
    test_override = str(os.environ.get(B2_TEST_OVERRIDE_ENV, "") or "").strip()
    return ack in {"1", "true", "TRUE", "yes", "YES"} or test_override in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }


def _preflight(connection) -> None:
    """Fail closed unless maintenance ack + hard counts pass."""
    if not _maintenance_ack_present():
        raise RuntimeError(
            f"{PREFLIGHT_BLOCKED_TOKEN}: "
            f"set {B2_MAINTENANCE_ACK_ENV}=1 "
            f"(or {B2_TEST_OVERRIDE_ENV}=1 for tests)"
        )

    def _scalar(sql: str) -> int:
        row = connection.execute(sa.text(sql)).fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)

    pending = _scalar(
        "SELECT COUNT(*) FROM assistant_human_approval WHERE status = 'pending'"
    )
    nonterminal = _scalar(
        "SELECT COUNT(*) FROM assistant_chat_run "
        "WHERE runtime_kind = 'legacy' AND status IN ("
        "'queued','running','recovering','waiting_approval',"
        "'waiting_input','cancelling','needs_reconciliation')"
    )
    invalid_l2 = _scalar(
        "SELECT COUNT(*) FROM assistant_conversation_skill_l2_memory "
        "WHERE skill_package_id IS NULL "
        "OR memory_namespace IS NULL "
        "OR length(trim(memory_namespace)) = 0"
    )

    blockers: list[str] = []
    if pending > 0:
        blockers.append(f"pending_legacy_approvals={pending}")
    if nonterminal > 0:
        blockers.append(f"nonterminal_legacy_runs={nonterminal}")
    if invalid_l2 > 0:
        blockers.append(f"invalid_l2_rows={invalid_l2}")

    if blockers:
        raise RuntimeError(f"{PREFLIGHT_BLOCKED_TOKEN}: " + ",".join(blockers))


def _index_exists(connection, name: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = :name"
        ),
        {"name": name},
    ).fetchone()
    return row is not None


def _constraint_exists(connection, table: str, name: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND constraint_name = :name"
        ),
        {"table": table, "name": name},
    ).fetchone()
    return row is not None


def _table_exists(connection, name: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :name"
        ),
        {"name": name},
    ).fetchone()
    return row is not None


def _column_exists(connection, table: str, column: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    _preflight(conn)

    # ------------------------------------------------------------------
    # L2: enforce package/namespace NOT NULL, drop skill_name + legacy index
    # ------------------------------------------------------------------
    l2 = "assistant_conversation_skill_l2_memory"

    if _index_exists(conn, "uq_assistant_l2_memory_legacy_conversation_skill"):
        op.drop_index(
            "uq_assistant_l2_memory_legacy_conversation_skill",
            table_name=l2,
        )
    if _index_exists(conn, "uq_assistant_l2_memory_native_package_namespace"):
        # Drop partial unique so we can recreate as full unique after NOT NULL.
        op.drop_index(
            "uq_assistant_l2_memory_native_package_namespace",
            table_name=l2,
        )

    if _constraint_exists(conn, l2, "ck_assistant_l2_memory_package_namespace_shape"):
        op.drop_constraint(
            "ck_assistant_l2_memory_package_namespace_shape",
            l2,
            type_="check",
        )

    # FK was SET NULL; recreate as RESTRICT after NOT NULL.
    if _constraint_exists(conn, l2, "fk_assistant_l2_memory_skill_package_id"):
        op.drop_constraint(
            "fk_assistant_l2_memory_skill_package_id",
            l2,
            type_="foreignkey",
        )

    op.alter_column(
        l2,
        "skill_package_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        l2,
        "memory_namespace",
        existing_type=sa.String(length=128),
        nullable=False,
    )

    op.create_foreign_key(
        "fk_assistant_l2_memory_skill_package_id",
        l2,
        "assistant_skill_package",
        ["skill_package_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_assistant_l2_memory_package_namespace_required",
        l2,
        "skill_package_id IS NOT NULL"
        " AND memory_namespace IS NOT NULL"
        " AND length(trim(memory_namespace)) > 0",
    )

    op.create_index(
        "uq_assistant_l2_memory_native_package_namespace",
        l2,
        ["conversation_id", "skill_package_id", "memory_namespace"],
        unique=True,
    )

    if _column_exists(conn, l2, "skill_name"):
        op.drop_column(l2, "skill_name")

    # ------------------------------------------------------------------
    # Drop legacy human approval table (archive path is separate evidence)
    # ------------------------------------------------------------------
    if _table_exists(conn, "assistant_human_approval"):
        # Drop known indexes first when present.
        for idx in (
            "ix_assistant_human_approval_run_status",
            "ix_assistant_human_approval_conversation_status",
            "ix_assistant_human_approval_skill_id",
            "ix_assistant_human_approval_status",
        ):
            if _index_exists(conn, idx):
                op.drop_index(idx, table_name="assistant_human_approval")
        op.drop_table("assistant_human_approval")


def downgrade() -> None:
    """Recreate structural compatibility only — does not restore dropped rows."""
    ack = str(os.environ.get(DOWNGRADE_ACK_ENV, "") or "").strip()
    if ack not in {"1", "true", "TRUE", "yes", "YES"}:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: set {DOWNGRADE_ACK_ENV}=1 to "
            "recreate structural L2/approval compatibility (data not restored)"
        )

    conn = op.get_bind()
    l2 = "assistant_conversation_skill_l2_memory"

    # Recreate empty assistant_human_approval skeleton.
    if not _table_exists(conn, "assistant_human_approval"):
        op.create_table(
            "assistant_human_approval",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=True),
            sa.Column("request_payload", sa.JSON(), nullable=True),
            sa.Column("decision_payload", sa.JSON(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Restore skill_name as nullable compatibility column.
    if not _column_exists(conn, l2, "skill_name"):
        op.add_column(
            l2,
            sa.Column("skill_name", sa.String(length=100), nullable=True),
        )
        op.execute(
            sa.text(
                f"UPDATE {l2} SET skill_name = COALESCE("
                "NULLIF(trim(memory_namespace), ''), 'skill') "
                "WHERE skill_name IS NULL"
            )
        )
        op.alter_column(
            l2,
            "skill_name",
            existing_type=sa.String(length=100),
            nullable=False,
        )

    if _index_exists(conn, "uq_assistant_l2_memory_native_package_namespace"):
        op.drop_index(
            "uq_assistant_l2_memory_native_package_namespace",
            table_name=l2,
        )
    if _constraint_exists(conn, l2, "ck_assistant_l2_memory_package_namespace_required"):
        op.drop_constraint(
            "ck_assistant_l2_memory_package_namespace_required",
            l2,
            type_="check",
        )
    if _constraint_exists(conn, l2, "fk_assistant_l2_memory_skill_package_id"):
        op.drop_constraint(
            "fk_assistant_l2_memory_skill_package_id",
            l2,
            type_="foreignkey",
        )

    op.alter_column(
        l2,
        "skill_package_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        l2,
        "memory_namespace",
        existing_type=sa.String(length=128),
        nullable=True,
    )

    op.create_foreign_key(
        "fk_assistant_l2_memory_skill_package_id",
        l2,
        "assistant_skill_package",
        ["skill_package_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_assistant_l2_memory_package_namespace_shape",
        l2,
        "("
        "  skill_package_id IS NULL AND memory_namespace IS NULL"
        ") OR ("
        "  skill_package_id IS NOT NULL"
        "  AND memory_namespace IS NOT NULL"
        "  AND length(trim(memory_namespace)) > 0"
        ")",
    )
    op.create_index(
        "uq_assistant_l2_memory_legacy_conversation_skill",
        l2,
        ["conversation_id", "skill_name"],
        unique=True,
        postgresql_where=sa.text("skill_package_id IS NULL"),
    )
    op.create_index(
        "uq_assistant_l2_memory_native_package_namespace",
        l2,
        ["conversation_id", "skill_package_id", "memory_namespace"],
        unique=True,
        postgresql_where=sa.text("skill_package_id IS NOT NULL"),
    )
