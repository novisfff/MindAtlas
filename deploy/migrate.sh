#!/bin/sh
set -e

echo "Checking database migration status..."

# Check if alembic_version table exists
HAS_ALEMBIC_VERSION=$(python -c "
from sqlalchemy import create_engine, inspect
import os
engine = create_engine(os.environ['DATABASE_URL'])
inspector = inspect(engine)
print('1' if 'alembic_version' in inspector.get_table_names() else '0')
")

# Check if business tables exist (excluding alembic_version)
NON_EMPTY_TABLES=$(python -c "
from sqlalchemy import create_engine, inspect
import os
engine = create_engine(os.environ['DATABASE_URL'])
inspector = inspect(engine)
tables = [t for t in inspector.get_table_names() if t != 'alembic_version']
print(len(tables))
")

if [ "$HAS_ALEMBIC_VERSION" = "1" ]; then
    # alembic_version exists: always run upgrade (idempotent)
    echo "alembic_version exists, running upgrade..."
    alembic upgrade head
    echo "Migrations completed."
elif [ "$NON_EMPTY_TABLES" -gt "0" ]; then
    # Legacy DB: tables exist but no alembic_version
    echo "Existing tables found but no alembic_version. Stamping head..."
    alembic stamp head
    echo "Database stamped to head."
else
    # Empty database: run full migrations
    echo "Empty database. Running migrations..."
    alembic upgrade head
    echo "Migrations completed."
fi
