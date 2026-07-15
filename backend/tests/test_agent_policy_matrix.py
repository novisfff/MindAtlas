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


def test_profile_context_budget_lowers_max_active_skills() -> None:
    """max_active_skills is resolved from Profile contextBudget, not outputBudget."""
    lowered = normalize_run_budget_limits(
        profile_context_budget={"max_active_skills": 2},
    )
    assert lowered.max_active_skills == 2
    # Other limits remain entrypoint defaults when only context budget is supplied.
    assert lowered.max_provider_rounds == 8

    # Profile may not raise above hard ceiling — clamp then min with default.
    raised = normalize_run_budget_limits(
        profile_context_budget={"max_active_skills": 100},
    )
    assert raised.max_active_skills == 4  # min(8 hard, 4 default, 8 clamped profile)

    # Attribute-style ContextBudgetV1-like source is accepted.
    class _Ctx:
        max_active_skills = 1

    attr_lowered = normalize_run_budget_limits(profile_context_budget=_Ctx())
    assert attr_lowered.max_active_skills == 1

    # outputBudget alone cannot set max_active_skills.
    output_only = normalize_run_budget_limits(
        profile_output_budget={"max_active_skills": 1, "max_provider_rounds": 5},
    )
    assert output_only.max_active_skills == 4
    assert output_only.max_provider_rounds == 5


def test_run_budget_and_snapshot_source_mutation_isolation() -> None:
    """Mutating operator/profile source maps after build must not alter digests."""
    operator = {"max_provider_rounds": 3, "max_active_skills": 2}
    context = {"max_active_skills": 1}
    limits = normalize_run_budget_limits(
        operator_limits=operator,
        profile_context_budget=context,
    )
    assert limits.max_active_skills == 1
    assert limits.max_provider_rounds == 3

    operator["max_provider_rounds"] = 99
    operator["max_active_skills"] = 99
    context["max_active_skills"] = 99
    assert limits.max_active_skills == 1
    assert limits.max_provider_rounds == 3

    owner_refs = [
        build_owner_policy_ref(
            owner_kind="main_agent",
            owner_id="general_chat",
            owner_version_id=PROFILE_VERSION_ID,
            content_or_policy_digest=DIGEST_A,
        )
    ]
    snap = build_effective_run_policy_snapshot(
        app_build_revision="test-build",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=_empty_exposure_index(),
        owner_policy_refs=owner_refs,
        run_budget_limits=limits,
    )
    digest_before = snap.effective_policy_digest
    # Mutate the source list of owner refs after snapshot construction.
    owner_refs.clear()
    assert snap.effective_policy_digest == digest_before
    assert len(snap.owner_policy_refs) == 1
    assert snap.run_budget_limits.max_active_skills == 1


# ---------------------------------------------------------------------------
# Plan 05 Task 2: pure ordered authorization reason-code matrix
# ---------------------------------------------------------------------------

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
)
from app.assistant.domain.contracts import CapabilityCompletionContract  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest as _binding_schema_digest,
    normalize_binding_schema as _normalize_binding_schema,
)
from app.assistant.policy.contracts import (  # noqa: E402
    AUTHORIZATION_REASON_CODES,
    AuthorizationDecision,
    EffectiveCapabilityGrant,
    build_authorization_decision,
    build_effective_capability_grant,
    compute_authorization_decision_digest,
    compute_grant_source_digest,
    compute_principal_digest,
)
from app.assistant.policy.evaluator import (  # noqa: E402
    AuthorizationProposal,
    GlobalPolicyView,
    OwnerGrantMaterial,
    derive_effective_capability_grant,
    evaluate_authorization,
    owner_material_key,
)


SCOPE_DIGEST = "1" * 64
MANIFEST_DIGEST = DIGEST_A
DESCRIPTOR_DIGEST = DIGEST_C
CONV_ID = UUID("00000000-0000-4000-8000-000000000099")


def _timeout():
    return CapabilityTimeoutPolicy(
        mode="cooperative", timeout_seconds=None, cancellation_supported=True
    )


