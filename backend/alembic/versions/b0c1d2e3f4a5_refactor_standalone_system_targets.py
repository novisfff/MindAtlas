"""refactor standalone system targets

Revision ID: b0c1d2e3f4a5
Revises: a3b4c5d6e7f8
Create Date: 2026-04-09
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.orm import Session


revision = "b0c1d2e3f4a5"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        from app.assistant_config.service import AssistantConfigService
        from app.openclaw_integration.service import OpenClawIntegrationService

        assistant = AssistantConfigService(session)
        assistant.sync_system_skills(commit=False)
        assistant.sync_standalone_system_targets(commit=False)
        assistant.ensure_system_behaviors(commit=False)

        openclaw = OpenClawIntegrationService(session)
        openclaw._ensure_system_items(commit=False)  # noqa: SLF001

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    pass
