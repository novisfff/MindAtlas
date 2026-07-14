from __future__ import annotations

from datetime import datetime
from typing import get_args
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.contracts import (  # noqa: E402
    AgentLoopStatus,
    BindingResolutionStatus,
    CapabilityCallStatus,
    CapabilityType,
    DeclaredSideEffect,
    MAX_CAPABILITY_CLASSIFIED_NODES,
    MAX_CAPABILITY_CLOSURE_DEPTH,
    MAX_CAPABILITY_CLOSURE_REFS,
    MainAgentMigrationState,
    ModelRef,
    ProviderRef,
    ResolvedCapabilityRef,
    ResolvedMainAgentRef,
    ResolvedProviderAliasRef,
    ResolvedRunManifestRevision,
    ResolvedSkillRef,
    SkillPackageMigrationState,
    SkillVersionConflictError,
    VersionSource,
    append_provider_aliases,
    append_skill_activation,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
    validate_manifest_child_link,
)
from app.assistant.domain.digests import (  # noqa: E402
    canonical_json_bytes,
    sha256_bytes,
    sha256_canonical_json,
)


RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000010")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000011")
PACKAGE_ID = UUID("00000000-0000-4000-8000-000000000020")
SKILL_VERSION_ID = UUID("00000000-0000-4000-8000-000000000021")
SKILL_VERSION_ID_B = UUID("00000000-0000-4000-8000-000000000022")
PACKAGE_ID_B = UUID("00000000-0000-4000-8000-000000000023")
SKILL_VERSION_ID_C = UUID("00000000-0000-4000-8000-000000000024")
TOOL_ID = UUID("00000000-0000-4000-8000-000000000030")
WORKFLOW_ID = UUID("00000000-0000-4000-8000-000000000031")
WORKFLOW_VERSION_ID = UUID("00000000-0000-4000-8000-000000000032")
AGENT_ID = UUID("00000000-0000-4000-8000-000000000033")
AGENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000034")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000040")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000050")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000051")
PROBE_ID = UUID("00000000-0000-4000-8000-000000000052")
ALIAS_ID = UUID("00000000-0000-4000-8000-000000000060")

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_1 = "1" * 64
DIGEST_2 = "2" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64
DIGEST_5 = "5" * 64
DIGEST_6 = "6" * 64
DIGEST_7 = "7" * 64
DIGEST_8 = "8" * 64
DIGEST_9 = "9" * 64


def test_sha256_canonical_json_is_stable_across_mapping_order() -> None:
    left = {"name": "周度回顾", "version": 1}
    right = {"version": 1, "name": "周度回顾"}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_canonical_json(left) == sha256_canonical_json(right)


def test_tuple_and_list_order_changes_digest() -> None:
    assert sha256_canonical_json(["a", "b"]) != sha256_canonical_json(["b", "a"])
    assert sha256_canonical_json(("a", "b")) != sha256_canonical_json(("b", "a"))
    assert canonical_json_bytes(["a", "b"]) == canonical_json_bytes(("a", "b"))


def test_non_ascii_strings_preserved_as_utf8() -> None:
    payload = {"title": "周度回顾"}
    raw = canonical_json_bytes(payload)
    assert "周度回顾".encode("utf-8") in raw
    assert "\\u" not in raw.decode("utf-8")


def test_nan_and_infinities_are_rejected() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_json_bytes(value)
        with pytest.raises(ValueError):
            canonical_json_bytes({"x": value})


def test_non_json_python_values_are_rejected() -> None:
    class Sample(BaseModel):
        value: int

    rejected = (
        b"raw-bytes",
        {1, 2, 3},
        datetime(2026, 1, 1),
        Sample(value=1),
        {1: "bad-key"},
    )
    for value in rejected:
        with pytest.raises((TypeError, ValueError)):
            canonical_json_bytes(value)


def test_sha256_bytes_is_lowercase_hex() -> None:
    digest = sha256_bytes(b"mindatlas")
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest == sha256_bytes(b"mindatlas")
    assert digest != sha256_bytes(b"mindatlas-other")


