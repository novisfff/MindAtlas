from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable

from app.assistant.skill_catalog.base import SkillDefinition

_graph_cache: dict[tuple, Any] = {}
_graph_cache_order: list[tuple] = []
_GRAPH_CACHE_MAX = 32
_graph_cache_lock = threading.Lock()


def make_cache_key(skill: SkillDefinition, kb_enabled: bool, model: str) -> tuple:
    parts = [skill.name, skill.langgraph_pattern or ""]

    if skill.langgraph_pattern == "agent_loop":
        parts.append(hashlib.md5((skill.system_prompt or "").encode()).hexdigest())
    parts.append(hashlib.md5(json.dumps(sorted(skill.tools or []), ensure_ascii=False).encode()).hexdigest())

    if skill.langgraph_pattern == "workflow_dag":
        wf_nodes = getattr(skill, "workflow_nodes", None) or []
        wf_edges = getattr(skill, "workflow_edges", None) or []
        nodes_data = []
        for node in wf_nodes:
            nodes_data.append(
                {
                    "node_id": getattr(node, "node_id", ""),
                    "node_type": getattr(node, "node_type", ""),
                    "config": getattr(node, "config", None),
                }
            )
        edges_data = []
        for edge in wf_edges:
            edges_data.append(
                {
                    "source_node_id": getattr(edge, "source_node_id", ""),
                    "target_node_id": getattr(edge, "target_node_id", ""),
                    "source_handle": getattr(edge, "source_handle", ""),
                }
            )
        dag_str = json.dumps({"n": nodes_data, "e": edges_data}, ensure_ascii=False, sort_keys=True)
        parts.append(hashlib.md5(dag_str.encode()).hexdigest())

    parts.append(str(kb_enabled))
    parts.append(model)
    return tuple(parts)


def get_or_compile_graph(
    key: tuple,
    compile_fn: Callable[[], Any],
) -> Any:
    with _graph_cache_lock:
        if key in _graph_cache:
            _graph_cache_order.remove(key)
            _graph_cache_order.append(key)
            return _graph_cache[key]

    compiled = compile_fn()

    with _graph_cache_lock:
        _graph_cache[key] = compiled
        _graph_cache_order.append(key)

        while len(_graph_cache_order) > _GRAPH_CACHE_MAX:
            evict = _graph_cache_order.pop(0)
            _graph_cache.pop(evict, None)

    return compiled
