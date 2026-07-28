"""add single operator control plane

Revision ID: 9f3c1a7e2b40
Revises: 3bd7bc4257c9
Create Date: 2026-07-28

Plan 1 Task 2: additive singleton operator account, durable sessions, and
append-only audit schema with PostgreSQL immutability trigger.

This revision introduces NO Plan 10 B2 maintenance acknowledgement.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "9f3c1a7e2b40"
down_revision = "3bd7bc4257c9"
branch_labels = None
depends_on = None

_SHA256 = r"^[0-9a-f]{64}$"
DOWNGRADE_BLOCKED_TOKEN = "operator_auth_downgrade_blocked"
DESTRUCTIVE_DOWNGRADE_ENV = "MINDATLAS_TEST_DESTRUCTIVE_DOWNGRADE"

_REVOKE_REASONS = (
    "logout",
    "revoke_all",
    "password_changed",
    "idle_expired",
    "absolute_expired",
    "hmac_key_removed",
    "account_disabled",
    "password_revision_mismatch",
    "maintenance",
)
_REVOKE_REASON_SQL = ", ".join(f"'{r}'" for r in _REVOKE_REASONS)

_APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION mindatlas_reject_operator_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'operator_audit_event is append-only'
    USING ERRCODE = '55000';
END;
$$;
"""

