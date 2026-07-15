"""Plan 05 Task 1: ManifestExposureIndex + strict duplicate compatibility."""

from __future__ import annotations

import copy
from dataclasses import replace
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
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedSkillRef,
    append_skill_activation,
    create_base_run_manifest,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.policy.contracts import (  # noqa: E402
    CapabilityExposureRef,
    ManifestExposureIndex,
    build_capability_exposure_ref,
    build_manifest_exposure_index,
    compute_exposure_digest,
    compute_exposure_index_digest,
)
from app.assistant.policy.conflicts import SkillConflictIdentity  # noqa: E402
from app.assistant.policy.exposures import (  # noqa: E402
    DuplicateCapabilityDeclaration,
    ExistingExposureCompatibilityView,
    ExposureBindingInput,
    ExposureBuildError,
    ExposureOwnerIdentity,
    append_compatible_consumer,
    build_manifest_exposure_index_from_inputs,
    choose_batch_owner,
    evaluate_duplicate_capability_compatibility,
    resolve_owner_from_binding,
)
from app.assistant.skills.contracts import SkillConflictRuleV1  # noqa: E402
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402

RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000010")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000011")
PACKAGE_ID = UUID("00000000-0000-4000-8000-000000000020")
SKILL_VERSION_ID = UUID("00000000-0000-4000-8000-000000000021")
PACKAGE_ID_B = UUID("00000000-0000-4000-8000-000000000023")
SKILL_VERSION_ID_B = UUID("00000000-0000-4000-8000-000000000022")
SKILL_VERSION_ID_C = UUID("00000000-0000-4000-8000-000000000024")

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


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


def _skill(
    *,
    package_id: UUID = PACKAGE_ID,
    version_id: UUID = SKILL_VERSION_ID,
    canonical_name: str = "weekly-review",
) -> ResolvedSkillRef:
    return ResolvedSkillRef(
        package_id=package_id,
        version_id=version_id,
        canonical_name=canonical_name,
        sequence=1,
        content_digest=DIGEST_B,
        version_digest=DIGEST_C,
        requested_name_normalized=None,
        resolved_via_alias_id=None,
    )


def _resolved_binding(
    *,
    capability_key: str = "skill.search",
    target_id: UUID | None = None,
) -> ResolvedCapabilityBinding:
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
    tid = target_id or uuid4()
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


def _frozen_binding(
    *,
    capability_key: str = "skill.search",
    origin: str = "main_agent_profile",
    owner_version_id: UUID | None = PROFILE_VERSION_ID,
    resolved: ResolvedCapabilityBinding | None = None,
) -> FrozenCapabilityBinding:
    material = resolved or _resolved_binding(capability_key=capability_key)
    provenance = FrozenBindingProvenance(
        origin=origin,  # type: ignore[arg-type]
        binding_row_id=None,
        owner_version_id=owner_version_id,
        source_snapshot_digest=DIGEST_D,
    )
    return project_frozen_capability_binding(resolved=material, provenance=provenance)


def _descriptor_for(binding: FrozenCapabilityBinding) -> CapabilityDescriptor:
    resolved = binding.resolved
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        side_effect="read",
        parallel_safe=True,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=DIGEST_B,
    )
    availability = CapabilityAvailability(
        status="available",
        reason_code=None,
        compatibility_only=False,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type=resolved.capability_type,
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=resolved.capability_key,
        description="test capability",
        input_schema=copy.deepcopy(resolved.input_schema),
        output_schema=copy.deepcopy(resolved.output_schema),
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=DIGEST_C,
        executable_revision=resolved.executable_revision,
        behavior=behavior,
        availability=availability,
        completion=resolved.completion,
    )


