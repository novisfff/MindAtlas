"""Main Agent runtime admission and Assistant Run composition.

Plan 2 Task 9: live admission is Main-Agent-only. No configuration, mode, or
runtime failure may select Legacy. Provider/worker errors raise into the Run
state machine on the same durable Run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.domain.contracts import (
    ModelRef,
    ProviderRef,
    ResolvedMainAgentRef,
    ResolvedRunManifestRevision,
    compute_manifest_digest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.main_agent.authorization import (
    LOCAL_ASSISTANT_PRINCIPAL,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
)
from app.assistant.main_agent.control_capabilities import (
    MAIN_AGENT_CONTROL_KEYS,
    build_all_main_agent_control_bindings,
    control_capability_refs,
)
from app.assistant.main_agent.events import MainAgentEventAdapter
from app.assistant.main_agent.model_eligibility import (
    ADAPTER_UNAVAILABLE,
    CREDENTIAL_REVISION_DRIFT,
    CurrentProbeView,
    FrozenModelIdentity,
    MODEL_BINDING_MISSING,
    MODEL_INELIGIBLE,
    MODEL_TYPE_UNSUPPORTED,
    ModelEligibilityError,
    ModelEligibilityReport,
    PROBE_MISSING,
    evaluate_probe_eligibility,
    probe_view_from_row,
    recheck_identity_before_decrypt,
    required_capability_keys_for_profile,
)
from app.assistant.main_agent.prompt_builder import (
    MainAgentPromptBuilder,
    resolve_prompt_budget_limits,
)
from app.assistant.provider_loop.adapters.openai_chat import (
    ADAPTER_KEY as OPENAI_ADAPTER_KEY,
    DEFAULT_ADAPTER_REVISION,
)
from app.assistant.provider_loop.aliases import OPENAI_CHAT_PROVIDER_PROTOCOL
from app.assistant.provider_loop.contracts import (
    NoOpManifestEffectLifecyclePort,
    ProviderAdapter,
    ProviderGenerationOptions,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderLoopResult,
    ProviderToolChoice,
    create_execution_scope,
)
from app.assistant.provider_loop.loop import ProviderAgentLoop
from app.assistant.provider_loop.probe import PROBE_CONTRACT_VERSION
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
)
from app.assistant.skills.schemas import (
    MainAgentProfileSnapshotV2,
    ModelRequirementsV1,
    ReadableMainAgentProfileSnapshot,
    parse_main_agent_profile_snapshot_for_read,
    require_production_profile_v2,
)
from app.config import get_settings

logger = logging.getLogger(__name__)

MainAgentAdmissionMode = Literal["off", "shadow", "read_only"]

RuntimeKind = Literal["main_agent"]
ExecutionKind = Literal["production", "evaluation"]

# Safe admission reason codes
MODE_OFF = "mode_off"
MODE_SHADOW_PRODUCTION = "mode_shadow_production"
PROFILE_UNAVAILABLE = "profile_unavailable"
PROFILE_DISABLED = "profile_disabled"
PROFILE_UNPUBLISHED = "profile_unpublished"
ENTRYPOINT_UNSUPPORTED = "entrypoint_unsupported"
CONTROL_UNSUPPORTED = "control_unsupported"
CONTROL_MISSING = "control_missing"
BUDGET_INVALID = "budget_invalid"
CATALOG_UNAVAILABLE = "catalog_unavailable"
ADAPTER_UNAVAILABLE_BEFORE_REQUEST = "adapter_unavailable_before_request"
ADAPTER_FAILURE_AFTER_REQUEST = "adapter_failure_after_request"
CANCELLED = "cancelled"
MAIN_AGENT_COMPLETED = "main_agent_completed"
MAIN_AGENT_FAILED = "main_agent_failed"
NOOP_LIFECYCLE_REJECTED = "noop_lifecycle_rejected"

# Stable policy / budget / completion / recursion codes that may surface as
# stop_reason or SafeProviderError.semantic_code after Provider request start.
# Exact membership promotes them onto AssistantRuntimeResult.reason_code;
# unknown codes fail closed to MAIN_AGENT_FAILED.
#
# §5.4 codes are inlined (not imported from app.assistant.policy.contracts) to
# avoid a circular import: policy.contracts → main_agent.authorization →
# main_agent.__init__ → main_agent.service → policy.contracts.
_STABLE_FAILED_REASON_CODES: frozenset[str] = frozenset(
    {
        # Plan 05 §5.4 pure authorization deny codes (exclude "allowed").
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
        # Recursion / cycle denials.
        "agent_cycle_denied",
        "main_agent_restart_denied",
        # Generic / gateway-aligned policy deny surface.
        "policy_denied",
        "capability_denied",
        # Budget exhaustion family.
        "budget_exhausted",
        "budget_exhausted_total_calls",
        "budget_exhausted_owner_calls",
        "budget_exhausted_parallel",
        "budget_exhausted_read_signature",
        "budget_exhausted_owner_read_signature",
        "budget_exhausted_deadline",
        "budget_exhausted_provider_rounds",
        "budget_exhausted_completion_tokens",
        "budget_exhausted_prompt_tokens",
        "budget_exhausted_capability_depth",
        "budget_exhausted_agent_depth",
        "budget_exhausted_main_agent_cycles",
        "budget_exhausted_completion_followups",
        "budget_exhausted_active_skills",
        "budget_exhausted_with_obligations",
        # Completion / obligation codes.
        "skill_completion_unsatisfiable",
        "completion_followup_limit",
        "obligations_pending_at_finalization",
        "pending_obligation",
        "terminal_text_missing",
        "skill_terminal_output_pending",
        "capability_followup_pending",
        "artifact_pending",
        "approval_pending",
        "user_input_pending",
        "reconciliation_pending",
        "waiting_without_obligation",
        "completion_evidence_invalid",
        "policy_state_protocol_error",
        "obligation_state_protocol_error",
        # Provider-loop / reservation surface codes that must not collapse
        # into main_agent_failed when they stop a Run after request start.
        "classification_changed",
        "budget_reservation_error",
        "reservation_not_found",
        "reservation_state_invalid",
        "arguments_digest_mismatch",
        "duplicate_call_id",
        "owner_limits_missing",
    }
)


class MainAgentAdmissionError(ValueError):
    """Preflight failure with a safe reason code (no secrets / exception text)."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class AssistantRuntimeRequest:
    run_id: UUID
    conversation_id: UUID
    user_text: str
    locale: str
    stream_output: bool = True
    l1_summary: str = ""
    l0_messages: tuple[Mapping[str, str], ...] = ()
    execution_kind: ExecutionKind = "production"
    cancel_checker: Callable[[], bool] | None = None
    # Worker/API may provide the run-scoped durable Workflow port. Keeping this
    # explicit prevents the production composition path from silently dropping
    # staged durable pauses.
    durable_workflow: Any | None = None


