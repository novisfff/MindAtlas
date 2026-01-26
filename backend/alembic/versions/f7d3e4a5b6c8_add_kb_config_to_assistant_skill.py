"""add_kb_config_to_assistant_skill_and_step

Revision ID: f7d3e4a5b6c8
Revises: e6c2f3a1d4b5
Create Date: 2026-01-23

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7d3e4a5b6c8"
down_revision = "92af972bc123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_skill", sa.Column("kb_config", sa.JSON(), nullable=True))
    op.add_column("assistant_skill_step", sa.Column("kb_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_skill_step", "kb_config")
    op.drop_column("assistant_skill", "kb_config")
