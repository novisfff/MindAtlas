"""Plan 07 Task 3: durable Workflow/Agent node adapters.

Ephemeral one-boundary adapters selected by exact ``adapter_key`` from a frozen
``DurableExecutionPlanV1``. Never call ``graph.invoke()`` or business Tools
directly — only Gateway none|read|compute through injected ports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID, uuid4

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.workflow.durable.contracts import (
    DurableBranchDecisionV1,
    DurableCallFrameV1,
    DurableEdgeV1,
    DurableExecutionPlanV1,
    DurableLoopCursorV1,
    DurableNodePlanV1,
    DurablePauseProposalV1,
    DurableWorkflowStateV1,
    compute_branch_decision_digest,
    compute_loop_cursor_digest,
    compute_proposal_digest,
    derive_frame_id,
    derive_interrupt_id,
    derive_node_visit_id,
    build_root_continuation,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DurableAdapterError(ValueError):
    """Fail-closed durable adapter/runtime error."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# Portable node bag (never WorkflowState / never serialized as Legacy)
# ---------------------------------------------------------------------------


@dataclass
class PortableNodeBag:
    """Ephemeral portable values for pure node evaluation.

    Process-local by default. May be projected to a portable snapshot that the
    runner rehydrates after cross-process recovery (Task 3 minimal durability).
    """

    inputs: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    env_vars: dict[str, Any] = field(default_factory=dict)
    sys_vars: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    # Synthetic artifact ids allocated during this unit (deterministic when seeded).
    artifact_ids: list[UUID] = field(default_factory=list)

    def output_of(self, node_id: str) -> dict[str, Any]:
        return dict(self.node_outputs.get(node_id) or {})

    def set_output(self, node_id: str, payload: Mapping[str, Any]) -> None:
        self.node_outputs[node_id] = dict(payload)

    def to_snapshot(self) -> dict[str, Any]:
        """Project bag into a JSON-friendly portable dict for material/checkpoint."""
        return {
            "inputs": dict(self.inputs),
            "nodeOutputs": {k: dict(v) for k, v in self.node_outputs.items()},
            "envVars": dict(self.env_vars),
            "sysVars": dict(self.sys_vars),
            "variables": dict(self.variables),
            "artifactIds": [str(a) for a in self.artifact_ids],
        }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any] | None) -> PortableNodeBag:
        """Rebuild a bag from a portable snapshot (empty bag if snapshot is None)."""
        if not snapshot:
            return cls()
        arts_raw = snapshot.get("artifactIds") or snapshot.get("artifact_ids") or []
        arts: list[UUID] = []
        for item in arts_raw:
            try:
                arts.append(item if isinstance(item, UUID) else UUID(str(item)))
            except (TypeError, ValueError):
                continue
        node_outputs_raw = snapshot.get("nodeOutputs") or snapshot.get("node_outputs") or {}
        node_outputs: dict[str, dict[str, Any]] = {}
        if isinstance(node_outputs_raw, Mapping):
            for k, v in node_outputs_raw.items():
                if isinstance(v, Mapping):
                    node_outputs[str(k)] = dict(v)
        return cls(
            inputs=dict(snapshot.get("inputs") or {}),
            node_outputs=node_outputs,
            env_vars=dict(snapshot.get("envVars") or snapshot.get("env_vars") or {}),
            sys_vars=dict(snapshot.get("sysVars") or snapshot.get("sys_vars") or {}),
            variables=dict(snapshot.get("variables") or {}),
            artifact_ids=arts,
        )


# ---------------------------------------------------------------------------
# Adapter context + result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AdapterExecutionContext:
    """Exact-dependency context for one node/agent-round boundary."""

    run_id: UUID
    root_invocation_digest: str
    root_call_id: str
    plan: DurableExecutionPlanV1
    node: DurableNodePlanV1
    frame: DurableCallFrameV1
    workflow_state: DurableWorkflowStateV1
    node_visit_id: str
    node_config: Mapping[str, Any]
    bag: PortableNodeBag
    # Nested child materials keyed by target_version_id str
    child_materials: Mapping[str, Any] = field(default_factory=dict)
    # Optional Gateway / exact dependency ports (structural)
    capability_gateway: Any = None
    exact_dependency_resolver: Any = None
    # Agent round hook (tests inject; production wires Plan 03 loop)
    agent_round_executor: Callable[..., Mapping[str, Any]] | None = None
    # Worker-unit ephemeral pause staging port (Plan 07 §5.3). Never serialized.
    pause_effect_port: Any = None


