from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure `backend/` is on sys.path so `import app.*` works when running Alembic.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import get_settings  # noqa: E402
from app.database import Base  # noqa: E402

# Import all models to ensure they are registered with Base.metadata
from app.entry_type.models import EntryType  # noqa: E402, F401
from app.tag.models import Tag  # noqa: E402, F401
from app.entry.models import Entry, entry_tag  # noqa: E402, F401
from app.relation.models import Relation, RelationType  # noqa: E402, F401
from app.attachment.models import Attachment  # noqa: E402, F401
from app.ai_provider.models import AiProvider  # noqa: E402, F401
from app.assistant.models import Conversation, Message  # noqa: E402, F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_uri())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
