"""Add ai_provider table

Revision ID: 7f0e6c9a1b2d
Revises: 2c1f9a6c3e4d
Create Date: 2026-01-09
"""

from alembic import op
import sqlalchemy as sa


revision = "7f0e6c9a1b2d"
down_revision = "2c1f9a6c3e4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_provider_name"), "ai_provider", ["name"], unique=True
    )
    op.create_index(
        "uq_ai_provider_active_true",
        "ai_provider",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_provider_active_true", table_name="ai_provider")
    op.drop_index(op.f("ix_ai_provider_name"), table_name="ai_provider")
    op.drop_table("ai_provider")