@dataclass(slots=True)
class AdapterBoundaryResult:
    """Pure result of one node/agent-round adapter invocation."""

    kind: str  # node_completed | child_pushed | child_completed | human_pause | root_completed | failed
    next_node_id: str | None = None
    branch_decision: DurableBranchDecisionV1 | None = None
    loop_cursor: DurableLoopCursorV1 | None = None
    child_frame: DurableCallFrameV1 | None = None
    pause_proposal: DurablePauseProposalV1 | None = None
    agent_loop_continuation: Any = None
    output_artifact_id: UUID | None = None
    bag: PortableNodeBag | None = None
    reason_code: str | None = None
    detail: str | None = None


class DurableNodeAdapter(Protocol):
    adapter_key: str

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult: ...


# ---------------------------------------------------------------------------
# Shared pure helpers
# ---------------------------------------------------------------------------


def _resolve_variable(variable: str, bag: PortableNodeBag) -> str:
    variable = (variable or "").strip()
    if not variable:
        return ""
    if variable.startswith("sys."):
        key = variable.split(".", 1)[1] if "." in variable else ""
        val = bag.sys_vars.get(key, "")
        return "" if val is None else str(val)
    if variable.startswith("env."):
        key = variable.split(".", 1)[1] if "." in variable else ""
        val = bag.env_vars.get(key, "")
        return "" if val is None else str(val)
    if variable.startswith("start."):
        key = variable.split(".", 1)[1] if "." in variable else ""
        # Prefer start node output, then root inputs
        start_out = bag.output_of("start")
        if key in start_out:
            val = start_out[key]
            return "" if val is None else str(val)
        json_fields = start_out.get("json_fields") or {}
        if isinstance(json_fields, Mapping) and key in json_fields:
            val = json_fields[key]
            return "" if val is None else str(val)
        val = bag.inputs.get(key, "")
        return "" if val is None else str(val)
    parts = variable.split(".", 1)
    ref_node = parts[0]
    ref_field = parts[1] if len(parts) > 1 else "text"
    out = bag.output_of(ref_node)
    json_fields = out.get("json_fields") or {}
    if isinstance(json_fields, Mapping) and ref_field in json_fields:
        val = json_fields[ref_field]
        return "" if val is None else str(val)
    if ref_field in out:
        val = out[ref_field]
        return "" if val is None else str(val)
    return "" if out.get("text") is None else str(out.get("text", ""))


def _eval_condition(actual: str, operator: str, value: str) -> bool:
    op = (operator or "eq").strip().lower()
    if op in {"eq", "equals", "=="}:
        return actual == value
    if op in {"neq", "not_equals", "!="}:
        return actual != value
    if op in {"contains"}:
        return value in actual
    if op in {"not_contains"}:
        return value not in actual
    if op in {"empty", "is_empty"}:
        return actual == ""
    if op in {"not_empty", "is_not_empty"}:
        return actual != ""
    if op in {"gt", ">"}:
        try:
            return float(actual) > float(value)
        except (TypeError, ValueError):
            return actual > value
    if op in {"gte", ">="}:
        try:
            return float(actual) >= float(value)
        except (TypeError, ValueError):
            return actual >= value
    if op in {"lt", "<"}:
        try:
            return float(actual) < float(value)
        except (TypeError, ValueError):
            return actual < value
    if op in {"lte", "<="}:
        try:
            return float(actual) <= float(value)
        except (TypeError, ValueError):
            return actual <= value
    # Unknown operator → fail closed as non-match
    return False


def _next_from_single_edge(node: DurableNodePlanV1) -> str | None:
    edges = node.outgoing_edges
    if not edges:
        return None
    if len(edges) == 1:
        return edges[0].target_node_id
    # Prefer edge without handle as default
    for e in edges:
        if not e.source_handle:
            return e.target_node_id
    return edges[0].target_node_id


def _target_for_handle(node: DurableNodePlanV1, handle: str) -> str | None:
    for e in node.outgoing_edges:
        if (e.source_handle or "") == handle:
            return e.target_node_id
    # else fallback
    for e in node.outgoing_edges:
        if (e.source_handle or "") in {"else", "false", "default"}:
            return e.target_node_id
    return _next_from_single_edge(node)


def _allocate_artifact_id(bag: PortableNodeBag, *, seed: str) -> UUID:
    # Deterministic artifact id from seed for stable retries of pure nodes.
    # Uses uuid5-like via sha256 truncated into UUID format.
    digest = sha256_canonical_json({"seed": seed})
    # Construct UUID from first 32 hex chars of digest.
    hex32 = digest[:32]
    art = UUID(hex=hex32)
    bag.artifact_ids.append(art)
    return art


