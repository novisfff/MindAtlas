"""add workflow copilot model binding

Revision ID: e1a2b3c4d5e6
Revises: d9e0f1a2b3c4
Create Date: 2026-03-23 17:30:00.000000

"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e1a2b3c4d5e6"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_component_binding") as batch_op:
        batch_op.drop_constraint("ck_ai_component_binding_component", type_="check")
        batch_op.create_check_constraint(
            "ck_ai_component_binding_component",
            "component IN ('assistant','lightrag','workflow_copilot')",
        )

    conn = op.get_bind()
    existing = conn.execute(
        sa.text("SELECT 1 FROM ai_component_binding WHERE component = 'workflow_copilot' LIMIT 1")
    ).first()
    if existing:
        return

    assistant_binding = conn.execute(
        sa.text("SELECT llm_model_id FROM ai_component_binding WHERE component = 'assistant' LIMIT 1")
    ).first()
    assistant_llm_model_id = assistant_binding[0] if assistant_binding else None
    now = datetime.now(timezone.utc)

    conn.execute(
        sa.text(
            """
            INSERT INTO ai_component_binding (
                id,
                component,
                llm_model_id,
                embedding_model_id,
                created_at,
                updated_at
            ) VALUES (
                :id,
                'workflow_copilot',
                :llm_model_id,
                NULL,
                :now,
                :now
            )
            """
        ),
        {
            "id": str(uuid4()),
            "llm_model_id": str(assistant_llm_model_id) if assistant_llm_model_id else None,
            "now": now,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM ai_component_binding WHERE component = 'workflow_copilot'"))

    with op.batch_alter_table("ai_component_binding") as batch_op:
        batch_op.drop_constraint("ck_ai_component_binding_component", type_="check")
        batch_op.create_check_constraint(
            "ck_ai_component_binding_component",
            "component IN ('assistant','lightrag')",
        )