@dataclass(frozen=True)
class AssistantRuntimeResult:
    runtime: RuntimeKind
    status: Literal["completed", "failed", "cancelled"]
    final_text: str
    reason_code: str | None = None
    write_message: bool = True
    write_l1: bool = True
    write_l2: bool = False
    write_title: bool = True
    skill_summaries: tuple[dict[str, Any], ...] = ()
    tool_summaries: tuple[dict[str, Any], ...] = ()


class AssistantRuntimeRunner(Protocol):
    def run(self, request: AssistantRuntimeRequest) -> AssistantRuntimeResult: ...


@dataclass(frozen=True)
class AdmissionContext:
    mode: MainAgentAdmissionMode
    execution_kind: ExecutionKind
    profile: AssistantMainAgentProfile
    profile_version: AssistantMainAgentProfileVersion
    snapshot: MainAgentProfileSnapshotV2
    main_agent_ref: ResolvedMainAgentRef
    control_keys: tuple[str, ...]
    frozen_model: FrozenModelIdentity
    provider_ref: ProviderRef
    model_ref: ModelRef
    eligibility: ModelEligibilityReport
    effective_policy_digest: str
    probe_diagnostics: ModelEligibilityReport | None = None


@dataclass
class MainAgentRunState:
    """Process-local Run state (non-durable; Plan 06 owns durability)."""

    run_id: UUID
    conversation_id: UUID
    manifest: ResolvedRunManifestRevision
    applied_skill_version_ids: set[UUID] = field(default_factory=set)
    final_text: str = ""
    status: str = "running"
    # Plan 05 process-local policy bundle (None when ports were fully injected).
    policy_runtime: Any | None = None
    stop_reason: str | None = None


def compute_main_agent_effective_policy_digest(
    *,
    profile_content_digest: str,
    ceiling_revision: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING.revision,
    ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "main_agent_effective_policy",
            "ceilingRevision": ceiling_revision,
            "ceilingDigest": ceiling_digest,
            "profileContentDigest": profile_content_digest,
            "entrypoint": "main_agent",
            "effectCeilingKey": MAIN_AGENT_READ_ONLY_EFFECT_CEILING.ceiling_key,
        }
    )


def select_runtime_for_mode(
    *,
    mode: MainAgentAdmissionMode | str,
    execution_kind: ExecutionKind = "production",
) -> tuple[RuntimeKind | None, str | None]:
    """Return (runtime_to_try, reason) without constructing services.

    Plan 2 Task 9: never returns legacy. off / shadow-production refuse construction
    with a typed reason; only main_agent is constructible.
    """
    del execution_kind  # reserved for evaluation shadow diagnostics
    normalized = str(mode or "off").strip().lower()
    if normalized == "off":
        return None, MODE_OFF
    if normalized == "shadow":
        # Shadow no longer routes to legacy; production shadow is refused.
        return None, MODE_SHADOW_PRODUCTION
    if normalized == "read_only":
        return "main_agent", None
    return None, MODE_OFF


def should_construct_main_agent(
    *,
    mode: MainAgentAdmissionMode | str,
    execution_kind: ExecutionKind = "production",
) -> bool:
    runtime, _ = select_runtime_for_mode(mode=mode, execution_kind=execution_kind)
    return runtime == "main_agent"


def _credential_config_digest(*, base_url: str, runtime_revision: int) -> str:
    from app.assistant.runtime.closure import credential_config_digest

    return credential_config_digest(
        base_url=base_url, runtime_revision=runtime_revision
    )


def load_default_published_profile(
    db: Session,
) -> tuple[AssistantMainAgentProfile, AssistantMainAgentProfileVersion, MainAgentProfileSnapshotV2]:
    """Load the default published Main Agent profile for production admission.

    Plan 2: production admit is V2-exclusive. V1 snapshots remain readable for
    historical display only and must fail closed here as PROFILE_UNAVAILABLE.
    """
    profile = (
        db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.is_default.is_(True))
        .one_or_none()
    )
    if profile is None:
        raise MainAgentAdmissionError(PROFILE_UNAVAILABLE)
    if not bool(profile.runtime_enabled):
        raise MainAgentAdmissionError(PROFILE_DISABLED)
    if profile.published_version_id is None:
        raise MainAgentAdmissionError(PROFILE_UNPUBLISHED)
    version = db.get(AssistantMainAgentProfileVersion, profile.published_version_id)
    if version is None or version.profile_id != profile.id:
        raise MainAgentAdmissionError(PROFILE_UNPUBLISHED)
    if str(version.version_source) != "publish":
        raise MainAgentAdmissionError(PROFILE_UNPUBLISHED)
    try:
        parsed = parse_main_agent_profile_snapshot_for_read(version.snapshot or {})
        snapshot = require_production_profile_v2(parsed)
    except Exception as exc:
        raise MainAgentAdmissionError(PROFILE_UNAVAILABLE) from exc
    recomputed = snapshot.content_digest()
    if recomputed != str(version.content_digest or ""):
        raise MainAgentAdmissionError(PROFILE_UNAVAILABLE)
    return profile, version, snapshot


