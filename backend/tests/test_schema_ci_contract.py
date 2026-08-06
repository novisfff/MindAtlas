from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_MIGRATE = ROOT / "deploy" / "migrate.sh"


def test_ci_has_release_critical_clean_schema_job() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")

    assert 'MINDATLAS_REQUIRE_SCHEMA_POSTGRES: "1"' in workflow
    assert "test_schema_equivalence_postgres.py" in workflow
    assert "archive_pre_ga_lineage.py --check" in workflow
    assert "MINDATLAS_PLAN10_" not in workflow


def test_deploy_migration_has_no_stamp_path() -> None:
    source = DEPLOY_MIGRATE.read_text(encoding="utf-8")

    assert "alembic stamp" not in source
    assert "unsupported_nonempty_unversioned_database" in source
