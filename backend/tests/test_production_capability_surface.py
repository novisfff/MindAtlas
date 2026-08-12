"""Production Agent capability-surface closure tests.

These tests deliberately inspect every code-owned surface that can advertise or
route an Agent capability.  Human REST endpoints are intentionally checked
separately: closing the Agent surface must not remove normal authenticated
Entry/Relation product APIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant import tools as assistant_tools  # noqa: E402
from app.assistant.capabilities.classification import (  # noqa: E402
    SYSTEM_TOOL_CLASSIFICATIONS,
)
from app.assistant.runtime.seed import load_verified_assistant_system_seed  # noqa: E402
from app.assistant.workflow.system_assets.registry import list_system_assets  # noqa: E402
from app.assistant_config.registry import ToolRegistry  # noqa: E402
from app.openclaw_integration.registry import (  # noqa: E402
    list_openclaw_system_item_definitions,
)
from tests.test_route_auth_inventory import application_routes  # noqa: E402


SUPPORTED_PRODUCTION_WRITE_CAPABILITIES = {"create_entry"}
UNSUPPORTED = {
    "update_entry",
    "merge_entry",
    "create_relation",
    "relation_followup",
    "openclaw_create_relation",
}
WRITE_EFFECTS = {"write_local", "write_external"}
WORKFLOW_ASSET_DIRECTORY = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "assistant"
    / "workflow"
    / "system_assets"
    / "workflows"
)


@dataclass(frozen=True)
class AgentCapabilitySurfaces:
    provider_write_names: frozenset[str]
    tool_registry_write_names: frozenset[str]
    assistant_exports_write_names: frozenset[str]
    trusted_seed_write_names: frozenset[str]
    system_asset_write_names: frozenset[str]
    all_exposed_names: frozenset[str]


def _write_names(names: set[str]) -> set[str]:
    return {
        name
        for name in names
        if SYSTEM_TOOL_CLASSIFICATIONS.get(name, ("unknown", False))[0]
        in WRITE_EFFECTS
    }


def _walk_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        strings = set(value.keys())
        for nested in value.values():
            strings.update(_walk_strings(nested))
        return strings
    if isinstance(value, list):
        strings: set[str] = set()
        for nested in value:
            strings.update(_walk_strings(nested))
        return strings
    return set()


def _asset_tokens() -> set[str]:
    tokens: set[str] = set()
    for path in sorted(WORKFLOW_ASSET_DIRECTORY.glob("*.json")):
        tokens.add(path.stem)
        tokens.update(_walk_strings(json.loads(path.read_text(encoding="utf-8"))))
    for asset in list_system_assets():
        tokens.update(
            {
                asset.asset_key,
                asset.canonical_name,
                *asset.legacy_canonical_names,
            }
        )
    return tokens


def _unsupported_references(tokens: set[str]) -> set[str]:
    return {
        token
        for token in tokens
        if any(branch in token for branch in UNSUPPORTED)
    }


def collect_agent_capability_surfaces() -> AgentCapabilitySurfaces:
    runtime_names = set(ToolRegistry.list_runtime_system_tool_names())
    provider_names = {
        definition.name for definition in ToolRegistry.list_system_tool_definitions()
    }
    assistant_exports = set(getattr(assistant_tools, "_EXPORTS", {}))
    seed = load_verified_assistant_system_seed()
    seed_names = {binding.key for binding in seed.capability_bindings}
    asset_tokens = _asset_tokens()
    openclaw_tokens: set[str] = set()
    for definition in list_openclaw_system_item_definitions():
        openclaw_tokens.update(
            value
            for value in (
                definition.key,
                definition.tool_name,
                definition.source_tool_name,
                definition.workflow_asset_key,
            )
            if value
        )

    return AgentCapabilitySurfaces(
        provider_write_names=frozenset(_write_names(provider_names)),
        tool_registry_write_names=frozenset(_write_names(runtime_names)),
        assistant_exports_write_names=frozenset(_write_names(assistant_exports)),
        trusted_seed_write_names=frozenset(_write_names(seed_names)),
        system_asset_write_names=frozenset(_write_names(asset_tokens)),
        all_exposed_names=frozenset(
            runtime_names
            | provider_names
            | assistant_exports
            | seed_names
            | asset_tokens
            | openclaw_tokens
        ),
    )


def test_all_production_agent_surfaces_have_exactly_one_write() -> None:
    surfaces = collect_agent_capability_surfaces()

    assert _write_names(set(SYSTEM_TOOL_CLASSIFICATIONS)) == SUPPORTED_PRODUCTION_WRITE_CAPABILITIES
    assert surfaces.provider_write_names == SUPPORTED_PRODUCTION_WRITE_CAPABILITIES
    assert surfaces.tool_registry_write_names == SUPPORTED_PRODUCTION_WRITE_CAPABILITIES
    assert surfaces.assistant_exports_write_names == SUPPORTED_PRODUCTION_WRITE_CAPABILITIES
    assert surfaces.trusted_seed_write_names == SUPPORTED_PRODUCTION_WRITE_CAPABILITIES
    assert surfaces.system_asset_write_names == SUPPORTED_PRODUCTION_WRITE_CAPABILITIES
    assert UNSUPPORTED.isdisjoint(surfaces.all_exposed_names)
    assert not _unsupported_references(set(surfaces.all_exposed_names))


def test_human_rest_entry_and_relation_routes_remain_present() -> None:
    from app.main import app

    paths = {
        (route.path, frozenset(route.methods or set()))
        for route in application_routes(app)
    }

    assert any(path == "/api/entries/{id}" and "PUT" in methods for path, methods in paths)
    assert any(path == "/api/relations" and "POST" in methods for path, methods in paths)