def test_exposure_ref_forbidden_extra_and_frozen() -> None:
    binding = _frozen_binding()
    ref = build_capability_exposure_ref(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    with pytest.raises(ValidationError):
        CapabilityExposureRef(
            domain_key=ref.domain_key,
            resolved_ref=ref.resolved_ref,
            binding_contract_digest=ref.binding_contract_digest,
            descriptor_digest=ref.descriptor_digest,
            owner_kind=ref.owner_kind,
            owner_id=ref.owner_id,
            owner_version_id=ref.owner_version_id,
            compatible_consumer_version_ids=(),
            exposure_digest=ref.exposure_digest,
            extra_field="nope",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ref.domain_key = "mutated"  # type: ignore[misc]


def test_exposure_digest_is_canonical_and_stable() -> None:
    binding = _frozen_binding()
    left = compute_exposure_digest(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        compatible_consumer_version_ids=(SKILL_VERSION_ID_B, SKILL_VERSION_ID),
    )
    right = compute_exposure_digest(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        compatible_consumer_version_ids=(SKILL_VERSION_ID, SKILL_VERSION_ID_B),
    )
    assert left == right
    assert len(left) == 64
    assert left == left.lower()

    changed = compute_exposure_digest(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_D,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    assert changed != left


def test_exposure_serialization_round_trip() -> None:
    binding = _frozen_binding()
    exposure = build_capability_exposure_ref(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        compatible_consumer_version_ids=(SKILL_VERSION_ID,),
    )
    payload = exposure.model_dump(mode="json", by_alias=True)
    restored = CapabilityExposureRef.model_validate(payload)
    assert restored == exposure
    assert restored.exposure_digest == exposure.exposure_digest


def test_owner_from_binding_provenance() -> None:
    main_binding = _frozen_binding(
        origin="main_agent_profile",
        owner_version_id=PROFILE_VERSION_ID,
    )
    owner = resolve_owner_from_binding(
        main_binding,
        profile_key="general_chat",
        profile_version_id=PROFILE_VERSION_ID,
    )
    assert owner.owner_kind == "main_agent"
    assert owner.owner_id == "general_chat"
    assert owner.owner_version_id == PROFILE_VERSION_ID

    skill_binding = _frozen_binding(
        capability_key="biz.lookup",
        origin="skill_version",
        owner_version_id=SKILL_VERSION_ID,
    )
    skill_owner = resolve_owner_from_binding(
        skill_binding,
        profile_key="general_chat",
        profile_version_id=PROFILE_VERSION_ID,
        skill_package_id=PACKAGE_ID,
    )
    assert skill_owner.owner_kind == "skill_version"
    assert skill_owner.owner_id == str(PACKAGE_ID)
    assert skill_owner.owner_version_id == SKILL_VERSION_ID


def test_owner_missing_and_unsupported_origin_fail_closed() -> None:
    skill_binding = _frozen_binding(
        origin="skill_version",
        owner_version_id=None,
    )
    with pytest.raises(ExposureBuildError) as exc:
        resolve_owner_from_binding(
            skill_binding,
            profile_key="general_chat",
            profile_version_id=PROFILE_VERSION_ID,
        )
    assert exc.value.reason_code == "owner_missing"

    test_binding = _frozen_binding(origin="test", owner_version_id=PROFILE_VERSION_ID)
    with pytest.raises(ExposureBuildError) as exc2:
        resolve_owner_from_binding(
            test_binding,
            profile_key="general_chat",
            profile_version_id=PROFILE_VERSION_ID,
        )
    assert exc2.value.reason_code == "owner_mismatch"


def test_build_exposure_index_from_manifest_skill_owner() -> None:
    binding = _frozen_binding(capability_key="skill.search")
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        current=base,
        skill=_skill(),
        capabilities=(binding.ref,),
    )
    skill_binding = _frozen_binding(
        capability_key="skill.search",
        origin="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        resolved=binding.resolved,
    )
    descriptor = _descriptor_for(skill_binding)
    owner = ExposureOwnerIdentity(
        owner_kind="skill_version",
        owner_id=str(PACKAGE_ID),
        owner_version_id=SKILL_VERSION_ID,
    )
    index = build_manifest_exposure_index_from_inputs(
        manifest=child,
        binding_inputs=(
            ExposureBindingInput(
                binding=skill_binding,
                descriptor=descriptor,
                owner=owner,
                max_skill_calls=16,
                max_same_read_calls=3,
                requires_terminal_output=False,
                terminal_text_allowed=False,
            ),
        ),
        profile_key="general_chat",
    )
    assert isinstance(index, ManifestExposureIndex)
    assert index.manifest_revision == child.revision
    assert index.manifest_digest == child.manifest_digest
    assert len(index.exposures) == 1
    assert index.exposures[0].owner_kind == "skill_version"
    assert index.exposures[0].owner_id == str(PACKAGE_ID)
    assert index.exposure_index_digest == compute_exposure_index_digest(
        manifest_revision=index.manifest_revision,
        manifest_digest=index.manifest_digest,
        exposures=index.exposures,
    )


def test_exposure_index_canonical_ordering() -> None:
    keys = ["zeta.tool", "alpha.tool", "middle.tool"]
    bindings = [_frozen_binding(capability_key=key) for key in keys]
    refs = tuple(b.ref for b in bindings)
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        current=base,
        skill=_skill(),
        capabilities=refs,
    )
    inputs = []
    for binding in bindings:
        skill_binding = _frozen_binding(
            capability_key=binding.ref.capability_key,
            origin="skill_version",
            owner_version_id=SKILL_VERSION_ID,
            resolved=binding.resolved,
        )
        inputs.append(
            ExposureBindingInput(
                binding=skill_binding,
                descriptor=_descriptor_for(skill_binding),
                owner=ExposureOwnerIdentity(
                    owner_kind="skill_version",
                    owner_id=str(PACKAGE_ID),
                    owner_version_id=SKILL_VERSION_ID,
                ),
            )
        )
    inputs = [inputs[1], inputs[2], inputs[0]]
    index = build_manifest_exposure_index_from_inputs(
        manifest=child,
        binding_inputs=inputs,
        profile_key="general_chat",
    )
    ordered_keys = [item.domain_key for item in index.exposures]
    assert ordered_keys == sorted(keys, key=lambda k: k.encode("utf-8"))


def test_missing_extra_and_ambiguous_owners() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        current=base,
        skill=_skill(),
        capabilities=(binding.ref,),
    )
    skill_binding = _frozen_binding(
        capability_key="biz.lookup",
        origin="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        resolved=binding.resolved,
    )
    descriptor = _descriptor_for(skill_binding)
    owner = ExposureOwnerIdentity(
        owner_kind="skill_version",
        owner_id=str(PACKAGE_ID),
        owner_version_id=SKILL_VERSION_ID,
    )
    good = ExposureBindingInput(
        binding=skill_binding,
        descriptor=descriptor,
        owner=owner,
    )

    with pytest.raises(ExposureBuildError) as missing:
        build_manifest_exposure_index_from_inputs(
            manifest=child,
            binding_inputs=(),
            profile_key="general_chat",
        )
    assert missing.value.reason_code == "exposure_missing"

    extra_binding = _frozen_binding(capability_key="other.tool")
    extra_skill_binding = _frozen_binding(
        capability_key="other.tool",
        origin="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        resolved=extra_binding.resolved,
    )
    extra = ExposureBindingInput(
        binding=extra_skill_binding,
        descriptor=_descriptor_for(extra_skill_binding),
        owner=owner,
    )
    with pytest.raises(ExposureBuildError) as extra_exc:
        build_manifest_exposure_index_from_inputs(
            manifest=child,
            binding_inputs=(good, extra),
            profile_key="general_chat",
        )
    assert extra_exc.value.reason_code == "exposure_extra"

    with pytest.raises(ExposureBuildError) as amb:
        build_manifest_exposure_index_from_inputs(
            manifest=child,
            binding_inputs=(good, good),
            profile_key="general_chat",
        )
    assert amb.value.reason_code == "exposure_ambiguous"

    bad_owner = ExposureBindingInput(
        binding=skill_binding,
        descriptor=descriptor,
        owner=ExposureOwnerIdentity(
            owner_kind="skill_version",
            owner_id=str(PACKAGE_ID_B),
            owner_version_id=SKILL_VERSION_ID,
        ),
    )
    with pytest.raises(ExposureBuildError) as owner_exc:
        build_manifest_exposure_index_from_inputs(
            manifest=child,
            binding_inputs=(bad_owner,),
            profile_key="general_chat",
        )
    assert owner_exc.value.reason_code == "owner_mismatch"


