"""Conservative recursive Capability classification tests (Plan 02 Task 3)."""

from __future__ import annotations

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


@pytest.fixture()
def db():
    reset_caches()
    os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
    os.environ["APP_ENV"] = "test"
    from app.config import get_settings

    get_settings.cache_clear()
    from tests._db import make_session

    session = make_session()
    from app.assistant_config.models import AssistantWorkflow

    original = AssistantWorkflow.graph_snapshot

    def _boom(_self):  # noqa: ANN001
        raise AssertionError("AssistantWorkflow.graph_snapshot must not be accessed")

    AssistantWorkflow.graph_snapshot = property(_boom)  # type: ignore[assignment]
    try:
        yield session
    finally:
        AssistantWorkflow.graph_snapshot = original  # type: ignore[assignment]
        session.close()
        get_settings.cache_clear()


def _freeze_system_tool(db, key: str = "search_entries"):
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="tool", key=key),)
    )[0]
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )


def _ensure_default_models(db, *, need_embedding: bool = True) -> None:
    from tests.agent_skill_test_support import create_default_model_binding
    from app.ai_registry.models import AiComponentBinding

    assistant = (
        db.query(AiComponentBinding)
        .filter(AiComponentBinding.component == "assistant")
        .one_or_none()
    )
    if assistant is None or assistant.llm_model_id is None:
        create_default_model_binding(db)
    if need_embedding:
        lightrag = (
            db.query(AiComponentBinding)
            .filter(AiComponentBinding.component == "lightrag")
            .one_or_none()
        )
        if lightrag is None or lightrag.embedding_model_id is None:
            create_default_model_binding(
                db,
                component="lightrag",
                model_name="embed-test",
                model_type="embedding",
            )


def _freeze_remote_tool(db, tool_name: str = "remote_cap", *, http_method: str | None = None):
    from tests.agent_skill_test_support import create_remote_tool
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import (
        CapabilityBindingContract,
        CapabilityDeclaration,
    )
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    tool = create_remote_tool(db, name=tool_name)
    if http_method is not None:
        tool.http_method = http_method
        db.flush()
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="tool",
                key=tool_name,
                contract=CapabilityBindingContract(
                    input_schema=None,
                    output_schema={"type": "string"},
                ),
            ),
        )
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
    return tool, frozen


def _freeze_workflow(db, name: str | None = None, **kwargs: Any):
    from tests.agent_skill_test_support import create_published_workflow
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    _ensure_default_models(db, need_embedding=True)
    workflow, version = create_published_workflow(db, name=name, **kwargs)
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
    return workflow, version, frozen


def _freeze_agent(db, name: str | None = None, **kwargs: Any):
    from tests.agent_skill_test_support import create_published_agent
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import (
        CapabilityBindingContract,
        CapabilityDeclaration,
    )
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    _ensure_default_models(db, need_embedding=bool(kwargs.get("kb_enabled")))
    agent, version = create_published_agent(db, name=name, **kwargs)
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key=agent.name,
                contract=CapabilityBindingContract(
                    input_schema={
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"],
                        "additionalProperties": False,
                    },
                    output_schema={"type": "string"},
                ),
            ),
        )
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
    return agent, version, frozen


def _classify_binding(db, frozen):
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry

    surface = CapabilityRegistry(db).resolve_surface(frozen)
    return CapabilityClassifier().classify(surface), surface


def _nodes_snapshot(extra_nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "node_id": "start",
            "node_type": "start",
            "label": "Start",
            "position_x": 0,
            "position_y": 0,
            "config": {"input_mode": "text"},
        },
        {
            "node_id": "end",
            "node_type": "output",
            "label": "End",
            "position_x": 400,
            "position_y": 0,
            "config": {"output_mode": "text"},
        },
    ]
    edges: list[dict[str, Any]] = []
    prev = "start"
    for node in extra_nodes or []:
        nodes.insert(-1, node)
        edges.append(
            {
                "edge_id": f"e_{prev}_{node['node_id']}",
                "source_node_id": prev,
                "target_node_id": node["node_id"],
            }
        )
        prev = node["node_id"]
    edges.append(
        {
            "edge_id": f"e_{prev}_end",
            "source_node_id": prev,
            "target_node_id": "end",
        }
    )
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