def _main_agent(**overrides) -> ResolvedMainAgentRef:
    payload = {
        "profile_id": PROFILE_ID,
        "version_id": PROFILE_VERSION_ID,
        "profile_key": "general_chat",
        "sequence": 1,
        "content_digest": DIGEST_A,
    }
    payload.update(overrides)
    return ResolvedMainAgentRef(**payload)


def _skill(
    *,
    package_id: UUID = PACKAGE_ID,
    version_id: UUID = SKILL_VERSION_ID,
    canonical_name: str = "weekly-review",
    sequence: int = 1,
    content_digest: str = DIGEST_B,
    version_digest: str = DIGEST_C,
    requested_name_normalized: str | None = None,
    resolved_via_alias_id: UUID | None = None,
) -> ResolvedSkillRef:
    return ResolvedSkillRef(
        package_id=package_id,
        version_id=version_id,
        canonical_name=canonical_name,
        sequence=sequence,
        content_digest=content_digest,
        version_digest=version_digest,
        requested_name_normalized=requested_name_normalized,
        resolved_via_alias_id=resolved_via_alias_id,
    )


def _capability(
    *,
    capability_type: str = "tool",
    capability_key: str = "search",
    target_identity: str = "system-tool:search_entries",
    target_id: UUID | None = None,
    target_version_id: UUID | None = None,
    target_revision: int | None = None,
    input_schema_digest: str = DIGEST_D,
    output_schema_digest: str = DIGEST_E,
    resolution_digest: str = DIGEST_F,
    dependency_closure_digest: str = DIGEST_1,
    binding_contract_digest: str = DIGEST_2,
) -> ResolvedCapabilityRef:
    return ResolvedCapabilityRef(
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target_id,
        target_version_id=target_version_id,
        target_revision=target_revision,
        input_schema_digest=input_schema_digest,
        output_schema_digest=output_schema_digest,
        resolution_digest=resolution_digest,
        dependency_closure_digest=dependency_closure_digest,
        binding_contract_digest=binding_contract_digest,
    )


def test_domain_vocabulary_literals_are_exact() -> None:
    assert get_args(CapabilityType) == ("tool", "workflow", "agent")
    assert get_args(BindingResolutionStatus) == ("unresolved", "resolved")
    assert get_args(SkillPackageMigrationState) == ("shadow", "native", "cutover")
    assert get_args(MainAgentMigrationState) == ("bootstrap", "shadow", "native", "cutover")
    assert get_args(VersionSource) == ("save", "publish")
    assert get_args(DeclaredSideEffect) == ("read", "compute", "draft", "write", "control")
    assert get_args(AgentLoopStatus) == (
        "completed",
        "waiting_input",
        "waiting_approval",
        "needs_reconciliation",
        "failed",
        "cancelled",
    )
    assert get_args(CapabilityCallStatus) == (
        "pending",
        "running",
        "deferred",
        "blocked",
        "waiting_approval",
        "waiting_input",
        "completed",
        "failed",
        "cancelled",
        "unknown",
        "needs_reconciliation",
    )


def test_closure_bound_constants_are_locked() -> None:
    assert MAX_CAPABILITY_CLOSURE_DEPTH == 16
    assert MAX_CAPABILITY_CLOSURE_REFS == 256
    assert MAX_CAPABILITY_CLASSIFIED_NODES == 4096


def test_vocabulary_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        ResolvedCapabilityRef(
            capability_type="prompt",  # type: ignore[arg-type]
            capability_key="x",
            target_identity="system-tool:x",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            input_schema_digest=DIGEST_A,
            output_schema_digest=DIGEST_B,
            resolution_digest=DIGEST_C,
            dependency_closure_digest=DIGEST_D,
            binding_contract_digest=DIGEST_E,
        )


def test_importing_vocabulary_does_not_introduce_loop_ledger_orm() -> None:
    import app.assistant.domain.contracts as contracts_mod

    source = open(contracts_mod.__file__, encoding="utf-8").read()
    forbidden = (
        "agent_loop",
        "capability_call_ledger",
        "CapabilityCallLedger",
        "AgentLoopRun",
        "declarative_base",
        "sqlalchemy",
    )
    for token in forbidden:
        assert token not in source


