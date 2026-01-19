"""add_include_in_summary_to_assistant_skill_step

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-01-19

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_skill_step",
        sa.Column("include_in_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("assistant_skill_step", "include_in_summary", server_default=None)


def downgrade() -> None:
    op.drop_column("assistant_skill_step", "include_in_summary")

