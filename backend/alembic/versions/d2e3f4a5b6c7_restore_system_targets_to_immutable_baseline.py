"""restore system workflows and agents to immutable baselines

Revision ID: d2e3f4a5b6c7
Revises: c6d7e8f9a0b1
Create Date: 2026-03-30 20:30:00.000000

Historically this migration imported AssistantConfigService / OpenClawIntegrationService
to re-sync system targets. That is unsafe on empty-DB upgrades: current service code
queries columns (e.g. folder_id) that land in later revisions, so alembic upgrade head
on a fresh PostgreSQL 15 database would fail mid-chain.

System skill/workflow/agent baselines are reconciled at application bootstrap /
catalog warm instead. Keep this revision as a safe no-op so the migration graph
remains linear.
"""

from __future__ import annotations

from alembic import op  # noqa: F401  — retained for Alembic discovery consistency
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "d2e3f4a5b6c7"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: system target restore is handled by bootstrap / warm sync.

    Optionally inspects whether assistant tables exist so operators can see that
    the revision intentionally skipped service imports.
    """
    bind = op.get_bind()
    inspector = inspect(bind)
    # Soft guard only — never import application services here.
    _ = inspector.has_table("assistant_skill")


def downgrade() -> None:
    pass
