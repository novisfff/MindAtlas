"""restore system workflows and agents to immutable baselines

Revision ID: d2e3f4a5b6c7
Revises: c6d7e8f9a0b1
Create Date: 2026-03-30 20:30:00.000000

"""

from __future__ import annotations

from alembic import op
from sqlalchemy.orm import Session


# revision identifiers, used by Alembic.
revision = "d2e3f4a5b6c7"
down_revision = "c6d7e8f9a0b1"
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