# ---------------------------------------------------------------------------
# Concrete adapters
# ---------------------------------------------------------------------------


class StartAdapter:
    adapter_key = "start.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        # Initialize bag inputs as start outputs
        payload = {
            "text": "",
            "json_fields": dict(ctx.bag.inputs),
            **{k: v for k, v in ctx.bag.inputs.items()},
        }
        ctx.bag.set_output(ctx.node.node_id, payload)
        art = _allocate_artifact_id(
            ctx.bag,
            seed=f"start|{ctx.frame.frame_id}|{ctx.node_visit_id}",
        )
        next_id = _next_from_single_edge(ctx.node)
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=next_id,
            output_artifact_id=art,
            bag=ctx.bag,
        )


class OutputAdapter:
    adapter_key = "output.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        # Project final-ish output from bag
        cfg = dict(ctx.node_config or {})
        text = cfg.get("text")
        if text is None:
            # Prefer last non-start output text
            text = ""
            for nid, out in ctx.bag.node_outputs.items():
                if nid == "start":
                    continue
                if isinstance(out, Mapping) and out.get("text") is not None:
                    text = out.get("text")
        payload = {"text": text, "json_fields": {"text": text}}
        ctx.bag.set_output(ctx.node.node_id, payload)
        art = _allocate_artifact_id(
            ctx.bag,
            seed=f"output|{ctx.frame.frame_id}|{ctx.node_visit_id}",
        )
        is_root = ctx.frame.parent_frame_id is None
        if is_root:
            return AdapterBoundaryResult(
                kind="root_completed",
                next_node_id=None,
                output_artifact_id=art,
                bag=ctx.bag,
            )
        return AdapterBoundaryResult(
            kind="child_completed",
            next_node_id=None,
            output_artifact_id=art,
            bag=ctx.bag,
        )


class IfElseAdapter:
    adapter_key = "if_else.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        cfg = dict(ctx.node_config or {})
        branches = cfg.get("branches") or []
        else_handle = str(cfg.get("else_handle") or "else")
        chosen = else_handle
        if isinstance(branches, list):
            for branch in branches:
                if not isinstance(branch, Mapping):
                    continue
                branch_handle = str(branch.get("id") or "").strip()
                if not branch_handle:
                    continue
                logic = str(branch.get("logic") or "and").strip().lower()
                if logic not in {"and", "or"}:
                    logic = "and"
                conditions = branch.get("conditions")
                if not isinstance(conditions, list) or not conditions:
                    continue
                results: list[bool] = []
                for cond in conditions:
                    if not isinstance(cond, Mapping):
                        continue
                    variable = str(cond.get("variable") or "").strip()
                    operator = str(cond.get("operator") or "eq").strip()
                    value = cond.get("value")
                    rhs = "" if value is None else str(value)
                    actual = _resolve_variable(variable, ctx.bag)
                    results.append(_eval_condition(actual, operator, rhs))
                if not results:
                    continue
                matched = all(results) if logic == "and" else any(results)
                if matched:
                    chosen = branch_handle
                    break
        target = _target_for_handle(ctx.node, chosen)
        if target is None:
            raise DurableAdapterError(
                f"if_else node {ctx.node.node_id!r} has no edge for handle {chosen!r}",
                reason_code="invalid_branch_edge",
            )
        decision = DurableBranchDecisionV1(
            node_id=ctx.node.node_id,
            node_visit_id=ctx.node_visit_id,
            chosen_handle=chosen,
            chosen_target_node_id=target,
            decision_digest=compute_branch_decision_digest(
                node_id=ctx.node.node_id,
                node_visit_id=ctx.node_visit_id,
                chosen_handle=chosen,
                chosen_target_node_id=target,
            ),
        )
        ctx.bag.set_output(
            ctx.node.node_id,
            {"text": chosen, "json_fields": {"handle": chosen}},
        )
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=target,
            branch_decision=decision,
            bag=ctx.bag,
        )


class VariableAssignAdapter:
    adapter_key = "variable_assign.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        cfg = dict(ctx.node_config or {})
        name = str(cfg.get("variableName") or cfg.get("variable_name") or "").strip()
        value = cfg.get("value")
        if name:
            ctx.bag.variables[name] = value
            ctx.bag.env_vars[name] = value
        ctx.bag.set_output(
            ctx.node.node_id,
            {"text": str(value) if value is not None else "", "json_fields": {"name": name, "value": value}},
        )
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=_next_from_single_edge(ctx.node),
            bag=ctx.bag,
        )


