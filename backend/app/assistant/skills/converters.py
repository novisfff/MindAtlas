"""Skill converters"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING
from uuid import UUID

from app.assistant.skills.base import (
    ConditionExpression,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)

if TYPE_CHECKING:
    from app.assistant_config.models import AssistantSkill, AssistantSkillEdge, AssistantSkillNode


_REMOVED_WORKFLOW_NODE_TYPES = {
    "answer",
    "template",
    "variable_aggregator",
}


def _parse_skill_kb_config(raw: Any) -> SkillKBConfig | None:
    if not raw or not isinstance(raw, dict):
        return None
    return SkillKBConfig(enabled=bool(raw.get("enabled", False)))


def _parse_agent_model_config(raw: Any) -> tuple[str, str | None]:
    if not isinstance(raw, dict):
        return ("default", None)

    source_raw = raw.get("model_source", raw.get("modelSource", "default"))
    source = str(source_raw or "default").strip().lower()
    if source not in {"default", "custom"}:
        source = "default"

    model_raw = raw.get("model_id", raw.get("modelId"))
    model_id = str(model_raw).strip() if model_raw is not None else ""
    if not model_id:
        model_id = None
    else:
        try:
            model_id = str(UUID(model_id))
        except Exception:
            model_id = None

    if source == "default":
        return ("default", None)
    if model_id is None:
        return ("default", None)
    return ("custom", model_id)


def db_skill_to_definition_light(skill: AssistantSkill) -> SkillDefinition:
    """轻量级转换 - 用于路由阶段，不加载 nodes/edges 详情。"""
    raw_intent_examples = skill.intent_examples or []
    if not isinstance(raw_intent_examples, list):
        raw_intent_examples = []
    intent_examples = [str(x) for x in raw_intent_examples]

    raw_tools = skill.tools or []
    if getattr(skill, "agent_profile", None) is not None and isinstance(getattr(skill.agent_profile, "tools", None), list):
        raw_tools = skill.agent_profile.tools
    if not isinstance(raw_tools, list):
        raw_tools = []
    tools = [str(x) for x in raw_tools]

    if (skill.mode or "langgraph") != "langgraph":
        raise ValueError(
            f"Skill '{skill.name}' uses legacy mode '{skill.mode}'. "
            "Only langgraph mode is supported."
        )
    pattern = "workflow_dag" if getattr(skill, "workflow_id", None) else "agent_loop" if getattr(skill, "agent_profile_id", None) else getattr(skill, "langgraph_pattern", None)
    if pattern not in ("agent_loop", "workflow_dag"):
        raise ValueError(
            f"Skill '{skill.name}' has invalid langgraph_pattern '{pattern}'. "
            "Supported patterns: agent_loop, workflow_dag."
        )

    system_prompt = skill.system_prompt
    kb_config_raw = getattr(skill, "kb_config", None)
    model_source = "default"
    model_id = None
    if pattern == "agent_loop" and getattr(skill, "agent_profile", None) is not None:
        system_prompt = skill.agent_profile.system_prompt
        if isinstance(getattr(skill.agent_profile, "kb_config", None), dict):
            kb_config_raw = skill.agent_profile.kb_config
    if pattern == "agent_loop":
        model_source, model_id = _parse_agent_model_config(kb_config_raw)

    return SkillDefinition(
        name=skill.name,
        description=skill.description or "",
        intent_examples=intent_examples,
        tools=tools,
        mode="langgraph",
        langgraph_pattern=pattern,
        model_source=model_source,
        model_id=model_id,
        system_prompt=system_prompt,
        kb=_parse_skill_kb_config(kb_config_raw),
        workflow_nodes=[],
        workflow_edges=[],
    )


def db_skill_to_definition(skill: AssistantSkill) -> SkillDefinition:
    """将数据库 AssistantSkill 模型转换为 SkillDefinition。"""
    raw_intent_examples = skill.intent_examples or []
    if not isinstance(raw_intent_examples, list):
        raw_intent_examples = []
    intent_examples = [str(x) for x in raw_intent_examples]

    if (skill.mode or "langgraph") != "langgraph":
        raise ValueError(
            f"Skill '{skill.name}' uses legacy mode '{skill.mode}'. "
            "Only langgraph mode is supported."
        )
    pattern = "workflow_dag" if getattr(skill, "workflow_id", None) else "agent_loop" if getattr(skill, "agent_profile_id", None) else getattr(skill, "langgraph_pattern", None)
    if pattern not in ("agent_loop", "workflow_dag"):
        raise ValueError(
            f"Skill '{skill.name}' has invalid langgraph_pattern '{pattern}'. "
            "Supported patterns: agent_loop, workflow_dag."
        )

    raw_tools = skill.tools or []
    if getattr(skill, "agent_profile", None) is not None and isinstance(getattr(skill.agent_profile, "tools", None), list):
        raw_tools = skill.agent_profile.tools
    if not isinstance(raw_tools, list):
        raw_tools = []
    tools = [str(x) for x in raw_tools]

    workflow_nodes_src = getattr(skill, "nodes", None) or []
    workflow_edges_src = getattr(skill, "edges", None) or []
    if getattr(skill, "workflow", None) is not None:
        workflow_nodes_src = getattr(skill.workflow, "nodes", None) or workflow_nodes_src
        workflow_edges_src = getattr(skill.workflow, "edges", None) or workflow_edges_src

    system_prompt = skill.system_prompt
    kb_config_raw = getattr(skill, "kb_config", None)
    model_source = "default"
    model_id = None
    if pattern == "agent_loop" and getattr(skill, "agent_profile", None) is not None:
        system_prompt = skill.agent_profile.system_prompt
        if isinstance(getattr(skill.agent_profile, "kb_config", None), dict):
            kb_config_raw = skill.agent_profile.kb_config
    if pattern == "agent_loop":
        model_source, model_id = _parse_agent_model_config(kb_config_raw)

    return SkillDefinition(
        name=skill.name,
        description=skill.description or "",
        intent_examples=intent_examples,
        tools=tools,
        mode="langgraph",
        langgraph_pattern=pattern,
        model_source=model_source,
        model_id=model_id,
        system_prompt=system_prompt,
        kb=_parse_skill_kb_config(kb_config_raw),
        workflow_nodes=db_nodes_to_definitions(workflow_nodes_src if pattern == "workflow_dag" else []),
        workflow_edges=db_edges_to_definitions(workflow_edges_src if pattern == "workflow_dag" else []),
    )


def db_nodes_to_definitions(nodes: list[AssistantSkillNode]) -> list[WorkflowNodeDefinition]:
    """将数据库节点模型列表转换为 WorkflowNodeDefinition 列表。"""
    result: list[WorkflowNodeDefinition] = []
    for n in nodes:
        if n.node_type in _REMOVED_WORKFLOW_NODE_TYPES:
            raise ValueError(
                f"Workflow node '{n.node_id}' uses unsupported type '{n.node_type}'. "
                "Removed node types: answer/template/variable_aggregator. "
                "Please migrate to supported node types."
            )
        result.append(WorkflowNodeDefinition(
            node_id=n.node_id,
            node_type=n.node_type,
            label=n.label or "",
            position_x=n.position_x or 0.0,
            position_y=n.position_y or 0.0,
            config=n.config or {},
        ))
    return result


def db_edges_to_definitions(edges: list[AssistantSkillEdge]) -> list[WorkflowEdgeDefinition]:
    """将数据库边模型列表转换为 WorkflowEdgeDefinition 列表。"""
    result: list[WorkflowEdgeDefinition] = []
    for e in edges:
        cond_expr = None
        if e.condition_expr and isinstance(e.condition_expr, dict):
            try:
                cond_expr = ConditionExpression(**e.condition_expr)
            except Exception:
                pass
        result.append(WorkflowEdgeDefinition(
            edge_id=e.edge_id,
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            source_handle=e.source_handle or "output",
            target_handle=e.target_handle or "input",
            condition_type=e.condition_type,
            condition_expr=cond_expr,
            label=e.label,
        ))
    return result


def db_workflow_to_skill_definition(
    *,
    name: str,
    description: str,
    tools: list[str] | None,
    workflow_nodes: list[AssistantSkillNode],
    workflow_edges: list[AssistantSkillEdge],
) -> SkillDefinition:
    raw_tools = tools or []
    return SkillDefinition(
        name=name,
        description=description,
        intent_examples=[],
        tools=[str(x) for x in raw_tools],
        mode="langgraph",
        langgraph_pattern="workflow_dag",
        system_prompt=None,
        kb=_parse_skill_kb_config({"enabled": False}),
        workflow_nodes=db_nodes_to_definitions(workflow_nodes),
        workflow_edges=db_edges_to_definitions(workflow_edges),
    )


def db_agent_profile_to_skill_definition(
    *,
    name: str,
    description: str,
    tools: list[str] | None,
    system_prompt: str | None,
    kb_config: dict | None,
) -> SkillDefinition:
    raw_tools = tools or []
    model_source, model_id = _parse_agent_model_config(kb_config)
    return SkillDefinition(
        name=name,
        description=description,
        intent_examples=[],
        tools=[str(x) for x in raw_tools],
        mode="langgraph",
        langgraph_pattern="agent_loop",
        model_source=model_source,
        model_id=model_id,
        system_prompt=system_prompt,
        kb=_parse_skill_kb_config(kb_config),
        workflow_nodes=[],
        workflow_edges=[],
    )