def test_stale_descriptor_rejected() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        current=base,
        skill=_skill(),
        capabilities=(binding.ref,),
    )
    skill_binding = _frozen_binding(
        capability_key="biz.lookup",
        origin="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        resolved=binding.resolved,
    )
    descriptor = _descriptor_for(skill_binding)
    stale = descriptor.model_copy(update={"binding_contract_digest": DIGEST_A})
    with pytest.raises(ExposureBuildError) as exc:
        build_manifest_exposure_index_from_inputs(
            manifest=child,
            binding_inputs=(
                ExposureBindingInput(
                    binding=skill_binding,
                    descriptor=stale,
                    owner=ExposureOwnerIdentity(
                        owner_kind="skill_version",
                        owner_id=str(PACKAGE_ID),
                        owner_version_id=SKILL_VERSION_ID,
                    ),
                ),
            ),
            profile_key="general_chat",
        )
    assert exc.value.reason_code == "version_or_digest_drift"


def test_batch_owner_choice_deterministic() -> None:
    assert (
        choose_batch_owner(
            existing_owner_version_id=SKILL_VERSION_ID,
            candidates=(("zeta", SKILL_VERSION_ID_B), ("alpha", SKILL_VERSION_ID_C)),
        )
        == SKILL_VERSION_ID
    )
    chosen = choose_batch_owner(
        existing_owner_version_id=None,
        candidates=(
            ("zeta-skill", SKILL_VERSION_ID),
            ("alpha-skill", SKILL_VERSION_ID_B),
            ("alpha-skill", SKILL_VERSION_ID_C),
        ),
    )
    expected = min(
        (SKILL_VERSION_ID_B, SKILL_VERSION_ID_C),
        key=lambda item: item.bytes,
    )
    assert chosen == expected
    chosen_rev = choose_batch_owner(
        existing_owner_version_id=None,
        candidates=(
            ("alpha-skill", SKILL_VERSION_ID_C),
            ("zeta-skill", SKILL_VERSION_ID),
            ("alpha-skill", SKILL_VERSION_ID_B),
        ),
    )
    assert chosen_rev == chosen