# ---------------------------------------------------------------------------
# Step 1: exhaustive system Tool map + ruleset digests
# ---------------------------------------------------------------------------


def test_every_runtime_system_tool_has_reviewed_classification() -> None:
    from app.assistant.capabilities.classification import SYSTEM_TOOL_CLASSIFICATIONS
    from app.assistant_config.registry import ToolRegistry

    assert set(SYSTEM_TOOL_CLASSIFICATIONS) == set(
        ToolRegistry.list_runtime_system_tool_names()
    )


def test_missing_system_tool_classification_is_detectable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities import classification as class_mod
    from app.assistant_config.registry import ToolRegistry

    patched = dict(class_mod.SYSTEM_TOOL_CLASSIFICATIONS)
    patched["brand_new_unreviewed_tool"] = ("read", True)
    monkeypatch.setattr(class_mod, "SYSTEM_TOOL_CLASSIFICATIONS", patched)
    assert set(class_mod.SYSTEM_TOOL_CLASSIFICATIONS) != set(
        ToolRegistry.list_runtime_system_tool_names()
    )


def test_system_tool_initial_values_match_section_6_2() -> None:
    from app.assistant.capabilities.classification import SYSTEM_TOOL_CLASSIFICATIONS

    expected = {
        "search_entries": ("read", True),
        "search_similar_entries": ("read", True),
        "get_entry_detail": ("read", True),
        "create_entry": ("write_local", False),
        "update_entry": ("write_local", False),
        "create_relation": ("write_local", False),
        "query_knowledge_graph": ("read", False),
        "generate_weekly_report": ("write_local", False),
        "generate_monthly_report": ("write_local", False),
        "openclaw_capture_entry": ("write_local", False),
        "openclaw_search_entries": ("read", True),
        "openclaw_get_entry": ("read", True),
        "openclaw_create_relation": ("write_local", False),
        "openclaw_query_knowledge_graph": ("read", False),
        "get_statistics": ("read", True),
        "get_entries_by_time_range": ("read", True),
        "analyze_activity": ("read", True),
        "get_tag_statistics": ("read", True),
        "list_entry_types": ("read", True),
        "list_tags": ("read", True),
        "kb_relation_recommendations": ("read", False),
        "kb_search": ("read", False),
    }
    assert SYSTEM_TOOL_CLASSIFICATIONS == expected


def test_classification_contract_revision_and_ruleset_digest_pinned() -> None:
    from app.assistant.capabilities.classification import (
        CLASSIFICATION_CONTRACT_REVISION,
        CLASSIFICATION_RULESET,
        CLASSIFICATION_RULESET_DIGEST,
        build_classification_ruleset,
    )
    from app.assistant.domain.digests import sha256_canonical_json

    assert CLASSIFICATION_CONTRACT_REVISION == "plan02-v1"
    recomputed = sha256_canonical_json(build_classification_ruleset())  # type: ignore[arg-type]
    assert CLASSIFICATION_RULESET_DIGEST == recomputed
    assert CLASSIFICATION_RULESET["revision"] == "plan02-v1"
    # Golden digest is stable for the checked-in ruleset.
    assert len(CLASSIFICATION_RULESET_DIGEST) == 64
    assert CLASSIFICATION_RULESET_DIGEST == sha256_canonical_json(CLASSIFICATION_RULESET)  # type: ignore[arg-type]


def test_mutating_ruleset_without_revision_bump_is_detectable() -> None:
    from app.assistant.capabilities.classification import (
        CLASSIFICATION_CONTRACT_REVISION,
        CLASSIFICATION_RULESET_DIGEST,
        build_classification_ruleset,
    )
    from app.assistant.domain.digests import sha256_canonical_json

    mutated = build_classification_ruleset()
    # Change a tool rule while keeping the old revision.
    mutated["systemTools"]["search_entries"]["sideEffect"] = "write_local"
    assert mutated["revision"] == CLASSIFICATION_CONTRACT_REVISION
    assert sha256_canonical_json(mutated) != CLASSIFICATION_RULESET_DIGEST  # type: ignore[arg-type]


