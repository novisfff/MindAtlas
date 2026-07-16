"""Plan 07 Task 2: derive and validate frozen DurableExecutionPlanV1.

Publication/admission gate for interrupt_mode=durable. Fail-closed against the
supported node/target matrix. Never reads Draft/current/latest ambient state.
"""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.assistant.capabilities.classification import (
    SIDE_EFFECT_RANK,
    SYSTEM_TOOL_CLASSIFICATIONS,
)
from app.assistant.capabilities.contracts import SideEffectClass
from app.assistant.domain.contracts import ResolvedCapabilityBinding, ResolvedCapabilityDependency
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.workflow.durable.contracts import (
    DurableEdgeV1,
    DurableExecutionPlanV1,
    DurableNodePlanV1,
    FrozenExecutionDependencyRef,
    compute_plan_digest,
)

# ---------------------------------------------------------------------------
# Public error
# ---------------------------------------------------------------------------


class DurablePlanError(ValueError):
    """Fail-closed durable plan validation error."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# Supported matrix (adapter keys + business side effects)
# ---------------------------------------------------------------------------

# Control bookkeeping (Interrupt/Checkpoint/Event) is never a business Draft.
_CONTROL_TYPES = frozenset({"start", "output", "if_else", "variable_assign"})
_LLM_TYPES = frozenset({"llm", "parameter_extractor"})
_LOOP_TYPES = frozenset({"iteration", "loop"})
_DENIED_TYPES = frozenset({"code_executor", "http_request"})

_ALLOWED_BUSINESS_SIDE_EFFECTS = frozenset({"none", "read", "compute"})

DURABLE_PLAN_EXTENSION_KEY = "durableExecutionPlanV1"
DURABLE_PLAN_EXTENSION_CONTRACT_VERSION = 1

AdapterKey = str


def _adapter_key(node_type: str) -> AdapterKey:
    return f"{node_type}.v1"


# ---------------------------------------------------------------------------
# Graph helpers (accept snake_case + camelCase published snapshots)
# ---------------------------------------------------------------------------


def _cfg_get(cfg: Mapping[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not isinstance(cfg, Mapping):
        return default
    for key in keys:
        if key in cfg:
            return cfg[key]
    return default


def _node_type(node: Any) -> str:
    if hasattr(node, "node_type"):
        return str(getattr(node, "node_type") or "").strip()
    if isinstance(node, Mapping):
        return str(node.get("node_type") or node.get("nodeType") or "").strip()
    return ""


def _node_id(node: Any) -> str:
    if hasattr(node, "node_id"):
        return str(getattr(node, "node_id") or "").strip()
    if isinstance(node, Mapping):
        return str(node.get("node_id") or node.get("nodeId") or "").strip()
    return ""


def _node_config(node: Any) -> dict[str, Any]:
    if hasattr(node, "config"):
        cfg = getattr(node, "config")
        return dict(cfg) if isinstance(cfg, Mapping) else {}
    if isinstance(node, Mapping):
        cfg = node.get("config")
        return dict(cfg) if isinstance(cfg, Mapping) else {}
    return {}


def _extract_nodes(workflow_input: Any) -> list[Any]:
    if workflow_input is None:
        return []
    if hasattr(workflow_input, "nodes"):
        nodes = getattr(workflow_input, "nodes")
        return list(nodes) if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)) else []
    if isinstance(workflow_input, Mapping):
        nodes = workflow_input.get("nodes")
        return list(nodes) if isinstance(nodes, list) else []
    return []


def _extract_edges(workflow_input: Any) -> list[Any]:
    if workflow_input is None:
        return []
    if hasattr(workflow_input, "edges"):
        edges = getattr(workflow_input, "edges")
        return list(edges) if isinstance(edges, Sequence) and not isinstance(edges, (str, bytes)) else []
    if isinstance(workflow_input, Mapping):
        edges = workflow_input.get("edges")
        return list(edges) if isinstance(edges, list) else []
    return []


def _edge_field(edge: Any, *names: str) -> str | None:
    if hasattr(edge, names[0]):
        raw = getattr(edge, names[0], None)
        if raw is not None:
            text = str(raw).strip()
            return text or None
    if isinstance(edge, Mapping):
        for name in names:
            if name in edge and edge[name] is not None:
                text = str(edge[name]).strip()
                return text or None
    return None


def _canonical_node_config(node: Any) -> dict[str, Any]:
    """Stable config payload for config_digest (drop presentation-only fields)."""
    cfg = _node_config(node)
    # Deep-copy JSON-ish mapping; fall back to empty.
    try:
        return copy.deepcopy(dict(cfg))
    except Exception:
        return {}


def _config_digest(node: Any) -> str:
    return sha256_canonical_json(
        {
            "nodeId": _node_id(node),
            "nodeType": _node_type(node),
            "config": _canonical_node_config(node),
        }
    )


def _max_side_effect(left: SideEffectClass, right: SideEffectClass) -> SideEffectClass:
    return left if SIDE_EFFECT_RANK[left] >= SIDE_EFFECT_RANK[right] else right


def _deps_by_path(
    dependencies: Sequence[FrozenExecutionDependencyRef | ResolvedCapabilityDependency],
) -> dict[str, FrozenExecutionDependencyRef]:
    out: dict[str, FrozenExecutionDependencyRef] = {}
    for dep in dependencies:
        if isinstance(dep, FrozenExecutionDependencyRef):
            out[dep.dependency_path] = dep
            continue
        # ResolvedCapabilityDependency → portable plan ref
        path = str(getattr(dep, "dependency_path", "") or "")
        if not path:
            continue
        dep_type = str(getattr(dep, "dependency_type", "") or "")
        if dep_type not in {"system_tool", "remote_tool", "workflow", "agent", "model"}:
            # Skip non-execution plan types (if any).
            continue
        out[path] = FrozenExecutionDependencyRef(
            dependency_path=path,
            dependency_type=dep_type,  # type: ignore[arg-type]
            target_identity=str(getattr(dep, "target_identity", "") or ""),
            target_version_id=getattr(dep, "resolved_workflow_version_id", None)
            or getattr(dep, "resolved_agent_version_id", None)
            or None,
            resolution_digest=str(getattr(dep, "resolution_digest", "") or ""),
            dependency_digest=str(getattr(dep, "dependency_digest", "") or ""),
        )
    return out


def _lookup_dep(
    deps: Mapping[str, FrozenExecutionDependencyRef],
    *,
    candidates: Sequence[str],
    path_prefix: str = "root",
    suffix: str | None = None,
) -> FrozenExecutionDependencyRef | None:
    for key in candidates:
        dep = deps.get(key)
        if dep is not None:
            return dep
    if suffix:
        for path, dep in deps.items():
            if path.startswith(path_prefix) and path.endswith(suffix):
                return dep
    return None


def _system_tool_side_effect(tool_name: str) -> SideEffectClass:
    entry = SYSTEM_TOOL_CLASSIFICATIONS.get(tool_name)
    if entry is None:
        return "unknown"
    return entry[0]


def _tool_name_from_identity(identity: str) -> str:
    if ":" in identity:
        return identity.split(":", 1)[1]
    return identity


# ---------------------------------------------------------------------------
# Core planner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ParsedEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    source_handle: str | None
    target_handle: str | None


def _parse_edges(raw_edges: Sequence[Any], *, node_ids: set[str]) -> list[_ParsedEdge]:
    parsed: list[_ParsedEdge] = []
    seen_ids: set[str] = set()
    for idx, edge in enumerate(raw_edges):
        edge_id = _edge_field(edge, "edge_id", "edgeId") or f"edge_{idx}"
        source = _edge_field(edge, "source_node_id", "sourceNodeId", "source")
        target = _edge_field(edge, "target_node_id", "targetNodeId", "target")
        if not source or not target:
            raise DurablePlanError(
                "edge missing source or target",
                reason_code="invalid_edge",
            )
        if source not in node_ids or target not in node_ids:
            raise DurablePlanError(
                f"edge {edge_id} references missing node ({source}->{target})",
                reason_code="invalid_edge",
            )
        if edge_id in seen_ids:
            # Allow duplicate ids only if fully identical; otherwise reject.
            for prev in parsed:
                if prev.edge_id == edge_id and (
                    prev.source_node_id != source or prev.target_node_id != target
                ):
                    raise DurablePlanError(
                        f"duplicate edge id {edge_id} with conflicting endpoints",
                        reason_code="invalid_edge",
                    )
            continue
        seen_ids.add(edge_id)
        parsed.append(
            _ParsedEdge(
                edge_id=edge_id,
                source_node_id=source,
                target_node_id=target,
                source_handle=_edge_field(edge, "source_handle", "sourceHandle"),
                target_handle=_edge_field(edge, "target_handle", "targetHandle"),
            )
        )
    return parsed


def _find_entry_node_id(nodes: Sequence[Any], edges: Sequence[_ParsedEdge]) -> str:
    # Durable plans require exactly one explicit start node (no ambient entry guess).
    starts = [_node_id(n) for n in nodes if _node_type(n) == "start" and _node_id(n)]
    if len(starts) == 1:
        return starts[0]
    if len(starts) > 1:
        raise DurablePlanError(
            f"ambiguous entry: multiple start nodes {starts}",
            reason_code="ambiguous_entry",
        )
    raise DurablePlanError(
        "missing start entry node",
        reason_code="missing_entry",
    )


def _detect_unbounded_cycle(
    *,
    entry: str,
    adj: Mapping[str, list[str]],
    loop_nodes: set[str],
) -> None:
    """Reject cycles that are not confined to explicit bounded loop containers.

    Simple DFS cycle detection over the top-level DAG. Edges that re-enter a
    declared loop container node are ignored (loop body is planned separately).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in adj}

    def dfs(u: str) -> None:
        color[u] = GRAY
        for v in adj.get(u, ()):
            if v in loop_nodes and u not in loop_nodes:
                # Entering a loop container is not a free cycle.
                continue
            if color.get(v, WHITE) == GRAY:
                raise DurablePlanError(
                    f"graph cycle without bounded loop contract involving {u}->{v}",
                    reason_code="unbounded_cycle",
                )
            if color.get(v, WHITE) == WHITE:
                dfs(v)
        color[u] = BLACK

    if entry in color:
        dfs(entry)
    for n in list(color):
        if color[n] == WHITE:
            dfs(n)