def _existing_view(binding: FrozenCapabilityBinding, **overrides: Any) -> ExistingExposureCompatibilityView:
    payload = {
        "domain_key": binding.ref.capability_key,
        "resolved_ref": binding.ref,
        "binding_contract_digest": binding.ref.binding_contract_digest,
        "descriptor_digest": DIGEST_C,
        "side_effect": "read",
        "input_schema_digest": binding.ref.input_schema_digest,
        "output_schema_digest": binding.ref.output_schema_digest,
        "dependency_closure_digest": binding.ref.dependency_closure_digest,
        "resolution_digest": binding.ref.resolution_digest,
        "executable_revision": binding.resolved.executable_revision,
        "timeout_mode": "none",
        "timeout_seconds": None,
        "interrupt_mode": "none",
        "parallel_safe": True,
        "terminal_output": False,
        "needs_followup": True,
        "followup_hint": None,
        "max_skill_calls": 16,
        "max_same_read_calls": 3,
        "requires_terminal_output": False,
        "terminal_text_allowed": False,
        "grant_admits_side_effect": True,
        "conflict_rules": (),
        "owner_version_id": SKILL_VERSION_ID,
        "compatible_consumer_version_ids": (),
        "owner_canonical_name": "weekly-review",
        "owner_aliases": (),
    }
    payload.update(overrides)
    return ExistingExposureCompatibilityView(**payload)


def _candidate(binding: FrozenCapabilityBinding, **overrides: Any) -> DuplicateCapabilityDeclaration:
    payload = {
        "domain_key": binding.ref.capability_key,
        "resolved_ref": binding.ref,
        "binding_contract_digest": binding.ref.binding_contract_digest,
        "descriptor_digest": DIGEST_C,
        "side_effect": "read",
        "input_schema_digest": binding.ref.input_schema_digest,
        "output_schema_digest": binding.ref.output_schema_digest,
        "dependency_closure_digest": binding.ref.dependency_closure_digest,
        "resolution_digest": binding.ref.resolution_digest,
        "executable_revision": binding.resolved.executable_revision,
        "timeout_mode": "none",
        "timeout_seconds": None,
        "interrupt_mode": "none",
        "parallel_safe": True,
        "terminal_output": False,
        "needs_followup": True,
        "followup_hint": None,
        "max_skill_calls": 16,
        "max_same_read_calls": 3,
        "requires_terminal_output": False,
        "terminal_text_allowed": False,
        "grant_admits_side_effect": True,
        "conflict_rules": (),
        "candidate_skill_version_id": SKILL_VERSION_ID_B,
        "candidate_canonical_name": "other-skill",
        "candidate_aliases": (),
    }
    payload.update(overrides)
    return DuplicateCapabilityDeclaration(**payload)


