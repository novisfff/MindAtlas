"""Pure Workflow graph walkers shared by legacy protection and Skill publication.

These helpers intentionally accept plain node lists (dict or ORM-like objects) and
do not touch SQLAlchemy sessions or mutable aggregate draft state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID


def cfg_get(cfg: Mapping[str, Any] | dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return default


def parse_uuid_value(value: Any) -> UUID | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return UUID(text)
    except Exception:
        return None


def _node_fields(node: Any) -> tuple[str, str, dict[str, Any]]:
    if isinstance(node, Mapping):
        node_id = str(node.get("node_id", node.get("nodeId", "")) or "").strip()
        node_type = str(node.get("node_type", node.get("nodeType", "")) or "").strip()
        cfg = node.get("config") if isinstance(node.get("config"), Mapping) else {}
        return node_id, node_type, dict(cfg)
    node_id = str(getattr(node, "node_id", "") or "").strip()
    node_type = str(getattr(node, "node_type", "") or "").strip()
    raw_cfg = getattr(node, "config", None)
    cfg = dict(raw_cfg) if isinstance(raw_cfg, Mapping) else {}
    return node_id, node_type, cfg


def iter_workflow_call_node_configs(
    nodes: Sequence[Any] | list[Any],
    *,
    container_node_id: str | None = None,
) -> list[tuple[str, str | None, dict[str, Any]]]:
    """Return (node_id, container_node_id, config) for every workflow_call node."""
    refs: list[tuple[str, str | None, dict[str, Any]]] = []
    for raw_node in nodes or []:
        node_id, node_type, cfg = _node_fields(raw_node)
        if not node_id:
            continue
        if node_type == "workflow_call":
            refs.append((node_id, container_node_id, dict(cfg)))
        if node_type not in {"iteration", "loop"}:
            continue
        body_nodes = cfg_get(cfg, "body_nodes", "bodyNodes", default=[])
        if isinstance(body_nodes, list):
            refs.extend(
                iter_workflow_call_node_configs(
                    body_nodes,
                    container_node_id=node_id,
                )
            )
    return refs


@dataclass(frozen=True)
class WorkflowCallRef:
    source_node_id: str
    source_container_node_id: str | None
    target_workflow_id: UUID
    binding_mode: str
    target_published_version_id: UUID | None


def collect_workflow_call_references(
    workflow_nodes: Sequence[Any] | list[Any],
) -> list[WorkflowCallRef]:
    """Collect workflow_call references from a node list (no DB)."""
    refs: list[WorkflowCallRef] = []
    for node_id, container_node_id, cfg in iter_workflow_call_node_configs(workflow_nodes):
        target_workflow_id = parse_uuid_value(
            cfg_get(cfg, "target_workflow_id", "targetWorkflowId", default=None)
        )
        if target_workflow_id is None:
            continue
        target_version_id = parse_uuid_value(
            cfg_get(
                cfg,
                "target_published_version_id",
                "targetPublishedVersionId",
                default=None,
            )
        )
        binding_mode = str(
            cfg_get(cfg, "binding_mode", "bindingMode", default="pinned") or "pinned"
        ).strip().lower()
        refs.append(
            WorkflowCallRef(
                source_node_id=node_id,
                source_container_node_id=container_node_id,
                target_workflow_id=target_workflow_id,
                binding_mode=binding_mode,
                target_published_version_id=target_version_id,
            )
        )
    return refs


def collect_workflow_tool_names(workflow_nodes: Sequence[Any] | list[Any]) -> set[str]:
    """Collect tool names referenced by tool/agent/knowledge nodes."""
    tool_names: set[str] = set()

    def _walk(nodes: Iterable[Any]) -> None:
        for node in nodes or []:
            _node_id, node_type, cfg = _node_fields(node)

            if node_type == "tool" and isinstance(cfg, dict):
                tool_name = cfg.get("toolName") or cfg.get("tool_name")
                if isinstance(tool_name, str) and tool_name.strip():
                    tool_names.add(tool_name.strip())

            if node_type == "knowledge_retrieval":
                tool_names.add("kb_search")

            if node_type == "agent" and isinstance(cfg, dict):
                raw_tool_names = cfg.get("toolNames", cfg.get("tool_names"))
                if isinstance(raw_tool_names, list):
                    for raw_name in raw_tool_names:
                        if not isinstance(raw_name, str):
                            continue
                        tool_name = raw_name.strip()
                        if tool_name:
                            tool_names.add(tool_name)
                knowledge_enabled = cfg.get("knowledgeEnabled", cfg.get("knowledge_enabled"))
                if isinstance(knowledge_enabled, bool) and knowledge_enabled:
                    tool_names.add("kb_search")

            if node_type in {"iteration", "loop"} and isinstance(cfg, dict):
                body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                if isinstance(body_nodes, list):
                    _walk(body_nodes)

    _walk(workflow_nodes)
    return tool_names


def collect_workflow_custom_model_ids(
    workflow_nodes: Sequence[Any] | list[Any],
) -> set[UUID]:
    """Collect custom model IDs from llm/parameter_extractor/agent nodes."""
    model_ids: set[UUID] = set()

    def _walk(nodes: Iterable[Any]) -> None:
        for node in nodes or []:
            _node_id, node_type, cfg = _node_fields(node)

            if node_type in {"llm", "parameter_extractor", "agent"} and isinstance(cfg, dict):
                model_source_raw = cfg.get("modelSource", cfg.get("model_source", "default"))
                model_source = str(model_source_raw or "default").strip().lower()
                if model_source == "custom":
                    model_id_raw = cfg.get("modelId", cfg.get("model_id"))
                    if isinstance(model_id_raw, str):
                        model_id_text = model_id_raw.strip()
                        if model_id_text:
                            try:
                                model_ids.add(UUID(model_id_text))
                            except Exception:
                                # UUID format is validated elsewhere; ignore here.
                                pass

            if node_type in {"iteration", "loop"} and isinstance(cfg, dict):
                body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                if isinstance(body_nodes, list):
                    _walk(body_nodes)

    _walk(workflow_nodes)
    return model_ids


def collect_workflow_model_usages(
    workflow_nodes: Sequence[Any] | list[Any],
    *,
    path_prefix: str = "root",
) -> list[tuple[str, str, UUID | None]]:
    """Return ordered (path, model_source, model_id) for model-using nodes.

    Paths are structural and deterministic: ``{prefix}/node:{node_id}/model``.
    """
    usages: list[tuple[str, str, UUID | None]] = []

    def _walk(nodes: Iterable[Any], prefix: str) -> None:
        for node in nodes or []:
            node_id, node_type, cfg = _node_fields(node)
            node_path = f"{prefix}/node:{node_id}" if node_id else prefix

            if node_type in {"llm", "parameter_extractor", "agent"} and isinstance(cfg, dict):
                model_source_raw = cfg.get("modelSource", cfg.get("model_source", "default"))
                model_source = str(model_source_raw or "default").strip().lower()
                if model_source not in {"default", "custom"}:
                    model_source = "default"
                model_id: UUID | None = None
                if model_source == "custom":
                    model_id = parse_uuid_value(cfg.get("modelId", cfg.get("model_id")))
                usages.append((f"{node_path}/model", model_source, model_id))

            if node_type in {"iteration", "loop"} and isinstance(cfg, dict):
                body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                if isinstance(body_nodes, list):
                    _walk(body_nodes, f"{node_path}/body")

    _walk(workflow_nodes, path_prefix)
    return usages


def collect_workflow_tool_usages(
    workflow_nodes: Sequence[Any] | list[Any],
    *,
    path_prefix: str = "root",
) -> list[tuple[str, str]]:
    """Return ordered (path, tool_name) for tool/agent/knowledge nodes."""
    usages: list[tuple[str, str]] = []

    def _walk(nodes: Iterable[Any], prefix: str) -> None:
        for node in nodes or []:
            node_id, node_type, cfg = _node_fields(node)
            node_path = f"{prefix}/node:{node_id}" if node_id else prefix

            if node_type == "tool" and isinstance(cfg, dict):
                tool_name = cfg.get("toolName") or cfg.get("tool_name")
                if isinstance(tool_name, str) and tool_name.strip():
                    usages.append((f"{node_path}/tool:{tool_name.strip()}", tool_name.strip()))

            if node_type == "knowledge_retrieval":
                usages.append((f"{node_path}/tool:kb_search", "kb_search"))

            if node_type == "agent" and isinstance(cfg, dict):
                raw_tool_names = cfg.get("toolNames", cfg.get("tool_names"))
                if isinstance(raw_tool_names, list):
                    for raw_name in raw_tool_names:
                        if not isinstance(raw_name, str):
                            continue
                        tool_name = raw_name.strip()
                        if tool_name:
                            usages.append((f"{node_path}/tool:{tool_name}", tool_name))
                knowledge_enabled = cfg.get("knowledgeEnabled", cfg.get("knowledge_enabled"))
                if isinstance(knowledge_enabled, bool) and knowledge_enabled:
                    usages.append((f"{node_path}/tool:kb_search", "kb_search"))

            if node_type in {"iteration", "loop"} and isinstance(cfg, dict):
                body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                if isinstance(body_nodes, list):
                    _walk(body_nodes, f"{node_path}/body")

    _walk(workflow_nodes, path_prefix)
    return usages


__all__ = [
    "WorkflowCallRef",
    "cfg_get",
    "collect_workflow_call_references",
    "collect_workflow_custom_model_ids",
    "collect_workflow_model_usages",
    "collect_workflow_tool_names",
    "collect_workflow_tool_usages",
    "iter_workflow_call_node_configs",
    "parse_uuid_value",
]