def test_every_contract_rejects_attribute_assignment() -> None:
    main_agent = _main_agent()
    skill = _skill()
    capability = _capability()
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=3,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    model = create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=4,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=5,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=PROBE_ID,
        capability_probe_digest=DIGEST_6,
    )
    alias = ResolvedProviderAliasRef(
        provider_protocol="openai_compat",
        domain_key="chat",
        provider_alias="search_tool",
        binding_contract_digest=DIGEST_7,
    )
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=main_agent,
        provider=provider,
        model=model,
        effective_policy_digest=DIGEST_8,
    )

    for obj, field, value in (
        (main_agent, "sequence", 99),
        (skill, "canonical_name", "other"),
        (capability, "capability_key", "other"),
        (provider, "provider_protocol", "other"),
        (model, "model_name", "other"),
        (alias, "provider_alias", "other"),
        (base, "revision", 99),
    ):
        with pytest.raises(ValidationError):
            setattr(obj, field, value)


def test_base_manifest_revision_is_one_without_parent() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    assert base.revision == 1
    assert base.parent_digest is None
    assert base.schema_version == 1
    assert base.provider_aliases == ()
    assert base.active_skills == ()
    assert base.capabilities == ()
    assert len(base.manifest_digest) == 64


def test_base_fixed_vector_includes_schema_version_and_empty_aliases() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    # Empty aliases and schemaVersion participate: a payload without them would hash differently.
    assert base.manifest_digest == sha256_canonical_json(
        {
            "schemaVersion": 1,
            "runId": str(RUN_ID),
            "revision": 1,
            "parentDigest": None,
            "mainAgent": {
                "profileId": str(PROFILE_ID),
                "versionId": str(PROFILE_VERSION_ID),
                "profileKey": "general_chat",
                "sequence": 1,
                "contentDigest": DIGEST_A,
            },
            "activeSkills": [],
            "capabilities": [],
            "provider": None,
            "model": None,
            "providerAliases": [],
            "effectivePolicyDigest": None,
        }
    )


def test_child_revision_increments_and_links_parent() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        base,
        skill=_skill(),
        capabilities=(_capability(),),
    )
    assert child.revision == base.revision + 1
    assert child.parent_digest == base.manifest_digest
    assert child.manifest_digest != base.manifest_digest


def test_parent_digest_mismatch_is_rejected() -> None:
    from app.assistant.domain.contracts import validate_manifest_child_link

    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        base,
        skill=_skill(),
        capabilities=(_capability(),),
    )
    validate_manifest_child_link(parent=base, child=child)
    broken = child.model_copy(update={"parent_digest": "0" * 64})
    with pytest.raises(ValueError, match="parent_digest"):
        validate_manifest_child_link(parent=base, child=broken)


def test_activating_second_skill_appends_without_replacing() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_skill_activation(
        base,
        skill=_skill(canonical_name="alpha-skill"),
        capabilities=(_capability(capability_key="cap-a"),),
    )
    second = append_skill_activation(
        first,
        skill=_skill(
            package_id=PACKAGE_ID_B,
            version_id=SKILL_VERSION_ID_C,
            canonical_name="beta-skill",
            sequence=1,
        ),
        capabilities=(
            _capability(
                capability_key="cap-b",
                target_identity=f"workflow:{WORKFLOW_ID}",
                target_id=WORKFLOW_ID,
                target_version_id=WORKFLOW_VERSION_ID,
                capability_type="workflow",
                binding_contract_digest=DIGEST_9,
            ),
        ),
    )
    assert [item.canonical_name for item in second.active_skills] == [
        "alpha-skill",
        "beta-skill",
    ]
    assert [item.capability_key for item in second.capabilities] == ["cap-a", "cap-b"]
    assert first.active_skills[0].canonical_name == "alpha-skill"
    assert first.capabilities[0].capability_key == "cap-a"


