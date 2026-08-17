"""Regression gates for test-order isolation and framework module identity."""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
import sys
import types

import pytest


BACKEND_ROOT = Path(__file__).parents[1]
FRAMEWORK_MODULES = {
    "fastapi",
    "fastapi.exceptions",
    "fastapi.responses",
    "starlette.requests",
    "starlette.exceptions",
    "starlette.status",
}
FRAMEWORK_SYMBOLS = {
    "fastapi": ("FastAPI",),
    "fastapi.exceptions": ("RequestValidationError",),
    "fastapi.responses": ("JSONResponse",),
    "starlette.requests": ("Request",),
    "starlette.exceptions": ("HTTPException",),
}


def test_known_streaming_tests_do_not_install_framework_stubs() -> None:
    for name in (
        "test_durable_run_streaming.py",
        "test_assistant_service_l1_summary.py",
        "test_assistant_service_no_outer_fallback.py",
    ):
        source = (BACKEND_ROOT / "tests" / name).read_text(encoding="utf-8")
        assert "_install_fastapi_stubs" not in source, name
        assert "types.ModuleType" not in source, name


def test_no_backend_test_assigns_lock_owned_framework_modules() -> None:
    violations: list[str] = []
    for path in sorted((BACKEND_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            if not isinstance(node.value, ast.Attribute) or node.value.attr != "modules":
                continue
            if not isinstance(node.value.value, ast.Name) or node.value.value.id != "sys":
                continue
            if isinstance(node.slice, ast.Constant) and node.slice.value in FRAMEWORK_MODULES:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}")
    assert violations == []


def test_scoped_modules_restores_modules_environment_and_app_imports_after_exception() -> None:
    from tests.scoped_modules import scoped_modules

    module_name = "optional_test_dependency"
    app_module_name = "app.scoped_optional_test.module"
    original_module = sys.modules.get(module_name)
    original_app_module = sys.modules.get(app_module_name)
    original_value = os.environ.get("MINDATLAS_SCOPED_TEST")
    replacement = types.ModuleType(module_name)
    replacement.marker = "temporary"

    try:
        with pytest.raises(RuntimeError, match="scope failure"):
            with scoped_modules(
                {module_name: replacement},
                app_module_roots=("app.scoped_optional_test",),
            ):
                os.environ["MINDATLAS_SCOPED_TEST"] = "temporary"
                sys.modules[app_module_name] = types.ModuleType(app_module_name)
                raise RuntimeError("scope failure")
    finally:
        if original_value is None:
            os.environ.pop("MINDATLAS_SCOPED_TEST", None)
        else:
            os.environ["MINDATLAS_SCOPED_TEST"] = original_value

    assert sys.modules.get(module_name) is original_module
    assert sys.modules.get(app_module_name) is original_app_module


def test_scoped_modules_rejects_lock_owned_replacements() -> None:
    from tests.scoped_modules import ScopedModulesError, scoped_modules

    with pytest.raises(ScopedModulesError, match="lock-owned"):
        with scoped_modules(
            {"fastapi": types.ModuleType("fastapi")},
            app_module_roots=(),
        ):
            pass


def test_order_regression_exposes_closed_modes_and_stable_order_digest() -> None:
    from scripts.run_test_order_regression import MODES, order_digest

    assert set(MODES) == {
        "streaming-then-tombstone",
        "tombstone-then-streaming",
        "isolated",
        "seeded",
    }
    forward = ("tests/test_durable_run_streaming.py", "tests/test_ai_runtime_legacy_cleanup.py")
    reverse = tuple(reversed(forward))
    assert order_digest(forward) == order_digest(forward)
    assert order_digest(forward) != order_digest(reverse)
    assert len(order_digest(forward)) == 64


def test_streaming_test_preserves_real_framework_module_identity() -> None:
    expected = {
        name: importlib.import_module(name)
        for name in sorted(FRAMEWORK_MODULES)
    }
    before = {
        name: (
            id(module),
            getattr(module, "__file__", None),
            tuple(id(getattr(module, symbol)) for symbol in FRAMEWORK_SYMBOLS.get(name, ())),
        )
        for name, module in expected.items()
    }
    result = pytest.main(["tests/test_durable_run_streaming.py", "-q"])
    assert result == pytest.ExitCode.OK
    after = {
        name: (
            id(sys.modules[name]),
            getattr(sys.modules[name], "__file__", None),
            tuple(id(getattr(sys.modules[name], symbol)) for symbol in FRAMEWORK_SYMBOLS.get(name, ())),
        )
        for name in sorted(FRAMEWORK_MODULES)
    }
    assert after == before
    assert all(expected[name].__file__ for name in expected)


def test_order_regression_uses_the_plan3_tombstone_test() -> None:
    source = (BACKEND_ROOT / "tests" / "test_ai_runtime_legacy_cleanup.py").read_text(
        encoding="utf-8"
    )
    assert "test_legacy_runtime_package_is_tombstoned" in source
    assert "cleanup_legacy" not in source


def test_authenticated_skill_client_overrides_settings_dependency_captured_by_old_callable() -> None:
    """A router imported under a temporary settings callable must still see the pin."""
    from fastapi import APIRouter, Depends

    from app.config import Settings
    from tests._bootstrap import reset_caches
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        operator_test_settings,
        restore_operator_settings,
    )

    reset_caches()
    stale_settings = operator_test_settings(APP_BUILD_REVISION="stale-settings")
    pinned_settings = operator_test_settings(APP_BUILD_REVISION="pinned-settings")

    def stale_get_settings() -> Settings:
        return stale_settings

    router = APIRouter(prefix="/test-order-settings")

    @router.get("/value")
    def settings_value(settings: Settings = Depends(stale_get_settings)) -> dict[str, str]:
        return {"buildRevision": settings.app_build_revision}

    db = make_session()
    client = None
    try:
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=pinned_settings,
        )
        response = client.get("/test-order-settings/value", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["buildRevision"] == "pinned-settings"
    finally:
        if client is not None:
            client.close()
        restore_operator_settings()
        db.close()
