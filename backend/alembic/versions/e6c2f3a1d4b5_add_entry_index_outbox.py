"""add_entry_index_outbox

Revision ID: e6c2f3a1d4b5
Revises: c3d4e5f6a7b8
Create Date: 2026-01-21

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6c2f3a1d4b5"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entry_index_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("entry_updated_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_outbox_pending_available",
        "entry_index_outbox",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "idx_outbox_entry_id",
        "entry_index_outbox",
        ["entry_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_entry_id", table_name="entry_index_outbox")
    op.drop_index("idx_outbox_pending_available", table_name="entry_index_outbox")
    op.drop_table("entry_index_outbox")