def _behavior(*, side_effect: str = "read", interrupt_mode: str = "none"):
    return CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        side_effect=side_effect,  # type: ignore[arg-type]
        parallel_safe=True,
        interrupt_mode=interrupt_mode,  # type: ignore[arg-type]
        timeout_policy=_timeout(),
        behavior_digest=DIGEST_B,
    )


def _descriptor_for(
    *,
    capability_key: str,
    binding_contract_digest: str,
    resolution_digest: str,
    dependency_closure_digest: str,
    descriptor_digest: str = DESCRIPTOR_DIGEST,
    side_effect: str = "read",
    interrupt_mode: str = "none",
    availability_status: str = "available",
):
    in_schema = _normalize_binding_schema({"type": "object"}, require_object_root=True)
    out_schema = _normalize_binding_schema({"type": "object"}, require_object_root=True)
    return CapabilityDescriptor(
        capability_key=capability_key,
        capability_type="tool",
        target_identity=f"system-tool:{capability_key}",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        resolution_digest=resolution_digest,
        binding_contract_digest=binding_contract_digest,
        dependency_closure_digest=dependency_closure_digest,
        display_name=capability_key,
        description="d",
        input_schema=in_schema,
        output_schema=out_schema,
        input_schema_digest=_binding_schema_digest(in_schema),
        output_schema_digest=_binding_schema_digest(out_schema),
        descriptor_digest=descriptor_digest,
        executable_revision="plan05-dev",
        behavior=_behavior(side_effect=side_effect, interrupt_mode=interrupt_mode),
        availability=CapabilityAvailability(status=availability_status),  # type: ignore[arg-type]
        completion=CapabilityCompletionContract(terminal_output=False, needs_followup=True),
    )


def _main_agent_fixture(*, capability_key: str = "skill.search"):
    """Build snapshot + exposure + owner material for a Main Agent control."""
    resolved = _resolved_binding(capability_key)
    binding = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="main_agent_profile",
            binding_row_id=None,
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=DIGEST_D,
        ),
    )
    exposure = build_capability_exposure_ref(
        domain_key=capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DESCRIPTOR_DIGEST,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=MANIFEST_DIGEST,
        exposures=(exposure,),
    )
    owner_ref = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=DIGEST_A,
        allowed_side_effects=PLAN05_RELEASE_GATE_SIDE_EFFECTS,
    )
    limits = normalize_run_budget_limits()
    snap = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=(owner_ref,),
        run_budget_limits=limits,
    )
    material = OwnerGrantMaterial(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        policy_digest=owner_ref.policy_digest,
        author_allowed_side_effects=("none", "compute", "read"),
        declared_capability_keys=None,
    )
    owners = {owner_material_key(material): material}
    return snap, binding, exposure, owners, material


def _skill_fixture(
    *,
    capability_key: str = "skill.reader",
    author_effects: tuple[str, ...] = ("read", "compute"),
    is_instruction_only: bool = False,
    package_id: UUID | None = None,
    version_id: UUID | None = None,
):
    pkg = package_id or PACKAGE_ID
    ver = version_id or SKILL_VERSION_ID
    resolved = _resolved_binding(capability_key)
    binding = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="skill_version",
            binding_row_id=None,
            owner_version_id=ver,
            source_snapshot_digest=DIGEST_D,
        ),
    )
    exposure = build_capability_exposure_ref(
        domain_key=capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DESCRIPTOR_DIGEST,
        owner_kind="skill_version",
        owner_id=str(pkg),
        owner_version_id=ver,
    )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=MANIFEST_DIGEST,
        exposures=(exposure,),
    )
    owner_ref = build_owner_policy_ref(
        owner_kind="skill_version",
        owner_id=str(pkg),
        owner_version_id=ver,
        content_or_policy_digest=DIGEST_B,
        allowed_side_effects=author_effects,
    )
    limits = normalize_run_budget_limits()
    snap = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=(owner_ref,),
        run_budget_limits=limits,
    )
    material = OwnerGrantMaterial(
        owner_kind="skill_version",
        owner_id=str(pkg),
        owner_version_id=ver,
        policy_digest=owner_ref.policy_digest,
        author_allowed_side_effects=author_effects,
        declared_capability_keys=frozenset({capability_key}) if not is_instruction_only else frozenset(),
        is_instruction_only=is_instruction_only,
    )
    owners = {owner_material_key(material): material}
    return snap, binding, exposure, owners, material


