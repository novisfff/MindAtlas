"""Main Agent control binding / classification / control-port tests (Plan 04 Task 5)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000201")
SOURCE_DIGEST = "a" * 64
BUILD = "plan04-dev"


@pytest.fixture(autouse=True)
def _pin_build_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin build revision for all control binding materialization tests."""
    monkeypatch.setenv("APP_BUILD_REVISION", BUILD)
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass
    yield
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass



def test_additive_provenance_enums_accept_main_agent() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityOwnerRef,
        FrozenBindingProvenance,
    )

    prov = FrozenBindingProvenance(
        origin="main_agent_profile",
        binding_row_id=None,
        owner_version_id=PROFILE_VERSION_ID,
        source_snapshot_digest=SOURCE_DIGEST,
    )
    assert prov.origin == "main_agent_profile"
    owner = CapabilityOwnerRef(
        owner_kind="main_agent",
        owner_id="default",
        owner_version_id=PROFILE_VERSION_ID,
    )
    assert owner.owner_kind == "main_agent"
    # Existing values still accepted (byte-compatible).
    assert FrozenBindingProvenance(
        origin="skill_version",
        binding_row_id=None,
        owner_version_id=None,
        source_snapshot_digest=SOURCE_DIGEST,
    ).origin == "skill_version"
    assert CapabilityOwnerRef(
        owner_kind="openclaw_catalog",
        owner_id="item",
        owner_version_id=None,
    ).owner_kind == "openclaw_catalog"


def test_all_four_controls_classified_exhaustively() -> None:
    from app.assistant.capabilities.classification import MAIN_AGENT_CONTROL_CLASSIFICATIONS
    from app.assistant.main_agent.control_capabilities import (
        MAIN_AGENT_CONTROL_KEYS,
        assert_all_controls_classified,
    )

    assert_all_controls_classified()
    assert MAIN_AGENT_CONTROL_CLASSIFICATIONS["skill.search"] == ("read", True)
    assert MAIN_AGENT_CONTROL_CLASSIFICATIONS["skill.inject"] == ("none", False)
    assert MAIN_AGENT_CONTROL_CLASSIFICATIONS["skill.read_resource"] == ("read", True)
    assert MAIN_AGENT_CONTROL_CLASSIFICATIONS["artifact.read"] == ("read", True)
    assert set(MAIN_AGENT_CONTROL_CLASSIFICATIONS) == set(MAIN_AGENT_CONTROL_KEYS)


def test_control_bindings_are_code_native_and_build_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_BUILD_REVISION", BUILD)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.assistant.main_agent.control_capabilities import (
        build_all_main_agent_control_bindings,
        main_agent_control_target_identity,
    )

    bindings = build_all_main_agent_control_bindings(
        owner_version_id=PROFILE_VERSION_ID,
        source_snapshot_digest=SOURCE_DIGEST,
        app_build_revision=BUILD,
    )
    assert len(bindings) == 4
    for binding in bindings:
        assert binding.provenance.origin == "main_agent_profile"
        assert binding.provenance.owner_version_id == PROFILE_VERSION_ID
        assert binding.resolved.capability_type == "tool"
        assert binding.resolved.target_identity == main_agent_control_target_identity(
            binding.ref.capability_key
        )
        assert binding.resolved.executable_revision == BUILD
        assert binding.resolved.dependencies == ()
        assert binding.resolved.input_schema["type"] == "object"
        assert binding.resolved.input_schema.get("additionalProperties") is False


def test_control_schemas_reject_unknown_keys() -> None:
    from app.assistant.capabilities.json_schema import (
        compile_binding_schema,
        validate_json_value,
    )
    from app.assistant.domain.json_schema import binding_schema_digest
    from app.assistant.main_agent.control_capabilities import control_input_schema

    schema = control_input_schema("skill.search")
    compiled = compile_binding_schema(
        schema,
        expected_digest=binding_schema_digest(schema),
        require_object_root=True,
    )
    with pytest.raises(Exception):
        validate_json_value(
            compiled,
            {"query": "x", "extra": True},
            label="input",
        )


def test_classifier_classifies_main_agent_controls() -> None:
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.capabilities.contracts import CapabilityAvailability
    from app.assistant.capabilities.execution_closure import build_frozen_execution_closure
    from app.assistant.capabilities.ports import (
        MainAgentControlExecutable,
        ResolvedCapabilitySurface,
    )
    from app.assistant.main_agent.control_capabilities import (
        build_main_agent_control_frozen_binding,
    )
    from app.assistant.main_agent.control_runtime import MainAgentControlRuntime
    from tests._db import make_session

    db = make_session()
    try:
        binding = build_main_agent_control_frozen_binding(
            domain_key="skill.search",
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=SOURCE_DIGEST,
            app_build_revision=BUILD,
        )
        closure = build_frozen_execution_closure(
            db,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            dependency_closure_digest=binding.resolved.dependency_closure_digest,
            dependencies=(),
        )
        port = MainAgentControlRuntime()
        surface = ResolvedCapabilitySurface(
            binding=binding,
            executable=MainAgentControlExecutable(
                capability_key="skill.search",
                target_identity=binding.resolved.target_identity,
                control_port=port,
            ),
            execution_closure=closure,
            display_name="Skill Search",
            description="search",
            availability=CapabilityAvailability(status="available"),
        )
        behavior = CapabilityClassifier().classify(surface)
        assert behavior.side_effect == "read"
        assert behavior.parallel_safe is True
        assert behavior.interrupt_mode == "none"
    finally:
        db.close()


