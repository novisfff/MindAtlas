from __future__ import annotations

from pathlib import Path

from app.schema.contracts import CLEAN_ROOT_REVISION, NEXT_RESERVED_REVISION
from app.schema.exclusions import (
    LEGACY_FUNCTION_KEYS,
    LEGACY_TABLE_NAMES,
    PLAN10_IMMUTABLE_TABLES,
    PLAN10_UPDATE_ONLY_TABLES,
    expected_legacy_object_keys,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def test_legacy_source_allowlist_is_exact() -> None:
    keys = expected_legacy_object_keys()

    assert len(LEGACY_TABLE_NAMES) == 11
    assert len(LEGACY_FUNCTION_KEYS) == 1
    assert len(PLAN10_IMMUTABLE_TABLES) == 7
    assert len(PLAN10_UPDATE_ONLY_TABLES) == 1
    assert len(keys) == 27
    assert len(set(keys)) == 27
    assert all("*" not in part for key in keys for part in key)
    assert all(not part.endswith("_") for key in keys for part in key if part)


def test_legacy_source_allowlist_has_only_declared_object_kinds() -> None:
    keys = expected_legacy_object_keys()
    assert {key[0] for key in keys} == {"function", "table", "trigger"}
    assert all(key[1] == "public" for key in keys)
    assert sum(key[0] == "table" for key in keys) == 11
    assert sum(key[0] == "function" for key in keys) == 1
    assert sum(key[0] == "trigger" for key in keys) == 15
    assert keys == tuple(sorted(keys))


def test_trigger_keys_are_bound_to_exact_table_names() -> None:
    trigger_keys = [key for key in expected_legacy_object_keys() if key[0] == "trigger"]
    expected_targets = set(PLAN10_IMMUTABLE_TABLES) | set(PLAN10_UPDATE_ONLY_TABLES)

    assert {key[3] for key in trigger_keys} == expected_targets
    assert all(key[2].startswith(f"trg_{key[3]}_reject_") for key in trigger_keys)


def test_plan4_revision_is_reserved_not_live() -> None:
    assert NEXT_RESERVED_REVISION != CLEAN_ROOT_REVISION
    assert list(ALEMBIC_VERSIONS.glob(f"{NEXT_RESERVED_REVISION}*.py")) == []