def _proposal(
    *,
    binding,
    exposure,
    side_effect: str = "read",
    interrupt_mode: str = "none",
    availability_status: str = "available",
    principal=None,
    nesting_depth: int = 0,
    max_capability_depth: int = 4,
    scope_digest: str = SCOPE_DIGEST,
    expected_scope_digest: str = SCOPE_DIGEST,
    run_id=RUN_ID,
    expected_run_id=RUN_ID,
    conversation_id=CONV_ID,
    expected_conversation_id=CONV_ID,
    manifest_digest: str = MANIFEST_DIGEST,
    expected_manifest_digest: str = MANIFEST_DIGEST,
    claimed_owner_kind=None,
    claimed_owner_id=None,
    claimed_owner_version_id=None,
    descriptor_digest: str = DESCRIPTOR_DIGEST,
    capability_key: str | None = None,
    binding_contract_digest: str | None = None,
    resolution_digest: str | None = None,
    dependency_closure_digest: str | None = None,
):
    key = capability_key or binding.ref.capability_key
    return AuthorizationProposal(
        run_id=run_id,
        conversation_id=conversation_id,
        scope_digest=scope_digest,
        expected_scope_digest=expected_scope_digest,
        expected_run_id=expected_run_id,
        expected_conversation_id=expected_conversation_id,
        manifest_digest=manifest_digest,
        expected_manifest_digest=expected_manifest_digest,
        capability_key=key,
        binding_contract_digest=binding_contract_digest or binding.ref.binding_contract_digest,
        resolution_digest=resolution_digest or binding.ref.resolution_digest,
        dependency_closure_digest=dependency_closure_digest
        or binding.ref.dependency_closure_digest,
        descriptor_digest=descriptor_digest,
        descriptor_side_effect=side_effect,  # type: ignore[arg-type]
        descriptor_interrupt_mode=interrupt_mode,
        descriptor_availability_status=availability_status,
        principal=principal or LOCAL_ASSISTANT_PRINCIPAL,
        nesting_depth=nesting_depth,
        max_capability_depth=max_capability_depth,
        claimed_owner_kind=claimed_owner_kind if claimed_owner_kind is not None else exposure.owner_kind,
        claimed_owner_id=claimed_owner_id if claimed_owner_id is not None else exposure.owner_id,
        claimed_owner_version_id=(
            claimed_owner_version_id
            if claimed_owner_version_id is not None
            else exposure.owner_version_id
        ),
    )


def test_authorization_reason_codes_stable_order() -> None:
    assert AUTHORIZATION_REASON_CODES == (
        "scope_mismatch",
        "manifest_surface_mismatch",
        "exposure_missing",
        "exposure_ambiguous",
        "owner_mismatch",
        "principal_unauthenticated",
        "principal_not_allowed",
        "entrypoint_not_allowed",
        "global_policy_denied",
        "owner_capability_not_declared",
        "owner_side_effect_denied",
        "release_gate_denied",
        "target_unavailable",
        "version_or_digest_drift",
        "recursion_denied",
        "allowed",
    )


def test_main_agent_allowed_read() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="read"),
        owner_materials=owners,
    )
    assert decision.allowed is True
    assert decision.reason_code == "allowed"
    assert decision.allowed_side_effects == ("none", "compute", "read")
    assert decision.grant_source_digest is not None
    assert len(decision.decision_digest) == 64
    assert decision.exposure_digest == exposure.exposure_digest