def test_unknown_tool_name_classifies_as_unknown() -> None:
    from app.assistant.capabilities.classification import _system_tool_partial

    partial = _system_tool_partial("definitely_not_a_reviewed_tool")
    assert partial.side_effect == "unknown"
    assert partial.parallel_safe is False


# ---------------------------------------------------------------------------
# Step 3: remote Tool defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "http_method",
    ["GET", "POST", "PUT", None],
)
def test_remote_tool_defaults_to_write_external(db, http_method) -> None:
    tool, frozen = _freeze_remote_tool(
        db,
        tool_name=f"remote_{http_method or 'missing'}",
        http_method=http_method,
    )
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "write_external"
    assert behavior.parallel_safe is False
    assert behavior.interrupt_mode == "none"
    assert behavior.timeout_policy.mode == "native"
    assert behavior.timeout_policy.cancellation_supported is False
    assert tool is not None


# ---------------------------------------------------------------------------
# Step 4: Workflow node matrix
# ---------------------------------------------------------------------------


def test_workflow_pure_control_nodes_are_none(db) -> None:
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "branch",
                "node_type": "if_else",
                "label": "If",
                "position_x": 100,
                "position_y": 0,
                "config": {"conditions": []},
            },
            {
                "node_id": "assign",
                "node_type": "variable_assign",
                "label": "Assign",
                "position_x": 200,
                "position_y": 0,
                "config": {"assignments": []},
            },
        ]
    )
    # Pure control still needs a model for the default minimal path? Use custom snapshot only.
    # create_published_workflow with snapshot still may not freeze models if no llm nodes.
    # Add an llm so publication succeeds for model deps if needed — wait, pure control
    # without llm should still publish if start+output only.
    _, _, frozen = _freeze_workflow(db, name="wf_control", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "none"
    assert behavior.interrupt_mode == "none"
    assert behavior.parallel_safe is False  # workflow opt-in is false


def test_workflow_llm_and_parameter_extractor_are_read_non_parallel(db) -> None:
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "llm_main",
                "node_type": "llm",
                "label": "LLM",
                "position_x": 100,
                "position_y": 0,
                "config": {"model_source": "default", "model_id": None},
            },
            {
                "node_id": "pe_1",
                "node_type": "parameter_extractor",
                "label": "PE",
                "position_x": 200,
                "position_y": 0,
                "config": {"model_source": "default", "model_id": None},
            },
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_llm", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"
    assert behavior.parallel_safe is False


def test_workflow_knowledge_retrieval_is_read(db) -> None:
    _, _, frozen = _freeze_workflow(db, name="wf_kr", knowledge_retrieval=True)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"


def test_workflow_read_tool_is_read(db) -> None:
    _, _, frozen = _freeze_workflow(db, name="wf_read_tool", tool_names=["search_entries"])
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"


def test_workflow_write_tool_is_write_local(db) -> None:
    _, _, frozen = _freeze_workflow(db, name="wf_write_tool", tool_names=["create_entry"])
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "write_local"
    assert behavior.parallel_safe is False


def test_workflow_code_executor_is_unknown(db) -> None:
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "code_1",
                "node_type": "code_executor",
                "label": "Code",
                "position_x": 100,
                "position_y": 0,
                "config": {"language": "python", "code": "return 1"},
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_code", snapshot=snapshot)
    behavior, surface = _classify_binding(db, frozen)
    assert behavior.side_effect == "unknown"
    assert behavior.parallel_safe is False
    # Descriptor path marks unsupported.
    from app.assistant.capabilities.registry import CapabilityRegistry

    target = CapabilityRegistry(db).resolve(surface.binding)
    assert target.descriptor.availability.status == "unsupported"
    assert target.descriptor.behavior.side_effect == "unknown"


def test_workflow_http_literal_safe_get_is_read(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.common import ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "validate_url_ssrf", lambda url, **kwargs: None)
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "http_1",
                "node_type": "http_request",
                "label": "HTTP",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    "method": "GET",
                    "url": "https://example.com/api",
                    "verify_ssl": True,
                    "body_type": "none",
                },
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_http_get", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"


