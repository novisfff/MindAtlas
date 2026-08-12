"""Unsupported Agent write boundaries must stop before every side effect."""

from __future__ import annotations

import ast
import inspect

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


@pytest.fixture(autouse=True)
def _clear_unsupported_attempt_metrics() -> None:
    from app.assistant.capabilities.supported_writes import (
        clear_unsupported_write_attempts_for_tests,
    )

    clear_unsupported_write_attempts_for_tests()
    yield
    clear_unsupported_write_attempts_for_tests()


def _assert_no_database_access(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError("unsupported Agent boundary must not access a database")


@pytest.mark.parametrize(
    ("branch", "call"),
    [
        (
            "update_entry",
            lambda: __import__(
                "app.assistant.tools.entry_tools", fromlist=["update_entry"]
            ).update_entry("not-an-entry-id"),
        ),
        (
            "create_relation",
            lambda: __import__(
                "app.assistant.tools.relation_tools", fromlist=["create_relation"]
            ).create_relation("source", "target", "related"),
        ),
    ],
)
def test_retained_direct_agent_boundaries_create_nothing(
    monkeypatch: pytest.MonkeyPatch,
    branch: str,
    call,
) -> None:
    from app.assistant.capabilities.supported_writes import (
        CapabilityNotSupported,
        unsupported_write_attempt_snapshot,
    )
    from app.assistant.tools import entry_tools

    monkeypatch.setattr(entry_tools, "_get_db", _assert_no_database_access)

    with pytest.raises(CapabilityNotSupported) as exc:
        call()

    assert exc.value.error.safe_code == "capability_not_supported"
    assert exc.value.error.error_type == "unsupported"
    assert exc.value.branch == branch
    assert unsupported_write_attempt_snapshot() == {
        (branch, "direct_agent_boundary"): 1,
    }


@pytest.mark.parametrize("branch", ["merge_entry", "relation_followup"])
def test_unimplemented_unsupported_branches_terminate_before_side_effect(
    branch: str,
) -> None:
    from app.assistant.capabilities.supported_writes import (
        CapabilityNotSupported,
        unsupported_write_attempt_snapshot,
        unsupported_write_boundary,
    )

    with pytest.raises(CapabilityNotSupported) as exc:
        unsupported_write_boundary(branch, "direct_agent_boundary")

    assert exc.value.error.safe_code == "capability_not_supported"
    assert exc.value.branch == branch
    assert unsupported_write_attempt_snapshot() == {
        (branch, "direct_agent_boundary"): 1,
    }


def test_unsupported_boundaries_are_not_provider_tools() -> None:
    from app.assistant.tools import entry_tools, relation_tools

    for module, function_name in (
        (entry_tools, "update_entry"),
        (relation_tools, "create_relation"),
    ):
        tree = ast.parse(inspect.getsource(module))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        assert not any(
            isinstance(decorator, ast.Name) and decorator.id == "tool"
            or isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "tool"
            for decorator in function.decorator_list
        )
        assert not hasattr(getattr(module, function_name), "invoke")


def test_unsupported_names_are_not_runtime_registry_entries() -> None:
    from app.assistant_config.registry import ToolRegistry

    names = set(ToolRegistry.list_runtime_system_tool_names())
    assert {"update_entry", "create_relation", "openclaw_create_relation"}.isdisjoint(names)
    for name in ("update_entry", "merge_entry", "create_relation", "relation_followup"):
        assert ToolRegistry.resolve_system_tool(name) is None


def test_forged_frozen_unsupported_binding_stops_before_registry_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale/fabricated old Agent write can never degrade to a normal lookup."""
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.supported_writes import (
        CapabilityNotSupported,
        unsupported_write_attempt_snapshot,
    )
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver
    from tests._db import make_session

    db = make_session()
    try:
        resolved = CapabilityReferenceResolver(db).resolve_many(
            (CapabilityDeclaration(type="tool", key="search_entries"),)
        )[0]
        from app.assistant.capabilities.contracts import (
            FrozenBindingProvenance,
            project_frozen_capability_binding,
        )

        trusted = project_frozen_capability_binding(
            resolved=resolved,
            provenance=FrozenBindingProvenance(
                origin="test",
                binding_row_id=None,
                owner_version_id=None,
                source_snapshot_digest="a" * 64,
            ),
        )
        forged_ref = trusted.ref.model_copy(
            update={
                "capability_key": "update_entry",
                "target_identity": "system-tool:update_entry",
            }
        )
        forged_resolved = trusted.resolved.model_copy(
            update={
                "capability_key": "update_entry",
                "target_identity": "system-tool:update_entry",
            }
        )
        forged = trusted.model_copy(update={"ref": forged_ref, "resolved": forged_resolved})

        monkeypatch.setattr(db, "query", _assert_no_database_access)

        with pytest.raises(CapabilityNotSupported) as exc:
            CapabilityRegistry(db).resolve_surface(forged)

        assert exc.value.error.safe_code == "capability_not_supported"
        assert exc.value.error.error_type == "unsupported"
        assert unsupported_write_attempt_snapshot() == {
            ("update_entry", "capability_registry"): 1,
        }
    finally:
        db.close()