def test_scope_mismatch() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            scope_digest="2" * 64,
            expected_scope_digest=SCOPE_DIGEST,
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "scope_mismatch"
    assert decision.grant_source_digest is None
    assert decision.allowed_side_effects == ()


def test_manifest_surface_mismatch() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            manifest_digest="9" * 64,
            expected_manifest_digest=MANIFEST_DIGEST,
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "manifest_surface_mismatch"


def test_exposure_missing() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            capability_key="guessed.stale.alias",
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "exposure_missing"


def test_exposure_ambiguous_binding_mismatch() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            binding_contract_digest="e" * 64,
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "exposure_ambiguous"


def test_owner_mismatch_claimed() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            claimed_owner_kind="skill_version",
            claimed_owner_id=str(PACKAGE_ID),
            claimed_owner_version_id=SKILL_VERSION_ID,
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_mismatch"


def test_owner_mismatch_missing_material() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure),
        owner_materials={},  # no materials
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_mismatch"


def test_principal_unauthenticated() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    unauth = CapabilityPrincipal(
        principal_type="service",
        principal_id="local-assistant",
        authenticated=False,
    )
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, principal=unauth),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "principal_unauthenticated"


def test_principal_not_allowed() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    other = CapabilityPrincipal(
        principal_type="user",
        principal_id="alice",
        authenticated=True,
    )
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, principal=other),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "principal_not_allowed"


def test_global_policy_denied_named_key() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    gp = GlobalPolicyView(
        policy_digest=snap.global_policy_digest,
        denied_capability_keys=frozenset({"skill.search"}),
    )
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure),
        owner_materials=owners,
        global_policy=gp,
    )
    assert decision.allowed is False
    assert decision.reason_code == "global_policy_denied"


def test_global_deny_compute_shrinks_grant_allowed_side_effects() -> None:
    """Global deny of ``compute`` shrinks the frozen grant lattice."""
    snap, binding, exposure, owners, material = _main_agent_fixture()
    gp = GlobalPolicyView(
        policy_digest=snap.global_policy_digest,
        denied_side_effects=frozenset({"compute"}),
    )
    grant = derive_effective_capability_grant(
        owner=material,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
        global_policy=gp,
    )
    assert grant is not None
    assert "compute" not in grant.allowed_side_effects
    assert grant.allowed_side_effects == ("none", "read")
    # Digest must reflect the post-intersection grant (differs from unclipped).
    unclipped = derive_effective_capability_grant(
        owner=material,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
    )
    assert unclipped is not None
    assert unclipped.allowed_side_effects == ("none", "compute", "read")
    assert grant.grant_source_digest != unclipped.grant_source_digest
    # Allowed decision carries the shrunk grant.
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="read"),
        owner_materials=owners,
        global_policy=gp,
    )
    assert decision.allowed is True
    assert decision.allowed_side_effects == ("none", "read")
    assert decision.grant_source_digest == grant.grant_source_digest


def test_grant_derivation_does_not_use_descriptor_side_effect() -> None:
    """Grant derivation never takes descriptor side effect as an input."""
    snap, binding, exposure, owners, material = _main_agent_fixture()
    # Signature / kwargs of derive_effective_capability_grant exclude descriptor.
    import inspect

    sig = inspect.signature(derive_effective_capability_grant)
    assert "descriptor" not in sig.parameters
    assert "descriptor_side_effect" not in sig.parameters
    assert "side_effect" not in sig.parameters
    # Same grant regardless of any later descriptor membership test input.
    grant = derive_effective_capability_grant(
        owner=material,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
        global_policy=GlobalPolicyView(
            policy_digest=snap.global_policy_digest,
            denied_side_effects=frozenset({"compute"}),
        ),
    )
    assert grant is not None
    assert grant.allowed_side_effects == ("none", "read")
    # evaluate with different descriptor effects still freezes the same grant.
    for effect in ("none", "read", "compute", "write_local"):
        decision = evaluate_authorization(
            snapshot=snap,
            proposal=_proposal(binding=binding, exposure=exposure, side_effect=effect),
            owner_materials=owners,
            global_policy=GlobalPolicyView(
                policy_digest=snap.global_policy_digest,
                denied_side_effects=frozenset({"compute"}),
            ),
        )
        if decision.grant_source_digest is not None:
            assert decision.allowed_side_effects == ("none", "read")
            assert decision.grant_source_digest == grant.grant_source_digest


