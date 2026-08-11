from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_MIGRATE = ROOT / "deploy" / "migrate.sh"
ARCHIVE_MANIFEST = (
    ROOT
    / "backend"
    / "alembic"
    / "archive"
    / "pre_ga_v1_superseded"
    / "manifest.v1.json"
)


def test_ci_has_release_critical_clean_schema_job() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")

    assert workflow.count("  schema-release-critical:") == 1
    assert 'MINDATLAS_REQUIRE_SCHEMA_POSTGRES: "1"' in workflow
    assert "test_schema_equivalence_postgres.py" in workflow
    assert "archive_pre_ga_lineage.py --check" in workflow
    assert "MINDATLAS_PLAN10_" not in workflow
    assert "run_pre_ga_schema_exit_proof.py" in workflow
    assert "--proof-file" in workflow
    assert "pre-ga-schema-exit-evidence.json" in workflow
    assert "${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "docs/superpowers/evidence" not in workflow
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


def _string_value(
    node: ast.expr,
    values: dict[str, str],
    function_returns: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return function_returns.get(node.func.id)
    return None


def _archived_migration_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            value = _string_value(node.value, values, {})
            if value is not None:
                values[node.targets[0].id] = value

    function_returns: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
        if len(returns) != 1 or returns[0].value is None:
            continue
        value = _string_value(returns[0].value, values, function_returns)
        if value is not None:
            function_returns[node.name] = value

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            value = _string_value(node.value, values, function_returns)
            if value is not None:
                values[node.targets[0].id] = value

    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"upgrade", "downgrade"}
            and len(node.args) >= 2
        ):
            value = _string_value(node.args[1], values, function_returns)
            if value is not None:
                targets.add(value)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "_run_alembic"
            and len(node.args) >= 2
            and _string_value(node.args[0], values, function_returns)
            in {"upgrade", "downgrade"}
        ):
            value = _string_value(node.args[1], values, function_returns)
            if value is not None:
                targets.add(value)
    return targets


def test_live_tests_do_not_execute_archived_revisions() -> None:
    manifest = json.loads(ARCHIVE_MANIFEST.read_text(encoding="utf-8"))
    archived_ids = {entry["revision"] for entry in manifest["revisions"]}
    violations: list[str] = []
    for path in sorted((ROOT / "backend" / "tests").glob("test_*.py")):
        for revision in sorted(_archived_migration_targets(path) & archived_ids):
            violations.append(f"{path.name}: {revision}")

    assert (
        not violations
    ), "live test executes archived Alembic revision(s): " + ", ".join(violations)