class IterationAdapter:
    adapter_key = "iteration.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        cfg = dict(ctx.node_config or {})
        items = cfg.get("items")
        if items is None:
            items = ctx.bag.inputs.get("items") or []
        if not isinstance(items, (list, tuple)):
            items = list(items) if items is not None else []
        max_iterations = cfg.get("max_iterations") or cfg.get("maxIterations")
        try:
            max_iter = int(max_iterations) if max_iterations is not None else len(items)
        except (TypeError, ValueError):
            max_iter = len(items)
        max_iter = max(0, min(max_iter, len(items)))

        # Find existing cursor for this loop node
        existing: DurableLoopCursorV1 | None = None
        for cur in ctx.frame.loop_cursors:
            if cur.loop_node_id == ctx.node.node_id:
                existing = cur
                break
        next_index = 0 if existing is None else int(existing.iteration_index) + 1

        if next_index >= max_iter:
            # Loop complete → advance to next node after loop
            ctx.bag.set_output(
                ctx.node.node_id,
                {
                    "text": "done",
                    "json_fields": {
                        "iterations": next_index,
                        "completed": True,
                    },
                },
            )
            return AdapterBoundaryResult(
                kind="node_completed",
                next_node_id=_next_from_single_edge(ctx.node),
                loop_cursor=existing,
                bag=ctx.bag,
            )

        item = items[next_index] if next_index < len(items) else None
        item_key = str(next_index)
        completed_ids = (
            () if existing is None else existing.completed_child_output_artifact_ids
        )
        # Synthetic completed child output for this iteration (body is pure/empty in Task 3)
        child_art = _allocate_artifact_id(
            ctx.bag,
            seed=f"loop|{ctx.frame.frame_id}|{ctx.node.node_id}|{next_index}",
        )
        new_completed = completed_ids + (child_art,)
        cursor = DurableLoopCursorV1(
            loop_node_id=ctx.node.node_id,
            node_visit_id=ctx.node_visit_id,
            iteration_index=next_index,
            item_key=item_key,
            completed_child_output_artifact_ids=new_completed,
            cursor_digest=compute_loop_cursor_digest(
                loop_node_id=ctx.node.node_id,
                node_visit_id=ctx.node_visit_id,
                iteration_index=next_index,
                item_key=item_key,
                completed_child_output_artifact_ids=new_completed,
            ),
        )
        ctx.bag.set_output(
            ctx.node.node_id,
            {
                "text": str(item) if item is not None else "",
                "json_fields": {
                    "iteration_index": next_index,
                    "item": item,
                    "completed": False,
                },
            },
        )
        # Stay on loop node for next iteration (runner keeps current_node_id=loop)
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=ctx.node.node_id,  # re-enter loop
            loop_cursor=cursor,
            bag=ctx.bag,
        )


