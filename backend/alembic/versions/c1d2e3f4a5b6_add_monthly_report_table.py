"""add_monthly_report_table

Revision ID: c1d2e3f4a5b6
Revises: 76a64b153e6f
Create Date: 2026-01-31 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = '76a64b153e6f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'monthly_report',
        sa.Column('month_start', sa.Date(), nullable=False),
        sa.Column('month_end', sa.Date(), nullable=False),
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
            "month_start = date_trunc('month', month_start)::date",
            name='ck_monthly_report_month_start_is_first_day',
        ),
        sa.CheckConstraint(
            "month_end = (month_start + interval '1 month' - interval '1 day')::date",
            name='ck_monthly_report_month_range',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_monthly_report_month_start'),
        'monthly_report',
        ['month_start'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_monthly_report_month_start'), table_name='monthly_report')
    op.drop_table('monthly_report')
