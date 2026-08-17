from __future__ import annotations

import hashlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT_REVISION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "pre_ga_v1_0001_clean_baseline.py"


def test_plan4_revision_is_additive_and_root_is_untouched() -> None:
    config = Config(str(ROOT_REVISION.parents[2] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("pre_ga_v1_0002")
    assert revision.down_revision == "pre_ga_v1_0001"
    assert revision.module.branch_labels is None
    assert hashlib.sha256(ROOT_REVISION.read_bytes()).hexdigest() == _reviewed_root_digest()


def _reviewed_root_digest() -> str:
    # Captured before Task 7; the test only protects accidental edits to the
    # Plan 3 root while the additive revision evolves.
    return "61b6da16636244fbbff123b6c337e11735b22449d8b182706d4965d09fa74455"