def validate_profile_for_assistant_chat(
    snapshot: ReadableMainAgentProfileSnapshot,
) -> tuple[str, ...]:
    """Validate shared profile fields used by assistant_chat admission.

    Accepts V1 or V2 for unit/historical callers that only exercise shared fields
    (supported_entrypoints, control_capability_keys). Production admit always
    supplies V2 via load_default_published_profile.
    """
    if "assistant_chat" not in set(snapshot.supported_entrypoints):
        raise MainAgentAdmissionError(ENTRYPOINT_UNSUPPORTED)
    keys = tuple(snapshot.control_capability_keys or ())
    required = set(MAIN_AGENT_CONTROL_KEYS)
    present = set(keys)
    if not required.issubset(present):
        raise MainAgentAdmissionError(CONTROL_MISSING)
    unknown = present - required
    if unknown:
        raise MainAgentAdmissionError(CONTROL_UNSUPPORTED)
    # Stable order = MAIN_AGENT_CONTROL_KEYS order
    return tuple(key for key in MAIN_AGENT_CONTROL_KEYS if key in present)


def resolve_assistant_model_identity(
    db: Session,
    *,
    requirements: ModelRequirementsV1 | None,
    app_build_revision: str,
    adapter_key: str = OPENAI_ADAPTER_KEY,
    adapter_revision: str = DEFAULT_ADAPTER_REVISION,
    provider_protocol: str = OPENAI_CHAT_PROVIDER_PROTOCOL,
) -> tuple[FrozenModelIdentity, ProviderRef, ModelRef, ModelEligibilityReport, CurrentProbeView | None]:
    """Resolve binding + current probe without decrypting the API key."""
    from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel, AiModelCapabilityProbe
    from app.assistant.provider_loop.probe import build_model_config_digest

    binding = (
        db.query(AiComponentBinding)
        .filter(AiComponentBinding.component == "assistant")
        .one_or_none()
    )
    if binding is None or binding.llm_model_id is None:
        raise MainAgentAdmissionError(MODEL_BINDING_MISSING)

    model = db.get(AiModel, binding.llm_model_id)
    if model is None:
        raise MainAgentAdmissionError(MODEL_BINDING_MISSING)
    if str(model.model_type or "") != "llm":
        raise MainAgentAdmissionError(MODEL_TYPE_UNSUPPORTED)
    credential = db.get(AiCredential, model.credential_id)
    if credential is None:
        raise MainAgentAdmissionError(MODEL_BINDING_MISSING)

    # Probe-aligned config digest (includes protocol/adapter/build; secret-free).
    try:
        from app.assistant.provider_loop.probe import build_endpoint_identity

        base_url_norm = str(credential.base_url or "").strip()
        endpoint = build_endpoint_identity(base_url_norm)
        expected_probe_config_digest = build_model_config_digest(
            model_id=model.id,
            model_name=str(model.name or ""),
            model_type="llm",
            model_runtime_revision=int(model.runtime_revision or 1),
            credential_id=credential.id,
            credential_runtime_revision=int(credential.runtime_revision or 1),
            endpoint_identity=endpoint,
            adapter_key=adapter_key,
            adapter_revision=adapter_revision,
            app_build_revision=app_build_revision,
            provider_protocol=provider_protocol,
            probe_contract_version=PROBE_CONTRACT_VERSION,
        )
    except Exception as exc:
        raise MainAgentAdmissionError(ADAPTER_UNAVAILABLE) from exc

    provider_ref = create_provider_ref(
        provider_protocol=provider_protocol,
        provider_config_id=credential.id,
        provider_runtime_revision=int(credential.runtime_revision or 1),
        provider_config_digest=_credential_config_digest(
            base_url=str(credential.base_url or ""),
            runtime_revision=int(credential.runtime_revision or 1),
        ),
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        protocol_revision="1",
        app_build_revision=app_build_revision,
    )

    probe_row: AiModelCapabilityProbe | None = None
    probe_view: CurrentProbeView | None = None
    if model.current_capability_probe_id is not None:
        probe_row = db.get(AiModelCapabilityProbe, model.current_capability_probe_id)
    if probe_row is not None:
        is_current = model.current_capability_probe_id == probe_row.id
        is_stale = (
            str(probe_row.model_config_digest or "") != str(expected_probe_config_digest)
        )
        probe_view = probe_view_from_row(
            probe_id=probe_row.id,
            probe_contract_version=int(probe_row.probe_contract_version),
            adapter_key=str(probe_row.adapter_key),
            adapter_revision=str(probe_row.adapter_revision),
            model_config_digest=str(probe_row.model_config_digest),
            status=str(probe_row.status),
            capabilities=probe_row.capabilities or {},
            probe_digest=str(probe_row.probe_digest),
            is_current=bool(is_current),
            is_stale_for_current_config=bool(is_stale),
        )

    required = required_capability_keys_for_profile(requirements)
    eligibility = evaluate_probe_eligibility(
        probe=probe_view,
        expected_adapter_key=adapter_key,
        expected_adapter_revision=adapter_revision,
        expected_model_config_digest=expected_probe_config_digest,
        required_capabilities=required,
    )
    if not eligibility.eligible:
        raise MainAgentAdmissionError(eligibility.reason_code or MODEL_INELIGIBLE)

    assert probe_view is not None and eligibility.probe_id is not None
    frozen = FrozenModelIdentity(
        model_id=model.id,
        model_name=str(model.name or ""),
        model_type="llm",
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=credential.id,
        credential_runtime_revision=int(credential.runtime_revision or 1),
        credential_config_digest=_credential_config_digest(
            base_url=str(credential.base_url or ""),
            runtime_revision=int(credential.runtime_revision or 1),
        ),
        # Store probe-aligned config digest used for eligibility.
        model_config_digest=str(expected_probe_config_digest),
        provider_ref_digest=provider_ref.provider_ref_digest,
        capability_probe_id=eligibility.probe_id,
        capability_probe_digest=str(eligibility.probe_digest or ""),
    )
    model_ref = create_model_ref(
        model_id=frozen.model_id,
        model_name=frozen.model_name,
        model_type=frozen.model_type,
        model_runtime_revision=frozen.model_runtime_revision,
        credential_id=frozen.credential_id,
        credential_runtime_revision=frozen.credential_runtime_revision,
        credential_config_digest=frozen.credential_config_digest,
        model_config_digest=frozen.model_config_digest,
        provider_ref_digest=provider_ref.provider_ref_digest,
        capability_probe_id=frozen.capability_probe_id,
        capability_probe_digest=frozen.capability_probe_digest,
    )
    return frozen, provider_ref, model_ref, eligibility, probe_view


