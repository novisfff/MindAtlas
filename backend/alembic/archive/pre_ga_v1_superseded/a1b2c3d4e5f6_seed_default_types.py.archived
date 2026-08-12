"""seed_default_entry_and_relation_types

Revision ID: a1b2c3d4e5f6
Revises: f7d3e4a5b6c8
Create Date: 2026-01-29

"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
import uuid


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f7d3e4a5b6c8"
branch_labels = None
depends_on = None


# Snapshot of default data (do not import from runtime code)
ENTRY_TYPES = [
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111001"),
        "code": "KNOWLEDGE",
        "name": "知识",
        "description": "学习的知识点",
        "color": "#3B82F6",
        "icon": "book",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111002"),
        "code": "PROJECT",
        "name": "项目",
        "description": "参与的项目",
        "color": "#10B981",
        "icon": "folder",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111003"),
        "code": "COMPETITION",
        "name": "比赛",
        "description": "参加的比赛",
        "color": "#F59E0B",
        "icon": "trophy",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111004"),
        "code": "EXPERIENCE",
        "name": "经历",
        "description": "个人经历",
        "color": "#8B5CF6",
        "icon": "star",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111005"),
        "code": "ACHIEVEMENT",
        "name": "成果",
        "description": "取得的成果",
        "color": "#EF4444",
        "icon": "award",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111006"),
        "code": "TECHNOLOGY",
        "name": "技术",
        "description": "掌握的技术",
        "color": "#06B6D4",
        "icon": "code",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111007"),
        "code": "DOCUMENT",
        "name": "资料",
        "description": "收集的资料",
        "color": "#6B7280",
        "icon": "file",
        "graph_enabled": True,
        "ai_enabled": False,
        "enabled": True,
    },
]

RELATION_TYPES = [
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222001"),
        "code": "BELONGS_TO",
        "name": "属于",
        "inverse_name": "包含",
        "description": "表示从属关系",
        "color": "#3B82F6",
        "directed": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222002"),
        "code": "USES",
        "name": "使用",
        "inverse_name": "被使用",
        "description": "表示使用关系",
        "color": "#10B981",
        "directed": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222003"),
        "code": "PARTICIPATES",
        "name": "参与",
        "inverse_name": "参与者",
        "description": "表示参与关系",
        "color": "#F59E0B",
        "directed": True,
        "enabled": True,
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222004"),
        "code": "RELATES_TO",
        "name": "关联",
        "inverse_name": "关联",
        "description": "表示一般关联",
        "color": "#8B5CF6",
        "directed": False,
        "enabled": True,
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222005"),
        "code": "DERIVES_FROM",
        "name": "派生自",
        "inverse_name": "派生出",
        "description": "表示派生关系",
        "color": "#EF4444",
        "directed": True,
        "enabled": True,
    },
]


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    conn = op.get_bind()

    # Insert entry_types (idempotent: ON CONFLICT DO NOTHING)
    for row in ENTRY_TYPES:
        conn.execute(
            sa.text(
                """
                INSERT INTO entry_type
                  (id, code, name, description, color, icon, graph_enabled, ai_enabled, enabled, created_at, updated_at)
                VALUES
                  (:id, :code, :name, :description, :color, :icon, :graph_enabled, :ai_enabled, :enabled, :created_at, :updated_at)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {**row, "created_at": now, "updated_at": now},
        )

    # Insert relation_types (idempotent: ON CONFLICT DO NOTHING)
    for row in RELATION_TYPES:
        conn.execute(
            sa.text(
                """
                INSERT INTO relation_type
                  (id, code, name, inverse_name, description, color, directed, enabled, created_at, updated_at)
                VALUES
                  (:id, :code, :name, :inverse_name, :description, :color, :directed, :enabled, :created_at, :updated_at)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {**row, "created_at": now, "updated_at": now},
        )


def downgrade() -> None:
    # Do not delete: users may have modified or referenced these types
    pass
