"""add tool config fields

Revision ID: d7b118d91830
Revises: 9c0b8c3a1d2e
Create Date: 2026-01-14 22:27:28.731031

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7b118d91830'
down_revision = '9c0b8c3a1d2e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assistant_tool', sa.Column('input_params', sa.JSON(), nullable=True))
    op.add_column('assistant_tool', sa.Column('query_params', sa.JSON(), nullable=True))
    op.add_column('assistant_tool', sa.Column('body_type', sa.String(length=32), nullable=True))
    op.add_column('assistant_tool', sa.Column('body_content', sa.Text(), nullable=True))
    op.add_column('assistant_tool', sa.Column('auth_type', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('assistant_tool', 'auth_type')
    op.drop_column('assistant_tool', 'body_content')
    op.drop_column('assistant_tool', 'body_type')
    op.drop_column('assistant_tool', 'query_params')
    op.drop_column('assistant_tool', 'input_params')
