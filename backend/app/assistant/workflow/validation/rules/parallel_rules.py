from __future__ import annotations

from collections import defaultdict, deque
from typing import Sequence

from app.assistant.workflow.validation.models import ValidationError, ValidationResult


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
    def _find_parallel_depth(start: str, depth: int) -> int:
        """Recursively find max parallel nesting depth."""
        max_depth = depth
        for tgt in out_edges.get(start, []):
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
            if cur in visited:
                continue
            visited.add(cur)
            if node_types.get(cur) == "if_else":
                errors.append(ValidationError(
                    node_id=cur,
                    message="if_else node not allowed inside parallel branches",
                ))
            queue.extend(out_edges.get(cur, []))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