def _reachable(entry: str, adj: Mapping[str, Sequence[str]]) -> set[str]:
    seen: set[str] = set()
    q: deque[str] = deque([entry])
    while q:
        u = q.popleft()
        if u in seen:
            continue
        seen.add(u)
        for v in adj.get(u, ()):
            if v not in seen:
                q.append(v)
    return seen


def _plan_node(
    node: Any,
    *,
    outgoing: Sequence[_ParsedEdge],
    deps: Mapping[str, FrozenExecutionDependencyRef],
    path_prefix: str,
    depth: int,
    nested_workflow_inputs: Mapping[str, Any],
    visited_workflow_versions: frozenset[UUID],
) -> DurableNodePlanV1:
    if depth > 16:
        raise DurablePlanError("plan depth exceeded", reason_code="depth_exceeded")

    nid = _node_id(node)
    ntype = _node_type(node)
    cfg = _node_config(node)
    if not nid or not ntype:
        raise DurablePlanError("node missing id or type", reason_code="invalid_graph")

    edges = tuple(
        DurableEdgeV1(
            edge_id=e.edge_id,
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            source_handle=e.source_handle,
            target_handle=e.target_handle,
        )
        for e in outgoing
        if e.source_node_id == nid
    )

    if ntype in _DENIED_TYPES:
        reason = "http_request_denied" if ntype == "http_request" else "unsupported_node"
        raise DurablePlanError(
            f"node type {ntype!r} is not supported for durable execution",
            reason_code=reason,
        )

    if ntype in _CONTROL_TYPES:
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect="none",
            may_interrupt=False,
            dependency_refs=(),
        )

    if ntype in _LLM_TYPES:
        dep = _lookup_dep(
            deps,
            candidates=(f"{path_prefix}/node:{nid}/model",),
            path_prefix=path_prefix,
            suffix=f"/node:{nid}/model",
        )
        if dep is None or dep.dependency_type != "model":
            raise DurablePlanError(
                f"missing frozen model dependency for {ntype} node {nid}",
                reason_code="incomplete_dependency_closure",
            )
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect="compute",
            may_interrupt=False,
            dependency_refs=(dep,),
        )

    if ntype == "human_in_loop":
        # Runtime control bookkeeping only — never business Draft.
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect="none",
            may_interrupt=True,
            dependency_refs=(),
        )

    if ntype == "tool":
        tool_name = str(
            _cfg_get(cfg, "tool_name", "toolName", default="") or ""
        ).strip()
        if not tool_name:
            raise DurablePlanError(
                f"tool node {nid} missing tool_name",
                reason_code="incomplete_dependency_closure",
            )
        dep = _lookup_dep(
            deps,
            candidates=(
                f"{path_prefix}/node:{nid}/tool:{tool_name}",
                f"{path_prefix}/tool:{tool_name}",
            ),
            path_prefix=path_prefix,
            suffix=f"/tool:{tool_name}",
        )
        if dep is None or dep.dependency_type not in {"system_tool", "remote_tool"}:
            raise DurablePlanError(
                f"missing frozen tool dependency for {tool_name} at node {nid}",
                reason_code="incomplete_dependency_closure",
            )
        if dep.dependency_type == "remote_tool":
            # Remote tools default write_external under Plan 02; denied in Plan 07 durable.
            raise DurablePlanError(
                f"remote tool {tool_name} denied for durable execution",
                reason_code="denied_side_effect",
            )
        side = _system_tool_side_effect(_tool_name_from_identity(dep.target_identity) or tool_name)
        if side not in _ALLOWED_BUSINESS_SIDE_EFFECTS:
            raise DurablePlanError(
                f"tool {tool_name} side effect {side!r} denied for durable execution",
                reason_code="denied_side_effect",
            )
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect=side,
            may_interrupt=False,
            dependency_refs=(dep,),
        )

    if ntype == "knowledge_retrieval":
        dep = _lookup_dep(
            deps,
            candidates=(f"{path_prefix}/node:{nid}/kb/model",),
            path_prefix=path_prefix,
            suffix=f"/node:{nid}/kb/model",
        )
        if dep is None or dep.dependency_type != "model":
            raise DurablePlanError(
                f"missing frozen knowledge model dependency for node {nid}",
                reason_code="incomplete_dependency_closure",
            )
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect="read",
            may_interrupt=False,
            dependency_refs=(dep,),
        )

    if ntype in _LOOP_TYPES:
        max_raw = _cfg_get(cfg, "max_iterations", "maxIterations", default=None)
        try:
            max_iterations = int(max_raw) if max_raw is not None else None
        except (TypeError, ValueError):
            max_iterations = None
        body_nodes = _cfg_get(cfg, "body_nodes", "bodyNodes", default=[])
        if not isinstance(body_nodes, list) or not body_nodes:
            raise DurablePlanError(
                f"loop node {nid} missing body_nodes",
                reason_code="unbounded_loop",
            )
        if max_iterations is None or max_iterations < 1:
            raise DurablePlanError(
                f"loop node {nid} is not statically bounded",
                reason_code="unbounded_loop",
            )
        # Plan body nodes for dependency completeness + side-effect max; body is
        # not expanded into the top-level plan graph (runner owns iteration).
        body_side: SideEffectClass = "none"
        body_deps: list[FrozenExecutionDependencyRef] = []
        body_may_interrupt = False
        body_path = f"{path_prefix}/node:{nid}/body"
        for body_node in body_nodes:
            # Body nodes have no top-level outgoing edges in this plan version.
            body_plan = _plan_node(
                body_node,
                outgoing=(),
                deps=deps,
                path_prefix=body_path,
                depth=depth + 1,
                nested_workflow_inputs=nested_workflow_inputs,
                visited_workflow_versions=visited_workflow_versions,
            )
            # Nested interrupt (human or nested workflow_call) must surface.
            body_may_interrupt = body_may_interrupt or body_plan.may_interrupt
            body_side = _max_side_effect(body_side, body_plan.business_side_effect)
            body_deps.extend(body_plan.dependency_refs)
            if body_plan.business_side_effect not in _ALLOWED_BUSINESS_SIDE_EFFECTS:
                raise DurablePlanError(
                    f"loop body node has denied side effect",
                    reason_code="denied_side_effect",
                )
        # Dedup deps by path, stable order.
        seen_paths: set[str] = set()
        ordered_deps: list[FrozenExecutionDependencyRef] = []
        for d in body_deps:
            if d.dependency_path in seen_paths:
                continue
            seen_paths.add(d.dependency_path)
            ordered_deps.append(d)
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect=body_side,
            may_interrupt=body_may_interrupt,
            dependency_refs=tuple(ordered_deps),
        )

    if ntype == "workflow_call":
        return _plan_workflow_call_node(
            node,
            nid=nid,
            cfg=cfg,
            edges=edges,
            deps=deps,
            path_prefix=path_prefix,
            depth=depth,
            nested_workflow_inputs=nested_workflow_inputs,
            visited_workflow_versions=visited_workflow_versions,
        )

    if ntype == "agent":
        # Reviewed Agent: fixed tools + model; no Main Agent Skill injection.
        if _cfg_get(cfg, "nested_agent", "nestedAgent", default=False):
            raise DurablePlanError(
                f"nested agent restart not supported at node {nid}",
                reason_code="unsupported_node",
            )
        if _cfg_get(cfg, "restart", default=False):
            raise DurablePlanError(
                f"agent restart not supported at node {nid}",
                reason_code="unsupported_node",
            )
        model_dep = _lookup_dep(
            deps,
            candidates=(f"{path_prefix}/node:{nid}/model",),
            path_prefix=path_prefix,
            suffix=f"/node:{nid}/model",
        )
        if model_dep is None or model_dep.dependency_type != "model":
            raise DurablePlanError(
                f"missing frozen model dependency for agent node {nid}",
                reason_code="incomplete_dependency_closure",
            )
        refs: list[FrozenExecutionDependencyRef] = [model_dep]
        side: SideEffectClass = "compute"
        tool_names_raw = _cfg_get(cfg, "tool_names", "toolNames", default=[]) or []
        if not isinstance(tool_names_raw, list):
            raise DurablePlanError(
                f"agent node {nid} has invalid tool_names",
                reason_code="incomplete_dependency_closure",
            )
        for item in tool_names_raw:
            if not isinstance(item, str) or not item.strip():
                raise DurablePlanError(
                    f"agent node {nid} has invalid tool name",
                    reason_code="incomplete_dependency_closure",
                )
            tool_name = item.strip()
            tdep = _lookup_dep(
                deps,
                candidates=(
                    f"{path_prefix}/node:{nid}/tool:{tool_name}",
                    f"{path_prefix}/tool:{tool_name}",
                ),
                path_prefix=path_prefix,
                suffix=f"/tool:{tool_name}",
            )
            if tdep is None:
                raise DurablePlanError(
                    f"missing tool dependency {tool_name} for agent node {nid}",
                    reason_code="incomplete_dependency_closure",
                )
            if tdep.dependency_type == "remote_tool":
                raise DurablePlanError(
                    f"remote tool {tool_name} denied in durable agent node",
                    reason_code="denied_side_effect",
                )
            tside = _system_tool_side_effect(
                _tool_name_from_identity(tdep.target_identity) or tool_name
            )
            if tside not in _ALLOWED_BUSINESS_SIDE_EFFECTS:
                raise DurablePlanError(
                    f"agent tool {tool_name} side effect {tside!r} denied",
                    reason_code="denied_side_effect",
                )
            side = _max_side_effect(side, tside)
            refs.append(tdep)
        if _cfg_get(cfg, "knowledge_enabled", "knowledgeEnabled", default=False):
            kb_dep = _lookup_dep(
                deps,
                candidates=(f"{path_prefix}/node:{nid}/kb/model",),
                path_prefix=path_prefix,
                suffix=f"/node:{nid}/kb/model",
            )
            if kb_dep is None:
                raise DurablePlanError(
                    f"missing kb model dependency for agent node {nid}",
                    reason_code="incomplete_dependency_closure",
                )
            refs.append(kb_dep)
            side = _max_side_effect(side, "read")
        return DurableNodePlanV1(
            node_id=nid,
            node_type=ntype,
            config_digest=_config_digest(node),
            outgoing_edges=edges,
            adapter_key=_adapter_key(ntype),
            business_side_effect=side,
            may_interrupt=False,
            dependency_refs=tuple(refs),
        )

    raise DurablePlanError(
        f"unsupported node type {ntype!r}",
        reason_code="unsupported_node",
    )


