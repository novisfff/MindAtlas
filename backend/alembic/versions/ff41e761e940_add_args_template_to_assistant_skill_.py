"""add_args_template_to_assistant_skill_step

Revision ID: ff41e761e940
Revises: abdb36b18b47
Create Date: 2026-01-15 16:34:53.352020

"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = 'ff41e761e940'
down_revision = 'abdb36b18b47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_skill_step", sa.Column("args_template", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_skill_step", "args_template")