_APPEND_ONLY_TRIGGER_SQL = """
CREATE TRIGGER trg_operator_audit_event_append_only
BEFORE UPDATE OR DELETE ON operator_audit_event
FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_operator_audit_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "operator_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "singleton_key",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'operator'"),
        ),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'operator'"),
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "password_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "failed_login_window_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "singleton_key", name="uq_operator_account_singleton_key"
        ),
        sa.CheckConstraint(
            "singleton_key = 'operator'",
            name="ck_operator_account_singleton_key",
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'operator')",
            name="ck_operator_account_role",
        ),
        sa.CheckConstraint(
            "password_revision > 0",
            name="ck_operator_account_password_revision_positive",
        ),
        sa.CheckConstraint(
            "failed_login_count >= 0",
            name="ck_operator_account_failed_login_count_nonnegative",
        ),
    )
    op.create_index(
        "idx_operator_account_lockout",
        "operator_account",
        ["locked_until", "failed_login_count"],
        unique=False,
    )

    op.create_table(
        "operator_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("operator_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("hmac_key_id", sa.String(length=64), nullable=False),
        sa.Column("password_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("user_agent_digest", sa.String(length=64), nullable=False),
        sa.Column("network_digest", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["operator_account_id"],
            ["operator_account.id"],
            name="fk_operator_session_operator_account_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_digest", name="uq_operator_session_token_digest"),
        sa.CheckConstraint(
            f"token_digest ~ '{_SHA256}'",
            name="ck_operator_session_token_digest_hex",
        ),
        sa.CheckConstraint(
            f"csrf_digest ~ '{_SHA256}'",
            name="ck_operator_session_csrf_digest_hex",
        ),
        sa.CheckConstraint(
            f"request_digest ~ '{_SHA256}'",
            name="ck_operator_session_request_digest_hex",
        ),
        sa.CheckConstraint(
            f"user_agent_digest ~ '{_SHA256}'",
            name="ck_operator_session_user_agent_digest_hex",
        ),
        sa.CheckConstraint(
            f"network_digest ~ '{_SHA256}'",
            name="ck_operator_session_network_digest_hex",
        ),
        sa.CheckConstraint(
            "password_revision > 0",
            name="ck_operator_session_password_revision_positive",
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at",
            name="ck_operator_session_absolute_after_created",
        ),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="ck_operator_session_idle_within_absolute",
        ),
        sa.CheckConstraint(
            "idle_expires_at > created_at",
            name="ck_operator_session_idle_after_created",
        ),
        sa.CheckConstraint(
            f"(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            f"(revoked_at IS NOT NULL AND revoke_reason IN ({_REVOKE_REASON_SQL}))",
            name="ck_operator_session_revoke_reason",
        ),
    )
    op.create_index(
        "idx_operator_session_active_lookup",
        "operator_session",
        ["token_digest"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "idx_operator_session_account_active",
        "operator_session",
        ["operator_account_id", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "idx_operator_session_absolute_expires",
        "operator_session",
        ["absolute_expires_at"],
        unique=False,
    )

    op.create_table(
        "operator_audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("user_agent_digest", sa.String(length=64), nullable=False),
        sa.Column("network_digest", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            f"request_digest ~ '{_SHA256}'",
            name="ck_operator_audit_event_request_digest_hex",
        ),
        sa.CheckConstraint(
            f"user_agent_digest ~ '{_SHA256}'",
            name="ck_operator_audit_event_user_agent_digest_hex",
        ),
        sa.CheckConstraint(
            f"network_digest ~ '{_SHA256}'",
            name="ck_operator_audit_event_network_digest_hex",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'rejected', 'failed')",
            name="ck_operator_audit_event_outcome",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="ck_operator_audit_event_metadata_object",
        ),
    )
    op.create_index(
        "idx_operator_audit_event_occurred_at",
        "operator_audit_event",
        ["occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_operator_audit_event_operator_occurred",
        "operator_audit_event",
        ["operator_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "idx_operator_audit_event_type_occurred",
        "operator_audit_event",
        ["event_type", "occurred_at"],
        unique=False,
    )

    op.execute(_APPEND_ONLY_FUNCTION_SQL)
    op.execute(_APPEND_ONLY_TRIGGER_SQL)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    ).fetchone()
    return row is not None


def _row_count(conn, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() or 0)


def _is_non_production_test_database(conn) -> bool:
    """Allow destructive downgrade only on disposable non-production test DBs.

    Production markers (``MINDATLAS_ENV=production`` / ``APP_ENV=production``)
    always block. The database name must look like a test database.
    """
    env_markers = (
        str(os.environ.get("MINDATLAS_ENV", "") or "").strip().lower(),
        str(os.environ.get("APP_ENV", "") or "").strip().lower(),
        str(os.environ.get("ENVIRONMENT", "") or "").strip().lower(),
    )
    if any(m in {"production", "prod"} for m in env_markers):
        return False

    db_name = conn.execute(sa.text("SELECT current_database()")).scalar()
    name = str(db_name or "").lower()
    if "test" not in name and "operator_auth" not in name:
        return False
    return True


def _assert_downgrade_allowed(conn) -> None:
    ack = str(os.environ.get(DESTRUCTIVE_DOWNGRADE_ENV, "") or "").strip()
    if ack not in {"1", "true", "TRUE", "yes", "YES"}:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: set {DESTRUCTIVE_DOWNGRADE_ENV}=1 "
            "on an empty uninitialized non-production test database"
        )
    if not _is_non_production_test_database(conn):
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: refuse downgrade outside non-production "
            "test database"
        )
    # Uninitialized = no operator account rows (and therefore no sessions).
    # Audit events alone also block — any durable evidence means not empty.
    if _row_count(conn, "operator_account") > 0:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: operator_account is not empty "
            "(system appears initialized)"
        )
    if _row_count(conn, "operator_session") > 0:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: operator_session is not empty"
        )
    if _row_count(conn, "operator_audit_event") > 0:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: operator_audit_event is not empty"
        )


def downgrade() -> None:
    conn = op.get_bind()
    _assert_downgrade_allowed(conn)

    op.execute(
        "DROP TRIGGER IF EXISTS trg_operator_audit_event_append_only "
        "ON operator_audit_event"
    )
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_operator_audit_mutation()")

    op.drop_index(
        "idx_operator_audit_event_type_occurred",
        table_name="operator_audit_event",
    )
    op.drop_index(
        "idx_operator_audit_event_operator_occurred",
        table_name="operator_audit_event",
    )
    op.drop_index(
        "idx_operator_audit_event_occurred_at",
        table_name="operator_audit_event",
    )
    op.drop_table("operator_audit_event")

    op.drop_index(
        "idx_operator_session_absolute_expires",
        table_name="operator_session",
    )
    op.drop_index(
        "idx_operator_session_account_active",
        table_name="operator_session",
    )
    op.drop_index(
        "idx_operator_session_active_lookup",
        table_name="operator_session",
    )
    op.drop_table("operator_session")

    op.drop_index("idx_operator_account_lockout", table_name="operator_account")
    op.drop_table("operator_account")
