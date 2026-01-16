#!/bin/sh
set -e

echo "Checking database migration status..."

# Check if alembic_version table exists
if alembic current 2>/dev/null | grep -q "head"; then
    echo "Database is up to date."
    exit 0
fi

# Check if tables exist but no alembic version (existing DB without migrations)
TABLES=$(python -c "
from sqlalchemy import create_engine, inspect
import os
engine = create_engine(os.environ['DATABASE_URL'])
inspector = inspect(engine)
tables = inspector.get_table_names()
print(len([t for t in tables if t not in ['alembic_version']]))
")

if [ "$TABLES" -gt "0" ]; then
    echo "Existing tables found. Stamping current migration..."
    alembic stamp head
    echo "Database stamped to head."
else
    echo "Empty database. Running migrations..."
    alembic upgrade head
    echo "Migrations completed."
fi
