"""Plan 07 Task 2: frozen DurableExecutionPlanV1 derivation + durable publish gate.

Covers supported matrix fail-closed behavior, golden proposal path acceptance,
durable human bookkeeping ≠ business Draft, smart_capture denial, and
new-publish-only interrupt_mode=durable without mutating old binding digests.
"""

from __future__ import annotations

import copy
import os
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-c25d03f")
os.environ.setdefault("APP_ENV", "test")

DIGEST_A = "a" * 64
TARGET_VERSION_ID = UUID("00000000-0000-4000-8000-000000000802")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    reset_caches()
    os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
    os.environ["APP_ENV"] = "test"
    from app.config import get_settings

    get_settings.cache_clear()
    from tests._db import make_session

    session = make_session()
    try:
        yield session
    finally:
        session.close()
        get_settings.cache_clear()


def _edge(
    *,
    edge_id: str,
    source: str,
    target: str,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> dict[str, Any]:
    # WorkflowInput rejects explicit null handles; omit unset handles entirely.
    payload: dict[str, Any] = {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
    }
    if source_handle is not None:
        payload["source_handle"] = source_handle
    if target_handle is not None:
        payload["target_handle"] = target_handle
    return payload


def _node(
    node_id: str,
    node_type: str,
    *,
    config: dict[str, Any] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label or node_id,
        "position_x": 0,
        "position_y": 0,
        "config": dict(config or {}),
    }


def golden_proposal_graph() -> dict[str, Any]:
    """start -> llm (compute) -> human_in_loop -> output."""
    return {
        "nodes": [
            _node("start", "start", config={"input_mode": "text"}),
            _node(
                "proposal_llm",
                "llm",
                config={
                    "model_source": "default",
                    "model_id": None,
                    "prompt": "draft a generic MindAtlas note proposal",
                },
            ),
            _node(
                "approve",
                "human_in_loop",
                config={"prompt": "Review and approve the proposal", "mode": "approval"},
            ),
            _node("output", "output", config={"output_mode": "text"}),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="proposal_llm"),
            _edge(edge_id="e2", source="proposal_llm", target="approve"),
            _edge(edge_id="e3", source="approve", target="output"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _dep(
    *,
    path: str,
    dep_type: str = "model",
    identity: str = "model:default-assistant",
    resolution_digest: str = DIGEST_A,
    dependency_digest: str | None = None,
    target_version_id: UUID | None = None,
) -> Any:
    from app.assistant.workflow.durable.contracts import FrozenExecutionDependencyRef

    return FrozenExecutionDependencyRef(
        dependency_path=path,
        dependency_type=dep_type,  # type: ignore[arg-type]
        target_identity=identity,
        target_version_id=target_version_id,
        resolution_digest=resolution_digest,
        dependency_digest=dependency_digest or DIGEST_A,
    )


def _model_dep_for_node(node_id: str) -> Any:
    return _dep(path=f"root/node:{node_id}/model", dep_type="model", identity="model:default")


def _tool_dep(
    node_id: str,
    tool_name: str,
    *,
    side: str = "read",
) -> Any:
    # Side is not on the dep; planner resolves via classification table / type.
    identity_prefix = "system-tool" if not tool_name.startswith("remote") else "remote-tool"
    return _dep(
        path=f"root/node:{node_id}/tool:{tool_name}",
        dep_type="system_tool" if identity_prefix == "system-tool" else "remote_tool",
        identity=f"{identity_prefix}:{tool_name}",
        dependency_digest=("b" if side == "read" else "c") * 64,
    )


def plan_or_raise(**kwargs: Any):
    from app.assistant.workflow.durable.planner import plan_durable_execution

    return plan_durable_execution(**kwargs)


# ---------------------------------------------------------------------------
# Golden path acceptance
# ---------------------------------------------------------------------------


def test_golden_proposal_plan_is_compute_nonparallel_durable() -> None:
    from app.assistant.domain.digests import sha256_canonical_json

    graph = golden_proposal_graph()
    target_digest = sha256_canonical_json(graph)
    plan = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=target_digest,
        workflow_input=graph,
        dependencies=(_model_dep_for_node("proposal_llm"),),
    )
    assert plan.contract_version == 1
    assert plan.target_kind == "workflow"
    assert plan.entry_node_id == "start"
    assert plan.plan_digest
    assert len(plan.plan_digest) == 64

    by_id = {n.node_id: n for n in plan.nodes}
    assert set(by_id) == {"start", "proposal_llm", "approve", "output"}
    assert by_id["start"].business_side_effect == "none"
    assert by_id["start"].may_interrupt is False
    assert by_id["proposal_llm"].business_side_effect == "compute"
    assert by_id["proposal_llm"].may_interrupt is False
    assert by_id["approve"].node_type == "human_in_loop"
    assert by_id["approve"].may_interrupt is True
    # Durable human bookkeeping is NOT a business Draft class.
    assert by_id["approve"].business_side_effect == "none"
    assert by_id["output"].business_side_effect == "none"

    from app.assistant.workflow.durable.planner import (
        business_side_effect_maximum,
        plan_allows_durable_interrupt,
    )

    assert business_side_effect_maximum(plan) == "compute"
    assert plan_allows_durable_interrupt(plan) is True
    # Interrupt-capable => not parallel-safe at plan level.
    assert plan.nodes  # non-empty


def test_plan_digest_stable_and_matches_compute_plan_digest() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.contracts import compute_plan_digest

    graph = golden_proposal_graph()
    target_digest = sha256_canonical_json(graph)
    deps = (_model_dep_for_node("proposal_llm"),)
    plan1 = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=target_digest,
        workflow_input=graph,
        dependencies=deps,
    )
    plan2 = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=target_digest,
        workflow_input=graph,
        dependencies=deps,
    )
    assert plan1.plan_digest == plan2.plan_digest
    expected = compute_plan_digest(
        target_kind=plan1.target_kind,
        target_version_id=plan1.target_version_id,
        target_digest=plan1.target_digest,
        entry_node_id=plan1.entry_node_id,
        nodes=plan1.nodes,
    )
    assert plan1.plan_digest == expected


