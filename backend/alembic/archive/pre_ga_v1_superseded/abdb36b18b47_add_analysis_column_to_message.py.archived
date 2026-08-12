"""add_analysis_column_to_message

Revision ID: abdb36b18b47
Revises: 857c3f829d82
Create Date: 2026-01-15 14:42:25.147863

"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = 'abdb36b18b47'
down_revision = '857c3f829d82'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assistant_message', sa.Column('analysis', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('assistant_message', 'analysis')