def test_descriptor_effect_denied_by_global_after_grant_freeze() -> None:
    """Descriptor effect denied by global policy after grant freeze → global_policy_denied."""
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    gp = GlobalPolicyView(
        policy_digest=snap.global_policy_digest,
        denied_side_effects=frozenset({"compute"}),
    )
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="compute"),
        owner_materials=owners,
        global_policy=gp,
    )
    assert decision.allowed is False
    assert decision.reason_code == "global_policy_denied"
    # Grant was derived (with compute removed) and carried for audit.
    assert decision.grant_source_digest is not None
    assert "compute" not in decision.allowed_side_effects
    assert decision.allowed_side_effects == ("none", "read")
    # Non-globally-denied out-of-grant effect still maps to owner_side_effect_denied.
    decision_w = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="write_local"),
        owner_materials=owners,
        global_policy=gp,
    )
    assert decision_w.allowed is False
    assert decision_w.reason_code == "owner_side_effect_denied"


def test_owner_capability_not_declared_instruction_only() -> None:
    snap, binding, exposure, owners, _ = _skill_fixture(
        is_instruction_only=True,
        author_effects=(),
    )
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_capability_not_declared"


def test_owner_capability_not_declared_wrong_key() -> None:
    snap, binding, exposure, owners, material = _skill_fixture()
    # Material declares a different key than the exposure.
    material2 = OwnerGrantMaterial(
        owner_kind=material.owner_kind,
        owner_id=material.owner_id,
        owner_version_id=material.owner_version_id,
        policy_digest=material.policy_digest,
        author_allowed_side_effects=material.author_allowed_side_effects,
        declared_capability_keys=frozenset({"other.key"}),
    )
    owners2 = {owner_material_key(material2): material2}
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure),
        owner_materials=owners2,
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_capability_not_declared"


def test_owner_side_effect_denied_write() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="write_local"),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_side_effect_denied"
    # Grant was derived; decision may carry grant fields for audit.
    assert decision.grant_source_digest is not None
    assert "write_local" not in decision.allowed_side_effects


def test_owner_side_effect_denied_skill_compute_only() -> None:
    snap, binding, exposure, owners, _ = _skill_fixture(author_effects=("compute",))
    # Descriptor is read; skill only declared compute → still admits none via entrypoint,
    # but read is not in author lattice ∩ platform → denied.
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="read"),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_side_effect_denied"


def test_release_gate_denied_for_draft() -> None:
    # Build a synthetic grant path: main agent grant is platform-capped so draft
    # is denied at owner_side_effect before release_gate. release_gate is hit only
    # when grant somehow admits a non-gate effect. Force via skill that maps write
    # and a descriptor write_external which is outside platform → owner_side_effect.
    # To hit release_gate specifically, grant must contain the effect but release
    # gate rejects it. Plan 05 platform==release gate so this path is defensive.
    # We verify the ordered code exists and draft is never allowed.
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    for effect in ("draft", "write_local", "write_external", "unknown"):
        decision = evaluate_authorization(
            snapshot=snap,
            proposal=_proposal(binding=binding, exposure=exposure, side_effect=effect),
            owner_materials=owners,
        )
        assert decision.allowed is False
        assert decision.reason_code in {
            "owner_side_effect_denied",
            "release_gate_denied",
        }


def test_target_unavailable() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            availability_status="disabled",
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "target_unavailable"


def test_version_or_digest_drift_descriptor() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            descriptor_digest="f" * 64,  # differs from exposure.descriptor_digest
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "version_or_digest_drift"


def test_version_or_digest_drift_availability() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            availability_status="version_drift",
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "version_or_digest_drift"


