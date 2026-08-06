"""drop ai_provider is_active

Revision ID: 92af972bc123
Revises: 54da69779b4d
Create Date: 2026-01-22 01:04:38.347561

"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = '92af972bc123'
down_revision = '54da69779b4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop index first
    op.drop_index('uq_ai_provider_active_true', table_name='ai_provider')

    # Drop column
    op.drop_column('ai_provider', 'is_active')


def downgrade() -> None:
    # Add column back
    op.add_column('ai_provider', sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'))

    # Recreate index (PostgreSQL only, SQLite will ignore)
    try:
        op.create_index(
            'uq_ai_provider_active_true',
            'ai_provider',
            ['is_active'],
            unique=True,
            postgresql_where=sa.text('is_active')
        )
    except Exception:
        pass