def test_registry_resolves_main_agent_control_with_port() -> None:
    from app.assistant.capabilities.ports import MainAgentControlExecutable
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.main_agent.control_capabilities import (
        build_main_agent_control_frozen_binding,
    )
    from app.assistant.main_agent.control_runtime import MainAgentControlRuntime
    from tests._db import make_session

    db = make_session()
    try:
        port = MainAgentControlRuntime()
        registry = CapabilityRegistry(db, main_agent_control_port=port)
        binding = build_main_agent_control_frozen_binding(
            domain_key="skill.inject",
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=SOURCE_DIGEST,
            app_build_revision=BUILD,
        )
        surface = registry.resolve_surface(binding)
        assert isinstance(surface.executable, MainAgentControlExecutable)
        assert surface.executable.capability_key == "skill.inject"
        desc = registry.describe(binding)
        assert desc.behavior.side_effect == "none"
        assert desc.behavior.parallel_safe is False
        assert desc.availability.status == "available"
    finally:
        db.close()


def test_registry_without_port_is_unavailable() -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.main_agent.control_capabilities import (
        build_main_agent_control_frozen_binding,
    )
    from tests._db import make_session

    db = make_session()
    try:
        registry = CapabilityRegistry(db)  # no control port
        binding = build_main_agent_control_frozen_binding(
            domain_key="skill.search",
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=SOURCE_DIGEST,
            app_build_revision=BUILD,
        )
        with pytest.raises(CapabilityDomainError) as exc:
            registry.resolve_surface(binding)
        assert exc.value.error.safe_code == "main_agent_control_port_missing"
    finally:
        db.close()


def test_control_runtime_search_and_no_pending_on_failure() -> None:
    from app.assistant.main_agent.catalog import (
        CatalogCandidateProjection,
        CatalogSearchState,
        build_catalog_snapshot,
    )
    from app.assistant.main_agent.control_runtime import MainAgentControlRuntime
    from app.assistant.skills.schemas import SkillCatalogScopeV1

    digest = "b" * 64
    candidate = CatalogCandidateProjection(
        package_id=uuid4(),
        version_id=uuid4(),
        canonical_name="weekly-review",
        display_name="Weekly Review",
        description="review weekly entries",
        locale="en",
        aliases=(),
        include_examples=(),
        exclude_examples=(),
        content_digest=digest,
        version_digest=digest,
        resource_index_digest=digest,
        binding_set_digest=digest,
        version_source="publish",
        catalog_enabled=True,
        conflict_rules=(),
        instruction_char_count=10,
    )
    snap = build_catalog_snapshot(
        [candidate],
        scope=SkillCatalogScopeV1(mode="all_published", package_ids=()),
        locale="en",
    )
    state = CatalogSearchState(snap, default_top_k=8)
    runtime = MainAgentControlRuntime(catalog_state=state)
    result = runtime.execute(
        call_id="c1",
        capability_key="skill.search",
        validated_input={"query": "weekly"},
    )
    assert result.status == "completed"
    assert result.structured_output is not None
    assert runtime.take_manifest_effect(call_id="c1") is None

    # inject without handler fails and stages nothing
    fail = runtime.execute(
        call_id="c2",
        capability_key="skill.inject",
        validated_input={"skills": [{"name": "weekly-review"}]},
    )
    assert fail.status == "failed"
    assert runtime.take_manifest_effect(call_id="c2") is None


def test_failed_control_leaves_no_pending_effect() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityError,
        CapabilityMetrics,
        failed_result,
    )
    from app.assistant.domain.contracts import (
        ResolvedMainAgentRef,
        create_base_run_manifest,
    )
    from app.assistant.main_agent.control_runtime import MainAgentControlRuntime

    run_id = uuid4()
    manifest = create_base_run_manifest(
        run_id=run_id,
        main_agent=ResolvedMainAgentRef(
            profile_id=uuid4(),
            version_id=PROFILE_VERSION_ID,
            profile_key="default",
            sequence=1,
            content_digest=SOURCE_DIGEST,
        ),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )

    def boom(call_id: str, validated_input: dict[str, Any], current_manifest: Any):
        del call_id, validated_input, current_manifest
        return (
            failed_result(
                error=CapabilityError(
                    error_type="execution_failed",
                    safe_code="skill_not_disclosed",
                    safe_message="skill not disclosed",
                    retry_disposition="never",
                ),
                metrics=CapabilityMetrics(
                    duration_ms=0.0,
                    adapter_duration_ms=0.0,
                    input_bytes=0,
                    output_bytes=0,
                ),
            ),
            None,
        )

    runtime = MainAgentControlRuntime(
        current_manifest=manifest,
        inject_handler=boom,
    )
    result = runtime.execute(
        call_id="inj-1",
        capability_key="skill.inject",
        validated_input={"skills": [{"versionId": str(uuid4())}]},
    )
    assert result.status == "failed"
    assert runtime.has_pending_effect(call_id="inj-1") is False
    assert runtime.take_manifest_effect(call_id="inj-1") is None
