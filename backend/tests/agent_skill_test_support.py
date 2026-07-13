"""Explicit factories for Agent Skill publication tests (Plan 01 Task 5).

Production bootstrap must never import or call these helpers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant_config.models import (
    AssistantAgentProfile,
    AssistantAgentProfileVersion,
    AssistantTool,
    AssistantWorkflow,
    AssistantWorkflowVersion,
)


def create_default_model_binding(
    db: Session,
    *,
    component: str = "assistant",
    model_name: str = "gpt-test",
    model_type: str = "llm",
    credential_name: str | None = None,
    base_url: str = "https://api.example.com/v1",
) -> tuple[AiCredential, AiModel, AiComponentBinding]:
    """Create credential + model + concrete component binding for default-model paths.

    Tests that claim successful publication must opt into this helper. Unbound-default
    tests must not inherit hidden global seed data.
    """
    cred = AiCredential(
        name=credential_name or f"cred-{uuid4().hex[:8]}",
        base_url=base_url,
        api_key_encrypted="enc-test-key-not-secret-material",
        api_key_hint="****test",
        runtime_revision=1,
    )
    db.add(cred)
    db.flush()
    model = AiModel(
        credential_id=cred.id,
        name=model_name,
        model_type=model_type,
        runtime_revision=1,
    )
    db.add(model)
    db.flush()
    binding = (
        db.query(AiComponentBinding)
        .filter(AiComponentBinding.component == component)
        .one_or_none()
    )
    if binding is None:
        binding = AiComponentBinding(component=component)
        db.add(binding)
        db.flush()
    if model_type == "llm":
        binding.llm_model_id = model.id
    else:
        binding.embedding_model_id = model.id
    db.flush()
    return cred, model, binding


def create_remote_tool(
    db: Session,
    *,
    name: str | None = None,
    endpoint_url: str = "https://hooks.example.com/run",
    headers: dict[str, Any] | None = None,
    input_params: list[dict[str, Any]] | None = None,
    api_key_encrypted: str | None = "enc-remote-key",
) -> AssistantTool:
    tool = AssistantTool(
        name=name or f"remote_{uuid4().hex[:8]}",
        description="test remote tool",
        kind="remote",
        is_system=False,
        enabled=True,
        input_params=input_params
        or [
            {
                "name": "query",
                "description": "search query",
                "param_type": "string",
                "required": True,
            }
        ],
        endpoint_url=endpoint_url,
        http_method="POST",
        headers=headers or {"X-Api-Key": "super-secret-header-value"},
        query_params={"token": "secret-query"},
        body_type="json",
        body_content='{"q":"{{query}}"}',
        auth_type="api-key",
        auth_header_name="X-Api-Key",
        auth_scheme=None,
        api_key_encrypted=api_key_encrypted,
        api_key_hint="****rem",
        timeout_seconds=10,
        payload_wrapper=None,
        config_revision=1,
    )
    db.add(tool)
    db.flush()
    return tool


def _minimal_workflow_snapshot(
    *,
    tool_names: list[str] | None = None,
    model_source: str = "default",
    model_id: UUID | None = None,
    nested_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "node_id": "start",
            "node_type": "start",
            "label": "Start",
            "position_x": 0,
            "position_y": 0,
            "config": {"input_mode": "text"},
        },
        {
            "node_id": "end",
            "node_type": "output",
            "label": "End",
            "position_x": 400,
            "position_y": 0,
            "config": {"output_mode": "text"},
        },
    ]
    edges: list[dict[str, Any]] = []
    prev = "start"
    for index, tool_name in enumerate(tool_names or []):
        node_id = f"tool_{index}"
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "tool",
                "label": tool_name,
                "position_x": 100 + index * 40,
                "position_y": 0,
                "config": {"tool_name": tool_name},
            }
        )
        edges.append(
            {
                "edge_id": f"e_{prev}_{node_id}",
                "source_node_id": prev,
                "target_node_id": node_id,
            }
        )
        prev = node_id

    llm_id = "llm_main"
    nodes.append(
        {
            "node_id": llm_id,
            "node_type": "llm",
            "label": "LLM",
            "position_x": 280,
            "position_y": 0,
            "config": {
                "model_source": model_source,
                "model_id": str(model_id) if model_id else None,
            },
        }
    )
    edges.append(
        {
            "edge_id": f"e_{prev}_{llm_id}",
            "source_node_id": prev,
            "target_node_id": llm_id,
        }
    )
    prev = llm_id

    for index, call in enumerate(nested_calls or []):
        node_id = f"call_{index}"
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "workflow_call",
                "label": "Nested",
                "position_x": 320 + index * 20,
                "position_y": 40,
                "config": call,
            }
        )
        edges.append(
            {
                "edge_id": f"e_{prev}_{node_id}",
                "source_node_id": prev,
                "target_node_id": node_id,
            }
        )
        prev = node_id

    edges.append(
        {
            "edge_id": f"e_{prev}_end",
            "source_node_id": prev,
            "target_node_id": "end",
        }
    )
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


def create_published_workflow(
    db: Session,
    *,
    name: str | None = None,
    tool_names: list[str] | None = None,
    model_source: str = "default",
    model_id: UUID | None = None,
    nested_calls: list[dict[str, Any]] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> tuple[AssistantWorkflow, AssistantWorkflowVersion]:
    workflow = AssistantWorkflow(
        name=name or f"wf_{uuid4().hex[:8]}",
        description="test workflow",
        enabled=True,
        is_system=False,
    )
    db.add(workflow)
    db.flush()
    payload = snapshot or _minimal_workflow_snapshot(
        tool_names=tool_names,
        model_source=model_source,
        model_id=model_id,
        nested_calls=nested_calls,
    )
    version = AssistantWorkflowVersion(
        workflow_id=workflow.id,
        sequence_no=1,
        version_name="v1",
        version_source="publish",
        snapshot=payload,
    )
    db.add(version)
    db.flush()
    workflow.published_version_id = version.id
    workflow.draft_version_id = version.id
    db.flush()
    return workflow, version


def create_published_agent(
    db: Session,
    *,
    name: str | None = None,
    tools: list[str] | None = None,
    model_source: str = "default",
    model_id: UUID | None = None,
    kb_enabled: bool = False,
) -> tuple[AssistantAgentProfile, AssistantAgentProfileVersion]:
    agent = AssistantAgentProfile(
        name=name or f"agent_{uuid4().hex[:8]}",
        description="test agent",
        enabled=True,
        is_system=False,
        system_prompt="You are a test agent.",
        tools=tools or ["search_entries"],
        kb_config={"enabled": kb_enabled},
    )
    db.add(agent)
    db.flush()
    snapshot = {
        "system_prompt": "You are a test agent.",
        "tools": list(tools or ["search_entries"]),
        "kb_config": {
            "enabled": kb_enabled,
            "model_source": model_source,
            "model_id": str(model_id) if model_id else None,
        },
        "model_source": model_source,
        "model_id": str(model_id) if model_id else None,
    }
    version = AssistantAgentProfileVersion(
        agent_profile_id=agent.id,
        sequence_no=1,
        version_name="v1",
        version_source="publish",
        snapshot=snapshot,
    )
    db.add(version)
    db.flush()
    agent.published_version_id = version.id
    agent.draft_version_id = version.id
    db.flush()
    return agent, version


__all__ = [
    "create_default_model_binding",
    "create_published_agent",
    "create_published_workflow",
    "create_remote_tool",
]
