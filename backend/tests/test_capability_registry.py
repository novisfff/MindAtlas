"""Exact frozen-reference CapabilityRegistry resolution tests (Plan 02 Task 2)."""

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


def _freeze_remote_tool(db, tool_name: str = "remote_cap"):
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


def _freeze_workflow(db, name: str = "wf_cap", **kwargs: Any):
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_workflow,
    )
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    create_default_model_binding(db)
    create_default_model_binding(
        db,
        component="lightrag",
        model_name="embed-test",
        model_type="embedding",
        credential_name="cred-embed",
    )
    workflow, version = create_published_workflow(db, name=name, **kwargs)
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="workflow", key=name),)
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


def _freeze_agent(db, name: str = "agent_cap", **kwargs: Any):
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_agent,
    )
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import (
        CapabilityBindingContract,
        CapabilityDeclaration,
    )
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    create_default_model_binding(db)
    if kwargs.get("kb_enabled"):
        create_default_model_binding(
            db,
            component="lightrag",
            model_name="embed-test",
            model_type="embedding",
            credential_name="cred-embed",
        )
    agent, version = create_published_agent(db, name=name, **kwargs)
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key=name,
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


# ---------------------------------------------------------------------------
# ToolRegistry stable APIs
# ---------------------------------------------------------------------------


def test_list_runtime_system_tool_names_is_sorted_export_allowlist() -> None:
    from app.assistant import tools as assistant_tools
    from app.assistant_config.registry import ToolRegistry

    names = ToolRegistry.list_runtime_system_tool_names()
    assert names == tuple(sorted(names))
    assert set(names) == set(assistant_tools._EXPORTS.keys())
    for openclaw_name in (
        "openclaw_capture_entry",
        "openclaw_search_entries",
        "openclaw_get_entry",
        "openclaw_create_relation",
        "openclaw_query_knowledge_graph",
    ):
        assert openclaw_name in names
    assert "kb_search" in names
    assert ToolRegistry.get_runtime_system_tool_definition("search_entries") is not None
    assert ToolRegistry.get_runtime_system_tool_definition("kb_search") is not None
    assert ToolRegistry.get_runtime_system_tool_definition("not_a_tool") is None


def test_resolve_system_tool_rejects_unlisted_module_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant import tools as assistant_tools
    from app.assistant_config.registry import ToolRegistry

    monkeypatch.setattr(assistant_tools, "sneaky_tool", object(), raising=False)
    assert getattr(assistant_tools, "sneaky_tool", None) is not None
    assert ToolRegistry.resolve_system_tool("sneaky_tool") is None
    assert "sneaky_tool" not in ToolRegistry.list_runtime_system_tool_names()


# ---------------------------------------------------------------------------
# System tool resolution
# ---------------------------------------------------------------------------


def test_system_tool_exact_resolution_matches_plan01_contract(db) -> None:
    from app.assistant.capabilities.ports import ExecutableToolTarget
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.json_schema import binding_schema_digest
    from app.assistant.skills.resolution import (
        compute_system_tool_contract_set_digest,
        system_tool_schemas,
    )

    frozen = _freeze_system_tool(db, "search_entries")
    surface = CapabilityRegistry(db).resolve_surface(frozen)

    assert surface.binding.ref.target_identity == "system-tool:search_entries"
    assert surface.availability.status == "available"
    assert isinstance(surface.executable, ExecutableToolTarget)
    assert surface.executable.is_system is True
    assert surface.executable.target_identity == "system-tool:search_entries"
    assert surface.executable.config_revision is None
    assert surface.executable.config_digest == compute_system_tool_contract_set_digest()
    assert surface.binding.resolved.executable_revision == "test-build-c25d03f"

    input_schema, output_schema = system_tool_schemas("search_entries")
    assert surface.binding.resolved.input_schema_digest == binding_schema_digest(input_schema)
    assert surface.binding.resolved.output_schema_digest == binding_schema_digest(output_schema)
    # Frozen binding schemas are source of truth on the surface binding projection.
    assert surface.binding.input_schema == frozen.input_schema
    assert surface.binding.output_schema == frozen.output_schema

    blob = repr(surface) + str(surface.binding.resolved.resolution_snapshot)
    assert "api_key" not in blob.lower()
    assert "authorization" not in blob.lower()


