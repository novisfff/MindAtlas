from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkflowDagPlan:
    node_map: dict[str, dict[str, Any]]
    type_map: dict[str, str]
    nodes_raw: list[dict[str, Any]]
    out_edges: dict[str, list[tuple[str, str, dict[str, Any] | None]]]
    edges_raw: list[dict[str, Any]]
    topo_order: list[str]
    start_node_id: str | None


def build_workflow_dag_plan(
    nodes: list[Any],
    edges: list[Any],
    *,
    normalize_config: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> WorkflowDagPlan:
    node_map: dict[str, dict[str, Any]] = {}
    type_map: dict[str, str] = {}
    nodes_raw: list[dict[str, Any]] = []
    for node in nodes:
        node_id = getattr(node, "node_id", None) or (node.get("node_id") if isinstance(node, dict) else None)
        node_type = getattr(node, "node_type", None) or (node.get("node_type") if isinstance(node, dict) else None)
        label = getattr(node, "label", None) or (node.get("label") if isinstance(node, dict) else None) or node_id
        cfg = getattr(node, "config", None) or (node.get("config") if isinstance(node, dict) else None)
        normalized_cfg = normalize_config(cfg) if isinstance(cfg, dict) else {}
        normalized_cfg.setdefault("__node_label", str(label or node_id))
        node_map[node_id] = normalized_cfg
        type_map[node_id] = node_type or ""
        nodes_raw.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "label": label,
                "config": node_map[node_id],
            }
        )

    out_edges: dict[str, list[tuple[str, str, dict[str, Any] | None]]] = defaultdict(list)
    edges_raw: list[dict[str, Any]] = []
    for edge in edges:
        src = getattr(edge, "source_node_id", None) or (edge.get("source_node_id") if isinstance(edge, dict) else None)
        tgt = getattr(edge, "target_node_id", None) or (edge.get("target_node_id") if isinstance(edge, dict) else None)
        src_handle = getattr(edge, "source_handle", "output") or (
            edge.get("source_handle", "output") if isinstance(edge, dict) else "output"
        )
        cond_expr = getattr(edge, "condition_expr", None) or (
            edge.get("condition_expr") if isinstance(edge, dict) else None
        )
        out_edges[src].append((tgt, src_handle, cond_expr))
        edges_raw.append(
            {
                "source_node_id": src,
                "target_node_id": tgt,
                "source_handle": src_handle,
            }
        )

    in_degree: dict[str, int] = {nid: 0 for nid in node_map}
    adj: dict[str, list[str]] = defaultdict(list)
    for src, targets in out_edges.items():
        for tgt, _, _ in targets:
            adj[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    queue = deque(nid for nid, degree in in_degree.items() if degree == 0)
    topo_order: list[str] = []
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for tgt in adj[nid]:
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                queue.append(tgt)

    start_node_id: str | None = None
    for nid, ntype in type_map.items():
        if ntype == "start":
            start_node_id = nid
            break

    return WorkflowDagPlan(
        node_map=node_map,
        type_map=type_map,
        nodes_raw=nodes_raw,
        out_edges=out_edges,
        edges_raw=edges_raw,
        topo_order=topo_order,
        start_node_id=start_node_id,
    )


def build_workflow_node_maps(
    nodes: list[Any],
    *,
    normalize_config: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    node_types: dict[str, str] = {}
    node_configs: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = str(getattr(node, "node_id", "") or "").strip()
        if not node_id:
            continue
        node_types[node_id] = str(getattr(node, "node_type", "") or "")
        raw_cfg = getattr(node, "config", None)
        node_configs[node_id] = normalize_config(raw_cfg) if isinstance(raw_cfg, dict) else {}
    return node_types, node_configs