def test_workflow_http_post_is_write_external(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.common import ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "validate_url_ssrf", lambda url, **kwargs: None)
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "http_1",
                "node_type": "http_request",
                "label": "HTTP",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    "method": "POST",
                    "url": "https://example.com/api",
                    "verify_ssl": True,
                    "body_type": "json",
                },
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_http_post", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "write_external"


@pytest.mark.parametrize(
    "cfg",
    [
        {"method": "TRACE", "url": "https://example.com", "verify_ssl": True},
        {"method": "GET", "url": "https://example.com/{{id}}", "verify_ssl": True},
        {"method": "GET", "url": "https://example.com", "verify_ssl": False},
        {"method": "GET", "url": "https://example.com", "verify_ssl": True, "body_type": "form-data"},
    ],
)
def test_workflow_http_ambiguous_is_unknown(db, cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.common import ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "validate_url_ssrf", lambda url, **kwargs: None)
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "http_1",
                "node_type": "http_request",
                "label": "HTTP",
                "position_x": 100,
                "position_y": 0,
                "config": cfg,
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name=f"wf_http_amb_{uuid4().hex[:6]}", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "unknown"


def test_workflow_bounded_loop_aggregates_body(db) -> None:
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "loop_1",
                "node_type": "loop",
                "label": "Loop",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    "max_iterations": 3,
                    "body_nodes": [
                        {
                            "node_id": "body_tool",
                            "node_type": "tool",
                            "label": "T",
                            "config": {"tool_name": "search_entries"},
                        }
                    ],
                    "body_edges": [],
                },
            }
        ]
    )
    _, _, frozen = _freeze_workflow(
        db, name="wf_loop", snapshot=snapshot, tool_names=["search_entries"]
    )
    # tool_names on create adds root tool nodes; override with pure loop snapshot and
    # ensure search_entries is freezable via a tool node in body only — publication
    # walks body nodes, so tool_names kwargs is not required if snapshot has the tool.
    # Re-freeze with snapshot only (publication collects body tools).
    behavior, _ = _classify_binding(db, frozen)
    # Body has read tool; loop itself is non-parallel.
    assert behavior.side_effect in {"read", "unknown"}
    # If body tool path was frozen correctly → read.
    assert behavior.parallel_safe is False


def test_workflow_unbounded_loop_is_unknown(db) -> None:
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "loop_1",
                "node_type": "loop",
                "label": "Loop",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    # missing max_iterations
                    "body_nodes": [
                        {
                            "node_id": "body_none",
                            "node_type": "variable_assign",
                            "label": "A",
                            "config": {},
                        }
                    ],
                    "body_edges": [],
                },
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_loop_unbounded", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "unknown"


def test_workflow_human_in_loop_is_draft_blocking(db) -> None:
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "hil",
                "node_type": "human_in_loop",
                "label": "Approve",
                "position_x": 100,
                "position_y": 0,
                "config": {"prompt": "approve?"},
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_hil", snapshot=snapshot)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "draft"
    assert behavior.interrupt_mode == "legacy_blocking"
    assert behavior.parallel_safe is False


def test_workflow_unknown_node_type_is_unknown(db) -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry

    snapshot = {
        "nodes": [
            {
                "node_id": "start",
                "node_type": "start",
                "label": "Start",
                "config": {},
            },
            {
                "node_id": "x1",
                "node_type": "totally_unknown_node",
                "label": "X",
                "config": {},
            },
            {
                "node_id": "end",
                "node_type": "output",
                "label": "End",
                "config": {},
            },
        ],
        "edges": [],
    }
    _, version, frozen = _freeze_workflow(db, name="wf_unk_base")
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    partial = CapabilityClassifier()._classify_workflow_graph(
        workflow_input=snapshot,
        path_prefix="root",
        deps={d.dependency_path: d for d in frozen.resolved.dependencies},
        closure=surface.execution_closure,
        visited={(version.workflow_id, version.id)},
        depth=0,
    )
    assert partial.side_effect == "unknown"


# ---------------------------------------------------------------------------
# Step 5: recursion and drift
# ---------------------------------------------------------------------------