def test_recursion_denied() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(
            binding=binding,
            exposure=exposure,
            nesting_depth=99,
            max_capability_depth=4,
        ),
        owner_materials=owners,
    )
    assert decision.allowed is False
    assert decision.reason_code == "recursion_denied"


def test_skill_owner_allowed_read() -> None:
    snap, binding, exposure, owners, material = _skill_fixture(
        author_effects=("read", "compute")
    )
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="read"),
        owner_materials=owners,
    )
    assert decision.allowed is True
    assert decision.reason_code == "allowed"
    # none admitted via entrypoint rule + author lattice intersection.
    assert decision.allowed_side_effects == ("none", "compute", "read")
    assert decision.owner_policy_digest == material.policy_digest


def test_skill_author_write_clipped_to_platform() -> None:
    snap, binding, exposure, owners, _ = _skill_fixture(
        author_effects=("read", "write")
    )
    grant = derive_effective_capability_grant(
        owner=owners[next(iter(owners))],
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
    )
    assert grant is not None
    assert "write_local" not in grant.allowed_side_effects
    assert grant.allowed_side_effects == ("none", "read")
    # read still allowed
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="read"),
        owner_materials=owners,
    )
    assert decision.allowed is True
    # write denied
    decision_w = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure, side_effect="write_local"),
        owner_materials=owners,
    )
    assert decision_w.allowed is False
    assert decision_w.reason_code == "owner_side_effect_denied"


def test_one_readonly_skill_does_not_restrict_other_owner() -> None:
    """One read-only Skill does not globally restrict another owner."""
    snap_a, binding_a, exposure_a, owners_a, mat_a = _skill_fixture(
        capability_key="skill.a",
        author_effects=("read",),
        package_id=UUID("00000000-0000-4000-8000-0000000000a1"),
        version_id=UUID("00000000-0000-4000-8000-0000000000a2"),
    )
    snap_b, binding_b, exposure_b, owners_b, mat_b = _skill_fixture(
        capability_key="skill.b",
        author_effects=("read", "compute"),
        package_id=UUID("00000000-0000-4000-8000-0000000000b1"),
        version_id=UUID("00000000-0000-4000-8000-0000000000b2"),
    )
    # Combined snapshot with both exposures and both owner materials.
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=MANIFEST_DIGEST,
        exposures=(exposure_a, exposure_b),
    )
    owner_refs = (
        build_owner_policy_ref(
            owner_kind=mat_a.owner_kind,
            owner_id=mat_a.owner_id,
            owner_version_id=mat_a.owner_version_id,
            content_or_policy_digest=DIGEST_B,
            allowed_side_effects=mat_a.author_allowed_side_effects,
        ),
        build_owner_policy_ref(
            owner_kind=mat_b.owner_kind,
            owner_id=mat_b.owner_id,
            owner_version_id=mat_b.owner_version_id,
            content_or_policy_digest=DIGEST_B,
            allowed_side_effects=mat_b.author_allowed_side_effects,
        ),
    )
    # Rebuild materials with matching policy digests from owner_refs.
    mat_a2 = OwnerGrantMaterial(
        owner_kind=mat_a.owner_kind,
        owner_id=mat_a.owner_id,
        owner_version_id=mat_a.owner_version_id,
        policy_digest=owner_refs[0].policy_digest,
        author_allowed_side_effects=mat_a.author_allowed_side_effects,
        declared_capability_keys=frozenset({"skill.a"}),
    )
    mat_b2 = OwnerGrantMaterial(
        owner_kind=mat_b.owner_kind,
        owner_id=mat_b.owner_id,
        owner_version_id=mat_b.owner_version_id,
        policy_digest=owner_refs[1].policy_digest,
        author_allowed_side_effects=mat_b.author_allowed_side_effects,
        declared_capability_keys=frozenset({"skill.b"}),
    )
    snap = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=owner_refs,
        run_budget_limits=normalize_run_budget_limits(),
    )
    owners = {
        owner_material_key(mat_a2): mat_a2,
        owner_material_key(mat_b2): mat_b2,
    }
    # skill.b still gets compute even though skill.a is read-only.
    decision_b = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding_b, exposure=exposure_b, side_effect="compute"),
        owner_materials=owners,
    )
    assert decision_b.allowed is True
    assert "compute" in decision_b.allowed_side_effects
    # skill.a cannot use compute (author only declared read).
    decision_a = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding_a, exposure=exposure_a, side_effect="compute"),
        owner_materials=owners,
    )
    assert decision_a.allowed is False
    assert decision_a.reason_code == "owner_side_effect_denied"


