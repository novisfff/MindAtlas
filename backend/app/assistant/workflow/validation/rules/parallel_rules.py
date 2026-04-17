from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from app.assistant.workflow.validation.models import ValidationError, ValidationResult


def _unique_targets(targets: list[str]) -> list[str]:
    return list(dict.fromkeys(targets))


def _collect_branch_reach(
    *,
    branch_roots: list[str],
    out_edges: dict[str, list[str]],
) -> dict[str, set[str]]:
    branch_reach: dict[str, set[str]] = defaultdict(set)
    for root in branch_roots:
        stack = [root]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            branch_reach[current].add(root)
            stack.extend(out_edges.get(current, []))
    return branch_reach


def validate_parallel_branches(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    """Validate parallel branch constraints (Task 13.3).

    - Max parallel depth: 3
    - Max fan-out edges per node: 5
    - if_else is allowed only after active parallel branches reconverge
    """
    errors: list[ValidationError] = []

    node_types: dict[str, str] = {}
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {}

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        if nid:
            node_types[nid] = ntype or ""
            in_degree.setdefault(nid, 0)

    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        if src and tgt:
            out_edges[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    # Find fan-out points (nodes with >1 outgoing edges, excluding if_else which is conditional)
    conditional_fan_out_types = {"if_else", "human_in_loop"}
    fan_out_nodes = [
        nid for nid, targets in out_edges.items()
        if len(targets) > 1 and node_types.get(nid) not in conditional_fan_out_types
    ]

    # Max fan-out: 5 edges
    for nid in fan_out_nodes:
        if len(out_edges[nid]) > 5:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Fan-out exceeds limit: {len(out_edges[nid])} edges (max 5)",
            ))

    full_join_nodes_by_fan: dict[str, set[str]] = {}
    for fan_nid in fan_out_nodes:
        branch_roots = _unique_targets(out_edges.get(fan_nid, []))
        if len(branch_roots) < 2:
            continue
        branch_reach = _collect_branch_reach(branch_roots=branch_roots, out_edges=out_edges)
        full_join_nodes_by_fan[fan_nid] = {
            node_id
            for node_id, reached_roots in branch_reach.items()
            if len(reached_roots) == len(branch_roots)
        }

    # Parallel depth check with reconvergence-aware scope closing.
    max_depth_by_fan: dict[str, int] = {}
    roots = [nid for nid, degree in in_degree.items() if degree == 0]
    seen_states: set[tuple[str, frozenset[str]]] = set()
    stack: list[tuple[str, frozenset[str]]] = [(root, frozenset()) for root in roots]
    while stack:
        current, active_scopes = stack.pop()
        closed_scopes = frozenset(
            fan_id for fan_id in active_scopes
            if current not in full_join_nodes_by_fan.get(fan_id, set())
        )
        state = (current, closed_scopes)
        if state in seen_states:
            continue
        seen_states.add(state)

        next_scopes = closed_scopes
        if current in fan_out_nodes:
            next_scopes = frozenset((*closed_scopes, current))
            max_depth_by_fan[current] = max(max_depth_by_fan.get(current, 0), len(next_scopes))

        for target in out_edges.get(current, []):
            stack.append((target, next_scopes))

    for fan_nid, depth in max_depth_by_fan.items():
        if depth > 3:
            errors.append(ValidationError(
                node_id=fan_nid,
                message=f"Parallel branch nesting depth {depth} exceeds limit (max 3)",
            ))

    # No if_else before the fan-out has fully reconverged.
    flagged_if_else_nodes: set[str] = set()
    for fan_nid in fan_out_nodes:
        full_join_nodes = full_join_nodes_by_fan.get(fan_nid, set())
        for branch_root in _unique_targets(out_edges.get(fan_nid, [])):
            stack = [branch_root]
            seen: set[str] = set()
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                if current in full_join_nodes:
                    continue
                if node_types.get(current) == "if_else" and current not in flagged_if_else_nodes:
                    flagged_if_else_nodes.add(current)
                    errors.append(ValidationError(
                        node_id=current,
                        message="if_else node not allowed before parallel branches reconverge",
                    ))
                    continue
                stack.extend(out_edges.get(current, []))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
