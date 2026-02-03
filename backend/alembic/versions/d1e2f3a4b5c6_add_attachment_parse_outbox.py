"""add_attachment_parse_outbox

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-02-03

"""

from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachment_parse_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("attachment_id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachment.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "idx_attachment_parse_outbox_pending",
        "attachment_parse_outbox",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "idx_attachment_parse_outbox_attachment_id",
        "attachment_parse_outbox",
        ["attachment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_attachment_parse_outbox_attachment_id", table_name="attachment_parse_outbox")
    op.drop_index("idx_attachment_parse_outbox_pending", table_name="attachment_parse_outbox")
    op.drop_table("attachment_parse_outbox")