def _plan_workflow_call_node(
    node: Any,
    *,
    nid: str,
    cfg: Mapping[str, Any],
    edges: tuple[DurableEdgeV1, ...],
    deps: Mapping[str, FrozenExecutionDependencyRef],
    path_prefix: str,
    depth: int,
    nested_workflow_inputs: Mapping[str, Any],
    visited_workflow_versions: frozenset[UUID],
) -> DurableNodePlanV1:
    """Fail-closed durable plan for a pinned workflow_call.

    Recurses into the frozen child workflow snapshot (when available) so every
    reachable child node/target is validated against the durable matrix. Parent
    plan folds child business side-effect max and may_interrupt.
    """
    binding_mode = str(
        _cfg_get(cfg, "binding_mode", "bindingMode", default="pinned") or "pinned"
    ).strip().lower()
    target_version = _cfg_get(
        cfg, "target_published_version_id", "targetPublishedVersionId", default=None
    )
    if binding_mode != "pinned" or not target_version:
        raise DurablePlanError(
            f"workflow_call {nid} must be pinned to an exact published version",
            reason_code="mutable_target_lookup",
        )

    call_key = nid
    call_path = f"{path_prefix}/workflow_call:{call_key}"
    dep = _lookup_dep(
        deps,
        candidates=(
            call_path,
            f"{path_prefix}/node:{nid}/workflow_call",
        ),
        path_prefix=path_prefix,
        suffix=f"/workflow_call:{call_key}",
    )
    if dep is None or dep.dependency_type != "workflow":
        raise DurablePlanError(
            f"missing frozen workflow_call dependency for node {nid}",
            reason_code="incomplete_dependency_closure",
        )

    # Resolve child version identity (prefer frozen dep; fall back to pin).
    child_version_id = dep.target_version_id
    if child_version_id is None:
        try:
            child_version_id = UUID(str(target_version))
        except (TypeError, ValueError) as exc:
            raise DurablePlanError(
                f"workflow_call {nid} has invalid target version id",
                reason_code="mutable_target_lookup",
            ) from exc
    if child_version_id in visited_workflow_versions:
        raise DurablePlanError(
            f"workflow_call cycle involving version {child_version_id}",
            reason_code="workflow_call_cycle",
        )

    nested_input = nested_workflow_inputs.get(dep.dependency_path)
    if nested_input is None:
        nested_input = nested_workflow_inputs.get(call_path)
    if nested_input is None:
        # Fail closed: without the frozen child graph we cannot prove reachable
        # child content is durable-safe (even on unused branches).
        raise DurablePlanError(
            f"workflow_call {nid} missing frozen nested workflow snapshot for durable planning",
            reason_code="nested_workflow_call_unsupported",
        )

    try:
        _child_entry, child_nodes = _plan_workflow_nodes(
            workflow_input=nested_input,
            deps=deps,
            path_prefix=dep.dependency_path if dep.dependency_path else call_path,
            depth=depth + 1,
            nested_workflow_inputs=nested_workflow_inputs,
            visited_workflow_versions=visited_workflow_versions | {child_version_id},
        )
    except DurablePlanError as exc:
        raise DurablePlanError(
            f"nested workflow_call {nid} denied: {exc}",
            reason_code=exc.reason_code,
        ) from exc

    child_side = business_side_effect_maximum_from_nodes(child_nodes)
    if child_side not in _ALLOWED_BUSINESS_SIDE_EFFECTS:
        raise DurablePlanError(
            f"nested workflow_call {nid} side effect {child_side!r} denied",
            reason_code="denied_side_effect",
        )
    child_may_interrupt = any(n.may_interrupt for n in child_nodes)

    # Surface the workflow dep itself plus nested child dependency refs.
    nested_refs: list[FrozenExecutionDependencyRef] = [dep]
    seen_paths = {dep.dependency_path}
    for child_node in child_nodes:
        for ref in child_node.dependency_refs:
            if ref.dependency_path in seen_paths:
                continue
            seen_paths.add(ref.dependency_path)
            nested_refs.append(ref)

    return DurableNodePlanV1(
        node_id=nid,
        node_type="workflow_call",
        config_digest=_config_digest(node),
        outgoing_edges=edges,
        adapter_key=_adapter_key("workflow_call"),
        business_side_effect=child_side,
        may_interrupt=child_may_interrupt,
        dependency_refs=tuple(nested_refs),
    )


