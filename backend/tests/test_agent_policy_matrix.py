"""Plan 05 Task 1: EffectiveRunPolicySnapshot, RunBudgetLimits, contract digests.

Full reason-code matrix is Task 2; this file freezes contract digests and limit
normalization for Task 1.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityPrincipal,
    FrozenBindingProvenance,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    create_base_run_manifest,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.main_agent.authorization import (  # noqa: E402
    LOCAL_ASSISTANT_PRINCIPAL,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
)
from app.assistant.policy.contracts import (  # noqa: E402
    ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
    ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS,
    ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS,
    EffectiveRunPolicySnapshot,
    OwnerBudgetLimits,
    OwnerPolicyRef,
    PLAN05_RELEASE_GATE_SIDE_EFFECTS,
    RunBudgetLimits,
    build_capability_exposure_ref,
    build_effective_run_policy_snapshot,
    build_manifest_exposure_index,
    build_owner_budget_limits,
    build_owner_policy_ref,
    compute_assistant_chat_entrypoint_policy_digest,
    compute_default_global_policy_digest,
    compute_grant_source_set_digest,
    normalize_owner_budget_limits,
    normalize_run_budget_limits,
)
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402

RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000010")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000011")
PACKAGE_ID = UUID("00000000-0000-4000-8000-000000000020")
SKILL_VERSION_ID = UUID("00000000-0000-4000-8000-000000000021")

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _main_agent(**overrides: Any) -> ResolvedMainAgentRef:
    payload = {
        "profile_id": PROFILE_ID,
        "version_id": PROFILE_VERSION_ID,
        "profile_key": "general_chat",
        "sequence": 1,
        "content_digest": DIGEST_A,
    }
    payload.update(overrides)
    return ResolvedMainAgentRef(**payload)


def _resolved_binding(capability_key: str = "skill.search") -> ResolvedCapabilityBinding:
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
    tid = uuid4()
    target_identity = f"system-tool:{capability_key}"
    config_digest = DIGEST_B
    executable_revision = "1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(tid),
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
        target_id=tid,
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
        target_id=tid,
        target_version_id=None,
        resolved_tool_id=tid,
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


def _empty_exposure_index(*, revision: int = 1, manifest_digest: str = DIGEST_A):
    return build_manifest_exposure_index(
        manifest_revision=revision,
        manifest_digest=manifest_digest,
        exposures=(),
    )


def test_run_budget_defaults_match_plan() -> None:
    limits = normalize_run_budget_limits()
    assert limits.max_provider_rounds == 8
    assert limits.max_main_agent_cycles == 1
    assert limits.max_active_skills == 4
    assert limits.max_total_capability_calls == 16
    assert limits.max_parallel_calls == 4
    assert limits.max_capability_depth == 4
    assert limits.max_agent_depth == 2
    assert limits.max_same_read_signature == 3
    assert limits.max_prompt_tokens is None
    assert limits.max_completion_tokens == 4096
    assert limits.max_wall_time_ms == 120_000
    assert limits.max_completion_followup_rounds == 2


def test_run_budget_hard_ceilings_checked_in() -> None:
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_provider_rounds"] == 16
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_main_agent_cycles"] == 1
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_active_skills"] == 8
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_total_capability_calls"] == 64
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_parallel_calls"] == 8
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_capability_depth"] == 8
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_agent_depth"] == 4
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_same_read_signature"] == 10
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_prompt_tokens"] == 1_000_000
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_completion_tokens"] == 16_384
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_wall_time_ms"] == 600_000
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_completion_followup_rounds"] == 4


def test_operator_and_profile_may_only_lower() -> None:
    # Operator tries to raise above default — clamped by hard then min with default.
    raised = normalize_run_budget_limits(
        operator_limits={"max_provider_rounds": 100},
    )
    assert raised.max_provider_rounds == 8  # min(16 hard, 8 default, 16 clamped op)

    lowered = normalize_run_budget_limits(
        operator_limits={"max_provider_rounds": 3, "max_active_skills": 2},
    )
    assert lowered.max_provider_rounds == 3
    assert lowered.max_active_skills == 2

    profile_lower = normalize_run_budget_limits(
        profile_output_budget={
            "max_provider_rounds": 5,
            "max_total_capability_calls": 10,
            "max_parallel_calls": 2,
            "max_capability_depth": 3,
            "max_agent_depth": 1,
            "max_same_read_signature": 2,
            "max_completion_tokens": 2048,
            "max_wall_time_ms": 60_000,
            "max_completion_followup_rounds": 1,
        }
    )
    assert profile_lower.max_provider_rounds == 5
    assert profile_lower.max_completion_tokens == 2048
    assert profile_lower.max_wall_time_ms == 60_000


def test_run_budget_frozen_forbidden_extra_round_trip() -> None:
    limits = normalize_run_budget_limits()
    with pytest.raises(ValidationError):
        RunBudgetLimits(
            **limits.model_dump(),
            extra_field=1,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        limits.max_provider_rounds = 1  # type: ignore[misc]
    payload = limits.model_dump(mode="json", by_alias=True)
    restored = RunBudgetLimits.model_validate(payload)
    assert restored == limits


def test_owner_budget_limits_capped_by_run() -> None:
    run_limits = normalize_run_budget_limits()
    main = normalize_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        run_limits=run_limits,
    )
    assert main.max_calls == 8
    assert main.max_same_read_signature == run_limits.max_same_read_signature
    assert len(main.owner_budget_digest) == 64

    skill = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        run_limits=run_limits,
        max_skill_calls=100,
        max_same_read_calls=50,
    )
    assert skill.max_calls == run_limits.max_total_capability_calls
    assert skill.max_same_read_signature == run_limits.max_same_read_signature

    instruction = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        run_limits=run_limits,
        is_instruction_only=True,
    )
    assert instruction.max_calls == 0


def test_owner_policy_ref_digest_stable() -> None:
    left = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
        allowed_side_effects=("none", "compute", "read"),
    )
    right = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
        allowed_side_effects=("none", "compute", "read"),
    )
    assert left == right
    assert left.policy_digest == right.policy_digest
    with pytest.raises(ValidationError):
        OwnerPolicyRef(
            owner_kind="main_agent",
            owner_id="general_chat",
            owner_version_id=PROFILE_VERSION_ID,
            policy_digest=left.policy_digest,
            extra=True,  # type: ignore[call-arg]
        )


def test_entrypoint_policy_digest_fixed_and_uses_platform_ceiling() -> None:
    digest = compute_assistant_chat_entrypoint_policy_digest()
    assert digest == ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST
    assert len(digest) == 64
    assert tuple(PLAN05_RELEASE_GATE_SIDE_EFFECTS) == (
        "none",
        "compute",
        "read",
    )
    assert MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_side_effects == (
        "none",
        "compute",
        "read",
    )


def test_effective_policy_snapshot_digest_changes_only_for_semantics() -> None:
    principal = LOCAL_ASSISTANT_PRINCIPAL
    exposure_index = _empty_exposure_index()
    owner = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
        allowed_side_effects=PLAN05_RELEASE_GATE_SIDE_EFFECTS,
    )
    limits = normalize_run_budget_limits()
    base = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=principal,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=exposure_index,
        owner_policy_refs=(owner,),
        run_budget_limits=limits,
    )
    assert isinstance(base, EffectiveRunPolicySnapshot)
    assert base.policy_contract_version == 1
    assert base.entrypoint == "main_agent"
    assert base.principal == principal
    assert len(base.effective_policy_digest) == 64

    # Rebuild with identical semantic inputs → same digest.
    again = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=principal,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=exposure_index,
        owner_policy_refs=(owner,),
        run_budget_limits=limits,
    )
    assert again.effective_policy_digest == base.effective_policy_digest
    assert again.grant_source_set_digest == base.grant_source_set_digest

    # Limit change → digest change.
    lower_limits = normalize_run_budget_limits(
        operator_limits={"max_provider_rounds": 3}
    )
    changed_limits = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=principal,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=exposure_index,
        owner_policy_refs=(owner,),
        run_budget_limits=lower_limits,
    )
    assert changed_limits.effective_policy_digest != base.effective_policy_digest

    # Profile digest change → digest change.
    changed_profile = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=principal,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_B,
        exposure_index=exposure_index,
        owner_policy_refs=(owner,),
        run_budget_limits=limits,
    )
    assert changed_profile.effective_policy_digest != base.effective_policy_digest

    # Exposure change → digest change.
    binding = project_frozen_capability_binding(
        resolved=_resolved_binding("skill.search"),
        provenance=FrozenBindingProvenance(
            origin="main_agent_profile",
            binding_row_id=None,
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=DIGEST_D,
        ),
    )
    exposure = build_capability_exposure_ref(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    index_with_exposure = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(exposure,),
    )
    changed_exposure = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=principal,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index_with_exposure,
        owner_policy_refs=(owner,),
        run_budget_limits=limits,
    )
    assert changed_exposure.effective_policy_digest != base.effective_policy_digest


def test_effective_policy_snapshot_frozen_round_trip() -> None:
    limits = normalize_run_budget_limits()
    owner = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
    )
    snap = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=_empty_exposure_index(),
        owner_policy_refs=(owner,),
        run_budget_limits=limits,
    )
    payload = snap.model_dump(mode="json", by_alias=True)
    restored = EffectiveRunPolicySnapshot.model_validate(payload)
    assert restored == snap
    assert restored.effective_policy_digest == snap.effective_policy_digest
    with pytest.raises(ValidationError):
        snap.app_build_revision = "x"  # type: ignore[misc]


def test_grant_source_set_digest_order_independent() -> None:
    a = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
    )
    b = build_owner_policy_ref(
        owner_kind="skill_version",
        owner_id=str(PACKAGE_ID),
        owner_version_id=SKILL_VERSION_ID,
        content_or_policy_digest=DIGEST_B,
        max_skill_calls=16,
        max_same_read_calls=3,
        requires_terminal_output=False,
        terminal_text_allowed=False,
    )
    left = compute_grant_source_set_digest(owner_policy_refs=(a, b))
    right = compute_grant_source_set_digest(owner_policy_refs=(b, a))
    assert left == right


def test_manifest_effective_policy_digest_field_compatible() -> None:
    """Existing Manifest v1 still accepts empty/base effective_policy_digest values."""
    limits = normalize_run_budget_limits()
    owner = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
    )
    snap = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=_empty_exposure_index(),
        owner_policy_refs=(owner,),
        run_budget_limits=limits,
    )
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=snap.effective_policy_digest,
    )
    assert base.effective_policy_digest == snap.effective_policy_digest
    # None remains valid for empty/base vectors.
    empty = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    assert empty.effective_policy_digest is None


def test_skills_cannot_supply_run_limits() -> None:
    """normalize_run_budget_limits has no skill parameter; defaults ignore skill counts."""
    one = normalize_run_budget_limits()
    # Adding profile/operator only; no skill path exists.
    many = normalize_run_budget_limits(
        profile_output_budget=None,
        operator_limits=None,
    )
    assert one == many
    assert one.max_total_capability_calls == ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS[
        "max_total_capability_calls"
    ]


def test_owner_budget_digest_canonical() -> None:
    limits = build_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        max_calls=8,
        max_same_read_signature=3,
    )
    again = build_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        max_calls=8,
        max_same_read_signature=3,
    )
    assert limits.owner_budget_digest == again.owner_budget_digest
    with pytest.raises(ValidationError):
        OwnerBudgetLimits(
            owner_kind="skill_version",
            owner_version_id=SKILL_VERSION_ID,
            max_calls=8,
            max_same_read_signature=3,
            owner_budget_digest=limits.owner_budget_digest,
            extra=1,  # type: ignore[call-arg]
        )


def test_global_policy_digest_depends_on_profile() -> None:
    a = compute_default_global_policy_digest(profile_content_digest=DIGEST_A)
    b = compute_default_global_policy_digest(profile_content_digest=DIGEST_B)
    assert a != b
    assert a == compute_default_global_policy_digest(profile_content_digest=DIGEST_A)


def test_principal_is_local_assistant_service() -> None:
    assert LOCAL_ASSISTANT_PRINCIPAL.principal_type == "service"
    assert LOCAL_ASSISTANT_PRINCIPAL.principal_id == "local-assistant"
    assert LOCAL_ASSISTANT_PRINCIPAL.authenticated is True
    # CapabilityPrincipal still forbids extras.
    with pytest.raises(ValidationError):
        CapabilityPrincipal(
            principal_type="service",
            principal_id="local-assistant",
            authenticated=True,
            tenant="x",  # type: ignore[call-arg]
        )
