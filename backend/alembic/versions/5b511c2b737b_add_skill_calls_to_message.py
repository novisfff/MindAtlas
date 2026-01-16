"""add skill_calls to message

Revision ID: 5b511c2b737b
Revises: adbc88975f20
Create Date: 2026-01-13 17:10:21.073418

"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = '5b511c2b737b'
down_revision = 'adbc88975f20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assistant_message', sa.Column('skill_calls', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('assistant_message', 'skill_calls')
