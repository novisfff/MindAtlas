"""refactor standalone system targets

Revision ID: b0c1d2e3f4a5
Revises: a3b4c5d6e7f8
Create Date: 2026-04-09

Historically this migration imported AssistantConfigService / OpenClawIntegrationService
to re-sync system targets. That is unsafe on empty-DB upgrades: current service code
queries columns (e.g. folder_id) that land in later revisions (a7b8c9d0e1f2), so
`alembic upgrade head` on a fresh PostgreSQL database fails mid-chain.

System skill/workflow/agent baselines are reconciled at application bootstrap /
catalog warm instead. Keep this revision as a safe no-op so the migration graph
remains linear and Plan 01's empty-DB migration gate can run.
"""

from __future__ import annotations

from alembic import op  # noqa: F401 — retained for Alembic discovery consistency
from sqlalchemy import inspect


revision = "b0c1d2e3f4a5"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: standalone system target refactor is handled by bootstrap / warm sync.

    Soft-inspect only — never import application services here.
    """
    bind = op.get_bind()
    inspector = inspect(bind)
    _ = inspector.has_table("assistant_skill")


def downgrade() -> None:
    pass
