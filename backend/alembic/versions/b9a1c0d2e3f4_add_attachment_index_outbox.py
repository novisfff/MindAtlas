"""add_attachment_index_outbox

Revision ID: b9a1c0d2e3f4
Revises: e2f3a4b5c6d7
Create Date: 2026-02-03

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b9a1c0d2e3f4"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachment_index_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("attachment_id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_attachment_outbox_pending_available",
        "attachment_index_outbox",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "idx_attachment_outbox_attachment_id",
        "attachment_index_outbox",
        ["attachment_id"],
        unique=False,
    )
    op.create_index(
        "idx_attachment_outbox_entry_id",
        "attachment_index_outbox",
        ["entry_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_attachment_outbox_entry_id", table_name="attachment_index_outbox")
    op.drop_index("idx_attachment_outbox_attachment_id", table_name="attachment_index_outbox")
    op.drop_index("idx_attachment_outbox_pending_available", table_name="attachment_index_outbox")
    op.drop_table("attachment_index_outbox")