def test_reactivation_of_same_skill_version_is_idempotent() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    skill = _skill()
    caps = (_capability(),)
    first = append_skill_activation(base, skill=skill, capabilities=caps)
    again = append_skill_activation(first, skill=skill, capabilities=caps)
    assert again is first
    assert again.revision == 2
    assert len(again.active_skills) == 1


def test_reactivation_same_version_via_different_alias_is_idempotent() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    caps = (_capability(),)
    first = append_skill_activation(
        base,
        skill=_skill(
            requested_name_normalized="weekly-review",
            resolved_via_alias_id=None,
        ),
        capabilities=caps,
    )
    again = append_skill_activation(
        first,
        skill=_skill(
            requested_name_normalized="Weekly Review Alias",
            resolved_via_alias_id=ALIAS_ID,
        ),
        capabilities=caps,
    )
    assert again is first
    assert again.revision == 2
    assert len(again.active_skills) == 1
    # Provenance from the first activation is preserved; alias differences are ignored.
    assert again.active_skills[0].requested_name_normalized == "weekly-review"
    assert again.active_skills[0].resolved_via_alias_id is None


def test_same_canonical_name_different_version_raises_conflict() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_skill_activation(
        base,
        skill=_skill(version_id=SKILL_VERSION_ID, canonical_name="weekly-review"),
        capabilities=(_capability(capability_key="cap-a"),),
    )
    with pytest.raises(SkillVersionConflictError):
        append_skill_activation(
            first,
            skill=_skill(
                version_id=SKILL_VERSION_ID_B,
                canonical_name="weekly-review",
                sequence=2,
                version_digest=DIGEST_9,
            ),
            capabilities=(_capability(capability_key="cap-b"),),
        )


def test_same_canonical_name_different_version_id_only_raises_conflict() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_skill_activation(
        base,
        skill=_skill(
            version_id=SKILL_VERSION_ID,
            canonical_name="weekly-review",
            sequence=1,
            content_digest=DIGEST_B,
            version_digest=DIGEST_C,
        ),
        capabilities=(_capability(capability_key="cap-a"),),
    )
    # Same package/sequence/digests except a different version_id is still a conflict.
    with pytest.raises(SkillVersionConflictError):
        append_skill_activation(
            first,
            skill=_skill(
                version_id=SKILL_VERSION_ID_B,
                canonical_name="weekly-review",
                sequence=1,
                content_digest=DIGEST_B,
                version_digest=DIGEST_C,
                requested_name_normalized="via-alias",
                resolved_via_alias_id=ALIAS_ID,
            ),
            capabilities=(),
        )


def test_duplicate_capability_key_with_different_resolution_is_rejected() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_skill_activation(
        base,
        skill=_skill(canonical_name="alpha-skill"),
        capabilities=(_capability(capability_key="shared", binding_contract_digest=DIGEST_2),),
    )
    with pytest.raises(ValueError, match="capability"):
        append_skill_activation(
            first,
            skill=_skill(
                package_id=PACKAGE_ID_B,
                version_id=SKILL_VERSION_ID_C,
                canonical_name="beta-skill",
            ),
            capabilities=(
                _capability(
                    capability_key="shared",
                    binding_contract_digest=DIGEST_9,
                    target_identity=f"agent:{AGENT_ID}",
                    target_id=AGENT_ID,
                    target_version_id=AGENT_VERSION_ID,
                    capability_type="tool",
                ),
            ),
        )


def test_same_capability_key_different_types_can_coexist() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_skill_activation(
        base,
        skill=_skill(canonical_name="alpha-skill"),
        capabilities=(
            _capability(
                capability_key="review",
                capability_type="tool",
                binding_contract_digest=DIGEST_2,
            ),
        ),
    )
    second = append_skill_activation(
        first,
        skill=_skill(
            package_id=PACKAGE_ID_B,
            version_id=SKILL_VERSION_ID_C,
            canonical_name="beta-skill",
        ),
        capabilities=(
            _capability(
                capability_key="review",
                capability_type="workflow",
                binding_contract_digest=DIGEST_9,
                target_identity=f"workflow:{WORKFLOW_ID}",
                target_id=WORKFLOW_ID,
                target_version_id=WORKFLOW_VERSION_ID,
            ),
        ),
    )
    keys = {(c.capability_type, c.capability_key) for c in second.capabilities}
    assert keys == {("tool", "review"), ("workflow", "review")}


