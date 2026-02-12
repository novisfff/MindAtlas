"""add_langgraph_pattern_column

Revision ID: f3a4b5c6d7e8
Revises: b9a1c0d2e3f4
Create Date: 2026-02-11

"""

from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = "b9a1c0d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_skill",
        sa.Column("langgraph_pattern", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assistant_skill", "langgraph_pattern")