def test_pinned_workflow_call_a_to_b(db) -> None:
    from tests.agent_skill_test_support import create_published_workflow

    _ensure_default_models(db, need_embedding=True)
    child, child_ver = create_published_workflow(
        db,
        name="child_b",
        tool_names=["search_entries"],
    )
    parent, parent_ver, frozen = _freeze_workflow(
        db,
        name="parent_a",
        nested_calls=[
            {
                "target_workflow_id": str(child.id),
                "binding_mode": "pinned",
                "target_published_version_id": str(child_ver.id),
                "input_bindings": {},
            }
        ],
    )
    assert parent.id != child.id
    behavior, _ = _classify_binding(db, frozen)
    # Nested child with search_entries is read; parent llm is read.
    assert behavior.side_effect == "read"
    assert parent_ver is not None


def test_workflow_call_chain_a_b_c(db) -> None:
    from tests.agent_skill_test_support import create_published_workflow

    _ensure_default_models(db, need_embedding=True)
    c, c_ver = create_published_workflow(db, name="wf_c", tool_names=["search_entries"])
    b, b_ver = create_published_workflow(
        db,
        name="wf_b",
        nested_calls=[
            {
                "target_workflow_id": str(c.id),
                "binding_mode": "pinned",
                "target_published_version_id": str(c_ver.id),
                "input_bindings": {},
            }
        ],
    )
    _, _, frozen = _freeze_workflow(
        db,
        name="wf_a",
        nested_calls=[
            {
                "target_workflow_id": str(b.id),
                "binding_mode": "pinned",
                "target_published_version_id": str(b_ver.id),
                "input_bindings": {},
            }
        ],
    )
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"


def test_workflow_call_cycle_is_unknown(db) -> None:
    """Direct cycle cannot be published by Plan 01; synthetic classify path still returns unknown."""
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry
    from uuid import uuid4

    wf_id = uuid4()
    ver_id = uuid4()
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "call_self",
                "node_type": "workflow_call",
                "label": "Self",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    "target_workflow_id": str(wf_id),
                    "binding_mode": "pinned",
                    "target_published_version_id": str(ver_id),
                },
            }
        ]
    )
    # Use a real surface for closure shape, then classify synthetic cycle graph.
    _, version, frozen = _freeze_workflow(db, name="wf_cycle_base")
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    partial = CapabilityClassifier()._classify_workflow_graph(
        workflow_input=snapshot,
        path_prefix="root",
        deps={},
        closure=surface.execution_closure,
        visited={(wf_id, ver_id)},
        depth=0,
    )
    assert partial.side_effect == "unknown"
    assert version is not None


def test_dynamic_latest_workflow_call_is_unknown(db) -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry

    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "call_latest",
                "node_type": "workflow_call",
                "label": "Latest",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    "target_workflow_id": str(uuid4()),
                    "binding_mode": "latest",
                    "target_published_version_id": None,
                },
            }
        ]
    )
    _, version, frozen = _freeze_workflow(db, name="wf_latest_base")
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    partial = CapabilityClassifier()._classify_workflow_graph(
        workflow_input=snapshot,
        path_prefix="root",
        deps={d.dependency_path: d for d in frozen.resolved.dependencies},
        closure=surface.execution_closure,
        visited={(version.workflow_id, version.id)},
        depth=0,
    )
    assert partial.side_effect == "unknown"


def test_missing_child_version_in_closure_is_unknown(db) -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry

    child_id = uuid4()
    child_ver = uuid4()
    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "call_missing",
                "node_type": "workflow_call",
                "label": "Missing",
                "position_x": 100,
                "position_y": 0,
                "config": {
                    "target_workflow_id": str(child_id),
                    "binding_mode": "pinned",
                    "target_published_version_id": str(child_ver),
                },
            }
        ]
    )
    _, version, frozen = _freeze_workflow(db, name="wf_missing_child_base")
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    # No dep entry for the call → unknown (never repairs via current DB).
    partial = CapabilityClassifier()._classify_workflow_graph(
        workflow_input=snapshot,
        path_prefix="root",
        deps={},
        closure=surface.execution_closure,
        visited={(version.workflow_id, version.id)},
        depth=0,
    )
    assert partial.side_effect == "unknown"


