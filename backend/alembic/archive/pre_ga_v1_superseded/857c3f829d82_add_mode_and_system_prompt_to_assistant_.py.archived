"""add mode and system_prompt to assistant_skills

Revision ID: 857c3f829d82
Revises: d7b118d91830
Create Date: 2026-01-15 01:49:42.708911

"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = '857c3f829d82'
down_revision = 'd7b118d91830'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加 mode 列，先允许 NULL，设置默认值后再改为 NOT NULL
    op.add_column('assistant_skill', sa.Column('mode', sa.String(length=32), nullable=True))
    op.add_column('assistant_skill', sa.Column('system_prompt', sa.Text(), nullable=True))

    # 为现有记录设置默认值
    op.execute("UPDATE assistant_skill SET mode = 'steps' WHERE mode IS NULL")

    # 将 mode 列改为 NOT NULL
    op.alter_column('assistant_skill', 'mode', nullable=False)


def downgrade() -> None:
    op.drop_column('assistant_skill', 'system_prompt')
    op.drop_column('assistant_skill', 'mode')