def admit_main_agent(
    db: Session,
    *,
    mode: MainAgentAdmissionMode | str,
    execution_kind: ExecutionKind = "production",
    app_build_revision: str | None = None,
) -> AdmissionContext:
    """Full preflight before first Provider request. Does not decrypt credentials."""
    runtime, reason = select_runtime_for_mode(mode=mode, execution_kind=execution_kind)
    if runtime != "main_agent":
        raise MainAgentAdmissionError(reason or MODE_OFF)

    settings = get_settings()
    build_rev = (app_build_revision or settings.app_build_revision or "").strip()
    if not build_rev:
        raise MainAgentAdmissionError(ADAPTER_UNAVAILABLE)

    profile, version, snapshot = load_default_published_profile(db)
    control_keys = validate_profile_for_assistant_chat(snapshot)
    try:
        resolve_prompt_budget_limits(profile=snapshot, caps=None)
    except Exception as exc:
        raise MainAgentAdmissionError(BUDGET_INVALID) from exc

    frozen, provider_ref, model_ref, eligibility, _probe = resolve_assistant_model_identity(
        db,
        requirements=snapshot.model_requirements,
        app_build_revision=build_rev,
    )
    policy_digest = compute_main_agent_effective_policy_digest(
        profile_content_digest=str(version.content_digest),
    )
    main_agent_ref = ResolvedMainAgentRef(
        profile_id=profile.id,
        version_id=version.id,
        profile_key=str(profile.profile_key),
        sequence=int(version.sequence_no),
        content_digest=str(version.content_digest),
    )
    return AdmissionContext(
        mode=str(mode),  # type: ignore[arg-type]
        execution_kind=execution_kind,
        profile=profile,
        profile_version=version,
        snapshot=snapshot,
        main_agent_ref=main_agent_ref,
        control_keys=control_keys,
        frozen_model=frozen,
        provider_ref=provider_ref,
        model_ref=model_ref,
        eligibility=eligibility,
        effective_policy_digest=policy_digest,
        probe_diagnostics=eligibility,
    )


def build_base_manifest_with_controls(
    *,
    run_id: UUID,
    main_agent: ResolvedMainAgentRef,
    provider: ProviderRef,
    model: ModelRef,
    effective_policy_digest: str,
    control_bindings: Sequence[Any],
) -> ResolvedRunManifestRevision:
    refs = control_capability_refs(control_bindings)
    digest = compute_manifest_digest(
        run_id=run_id,
        revision=1,
        parent_digest=None,
        main_agent=main_agent,
        active_skills=(),
        capabilities=refs,
        provider=provider,
        model=model,
        provider_aliases=(),
        effective_policy_digest=effective_policy_digest,
    )
    return ResolvedRunManifestRevision(
        run_id=run_id,
        revision=1,
        parent_digest=None,
        main_agent=main_agent,
        active_skills=(),
        capabilities=refs,
        provider=provider,
        model=model,
        provider_aliases=(),
        effective_policy_digest=effective_policy_digest,
        manifest_digest=digest,
    )


