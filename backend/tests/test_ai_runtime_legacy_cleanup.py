"""Tombstone and import-order guard for the retired Legacy runtime."""

from __future__ import annotations

from pathlib import Path

import fastapi
import starlette

from app.database import Base
from app.schema.exclusions import LEGACY_TABLE_NAMES


APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _load_live_metadata() -> None:
    import app.ai_provider.models  # noqa: F401
    import app.ai_registry.models  # noqa: F401
    import app.assistant.models  # noqa: F401
    import app.assistant.capability_calls.models  # noqa: F401
    import app.assistant.durable.models  # noqa: F401
    import app.assistant.evaluation.models  # noqa: F401
    import app.assistant.runtime.models  # noqa: F401
    import app.assistant.skills.models  # noqa: F401
    import app.assistant_config.models  # noqa: F401
    import app.attachment.models  # noqa: F401
    import app.entry.models  # noqa: F401
    import app.entry_type.models  # noqa: F401
    import app.lightrag.models  # noqa: F401
    import app.openclaw_integration.models  # noqa: F401
    import app.operator_auth.models  # noqa: F401
    import app.relation.models  # noqa: F401
    import app.report.models  # noqa: F401
    import app.system_settings.models  # noqa: F401
    import app.tag.models  # noqa: F401


def test_legacy_runtime_package_is_tombstoned() -> None:
    assert not (APP_ROOT / "assistant" / "migration").exists()


def test_legacy_tables_are_absent_from_live_metadata() -> None:
    _load_live_metadata()
    assert set(LEGACY_TABLE_NAMES).isdisjoint(Base.metadata.tables)


def test_framework_imports_remain_real_after_tombstone() -> None:
    assert Path(fastapi.__file__).is_file()
    assert Path(starlette.__file__).is_file()