def test_broad_skill_cannot_grant_other_owner() -> None:
    """One broad Skill cannot grant another owner's capability."""
    snap_a, binding_a, exposure_a, owners_a, mat_a = _skill_fixture(
        capability_key="skill.broad",
        author_effects=("read", "compute", "write"),
        package_id=UUID("00000000-0000-4000-8000-0000000000c1"),
        version_id=UUID("00000000-0000-4000-8000-0000000000c2"),
    )
    # Attempt to authorize skill.broad using a different owner's material only.
    other = OwnerGrantMaterial(
        owner_kind="skill_version",
        owner_id=str(UUID("00000000-0000-4000-8000-0000000000d1")),
        owner_version_id=UUID("00000000-0000-4000-8000-0000000000d2"),
        policy_digest="c" * 64,
        author_allowed_side_effects=("read", "compute", "write"),
        declared_capability_keys=frozenset({"skill.broad"}),
    )
    decision = evaluate_authorization(
        snapshot=snap_a,
        proposal=_proposal(binding=binding_a, exposure=exposure_a),
        owner_materials={owner_material_key(other): other},
    )
    assert decision.allowed is False
    assert decision.reason_code == "owner_mismatch"


def test_compatible_consumer_does_not_participate_in_auth() -> None:
    """Compatible consumers are completion-evidence consumers only."""
    snap, binding, exposure, owners, material = _skill_fixture()
    # Append a consumer version id on the exposure; auth still uses owner only.
    consumer_id = UUID("00000000-0000-4000-8000-0000000000ee")
    exposure2 = build_capability_exposure_ref(
        domain_key=exposure.domain_key,
        resolved_ref=exposure.resolved_ref,
        binding_contract_digest=exposure.binding_contract_digest,
        descriptor_digest=exposure.descriptor_digest,
        owner_kind=exposure.owner_kind,
        owner_id=exposure.owner_id,
        owner_version_id=exposure.owner_version_id,
        compatible_consumer_version_ids=(consumer_id,),
    )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=MANIFEST_DIGEST,
        exposures=(exposure2,),
    )
    snap2 = build_effective_run_policy_snapshot(
        app_build_revision="development",
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=snap.owner_policy_refs,
        run_budget_limits=snap.run_budget_limits,
    )
    decision = evaluate_authorization(
        snapshot=snap2,
        proposal=_proposal(binding=binding, exposure=exposure2, side_effect="read"),
        owner_materials=owners,
    )
    assert decision.allowed is True
    # Claiming the consumer as owner fails.
    decision2 = evaluate_authorization(
        snapshot=snap2,
        proposal=_proposal(
            binding=binding,
            exposure=exposure2,
            claimed_owner_kind="skill_version",
            claimed_owner_id=str(consumer_id),
            claimed_owner_version_id=consumer_id,
        ),
        owner_materials=owners,
    )
    assert decision2.allowed is False
    assert decision2.reason_code == "owner_mismatch"


