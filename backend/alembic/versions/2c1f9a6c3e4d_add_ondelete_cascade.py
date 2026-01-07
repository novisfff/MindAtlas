"""Add ON DELETE CASCADE for entry foreign keys

Revision ID: 2c1f9a6c3e4d
Revises: a7d8f1424a99
Create Date: 2026-01-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "2c1f9a6c3e4d"
down_revision = "a7d8f1424a99"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Relation -> Entry
    op.execute("ALTER TABLE relation DROP CONSTRAINT IF EXISTS relation_source_entry_id_fkey")
    op.execute("ALTER TABLE relation DROP CONSTRAINT IF EXISTS relation_target_entry_id_fkey")
    op.execute(
        "ALTER TABLE relation "
        "ADD CONSTRAINT relation_source_entry_id_fkey "
        "FOREIGN KEY (source_entry_id) REFERENCES entry (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE relation "
        "ADD CONSTRAINT relation_target_entry_id_fkey "
        "FOREIGN KEY (target_entry_id) REFERENCES entry (id) ON DELETE CASCADE"
    )

    # Attachment -> Entry
    op.execute("ALTER TABLE attachment DROP CONSTRAINT IF EXISTS attachment_entry_id_fkey")
    op.execute(
        "ALTER TABLE attachment "
        "ADD CONSTRAINT attachment_entry_id_fkey "
        "FOREIGN KEY (entry_id) REFERENCES entry (id) ON DELETE CASCADE"
    )

    # EntryTag -> Entry/Tag
    op.execute("ALTER TABLE entry_tag DROP CONSTRAINT IF EXISTS entry_tag_entry_id_fkey")
    op.execute("ALTER TABLE entry_tag DROP CONSTRAINT IF EXISTS entry_tag_tag_id_fkey")
    op.execute(
        "ALTER TABLE entry_tag "
        "ADD CONSTRAINT entry_tag_entry_id_fkey "
        "FOREIGN KEY (entry_id) REFERENCES entry (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE entry_tag "
        "ADD CONSTRAINT entry_tag_tag_id_fkey "
        "FOREIGN KEY (tag_id) REFERENCES tag (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE relation DROP CONSTRAINT IF EXISTS relation_source_entry_id_fkey")
    op.execute("ALTER TABLE relation DROP CONSTRAINT IF EXISTS relation_target_entry_id_fkey")
    op.execute(
        "ALTER TABLE relation "
        "ADD CONSTRAINT relation_source_entry_id_fkey "
        "FOREIGN KEY (source_entry_id) REFERENCES entry (id)"
    )
    op.execute(
        "ALTER TABLE relation "
        "ADD CONSTRAINT relation_target_entry_id_fkey "
        "FOREIGN KEY (target_entry_id) REFERENCES entry (id)"
    )

    op.execute("ALTER TABLE attachment DROP CONSTRAINT IF EXISTS attachment_entry_id_fkey")
    op.execute(
        "ALTER TABLE attachment "
        "ADD CONSTRAINT attachment_entry_id_fkey "
        "FOREIGN KEY (entry_id) REFERENCES entry (id)"
    )

    op.execute("ALTER TABLE entry_tag DROP CONSTRAINT IF EXISTS entry_tag_entry_id_fkey")
    op.execute("ALTER TABLE entry_tag DROP CONSTRAINT IF EXISTS entry_tag_tag_id_fkey")
    op.execute(
        "ALTER TABLE entry_tag "
        "ADD CONSTRAINT entry_tag_entry_id_fkey "
        "FOREIGN KEY (entry_id) REFERENCES entry (id)"
    )
    op.execute(
        "ALTER TABLE entry_tag "
        "ADD CONSTRAINT entry_tag_tag_id_fkey "
        "FOREIGN KEY (tag_id) REFERENCES tag (id)"
    )