def test_reordering_inputs_cannot_create_nondeterministic_manifest() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    skill_a = _skill(canonical_name="alpha-skill", version_id=SKILL_VERSION_ID)
    skill_b = _skill(
        package_id=PACKAGE_ID_B,
        version_id=SKILL_VERSION_ID_C,
        canonical_name="beta-skill",
    )
    cap_z = _capability(capability_key="z-cap", binding_contract_digest=DIGEST_2)
    cap_a = _capability(
        capability_key="a-cap",
        binding_contract_digest=DIGEST_9,
        target_identity=f"tool:{TOOL_ID}",
        target_id=TOOL_ID,
        target_revision=7,
        capability_type="tool",
    )

    # Capability tuple order within one activation must not affect digest/order.
    left_caps = append_skill_activation(
        base, skill=skill_a, capabilities=(cap_z, cap_a)
    )
    right_caps = append_skill_activation(
        base, skill=skill_a, capabilities=(cap_a, cap_z)
    )
    assert [item.capability_key for item in left_caps.capabilities] == ["a-cap", "z-cap"]
    assert left_caps.manifest_digest == right_caps.manifest_digest

    # Multi-skill activation always stores skills sorted by canonical name.
    multi = append_skill_activation(base, skill=skill_b, capabilities=(cap_z,))
    multi = append_skill_activation(multi, skill=skill_a, capabilities=(cap_a,))
    assert [item.canonical_name for item in multi.active_skills] == [
        "alpha-skill",
        "beta-skill",
    ]
    assert [item.capability_key for item in multi.capabilities] == ["a-cap", "z-cap"]


def test_manifest_digest_changes_when_frozen_reference_or_policy_changes() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=DIGEST_8,
    )
    changed_policy = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=DIGEST_9,
    )
    changed_main = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(content_digest=DIGEST_B),
        provider=None,
        model=None,
        effective_policy_digest=DIGEST_8,
    )
    assert base.manifest_digest != changed_policy.manifest_digest
    assert base.manifest_digest != changed_main.manifest_digest

    with_skill = append_skill_activation(
        base,
        skill=_skill(),
        capabilities=(_capability(),),
    )
    with_other_skill = append_skill_activation(
        base,
        skill=_skill(content_digest=DIGEST_9),
        capabilities=(_capability(),),
    )
    assert with_skill.manifest_digest != with_other_skill.manifest_digest


def test_provider_and_model_ref_digests_change_with_any_execution_slot() -> None:
    base_provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    changed_revision = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=2,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    changed_adapter = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a2",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    assert base_provider.provider_ref_digest != changed_revision.provider_ref_digest
    assert base_provider.provider_ref_digest != changed_adapter.provider_ref_digest

    base_model = create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=base_provider.provider_ref_digest,
        capability_probe_id=PROBE_ID,
        capability_probe_digest=DIGEST_6,
    )
    changed_probe = create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=base_provider.provider_ref_digest,
        capability_probe_id=PROBE_ID,
        capability_probe_digest=DIGEST_7,
    )
    changed_credential = create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=2,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=base_provider.provider_ref_digest,
        capability_probe_id=PROBE_ID,
        capability_probe_digest=DIGEST_6,
    )
    assert base_model.model_ref_digest != changed_probe.model_ref_digest
    assert base_model.model_ref_digest != changed_credential.model_ref_digest