def _plan_workflow_nodes(
    *,
    workflow_input: Any,
    deps: Mapping[str, FrozenExecutionDependencyRef],
    path_prefix: str,
    depth: int,
    nested_workflow_inputs: Mapping[str, Any],
    visited_workflow_versions: frozenset[UUID],
) -> tuple[str, list[DurableNodePlanV1]]:
    """Plan every declared node of a workflow graph (fail-closed).

    Returns ``(entry_node_id, planned_nodes)``.
    """
    nodes_raw = _extract_nodes(workflow_input)
    if not nodes_raw:
        raise DurablePlanError("empty workflow graph", reason_code="invalid_graph")

    by_id: dict[str, Any] = {}
    for node in nodes_raw:
        nid = _node_id(node)
        if not nid:
            raise DurablePlanError("node missing id", reason_code="invalid_graph")
        if nid in by_id:
            raise DurablePlanError(
                f"duplicate node id {nid}",
                reason_code="invalid_graph",
            )
        by_id[nid] = node

    edges = _parse_edges(_extract_edges(workflow_input), node_ids=set(by_id))
    entry = _find_entry_node_id(nodes_raw, edges)

    adj: dict[str, list[str]] = defaultdict(list)
    outgoing_by_source: dict[str, list[_ParsedEdge]] = defaultdict(list)
    for e in edges:
        adj[e.source_node_id].append(e.target_node_id)
        outgoing_by_source[e.source_node_id].append(e)
    for nid in by_id:
        adj.setdefault(nid, [])
        outgoing_by_source.setdefault(nid, [])

    loop_nodes = {
        nid for nid, node in by_id.items() if _node_type(node) in _LOOP_TYPES
    }
    _detect_unbounded_cycle(entry=entry, adj=adj, loop_nodes=loop_nodes)

    # Stable order: entry first via BFS, then remaining by node_id.
    # Unreachable unsafe nodes still poison the plan (fail closed).
    planned: list[DurableNodePlanV1] = []
    order: list[str] = []
    seen_order: set[str] = set()
    q: deque[str] = deque([entry])
    while q:
        u = q.popleft()
        if u in seen_order:
            continue
        seen_order.add(u)
        order.append(u)
        for v in adj.get(u, ()):
            if v not in seen_order:
                q.append(v)
    for nid in sorted(by_id):
        if nid not in seen_order:
            order.append(nid)

    for nid in order:
        node = by_id[nid]
        planned.append(
            _plan_node(
                node,
                outgoing=outgoing_by_source.get(nid, ()),
                deps=deps,
                path_prefix=path_prefix,
                depth=depth,
                nested_workflow_inputs=nested_workflow_inputs,
                visited_workflow_versions=visited_workflow_versions,
            )
        )
    return entry, planned


