from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator

from app.assistant.skill_catalog.base import (
    DEFAULT_SKILL_NAME,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)
from app.system_settings.service import get_default_system_locale, normalize_system_locale


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class SystemDefaultSkillEntry(_CamelModel):
    name: str = Field(min_length=1)
    description: str = ""
    intent_examples: list[str] = Field(default_factory=list, alias="intentExamples")
    target_type: Literal["workflow", "agent"] = Field(alias="targetType")
    preset_file: str = Field(min_length=1, alias="presetFile")
    hidden: bool = False


class _WorkflowNodePreset(_CamelModel):
    node_id: str = Field(alias="nodeId")
    node_type: str = Field(alias="nodeType")
    label: str = ""
    position_x: float = Field(alias="positionX")
    position_y: float = Field(alias="positionY")
    config: dict = Field(default_factory=dict)


class _WorkflowEdgePreset(_CamelModel):
    edge_id: str = Field(alias="edgeId")
    source_node_id: str = Field(alias="sourceNodeId")
    target_node_id: str = Field(alias="targetNodeId")
    source_handle: str = Field(default="output", alias="sourceHandle")
    target_handle: str = Field(default="input", alias="targetHandle")
    condition_type: str | None = Field(default=None, alias="conditionType")
    condition_expr: dict | None = Field(default=None, alias="conditionExpr")
    label: str | None = None


class SystemDefaultWorkflowPreset(_CamelModel):
    nodes: list[_WorkflowNodePreset] = Field(default_factory=list)
    edges: list[_WorkflowEdgePreset] = Field(default_factory=list)
    viewport: dict | None = None


class SystemDefaultAgentPreset(_CamelModel):
    system_prompt: str = Field(alias="systemPrompt")
    tools: list[str] = Field(default_factory=list)
    kb_config: dict = Field(default_factory=dict, alias="kbConfig")
    model_source: Literal["default", "custom"] = Field(default="default", alias="modelSource")
    model_id: str | None = Field(default=None, alias="modelId")

    @model_validator(mode="after")
    def _validate_model_binding(self) -> "SystemDefaultAgentPreset":
        if self.model_source == "default":
            self.model_id = None
            return self
        if not (self.model_id or "").strip():
            raise ValueError("modelId is required when modelSource=custom")
        return self


class SystemDefaultsManifest(_CamelModel):
    schema_version: int = Field(alias="schemaVersion")
    skills: list[SystemDefaultSkillEntry] = Field(default_factory=list)


def _defaults_dir() -> Path:
    return Path(__file__).resolve().parent / "system_defaults"


def _normalize_defaults_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