def test_provider_and_model_refs_reject_unknown_and_secret_fields() -> None:
    with pytest.raises(ValidationError):
        ProviderRef(
            provider_protocol="openai_compat",
            provider_config_id=None,
            provider_runtime_revision=None,
            provider_config_digest=None,
            adapter_key=None,
            adapter_revision=None,
            protocol_revision=None,
            app_build_revision=None,
            provider_ref_digest=DIGEST_A,
            api_key="secret",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ModelRef(
            model_id=MODEL_ID,
            model_name="gpt-test",
            model_type="llm",
            model_runtime_revision=None,
            credential_id=CREDENTIAL_ID,
            credential_runtime_revision=None,
            credential_config_digest=None,
            model_config_digest=None,
            provider_ref_digest=None,
            capability_probe_id=None,
            capability_probe_digest=None,
            model_ref_digest=DIGEST_A,
            headers={"Authorization": "Bearer x"},  # type: ignore[call-arg]
        )
    with pytest.raises((ValidationError, TypeError)):
        create_model_ref(
            model_id=MODEL_ID,
            model_name="gpt-test",
            model_type="llm",
            model_runtime_revision=None,
            credential_id=CREDENTIAL_ID,
            credential_runtime_revision=None,
            credential_config_digest=None,
            model_config_digest=None,
            provider_ref_digest=None,
            capability_probe_id=None,
            capability_probe_digest=None,
            api_key="secret",  # type: ignore[call-arg]
        )


def test_main_agent_fields_affect_manifest_digest() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    for kwargs in (
        {"profile_id": UUID("00000000-0000-4000-8000-000000000099")},
        {"version_id": UUID("00000000-0000-4000-8000-000000000098")},
        {"profile_key": "other_profile"},
        {"sequence": 2},
        {"content_digest": DIGEST_B},
    ):
        other = create_base_run_manifest(
            run_id=RUN_ID,
            main_agent=_main_agent(**kwargs),
            provider=None,
            model=None,
            effective_policy_digest=None,
        )
        assert other.manifest_digest != base.manifest_digest


def test_provider_alias_ref_can_be_represented_without_schema_change() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    alias = ResolvedProviderAliasRef(
        provider_protocol="openai_compat",
        domain_key="tools",
        provider_alias="search_entries",
        binding_contract_digest=DIGEST_7,
    )
    # Plan 03 will append aliases via a child revision without redefining the class.
    # Represent the value on the same schema_version=1 model via model_copy.
    with_alias = base.model_copy(update={"provider_aliases": (alias,)})
    rebuilt = ResolvedRunManifestRevision.model_validate(
        {
            **with_alias.model_dump(),
            "manifest_digest": sha256_canonical_json(
                {
                    "schemaVersion": 1,
                    "runId": str(RUN_ID),
                    "revision": 1,
                    "parentDigest": None,
                    "mainAgent": {
                        "profileId": str(PROFILE_ID),
                        "versionId": str(PROFILE_VERSION_ID),
                        "profileKey": "general_chat",
                        "sequence": 1,
                        "contentDigest": DIGEST_A,
                    },
                    "activeSkills": [],
                    "capabilities": [],
                    "provider": None,
                    "model": None,
                    "providerAliases": [
                        {
                            "providerProtocol": "openai_compat",
                            "domainKey": "tools",
                            "providerAlias": "search_entries",
                            "bindingContractDigest": DIGEST_7,
                        }
                    ],
                    "effectivePolicyDigest": None,
                }
            ),
        }
    )
    assert rebuilt.schema_version == 1
    assert rebuilt.provider_aliases[0].provider_alias == "search_entries"
    assert isinstance(rebuilt.provider_aliases[0], ResolvedProviderAliasRef)


def test_provider_model_constructors_ignore_caller_supplied_self_digests() -> None:
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    expected = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "providerProtocol": "openai_compat",
            "providerConfigId": str(PROVIDER_CONFIG_ID),
            "providerRuntimeRevision": 1,
            "providerConfigDigest": DIGEST_3,
            "adapterKey": "openai",
            "adapterRevision": "a1",
            "protocolRevision": "p1",
            "appBuildRevision": "build-1",
        }
    )
    assert provider.provider_ref_digest == expected

    model = create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    assert model.model_ref_digest == sha256_canonical_json(
        {
            "schemaVersion": 1,
            "modelId": str(MODEL_ID),
            "modelName": "gpt-test",
            "modelType": "llm",
            "modelRuntimeRevision": 2,
            "credentialId": str(CREDENTIAL_ID),
            "credentialRuntimeRevision": 3,
            "credentialConfigDigest": DIGEST_4,
            "modelConfigDigest": DIGEST_5,
            "providerRefDigest": provider.provider_ref_digest,
            "capabilityProbeId": None,
            "capabilityProbeDigest": None,
        }
    )


