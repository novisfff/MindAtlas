"""add_attachment_parse_columns

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-02-03

"""

from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attachment", sa.Column("index_to_knowledge_graph", sa.Boolean(), nullable=True, server_default="false"))
    op.add_column("attachment", sa.Column("parse_status", sa.String(length=20), nullable=True))
    op.add_column("attachment", sa.Column("parsed_text", sa.Text(), nullable=True))
    op.add_column("attachment", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("attachment", sa.Column("parse_last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("attachment", "parse_last_error")
    op.drop_column("attachment", "parsed_at")
    op.drop_column("attachment", "parsed_text")
    op.drop_column("attachment", "parse_status")
    op.drop_column("attachment", "index_to_knowledge_graph")