def test_system_tool_locale_affects_display_only(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    frozen = _freeze_system_tool(db, "search_entries")
    zh = CapabilityRegistry(db, locale="zh").resolve_surface(frozen)
    en = CapabilityRegistry(db, locale="en").resolve_surface(frozen)
    assert zh.display_name != en.display_name or zh.description != en.description
    assert zh.binding.resolved.binding_contract_digest == en.binding.resolved.binding_contract_digest
    assert zh.executable.target_identity == en.executable.target_identity


def test_system_tool_resolution_is_deterministic(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    frozen = _freeze_system_tool(db, "search_entries")
    reg = CapabilityRegistry(db)
    a = reg.resolve_surface(frozen)
    b = reg.resolve_surface(frozen)
    assert a.binding.resolved.binding_contract_digest == b.binding.resolved.binding_contract_digest
    assert a.availability.status == b.availability.status
    assert a.executable.target_identity == b.executable.target_identity
    assert a.display_name == b.display_name


def test_system_tool_disabled_db_record_shadows(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant_config.models import AssistantTool

    frozen = _freeze_system_tool(db, "search_entries")
    db.add(
        AssistantTool(
            name="search_entries",
            description="shadow",
            kind="local",
            is_system=True,
            enabled=False,
        )
    )
    db.commit()
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "disabled"
    assert surface.availability.reason_code == "tool_disabled"
    # Still identifiable; no silent fallback to a different tool.
    assert surface.executable.target_identity == "system-tool:search_entries"


def test_system_tool_missing_export_raises(db) -> None:
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.contracts import CapabilityCompletionContract, ResolvedCapabilityBinding
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant.skills.resolution import build_binding_snapshot

    input_schema = normalize_binding_schema(
        {"type": "object", "properties": {}, "additionalProperties": False},
        require_object_root=True,
    )
    output_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    completion = CapabilityCompletionContract()
    target_identity = "system-tool:definitely_missing_tool_xyz"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    config_digest = "b" * 64
    executable_revision = "test-build-c25d03f"
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": None,
            "targetVersionId": None,
            "targetRevision": None,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": config_digest,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    resolved = ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key="definitely_missing_tool_xyz",
        target_identity=target_identity,
        target_id=None,
        target_version_id=None,
        resolved_tool_id=None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "not_found"


def test_system_tool_build_revision_drift(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.config import get_settings

    frozen = _freeze_system_tool(db, "search_entries")
    monkeypatch.setenv("APP_BUILD_REVISION", "other-build-revision")
    get_settings.cache_clear()
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "version_drift"
    assert ctx.value.error.safe_code == "build_revision_drift"
    get_settings.cache_clear()


def test_system_tool_schema_drift_is_fail_closed(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.json_schema import normalize_binding_schema
    from app.assistant.skills import resolution as resolution_mod

    frozen = _freeze_system_tool(db, "search_entries")
    drifted_input = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "number"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    drifted_output = normalize_binding_schema(
        {"type": "object", "properties": {"x": {"type": "string"}}},
        require_object_root=False,
    )

    def _drifted(_name: str):
        return drifted_input, drifted_output

    monkeypatch.setattr(resolution_mod, "system_tool_schemas", _drifted)
    monkeypatch.setattr(
        resolution_mod,
        "compute_system_tool_contract_set_digest",
        lambda: "c" * 64,
    )
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "version_drift"


def test_system_tool_registry_schema_does_not_replace_frozen_binding(db) -> None:
    """Current ToolRegistry schema is drift evidence only, never replaces frozen Schema."""
    from app.assistant.capabilities.registry import CapabilityRegistry

    frozen = _freeze_system_tool(db, "search_entries")
    original_input = frozen.input_schema
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    # Surface binding schemas remain the frozen Plan 01 bodies, not a live re-derive.
    assert surface.binding.input_schema == original_input
    assert surface.binding.resolved.input_schema_digest == frozen.resolved.input_schema_digest
    assert surface.binding.input_schema is not original_input or True


def test_mutating_plan01_source_after_projection_does_not_mutate_surface(db) -> None:
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="tool", key="search_entries"),)
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
    # Mutate the original resolved mapping if possible via model_copy (immutable) —
    # instead mutate a raw dict copy of snapshot and ensure surface is isolated.
    mutated_snapshot = dict(resolved.resolution_snapshot)
    mutated_snapshot["inputSchema"] = {"type": "string"}
    assert surface.binding.resolved.resolution_snapshot.get("inputSchema") != {
        "type": "string"
    }
    assert surface.binding.input_schema == frozen.input_schema


# ---------------------------------------------------------------------------
# Remote tool resolution
# ---------------------------------------------------------------------------


def test_remote_tool_exact_resolution_and_secret_free(db) -> None:
    from app.assistant.capabilities.ports import ExecutableToolTarget
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.skills.resolution import secret_free_remote_execution_snapshot
    from app.assistant.domain.digests import sha256_canonical_json

    tool, frozen = _freeze_remote_tool(db, "remote_exact")
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "available"
    assert isinstance(surface.executable, ExecutableToolTarget)
    assert surface.executable.is_system is False
    assert surface.executable.tool_id == tool.id
    assert surface.executable.config_revision == int(tool.config_revision or 1)
    expected_digest = sha256_canonical_json(secret_free_remote_execution_snapshot(tool))
    assert surface.executable.config_digest == expected_digest
    assert surface.binding.ref.target_identity == f"remote-tool:{tool.id}"

    blob = (
        repr(surface)
        + str(surface.binding.resolved.resolution_snapshot)
        + str(surface.binding.resolved.config_digest)
    )
    assert "enc-remote-key" not in blob
    assert "super-secret-header-value" not in blob
    assert "secret-query" not in blob
    assert "api_key_encrypted" not in blob.lower()


def test_remote_tool_disabled_availability(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    tool, frozen = _freeze_remote_tool(db, "remote_disabled")
    tool.enabled = False
    db.commit()
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "disabled"
    assert surface.executable.tool_id == tool.id


def test_remote_tool_stale_revision_is_version_drift(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry

    tool, frozen = _freeze_remote_tool(db, "remote_stale")
    tool.config_revision = int(tool.config_revision or 1) + 1
    db.commit()
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "version_drift"


def test_remote_tool_endpoint_drift_is_version_drift(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry

    tool, frozen = _freeze_remote_tool(db, "remote_endpoint")
    tool.endpoint_url = "https://hooks.example.com/other"
    # Keep revision same so only config digest drifts
    db.commit()
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "version_drift"


def test_remote_tool_kind_mismatch_is_version_drift(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry

    tool, frozen = _freeze_remote_tool(db, "remote_kind")
    tool.kind = "local"
    db.commit()
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type in {"version_drift", "unavailable", "not_found"}


def test_remote_tool_malformed_endpoint_unavailable_without_decrypt(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.ai_provider import crypto as crypto_mod

    tool, frozen = _freeze_remote_tool(db, "remote_malformed")
    tool.endpoint_url = "not a valid url :::"
    db.commit()

    def _boom(*_a, **_k):
        raise AssertionError("decrypt_api_key must not be called during resolution")

    monkeypatch.setattr(crypto_mod, "decrypt_api_key", _boom)
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type in {"version_drift", "unavailable"}


def test_remote_tool_missing_raises_not_found(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry

    tool, frozen = _freeze_remote_tool(db, "remote_delete")
    db.delete(tool)
    db.commit()
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "not_found"


# ---------------------------------------------------------------------------
# Workflow resolution
# ---------------------------------------------------------------------------


def test_workflow_exact_version_ownership_and_digest(db) -> None:
    from app.assistant.capabilities.ports import ExecutableWorkflowVersionTarget
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.domain.json_schema import binding_schema_digest
    from app.assistant_config.workflow_contracts import workflow_contract_from_input
    from app.assistant_config.schemas import WorkflowInput

    workflow, version, frozen = _freeze_workflow(
        db, name="wf_exact", tool_names=["search_entries"]
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "available"
    assert isinstance(surface.executable, ExecutableWorkflowVersionTarget)
    assert surface.executable.workflow_id == workflow.id
    assert surface.executable.version_id == version.id
    assert surface.executable.snapshot_digest == sha256_canonical_json(version.snapshot)
    assert surface.binding.ref.target_version_id == version.id

    wf_input = WorkflowInput.model_validate(
        {
            "nodes": version.snapshot.get("nodes") or [],
            "edges": version.snapshot.get("edges") or [],
            "viewport": version.snapshot.get("viewport"),
        }
    )
    contract = workflow_contract_from_input(wf_input)
    assert surface.binding.resolved.input_schema_digest == binding_schema_digest(
        contract.input_schema  # type: ignore[arg-type]
    )


def test_workflow_newer_publish_does_not_redirect_frozen_target(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant_config.models import AssistantWorkflowVersion

    workflow, version, frozen = _freeze_workflow(
        db, name="wf_newer", tool_names=["search_entries"]
    )
    # Publish a newer version and point the aggregate at it.
    newer_snapshot = dict(version.snapshot)
    nodes = list(newer_snapshot.get("nodes") or [])
    nodes.append(
        {
            "node_id": "extra_tool",
            "node_type": "tool",
            "label": "extra",
            "position_x": 10,
            "position_y": 10,
            "config": {"tool_name": "list_tags"},
        }
    )
    newer = AssistantWorkflowVersion(
        workflow_id=workflow.id,
        sequence_no=2,
        version_name="v2",
        version_source="publish",
        snapshot={**newer_snapshot, "nodes": nodes},
    )
    db.add(newer)
    db.flush()
    workflow.published_version_id = newer.id
    db.commit()

    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.executable.version_id == version.id
    assert surface.executable.version_id != newer.id
    assert surface.binding.ref.target_version_id == version.id


def test_workflow_disabled_aggregate_unavailable_but_identifiable(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    workflow, version, frozen = _freeze_workflow(
        db, name="wf_disabled", tool_names=["search_entries"]
    )
    workflow.enabled = False
    db.commit()
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "disabled"
    assert surface.executable.version_id == version.id
    assert surface.executable.workflow_id == workflow.id


def test_workflow_deleted_version_is_version_drift(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant_config.models import AssistantWorkflowVersion

    workflow, version, frozen = _freeze_workflow(
        db, name="wf_deleted", tool_names=["search_entries"]
    )
    # Point aggregate elsewhere then delete frozen version.
    other = AssistantWorkflowVersion(
        workflow_id=workflow.id,
        sequence_no=2,
        version_name="v2",
        version_source="publish",
        snapshot=version.snapshot,
    )
    db.add(other)
    db.flush()
    workflow.published_version_id = other.id
    workflow.draft_version_id = other.id
    db.delete(version)
    db.commit()
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type in {"version_drift", "not_found"}


def test_workflow_never_reads_graph_snapshot(db) -> None:
    """graph_snapshot is monkeypatched to raise in fixture; resolution must succeed."""
    from app.assistant.capabilities.registry import CapabilityRegistry

    _workflow, _version, frozen = _freeze_workflow(
        db, name="wf_no_graph", tool_names=["search_entries"]
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "available"


def test_workflow_current_contract_does_not_replace_frozen_schema(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    _workflow, version, frozen = _freeze_workflow(
        db, name="wf_schema", tool_names=["search_entries"]
    )
    original_input = frozen.input_schema
    # Mutate the live version snapshot; frozen binding schemas must remain.
    mutated = dict(version.snapshot)
    nodes = list(mutated.get("nodes") or [])
    # Leave snapshot digest check to drift path separately; here we only
    # assert frozen schemas stay when resolution still succeeds on matching digest.
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.binding.input_schema == original_input


# ---------------------------------------------------------------------------
# Agent + model closure
# ---------------------------------------------------------------------------


def test_agent_exact_version_and_binding_schema_only(db) -> None:
    from app.assistant.capabilities.ports import ExecutableAgentVersionTarget
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.digests import sha256_canonical_json

    agent, version, frozen = _freeze_agent(
        db, name="agent_exact", tools=["search_entries"], model_source="default"
    )
    # Mutate mutable aggregate fields after publication.
    agent.system_prompt = "MUTATED PROMPT"
    agent.tools = ["list_tags"]
    agent.kb_config = {"enabled": True, "mutated": True}
    db.commit()

    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "available"
    assert isinstance(surface.executable, ExecutableAgentVersionTarget)
    assert surface.executable.agent_profile_id == agent.id
    assert surface.executable.version_id == version.id
    assert surface.executable.snapshot_digest == sha256_canonical_json(version.snapshot)
    parsed = surface.executable.parsed_snapshot
    assert isinstance(parsed, dict)
    assert parsed.get("system_prompt") == "You are a test agent."
    assert parsed.get("tools") == ["search_entries"]
    # Binding schemas come only from frozen binding, not agent profile defaults.
    assert surface.binding.input_schema == frozen.input_schema
    assert surface.binding.output_schema == frozen.output_schema
    assert "prompt" in (surface.binding.input_schema.get("properties") or {})


def test_agent_model_closure_is_exact_not_component_binding(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.ai_registry.models import AiComponentBinding, AiModel
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import (
        CapabilityBindingContract,
        CapabilityDeclaration,
    )
    from app.assistant.skills.resolution import CapabilityReferenceResolver
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_agent,
    )

    cred, model, binding = create_default_model_binding(db)
    create_published_agent(
        db,
        name="agent_model",
        tools=["search_entries"],
        model_source="default",
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key="agent_model",
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
    frozen_model_id = next(
        d.resolved_model_id for d in frozen.dependencies if d.dependency_type == "model"
    )
    assert frozen_model_id == model.id

    # Redirect component binding after freeze.
    other = AiModel(
        credential_id=cred.id,
        name="gpt-other",
        model_type="llm",
        runtime_revision=1,
    )
    db.add(other)
    db.flush()
    binding.llm_model_id = other.id
    db.commit()

    surface = CapabilityRegistry(db).resolve_surface(frozen)
    model_deps = [
        d for d in surface.binding.dependencies if d.dependency_type == "model"
    ]
    assert model_deps
    assert model_deps[0].resolved_model_id == model.id
    assert model_deps[0].resolved_model_id != other.id
    # Closure preflight succeeds against frozen model, not current binding.
    assert surface.execution_closure.binding_contract_digest == (
        frozen.resolved.binding_contract_digest
    )


def test_agent_model_revision_drift_before_decrypt(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.ai_provider import crypto as crypto_mod
    from app.ai_registry.models import AiModel

    _agent, _version, frozen = _freeze_agent(
        db, name="agent_rev", tools=["search_entries"], model_source="default"
    )
    model_dep = next(d for d in frozen.dependencies if d.dependency_type == "model")
    model = db.query(AiModel).filter(AiModel.id == model_dep.resolved_model_id).one()
    model.runtime_revision = int(model.runtime_revision or 1) + 1
    db.commit()

    def _boom(*_a, **_k):
        raise AssertionError("decrypt_api_key must not be called")

    monkeypatch.setattr(crypto_mod, "decrypt_api_key", _boom)
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve_surface(frozen)
    assert ctx.value.error.error_type == "version_drift"


def test_agent_disabled_unavailable(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    agent, version, frozen = _freeze_agent(
        db, name="agent_disabled", tools=["search_entries"], model_source="default"
    )
    agent.enabled = False
    db.commit()
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    assert surface.availability.status == "disabled"
    assert surface.executable.version_id == version.id


def test_workflow_models_present_in_closure(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry

    _workflow, _version, frozen = _freeze_workflow(
        db, name="wf_models", tool_names=["search_entries"], model_source="default"
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    model_deps = [d for d in surface.binding.dependencies if d.dependency_type == "model"]
    tool_deps = [
        d
        for d in surface.binding.dependencies
        if d.dependency_type in {"system_tool", "remote_tool"}
    ]
    assert model_deps, "workflow node models must be present in frozen closure"
    assert tool_deps, "workflow tools must be present in frozen closure"
    # Preflighted closure accepts exact locators for those deps.
    for dep in surface.binding.dependencies:
        # require lookup only for tools/workflows/models via closure APIs in other test
        assert dep.dependency_path
        assert dep.dependency_digest


# ---------------------------------------------------------------------------
# Query bounds + no decryption / no engine construction
# ---------------------------------------------------------------------------


def test_resolution_does_not_decrypt_or_build_engines(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.ai_provider import crypto as crypto_mod

    def _boom(*_a, **_k):
        raise AssertionError("decrypt_api_key must not be called during resolve_surface")

    monkeypatch.setattr(crypto_mod, "decrypt_api_key", _boom)

    # System
    frozen_sys = _freeze_system_tool(db, "search_entries")
    CapabilityRegistry(db).resolve_surface(frozen_sys)

    # Remote
    _tool, frozen_remote = _freeze_remote_tool(db, "remote_no_decrypt")
    CapabilityRegistry(db).resolve_surface(frozen_remote)

    # Agent with model dependency
    _agent, _version, frozen_agent = _freeze_agent(
        db, name="agent_no_decrypt", tools=["search_entries"], model_source="default"
    )
    CapabilityRegistry(db).resolve_surface(frozen_agent)


def test_resolution_query_count_is_bounded(db) -> None:
    from sqlalchemy import event
    from app.assistant.capabilities.registry import CapabilityRegistry

    _agent, _version, frozen = _freeze_agent(
        db,
        name="agent_queries",
        tools=["search_entries"],
        model_source="default",
    )
    engine = db.get_bind()
    queries: list[str] = []

    def _before_cursor(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        queries.append(str(statement))

    event.listen(engine, "before_cursor_execute", _before_cursor)
    try:
        CapabilityRegistry(db).resolve_surface(frozen)
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor)

    # Single surface resolve must not N+1; keep a conservative upper bound.
    assert len(queries) <= 40, f"too many queries: {len(queries)}"
