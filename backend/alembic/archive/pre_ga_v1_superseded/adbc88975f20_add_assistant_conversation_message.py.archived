"""add_assistant_conversation_message

Revision ID: adbc88975f20
Revises: 7f0e6c9a1b2d
Create Date: 2026-01-10 10:09:22.969415

"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = 'adbc88975f20'
down_revision = '7f0e6c9a1b2d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('assistant_conversation',
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('is_archived', sa.Boolean(), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table('assistant_message',
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tool_calls', sa.JSON(), nullable=True),
        sa.Column('tool_results', sa.JSON(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['assistant_conversation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_assistant_message_conversation_id', 'assistant_message', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_assistant_message_conversation_id', table_name='assistant_message')
    op.drop_table('assistant_message')
    op.drop_table('assistant_conversation')