def construct_openai_adapter_after_identity_recheck(
    db: Session,
    *,
    frozen: FrozenModelIdentity,
    provider_ref: ProviderRef,
    app_build_revision: str,
) -> ProviderAdapter:
    """Decrypt credential and build adapter after identity recheck.

    Probe is optional: when ``frozen`` carries no diagnostic probe, recheck
    compares only model/credential revisions and config digests.
    """
    from app.ai_provider.crypto import decrypt_api_key
    from app.ai_registry.models import AiCredential, AiModel, AiModelCapabilityProbe
    from app.assistant.provider_loop.adapters.openai_chat import (
        ExactOpenAIChatRuntimeConfig,
        OpenAIChatCompletionsAdapter,
    )
    from app.assistant.provider_loop.probe import build_model_config_digest
    from app.common.ssrf import normalize_openai_base_url

    model = db.get(AiModel, frozen.model_id)
    credential = db.get(AiCredential, frozen.credential_id)
    if model is None or credential is None:
        raise MainAgentAdmissionError(MODEL_BINDING_MISSING)

    probe_id = model.current_capability_probe_id
    probe_row = db.get(AiModelCapabilityProbe, probe_id) if probe_id else None
    live_probe_digest = str(probe_row.probe_digest) if probe_row is not None else None

    try:
        from app.assistant.provider_loop.probe import build_endpoint_identity

        base_url = normalize_openai_base_url(credential.base_url)
        endpoint = build_endpoint_identity(base_url)
        live_model_config_digest = build_model_config_digest(
            model_id=model.id,
            model_name=str(model.name or ""),
            model_type="llm",
            model_runtime_revision=int(model.runtime_revision or 1),
            credential_id=credential.id,
            credential_runtime_revision=int(credential.runtime_revision or 1),
            endpoint_identity=endpoint,
            adapter_key=str(provider_ref.adapter_key or OPENAI_ADAPTER_KEY),
            adapter_revision=str(provider_ref.adapter_revision or DEFAULT_ADAPTER_REVISION),
            app_build_revision=app_build_revision,
            provider_protocol=provider_ref.provider_protocol,
            probe_contract_version=PROBE_CONTRACT_VERSION,
        )
    except Exception as exc:
        raise MainAgentAdmissionError(ADAPTER_UNAVAILABLE_BEFORE_REQUEST) from exc

    live_cred_digest = _credential_config_digest(
        base_url=str(credential.base_url or ""),
        runtime_revision=int(credential.runtime_revision or 1),
    )
    try:
        recheck_identity_before_decrypt(
            frozen=frozen,
            live_model_runtime_revision=int(model.runtime_revision or 1),
            live_credential_runtime_revision=int(credential.runtime_revision or 1),
            live_model_config_digest=live_model_config_digest,
            live_credential_config_digest=live_cred_digest,
            live_probe_id=probe_id,
            live_probe_digest=live_probe_digest,
        )
    except ModelEligibilityError as exc:
        raise MainAgentAdmissionError(exc.reason_code) from exc

    try:
        api_key = decrypt_api_key(credential.api_key_encrypted)
    except Exception as exc:
        raise MainAgentAdmissionError(ADAPTER_UNAVAILABLE_BEFORE_REQUEST) from exc
    if not api_key or not base_url:
        raise MainAgentAdmissionError(ADAPTER_UNAVAILABLE_BEFORE_REQUEST)

    runtime_config = ExactOpenAIChatRuntimeConfig(
        model_id=model.id,
        model_name=str(model.name or ""),
        model_type="llm",
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=credential.id,
        credential_runtime_revision=int(credential.runtime_revision or 1),
        model_config_digest=live_model_config_digest,
        adapter_key=OPENAI_ADAPTER_KEY,
        adapter_revision=str(provider_ref.adapter_revision or DEFAULT_ADAPTER_REVISION),
        app_build_revision=app_build_revision,
        base_url=base_url,
        api_key=api_key,
        endpoint_identity=endpoint,
    )
    return OpenAIChatCompletionsAdapter(runtime_config=runtime_config)


def construct_openai_adapter_after_eligibility(
    db: Session,
    *,
    frozen: FrozenModelIdentity,
    provider_ref: ProviderRef,
    app_build_revision: str,
) -> ProviderAdapter:
    """Backward-compatible alias for identity-recheck adapter construction."""
    return construct_openai_adapter_after_identity_recheck(
        db,
        frozen=frozen,
        provider_ref=provider_ref,
        app_build_revision=app_build_revision,
    )


class _CancelBridge:
    def __init__(self, checker: Callable[[], bool] | None) -> None:
        self._checker = checker

    def is_cancelled(self) -> bool:
        if self._checker is None:
            return False
        try:
            return bool(self._checker())
        except Exception:
            return False


class _NullEventSink:
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        del event_type, payload


