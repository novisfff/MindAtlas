"""Classify a database for the clean-only deployment migration path.

The classifier is deliberately read-only and emits only a bounded enum.  It
does not infer compatibility from a particular business table and never
includes connection details or catalog names in its output.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from sqlalchemy import create_engine, inspect, text


DATABASE_STATES = frozenset(
    {"empty", "versioned", "nonempty_unversioned", "unknown"}
)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def classify_database_state(
    tables: tuple[str, ...] | list[str],
    versions: tuple[str, ...] | list[str],
) -> str:
    """Return the only deployment state permitted by the migration script."""

    table_names = tuple(str(name) for name in tables if str(name) != "alembic_version")
    revision_values = tuple(str(value) for value in versions)
    if not revision_values and not table_names:
        return "empty"
    if len(revision_values) == 1:
        return "versioned"
    if not revision_values and table_names:
        return "nonempty_unversioned"
    return "unknown"


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _database_state(url: str) -> str:
    engine = create_engine(_sqlalchemy_url(url), future=True, pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        tables = tuple(inspector.get_table_names(schema="public"))
        if "alembic_version" not in tables:
            versions: tuple[str, ...] = ()
        else:
            with engine.connect() as connection:
                versions = tuple(
                    str(value)
                    for value in connection.execute(
                        text(
                            'SELECT version_num FROM "public"."alembic_version" '
                            "ORDER BY version_num"
                        )
                    ).scalars()
                )
        return classify_database_state(tables, versions)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", required=True)
    args = parser.parse_args(argv)
    if _ENV_NAME.fullmatch(args.database_url_env) is None:
        print("unknown", file=sys.stdout)
        return 0
    url = os.environ.get(args.database_url_env, "").strip()
    if not url:
        print("unknown", file=sys.stdout)
        return 0
    try:
        state = _database_state(url)
    except Exception:
        state = "unknown"
    print(state, file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