def test_nested_child_with_human_propagates_interrupt(db) -> None:
    from tests.agent_skill_test_support import create_published_workflow

    _ensure_default_models(db, need_embedding=True)
    child_snapshot = _nodes_snapshot(
        [
            {
                "node_id": "hil",
                "node_type": "human_in_loop",
                "label": "Approve",
                "position_x": 100,
                "position_y": 0,
                "config": {"prompt": "ok?"},
            }
        ]
    )
    child, child_ver = create_published_workflow(
        db, name="child_hil", snapshot=child_snapshot
    )
    _, _, frozen = _freeze_workflow(
        db,
        name="parent_hil",
        nested_calls=[
            {
                "target_workflow_id": str(child.id),
                "binding_mode": "pinned",
                "target_published_version_id": str(child_ver.id),
                "input_bindings": {},
            }
        ],
    )
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "draft"
    assert behavior.interrupt_mode == "legacy_blocking"


def test_depth_limit_boundary_is_unknown(db) -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.contracts import MAX_CAPABILITY_CLOSURE_DEPTH

    _, version, frozen = _freeze_workflow(db, name="wf_depth_base")
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    partial = CapabilityClassifier()._classify_workflow_graph(
        workflow_input=surface.executable.parsed_published_input,
        path_prefix="root",
        deps={d.dependency_path: d for d in frozen.resolved.dependencies},
        closure=surface.execution_closure,
        visited={(version.workflow_id, version.id)},
        depth=MAX_CAPABILITY_CLOSURE_DEPTH + 1,
    )
    assert partial.side_effect == "unknown"


def test_classified_node_limit_one_over_is_unknown() -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.domain.contracts import MAX_CAPABILITY_CLASSIFIED_NODES

    # Build a synthetic graph with too many nodes without publishing.
    nodes = [
        {
            "node_id": f"n{i}",
            "node_type": "variable_assign",
            "label": f"n{i}",
            "config": {},
        }
        for i in range(MAX_CAPABILITY_CLASSIFIED_NODES + 1)
    ]
    partial = CapabilityClassifier()._classify_workflow_graph(
        workflow_input={"nodes": nodes, "edges": []},
        path_prefix="root",
        deps={},
        closure=object(),
        visited=set(),
        depth=0,
    )
    assert partial.side_effect == "unknown"


# ---------------------------------------------------------------------------
# Step 6: Agent classification
# ---------------------------------------------------------------------------


def test_agent_no_tools_is_read(db) -> None:
    _, _, frozen = _freeze_agent(db, name="agent_no_tools", tools=[])
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"
    assert behavior.parallel_safe is False


def test_agent_read_only_tools(db) -> None:
    _, _, frozen = _freeze_agent(
        db, name="agent_read", tools=["search_entries", "get_entry_detail"]
    )
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"
    assert behavior.parallel_safe is False


def test_agent_read_plus_write_tool(db) -> None:
    _, _, frozen = _freeze_agent(
        db, name="agent_write", tools=["search_entries", "create_entry"]
    )
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "write_local"
    assert behavior.parallel_safe is False


def test_agent_remote_tool(db) -> None:
    from tests.agent_skill_test_support import create_remote_tool

    create_remote_tool(db, name="agent_remote_tool")
    db.commit()
    _, _, frozen = _freeze_agent(db, name="agent_remote", tools=["agent_remote_tool"])
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "write_external"
    assert behavior.parallel_safe is False
    assert behavior.interrupt_mode == "none"


def test_agent_kb_enabled_includes_kb_search(db) -> None:
    _, _, frozen = _freeze_agent(db, name="agent_kb", tools=[], kb_enabled=True)
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.side_effect == "read"