class MainAgentService:
    """Admits and runs one Main Agent Chat path for a single Assistant Run."""

    def __init__(
        self,
        db: Session,
        *,
        admission: AdmissionContext | None = None,
        provider: ProviderAdapter | None = None,
        ports: ProviderLoopPorts | None = None,
        event_adapter: MainAgentEventAdapter | None = None,
        loop: ProviderAgentLoop | None = None,
        app_build_revision: str | None = None,
        # Test/scripted injection: skip live admission when admission+provider given.
        allow_injected_provider: bool = False,
    ) -> None:
        self.db = db
        self._settings = get_settings()
        self._app_build_revision = (
            app_build_revision or self._settings.app_build_revision or "development"
        ).strip()
        self._admission = admission
        self._injected_provider = provider
        self._injected_ports = ports
        self._event_adapter = event_adapter
        self._loop = loop or ProviderAgentLoop()
        self._allow_injected_provider = allow_injected_provider
        self._state: MainAgentRunState | None = None

    @property
    def admission(self) -> AdmissionContext | None:
        return self._admission

    @property
    def state(self) -> MainAgentRunState | None:
        return self._state

    def admit(
        self,
        *,
        mode: MainAgentAdmissionMode | str,
        execution_kind: ExecutionKind = "production",
    ) -> AdmissionContext:
        if self._admission is not None and self._allow_injected_provider:
            return self._admission
        self._admission = admit_main_agent(
            self.db,
            mode=mode,
            execution_kind=execution_kind,
            app_build_revision=self._app_build_revision,
        )
        return self._admission

    def run(self, request: AssistantRuntimeRequest) -> AssistantRuntimeResult:
        events = self._event_adapter or MainAgentEventAdapter(lambda *_a, **_k: None)
        if self._admission is None:
            try:
                # A durable Main Agent Run has already frozen runtime selection.
                # Re-admission validates its dependencies; it never re-routes from env.
                self.admit(mode="read_only", execution_kind=request.execution_kind)
            except MainAgentAdmissionError as exc:
                return self._failed_result(reason_code=exc.reason_code)

        assert self._admission is not None
        admission = self._admission
        events.runtime_selected(
            run_id=request.run_id,
            source_runtime="admission",
            target_runtime="main_agent",
            mode=str(admission.mode),
        )

        control_bindings = build_all_main_agent_control_bindings(
            owner_version_id=admission.main_agent_ref.version_id,
            source_snapshot_digest=admission.main_agent_ref.content_digest,
            app_build_revision=self._app_build_revision,
        )
        manifest = build_base_manifest_with_controls(
            run_id=request.run_id,
            main_agent=admission.main_agent_ref,
            provider=admission.provider_ref,
            model=admission.model_ref,
            effective_policy_digest=admission.effective_policy_digest,
            control_bindings=control_bindings,
        )
        self._state = MainAgentRunState(
            run_id=request.run_id,
            conversation_id=request.conversation_id,
            manifest=manifest,
        )

        # Shadow / evaluation: never write Message/L1/L2/title from MA output.
        persist = request.execution_kind == "production" and str(admission.mode) == "read_only"

        try:
            if request.cancel_checker and request.cancel_checker():
                return AssistantRuntimeResult(
                    runtime="main_agent",
                    status="cancelled",
                    final_text="",
                    reason_code=CANCELLED,
                    write_message=False,
                    write_l1=False,
                    write_l2=False,
                    write_title=False,
                )

            provider = self._injected_provider
            if provider is None:
                try:
                    provider = construct_openai_adapter_after_eligibility(
                        self.db,
                        frozen=admission.frozen_model,
                        provider_ref=admission.provider_ref,
                        app_build_revision=self._app_build_revision,
                    )
                except MainAgentAdmissionError as exc:
                    return self._failed_result(reason_code=exc.reason_code)

            ports = self._injected_ports
            policy_runtime = None
            if ports is None:
                # Plan 05 Task 8: compose frozen policy snapshot + ledgers +
                # Gateway/scheduler/completion guards for this admitted Run.
                try:
                    policy_runtime, ports = self._compose_policy_ports(
                        request=request,
                        admission=admission,
                        manifest=manifest,
                        provider=provider,
                        events=events,
                    )
                    if self._state is not None:
                        self._state.policy_runtime = policy_runtime
                        # Prefer Manifest aligned to Plan 05 effective_policy_digest.
                        if getattr(policy_runtime, "manifest", None) is not None:
                            self._state.manifest = policy_runtime.manifest
                            manifest = policy_runtime.manifest
                except Exception:
                    logger.exception(
                        "main agent policy composition failed run_id=%s",
                        request.run_id,
                    )
                    return self._failed_result(
                        reason_code=ADAPTER_UNAVAILABLE_BEFORE_REQUEST
                    )

            # Reject no-op lifecycle for Main Agent composition.
            if isinstance(
                getattr(ports, "manifest_effect_lifecycle", None),
                NoOpManifestEffectLifecyclePort,
            ):
                return self._failed_result(
                    reason_code=ADAPTER_UNAVAILABLE_BEFORE_REQUEST
                )

            builder = MainAgentPromptBuilder()
            try:
                prompt = builder.build_initial_messages(
                    profile=admission.snapshot,
                    manifest=manifest,
                    current_user_message=request.user_text,
                    locale=request.locale,
                    principal=LOCAL_ASSISTANT_PRINCIPAL,
                    l1_summary=request.l1_summary or "",
                    history=request.l0_messages,
                    catalog_records=(),
                    tool_artifact_summaries=(),
                )
            except Exception:
                return self._failed_result(reason_code=BUDGET_INVALID)

            scope = create_execution_scope(
                run_id=request.run_id,
                conversation_id=request.conversation_id,
                principal=LOCAL_ASSISTANT_PRINCIPAL,
                tenant_scope_id=None,
            )
            max_rounds = int(admission.snapshot.output_budget.max_provider_rounds)
            generation = ProviderGenerationOptions(
                max_output_tokens=int(admission.snapshot.output_budget.max_completion_tokens),
                tool_choice=ProviderToolChoice(mode="auto"),
            )
            loop_request = ProviderLoopRequest(
                manifest=manifest,
                initial_messages=prompt.messages,
                model_ref=admission.model_ref,
                execution_scope=scope,
                max_rounds=max(2, max_rounds),
                locale=request.locale or "en",
                generation=generation,
            )

            # Enforced Runs enter the Provider loop only after the exact
            # Manifest/policy/budget/obligation state and initial prompt have a
            # durable checkpoint. This gives sibling reservation and result
            # commits real revision pointers on a fresh worker claim.
            if policy_runtime is not None and ports.capability_ledger is not None:
                from app.assistant.durable.materialize import materialize_base_run_state
                from app.assistant.durable.repository import LeaseToken
                from app.assistant.models import AssistantChatRun

                run_row = self.db.get(AssistantChatRun, request.run_id)
                if run_row is None or not run_row.lease_owner:
                    raise RuntimeError("enforced capability ledger requires Run lease")
                if run_row.current_manifest_revision_id is None:
                    budget_state = policy_runtime.budget_ledger.snapshot()
                    obligation_state = policy_runtime.obligation_ledger.snapshot()
                    materialize_base_run_state(
                        self.db,
                        run_id=request.run_id,
                        lease=LeaseToken(
                            run_id=request.run_id,
                            worker_id=str(run_row.lease_owner),
                            lease_generation=int(run_row.lease_generation or 0),
                        ),
                        expected_revision=int(run_row.state_revision),
                        manifest_payload=manifest.model_dump(mode="json", by_alias=True),
                        manifest_digest=manifest.manifest_digest,
                        policy_payload=policy_runtime.policy_snapshot.model_dump(
                            mode="json", by_alias=True
                        ),
                        policy_digest=(
                            policy_runtime.policy_snapshot.effective_policy_digest
                        ),
                        budget_payload=budget_state.model_dump(
                            mode="json", by_alias=True
                        ),
                        budget_digest=budget_state.ledger_digest,
                        obligation_payload=obligation_state.model_dump(
                            mode="json", by_alias=True
                        ),
                        obligation_digest=obligation_state.ledger_digest,
                        provider_messages=prompt.messages,
                    )

            # Bridge cancellation into ports if caller provided one.
            # Preserve every Plan 05 additive port — rebuilding without them
            # drops budget/completion/frame guards and dual-wired dispatch state.
            if request.cancel_checker is not None:
                ports = ProviderLoopPorts(
                    provider=ports.provider,
                    tools_provider=ports.tools_provider,
                    current_descriptors=ports.current_descriptors,
                    authorization_evidence=ports.authorization_evidence,
                    tool_dispatcher=ports.tool_dispatcher,
                    sibling_executor=ports.sibling_executor,
                    cancellation=_CancelBridge(request.cancel_checker),
                    events=ports.events,
                    round_context_provider=ports.round_context_provider,
                    manifest_effect_lifecycle=ports.manifest_effect_lifecycle,
                    round_budget_guard=ports.round_budget_guard,
                    call_reservation=ports.call_reservation,
                    call_owner_resolver=ports.call_owner_resolver,
                    dispatch_guard=ports.dispatch_guard,
                    call_frames=ports.call_frames,
                    completion_guard=ports.completion_guard,
                    capability_ledger=ports.capability_ledger,
                )

            result: ProviderLoopResult = self._loop.start(loop_request, ports=ports)
            final_text = (result.final_text or "").strip()

            if result.status == "cancelled":
                return AssistantRuntimeResult(
                    runtime="main_agent",
                    status="cancelled",
                    final_text=final_text,
                    reason_code=CANCELLED,
                    write_message=False,
                    write_l1=False,
                    write_l2=False,
                    write_title=False,
                )
            if result.status != "completed":
                # Fail closed after Provider request started unless retry-safe and
                # no user-visible output was emitted.
                reason = MAIN_AGENT_FAILED
                stop = getattr(result, "stop_reason", None) or ""
                # When stop_reason is coarse capability_error, prefer the stable
                # semantic_code from the SafeProviderError (policy/budget/auth codes).
                semantic = None
                if result.error is not None:
                    semantic = getattr(result.error, "semantic_code", None)
                effective_stop = str(stop)
                if (
                    effective_stop in {"", "capability_error"}
                    and isinstance(semantic, str)
                    and semantic
                    and semantic != "capability_error"
                ):
                    effective_stop = semantic
                if effective_stop:
                    if self._state is not None:
                        self._state.stop_reason = effective_stop[:64]
                    # Prefer exact membership against known policy/budget/completion
                    # stable codes (includes pure §5.4 denials like owner_mismatch).
                    # Unknown codes fail closed to MAIN_AGENT_FAILED.
                    if effective_stop in _STABLE_FAILED_REASON_CODES:
                        reason = effective_stop[:64]
                if (
                    result.error is not None
                    and getattr(result.error, "retry_disposition", None) == "retryable"
                    and not final_text
                    and reason == MAIN_AGENT_FAILED
                ):
                    reason = "provider_retry_safe_before_output"
                    return self._failed_result(reason_code=reason)
                return AssistantRuntimeResult(
                    runtime="main_agent",
                    status="failed",
                    final_text="",
                    reason_code=reason,
                    write_message=False,
                    write_l1=False,
                    write_l2=False,
                    write_title=False,
                )

            # Buffer provisional tool-round text: only terminal final_text is user output.
            # Do NOT emit content_delta here — the outer Assistant Run path owns
            # Message checkpoint + single bounded content_delta stream so SSE and
            # Message content stay ordered and non-duplicated.
            if final_text:
                self._state.final_text = final_text

            return AssistantRuntimeResult(
                runtime="main_agent",
                status="completed",
                final_text=final_text,
                reason_code=MAIN_AGENT_COMPLETED,
                write_message=persist,
                write_l1=persist,
                write_l2=False,  # Plan 04: L2 always zero on new path
                write_title=persist,
                skill_summaries=tuple(events.skill_summaries),
                tool_summaries=tuple(events.tool_summaries),
            )
        except MainAgentAdmissionError as exc:
            return self._failed_result(reason_code=exc.reason_code)
        except Exception:
            logger.exception(
                "main agent run failed run_id=%s conversation_id=%s",
                request.run_id,
                request.conversation_id,
            )
            return AssistantRuntimeResult(
                runtime="main_agent",
                status="failed",
                final_text="",
                reason_code=MAIN_AGENT_FAILED,
                write_message=False,
                write_l1=False,
                write_l2=False,
                write_title=False,
            )

    def _compose_policy_ports(
        self,
        *,
        request: AssistantRuntimeRequest,
        admission: AdmissionContext,
        manifest: ResolvedRunManifestRevision,
        provider: ProviderAdapter,
        events: MainAgentEventAdapter,
    ) -> tuple[Any, ProviderLoopPorts]:
        """Compose Plan 05 policy ledgers + ProviderLoopPorts for this Run."""
        from app.assistant.main_agent.inject_wiring import build_run_catalog_state
        from app.assistant.main_agent.policy_runtime import (
            compose_main_agent_policy_runtime,
        )

        if self.db is None:
            raise RuntimeError("adapter_unavailable_before_request")

        # Operator may lower max_active_skills only (settings).
        operator_limits: dict[str, int | None] = {
            "max_active_skills": int(
                getattr(self._settings, "assistant_main_agent_max_active_skills", 4)
            ),
        }
        # Per-Run catalog for skill.search / skill.inject (fail soft → empty).
        catalog_state = None
        try:
            scope = getattr(admission.snapshot, "skill_catalog_scope", None)
            catalog_state = build_run_catalog_state(
                self.db,
                scope=scope,
                locale=request.locale or "und",
            )
        except Exception:
            logger.exception(
                "main agent catalog build failed run_id=%s", request.run_id
            )
            catalog_state = None

        from app.assistant.durable.repository import LeaseToken
        from app.assistant.models import AssistantChatRun

        run_row = self.db.get(AssistantChatRun, request.run_id)
        ledger_mode = str(
            getattr(run_row, "capability_ledger_mode", None)
            or "legacy_read_only"
        )
        ledger_lease = None
        if run_row is not None and run_row.lease_owner:
            ledger_lease = LeaseToken(
                run_id=run_row.id,
                worker_id=str(run_row.lease_owner),
                lease_generation=int(run_row.lease_generation or 0),
            )

        runtime, ports = compose_main_agent_policy_runtime(
            db=self.db,
            run_id=request.run_id,
            conversation_id=request.conversation_id,
            manifest=manifest,
            profile_key=str(admission.main_agent_ref.profile_key),
            profile_version_id=admission.main_agent_ref.version_id,
            profile_content_digest=str(admission.main_agent_ref.content_digest),
            app_build_revision=self._app_build_revision,
            provider=provider,
            events=events,
            locale=request.locale or "en",
            cancel_checker=request.cancel_checker,
            catalog_state=catalog_state,
            profile_budget_fields=admission.snapshot.output_budget,
            profile_context_budget=admission.snapshot.context_budget,
            operator_budget_limits=operator_limits,
            durable_workflow=request.durable_workflow,
            capability_ledger_mode=ledger_mode,
            capability_ledger_lease=ledger_lease,
            capability_ledger_idempotency_secret=(
                self._settings.assistant_capability_call_idempotency_secret
            ),
            policy_contract_version=(2 if ledger_mode == "enforced" else 1),
        )
        # Use the Manifest aligned to Plan 05 effective_policy_digest.
        if self._state is not None and runtime.manifest is not None:
            self._state.manifest = runtime.manifest
        events.policy_snapshot(
            run_id=request.run_id,
            effective_policy_digest=runtime.policy_snapshot.effective_policy_digest,
            exposure_index_digest=runtime.policy_snapshot.exposure_index.exposure_index_digest,
            max_total_capability_calls=int(
                runtime.run_budget_limits.max_total_capability_calls
            ),
            max_provider_rounds=int(runtime.run_budget_limits.max_provider_rounds),
            max_capability_depth=int(runtime.run_budget_limits.max_capability_depth),
            max_agent_depth=int(runtime.run_budget_limits.max_agent_depth),
        )
        return runtime, ports

    def _failed_result(
        self,
        *,
        reason_code: str,
    ) -> AssistantRuntimeResult:
        """Return a typed Main Agent failure without selecting another runtime."""
        return AssistantRuntimeResult(
            runtime="main_agent",
            status="failed",
            final_text="",
            reason_code=reason_code or MAIN_AGENT_FAILED,
            write_message=False,
            write_l1=False,
            write_l2=False,
            write_title=False,
        )