def test_durable_human_bookkeeping_does_not_authorize_business_draft() -> None:
    """Prove Interrupt control bookkeeping ≠ business Draft class."""
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import business_side_effect_maximum

    graph = golden_proposal_graph()
    plan = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=sha256_canonical_json(graph),
        workflow_input=graph,
        dependencies=(_model_dep_for_node("proposal_llm"),),
    )
    human = next(n for n in plan.nodes if n.node_type == "human_in_loop")
    assert human.business_side_effect == "none"
    assert human.may_interrupt is True
    # Aggregate business max excludes control bookkeeping.
    assert business_side_effect_maximum(plan) == "compute"
    assert business_side_effect_maximum(plan) != "draft"


# ---------------------------------------------------------------------------
# Fail-closed matrix
# ---------------------------------------------------------------------------


def test_code_executor_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("code", "code_executor", config={"code": "print(1)"}),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="code"),
            _edge(edge_id="e2", source="code", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    assert "code_executor" in str(exc.value).lower() or exc.value.reason_code == "unsupported_node"


def test_http_request_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node(
                "http",
                "http_request",
                config={"method": "GET", "url": "https://example.com", "verify_ssl": True},
            ),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="http"),
            _edge(edge_id="e2", source="http", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    assert exc.value.reason_code in {"unsupported_node", "denied_side_effect", "http_request_denied"}


def test_write_local_tool_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("write", "tool", config={"tool_name": "create_entry"}),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="write"),
            _edge(edge_id="e2", source="write", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(_tool_dep("write", "create_entry", side="write_local"),),
        )
    assert exc.value.reason_code in {
        "denied_side_effect",
        "unsafe_tool",
        "unsupported_side_effect",
    }


def test_missing_model_dependency_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = golden_proposal_graph()
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),  # incomplete closure
        )
    assert exc.value.reason_code in {
        "incomplete_dependency_closure",
        "missing_dependency",
    }


def test_unknown_node_type_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("x", "totally_unknown_node"),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="x"),
            _edge(edge_id="e2", source="x", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    assert exc.value.reason_code == "unsupported_node"


def test_cycle_without_bounded_loop_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("a", "variable_assign", config={"assignments": []}),
            _node("b", "variable_assign", config={"assignments": []}),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="a"),
            _edge(edge_id="e2", source="a", target="b"),
            _edge(edge_id="e3", source="b", target="a"),  # cycle
            _edge(edge_id="e4", source="b", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    assert exc.value.reason_code in {"unbounded_cycle", "graph_cycle"}


def test_unbounded_iteration_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node(
                "loop",
                "iteration",
                config={
                    # no max_iterations
                    "body_nodes": [
                        _node("body_llm", "llm", config={"model_source": "default"}),
                    ],
                },
            ),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="loop"),
            _edge(edge_id="e2", source="loop", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(_model_dep_for_node("body_llm"),),
        )
    assert exc.value.reason_code in {"unbounded_loop", "incomplete_dependency_closure", "unsupported_node"}