def _read_json_file(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"System defaults JSON not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"System defaults JSON unreadable: {path}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"System defaults JSON invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"System defaults JSON root must be object: {path}")
    return payload


def _resolve_preset_path(base_dir: Path, preset_file: str) -> Path:
    path = (base_dir / preset_file).resolve()
    try:
        path.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(f"System defaults preset path escapes base dir: {preset_file}") from exc
    return path


def _collect_workflow_tools(nodes: list[_WorkflowNodePreset]) -> list[str]:
    tools: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if node.node_type != "tool":
            continue
        tool_name = str(node.config.get("toolName", "")).strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        tools.append(tool_name)
    return tools


def _build_skill_definition(
    *,
    entry: SystemDefaultSkillEntry,
    workflow_preset: SystemDefaultWorkflowPreset | None,
    agent_preset: SystemDefaultAgentPreset | None,
) -> SkillDefinition:
    if entry.target_type == "workflow":
        if workflow_preset is None:
            raise RuntimeError(f"Workflow preset missing for skill: {entry.name}")
        return SkillDefinition(
            name=entry.name,
            description=entry.description,
            intent_examples=list(entry.intent_examples),
            tools=_collect_workflow_tools(workflow_preset.nodes),
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            system_prompt=None,
            kb=None,
            workflow_nodes=[
                WorkflowNodeDefinition(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    label=node.label,
                    position_x=node.position_x,
                    position_y=node.position_y,
                    config=node.config or {},
                )
                for node in workflow_preset.nodes
            ],
            workflow_edges=[
                WorkflowEdgeDefinition(
                    edge_id=edge.edge_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    source_handle=edge.source_handle,
                    target_handle=edge.target_handle,
                    condition_type=edge.condition_type,
                    condition_expr=edge.condition_expr,
                    label=edge.label,
                )
                for edge in workflow_preset.edges
            ],
        )

    if agent_preset is None:
        raise RuntimeError(f"Agent preset missing for skill: {entry.name}")
    return SkillDefinition(
        name=entry.name,
        description=entry.description,
        intent_examples=list(entry.intent_examples),
        tools=[tool for tool in (agent_preset.tools or []) if str(tool).strip()],
        mode="langgraph",
        langgraph_pattern="agent_loop",
        model_source=agent_preset.model_source,
        model_id=agent_preset.model_id,
        system_prompt=agent_preset.system_prompt,
        kb=SkillKBConfig(enabled=bool((agent_preset.kb_config or {}).get("enabled", False))),
        workflow_nodes=[],
        workflow_edges=[],
    )


def _load_system_skill_defaults_from_dir(base_dir: Path, locale: str | None = None) -> list[SkillDefinition]:
    locale = _normalize_defaults_locale(locale)
    manifest_path = base_dir / f"manifest.{locale}.json"
    if locale == "zh" and not manifest_path.exists():
        manifest_path = base_dir / "manifest.json"
    manifest_payload = _read_json_file(manifest_path)
    manifest = SystemDefaultsManifest.model_validate(manifest_payload)
    if manifest.schema_version != 1:
        raise RuntimeError(f"Unsupported system defaults schemaVersion: {manifest.schema_version}")

    defaults: list[SkillDefinition] = []
    seen_names: set[str] = set()
    for entry in manifest.skills:
        if entry.name in seen_names:
            raise RuntimeError(f"Duplicate system skill name in manifest: {entry.name}")
        seen_names.add(entry.name)

        preset_path = _resolve_preset_path(base_dir, entry.preset_file)
        preset_payload = _read_json_file(preset_path)

        workflow_preset: SystemDefaultWorkflowPreset | None = None
        agent_preset: SystemDefaultAgentPreset | None = None
        if entry.target_type == "workflow":
            workflow_preset = SystemDefaultWorkflowPreset.model_validate(preset_payload)
        elif entry.target_type == "agent":
            agent_preset = SystemDefaultAgentPreset.model_validate(preset_payload)
        else:
            raise RuntimeError(f"Unknown targetType in system defaults: {entry.target_type}")

        defaults.append(
            _build_skill_definition(
                entry=entry,
                workflow_preset=workflow_preset,
                agent_preset=agent_preset,
            )
        )

    if not any(item.name == DEFAULT_SKILL_NAME for item in defaults):
        raise RuntimeError(f"System defaults missing required fallback skill: {DEFAULT_SKILL_NAME}")
    return defaults


@lru_cache(maxsize=4)
def _load_system_skill_defaults_cached(locale: str) -> tuple[SkillDefinition, ...]:
    defaults = _load_system_skill_defaults_from_dir(_defaults_dir(), locale)
    return tuple(defaults)


def load_system_skill_defaults(locale: str | None = None) -> list[SkillDefinition]:
    normalized_locale = _normalize_defaults_locale(locale)
    return list(_load_system_skill_defaults_cached(normalized_locale))


def get_system_skill_default(name: str, locale: str | None = None) -> SkillDefinition | None:
    normalized_locale = _normalize_defaults_locale(locale)
    for item in _load_system_skill_defaults_cached(normalized_locale):
        if item.name == name:
            return item
    return None


def get_system_workflow_baseline(name: str, locale: str | None = None):
    from app.assistant_config.schemas import WorkflowInput

    skill = get_system_skill_default(name, locale=locale)
    if skill is None or skill.langgraph_pattern != "workflow_dag":
        return None
    return WorkflowInput.model_validate(
        {
            "nodes": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "label": node.label,
                    "position_x": node.position_x,
                    "position_y": node.position_y,
                    "config": node.config,
                }
                for node in (skill.workflow_nodes or [])
            ],
            "edges": [
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
                for edge in (skill.workflow_edges or [])
            ],
            "viewport": None,
        }
    )


def get_system_agent_baseline(name: str, locale: str | None = None):
    from app.assistant_config.schemas import AgentPublishDraftInput

    skill = get_system_skill_default(name, locale=locale)
    if skill is None or skill.langgraph_pattern != "agent_loop":
        return None
    raw_model_id = getattr(skill, "model_id", None)
    model_id = None
    if raw_model_id is not None:
        text = str(raw_model_id).strip()
        if text:
            model_id = text
    return AgentPublishDraftInput.model_validate(
        {
            "system_prompt": skill.system_prompt or "",
            "tools": [str(tool) for tool in (skill.tools or []) if str(tool).strip()],
            "kb_config": {"enabled": bool(getattr(getattr(skill, "kb", None), "enabled", False))},
            "model_source": str(getattr(skill, "model_source", "default") or "default"),
            "model_id": model_id,
        }
    )


def clear_system_defaults_cache() -> None:
    _load_system_skill_defaults_cached.cache_clear()