class HumanInLoopAdapter:
    adapter_key = "human_in_loop.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        cfg = dict(ctx.node_config or {})
        kind_raw = str(cfg.get("kind") or cfg.get("mode") or "approval").strip().lower()
        kind = "input" if kind_raw == "input" else "approval"
        title = str(cfg.get("title") or cfg.get("prompt") or "Human approval required")
        field_schema = cfg.get("field_schema") or cfg.get("fieldSchema")
        if field_schema is not None and not isinstance(field_schema, Mapping):
            field_schema = None
        initial_values = cfg.get("initial_values") or cfg.get("initialValues") or {}
        if not isinstance(initial_values, Mapping):
            initial_values = {}
        interrupt_id = derive_interrupt_id(
            run_id=ctx.run_id,
            root_invocation_digest=ctx.root_invocation_digest,
            frame_id=ctx.frame.frame_id,
            node_visit_id=ctx.node_visit_id,
            logical_interrupt_ordinal=1,
        )
        root_continuation = build_root_continuation(
            root_frame_id=ctx.workflow_state.root_frame_id,
            root_invocation_digest=ctx.root_invocation_digest,
        )
        # Proposed state: top frame waiting, pending interrupt set
        top = ctx.frame
        waiting_frame = DurableCallFrameV1(
            frame_id=top.frame_id,
            parent_frame_id=top.parent_frame_id,
            invocation_call_id=top.invocation_call_id,
            owner_skill_package_id=top.owner_skill_package_id,
            owner_skill_version_id=top.owner_skill_version_id,
            target_kind=top.target_kind,
            target_id=top.target_id,
            target_version_id=top.target_version_id,
            target_digest=top.target_digest,
            execution_plan_digest=top.execution_plan_digest,
            current_node_id=ctx.node.node_id,
            node_visit_id=ctx.node_visit_id,
            node_visit_ordinal=top.node_visit_ordinal,
            execution_attempt=top.execution_attempt,
            phase="waiting",
            node_state_artifact_id=top.node_state_artifact_id,
            node_output_artifact_ids=top.node_output_artifact_ids,
            branch_decisions=top.branch_decisions,
            loop_cursors=top.loop_cursors,
            child_frame_ids=top.child_frame_ids,
            agent_loop_continuation=top.agent_loop_continuation,
        )
        stack = tuple(ctx.workflow_state.frame_stack[:-1]) + (waiting_frame,)
        proposed = DurableWorkflowStateV1(
            run_id=ctx.workflow_state.run_id,
            root_frame_id=ctx.workflow_state.root_frame_id,
            root_invocation_digest=ctx.workflow_state.root_invocation_digest,
            frame_stack=stack,
            pending_interrupt_id=interrupt_id,
            terminal_output_artifact_id=ctx.workflow_state.terminal_output_artifact_id,
        )
        request_payload: dict[str, Any] = {"title": title}
        proposal = DurablePauseProposalV1(
            run_id=ctx.run_id,
            root_call_id=ctx.root_call_id,
            root_continuation=root_continuation,
            frame_id=ctx.frame.frame_id,
            node_id=ctx.node.node_id,
            node_visit_id=ctx.node_visit_id,
            interrupt_id=interrupt_id,
            kind=kind,  # type: ignore[arg-type]
            request_payload=request_payload,
            field_schema=dict(field_schema) if field_schema is not None else None,
            initial_values=dict(initial_values),
            proposed_workflow_state=proposed,
            proposal_digest=compute_proposal_digest(
                run_id=ctx.run_id,
                root_call_id=ctx.root_call_id,
                root_continuation=root_continuation,
                frame_id=ctx.frame.frame_id,
                node_id=ctx.node.node_id,
                node_visit_id=ctx.node_visit_id,
                interrupt_id=interrupt_id,
                kind=kind,
                request_payload=request_payload,
                field_schema=dict(field_schema) if field_schema is not None else None,
                initial_values=dict(initial_values),
                proposed_workflow_state=proposed,
            ),
        )
        # Stage pure proposal on the worker-unit port when injected. Never call
        # HumanLoopRuntime.create_and_wait from durable execution.
        port = ctx.pause_effect_port
        if port is not None:
            stage = getattr(port, "stage", None)
            if not callable(stage):
                raise DurableAdapterError(
                    "pause_effect_port missing stage()",
                    reason_code="durable_pause_protocol_error",
                )
            stage(proposal)
        return AdapterBoundaryResult(
            kind="human_pause",
            pause_proposal=proposal,
            bag=ctx.bag,
        )


class WorkflowCallAdapter:
    adapter_key = "workflow_call.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        deps = ctx.node.dependency_refs
        if not deps:
            raise DurableAdapterError(
                f"workflow_call {ctx.node.node_id!r} missing frozen workflow dependency",
                reason_code="missing_child_plan",
            )
        dep = deps[0]
        if dep.dependency_type != "workflow" or dep.target_version_id is None:
            raise DurableAdapterError(
                f"workflow_call {ctx.node.node_id!r} requires pinned workflow dependency",
                reason_code="mutable_target_lookup",
            )
        child_key = str(dep.target_version_id)
        child_mat = ctx.child_materials.get(child_key)
        if child_mat is None:
            raise DurableAdapterError(
                f"workflow_call {ctx.node.node_id!r} missing child material for {child_key}",
                reason_code="missing_child_plan",
            )
        child_plan: DurableExecutionPlanV1 = getattr(child_mat, "plan", child_mat)
        if not isinstance(child_plan, DurableExecutionPlanV1):
            raise DurableAdapterError(
                "child material must expose DurableExecutionPlanV1",
                reason_code="missing_child_plan",
            )
        invocation_call_id = f"{ctx.frame.invocation_call_id}:{ctx.node.node_id}:{ctx.node_visit_id}"
        child_frame_id = derive_frame_id(
            root_invocation_digest=ctx.root_invocation_digest,
            parent_path=f"{ctx.frame.frame_id}/{ctx.node.node_id}",
            target_version_id=child_plan.target_version_id,
            invocation_call_id=invocation_call_id,
        )
        child_frame = DurableCallFrameV1(
            frame_id=child_frame_id,
            parent_frame_id=ctx.frame.frame_id,
            invocation_call_id=invocation_call_id,
            owner_skill_package_id=ctx.frame.owner_skill_package_id,
            owner_skill_version_id=ctx.frame.owner_skill_version_id,
            target_kind="workflow",
            target_id=dep.target_version_id,  # stable enough; exact target_id may equal version in tests
            target_version_id=child_plan.target_version_id,
            target_digest=child_plan.target_digest,
            execution_plan_digest=child_plan.plan_digest,
            current_node_id=child_plan.entry_node_id,
            node_visit_id=None,
            node_visit_ordinal=0,
            execution_attempt=1,
            phase="ready",
        )
        # Record the parent call-node successor so CHILD_COMPLETED can advance
        # without fixture-hardcoded node id maps.
        return_target = _next_from_single_edge(ctx.node)
        branch_decision = None
        if return_target is not None:
            branch_decision = DurableBranchDecisionV1(
                node_id=ctx.node.node_id,
                node_visit_id=ctx.node_visit_id,
                chosen_handle="return",
                chosen_target_node_id=return_target,
                decision_digest=compute_branch_decision_digest(
                    node_id=ctx.node.node_id,
                    node_visit_id=ctx.node_visit_id,
                    chosen_handle="return",
                    chosen_target_node_id=return_target,
                ),
            )
        return AdapterBoundaryResult(
            kind="child_pushed",
            next_node_id=return_target,
            branch_decision=branch_decision,
            child_frame=child_frame,
            bag=ctx.bag,
        )


