"""add_output_config_to_assistant_skill_step

Revision ID: b1c2d3e4f5a6
Revises: ff41e761e940
Create Date: 2026-01-19

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "ff41e761e940"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_skill_step", sa.Column("output_mode", sa.String(length=16), nullable=True))
    op.add_column("assistant_skill_step", sa.Column("output_fields", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_skill_step", "output_fields")
    op.drop_column("assistant_skill_step", "output_mode")