def chunk_text(text: str, *, chunk_size: int = 64) -> Iterator[str]:
    value = text or ""
    if not value:
        return
        yield  # pragma: no cover
    for i in range(0, len(value), chunk_size):
        yield value[i : i + chunk_size]


__all__ = [
    "ADAPTER_UNAVAILABLE_BEFORE_REQUEST",
    "AdmissionContext",
    "AssistantRuntimeRequest",
    "AssistantRuntimeResult",
    "AssistantRuntimeRunner",
    "BUDGET_INVALID",
    "CONTROL_MISSING",
    "CONTROL_UNSUPPORTED",
    "CANCELLED",
    "ENTRYPOINT_UNSUPPORTED",
    "MAIN_AGENT_COMPLETED",
    "MAIN_AGENT_FAILED",
    "MODE_OFF",
    "MODE_SHADOW_PRODUCTION",
    "MainAgentAdmissionError",
    "MainAgentRunState",
    "MainAgentService",
    "NOOP_LIFECYCLE_REJECTED",
    "PROFILE_DISABLED",
    "PROFILE_UNAVAILABLE",
    "PROFILE_UNPUBLISHED",
    "admit_main_agent",
    "build_base_manifest_with_controls",
    "chunk_text",
    "compute_main_agent_effective_policy_digest",
    "construct_openai_adapter_after_eligibility",
    "construct_openai_adapter_after_identity_recheck",
    "load_default_published_profile",
    "resolve_assistant_model_identity",
    "select_runtime_for_mode",
    "should_construct_main_agent",
    "validate_profile_for_assistant_chat",
]