class AgentCallAdapter:
    """Parent-frame agent node that pushes a nested Agent child frame."""

    adapter_key = "agent.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        # Agent root frame (target_kind=agent, single node): run one agent round.
        if ctx.frame.target_kind == "agent" and ctx.plan.target_kind == "agent":
            return _execute_agent_round(ctx)

        # Parent workflow agent node → push child agent frame when deps present.
        deps = ctx.node.dependency_refs
        if deps:
            dep = deps[0]
            if dep.dependency_type != "agent" or dep.target_version_id is None:
                raise DurableAdapterError(
                    f"agent node {ctx.node.node_id!r} requires pinned agent dependency",
                    reason_code="mutable_target_lookup",
                )
            child_key = str(dep.target_version_id)
            child_mat = ctx.child_materials.get(child_key)
            if child_mat is None:
                raise DurableAdapterError(
                    f"agent node {ctx.node.node_id!r} missing child material for {child_key}",
                    reason_code="missing_child_plan",
                )
            child_plan: DurableExecutionPlanV1 = getattr(child_mat, "plan", child_mat)
            if not isinstance(child_plan, DurableExecutionPlanV1):
                raise DurableAdapterError(
                    "child material must expose DurableExecutionPlanV1",
                    reason_code="missing_child_plan",
                )
            invocation_call_id = (
                f"{ctx.frame.invocation_call_id}:{ctx.node.node_id}:{ctx.node_visit_id}"
            )
            child_frame_id = derive_frame_id(
                root_invocation_digest=ctx.root_invocation_digest,
                parent_path=f"{ctx.frame.frame_id}/{ctx.node.node_id}",
                target_version_id=child_plan.target_version_id,
                invocation_call_id=invocation_call_id,
            )
            child_frame = DurableCallFrameV1(
                frame_id=child_frame_id,
                parent_frame_id=ctx.frame.frame_id,
                invocation_call_id=invocation_call_id,
                owner_skill_package_id=ctx.frame.owner_skill_package_id,
                owner_skill_version_id=ctx.frame.owner_skill_version_id,
                target_kind="agent",
                target_id=dep.target_version_id,
                target_version_id=child_plan.target_version_id,
                target_digest=child_plan.target_digest,
                execution_plan_digest=child_plan.plan_digest,
                current_node_id=child_plan.entry_node_id,
                node_visit_id=None,
                node_visit_ordinal=0,
                execution_attempt=1,
                phase="ready",
            )
            # Record parent call-node successor for CHILD_COMPLETED advance.
            return_target = _next_from_single_edge(ctx.node)
            branch_decision = None
            if return_target is not None:
                branch_decision = DurableBranchDecisionV1(
                    node_id=ctx.node.node_id,
                    node_visit_id=ctx.node_visit_id,
                    chosen_handle="return",
                    chosen_target_node_id=return_target,
                    decision_digest=compute_branch_decision_digest(
                        node_id=ctx.node.node_id,
                        node_visit_id=ctx.node_visit_id,
                        chosen_handle="return",
                        chosen_target_node_id=return_target,
                    ),
                )
            return AdapterBoundaryResult(
                kind="child_pushed",
                next_node_id=return_target,
                branch_decision=branch_decision,
                child_frame=child_frame,
                bag=ctx.bag,
            )

        # Agent node without nested dep inside a workflow: treat as compute round
        return _execute_agent_round(ctx)


