"""add_weekly_report_table

Revision ID: 76a64b153e6f
Revises: a1b2c3d4e5f6
Create Date: 2026-01-30 17:36:47.039367

"""

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '76a64b153e6f'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('weekly_report',
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('week_end', sa.Date(), nullable=False),
        sa.Column('entry_count', sa.Integer(), nullable=True),
        sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "week_end = week_start + interval '6 days'",
            name='ck_weekly_report_week_range'
        ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        op.f('ix_weekly_report_week_start'),
        'weekly_report',
        ['week_start'],
        unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_weekly_report_week_start'), table_name='weekly_report')
    op.drop_table('weekly_report')
