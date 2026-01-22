"""add ai_registry tables

Revision ID: 54da69779b4d
Revises: e6c2f3a1d4b5
Create Date: 2026-01-22 00:24:51.813028

"""

from alembic import op
import sqlalchemy as sa
from uuid import uuid4
from datetime import datetime, timezone


# revision identifiers, used by Alembic.
revision = '54da69779b4d'
down_revision = 'e6c2f3a1d4b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 ai_credential 表
    op.create_table('ai_credential',
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('base_url', sa.String(length=2048), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False),
        sa.Column('api_key_hint', sa.String(length=64), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_credential_name'), 'ai_credential', ['name'], unique=True)

    # 2. 创建 ai_model 表
    op.create_table('ai_model',
        sa.Column('credential_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('model_type', sa.String(length=32), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("model_type IN ('llm','embedding')", name='ck_ai_model_type'),
        sa.ForeignKeyConstraint(['credential_id'], ['ai_credential.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ai_model_credential_type', 'ai_model', ['credential_id', 'model_type'], unique=False)
    op.create_index(op.f('ix_ai_model_credential_id'), 'ai_model', ['credential_id'], unique=False)
    op.create_index('uq_ai_model_credential_name_type', 'ai_model', ['credential_id', 'name', 'model_type'], unique=True)

    # 3. 创建 ai_component_binding 表
    op.create_table('ai_component_binding',
        sa.Column('component', sa.String(length=32), nullable=False),
        sa.Column('llm_model_id', sa.UUID(), nullable=True),
        sa.Column('embedding_model_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("component IN ('assistant','lightrag')", name='ck_ai_component_binding_component'),
        sa.ForeignKeyConstraint(['embedding_model_id'], ['ai_model.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['llm_model_id'], ['ai_model.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_component_binding_component'), 'ai_component_binding', ['component'], unique=True)

    # 4. 数据迁移: 从 ai_provider 迁移到新表
    conn = op.get_bind()

    # 检查 ai_provider 表是否存在且有数据
    result = conn.execute(sa.text("SELECT COUNT(*) FROM ai_provider"))
    count = result.scalar()

    if count and count > 0:
        # 迁移所有 ai_provider 到 ai_credential
        conn.execute(sa.text("""
            INSERT INTO ai_credential (id, name, base_url, api_key_encrypted, api_key_hint, created_at, updated_at)
            SELECT id, name, base_url, api_key_encrypted, api_key_hint, created_at, updated_at
            FROM ai_provider
        """))

        # 为每个 credential 创建一个 LLM 模型 (使用 Python UUID)
        providers = conn.execute(sa.text("SELECT id, model, created_at, updated_at FROM ai_provider")).fetchall()
        now = datetime.now(timezone.utc)
        for p in providers:
            model_id = str(uuid4())
            conn.execute(
                sa.text("INSERT INTO ai_model (id, credential_id, name, model_type, created_at, updated_at) VALUES (:id, :cred_id, :name, 'llm', :created, :updated)"),
                {"id": model_id, "cred_id": str(p[0]), "name": p[1], "created": p[2], "updated": p[3]}
            )

        # 为 is_active=true 的 provider 创建 assistant 和 lightrag 绑定
        active = conn.execute(sa.text("""
            SELECT m.id FROM ai_provider p
            JOIN ai_model m ON m.credential_id = p.id AND m.name = p.model
            WHERE p.is_active = true LIMIT 1
        """)).fetchone()

        if active:
            llm_model_id = str(active[0])
            # assistant binding
            conn.execute(
                sa.text("INSERT INTO ai_component_binding (id, component, llm_model_id, embedding_model_id, created_at, updated_at) VALUES (:id, 'assistant', :llm_id, NULL, :now, :now)"),
                {"id": str(uuid4()), "llm_id": llm_model_id, "now": now}
            )
            # lightrag binding (LLM only, embedding will be configured via UI)
            conn.execute(
                sa.text("INSERT INTO ai_component_binding (id, component, llm_model_id, embedding_model_id, created_at, updated_at) VALUES (:id, 'lightrag', :llm_id, NULL, :now, :now)"),
                {"id": str(uuid4()), "llm_id": llm_model_id, "now": now}
            )


def downgrade() -> None:
    # 删除新表 (按依赖顺序)
    op.drop_index(op.f('ix_ai_component_binding_component'), table_name='ai_component_binding')
    op.drop_table('ai_component_binding')
    op.drop_index('uq_ai_model_credential_name_type', table_name='ai_model')
    op.drop_index(op.f('ix_ai_model_credential_id'), table_name='ai_model')
    op.drop_index('idx_ai_model_credential_type', table_name='ai_model')
    op.drop_table('ai_model')
    op.drop_index(op.f('ix_ai_credential_name'), table_name='ai_credential')
    op.drop_table('ai_credential')