def _execute_agent_round(ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
    executor = ctx.agent_round_executor
    if executor is None:
        # Default: complete immediately (tests inject; production wires Plan 03)
        payload = {"text": "", "json_fields": {}}
        ctx.bag.set_output(ctx.node.node_id, payload)
        art = _allocate_artifact_id(
            ctx.bag,
            seed=f"agent|{ctx.frame.frame_id}|{ctx.node_visit_id}",
        )
        is_root = ctx.frame.parent_frame_id is None
        return AdapterBoundaryResult(
            kind="root_completed" if is_root else "child_completed",
            output_artifact_id=art,
            bag=ctx.bag,
        )
    # Exact frozen tools only; no Main Agent skill injection
    result = executor(
        frame=ctx.frame,
        node=ctx.node,
        plan=ctx.plan,
        bag=ctx.bag,
        node_visit_id=ctx.node_visit_id,
        allow_main_agent_skills=False,
        skill_injection=None,
    )
    if not isinstance(result, Mapping):
        raise DurableAdapterError(
            "agent_round_executor must return a mapping",
            reason_code="agent_round_invalid_result",
        )
    status = str(result.get("status") or "completed")
    output = result.get("output") or {}
    if not isinstance(output, Mapping):
        output = {"text": str(output)}
    ctx.bag.set_output(ctx.node.node_id, dict(output))
    cont = result.get("agent_loop_continuation")
    art = _allocate_artifact_id(
        ctx.bag,
        seed=f"agent|{ctx.frame.frame_id}|{ctx.node_visit_id}|{status}",
    )
    if status == "continue":
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=ctx.node.node_id,  # another agent round
            agent_loop_continuation=cont,
            output_artifact_id=art,
            bag=ctx.bag,
        )
    is_root = ctx.frame.parent_frame_id is None
    return AdapterBoundaryResult(
        kind="root_completed" if is_root else "child_completed",
        agent_loop_continuation=None,
        output_artifact_id=art,
        bag=ctx.bag,
    )


class LlmAdapter:
    adapter_key = "llm.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        # Compute unit via Gateway when available; otherwise scripted empty text.
        text = ""
        gw = ctx.capability_gateway
        if gw is not None and hasattr(gw, "invoke"):
            # Structural only — tests may inject. No business write.
            try:
                out = gw.invoke(
                    kind="compute",
                    node_id=ctx.node.node_id,
                    node_visit_id=ctx.node_visit_id,
                    config=dict(ctx.node_config or {}),
                )
                if isinstance(out, Mapping):
                    text = str(out.get("text") or "")
                elif out is not None:
                    text = str(out)
            except Exception as exc:  # noqa: BLE001
                raise DurableAdapterError(
                    f"llm gateway invoke failed: {exc}",
                    reason_code="gateway_invoke_failed",
                ) from exc
        ctx.bag.set_output(
            ctx.node.node_id,
            {"text": text, "json_fields": {"text": text}},
        )
        art = _allocate_artifact_id(
            ctx.bag,
            seed=f"llm|{ctx.frame.frame_id}|{ctx.node_visit_id}",
        )
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=_next_from_single_edge(ctx.node),
            output_artifact_id=art,
            bag=ctx.bag,
        )


class ParameterExtractorAdapter(LlmAdapter):
    adapter_key = "parameter_extractor.v1"


class ToolAdapter:
    adapter_key = "tool.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        side = str(ctx.node.business_side_effect or "none")
        if side not in {"none", "read", "compute"}:
            raise DurableAdapterError(
                f"tool node {ctx.node.node_id!r} side effect {side!r} denied in Plan 07",
                reason_code="unsafe_side_effect",
            )
        gw = ctx.capability_gateway
        payload: dict[str, Any] = {"status": "ok"}
        if gw is not None and hasattr(gw, "invoke"):
            try:
                out = gw.invoke(
                    kind=side,
                    node_id=ctx.node.node_id,
                    node_visit_id=ctx.node_visit_id,
                    config=dict(ctx.node_config or {}),
                    dependency_refs=ctx.node.dependency_refs,
                )
                if isinstance(out, Mapping):
                    payload = dict(out)
            except Exception as exc:  # noqa: BLE001
                raise DurableAdapterError(
                    f"tool gateway invoke failed: {exc}",
                    reason_code="gateway_invoke_failed",
                ) from exc
        ctx.bag.set_output(ctx.node.node_id, {"text": "", "json_fields": payload})
        art = _allocate_artifact_id(
            ctx.bag,
            seed=f"tool|{ctx.frame.frame_id}|{ctx.node_visit_id}",
        )
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=_next_from_single_edge(ctx.node),
            output_artifact_id=art,
            bag=ctx.bag,
        )


