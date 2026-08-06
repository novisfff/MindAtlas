from __future__ import annotations

from pathlib import Path

import pytest

from scripts.schema_database_state import classify_database_state


DEPLOY_MIGRATE = Path(__file__).resolve().parents[2] / "deploy" / "migrate.sh"


class _FakeInspector:
    def __init__(self, tables: tuple[str, ...], versions: tuple[str, ...]):
        self.tables = tables
        self.versions = versions


@pytest.mark.parametrize(
    ("tables", "versions", "expected"),
    [
        ((), (), "empty"),
        (("assistant_chat_run",), ("pre_ga_v1_0001",), "versioned"),
        (("legacy_table",), (), "nonempty_unversioned"),
        ((), ("pre_ga_v1_0001", "second_head"), "unknown"),
    ],
)
def test_schema_database_state_is_fail_closed(
    tables: tuple[str, ...],
    versions: tuple[str, ...],
    expected: str,
) -> None:
    assert classify_database_state(tables, versions) == expected


def test_deploy_migrate_has_no_auto_stamp_and_requires_identity() -> None:
    source = DEPLOY_MIGRATE.read_text(encoding="utf-8")

    assert "alembic stamp" not in source
    assert "unsupported_nonempty_unversioned_database" in source
    assert "MINDATLAS_DEPLOYMENT_CLASS" in source
