from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Sequence

from app.assistant.workflow.validation.contracts import (
    _REMOVED_NODE_TYPE_MESSAGES,
    _SUPPORTED_NODE_TYPES,
    _SYS_FIELDS,
)
from app.assistant.workflow.validation.helpers import (
    extract_container_body,
    iter_config_template_texts,
    resolve_start_env_var_contract,
    resolve_start_input_contract,
)
from app.assistant.workflow.validation.models import ValidationError
from app.assistant.workflow.validation.rules.if_else_rules import normalize_if_else_handle


@dataclass(frozen=True)
class ValidationContext:
    node_ids: set[str]
    type_map: dict[str, str]
    config_map: dict[str, dict]
    out_edges: dict[str, list[str]]
    out_handles: dict[str, list[str]]
    out_edge_count: dict[str, int]
    in_edge_count: dict[str, int]
    start_nodes: list[str]
    output_nodes: list[str]
    topo_order: list[str]
    topo_index: dict[str, int]
    start_memory_mode: str
    start_allowed_fields: set[str]
    start_env_var_types: dict[str, str]


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")


def build_validation_context(
    nodes: Sequence,
    edges: Sequence,
    errors: list[ValidationError],
) -> ValidationContext:
    node_map: dict[str, object] = {}
    type_map: dict[str, str] = {}
    config_map: dict[str, dict] = {}
    label_map: dict[str, str] = {}

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        label = getattr(n, "label", None) or (n.get("label") if isinstance(n, dict) else None)
        if nid is None:
            continue
        type_map[nid] = ntype or ""
        config_map[nid] = cfg if isinstance(cfg, dict) else {}
        label_map[nid] = str(label or "")
        if nid in node_map:
            errors.append(ValidationError(node_id=nid, message=f"Duplicate node_id: {nid}"))
        node_map[nid] = n

    node_ids = set(node_map.keys())

    seen_labels: dict[str, str] = {}
    for nid in node_ids:
        raw_label = label_map.get(nid, "")
        label = str(raw_label or "").strip()
        if not label:
            errors.append(
                ValidationError(
                    node_id=nid,
                    message="Node label is required",
                )
            )
            continue
        if "." in label:
            errors.append(
                ValidationError(
                    node_id=nid,
                    message="Node label must not contain '.'",
                )
            )
        normalized = label.casefold()
        existing = seen_labels.get(normalized)
        if existing and existing != nid:
            errors.append(
                ValidationError(
                    node_id=nid,
                    message=f"Duplicate node label (case-insensitive): '{label}'",
                )
            )
            continue
        seen_labels[normalized] = nid

    start_nodes = [nid for nid, nt in type_map.items() if nt == "start"]
    if len(start_nodes) == 0:
        errors.append(ValidationError(node_id=None, message="Must have exactly one start node"))
    elif len(start_nodes) > 1:
        for nid in start_nodes[1:]:
            errors.append(ValidationError(node_id=nid, message="Multiple start nodes found"))

    start_memory_mode = "auto"
    start_allowed_fields: set[str] = {"user_input"}
    start_env_var_types: dict[str, str] = {}
    if len(start_nodes) == 1:
        start_node_id = start_nodes[0]
        start_cfg = config_map.get(start_node_id, {})
        if not isinstance(start_cfg, dict):
            start_cfg = {}
        _, start_memory_mode, start_allowed_fields, start_contract_errors = resolve_start_input_contract(start_cfg)
        for message in start_contract_errors:
            errors.append(ValidationError(node_id=start_node_id, message=message))
        start_env_var_types, start_env_contract_errors = resolve_start_env_var_contract(start_cfg)
        for message in start_env_contract_errors:
            errors.append(ValidationError(node_id=start_node_id, message=message))

    for nid, ntype in type_map.items():
        if ntype in _REMOVED_NODE_TYPE_MESSAGES:
            errors.append(ValidationError(node_id=nid, message=_REMOVED_NODE_TYPE_MESSAGES[ntype]))
        elif ntype not in _SUPPORTED_NODE_TYPES:
            errors.append(ValidationError(node_id=nid, message=f"Unsupported node type: {ntype}"))

    output_nodes = [nid for nid, ntype in type_map.items() if ntype == "output"]
    if len(output_nodes) == 0:
        errors.append(
            ValidationError(
                node_id=None,
                message="Must have at least one output node",
            )
        )

    out_edges: dict[str, list[str]] = defaultdict(list)
    out_handles: dict[str, list[str]] = defaultdict(list)
    out_edge_count: dict[str, int] = defaultdict(int)
    in_edge_count: dict[str, int] = defaultdict(int)

    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        src_handle = (
            getattr(e, "source_handle", None)
            or (e.get("source_handle") if isinstance(e, dict) else None)
            or "output"
        )
        if src is None or tgt is None:
            continue

        if src not in node_ids:
            errors.append(ValidationError(node_id=src, message=f"Edge references unknown source node: {src}"))
        if tgt not in node_ids:
            errors.append(ValidationError(node_id=tgt, message=f"Edge references unknown target node: {tgt}"))

        if src in node_ids and tgt in node_ids:
            out_edges[src].append(tgt)
            out_handles[src].append(normalize_if_else_handle(src_handle))
            out_edge_count[src] += 1
            in_edge_count[tgt] += 1

    for nid in node_ids:
        if type_map[nid] == "start":
            continue
        if in_edge_count[nid] == 0:
            errors.append(ValidationError(node_id=nid, message="Orphan node: no incoming edges"))

    in_degree = {nid: 0 for nid in node_ids}
    for nid in node_ids:
        in_degree[nid] = in_edge_count[nid]

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    topo_order: list[str] = []
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for tgt in out_edges[nid]:
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                queue.append(tgt)

    if len(topo_order) != len(node_ids):
        cycle_nodes = node_ids - set(topo_order)
        for nid in cycle_nodes:
            errors.append(ValidationError(node_id=nid, message="Node is part of a cycle"))

    for nid, nt in type_map.items():
        if nt == "if_else" and out_edge_count[nid] < 2:
            errors.append(ValidationError(node_id=nid, message="if_else node must have at least 2 outgoing edges"))

    for nid in start_nodes:
        if in_edge_count[nid] > 0:
            errors.append(ValidationError(node_id=nid, message="start node must not have incoming edges"))

    for nid in output_nodes:
        if out_edge_count[nid] > 0:
            errors.append(
                ValidationError(
                    node_id=nid,
                    message="output node must not have outgoing edges",
                )
            )

    topo_index = {nid: i for i, nid in enumerate(topo_order)}
    return ValidationContext(
        node_ids=node_ids,
        type_map=type_map,
        config_map=config_map,
        out_edges=out_edges,
        out_handles=out_handles,
        out_edge_count=out_edge_count,
        in_edge_count=in_edge_count,
        start_nodes=start_nodes,
        output_nodes=output_nodes,
        topo_order=topo_order,
        topo_index=topo_index,
        start_memory_mode=start_memory_mode,
        start_allowed_fields=start_allowed_fields,
        start_env_var_types=start_env_var_types,
    )