def test_ambiguous_multiple_entry_nodes_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start_a", "start"),
            _node("start_b", "start"),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start_a", target="output"),
            _edge(edge_id="e2", source="start_b", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    assert exc.value.reason_code in {"ambiguous_entry", "invalid_graph"}


def test_missing_entry_node_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("llm", "llm", config={"model_source": "default"}),
            _node("output", "output"),
        ],
        "edges": [_edge(edge_id="e1", source="llm", target="output")],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(_model_dep_for_node("llm"),),
        )
    assert exc.value.reason_code in {"ambiguous_entry", "invalid_graph", "missing_entry"}


def test_invalid_edge_target_denied() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="missing_node"),
        ],
    }
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    assert exc.value.reason_code in {"invalid_edge", "invalid_graph"}


def test_read_tool_allowed() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import business_side_effect_maximum

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("search", "tool", config={"tool_name": "search_entries"}),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="search"),
            _edge(edge_id="e2", source="search", target="output"),
        ],
    }
    plan = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=sha256_canonical_json(graph),
        workflow_input=graph,
        dependencies=(_tool_dep("search", "search_entries", side="read"),),
    )
    assert business_side_effect_maximum(plan) == "read"
    search = next(n for n in plan.nodes if n.node_id == "search")
    assert search.business_side_effect == "read"
    assert search.may_interrupt is False


def test_if_else_and_variable_assign_supported() -> None:
    from app.assistant.domain.digests import sha256_canonical_json

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("assign", "variable_assign", config={"assignments": [{"key": "x", "value": "1"}]}),
            _node(
                "branch",
                "if_else",
                config={"condition": "{{assign.x}} == '1'"},
            ),
            _node("then_out", "output", config={"output_mode": "text"}),
            _node("else_out", "output", config={"output_mode": "text"}),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="assign"),
            _edge(edge_id="e2", source="assign", target="branch"),
            _edge(
                edge_id="e3",
                source="branch",
                target="then_out",
                source_handle="true",
            ),
            _edge(
                edge_id="e4",
                source="branch",
                target="else_out",
                source_handle="false",
            ),
        ],
    }
    plan = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=sha256_canonical_json(graph),
        workflow_input=graph,
        dependencies=(),
    )
    by_id = {n.node_id: n for n in plan.nodes}
    assert by_id["assign"].adapter_key.startswith("variable_assign")
    assert by_id["branch"].adapter_key.startswith("if_else")
    assert by_id["assign"].business_side_effect == "none"
    assert by_id["branch"].business_side_effect == "none"


def test_knowledge_retrieval_requires_frozen_kb_dependency() -> None:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError, business_side_effect_maximum

    graph = {
        "nodes": [
            _node("start", "start"),
            _node("kb", "knowledge_retrieval", config={"query": "q"}),
            _node("output", "output"),
        ],
        "edges": [
            _edge(edge_id="e1", source="start", target="kb"),
            _edge(edge_id="e2", source="kb", target="output"),
        ],
    }
    with pytest.raises(DurablePlanError):
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(graph),
            workflow_input=graph,
            dependencies=(),
        )
    plan = plan_or_raise(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=sha256_canonical_json(graph),
        workflow_input=graph,
        dependencies=(
            _dep(
                path="root/node:kb/kb/model",
                dep_type="model",
                identity="model:embed-default",
            ),
        ),
    )
    assert business_side_effect_maximum(plan) == "read"


# ---------------------------------------------------------------------------
# smart_capture denial
# ---------------------------------------------------------------------------


def test_smart_capture_system_asset_denied_for_durable() -> None:
    from pathlib import Path

    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import DurablePlanError

    asset = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "assistant"
        / "workflow"
        / "system_assets"
        / "workflows"
        / "smart_capture.json"
    )
    if not asset.is_file():
        pytest.skip("smart_capture system asset not present")
    import json

    graph = json.loads(asset.read_text(encoding="utf-8"))
    # smart_capture typically nests under a published input wrapper; accept both shapes.
    workflow_input = graph.get("nodes") and graph or graph.get("graph") or graph
    if "nodes" not in workflow_input and isinstance(graph.get("snapshot"), dict):
        workflow_input = graph["snapshot"]
    with pytest.raises(DurablePlanError) as exc:
        plan_or_raise(
            target_kind="workflow",
            target_version_id=TARGET_VERSION_ID,
            target_digest=sha256_canonical_json(workflow_input),
            workflow_input=workflow_input,
            dependencies=(),
        )
    assert exc.value.reason_code in {
        "unsupported_node",
        "denied_side_effect",
        "unsafe_tool",
        "incomplete_dependency_closure",
        "http_request_denied",
        "unsupported_side_effect",
    }


# ---------------------------------------------------------------------------
# Versioned binding-snapshot extension (new-publish-only)
# ---------------------------------------------------------------------------