class KnowledgeAdapter:
    adapter_key = "knowledge_retrieval.v1"

    def execute(self, ctx: AdapterExecutionContext) -> AdapterBoundaryResult:
        # Exact frozen read dependency through Gateway; no ambient latest KB.
        gw = ctx.capability_gateway
        text = ""
        if gw is not None and hasattr(gw, "invoke"):
            try:
                out = gw.invoke(
                    kind="read",
                    node_id=ctx.node.node_id,
                    node_visit_id=ctx.node_visit_id,
                    config=dict(ctx.node_config or {}),
                    dependency_refs=ctx.node.dependency_refs,
                )
                if isinstance(out, Mapping):
                    text = str(out.get("text") or "")
            except Exception as exc:  # noqa: BLE001
                raise DurableAdapterError(
                    f"knowledge gateway invoke failed: {exc}",
                    reason_code="gateway_invoke_failed",
                ) from exc
        ctx.bag.set_output(
            ctx.node.node_id,
            {"text": text, "json_fields": {"text": text}},
        )
        art = _allocate_artifact_id(
            ctx.bag,
            seed=f"knowledge|{ctx.frame.frame_id}|{ctx.node_visit_id}",
        )
        return AdapterBoundaryResult(
            kind="node_completed",
            next_node_id=_next_from_single_edge(ctx.node),
            output_artifact_id=art,
            bag=ctx.bag,
        )


# Alias for planner key "knowledge_retrieval" vs short "knowledge"
class KnowledgeShortAdapter(KnowledgeAdapter):
    adapter_key = "knowledge.v1"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_DEFAULT_ADAPTERS: tuple[DurableNodeAdapter, ...] = (
    StartAdapter(),
    OutputAdapter(),
    IfElseAdapter(),
    VariableAssignAdapter(),
    IterationAdapter(),
    HumanInLoopAdapter(),
    WorkflowCallAdapter(),
    AgentCallAdapter(),
    LlmAdapter(),
    ParameterExtractorAdapter(),
    ToolAdapter(),
    KnowledgeAdapter(),
    KnowledgeShortAdapter(),
)


class DefaultDurableNodeAdapterRegistry:
    """Exact adapter_key registry. Unknown keys fail closed."""

    def __init__(
        self,
        adapters: Mapping[str, DurableNodeAdapter] | None = None,
    ) -> None:
        reg: dict[str, DurableNodeAdapter] = {
            a.adapter_key: a for a in _DEFAULT_ADAPTERS
        }
        if adapters:
            reg.update(dict(adapters))
        self._adapters = reg

    def get(self, adapter_key: str) -> DurableNodeAdapter:
        key = str(adapter_key or "").strip()
        adapter = self._adapters.get(key)
        if adapter is None:
            raise DurableAdapterError(
                f"unsupported durable adapter_key: {key!r}",
                reason_code="unsupported_adapter",
            )
        return adapter

    def require(self, node: DurableNodePlanV1) -> DurableNodeAdapter:
        # Also deny known-unsafe node types even if someone forged an adapter_key
        if node.node_type in {"http_request", "code_executor"}:
            raise DurableAdapterError(
                f"node type {node.node_type!r} is not supported in Plan 07 durable runtime",
                reason_code="unsupported_adapter",
            )
        side = str(node.business_side_effect or "none")
        if side not in {"none", "read", "compute"}:
            raise DurableAdapterError(
                f"business side effect {side!r} denied in Plan 07 durable runtime",
                reason_code="unsafe_side_effect",
            )
        return self.get(node.adapter_key)


def build_default_registry() -> DefaultDurableNodeAdapterRegistry:
    return DefaultDurableNodeAdapterRegistry()


__all__ = [
    "AdapterBoundaryResult",
    "AdapterExecutionContext",
    "DefaultDurableNodeAdapterRegistry",
    "DurableAdapterError",
    "DurableNodeAdapter",
    "PortableNodeBag",
    "build_default_registry",
]