def test_skill_ref_supports_alias_resolution_fields() -> None:
    skill = ResolvedSkillRef(
        package_id=PACKAGE_ID,
        version_id=SKILL_VERSION_ID,
        canonical_name="weekly-review",
        sequence=1,
        content_digest=DIGEST_B,
        version_digest=DIGEST_C,
        requested_name_normalized="Weekly Review",
        resolved_via_alias_id=ALIAS_ID,
    )
    assert skill.resolved_via_alias_id == ALIAS_ID
    with pytest.raises(ValidationError):
        skill.canonical_name = "x"  # type: ignore[misc]


def _alias(
    *,
    provider_protocol: str = "openai_compat",
    domain_key: str = "tools.search",
    provider_alias: str = "search_entries",
    binding_contract_digest: str = DIGEST_7,
) -> ResolvedProviderAliasRef:
    return ResolvedProviderAliasRef(
        provider_protocol=provider_protocol,
        domain_key=domain_key,
        provider_alias=provider_alias,
        binding_contract_digest=binding_contract_digest,
    )


def test_plan01_empty_alias_digest_remains_byte_identical() -> None:
    """Cross-plan fixed vector: empty aliases still participate in v1 payload."""
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    assert base.provider_aliases == ()
    assert base.manifest_digest == sha256_canonical_json(
        {
            "schemaVersion": 1,
            "runId": str(RUN_ID),
            "revision": 1,
            "parentDigest": None,
            "mainAgent": {
                "profileId": str(PROFILE_ID),
                "versionId": str(PROFILE_VERSION_ID),
                "profileKey": "general_chat",
                "sequence": 1,
                "contentDigest": DIGEST_A,
            },
            "activeSkills": [],
            "capabilities": [],
            "provider": None,
            "model": None,
            "providerAliases": [],
            "effectivePolicyDigest": None,
        }
    )


def test_append_provider_aliases_creates_one_child_revision() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    empty_digest = base.manifest_digest
    child = append_provider_aliases(base, aliases=(_alias(),))
    assert child is not base
    assert child.revision == 2
    assert child.parent_digest == empty_digest
    assert child.manifest_digest != empty_digest
    assert len(child.provider_aliases) == 1
    validate_manifest_child_link(parent=base, child=child)


def test_append_identical_aliases_is_idempotent() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_provider_aliases(base, aliases=(_alias(),))
    again = append_provider_aliases(first, aliases=(_alias(),))
    assert again is first
    assert again.revision == 2


def test_existing_alias_cannot_change_or_disappear() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_provider_aliases(base, aliases=(_alias(provider_alias="search_entries"),))
    with pytest.raises(ValueError, match="conflict|cannot change"):
        append_provider_aliases(
            first,
            aliases=(
                _alias(
                    provider_alias="search_other",
                    binding_contract_digest=DIGEST_7,
                ),
            ),
        )
    # Parent aliases remain intact after a rejected append attempt.
    assert first.provider_aliases[0].provider_alias == "search_entries"
    assert len(first.provider_aliases) == 1


def test_case_folded_alias_collision_is_rejected() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_provider_aliases(
        base,
        aliases=(_alias(domain_key="a.tool", provider_alias="SearchTool"),),
    )
    with pytest.raises(ValueError, match="case-fold|collision"):
        append_provider_aliases(
            first,
            aliases=(
                _alias(
                    domain_key="b.tool",
                    provider_alias="searchtool",
                    binding_contract_digest=DIGEST_8,
                ),
            ),
        )


def test_same_domain_key_conflicting_binding_is_rejected() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    first = append_provider_aliases(
        base,
        aliases=(_alias(domain_key="tools.search", binding_contract_digest=DIGEST_7),),
    )
    with pytest.raises(ValueError, match="conflict|binding"):
        append_provider_aliases(
            first,
            aliases=(
                _alias(
                    domain_key="tools.search",
                    provider_alias="search_entries_v2",
                    binding_contract_digest=DIGEST_8,
                ),
            ),
        )


