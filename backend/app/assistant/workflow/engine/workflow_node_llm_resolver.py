from __future__ import annotations

from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.skill_catalog.base import SkillDefinition


def resolve_workflow_node_llms(
    *,
    skill: SkillDefinition,
    normalize_config: Callable[[dict[str, Any] | None], dict[str, Any]],
    normalize_container_body_nodes: Callable[[dict[str, Any]], list[dict[str, Any]]],
    resolve_node_custom_llm: Callable[[str, str], ChatOpenAI],
) -> dict[str, ChatOpenAI]:
    node_llms: dict[str, ChatOpenAI] = {}
    if skill.langgraph_pattern != "workflow_dag":
        return node_llms

    def _bind_model_for_node(*, runtime_key: str, cfg: dict[str, Any]) -> None:
        model_source = str(cfg.get("model_source", "default") or "default").strip().lower()
        if model_source in {"", "default"}:
            return
        if model_source != "custom":
            raise RuntimeError(
                f"Workflow node {runtime_key} has unsupported modelSource: {model_source}"
            )

        model_id = str(cfg.get("model_id", "") or "").strip()
        if not model_id:
            raise RuntimeError(
                f"Workflow node {runtime_key} requires modelId when modelSource=custom"
            )
        node_llms[runtime_key] = resolve_node_custom_llm(model_id, runtime_key)

    for node in getattr(skill, "workflow_nodes", None) or []:
        node_id = str(getattr(node, "node_id", "") or "").strip()
        node_type = str(getattr(node, "node_type", "") or "").strip()
        if not node_id:
            continue

        raw_cfg = getattr(node, "config", None)
        cfg = normalize_config(raw_cfg) if isinstance(raw_cfg, dict) else {}
        if node_type in {"llm", "parameter_extractor"}:
            _bind_model_for_node(runtime_key=node_id, cfg=cfg)
            continue
        if node_type not in {"iteration", "loop"}:
            continue

        body_nodes = normalize_container_body_nodes(cfg)
        for body in body_nodes:
            body_id = str(body.get("node_id", "") or "").strip()
            body_type = str(body.get("node_type", "") or "").strip()
            if not body_id or body_type not in {"llm", "parameter_extractor"}:
                continue
            body_cfg = body.get("config") if isinstance(body.get("config"), dict) else {}
            runtime_key = f"{node_id}::{body_id}"
            _bind_model_for_node(runtime_key=runtime_key, cfg=body_cfg)

    return node_llms