def test_binding_snapshot_without_extension_digest_unchanged() -> None:
    """Old schemaVersion=1 snapshots must not change when extension helpers exist."""
    from app.assistant.domain.contracts import CapabilityCompletionContract
    from app.assistant.skills.resolution import build_binding_snapshot

    input_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "string"}
    completion = CapabilityCompletionContract(
        terminal_output=True, needs_followup=False, followup_hint=None
    )
    snap, closure, contract = build_binding_snapshot(
        capability_type="workflow",
        target_identity="workflow:" + str(uuid4()),
        target_id=uuid4(),
        target_version_id=TARGET_VERSION_ID,
        target_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=DIGEST_A,
        executable_revision=str(TARGET_VERSION_ID),
        resolution_digest=DIGEST_A,
        dependencies=(),
    )
    assert snap["schemaVersion"] == 1
    assert "extensions" not in snap
    assert "durableExecutionPlanV1" not in snap
    assert contract == snap["bindingContractDigest"]
    assert len(closure) == 64


def test_attach_durable_plan_extension_freezes_plan_digest() -> None:
    from app.assistant.domain.contracts import CapabilityCompletionContract
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.skills.resolution import build_binding_snapshot
    from app.assistant.workflow.durable.planner import (
        attach_durable_plan_extension,
        plan_durable_execution,
    )

    graph = golden_proposal_graph()
    target_digest = sha256_canonical_json(graph)
    plan = plan_durable_execution(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=target_digest,
        workflow_input=graph,
        dependencies=(_model_dep_for_node("proposal_llm"),),
    )
    input_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "string"}
    completion = CapabilityCompletionContract(
        terminal_output=True, needs_followup=False, followup_hint=None
    )
    base_snap, _, base_digest = build_binding_snapshot(
        capability_type="workflow",
        target_identity="workflow:" + str(uuid4()),
        target_id=uuid4(),
        target_version_id=TARGET_VERSION_ID,
        target_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=target_digest,
        executable_revision=str(TARGET_VERSION_ID),
        resolution_digest=DIGEST_A,
        dependencies=(),
    )
    extended, new_digest = attach_durable_plan_extension(base_snap, plan)
    assert new_digest != base_digest
    assert extended["schemaVersion"] == 1
    ext = extended["extensions"]["durableExecutionPlanV1"]
    assert ext["contractVersion"] == 1
    assert ext["planDigest"] == plan.plan_digest
    # Digest covers the extension (payload without bindingContractDigest).
    payload = {k: v for k, v in extended.items() if k != "bindingContractDigest"}
    assert sha256_canonical_json(payload) == new_digest
    assert extended["bindingContractDigest"] == new_digest


def test_reconstruct_binding_snapshot_preserves_durable_extension() -> None:
    from app.assistant.domain.contracts import CapabilityCompletionContract
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.skills.resolution import (
        build_binding_snapshot,
        reconstruct_binding_snapshot,
    )
    from app.assistant.workflow.durable.planner import (
        attach_durable_plan_extension,
        plan_durable_execution,
    )

    graph = golden_proposal_graph()
    target_digest = sha256_canonical_json(graph)
    plan = plan_durable_execution(
        target_kind="workflow",
        target_version_id=TARGET_VERSION_ID,
        target_digest=target_digest,
        workflow_input=graph,
        dependencies=(_model_dep_for_node("proposal_llm"),),
    )
    completion = CapabilityCompletionContract(
        terminal_output=True, needs_followup=False, followup_hint=None
    )
    base_snap, _, _ = build_binding_snapshot(
        capability_type="workflow",
        target_identity="workflow:" + str(uuid4()),
        target_id=uuid4(),
        target_version_id=TARGET_VERSION_ID,
        target_revision=None,
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "string"},
        completion=completion,
        config_digest=target_digest,
        executable_revision=str(TARGET_VERSION_ID),
        resolution_digest=DIGEST_A,
        dependencies=(),
    )
    extended, digest = attach_durable_plan_extension(base_snap, plan)

    # Simulate a stored binding row + empty deps.
    class _Binding:
        capability_type = "workflow"
        target_identity = base_snap["target"]["targetIdentity"]
        resolved_tool_id = None
        resolved_workflow_version_id = TARGET_VERSION_ID
        resolved_agent_version_id = None
        resolved_revision = None
        resolution_digest = DIGEST_A
        input_schema_digest = base_snap["inputSchemaDigest"]
        output_schema_digest = base_snap["outputSchemaDigest"]
        config_digest = target_digest
        executable_revision = str(TARGET_VERSION_ID)
        dependency_closure_digest = base_snap["dependencyClosureDigest"]
        resolution_snapshot = extended

    reconstructed = reconstruct_binding_snapshot(_Binding(), [])
    assert reconstructed["bindingContractDigest"] == digest
    assert reconstructed["extensions"]["durableExecutionPlanV1"]["planDigest"] == plan.plan_digest


