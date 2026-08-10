from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_MIGRATE = ROOT / "deploy" / "migrate.sh"


def test_ci_has_release_critical_clean_schema_job() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")

    assert workflow.count("  schema-release-critical:") == 1
    assert 'MINDATLAS_REQUIRE_SCHEMA_POSTGRES: "1"' in workflow
    assert "test_schema_equivalence_postgres.py" in workflow
    assert "archive_pre_ga_lineage.py --check" in workflow
    assert "MINDATLAS_PLAN10_" not in workflow
    assert "run_pre_ga_schema_exit_proof.py" in workflow
    assert "--proof-file" in workflow
    for suite in (
        "tests/test_plan09_lifecycle_postgres.py",
        "tests/test_skill_eval_repository_postgres.py",
        "tests/test_agent_skill_admin_postgres_migration.py",
        "tests/test_main_agent_postgres_migration.py",
        "tests/test_durable_interrupt_repository_postgres.py",
        "tests/test_durable_run_events_postgres.py",
        "tests/test_schema_capture_postgres.py",
    ):
        assert suite in workflow


def test_deploy_migration_has_no_stamp_path() -> None:
    source = DEPLOY_MIGRATE.read_text(encoding="utf-8")

    assert "alembic stamp" not in source
    assert "unsupported_nonempty_unversioned_database" in source


def test_release_postgres_suites_do_not_execute_archived_lineage() -> None:
    backend_tests = ROOT / "backend" / "tests"
    release_suites = (
        "test_plan09_lifecycle_postgres.py",
        "test_skill_eval_repository_postgres.py",
        "test_agent_skill_admin_postgres_migration.py",
        "test_main_agent_postgres_migration.py",
        "test_durable_interrupt_repository_postgres.py",
        "test_durable_run_events_postgres.py",
        "test_schema_capture_postgres.py",
    )
    archived_ids = ("b6e2d4f8a901", "9f3c1a7e2b40", "3bd7bc4257c9")
    for name in release_suites:
        source = (backend_tests / name).read_text(encoding="utf-8")
        assert not any(revision in source for revision in archived_ids), name
        assert "MINDATLAS_PLAN10_B2_TEST_OVERRIDE" not in source, name