def _nested_workflow_inputs_from_closure(closure: Any) -> dict[str, Any]:
    """Extract frozen nested workflow graphs from an execution closure index."""
    out: dict[str, Any] = {}
    if closure is None:
        return out
    workflows = getattr(closure, "workflows_by_locator", None)
    if isinstance(workflows, Mapping):
        for locator, entry in workflows.items():
            parsed = getattr(entry, "parsed_published_input", None)
            if parsed is not None:
                out[str(locator)] = parsed
    inspect = getattr(closure, "workflow_input_for_classification", None)
    if callable(inspect) and isinstance(workflows, Mapping):
        for locator in workflows:
            if str(locator) in out:
                continue
            try:
                parsed = inspect(source_locator=locator)
            except Exception:
                continue
            if parsed is not None:
                out[str(locator)] = parsed
    return out


def plan_durable_execution(
    *,
    target_kind: Literal["workflow", "agent"],
    target_version_id: UUID,
    target_digest: str,
    workflow_input: Any,
    dependencies: Sequence[FrozenExecutionDependencyRef | ResolvedCapabilityDependency] = (),
    nested_workflow_inputs: Mapping[str, Any] | None = None,
) -> DurableExecutionPlanV1:
    """Derive a frozen DurableExecutionPlanV1 from an exact immutable snapshot.

    Fail-closed: unsupported/unsafe/ambiguous/cyclic/unbounded cases raise
    DurablePlanError. Never consults Draft/current/latest ambient state.

    ``nested_workflow_inputs`` maps frozen dependency paths
    (e.g. ``root/workflow_call:<nodeId>``) to child workflow snapshots so
    ``workflow_call`` nodes can be recursively validated. Missing nested graphs
    fail closed with ``nested_workflow_call_unsupported``.
    """
    if target_kind not in {"workflow", "agent"}:
        raise DurablePlanError("invalid target_kind", reason_code="invalid_graph")
    if not isinstance(target_version_id, UUID):
        raise DurablePlanError("target_version_id must be a UUID", reason_code="invalid_graph")
    if (
        not isinstance(target_digest, str)
        or len(target_digest) != 64
        or any(c not in "0123456789abcdef" for c in target_digest)
    ):
        raise DurablePlanError("target_digest must be sha256 hex", reason_code="invalid_graph")

    nested_inputs: Mapping[str, Any] = (
        dict(nested_workflow_inputs) if isinstance(nested_workflow_inputs, Mapping) else {}
    )
    deps = _deps_by_path(dependencies)

    if target_kind == "agent":
        # Agent target plans as a single synthetic agent node using the snapshot
        # as agent config. Kept minimal for Task 2; workflow is the golden path.
        if not isinstance(workflow_input, Mapping):
            raise DurablePlanError(
                "agent snapshot must be a mapping",
                reason_code="invalid_graph",
            )
        synthetic = {
            "node_id": "agent_root",
            "node_type": "agent",
            "config": dict(workflow_input),
        }
        node_plan = _plan_node(
            synthetic,
            outgoing=(),
            deps=deps,
            path_prefix="root",
            depth=0,
            nested_workflow_inputs=nested_inputs,
            visited_workflow_versions=frozenset(),
        )
        nodes = (node_plan,)
        plan_digest = compute_plan_digest(
            target_kind=target_kind,
            target_version_id=target_version_id,
            target_digest=target_digest,
            entry_node_id="agent_root",
            nodes=nodes,
        )
        return DurableExecutionPlanV1(
            target_kind=target_kind,
            target_version_id=target_version_id,
            target_digest=target_digest,
            entry_node_id="agent_root",
            nodes=nodes,
            plan_digest=plan_digest,
        )

    entry, planned = _plan_workflow_nodes(
        workflow_input=workflow_input,
        deps=deps,
        path_prefix="root",
        depth=0,
        nested_workflow_inputs=nested_inputs,
        visited_workflow_versions=frozenset({target_version_id}),
    )

    # Aggregate business side-effect max must stay within allowed set.
    biz = business_side_effect_maximum_from_nodes(planned)
    if biz not in _ALLOWED_BUSINESS_SIDE_EFFECTS:
        raise DurablePlanError(
            f"aggregate business side effect {biz!r} denied for durable execution",
            reason_code="denied_side_effect",
        )

    # Interrupt-capable plans are never parallel_safe (enforced at descriptor level).
    nodes_tuple = tuple(planned)
    plan_digest = compute_plan_digest(
        target_kind=target_kind,
        target_version_id=target_version_id,
        target_digest=target_digest,
        entry_node_id=entry,
        nodes=nodes_tuple,
    )
    return DurableExecutionPlanV1(
        target_kind=target_kind,
        target_version_id=target_version_id,
        target_digest=target_digest,
        entry_node_id=entry,
        nodes=nodes_tuple,
        plan_digest=plan_digest,
    )


