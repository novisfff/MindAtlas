"""add system locale and report content locale

Revision ID: a2b3c4d5e6f7
Revises: f1b2c3d4e5f7
Create Date: 2026-03-24 12:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_setting_key", "app_setting", ["key"], unique=True)

    op.add_column("weekly_report", sa.Column("content_locale", sa.String(length=8), nullable=True))
    op.add_column("monthly_report", sa.Column("content_locale", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("monthly_report", "content_locale")
    op.drop_column("weekly_report", "content_locale")
    op.drop_index("ix_app_setting_key", table_name="app_setting")
    op.drop_table("app_setting")
