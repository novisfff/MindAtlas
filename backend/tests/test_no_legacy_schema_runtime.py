from __future__ import annotations

import ast
from pathlib import Path

from app.database import Base
from app.schema.exclusions import LEGACY_TABLE_NAMES


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
CI_WORKFLOW = BACKEND_ROOT.parent / ".github" / "workflows" / "ci.yml"
FORBIDDEN_MODULE_PREFIX = "app.assistant.migration"
FORBIDDEN_TABLE_NAMES = set(LEGACY_TABLE_NAMES)


def _load_all_live_models() -> None:
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


def _absolute_import_base(
    path: Path,
    node: ast.ImportFrom,
    root: Path = BACKEND_ROOT,
) -> str:
    if node.level == 0:
        return node.module or ""
    relative = path.relative_to(root).with_suffix("")
    package = list(relative.parts[:-1])
    keep = len(package) - node.level + 1
    if keep < 0:
        return ""
    prefix = package[:keep]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _scan_python_imports(root: Path = BACKEND_ROOT) -> list[str]:
    violations: list[str] = []
    archive_root = root / "alembic" / "archive"
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if (
            path.is_relative_to(archive_root)
            or any(part.startswith(".venv") for part in relative.parts)
            or "__pycache__" in relative.parts
        ):
            continue
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = _absolute_import_base(path, node, root)
                if base:
                    candidates.append(base)
                candidates.extend(
                    f"{base}.{alias.name}" if base else alias.name
                    for alias in node.names
                )
            elif (
                isinstance(node, ast.Call)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                is_builtin_import = (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"
                )
                is_importlib_import = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                )
                if is_builtin_import or is_importlib_import:
                    candidates.append(node.args[0].value)
            if any(
                candidate == FORBIDDEN_MODULE_PREFIX
                or candidate.startswith(f"{FORBIDDEN_MODULE_PREFIX}.")
                for candidate in candidates
            ):
                violations.append(
                    f"{relative}:{node.lineno}"
                )
    return violations


def test_legacy_migration_package_is_absent() -> None:
    assert not (APP_ROOT / "assistant" / "migration").exists()
    assert not (BACKEND_ROOT / "tests" / "test_ai_runtime_destructive_migration.py").exists()


def test_live_metadata_has_no_legacy_tables() -> None:
    _load_all_live_models()
    assert FORBIDDEN_TABLE_NAMES.isdisjoint(Base.metadata.tables)


def test_source_and_tests_do_not_import_legacy_migration_package() -> None:
    assert _scan_python_imports() == []


def test_import_scan_rejects_literal_dynamic_imports(tmp_path: Path) -> None:
    source = tmp_path / "app" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import importlib\n"
        'importlib.import_module("app.assistant.migration.models")\n'
        '__import__("app.assistant.migration.repository")\n',
        encoding="utf-8",
    )

    assert _scan_python_imports(tmp_path) == [
        "app/service.py:2",
        "app/service.py:3",
    ]


def test_ci_does_not_execute_the_retired_plan10_upgrade_path() -> None:
    workflow = CI_WORKFLOW.read_text("utf-8")

    assert "full-postgres-backend:" not in workflow
    assert "alembic upgrade b6e2d4f8a901" not in workflow
    assert "alembic upgrade 9f3c1a7e2b40" not in workflow
    assert "alembic upgrade 027869a00a47" in workflow
    assert "MINDATLAS_PLAN10_" not in workflow