def test_aliases_participate_in_manifest_digest_and_input_order_is_canonical() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    left = append_provider_aliases(
        base,
        aliases=(
            _alias(domain_key="zeta", provider_alias="zeta_tool", binding_contract_digest=DIGEST_7),
            _alias(domain_key="alpha", provider_alias="alpha_tool", binding_contract_digest=DIGEST_8),
        ),
    )
    right = append_provider_aliases(
        base,
        aliases=(
            _alias(domain_key="alpha", provider_alias="alpha_tool", binding_contract_digest=DIGEST_8),
            _alias(domain_key="zeta", provider_alias="zeta_tool", binding_contract_digest=DIGEST_7),
        ),
    )
    assert left.manifest_digest == right.manifest_digest
    assert [item.domain_key for item in left.provider_aliases] == ["alpha", "zeta"]
    assert left.manifest_digest != base.manifest_digest


def test_later_skill_activation_preserves_old_aliases() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    with_alias = append_provider_aliases(base, aliases=(_alias(),))
    with_skill = append_skill_activation(
        with_alias,
        skill=_skill(),
        capabilities=(_capability(),),
    )
    assert with_skill.provider_aliases == with_alias.provider_aliases
    assert with_skill.revision == with_alias.revision + 1
    assert with_skill.parent_digest == with_alias.manifest_digest


def test_realiasing_on_resume_fails() -> None:
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    frozen = append_provider_aliases(
        base,
        aliases=(_alias(domain_key="tools.search", provider_alias="search_entries"),),
    )
    # Resume must use the exact alias revision; attempting to re-alias the same
    # domain key with a different transport name is rejected.
    with pytest.raises(ValueError, match="conflict|cannot change"):
        append_provider_aliases(
            frozen,
            aliases=(
                _alias(
                    domain_key="tools.search",
                    provider_alias="search_entries_renamed",
                    binding_contract_digest=DIGEST_7,
                ),
            ),
        )


def test_digest_dependency_direction_binding_to_alias_to_manifest() -> None:
    """binding -> alias ref -> manifest; no reverse/self reference in payloads."""
    binding_digest = DIGEST_2
    alias = _alias(binding_contract_digest=binding_digest)
    alias_payload = {
        "providerProtocol": alias.provider_protocol,
        "domainKey": alias.domain_key,
        "providerAlias": alias.provider_alias,
        "bindingContractDigest": alias.binding_contract_digest,
    }
    # Alias payload never contains a manifest digest.
    assert "manifestDigest" not in alias_payload
    assert "surfaceDigest" not in alias_payload
    assert alias_payload["bindingContractDigest"] == binding_digest

    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_provider_aliases(base, aliases=(alias,))
    # Manifest digest includes alias payload but not surface digests.
    from app.assistant.domain.contracts import build_manifest_digest_payload

    payload = build_manifest_digest_payload(
        run_id=child.run_id,
        revision=child.revision,
        parent_digest=child.parent_digest,
        main_agent=child.main_agent,
        active_skills=child.active_skills,
        capabilities=child.capabilities,
        provider=child.provider,
        model=child.model,
        provider_aliases=child.provider_aliases,
        effective_policy_digest=child.effective_policy_digest,
    )
    assert "manifestDigest" not in payload
    assert "surfaceDigest" not in payload
    assert "aliasMapDigest" not in payload
    assert payload["providerAliases"][0]["bindingContractDigest"] == binding_digest
    # alias map digest is computed after the revision exists and is not copied back.
    alias_map_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "providerProtocol": "openai_compat",
            "manifestDigest": child.manifest_digest,
            "aliases": [
                {
                    "domainKey": alias.domain_key,
                    "providerAlias": alias.provider_alias,
                    "bindingContractDigest": binding_digest,
                }
            ],
        }
    )
    assert alias_map_digest != child.manifest_digest
    assert alias_map_digest not in child.model_dump().values()