def test_strict_duplicate_compatibility_accepts_identical() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    existing = _existing_view(binding)
    candidate = _candidate(binding)
    status, consumers = evaluate_duplicate_capability_compatibility(
        existing=existing,
        candidate=candidate,
    )
    assert status == "compatible"
    assert consumers == (SKILL_VERSION_ID_B,)

    existing2 = replace(existing, compatible_consumer_version_ids=consumers)
    status2, consumers2 = evaluate_duplicate_capability_compatibility(
        existing=existing2,
        candidate=_candidate(binding, candidate_skill_version_id=SKILL_VERSION_ID_C),
    )
    assert status2 == "compatible"
    assert consumers2 == tuple(
        sorted((SKILL_VERSION_ID_B, SKILL_VERSION_ID_C), key=lambda item: item.bytes)
    )


def test_strict_duplicate_incompatible_policy() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    existing = _existing_view(binding)
    candidate = _candidate(binding, max_skill_calls=8)
    with pytest.raises(ExposureBuildError) as exc:
        evaluate_duplicate_capability_compatibility(existing=existing, candidate=candidate)
    assert exc.value.reason_code == "duplicate_capability_policy_conflict"


def test_append_compatible_consumer_preserves_owner() -> None:
    binding = _frozen_binding()
    exposure = build_capability_exposure_ref(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="skill_version",
        owner_id=str(PACKAGE_ID),
        owner_version_id=SKILL_VERSION_ID,
    )
    updated = append_compatible_consumer(exposure, consumer_version_id=SKILL_VERSION_ID_B)
    assert updated.owner_version_id == SKILL_VERSION_ID
    assert updated.compatible_consumer_version_ids == (SKILL_VERSION_ID_B,)
    assert updated.exposure_digest != exposure.exposure_digest