def test_grant_derived_before_descriptor_and_digest_stable() -> None:
    snap, binding, exposure, owners, material = _main_agent_fixture()
    grant = derive_effective_capability_grant(
        owner=material,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
    )
    assert grant is not None
    assert isinstance(grant, EffectiveCapabilityGrant)
    # Grant does not cover descriptor side effect — rebuild is identical.
    grant2 = derive_effective_capability_grant(
        owner=material,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
    )
    assert grant.grant_source_digest == grant2.grant_source_digest
    # Changing owner policy digest changes grant_source.
    material_changed = OwnerGrantMaterial(
        owner_kind=material.owner_kind,
        owner_id=material.owner_id,
        owner_version_id=material.owner_version_id,
        policy_digest="d" * 64,
        author_allowed_side_effects=material.author_allowed_side_effects,
    )
    grant3 = derive_effective_capability_grant(
        owner=material_changed,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
    )
    assert grant3 is not None
    assert grant3.grant_source_digest != grant.grant_source_digest


def test_decision_digest_deterministic_no_prose() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    proposal = _proposal(binding=binding, exposure=exposure)
    d1 = evaluate_authorization(
        snapshot=snap, proposal=proposal, owner_materials=owners
    )
    d2 = evaluate_authorization(
        snapshot=snap, proposal=proposal, owner_materials=owners
    )
    assert d1.decision_digest == d2.decision_digest
    assert d1 == d2
    # Recompute digest from fields.
    recomputed = compute_authorization_decision_digest(
        allowed=d1.allowed,
        reason_code=d1.reason_code,
        principal_digest=d1.principal_digest,
        entrypoint_policy_digest=d1.entrypoint_policy_digest,
        global_policy_digest=d1.global_policy_digest,
        owner_policy_digest=d1.owner_policy_digest,
        allowed_side_effects=d1.allowed_side_effects,
        grant_source_digest=d1.grant_source_digest,
        exposure_digest=d1.exposure_digest,
        effective_policy_digest=d1.effective_policy_digest,
    )
    assert recomputed == d1.decision_digest
    # No prose/user data in digests or reason codes.
    for field in (
        d1.principal_digest,
        d1.decision_digest,
        d1.grant_source_digest or "",
        d1.reason_code,
    ):
        assert "password" not in field.lower()
        assert "prompt" not in field.lower()
        assert "http" not in field.lower()


def test_decision_and_grant_frozen_forbidden_extra() -> None:
    snap, binding, exposure, owners, material = _main_agent_fixture()
    decision = evaluate_authorization(
        snapshot=snap,
        proposal=_proposal(binding=binding, exposure=exposure),
        owner_materials=owners,
    )
    with pytest.raises(ValidationError):
        AuthorizationDecision(
            **decision.model_dump(),
            extra=True,  # type: ignore[call-arg]
        )
    grant = derive_effective_capability_grant(
        owner=material,
        capability_key=binding.ref.capability_key,
        binding_contract_digest=binding.ref.binding_contract_digest,
        entrypoint_policy_digest=snap.entrypoint_policy_digest,
        global_policy_digest=snap.global_policy_digest,
    )
    assert grant is not None
    with pytest.raises(ValidationError):
        EffectiveCapabilityGrant(
            **grant.model_dump(),
            extra=True,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        decision.reason_code = "x"  # type: ignore[misc]


def test_every_side_effect_membership() -> None:
    snap, binding, exposure, owners, _ = _main_agent_fixture()
    expected = {
        "none": True,
        "compute": True,
        "read": True,
        "draft": False,
        "write_local": False,
        "write_external": False,
        "unknown": False,
    }
    for effect, should_allow in expected.items():
        decision = evaluate_authorization(
            snapshot=snap,
            proposal=_proposal(binding=binding, exposure=exposure, side_effect=effect),
            owner_materials=owners,
        )
        assert decision.allowed is should_allow, effect
        if not should_allow:
            assert decision.reason_code in {
                "owner_side_effect_denied",
                "release_gate_denied",
            }


def test_principal_digest_identity_only() -> None:
    d1 = compute_principal_digest(LOCAL_ASSISTANT_PRINCIPAL)
    d2 = compute_principal_digest(LOCAL_ASSISTANT_PRINCIPAL)
    assert d1 == d2
    assert len(d1) == 64
    other = CapabilityPrincipal(
        principal_type="service",
        principal_id="other",
        authenticated=True,
    )
    assert compute_principal_digest(other) != d1