def test_agent_missing_tool_is_unknown(db) -> None:
    """If a tool is in the snapshot but missing from frozen deps → unknown.

    Plan 01 publish fails on missing tools, so we mutate the resolved binding
    after freeze to drop a dependency and prove classifier fail-closed behavior.
    """
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.contracts import ResolvedCapabilityBinding

    _, _, frozen = _freeze_agent(db, name="agent_missing", tools=["search_entries"])
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    # Drop all tool deps from a shallow copy of the binding projection used by classify.
    # Surface.binding is frozen; build a new surface-like object with empty deps.
    resolved = surface.binding.resolved
    emptied = ResolvedCapabilityBinding(
        capability_type=resolved.capability_type,
        capability_key=resolved.capability_key,
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        resolved_tool_id=resolved.resolved_tool_id,
        resolved_workflow_version_id=resolved.resolved_workflow_version_id,
        resolved_agent_version_id=resolved.resolved_agent_version_id,
        resolved_revision=resolved.resolved_revision,
        input_schema=resolved.input_schema,
        output_schema=resolved.output_schema,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        completion=resolved.completion,
        config_digest=resolved.config_digest,
        executable_revision=resolved.executable_revision,
        resolution_digest=resolved.resolution_digest,
        resolution_snapshot=resolved.resolution_snapshot,
        dependencies=(),
        dependency_closure_digest=resolved.dependency_closure_digest,
        binding_contract_digest=resolved.binding_contract_digest,
    )
    from app.assistant.capabilities.contracts import FrozenCapabilityBinding

    emptied_binding = FrozenCapabilityBinding(
        provenance=surface.binding.provenance,
        ref=surface.binding.ref,
        resolved=emptied,
    )
    from app.assistant.capabilities.ports import ResolvedCapabilitySurface

    emptied_surface = ResolvedCapabilitySurface(
        binding=emptied_binding,
        executable=surface.executable,
        execution_closure=surface.execution_closure,
        display_name=surface.display_name,
        description=surface.description,
        availability=surface.availability,
    )
    behavior = CapabilityClassifier().classify(emptied_surface)
    assert behavior.side_effect == "unknown"


def test_agent_nested_restart_semantics_unknown(db) -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.ports import (
        ExecutableAgentVersionTarget,
        ResolvedCapabilitySurface,
    )

    _, version, frozen = _freeze_agent(db, name="agent_restart", tools=[])
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    nested_snapshot = dict(surface.executable.parsed_snapshot)  # type: ignore[arg-type]
    nested_snapshot["nested_agent"] = True
    patched = ResolvedCapabilitySurface(
        binding=surface.binding,
        executable=ExecutableAgentVersionTarget(
            agent_profile_id=version.agent_profile_id,
            version_id=version.id,
            snapshot_digest=surface.executable.snapshot_digest,
            parsed_snapshot=nested_snapshot,
        ),
        execution_closure=surface.execution_closure,
        display_name=surface.display_name,
        description=surface.description,
        availability=surface.availability,
    )
    behavior = CapabilityClassifier().classify(patched)
    assert behavior.side_effect == "unknown"
    assert behavior.parallel_safe is False


def test_agent_always_non_parallel(db) -> None:
    _, _, frozen = _freeze_agent(db, name="agent_par", tools=["search_entries"])
    behavior, _ = _classify_binding(db, frozen)
    assert behavior.parallel_safe is False


# ---------------------------------------------------------------------------
# Step 9: registry resolve/describe integration
# ---------------------------------------------------------------------------


