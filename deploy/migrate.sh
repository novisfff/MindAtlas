#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${MINDATLAS_DEPLOYMENT_CLASS:?MINDATLAS_DEPLOYMENT_CLASS is required}"

case "$MINDATLAS_DEPLOYMENT_CLASS" in
  development|rehearsal|production) ;;
  *)
    echo "schema_deployment_class_invalid" >&2
    exit 64
    ;;
esac

echo "Checking database migration status..."
status="$(python scripts/schema_database_state.py --database-url-env DATABASE_URL)"

case "$status" in
  empty|versioned)
    alembic upgrade head
    ;;
  nonempty_unversioned)
    echo "unsupported_nonempty_unversioned_database" >&2
    exit 65
    ;;
  *)
    echo "schema_database_state_unknown" >&2
    exit 66
    ;;
esac

python scripts/verify_pre_ga_schema.py runtime --database-url-env DATABASE_URL