def test_manifest_exposure_index_rejects_unsorted() -> None:
    binding_a = _frozen_binding(capability_key="a.tool")
    binding_z = _frozen_binding(capability_key="z.tool")
    exp_a = build_capability_exposure_ref(
        domain_key="a.tool",
        resolved_ref=binding_a.ref,
        binding_contract_digest=binding_a.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    exp_z = build_capability_exposure_ref(
        domain_key="z.tool",
        resolved_ref=binding_z.ref,
        binding_contract_digest=binding_z.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    digest = compute_exposure_index_digest(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(exp_z, exp_a),
    )
    with pytest.raises(ValidationError):
        ManifestExposureIndex(
            manifest_revision=1,
            manifest_digest=DIGEST_A,
            exposures=(exp_z, exp_a),
            exposure_index_digest=digest,
        )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(exp_z, exp_a),
    )
    assert [item.domain_key for item in index.exposures] == ["a.tool", "z.tool"]


def test_candidate_excludes_existing_owner_is_conflict() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    existing = _existing_view(binding, owner_canonical_name="weekly-review")
    candidate = _candidate(
        binding,
        candidate_canonical_name="other-skill",
        conflict_rules=(
            SkillConflictRuleV1(kind="excludes", target_skill="weekly-review"),
        ),
    )
    with pytest.raises(ExposureBuildError) as exc:
        evaluate_duplicate_capability_compatibility(existing=existing, candidate=candidate)
    assert exc.value.reason_code == "duplicate_capability_policy_conflict"


def test_existing_excludes_candidate_is_conflict() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    existing = _existing_view(
        binding,
        owner_canonical_name="weekly-review",
        conflict_rules=(
            SkillConflictRuleV1(kind="excludes", target_skill="other-skill"),
        ),
    )
    candidate = _candidate(binding, candidate_canonical_name="other-skill")
    with pytest.raises(ExposureBuildError) as exc:
        evaluate_duplicate_capability_compatibility(existing=existing, candidate=candidate)
    assert exc.value.reason_code == "duplicate_capability_policy_conflict"


def test_alias_resolved_excludes_is_conflict() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    existing = _existing_view(
        binding,
        owner_canonical_name="weekly-review",
        owner_aliases=("weekly",),
    )
    candidate = _candidate(
        binding,
        candidate_canonical_name="other-skill",
        conflict_rules=(
            # Author used alias form; catalog resolves to weekly-review.
            SkillConflictRuleV1(kind="excludes", target_skill="weekly"),
        ),
    )
    catalog = (
        SkillConflictIdentity(
            canonical_name="weekly-review",
            version_id=SKILL_VERSION_ID,
            aliases=("weekly",),
        ),
        SkillConflictIdentity(
            canonical_name="other-skill",
            version_id=SKILL_VERSION_ID_B,
        ),
    )
    with pytest.raises(ExposureBuildError) as exc:
        evaluate_duplicate_capability_compatibility(
            existing=existing,
            candidate=candidate,
            catalog_skills=catalog,
        )
    assert exc.value.reason_code == "duplicate_capability_policy_conflict"


def test_exclusive_group_strip_clash_is_conflict() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    existing = _existing_view(
        binding,
        owner_canonical_name="weekly-review",
        conflict_rules=(
            SkillConflictRuleV1(kind="exclusive_group", group="  review-family  "),
        ),
    )
    candidate = _candidate(
        binding,
        candidate_canonical_name="other-skill",
        conflict_rules=(
            SkillConflictRuleV1(kind="exclusive_group", group="review-family"),
        ),
    )
    with pytest.raises(ExposureBuildError) as exc:
        evaluate_duplicate_capability_compatibility(existing=existing, candidate=candidate)
    assert exc.value.reason_code == "duplicate_capability_policy_conflict"


def test_invalid_skill_owner_id_uuid_raises_exposure_build_error() -> None:
    binding = _frozen_binding(capability_key="biz.lookup")
    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    child = append_skill_activation(
        current=base,
        skill=_skill(),
        capabilities=(binding.ref,),
    )
    skill_binding = _frozen_binding(
        capability_key="biz.lookup",
        origin="skill_version",
        owner_version_id=SKILL_VERSION_ID,
        resolved=binding.resolved,
    )
    with pytest.raises(ExposureBuildError) as exc:
        build_manifest_exposure_index_from_inputs(
            manifest=child,
            binding_inputs=(
                ExposureBindingInput(
                    binding=skill_binding,
                    descriptor=_descriptor_for(skill_binding),
                    owner=ExposureOwnerIdentity(
                        owner_kind="skill_version",
                        owner_id="not-a-uuid",
                        owner_version_id=SKILL_VERSION_ID,
                    ),
                ),
            ),
            profile_key="general_chat",
        )
    assert exc.value.reason_code == "owner_mismatch"


def test_exposure_index_source_mutation_isolation() -> None:
    """Mutating source lists/dicts after build must not alter digests or frozen fields."""
    binding_a = _frozen_binding(capability_key="a.tool")
    binding_z = _frozen_binding(capability_key="z.tool")
    consumers = [SKILL_VERSION_ID_B]
    exp_a = build_capability_exposure_ref(
        domain_key="a.tool",
        resolved_ref=binding_a.ref,
        binding_contract_digest=binding_a.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="skill_version",
        owner_id=str(PACKAGE_ID),
        owner_version_id=SKILL_VERSION_ID,
        compatible_consumer_version_ids=consumers,
    )
    exp_z = build_capability_exposure_ref(
        domain_key="z.tool",
        resolved_ref=binding_z.ref,
        binding_contract_digest=binding_z.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
    )
    source_exposures = [exp_z, exp_a]
    digest_before = compute_exposure_index_digest(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=source_exposures,
    )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=source_exposures,
    )
    # Mutate source list and consumer list after build.
    source_exposures.clear()
    source_exposures.append(exp_a)
    consumers.append(SKILL_VERSION_ID_C)
    assert index.exposure_index_digest == digest_before
    assert [item.domain_key for item in index.exposures] == ["a.tool", "z.tool"]
    assert index.exposures[0].compatible_consumer_version_ids == (SKILL_VERSION_ID_B,)
    assert index.exposure_index_digest == compute_exposure_index_digest(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=index.exposures,
    )