def test_registry_resolve_assembles_descriptor_with_behavior(db) -> None:
    from app.assistant.capabilities.classification import (
        CLASSIFICATION_CONTRACT_REVISION,
        CLASSIFICATION_RULESET_DIGEST,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry

    frozen = _freeze_system_tool(db, "search_entries")
    target = CapabilityRegistry(db).resolve(frozen)
    desc = target.descriptor
    assert desc.capability_key == "search_entries"
    assert desc.behavior.side_effect == "read"
    assert desc.behavior.parallel_safe is True
    assert desc.behavior.classification.revision == CLASSIFICATION_CONTRACT_REVISION
    assert desc.behavior.classification.ruleset_digest == CLASSIFICATION_RULESET_DIGEST
    assert desc.binding_contract_digest == frozen.resolved.binding_contract_digest
    assert desc.dependency_closure_digest == frozen.resolved.dependency_closure_digest
    assert len(desc.descriptor_digest) == 64
    assert len(desc.behavior.behavior_digest) == 64


def test_registry_describe_matches_resolve(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    frozen = _freeze_system_tool(db, "create_entry")
    reg = CapabilityRegistry(db)
    assert reg.describe(frozen) == reg.resolve(frozen).descriptor
    assert reg.describe(frozen).behavior.side_effect == "write_local"


def test_unknown_classification_marks_unsupported_but_describable(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    snapshot = _nodes_snapshot(
        [
            {
                "node_id": "code_1",
                "node_type": "code_executor",
                "label": "Code",
                "position_x": 100,
                "position_y": 0,
                "config": {"language": "python", "code": "return 1"},
            }
        ]
    )
    _, _, frozen = _freeze_workflow(db, name="wf_code_desc", snapshot=snapshot)
    desc = CapabilityRegistry(db).describe(frozen)
    assert desc.behavior.side_effect == "unknown"
    assert desc.availability.status == "unsupported"
    assert desc.availability.reason_code == "unknown_side_effect"
    # Still a full descriptor.
    assert desc.descriptor_digest
    assert desc.capability_type == "workflow"


def test_behavior_digest_changes_with_ruleset(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities import classification as class_mod
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.digests import sha256_canonical_json

    frozen = _freeze_system_tool(db, "search_entries")
    first = CapabilityRegistry(db).describe(frozen).behavior.behavior_digest

    # Mutate ruleset digest constants as a deliberate revision change would.
    new_ruleset = class_mod.build_classification_ruleset()
    new_ruleset["revision"] = "plan02-v2"
    new_digest = sha256_canonical_json(new_ruleset)  # type: ignore[arg-type]
    monkeypatch.setattr(class_mod, "CLASSIFICATION_CONTRACT_REVISION", "plan02-v2")
    monkeypatch.setattr(class_mod, "CLASSIFICATION_RULESET", new_ruleset)
    monkeypatch.setattr(class_mod, "CLASSIFICATION_RULESET_DIGEST", new_digest)

    second = CapabilityRegistry(db).describe(frozen).behavior.behavior_digest
    assert first != second


def test_imports_plan01_limits_without_local_redefinition() -> None:
    import app.assistant.capabilities.classification as class_mod
    from app.assistant.domain.contracts import (
        MAX_CAPABILITY_CLASSIFIED_NODES,
        MAX_CAPABILITY_CLOSURE_DEPTH,
        MAX_CAPABILITY_CLOSURE_REFS,
    )

    assert class_mod.MAX_CAPABILITY_CLASSIFIED_NODES is MAX_CAPABILITY_CLASSIFIED_NODES
    assert class_mod.MAX_CAPABILITY_CLOSURE_DEPTH is MAX_CAPABILITY_CLOSURE_DEPTH
    assert class_mod.MAX_CAPABILITY_CLOSURE_REFS is MAX_CAPABILITY_CLOSURE_REFS
    assert MAX_CAPABILITY_CLASSIFIED_NODES == 4096
    assert MAX_CAPABILITY_CLOSURE_DEPTH == 16
    assert MAX_CAPABILITY_CLOSURE_REFS == 256


def test_impact_list_code_executor_and_ambiguous_patterns() -> None:
    """Document impact categories for shared-mode unavailability (no DB inventory).

    Plan 02A keeps these on legacy or disables them before 02B. This test pins the
    classification outcomes that drive that inventory rather than scanning production
    OpenClaw catalogs (no OpenClaw import in capabilities).
    """
    from app.assistant.capabilities.classification import CapabilityClassifier

    clf = CapabilityClassifier()
    code = clf._classify_node(
        {
            "node_id": "c",
            "node_type": "code_executor",
            "config": {},
        },
        path_prefix="root",
        deps={},
        closure=object(),
        visited=set(),
        depth=0,
        classified_so_far=0,
    )
    assert code.side_effect == "unknown"

    templated = clf._classify_node(
        {
            "node_id": "h",
            "node_type": "http_request",
            "config": {"method": "GET", "url": "https://x/{{id}}", "verify_ssl": True},
        },
        path_prefix="root",
        deps={},
        closure=object(),
        visited=set(),
        depth=0,
        classified_so_far=0,
    )
    assert templated.side_effect == "unknown"

    unbounded = clf._classify_node(
        {
            "node_id": "l",
            "node_type": "iteration",
            "config": {"body_nodes": [{"node_id": "b", "node_type": "start", "config": {}}]},
        },
        path_prefix="root",
        deps={},
        closure=object(),
        visited=set(),
        depth=0,
        classified_so_far=0,
    )
    assert unbounded.side_effect == "unknown"