def business_side_effect_maximum_from_nodes(
    nodes: Sequence[DurableNodePlanV1],
) -> SideEffectClass:
    acc: SideEffectClass = "none"
    for node in nodes:
        # may_interrupt control bookkeeping already stores business_side_effect=none
        # for human_in_loop; still only fold declared business_side_effect.
        acc = _max_side_effect(acc, node.business_side_effect)
    return acc


def business_side_effect_maximum(plan: DurableExecutionPlanV1) -> SideEffectClass:
    return business_side_effect_maximum_from_nodes(plan.nodes)


def plan_allows_durable_interrupt(plan: DurableExecutionPlanV1) -> bool:
    return any(n.may_interrupt for n in plan.nodes)


def plan_parallel_safe(plan: DurableExecutionPlanV1) -> bool:
    """Interrupt-capable durable plans are never parallel_safe."""
    if plan_allows_durable_interrupt(plan):
        return False
    return True


# ---------------------------------------------------------------------------
# Surface / binding helpers (new-publish-only)
# ---------------------------------------------------------------------------


def plan_durable_execution_from_surface(surface: Any) -> DurableExecutionPlanV1:
    """Derive plan from a ResolvedCapabilitySurface's exact frozen target."""
    from app.assistant.capabilities.ports import (
        ExecutableAgentVersionTarget,
        ExecutableWorkflowVersionTarget,
    )

    binding = surface.binding
    resolved = binding.resolved
    executable = surface.executable
    deps = tuple(resolved.dependencies)
    nested_inputs = _nested_workflow_inputs_from_closure(
        getattr(surface, "execution_closure", None)
    )

    if isinstance(executable, ExecutableWorkflowVersionTarget):
        target_digest = str(resolved.config_digest or resolved.resolution_digest)
        return plan_durable_execution(
            target_kind="workflow",
            target_version_id=executable.version_id,
            target_digest=target_digest,
            workflow_input=executable.parsed_published_input,
            dependencies=deps,
            nested_workflow_inputs=nested_inputs,
        )
    if isinstance(executable, ExecutableAgentVersionTarget):
        target_digest = str(resolved.config_digest or resolved.resolution_digest)
        snapshot = getattr(executable, "parsed_snapshot", None)
        if snapshot is None:
            snapshot = {}
        return plan_durable_execution(
            target_kind="agent",
            target_version_id=executable.version_id,
            target_digest=target_digest,
            workflow_input=snapshot,
            dependencies=deps,
            nested_workflow_inputs=nested_inputs,
        )
    raise DurablePlanError(
        "surface executable is not a workflow/agent target",
        reason_code="unsupported_node",
    )