def validate_template_refs(
    ctx: ValidationContext,
    errors: list[ValidationError],
) -> None:
    for nid in ctx.node_ids:
        cfg = ctx.config_map.get(nid, {})
        local_container_refs: set[str] = set()
        if ctx.type_map.get(nid) in {"iteration", "loop"}:
            body_nodes, _ = extract_container_body(cfg)
            local_container_refs = {
                str(raw.get("node_id", raw.get("nodeId", "")) or "").strip()
                for raw in body_nodes
                if isinstance(raw, dict)
            }
        for text in iter_config_template_texts(cfg):
            for m in _VAR_RE.finditer(text):
                ref_node = m.group(1)
                ref_field = m.group(2)
                if ref_node == "sys":
                    if ref_field not in _SYS_FIELDS:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Template references unsupported sys variable: sys.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "start":
                    if ref_field not in ctx.start_allowed_fields:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Template references unsupported start field: start.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "env":
                    if ref_field not in ctx.start_env_var_types:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Template references unknown env variable: env.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "container" and ctx.type_map.get(nid) in {"iteration", "loop"}:
                    continue
                if ref_node in local_container_refs:
                    continue
                if ref_node not in ctx.node_ids:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"Template references unknown node: {ref_node}",
                        )
                    )
                elif nid in ctx.topo_index and ref_node in ctx.topo_index:
                    if ctx.topo_index[ref_node] >= ctx.topo_index[nid]:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Template references non-upstream node: {ref_node}",
                            )
                        )
