"""Workflow DAG topology validator."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class ValidationError:
    node_id: str | None
    message: str


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)


def _cfg_get(cfg: dict, *keys: str, default=None):
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return default


def _cfg_bool(cfg: dict, *keys: str, default: bool = False) -> bool:
    value = _cfg_get(cfg, *keys, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


_IF_ELSE_HANDLE_RE = re.compile(r"[a-zA-Z0-9_]+")
_IF_ELSE_NEW_OPERATORS = {
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "is",
    "is_not",
    "is_empty",
    "is_not_empty",
}
_IF_ELSE_LEGACY_OPERATORS = {"equals", "not_equals", "gt", "lt", "gte", "lte"}
_IF_ELSE_ALL_OPERATORS = _IF_ELSE_NEW_OPERATORS | _IF_ELSE_LEGACY_OPERATORS
_IF_ELSE_LEGACY_OPERATOR_MAP = {
    "equals": "is",
    "not_equals": "is_not",
}
_SYS_FIELDS = {"date", "datetime", "conversation_id"}


def _normalize_if_else_operator(raw: object) -> str:
    op = str(raw or "is").strip().lower()
    if not op:
        return "is"
    return _IF_ELSE_LEGACY_OPERATOR_MAP.get(op, op)


def _normalize_if_else_handle(raw: object) -> str:
    handle = str(raw or "").strip()
    if handle == "default":
        return "else"
    return handle


def _normalize_if_else_config(cfg: dict) -> dict[str, object]:
    else_handle = _normalize_if_else_handle(_cfg_get(cfg, "else_handle", "elseHandle", default="else"))
    if not else_handle or not _IF_ELSE_HANDLE_RE.fullmatch(else_handle):
        else_handle = "else"

    branches_raw = _cfg_get(cfg, "branches", default=None)
    branches: list[dict[str, object]] = []

    if isinstance(branches_raw, list) and branches_raw:
        for idx, branch in enumerate(branches_raw, start=1):
            if not isinstance(branch, dict):
                continue
            branch_id = _normalize_if_else_handle(branch.get("id"))
            if not branch_id or not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                branch_id = f"if_{idx}"
            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"
            label = str(branch.get("label") or ("IF" if idx == 1 else f"ELIF {idx - 1}")).strip() or ("IF" if idx == 1 else f"ELIF {idx - 1}")
            conds: list[dict[str, object]] = []
            conds_raw = branch.get("conditions")
            if isinstance(conds_raw, list):
                for cond_idx, cond in enumerate(conds_raw, start=1):
                    if not isinstance(cond, dict):
                        continue
                    conds.append(
                        {
                            "id": str(cond.get("id") or f"{branch_id}_cond_{cond_idx}").strip() or f"{branch_id}_cond_{cond_idx}",
                            "variable": str(cond.get("variable") or "").strip(),
                            "operator": _normalize_if_else_operator(cond.get("operator")),
                            "value": None if cond.get("value") is None else str(cond.get("value")),
                        }
                    )
            branches.append(
                {
                    "id": branch_id,
                    "label": label,
                    "logic": logic,
                    "conditions": conds,
                }
            )

    if not branches:
        # legacy format: conditions[] where each condition carries handle
        grouped: dict[str, list[dict[str, object]]] = {}
        handle_order: list[str] = []
        legacy_conds = _cfg_get(cfg, "conditions", default=[])
        if isinstance(legacy_conds, list):
            for idx, cond in enumerate(legacy_conds, start=1):
                if not isinstance(cond, dict):
                    continue
                handle = _normalize_if_else_handle(cond.get("handle"))
                if not handle:
                    continue
                if handle in {"else"}:
                    continue
                if not _IF_ELSE_HANDLE_RE.fullmatch(handle):
                    continue
                if handle not in grouped:
                    grouped[handle] = []
                    handle_order.append(handle)
                grouped[handle].append(
                    {
                        "id": str(cond.get("id") or f"{handle}_cond_{idx}").strip() or f"{handle}_cond_{idx}",
                        "variable": str(cond.get("variable") or "").strip(),
                        "operator": _normalize_if_else_operator(cond.get("operator")),
                        "value": None if cond.get("value") is None else str(cond.get("value")),
                    }
                )
        for branch_idx, handle in enumerate(handle_order, start=1):
            branches.append(
                {
                    "id": handle,
                    "label": "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}",
                    "logic": "and",
                    "conditions": grouped.get(handle, []),
                }
            )

    return {"branches": branches, "else_handle": else_handle}


def validate_workflow(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    """Validate workflow DAG topology.

    Args:
        nodes: list of objects with node_id, node_type, config attributes
        edges: list of objects with source_node_id, target_node_id, source_handle attributes
    """
    errors: list[ValidationError] = []

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

    # Rule 0: labels must be non-empty, case-insensitive unique, and cannot include dot
    seen_labels: dict[str, str] = {}
    for nid in node_ids:
        raw_label = label_map.get(nid, "")
        label = str(raw_label or "").strip()
        if not label:
            errors.append(ValidationError(
                node_id=nid,
                message="Node label is required",
            ))
            continue
        if "." in label:
            errors.append(ValidationError(
                node_id=nid,
                message="Node label must not contain '.'",
            ))
        normalized = label.casefold()
        existing = seen_labels.get(normalized)
        if existing and existing != nid:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Duplicate node label (case-insensitive): '{label}'",
            ))
            continue
        seen_labels[normalized] = nid

    # Rule 1: exactly one start node
    start_nodes = [nid for nid, nt in type_map.items() if nt == "start"]
    if len(start_nodes) == 0:
        errors.append(ValidationError(node_id=None, message="Must have exactly one start node"))
    elif len(start_nodes) > 1:
        for nid in start_nodes[1:]:
            errors.append(ValidationError(node_id=nid, message="Multiple start nodes found"))

    # Rule 2: answer node is no longer supported (strict replacement by llm.is_output)
    answer_nodes = [nid for nid, nt in type_map.items() if nt == "answer"]
    for nid in answer_nodes:
        errors.append(ValidationError(
            node_id=nid,
            message="Node type 'answer' is no longer supported. Use llm node with isOutput=true.",
        ))

    # Rule 3: at least one llm output node
    output_llm_nodes: list[str] = []
    for nid, ntype in type_map.items():
        if ntype != "llm":
            continue
        cfg = config_map.get(nid, {})
        if _cfg_bool(cfg, "is_output", "isOutput", default=False):
            output_llm_nodes.append(nid)
    if not output_llm_nodes:
        errors.append(ValidationError(
            node_id=None,
            message="Must have at least one llm node with isOutput=true",
        ))

    # Rule 4: node_id uniqueness (already checked above)

    # Build adjacency structures
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)
    out_handles: dict[str, list[str]] = defaultdict(list)
    out_edge_count: dict[str, int] = defaultdict(int)
    in_edge_count: dict[str, int] = defaultdict(int)

    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        src_handle = getattr(e, "source_handle", None) or (e.get("source_handle") if isinstance(e, dict) else None) or "output"
        if src is None or tgt is None:
            continue

        # Rule 5: edge references valid nodes
        if src not in node_ids:
            errors.append(ValidationError(node_id=src, message=f"Edge references unknown source node: {src}"))
        if tgt not in node_ids:
            errors.append(ValidationError(node_id=tgt, message=f"Edge references unknown target node: {tgt}"))

        if src in node_ids and tgt in node_ids:
            out_edges[src].append(tgt)
            in_edges[tgt].append(src)
            out_handles[src].append(_normalize_if_else_handle(src_handle))
            out_edge_count[src] += 1
            in_edge_count[tgt] += 1

    # Rule 6: no orphan nodes (every non-start node must have at least one in-edge)
    for nid in node_ids:
        if type_map[nid] == "start":
            continue
        if in_edge_count[nid] == 0:
            errors.append(ValidationError(node_id=nid, message="Orphan node: no incoming edges"))

    # Rule 7: cycle detection (Kahn's algorithm)
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

    # Rule 8: if_else must have at least 2 outgoing edges
    for nid, nt in type_map.items():
        if nt == "if_else" and out_edge_count[nid] < 2:
            errors.append(ValidationError(node_id=nid, message="if_else node must have at least 2 outgoing edges"))

    # Rule 9: variable_aggregator must have at least 2 incoming edges
    for nid, nt in type_map.items():
        if nt == "variable_aggregator" and in_edge_count[nid] < 2:
            errors.append(ValidationError(node_id=nid, message="variable_aggregator must have at least 2 incoming edges"))

    # Rule 10: start has no in-edges
    for nid in start_nodes:
        if in_edge_count[nid] > 0:
            errors.append(ValidationError(node_id=nid, message="start node must not have incoming edges"))

    # Rule 11: template variable references must point to upstream nodes
    topo_index = {nid: i for i, nid in enumerate(topo_order)}
    _VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")
    for nid in node_ids:
        cfg = config_map.get(nid, {})
        # Check template strings in config (snake_case + camelCase)
        for key in (
            "system_prompt", "systemPrompt",
            "user_input", "userInput",
            "instruction",
            "template",
            "query",
            "args_template", "argsTemplate",
        ):
            text = cfg.get(key, "")
            if not isinstance(text, str):
                continue
            for m in _VAR_RE.finditer(text):
                ref_node = m.group(1)
                ref_field = m.group(2)
                if ref_node == "sys":
                    if ref_field not in _SYS_FIELDS:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Template references unsupported sys variable: sys.{ref_field}",
                        ))
                    continue
                if ref_node not in node_ids:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"Template references unknown node: {ref_node}",
                    ))
                elif nid in topo_index and ref_node in topo_index:
                    if topo_index[ref_node] >= topo_index[nid]:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Template references non-upstream node: {ref_node}",
                        ))

        # Rule 12: tool node config validation (save-time)
        if type_map.get(nid) == "tool":
            tool_name = _cfg_get(cfg, "tool_name", "toolName", default="")
            if not isinstance(tool_name, str) or not tool_name.strip():
                errors.append(ValidationError(
                    node_id=nid,
                    message="Tool node requires toolName",
                ))

            input_bindings = _cfg_get(cfg, "input_bindings", "inputBindings")
            if input_bindings is None:
                errors.append(ValidationError(
                    node_id=nid,
                    message="Tool node requires inputBindings; legacy argsFrom/argsTemplate are no longer supported",
                ))
            elif not isinstance(input_bindings, dict):
                errors.append(ValidationError(
                    node_id=nid,
                    message="tool.inputBindings must be an object",
                ))
            else:
                for key, value in input_bindings.items():
                    if not isinstance(key, str) or not key.strip():
                        errors.append(ValidationError(
                            node_id=nid,
                            message="tool.inputBindings contains empty parameter name",
                        ))
                        continue
                    if not isinstance(value, str):
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"tool.inputBindings['{key}'] must be a string",
                        ))

        # Rule 13: if_else config and edge-handle validation
        if type_map.get(nid) == "if_else":
            normalized = _normalize_if_else_config(cfg)
            branches = normalized.get("branches")
            else_handle = str(normalized.get("else_handle") or "else")

            if not isinstance(branches, list) or not branches:
                errors.append(ValidationError(
                    node_id=nid,
                    message="if_else requires at least one IF/ELIF branch",
                ))
                continue

            branch_ids: list[str] = []
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                branch_id = _normalize_if_else_handle(branch.get("id"))
                if not branch_id or not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch has invalid id: {branch.get('id')}",
                    ))
                    continue
                if branch_id in branch_ids:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch id duplicated: {branch_id}",
                    ))
                branch_ids.append(branch_id)

                logic = str(branch.get("logic") or "and").strip().lower()
                if logic not in {"and", "or"}:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch '{branch_id}' has invalid logic: {logic}",
                    ))

                conditions = branch.get("conditions")
                if not isinstance(conditions, list) or not conditions:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch '{branch_id}' requires at least one condition",
                    ))
                    continue

                for cond in conditions:
                    if not isinstance(cond, dict):
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"if_else branch '{branch_id}' contains invalid condition item",
                        ))
                        continue
                    var = str(cond.get("variable") or "").strip()
                    if not var:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"if_else branch '{branch_id}' contains empty condition variable",
                        ))
                        continue
                    if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Invalid condition variable path: {var}",
                        ))
                    if var.startswith("sys."):
                        sys_field = var.split(".", 1)[1]
                        if sys_field not in _SYS_FIELDS:
                            errors.append(ValidationError(
                                node_id=nid,
                                message=f"Unsupported sys variable in condition: {var}",
                            ))

                    raw_op = str(cond.get("operator") or "").strip().lower()
                    op = _normalize_if_else_operator(raw_op)
                    if op not in _IF_ELSE_ALL_OPERATORS:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Unsupported condition operator: {raw_op or cond.get('operator')}",
                        ))
                        continue

                    if op not in {"is_empty", "is_not_empty"}:
                        raw_value = cond.get("value")
                        value = "" if raw_value is None else str(raw_value)
                        if not value.strip():
                            errors.append(ValidationError(
                                node_id=nid,
                                message=f"Condition operator '{op}' requires value",
                            ))

            normalized_out_handles = [_normalize_if_else_handle(h) for h in out_handles.get(nid, [])]
            if normalized_out_handles.count(else_handle) != 1:
                errors.append(ValidationError(
                    node_id=nid,
                    message=f"if_else requires exactly one '{else_handle}' outgoing edge",
                ))

            expected_handles = set(branch_ids)
            expected_handles.add(else_handle)
            for handle in expected_handles:
                count = normalized_out_handles.count(handle)
                if count != 1:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else handle '{handle}' must map to exactly one outgoing edge",
                    ))

            for handle in normalized_out_handles:
                if handle not in expected_handles:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else has unknown outgoing handle: {handle}",
                    ))

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_workflow_compile(
    nodes: Sequence,
    edges: Sequence,
    tool_names: set[str] | None = None,
) -> ValidationResult:
    """Extended validation for compilation (Task 13.2).

    Checks tool_name existence, output_fields format, condition expressions.
    """
    result = validate_workflow(nodes, edges)
    errors = list(result.errors)

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        if not isinstance(cfg, dict):
            cfg = {}

        # Tool node: runtime tool map existence
        if ntype == "tool" and tool_names is not None:
            tool_name = _cfg_get(cfg, "tool_name", "toolName", default="")
            if isinstance(tool_name, str) and tool_name.strip() and tool_name not in tool_names:
                errors.append(ValidationError(
                    node_id=nid,
                    message=f"Tool node references unknown tool: {tool_name}",
                ))

        # LLM node: output_mode/output_fields format
        if ntype == "llm":
            output_mode_raw = _cfg_get(cfg, "output_mode", "outputMode", default="text")
            output_mode = str(output_mode_raw or "text").strip().lower()
            if output_mode == "json":
                output_mode = "structured"
            if output_mode not in {"text", "structured"}:
                errors.append(ValidationError(
                    node_id=nid,
                    message=f"Unsupported llm output_mode: {output_mode_raw}",
                ))

            output_fields = _cfg_get(cfg, "output_fields", "outputFields")
            if output_mode == "structured" and (not isinstance(output_fields, list) or not output_fields):
                errors.append(ValidationError(
                    node_id=nid,
                    message="LLM structured mode requires output_fields",
                ))

            if output_fields is not None and output_fields != []:
                if not isinstance(output_fields, list):
                    errors.append(ValidationError(
                        node_id=nid,
                        message="LLM node output_fields must be a list",
                    ))
                else:
                    for f in output_fields:
                        if isinstance(f, dict):
                            name = f.get("name", "")
                            if not name or not re.fullmatch(r"[a-zA-Z0-9_]+", str(name)):
                                errors.append(ValidationError(
                                    node_id=nid,
                                    message=f"Invalid output field name: {name}",
                                ))

        # if_else: compile-time variable path check for normalized branches
        if ntype == "if_else":
            normalized = _normalize_if_else_config(cfg)
            branches = normalized.get("branches", [])
            if isinstance(branches, list):
                for branch in branches:
                    if not isinstance(branch, dict):
                        continue
                    for cond in (branch.get("conditions") or []):
                        if not isinstance(cond, dict):
                            continue
                        var = str(cond.get("variable") or "").strip()
                        if not var:
                            continue
                        if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                            errors.append(ValidationError(
                                node_id=nid,
                                message=f"Invalid condition variable path: {var}",
                            ))
                            continue
                        if var.startswith("sys."):
                            sys_field = var.split(".", 1)[1]
                            if sys_field not in _SYS_FIELDS:
                                errors.append(ValidationError(
                                    node_id=nid,
                                    message=f"Unsupported sys variable in condition: {var}",
                                ))

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_parallel_branches(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    """Validate parallel branch constraints (Task 13.3).

    - Max parallel depth: 3
    - Max fan-out edges per node: 5
    - No nested if_else inside parallel branches
    """
    errors: list[ValidationError] = []

    node_types: dict[str, str] = {}
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        if nid:
            node_types[nid] = ntype or ""

    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        if src and tgt:
            out_edges[src].append(tgt)
            in_edges[tgt].append(src)

    # Find fan-out points (nodes with >1 outgoing edges, excluding if_else which is conditional)
    fan_out_nodes = [
        nid for nid, targets in out_edges.items()
        if len(targets) > 1 and node_types.get(nid) != "if_else"
    ]

    # Max fan-out: 5 edges
    for nid in fan_out_nodes:
        if len(out_edges[nid]) > 5:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Fan-out exceeds limit: {len(out_edges[nid])} edges (max 5)",
            ))

    # Parallel depth check via DFS from fan-out points
    aggregator_nodes = {nid for nid, nt in node_types.items() if nt == "variable_aggregator"}

    def _find_parallel_depth(start: str, depth: int) -> int:
        """Recursively find max parallel nesting depth."""
        max_depth = depth
        for tgt in out_edges.get(start, []):
            if tgt in aggregator_nodes:
                continue
            # Check if this target is itself a fan-out (nested parallel)
            if len(out_edges.get(tgt, [])) > 1 and node_types.get(tgt) != "if_else":
                nested = _find_parallel_depth(tgt, depth + 1)
                max_depth = max(max_depth, nested)
        return max_depth

    for nid in fan_out_nodes:
        depth = _find_parallel_depth(nid, 1)
        if depth > 3:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Parallel branch nesting depth {depth} exceeds limit (max 3)",
            ))

    # No if_else inside parallel branches
    for fan_nid in fan_out_nodes:
        visited: set[str] = set()
        queue = deque(out_edges.get(fan_nid, []))
        while queue:
            cur = queue.popleft()
            if cur in visited or cur in aggregator_nodes:
                continue
            visited.add(cur)
            if node_types.get(cur) == "if_else":
                errors.append(ValidationError(
                    node_id=cur,
                    message="if_else node not allowed inside parallel branches",
                ))
            queue.extend(out_edges.get(cur, []))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
