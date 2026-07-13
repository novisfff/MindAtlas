"""Plan 03 Task 2: deterministic alias mapping and frozen Provider tool surfaces."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedProviderAliasRef,
    ResolvedRunManifestRevision,
    append_provider_aliases,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.provider_loop.aliases import (  # noqa: E402
    DEFAULT_RESERVED_CONTROL_ALIASES,
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    allocate_provider_aliases,
    build_provider_tool_surface,
    forward_alias_map,
    generated_alias_candidate,
    identity_digest_prefix,
    is_valid_provider_alias,
    lookup_tool_by_alias,
    reverse_alias_map,
    sanitize_domain_key_for_alias,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    ProviderToolDefinition,
    compute_alias_map_digest,
    compute_surface_digest,
    create_execution_scope,
)
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64
DIGEST_5 = "5" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000101")
RUN_ID_B = UUID("00000000-0000-4000-8000-000000000201")
CONV_ID = UUID("00000000-0000-4000-8000-000000000102")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000110")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000111")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000150")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000151")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000140")
TARGET_A = UUID("00000000-0000-4000-8000-000000000210")
TARGET_B = UUID("00000000-0000-4000-8000-000000000211")
TARGET_C = UUID("00000000-0000-4000-8000-000000000212")

P = OPENAI_CHAT_PROVIDER_PROTOCOL

# Hard-coded expected generated aliases / identity prefixes (binding digest = DIGEST_A).
FIXED_GENERATED: dict[str, str] = {
    "skill.inject": "skill_inject",
    "human/request-input": "human_request_input",
    "with_underscores": "with_underscores",
    "with-hyphens": "with_hyphens",
    "with.dots": "with_dots",
    "with/slashes": "with_slashes",
    "with spaces": "with_spaces",
    "UPPERCASE.Key": "uppercase_key",
    "仅中文": "cap_3c7157e27346",
    "emoji🚀tool": "emoji_tool",
    "!!!": "cap_2b8e8c8d1c0d",
    "a" * 48: "a" * 48,
    "a" * 49: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_06048b0fae9f",
    "a" * 64: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_d65df5e6f01d",
    "a" * 65: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_553506de2957",
}

FIXED_IDENTITY_PREFIX: dict[str, str] = {
    "skill.inject": "eaffde4da287",
    "human/request-input": "b7a2c6eaa15e",
    "仅中文": "3c7157e27346",
    "!!!": "2b8e8c8d1c0d",
    "a" * 49: "06048b0fae9f",
    "a" * 65: "553506de2957",
    "Foo.Bar": "fb3d57ac935f",
    "foo_bar": "84404d82e0ab",
}


def _main_agent() -> ResolvedMainAgentRef:
    return ResolvedMainAgentRef(
        profile_id=PROFILE_ID,
        version_id=PROFILE_VERSION_ID,
        profile_key="general_chat",
        sequence=1,
        content_digest=DIGEST_A,
    )


def _provider():
    return create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )


def _model() -> ModelRef:
    provider = _provider()
    return create_model_ref(
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


def _manifest(run_id: UUID = RUN_ID) -> ResolvedRunManifestRevision:
    return create_base_run_manifest(
        run_id=run_id,
        main_agent=_main_agent(),
        provider=_provider(),
        model=_model(),
        effective_policy_digest=None,
    )


def _resolved_binding(
    *,
    capability_key: str,
    target_id: UUID | None = None,
    config_digest: str = DIGEST_B,
    interrupt_mode_note: str | None = None,  # unused; kept for call-site clarity
) -> ResolvedCapabilityBinding:
    del interrupt_mode_note
    input_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    output_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    completion = CapabilityCompletionContract()
    target = target_id or uuid4()
    target_identity = f"remote-tool:{target}"
    executable_revision = "1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(target),
            "targetVersionId": None,
            "targetRevision": 1,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        target_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    return ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        resolved_tool_id=target,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=1,
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


def _frozen(
    *,
    capability_key: str,
    target_id: UUID | None = None,
    config_digest: str = DIGEST_B,
) -> FrozenCapabilityBinding:
    resolved = _resolved_binding(
        capability_key=capability_key,
        target_id=target_id,
        config_digest=config_digest,
    )
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _descriptor(
    binding: FrozenCapabilityBinding | ResolvedCapabilityBinding,
    *,
    description: str = "tool description",
    interrupt_mode: str = "none",
    availability_status: str = "available",
    classification_revision: str = "plan02-v1",
    ruleset_digest: str = DIGEST_A,
    behavior_digest: str | None = None,
    descriptor_digest: str | None = None,
) -> CapabilityDescriptor:
    if isinstance(binding, FrozenCapabilityBinding):
        resolved = binding.resolved
    else:
        resolved = binding
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision=classification_revision,
            ruleset_digest=ruleset_digest,
        ),
        side_effect="read",
        parallel_safe=True,
        interrupt_mode=interrupt_mode,  # type: ignore[arg-type]
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=behavior_digest or DIGEST_B,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type="tool",
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=resolved.capability_key,
        description=description,
        input_schema=resolved.input_schema,
        output_schema=resolved.output_schema,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=descriptor_digest or DIGEST_C,
        executable_revision=resolved.executable_revision or "1",
        behavior=behavior,
        availability=CapabilityAvailability(
            status=availability_status,  # type: ignore[arg-type]
            reason_code=None if availability_status == "available" else "test_unavailable",
            compatibility_only=False,
        ),
        completion=resolved.completion,
    )


def _pair(
    capability_key: str,
    *,
    target_id: UUID | None = None,
    config_digest: str = DIGEST_B,
    description: str = "tool description",
    **descriptor_kwargs: Any,
) -> tuple[FrozenCapabilityBinding, CapabilityDescriptor]:
    binding = _frozen(
        capability_key=capability_key,
        target_id=target_id,
        config_digest=config_digest,
    )
    return binding, _descriptor(binding, description=description, **descriptor_kwargs)


# ---------------------------------------------------------------------------
# Step 1: fixed alias vectors
# ---------------------------------------------------------------------------


def test_fixed_sanitize_and_generated_alias_vectors() -> None:
    for domain_key, expected in FIXED_GENERATED.items():
        got = generated_alias_candidate(
            domain_key,
            provider_protocol=P,
            binding_contract_digest=DIGEST_A,
        )
        assert got == expected, domain_key
        assert is_valid_provider_alias(got)
        assert len(got) <= 64


def test_fixed_identity_digest_prefixes() -> None:
    for domain_key, expected in FIXED_IDENTITY_PREFIX.items():
        assert (
            identity_digest_prefix(
                provider_protocol=P,
                domain_key=domain_key,
                binding_contract_digest=DIGEST_A,
            )
            == expected
        )


def test_sanitize_special_characters() -> None:
    assert sanitize_domain_key_for_alias("skill.inject") == "skill_inject"
    assert sanitize_domain_key_for_alias("human/request-input") == "human_request_input"
    assert sanitize_domain_key_for_alias("with_underscores") == "with_underscores"
    assert sanitize_domain_key_for_alias("with-hyphens") == "with_hyphens"
    assert sanitize_domain_key_for_alias("with.dots") == "with_dots"
    assert sanitize_domain_key_for_alias("with/slashes") == "with_slashes"
    assert sanitize_domain_key_for_alias("with spaces") == "with_spaces"
    assert sanitize_domain_key_for_alias("UPPERCASE.Key") == "uppercase_key"
    assert sanitize_domain_key_for_alias("仅中文") == ""
    assert sanitize_domain_key_for_alias("emoji🚀tool") == "emoji_tool"
    assert sanitize_domain_key_for_alias("!!!") == ""


def test_normalized_collision_uses_digest_suffix() -> None:
    base = _manifest()
    allocated = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("Foo.Bar", DIGEST_A), ("foo_bar", DIGEST_B)),
    )
    by_key = {item.domain_key: item.provider_alias for item in allocated}
    # Sorted (domain_key, binding) => Foo.Bar wins the bare name; foo_bar suffixes.
    assert by_key["Foo.Bar"] == "foo_bar"
    assert by_key["foo_bar"] == "foo_bar_54d831ab"
    assert is_valid_provider_alias(by_key["foo_bar"])


def test_case_only_collision_rejected_at_occupancy() -> None:
    base = _manifest()
    allocated = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("Foo_Bar", DIGEST_A), ("foo_bar", DIGEST_B)),
    )
    by_key = {item.domain_key: item.provider_alias for item in allocated}
    assert by_key["Foo_Bar"] == "foo_bar"
    assert by_key["foo_bar"].startswith("foo_bar_")
    folded = {item.provider_alias.casefold() for item in allocated}
    assert len(folded) == len(allocated)


def test_valid_and_invalid_hints() -> None:
    base = _manifest()
    valid = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("skill.inject", DIGEST_A), ("human/request-input", DIGEST_B)),
        alias_hints={
            "skill.inject": "inject_skill",
            "human/request-input": "request_input",
        },
    )
    by_key = {item.domain_key: item.provider_alias for item in valid}
    assert by_key["skill.inject"] == "inject_skill"
    assert by_key["human/request-input"] == "request_input"

    invalid = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("skill.inject", DIGEST_A),),
        alias_hints={"skill.inject": "bad.alias!"},
    )
    assert invalid[0].provider_alias == "skill_inject"


def test_colliding_hints_none_win() -> None:
    base = _manifest()
    allocated = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("skill.inject", DIGEST_A), ("human/request-input", DIGEST_B)),
        alias_hints={
            "skill.inject": "same_hint",
            "human/request-input": "Same_Hint",
        },
    )
    by_key = {item.domain_key: item.provider_alias for item in allocated}
    assert by_key["skill.inject"] == "skill_inject"
    assert by_key["human/request-input"] == "human_request_input"


def test_reserved_control_alias_not_granted_by_hint() -> None:
    base = _manifest()
    reserved = sorted(DEFAULT_RESERVED_CONTROL_ALIASES)[0]
    allocated = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("x.tool", DIGEST_A),),
        alias_hints={"x.tool": reserved},
    )
    assert allocated[0].provider_alias != reserved
    assert allocated[0].provider_alias == "x_tool"
    assert reserved not in {item.provider_alias for item in allocated}


def test_same_domain_key_different_binding_on_fresh_surface_conflict() -> None:
    base = _manifest()
    with pytest.raises(ValueError, match="conflicting binding digests"):
        allocate_provider_aliases(
            provider_protocol=P,
            current_manifest=base,
            domain_bindings=(("skill.inject", DIGEST_A), ("skill.inject", DIGEST_B)),
        )


def test_same_domain_key_under_two_provider_protocols() -> None:
    base = _manifest()
    first = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("skill.inject", DIGEST_A),),
    )
    child = append_provider_aliases(base, aliases=first)
    second = allocate_provider_aliases(
        provider_protocol="other_protocol",
        current_manifest=child,
        domain_bindings=(("skill.inject", DIGEST_A),),
    )
    both = append_provider_aliases(child, aliases=second)
    assert len(both.provider_aliases) == 2
    protocols = {item.provider_protocol for item in both.provider_aliases}
    assert protocols == {P, "other_protocol"}
    for item in both.provider_aliases:
        assert item.domain_key == "skill.inject"
        assert item.provider_alias == "skill_inject"


def test_binding_digest_must_not_depend_on_alias_or_surface() -> None:
    binding = _frozen(capability_key="skill.inject", target_id=TARGET_A)
    # Inject forbidden reverse-edge keys into a copy of the snapshot.
    bad_snapshot = dict(binding.resolved.resolution_snapshot)
    bad_snapshot["providerAlias"] = "skill_inject"

    # Surface builder rejects snapshots that already embed reverse-edge keys even
    # if digests were forced equal (construct a bypassing object via model_construct).
    forced = ResolvedCapabilityBinding.model_construct(
        **{
            **binding.resolved.model_dump(mode="python"),
            "resolution_snapshot": bad_snapshot,
        }
    )
    forced_frozen = FrozenCapabilityBinding.model_construct(
        provenance=binding.provenance,
        ref=binding.ref,
        resolved=forced,
    )
    descriptor = _descriptor(binding)
    with pytest.raises(ValueError, match="must not depend on alias/Manifest/surface"):
        build_provider_tool_surface(
            manifest=_manifest(),
            provider_protocol=P,
            visible=((forced_frozen, descriptor),),
        )


# ---------------------------------------------------------------------------
# Step 2: append-only growth
# ---------------------------------------------------------------------------


def test_append_only_growth_preserves_existing_aliases() -> None:
    base = _manifest()
    first_alloc = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("zeta.tool", DIGEST_A),),
    )
    m1 = append_provider_aliases(base, aliases=first_alloc)
    assert [item.provider_alias for item in m1.provider_aliases] == ["zeta_tool"]
    frozen_first = m1.provider_aliases[0]

    # New Domain Key normalizes to the same base and would sort before zeta.tool.
    second_alloc = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=m1,
        domain_bindings=(
            ("zeta.tool", DIGEST_A),
            ("Zeta.Tool", DIGEST_B),
        ),
    )
    m2 = append_provider_aliases(m1, aliases=second_alloc)
    by_key = {item.domain_key: item for item in m2.provider_aliases}
    assert by_key["zeta.tool"].provider_alias == frozen_first.provider_alias
    assert by_key["zeta.tool"].binding_contract_digest == frozen_first.binding_contract_digest
    assert by_key["zeta.tool"].model_dump() == frozen_first.model_dump()
    assert by_key["Zeta.Tool"].provider_alias == "zeta_tool_5e6f6a48"
    assert m2.revision == m1.revision + 1
    assert m2.parent_digest == m1.manifest_digest


def test_resume_does_not_re_alias_existing_domain_key() -> None:
    base = _manifest()
    first = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("skill.inject", DIGEST_A),),
    )
    child = append_provider_aliases(base, aliases=first)
    with pytest.raises(ValueError, match="already frozen to a different binding"):
        allocate_provider_aliases(
            provider_protocol=P,
            current_manifest=child,
            domain_bindings=(("skill.inject", DIGEST_B),),
        )


# ---------------------------------------------------------------------------
# Step 3: forward/reverse maps and surface digests
# ---------------------------------------------------------------------------


def test_forward_reverse_maps_and_unknown_alias() -> None:
    base = _manifest()
    pair_a = _pair("skill.inject", target_id=TARGET_A, config_digest=DIGEST_B)
    pair_b = _pair("human/request-input", target_id=TARGET_B, config_digest=DIGEST_C)
    resolution = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair_a, pair_b),
    )
    surface = resolution.surface
    forward = forward_alias_map(surface)
    reverse = reverse_alias_map(surface)
    assert forward == {
        "human_request_input": "human/request-input",
        "skill_inject": "skill.inject",
    }
    assert reverse["skill_inject"][0] == "skill.inject"
    assert reverse["skill_inject"][1] == pair_a[0].ref.binding_contract_digest
    assert reverse["human_request_input"][0] == "human/request-input"
    # Exact one-to-one.
    assert len(forward) == len(set(forward.values())) == 2
    assert len(reverse) == 2

    tool = lookup_tool_by_alias(surface, "skill_inject")
    assert tool.domain_key == "skill.inject"
    with pytest.raises(KeyError, match="unknown provider alias"):
        lookup_tool_by_alias(surface, "missing_alias")
    # Case-fold variants are not accepted as lookups.
    with pytest.raises(KeyError):
        lookup_tool_by_alias(surface, "Skill_Inject")


def test_map_and_surface_digest_stable_across_input_ordering() -> None:
    base = _manifest()
    pair_a = _pair("skill.inject", target_id=TARGET_A, config_digest=DIGEST_B)
    pair_b = _pair("human/request-input", target_id=TARGET_B, config_digest=DIGEST_C)
    left = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair_a, pair_b),
    )
    right = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair_b, pair_a),
    )
    assert left.surface.alias_map_digest == right.surface.alias_map_digest
    assert left.surface.surface_digest == right.surface.surface_digest
    assert left.manifest.manifest_digest == right.manifest.manifest_digest
    assert [t.provider_alias for t in left.surface.tools] == [
        t.provider_alias for t in right.surface.tools
    ]


def test_description_schema_descriptor_behavior_change_surface_digest() -> None:
    base = _manifest()
    pair = _pair("skill.inject", target_id=TARGET_A, description="en-desc")
    surface_a = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair,),
        descriptions={"skill.inject": "en-desc"},
    ).surface

    surface_desc = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair,),
        descriptions={"skill.inject": "zh-desc"},
    ).surface
    assert surface_desc.surface_digest != surface_a.surface_digest
    assert surface_desc.tools[0].description == "zh-desc"

    pair_behavior = _pair(
        "skill.inject",
        target_id=TARGET_A,
        description="en-desc",
        behavior_digest=DIGEST_E,
        descriptor_digest=DIGEST_F,
    )
    surface_b = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair_behavior,),
        descriptions={"skill.inject": "en-desc"},
    ).surface
    assert surface_b.surface_digest != surface_a.surface_digest

    pair_class = _pair(
        "skill.inject",
        target_id=TARGET_A,
        description="en-desc",
        classification_revision="plan02-v2",
        ruleset_digest=DIGEST_E,
        behavior_digest=DIGEST_F,
        descriptor_digest=DIGEST_D,
    )
    surface_c = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair_class,),
        descriptions={"skill.inject": "en-desc"},
    ).surface
    assert surface_c.surface_digest != surface_a.surface_digest


def test_locale_text_is_frozen_for_surface() -> None:
    base = _manifest()
    binding, descriptor = _pair(
        "skill.inject",
        target_id=TARGET_A,
        description="descriptor-default",
    )
    resolution = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=((binding, descriptor),),
        descriptions={"skill.inject": "frozen-locale-text"},
    )
    assert resolution.surface.tools[0].description == "frozen-locale-text"
    # Changing the live descriptor text later must not mutate the frozen surface.
    assert descriptor.description == "descriptor-default"
    assert resolution.surface.tools[0].description != descriptor.description


def test_empty_surface_has_deterministic_digests() -> None:
    base = _manifest()
    empty_digest = base.manifest_digest
    left = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(),
    )
    right = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(),
    )
    # No new aliases => Plan 01 empty-alias Manifest digest unchanged.
    assert left.manifest.manifest_digest == empty_digest
    assert left.manifest.revision == base.revision
    assert left.manifest.provider_aliases == ()
    assert left.surface.tools == ()
    assert left.surface.alias_map_digest == right.surface.alias_map_digest
    assert left.surface.surface_digest == right.surface.surface_digest
    expected_alias_map = compute_alias_map_digest(
        provider_protocol=P,
        manifest_digest=empty_digest,
        aliases=(),
    )
    assert left.surface.alias_map_digest == expected_alias_map
    expected_surface = compute_surface_digest(
        provider_protocol=P,
        manifest_revision=base.revision,
        manifest_digest=empty_digest,
        alias_map_digest=expected_alias_map,
        tools=(),
    )
    assert left.surface.surface_digest == expected_surface


def test_plan01_empty_alias_manifest_digest_unchanged_before_first_append() -> None:
    base = _manifest()
    # Hard-coded Plan 01 empty-alias v1 vector for this fixture set.
    assert base.provider_aliases == ()
    assert base.manifest_digest == (
        "9471cd219121b1cd0dd5bee75e3b04a4f5dac1faf0e6b8caffa46bcd2ab39fec"
    )
    resolution = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(),
    )
    assert resolution.manifest.manifest_digest == base.manifest_digest


def test_scope_run_mismatch_rejected() -> None:
    base = _manifest(run_id=RUN_ID)
    scope = create_execution_scope(
        run_id=RUN_ID_B,
        conversation_id=CONV_ID,
        principal=__import__(
            "app.assistant.capabilities.contracts", fromlist=["CapabilityPrincipal"]
        ).CapabilityPrincipal(
            principal_type="test",
            principal_id="p1",
            authenticated=True,
        ),
        tenant_scope_id=None,
    )
    with pytest.raises(ValueError, match="run_id"):
        build_provider_tool_surface(
            manifest=base,
            provider_protocol=P,
            visible=(),
            scope=scope,
        )


def test_scope_dependent_surfaces_remain_run_isolated() -> None:
    pair = _pair("skill.inject", target_id=TARGET_A)
    scope_a = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=__import__(
            "app.assistant.capabilities.contracts", fromlist=["CapabilityPrincipal"]
        ).CapabilityPrincipal(
            principal_type="test",
            principal_id="principal-a",
            authenticated=True,
        ),
        tenant_scope_id="tenant-a",
    )
    scope_b = create_execution_scope(
        run_id=RUN_ID_B,
        conversation_id=None,
        principal=__import__(
            "app.assistant.capabilities.contracts", fromlist=["CapabilityPrincipal"]
        ).CapabilityPrincipal(
            principal_type="test",
            principal_id="principal-b",
            authenticated=True,
        ),
        tenant_scope_id="tenant-b",
    )
    surface_a = build_provider_tool_surface(
        manifest=_manifest(run_id=RUN_ID),
        provider_protocol=P,
        visible=(pair,),
        scope=scope_a,
    )
    surface_b = build_provider_tool_surface(
        manifest=_manifest(run_id=RUN_ID_B),
        provider_protocol=P,
        visible=(pair,),
        scope=scope_b,
    )
    # Same Domain Keys, different runs => distinct Manifest digests / surfaces.
    assert surface_a.manifest.manifest_digest != surface_b.manifest.manifest_digest
    assert surface_a.surface.surface_digest != surface_b.surface.surface_digest
    assert scope_a.scope_digest != scope_b.scope_digest


# ---------------------------------------------------------------------------
# Step 6: surface completeness validation
# ---------------------------------------------------------------------------


def test_unavailable_descriptor_rejected() -> None:
    base = _manifest()
    pair = _pair("skill.inject", target_id=TARGET_A, availability_status="disabled")
    with pytest.raises(ValueError, match="not available"):
        build_provider_tool_surface(
            manifest=base,
            provider_protocol=P,
            visible=(pair,),
        )


def test_legacy_blocking_descriptor_rejected() -> None:
    base = _manifest()
    pair = _pair("skill.inject", target_id=TARGET_A, interrupt_mode="legacy_blocking")
    with pytest.raises(ValueError, match="legacy_blocking"):
        build_provider_tool_surface(
            manifest=base,
            provider_protocol=P,
            visible=(pair,),
        )


def test_duplicate_domain_key_on_surface_rejected() -> None:
    base = _manifest()
    pair_a = _pair("skill.inject", target_id=TARGET_A, config_digest=DIGEST_B)
    pair_b = _pair("skill.inject", target_id=TARGET_B, config_digest=DIGEST_C)
    with pytest.raises(ValueError, match="duplicate Domain Key"):
        build_provider_tool_surface(
            manifest=base,
            provider_protocol=P,
            visible=(pair_a, pair_b),
        )


def test_descriptor_binding_key_mismatch_rejected() -> None:
    base = _manifest()
    binding = _frozen(capability_key="skill.inject", target_id=TARGET_A)
    other = _frozen(capability_key="other.tool", target_id=TARGET_B)
    descriptor = _descriptor(other)
    with pytest.raises(ValueError, match="capability_key"):
        build_provider_tool_surface(
            manifest=base,
            provider_protocol=P,
            visible=((binding, descriptor),),
        )


def test_hints_do_not_grant_permission_or_visibility() -> None:
    """Hints only affect transport spelling; unavailable tools stay rejected."""
    base = _manifest()
    pair = _pair("skill.inject", target_id=TARGET_A, availability_status="missing")
    with pytest.raises(ValueError, match="not available"):
        build_provider_tool_surface(
            manifest=base,
            provider_protocol=P,
            visible=(pair,),
            alias_hints={"skill.inject": "totally_allowed_looking_name"},
        )


def test_surface_tools_sorted_by_alias_and_complete() -> None:
    base = _manifest()
    pairs = (
        _pair("zeta.tool", target_id=TARGET_A, config_digest=DIGEST_B),
        _pair("alpha.tool", target_id=TARGET_B, config_digest=DIGEST_C),
        _pair("mid.tool", target_id=TARGET_C, config_digest=DIGEST_D),
    )
    resolution = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=pairs,
    )
    aliases = [tool.provider_alias for tool in resolution.surface.tools]
    assert aliases == sorted(aliases)
    assert set(aliases) == {"alpha_tool", "mid_tool", "zeta_tool"}
    assert resolution.manifest.revision == 2
    assert len(resolution.manifest.provider_aliases) == 3
    # Digest dependency: alias map uses child manifest digest.
    triples = [
        (
            tool.domain_key,
            tool.provider_alias,
            tool.binding.ref.binding_contract_digest,
        )
        for tool in resolution.surface.tools
    ]
    assert resolution.surface.alias_map_digest == compute_alias_map_digest(
        provider_protocol=P,
        manifest_digest=resolution.manifest.manifest_digest,
        aliases=triples,
    )
    assert resolution.surface.surface_digest == compute_surface_digest(
        provider_protocol=P,
        manifest_revision=resolution.manifest.revision,
        manifest_digest=resolution.manifest.manifest_digest,
        alias_map_digest=resolution.surface.alias_map_digest,
        tools=resolution.surface.tools,
    )


def test_allocation_order_independent_of_python_hash() -> None:
    base = _manifest()
    bindings = (
        ("m.tool", DIGEST_A),
        ("a.tool", DIGEST_B),
        ("z.tool", DIGEST_C),
    )
    left = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=bindings,
    )
    right = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=tuple(reversed(bindings)),
    )
    assert [(i.domain_key, i.provider_alias) for i in left] == [
        (i.domain_key, i.provider_alias) for i in right
    ]


def test_generated_alias_candidate_never_uses_process_hash() -> None:
    # Identity is SHA-256 over protocol/domain/binding; stable across processes.
    a = generated_alias_candidate(
        "仅中文",
        provider_protocol=P,
        binding_contract_digest=DIGEST_A,
    )
    b = generated_alias_candidate(
        "仅中文",
        provider_protocol=P,
        binding_contract_digest=DIGEST_A,
    )
    assert a == b == "cap_3c7157e27346"


def test_provider_tool_definition_requires_sorted_surface() -> None:
    base = _manifest()
    pair = _pair("skill.inject", target_id=TARGET_A)
    resolution = build_provider_tool_surface(
        manifest=base,
        provider_protocol=P,
        visible=(pair,),
    )
    tool = resolution.surface.tools[0]
    # Tampering sort order is rejected by ProviderToolSurface.
    with pytest.raises(ValidationError):
        # Construct an unsorted multi-tool surface manually.
        other_binding, other_desc = _pair(
            "zzz.tool",
            target_id=TARGET_B,
            config_digest=DIGEST_C,
        )
        other = ProviderToolDefinition(
            provider_alias="aaa_tool",
            domain_key="zzz.tool",
            description="x",
            input_schema=other_desc.input_schema,
            binding=other_binding,
            descriptor=other_desc,
        )
        # Put higher alias first.
        tools = (tool, other) if tool.provider_alias > other.provider_alias else (other, tool)
        if tools[0].provider_alias <= tools[1].provider_alias:
            tools = (tools[1], tools[0])
        from app.assistant.provider_loop.contracts import ProviderToolSurface

        ProviderToolSurface(
            provider_protocol=P,
            manifest_revision=resolution.manifest.revision,
            manifest_digest=resolution.manifest.manifest_digest,
            alias_map_digest=resolution.surface.alias_map_digest,
            tools=tools,
            surface_digest=resolution.surface.surface_digest,
        )


def test_same_domain_key_different_binding_digest_produces_distinct_identity_prefix() -> None:
    p1 = identity_digest_prefix(
        provider_protocol=P,
        domain_key="skill.inject",
        binding_contract_digest=DIGEST_A,
    )
    p2 = identity_digest_prefix(
        provider_protocol=P,
        domain_key="skill.inject",
        binding_contract_digest=DIGEST_B,
    )
    assert p1 == "eaffde4da287"
    assert p2 == "e2bcaf176440"
    assert p1 != p2


def test_digest_prefix_collision_fixture_suffixes_deterministically() -> None:
    """When the generated base is already occupied, suffix uses identity material."""
    base = _manifest()
    # Pre-freeze the bare generated name for a different domain key.
    pre = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=base,
        domain_bindings=(("occupied.name", DIGEST_A),),
        alias_hints={"occupied.name": "skill_inject"},
    )
    child = append_provider_aliases(base, aliases=pre)
    assert child.provider_aliases[0].provider_alias == "skill_inject"

    allocated = allocate_provider_aliases(
        provider_protocol=P,
        current_manifest=child,
        domain_bindings=(
            ("occupied.name", DIGEST_A),
            ("skill.inject", DIGEST_A),
        ),
    )
    by_key = {item.domain_key: item.provider_alias for item in allocated}
    assert by_key["occupied.name"] == "skill_inject"
    # skill.inject base is occupied; must gain a deterministic suffix.
    assert by_key["skill.inject"].startswith("skill_inject_")
    assert is_valid_provider_alias(by_key["skill.inject"])
    # Hard-coded suffix from identity material for (P, skill.inject, DIGEST_A).
    material = identity_digest_prefix(
        provider_protocol=P,
        domain_key="skill.inject",
        binding_contract_digest=DIGEST_A,
        length=64,
    )
    assert by_key["skill.inject"] == f"skill_inject_{material[:8]}"