def attach_durable_plan_extension(
    snapshot: Mapping[str, Any],
    plan: DurableExecutionPlanV1,
) -> tuple[dict[str, Any], str]:
    """Return a copy of a schemaVersion=1 binding snapshot with a versioned extension.

    Old snapshots without this call remain byte-identical (no unversioned nullable
    field). The extension is nested under ``extensions.durableExecutionPlanV1`` so
    absent extensions do not change Plan 01 fixed digest vectors.
    """
    if not isinstance(snapshot, Mapping):
        raise DurablePlanError("snapshot must be a mapping", reason_code="invalid_graph")
    if int(snapshot.get("schemaVersion") or 0) != 1:
        raise DurablePlanError(
            "only schemaVersion=1 binding snapshots may gain durable extensions",
            reason_code="invalid_graph",
        )
    payload = copy.deepcopy(dict(snapshot))
    payload.pop("bindingContractDigest", None)
    extensions = payload.get("extensions")
    if extensions is None:
        extensions = {}
    elif not isinstance(extensions, dict):
        raise DurablePlanError("extensions must be a mapping", reason_code="invalid_graph")
    else:
        extensions = copy.deepcopy(extensions)
    extensions[DURABLE_PLAN_EXTENSION_KEY] = {
        "contractVersion": DURABLE_PLAN_EXTENSION_CONTRACT_VERSION,
        "planDigest": plan.plan_digest,
        "targetKind": plan.target_kind,
        "targetVersionId": str(plan.target_version_id),
        "targetDigest": plan.target_digest,
        "entryNodeId": plan.entry_node_id,
    }
    payload["extensions"] = extensions
    digest = sha256_canonical_json(payload)
    payload["bindingContractDigest"] = digest
    return payload, digest


