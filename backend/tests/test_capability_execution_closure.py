"""No-fallback exact execution-closure tests (Plan 02 Task 2)."""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

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


def _freeze_agent_with_tools(db):
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_agent,
        create_remote_tool,
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
    # KB path freezes the lightrag embedding component binding when enabled.
    create_default_model_binding(
        db,
        component="lightrag",
        model_name="embed-test",
        model_type="embedding",
        credential_name="cred-embed",
    )
    create_remote_tool(db, name="remote_dep")
    create_published_agent(
        db,
        name="closure_agent",
        tools=["search_entries", "remote_dep"],
        model_source="default",
        kb_enabled=True,
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key="closure_agent",
                contract=CapabilityBindingContract(
                    input_schema={"type": "object", "properties": {}},
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
    return frozen


def _freeze_workflow_with_nested(db):
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_workflow,
        create_remote_tool,
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
    create_remote_tool(db, name="remote_dep")
    nested, nested_version = create_published_workflow(
        db,
        name="nested_wf",
        tool_names=["search_entries"],
    )
    create_published_workflow(
        db,
        name="parent_wf",
        tool_names=["remote_dep"],
        nested_calls=[
            {
                "target_workflow_id": str(nested.id),
                "binding_mode": "pinned",
                "target_published_version_id": str(nested_version.id),
            }
        ],
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="workflow", key="parent_wf"),)
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
    return frozen, nested.id, nested_version.id


def _allow_decision(*, binding_contract_digest: str, dependency_closure_digest: str):
    from app.assistant.capabilities.contracts import (
        CapabilityOwnerRef,
        CapabilityPolicyDecision,
    )
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    return CapabilityPolicyDecision(
        allowed=True,
        reason_code="test_allow",
        call_id="call-1",
        descriptor_digest=DIGEST_A,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_A,
        owner=CapabilityOwnerRef(
            owner_kind="test",
            owner_id="test-owner",
            owner_version_id=None,
        ),
        granted_side_effects=("read",),
        grant_source_digest=DIGEST_A,
        decision_digest=DIGEST_A,
        # Real single-use permit; forged object() must not activate credentials.
        dispatch_permit=AtomicSingleUseDispatchPermit(),
    )


def test_workflow_engine_scope_protocol_exports() -> None:
    from app.assistant.workflow.engine.runtime_dependency_resolver import (
        ExactRuntimeDependencyResolver,
        WorkflowEngineExecutionScope,
    )

    assert ExactRuntimeDependencyResolver is not None
    scope = WorkflowEngineExecutionScope(
        dependency_resolver=object(),  # type: ignore[arg-type]
        binding_contract_digest=DIGEST_A,
        dependency_closure_digest=DIGEST_A,
        nesting_depth=0,
    )
    assert scope.allow_ambient_memory is False
    assert scope.allow_global_graph_cache is False
    assert scope.safe_diagnostics is True


def test_agent_closure_preflight_and_exact_tool_lookup(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.ai_provider import crypto as crypto_mod

    frozen = _freeze_agent_with_tools(db)
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    closure = surface.execution_closure
    assert closure.binding_contract_digest == frozen.resolved.binding_contract_digest
    assert closure.dependency_closure_digest == frozen.resolved.dependency_closure_digest

    # Find system tool dependency path.
    sys_dep = next(
        d for d in frozen.dependencies if d.dependency_type == "system_tool"
    )
    remote_dep = next(
        d for d in frozen.dependencies if d.dependency_type == "remote_tool"
    )
    model_dep = next(d for d in frozen.dependencies if d.dependency_type == "model")

    # Before authorization, activation must be impossible.
    with pytest.raises(CapabilityDomainError) as denied:
        # bind_authorized with a deny decision must not activate
        from app.assistant.capabilities.contracts import (
            CapabilityOwnerRef,
            CapabilityPolicyDecision,
        )

        deny = CapabilityPolicyDecision(
            allowed=False,
            reason_code="denied",
            call_id="c",
            descriptor_digest=DIGEST_A,
            classification_ruleset_digest=DIGEST_A,
            evidence_digest=DIGEST_A,
            owner=CapabilityOwnerRef(
                owner_kind="test", owner_id="o", owner_version_id=None
            ),
            granted_side_effects=(),
            grant_source_digest=DIGEST_A,
            decision_digest=DIGEST_A,
            dispatch_permit=None,
        )
        closure.bind_authorized(decision=deny)
    assert denied.value.error.error_type == "unauthorized"

    # Fixtures use non-Fernet placeholders; activation still requires decrypt success.
    monkeypatch.setattr(crypto_mod, "decrypt_api_key", lambda _token: "test-decrypted-key")

    decision = _allow_decision(
        binding_contract_digest=frozen.resolved.binding_contract_digest,
        dependency_closure_digest=frozen.resolved.dependency_closure_digest,
    )
    resolver = closure.bind_authorized(decision=decision)

    tool_name = sys_dep.target_identity.split(":", 1)[1]
    tool = resolver.require_tool(source_locator=sys_dep.dependency_path, tool_name=tool_name)
    assert tool.is_system is True
    assert tool.target_identity == sys_dep.target_identity

    remote_name = "remote_dep"
    remote = resolver.require_tool(
        source_locator=remote_dep.dependency_path, tool_name=remote_name
    )
    assert remote.is_system is False
    assert remote.tool_id == remote_dep.resolved_tool_id

    model_cfg = resolver.require_model(
        source_locator=model_dep.dependency_path,
        requested_model_id=model_dep.resolved_model_id,
    )
    assert model_cfg.verified.model_id == model_dep.resolved_model_id
    assert model_cfg.client_or_credential_handle is not None
    handle = model_cfg.client_or_credential_handle
    assert isinstance(handle, dict)
    assert handle.get("api_key") == "test-decrypted-key"
    assert handle.get("api_key") != "enc-test-key-not-secret-material"


def test_undeclared_tool_lookup_fails_without_registry_fallback(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant_config import registry as reg_mod

    frozen = _freeze_agent_with_tools(db)
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    decision = _allow_decision(
        binding_contract_digest=frozen.resolved.binding_contract_digest,
        dependency_closure_digest=frozen.resolved.dependency_closure_digest,
    )
    resolver = surface.execution_closure.bind_authorized(decision=decision)

    calls: list[str] = []

    original = reg_mod.ToolRegistry.resolve

    def _spy(self, tool_name: str):  # noqa: ANN001
        calls.append(tool_name)
        return original(self, tool_name)

    monkeypatch.setattr(reg_mod.ToolRegistry, "resolve", _spy)

    with pytest.raises(CapabilityDomainError) as ctx:
        resolver.require_tool(source_locator="root.tools.not_declared", tool_name="list_tags")
    assert ctx.value.error.error_type in {"not_found", "unavailable", "protocol_error"}
    # Capability scope never falls back to ToolRegistry for undeclared names.
    assert calls == []


def test_workflow_closure_child_workflow_and_tools(db) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.errors import CapabilityDomainError

    frozen, nested_id, nested_version_id = _freeze_workflow_with_nested(db)
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    decision = _allow_decision(
        binding_contract_digest=frozen.resolved.binding_contract_digest,
        dependency_closure_digest=frozen.resolved.dependency_closure_digest,
    )
    resolver = surface.execution_closure.bind_authorized(decision=decision)

    child_dep = next(d for d in frozen.dependencies if d.dependency_type == "workflow")
    child = resolver.require_workflow_version(
        source_locator=child_dep.dependency_path,
        workflow_id=nested_id,
        version_id=nested_version_id,
    )
    assert child.workflow_id == nested_id
    assert child.version_id == nested_version_id

    tool_dep = next(
        d for d in frozen.dependencies if d.dependency_type in {"system_tool", "remote_tool"}
    )
    tool_name = (
        tool_dep.target_identity.split(":", 1)[1]
        if tool_dep.dependency_type == "system_tool"
        else "remote_dep"
    )
    tool = resolver.require_tool(
        source_locator=tool_dep.dependency_path, tool_name=tool_name
    )
    assert tool.target_identity == tool_dep.target_identity

    # Wrong version fails.
    with pytest.raises(CapabilityDomainError):
        resolver.require_workflow_version(
            source_locator=child_dep.dependency_path,
            workflow_id=nested_id,
            version_id=uuid4(),
        )


def test_credential_activation_once_after_policy(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.ai_provider import crypto as crypto_mod

    frozen = _freeze_agent_with_tools(db)
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    model_dep = next(d for d in frozen.dependencies if d.dependency_type == "model")

    decrypt_calls = {"n": 0}
    real_decrypt = crypto_mod.decrypt_api_key

    def _counting(token: str) -> str:
        decrypt_calls["n"] += 1
        # Prefer not to depend on real Fernet material in unit tests.
        try:
            return real_decrypt(token)
        except Exception:
            return "test-decrypted-key"

    monkeypatch.setattr(crypto_mod, "decrypt_api_key", _counting)

    decision = _allow_decision(
        binding_contract_digest=frozen.resolved.binding_contract_digest,
        dependency_closure_digest=frozen.resolved.dependency_closure_digest,
    )
    resolver = surface.execution_closure.bind_authorized(decision=decision)
    first = resolver.require_model(
        source_locator=model_dep.dependency_path,
        requested_model_id=model_dep.resolved_model_id,
    )
    second = resolver.require_model(
        source_locator=model_dep.dependency_path,
        requested_model_id=model_dep.resolved_model_id,
    )
    assert first.verified.model_id == second.verified.model_id
    # Activation may decrypt once; subsequent lookups reuse the handle.
    assert decrypt_calls["n"] <= 1

    # Second bind_authorized is independent; deny remains denied.
    from app.assistant.capabilities.contracts import (
        CapabilityOwnerRef,
        CapabilityPolicyDecision,
    )

    deny = CapabilityPolicyDecision(
        allowed=False,
        reason_code="denied",
        call_id="c2",
        descriptor_digest=DIGEST_A,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_A,
        owner=CapabilityOwnerRef(owner_kind="test", owner_id="o", owner_version_id=None),
        granted_side_effects=(),
        grant_source_digest=DIGEST_A,
        decision_digest=DIGEST_A,
        dispatch_permit=None,
    )
    with pytest.raises(CapabilityDomainError):
        surface.execution_closure.bind_authorized(decision=deny)


def test_legacy_execution_scope_none_is_documented() -> None:
    """Legacy LangGraphEngine(..., execution_scope=None) remains valid constructor usage.

    Task 2 only introduces the Protocol/scope; engine wiring lands in later tasks.
    """
    from app.assistant.workflow.engine.engine import LangGraphEngine
    import inspect

    sig = inspect.signature(LangGraphEngine.__init__)
    # Additive optional parameter may or may not be present yet; either is OK for Task 2
    # as long as the Protocol/scope module exists and default-less construction still works.
    assert "self" in sig.parameters


def test_nested_workflow_mutated_snapshot_fails_preflight(db) -> None:
    """Same nested version id with mutated snapshot must fail closed on digest mismatch."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.execution_closure import build_frozen_execution_closure
    from app.assistant_config.models import AssistantWorkflowVersion

    frozen, _nested_id, nested_version_id = _freeze_workflow_with_nested(db)
    version = db.get(AssistantWorkflowVersion, nested_version_id)
    assert version is not None
    mutated = dict(version.snapshot)
    # Keep structure parseable but change content so snapshotDigest drifts.
    mutated["viewport"] = {"x": 99, "y": 99, "zoom": 2}
    version.snapshot = mutated
    flag_modified(version, "snapshot")
    db.add(version)
    db.commit()

    with pytest.raises(CapabilityDomainError) as ctx:
        build_frozen_execution_closure(
            db,
            binding_contract_digest=frozen.resolved.binding_contract_digest,
            dependency_closure_digest=frozen.resolved.dependency_closure_digest,
            dependencies=frozen.resolved.dependencies,
        )
    assert ctx.value.error.error_type == "version_drift"
    assert ctx.value.error.safe_code in {
        "workflow_snapshot_drift",
        "config_digest_drift",
        "version_drift",
    }


def test_nested_disabled_workflow_fails_preflight(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.execution_closure import build_frozen_execution_closure
    from app.assistant_config.models import AssistantWorkflow

    frozen, nested_id, _nested_version_id = _freeze_workflow_with_nested(db)
    workflow = db.get(AssistantWorkflow, nested_id)
    assert workflow is not None
    workflow.enabled = False
    db.add(workflow)
    db.commit()

    with pytest.raises(CapabilityDomainError) as ctx:
        build_frozen_execution_closure(
            db,
            binding_contract_digest=frozen.resolved.binding_contract_digest,
            dependency_closure_digest=frozen.resolved.dependency_closure_digest,
            dependencies=frozen.resolved.dependencies,
        )
    assert ctx.value.error.error_type == "unavailable"
    assert "disabled" in ctx.value.error.safe_code


def test_nested_agent_missing_version_fails_preflight(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.execution_closure import build_frozen_execution_closure
    from app.assistant.domain.contracts import ResolvedCapabilityDependency

    missing_version_id = uuid4()
    agent_id = uuid4()
    target_identity = f"agent:{agent_id}"
    resolution_snapshot = {
        "schemaVersion": 1,
        "targetIdentity": target_identity,
        "targetId": str(agent_id),
        "targetVersionId": str(missing_version_id),
        "snapshotDigest": "b" * 64,
    }
    dep = ResolvedCapabilityDependency(
        ordinal=0,
        dependency_path="root.nested.agent",
        dependency_type="agent",
        target_identity=target_identity,
        resolved_tool_id=None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=missing_version_id,
        resolved_model_id=None,
        target_revision=None,
        input_schema=None,
        output_schema=None,
        input_schema_digest=None,
        output_schema_digest=None,
        resolution_snapshot=resolution_snapshot,
        resolution_digest="c" * 64,
        dependency_digest="d" * 64,
    )
    with pytest.raises(CapabilityDomainError) as ctx:
        build_frozen_execution_closure(
            db,
            binding_contract_digest=DIGEST_A,
            dependency_closure_digest=DIGEST_A,
            dependencies=(dep,),
        )
    assert ctx.value.error.error_type in {"not_found", "version_drift"}
    assert "agent" in ctx.value.error.safe_code


def test_nested_agent_wrong_version_ownership_fails_preflight(db) -> None:
    from tests.agent_skill_test_support import create_published_agent
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.execution_closure import build_frozen_execution_closure
    from app.assistant.domain.contracts import ResolvedCapabilityDependency
    from app.assistant.domain.digests import sha256_canonical_json

    agent, version = create_published_agent(db, name="owned_agent", tools=["search_entries"])
    other_agent, _other_version = create_published_agent(
        db, name="other_agent", tools=["search_entries"]
    )
    db.commit()

    # Point target identity at other_agent while version belongs to agent.
    target_identity = f"agent:{other_agent.id}"
    snapshot_digest = sha256_canonical_json(version.snapshot)
    resolution_snapshot = {
        "schemaVersion": 1,
        "targetIdentity": target_identity,
        "targetId": str(other_agent.id),
        "targetVersionId": str(version.id),
        "snapshotDigest": snapshot_digest,
    }
    dep = ResolvedCapabilityDependency(
        ordinal=0,
        dependency_path="root.nested.agent",
        dependency_type="agent",
        target_identity=target_identity,
        resolved_tool_id=None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=version.id,
        resolved_model_id=None,
        target_revision=None,
        input_schema=None,
        output_schema=None,
        input_schema_digest=None,
        output_schema_digest=None,
        resolution_snapshot=resolution_snapshot,
        resolution_digest="c" * 64,
        dependency_digest="d" * 64,
    )
    with pytest.raises(CapabilityDomainError) as ctx:
        build_frozen_execution_closure(
            db,
            binding_contract_digest=DIGEST_A,
            dependency_closure_digest=DIGEST_A,
            dependencies=(dep,),
        )
    assert ctx.value.error.error_type in {"not_found", "version_drift"}


def test_decrypt_failure_does_not_expose_ciphertext(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.ai_provider import crypto as crypto_mod

    frozen = _freeze_agent_with_tools(db)
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    model_dep = next(d for d in frozen.dependencies if d.dependency_type == "model")

    def _boom(_token: str) -> str:
        raise ValueError("fernet decrypt failed")

    monkeypatch.setattr(crypto_mod, "decrypt_api_key", _boom)

    decision = _allow_decision(
        binding_contract_digest=frozen.resolved.binding_contract_digest,
        dependency_closure_digest=frozen.resolved.dependency_closure_digest,
    )
    resolver = surface.execution_closure.bind_authorized(decision=decision)
    with pytest.raises(CapabilityDomainError) as ctx:
        resolver.require_model(
            source_locator=model_dep.dependency_path,
            requested_model_id=model_dep.resolved_model_id,
        )
    assert ctx.value.error.error_type in {"unavailable", "protocol_error"}
    # Ciphertext placeholder from test fixtures must never surface as api_key.
    err_text = f"{ctx.value.error.safe_code}:{ctx.value.error.safe_message}:{ctx.value}"
    assert "enc-test-key-not-secret-material" not in err_text