# ---------------------------------------------------------------------------
# New-publish-only durable descriptor path
# ---------------------------------------------------------------------------


def test_default_classification_still_legacy_blocking_for_human(db) -> None:
    """Existing Plan 02 path: human_in_loop → draft + legacy_blocking."""
    from tests.agent_skill_test_support import create_default_model_binding, create_published_workflow
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    create_default_model_binding(db)
    graph = golden_proposal_graph()
    workflow, version = create_published_workflow(
        db, name="wf_legacy_hil_default", snapshot=graph
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="workflow", key=workflow.name),)
    )[0]
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    behavior = CapabilityClassifier().classify(surface)
    assert behavior.side_effect == "draft"
    assert behavior.interrupt_mode == "legacy_blocking"


def test_classify_for_durable_publish_emits_durable_compute(db) -> None:
    from tests.agent_skill_test_support import create_default_model_binding, create_published_workflow
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver
    from app.assistant.workflow.durable.planner import (
        plan_durable_execution_from_surface,
    )

    create_default_model_binding(db)
    graph = golden_proposal_graph()
    workflow, version = create_published_workflow(
        db, name="wf_durable_publish_hil", snapshot=graph
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="workflow", key=workflow.name),)
    )[0]
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    plan = plan_durable_execution_from_surface(surface)
    behavior = CapabilityClassifier().classify_for_durable_publish(surface, plan=plan)
    assert behavior.interrupt_mode == "durable"
    assert behavior.side_effect == "compute"
    assert behavior.parallel_safe is False


def test_publish_with_durable_plan_attaches_extension_and_descriptor(db) -> None:
    """New-publish path freezes plan digest into binding snapshot + durable descriptor."""
    from tests.agent_skill_test_support import create_default_model_binding, create_published_workflow
    from app.assistant.capabilities.classification import (
        CapabilityClassifier,
        assemble_capability_descriptor,
    )
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver
    from app.assistant.workflow.durable.planner import (
        attach_durable_plan_extension,
        plan_durable_execution_from_surface,
        publish_durable_binding_snapshot,
    )

    create_default_model_binding(db)
    graph = golden_proposal_graph()
    workflow, version = create_published_workflow(
        db, name="wf_durable_ext_publish", snapshot=graph
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="workflow", key=workflow.name),)
    )[0]
    # Attach extension onto a copy of the resolved snapshot (new publish only).
    frozen_base = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen_base)
    plan = plan_durable_execution_from_surface(surface)
    new_resolved = publish_durable_binding_snapshot(resolved, plan=plan)
    assert new_resolved.binding_contract_digest != resolved.binding_contract_digest
    ext = new_resolved.resolution_snapshot["extensions"]["durableExecutionPlanV1"]
    assert ext["planDigest"] == plan.plan_digest

    frozen = project_frozen_capability_binding(
        resolved=new_resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    surface2 = CapabilityRegistry(db).resolve_surface(frozen)
    behavior = CapabilityClassifier().classify_for_durable_publish(surface2, plan=plan)
    desc = assemble_capability_descriptor(surface2, behavior)
    assert desc.behavior.interrupt_mode == "durable"
    assert desc.behavior.side_effect == "compute"
    assert desc.binding_contract_digest == new_resolved.binding_contract_digest
    # Descriptor digest covers the durable binding digest.
    assert len(desc.descriptor_digest) == 64


def test_old_binding_without_extension_unaffected_by_helpers() -> None:
    """Helpers must not rewrite snapshots that lack durable extensions."""
    from app.assistant.domain.contracts import CapabilityCompletionContract
    from app.assistant.skills.resolution import build_binding_snapshot
    from app.assistant.workflow.durable.planner import extract_durable_plan_digest

    completion = CapabilityCompletionContract(
        terminal_output=True, needs_followup=False, followup_hint=None
    )
    snap, _, digest = build_binding_snapshot(
        capability_type="tool",
        target_identity="system-tool:search_entries",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        completion=completion,
        config_digest=DIGEST_A,
        executable_revision="rev",
        resolution_digest=DIGEST_A,
        dependencies=(),
    )
    assert extract_durable_plan_digest(snap) is None
    # Re-reading snap is identity-stable.
    assert snap["bindingContractDigest"] == digest
    again = copy.deepcopy(snap)
    assert again == snap
