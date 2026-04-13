from __future__ import annotations

from functools import lru_cache

from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME, SkillDefinition, SkillKBConfig
from app.assistant.workflow.system_assets import (
    get_system_skill_asset,
    list_system_assets,
    load_system_agent_asset,
    load_system_workflow_asset,
)


def _collect_workflow_tools(workflow_input) -> list[str]:
    tools: list[str] = []
    seen: set[str] = set()
    for node in (workflow_input.nodes or []):
        if str(getattr(node, "node_type", "") or "") != "tool":
            continue
        config = node.config if isinstance(node.config, dict) else {}
        tool_name = str(config.get("toolName", config.get("tool_name", "")) or "").strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        tools.append(tool_name)
    return tools


def _build_skill_definition(asset, *, locale: str) -> SkillDefinition:
    if asset.kind == "workflow":
        workflow_input = load_system_workflow_asset(asset.asset_key, locale=locale)
        return SkillDefinition(
            name=asset.skill_name or asset.asset_key,
            description=asset.description,
            intent_examples=list(asset.skill_intent_examples),
            tools=_collect_workflow_tools(workflow_input),
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            system_prompt=None,
            kb=None,
            workflow_nodes=[
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "label": node.label,
                    "position_x": node.position_x,
                    "position_y": node.position_y,
                    "config": node.config or {},
                }
                for node in (workflow_input.nodes or [])
            ],
            workflow_edges=[
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "source_handle": edge.source_handle,
                    "target_handle": edge.target_handle,
                    "condition_type": edge.condition_type,
                    "condition_expr": edge.condition_expr.model_dump() if edge.condition_expr else None,
                    "label": edge.label,
                }
                for edge in (workflow_input.edges or [])
            ],
        )

    draft = load_system_agent_asset(asset.asset_key, locale=locale)
    raw_model_id = getattr(draft, "model_id", None)
    model_id = str(raw_model_id) if raw_model_id is not None else None
    return SkillDefinition(
        name=asset.skill_name or asset.asset_key,
        description=asset.description,
        intent_examples=list(asset.skill_intent_examples),
        tools=[str(tool) for tool in (draft.tools or []) if str(tool).strip()],
        mode="langgraph",
        langgraph_pattern="agent_loop",
        model_source=str(draft.model_source or "default"),
        model_id=model_id,
        system_prompt=draft.system_prompt,
        kb=SkillKBConfig(enabled=bool((draft.kb_config or {}).get("enabled", False))),
        workflow_nodes=[],
        workflow_edges=[],
    )


@lru_cache(maxsize=4)
def _load_system_skill_defaults_cached(locale: str) -> tuple[SkillDefinition, ...]:
    assets = list_system_assets(usage_tag="skill_default", locale=locale)
    defaults = tuple(
        _build_skill_definition(asset, locale=locale)
        for asset in assets
        if asset.skill_name
    )
    if not any(item.name == DEFAULT_SKILL_NAME for item in defaults):
        raise RuntimeError(f"System defaults missing required fallback skill: {DEFAULT_SKILL_NAME}")
    return defaults


def load_system_skill_defaults(locale: str | None = None) -> list[SkillDefinition]:
    from app.system_settings.service import get_default_system_locale, normalize_system_locale

    normalized_locale = normalize_system_locale(locale) or get_default_system_locale()
    return list(_load_system_skill_defaults_cached(normalized_locale))


def get_system_skill_default(name: str, locale: str | None = None) -> SkillDefinition | None:
    normalized = str(name or "").strip()
    if not normalized:
        return None
    for item in load_system_skill_defaults(locale=locale):
        if item.name == normalized:
            return item
    return None


def get_system_workflow_baseline(name: str, locale: str | None = None):
    asset = get_system_skill_asset(name, locale=locale)
    if asset is None or asset.kind != "workflow":
        return None
    return load_system_workflow_asset(asset.asset_key, locale=locale)


def get_system_agent_baseline(name: str, locale: str | None = None):
    asset = get_system_skill_asset(name, locale=locale)
    if asset is None or asset.kind != "agent":
        return None
    return load_system_agent_asset(asset.asset_key, locale=locale)


def clear_system_defaults_cache() -> None:
    _load_system_skill_defaults_cached.cache_clear()