def extract_durable_plan_digest(snapshot: Mapping[str, Any] | None) -> str | None:
    if not isinstance(snapshot, Mapping):
        return None
    extensions = snapshot.get("extensions")
    if not isinstance(extensions, Mapping):
        return None
    block = extensions.get(DURABLE_PLAN_EXTENSION_KEY)
    if not isinstance(block, Mapping):
        return None
    digest = block.get("planDigest")
    if isinstance(digest, str) and len(digest) == 64:
        return digest
    return None


def publish_durable_binding_snapshot(
    resolved: ResolvedCapabilityBinding,
    *,
    plan: DurableExecutionPlanV1,
) -> ResolvedCapabilityBinding:
    """Return a new ResolvedCapabilityBinding with durable plan extension frozen in.

    Does not mutate the input binding. Call only for newly published versions.
    """
    if not isinstance(resolved, ResolvedCapabilityBinding):
        raise TypeError("resolved must be a ResolvedCapabilityBinding")
    snap = resolved.resolution_snapshot
    if not isinstance(snap, Mapping):
        raise DurablePlanError("resolution_snapshot must be a mapping", reason_code="invalid_graph")
    # Require plan digest match target identity.
    if plan.target_version_id != resolved.target_version_id:
        raise DurablePlanError(
            "plan target_version_id does not match binding",
            reason_code="invalid_graph",
        )
    new_snap, new_digest = attach_durable_plan_extension(snap, plan)
    return resolved.model_copy(
        update={
            "resolution_snapshot": new_snap,
            "binding_contract_digest": new_digest,
        }
    )


__all__ = [
    "DURABLE_PLAN_EXTENSION_CONTRACT_VERSION",
    "DURABLE_PLAN_EXTENSION_KEY",
    "DurablePlanError",
    "attach_durable_plan_extension",
    "business_side_effect_maximum",
    "business_side_effect_maximum_from_nodes",
    "extract_durable_plan_digest",
    "plan_allows_durable_interrupt",
    "plan_durable_execution",
    "plan_durable_execution_from_surface",
    "plan_parallel_safe",
    "publish_durable_binding_snapshot",
]
