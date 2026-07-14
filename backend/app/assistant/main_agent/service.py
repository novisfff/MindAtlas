"""Main Agent runtime admission and Assistant Run composition (Plan 04 Task 8).

Feature modes:
- off: Legacy only; never construct MainAgentService
- shadow: production chat stays Legacy; explicit evaluation may run MA without
  Message/L1/L2/title writes
- read_only: admit MA after preflight; fallback to Legacy only when §4.3 allows
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
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
    MainAgentProfileSnapshotV1,
    ModelRequirementsV1,
)
from app.config import AssistantMainAgentMode, get_settings

logger = logging.getLogger(__name__)

RuntimeKind = Literal["legacy", "main_agent"]
ExecutionKind = Literal["production", "evaluation"]
AdmissionDecision = Literal["legacy", "main_agent", "fallback_legacy", "fail"]

# Safe admission / fallback reason codes
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
FALLBACK_DISALLOWED = "fallback_disallowed"
FALLBACK_SAFE = "fallback_safe"
CANCELLED = "cancelled"
MAIN_AGENT_COMPLETED = "main_agent_completed"
MAIN_AGENT_FAILED = "main_agent_failed"
NOOP_LIFECYCLE_REJECTED = "noop_lifecycle_rejected"

FALLBACK_SAFE_REASONS = frozenset(
    {
        PROFILE_UNAVAILABLE,
        PROFILE_DISABLED,
        PROFILE_UNPUBLISHED,
        MODEL_BINDING_MISSING,
        MODEL_INELIGIBLE,
        MODEL_TYPE_UNSUPPORTED,
        PROBE_MISSING,
        ADAPTER_UNAVAILABLE,
        ADAPTER_UNAVAILABLE_BEFORE_REQUEST,
        CATALOG_UNAVAILABLE,
        BUDGET_INVALID,
        CONTROL_MISSING,
        CONTROL_UNSUPPORTED,
        ENTRYPOINT_UNSUPPORTED,
        "provider_retry_safe_before_output",
    }
)


class MainAgentAdmissionError(ValueError):
    """Preflight failure with a safe reason code (no secrets / exception text)."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class MainAgentFallbackState:
    """Process-local fallback gate (Plan 04 §4.3)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.provider_requests_started = 0
        self.capability_dispatches_started = 0
        self.strongest_started_side_effect: str | None = None
        self.pending_interrupt = False
        self.uncertain_result = False
        self.user_output_started = False

    def mark_provider_request(self) -> None:
        with self._lock:
            self.provider_requests_started += 1

    def mark_capability_dispatch(self, *, side_effect: str | None = None) -> None:
        with self._lock:
            self.capability_dispatches_started += 1
            if side_effect:
                self.strongest_started_side_effect = _stronger_side_effect(
                    self.strongest_started_side_effect, side_effect
                )

    def mark_user_output(self) -> None:
        with self._lock:
            self.user_output_started = True

    def mark_pending_interrupt(self) -> None:
        with self._lock:
            self.pending_interrupt = True

    def mark_uncertain(self) -> None:
        with self._lock:
            self.uncertain_result = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider_requests_started": self.provider_requests_started,
                "capability_dispatches_started": self.capability_dispatches_started,
                "strongest_started_side_effect": self.strongest_started_side_effect,
                "pending_interrupt": self.pending_interrupt,
                "uncertain_result": self.uncertain_result,
                "user_output_started": self.user_output_started,
            }

    def allows_automatic_fallback(
        self,
        *,
        reason_code: str,
        legacy_runtime_allowed: bool,
        before_side_effects_only: bool,
        cancel_requested: bool,
    ) -> bool:
        with self._lock:
            if cancel_requested:
                return False
            if not legacy_runtime_allowed:
                return False
            if before_side_effects_only is False:
                return False
            if reason_code not in FALLBACK_SAFE_REASONS:
                return False
            if self.user_output_started:
                return False
            if self.pending_interrupt or self.uncertain_result:
                return False
            strongest = self.strongest_started_side_effect
            if strongest in {"draft", "write_local", "write_external", "unknown"}:
                return False
            return True


_SIDE_EFFECT_RANK = {
    None: -1,
    "none": 0,
    "compute": 1,
    "read": 2,
    "draft": 3,
    "write_local": 4,
    "write_external": 5,
    "unknown": 6,
}


def _stronger_side_effect(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return (
        candidate
        if _SIDE_EFFECT_RANK.get(candidate, -1) > _SIDE_EFFECT_RANK.get(current, -1)
        else current
    )


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


@dataclass(frozen=True)
class AssistantRuntimeResult:
    runtime: RuntimeKind
    status: Literal["completed", "failed", "cancelled", "fallback"]
    final_text: str
    reason_code: str | None = None
    write_message: bool = True
    write_l1: bool = True
    write_l2: bool = False
    write_title: bool = True
    skill_summaries: tuple[dict[str, Any], ...] = ()
    tool_summaries: tuple[dict[str, Any], ...] = ()
    fallback_to_legacy: bool = False


class AssistantRuntimeRunner(Protocol):
    def run(self, request: AssistantRuntimeRequest) -> AssistantRuntimeResult: ...


@dataclass(frozen=True)
class AdmissionContext:
    mode: AssistantMainAgentMode
    execution_kind: ExecutionKind
    profile: AssistantMainAgentProfile
    profile_version: AssistantMainAgentProfileVersion
    snapshot: MainAgentProfileSnapshotV1
    main_agent_ref: ResolvedMainAgentRef
    control_keys: tuple[str, ...]
    frozen_model: FrozenModelIdentity
    provider_ref: ProviderRef
    model_ref: ModelRef
    eligibility: ModelEligibilityReport
    effective_policy_digest: str
    legacy_runtime_allowed: bool
    before_side_effects_only: bool


@dataclass
class MainAgentRunState:
    """Process-local Run state (non-durable; Plan 06 owns durability)."""

    run_id: UUID
    conversation_id: UUID
    manifest: ResolvedRunManifestRevision
    fallback: MainAgentFallbackState = field(default_factory=MainAgentFallbackState)
    applied_skill_version_ids: set[UUID] = field(default_factory=set)
    final_text: str = ""
    status: str = "running"


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
    mode: AssistantMainAgentMode | str,
    execution_kind: ExecutionKind = "production",
) -> tuple[RuntimeKind | None, str | None]:
    """Return (runtime_to_try, reason) without constructing services.

    - off: legacy only (None means "do not construct MA")
    - shadow + production: legacy only
    - shadow + evaluation: main_agent allowed
    - read_only: main_agent after preflight
    """
    normalized = str(mode or "off").strip().lower()
    if normalized == "off":
        return "legacy", MODE_OFF
    if normalized == "shadow":
        if execution_kind == "production":
            return "legacy", MODE_SHADOW_PRODUCTION
        return "main_agent", None
    if normalized == "read_only":
        return "main_agent", None
    return "legacy", MODE_OFF


def should_construct_main_agent(
    *,
    mode: AssistantMainAgentMode | str,
    execution_kind: ExecutionKind = "production",
) -> bool:
    runtime, _ = select_runtime_for_mode(mode=mode, execution_kind=execution_kind)
    return runtime == "main_agent"


def _credential_config_digest(*, base_url: str, runtime_revision: int) -> str:
    parts = urlsplit((base_url or "").strip())
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "scheme": parts.scheme or None,
            "host": parts.hostname,
            "port": parts.port,
            "path": parts.path or None,
            "runtimeRevision": int(runtime_revision or 1),
        }
    )


def load_default_published_profile(
    db: Session,
) -> tuple[AssistantMainAgentProfile, AssistantMainAgentProfileVersion, MainAgentProfileSnapshotV1]:
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
        snapshot = MainAgentProfileSnapshotV1.model_validate(version.snapshot or {})
    except Exception as exc:
        raise MainAgentAdmissionError(PROFILE_UNAVAILABLE) from exc
    recomputed = snapshot.content_digest()
    if recomputed != str(version.content_digest or ""):
        raise MainAgentAdmissionError(PROFILE_UNAVAILABLE)
    return profile, version, snapshot


def validate_profile_for_assistant_chat(
    snapshot: MainAgentProfileSnapshotV1,
) -> tuple[str, ...]:
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
    mode: AssistantMainAgentMode | str,
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
        legacy_runtime_allowed=bool(snapshot.fallback_policy.legacy_runtime_allowed),
        before_side_effects_only=bool(snapshot.fallback_policy.before_side_effects_only),
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


def construct_openai_adapter_after_eligibility(
    db: Session,
    *,
    frozen: FrozenModelIdentity,
    provider_ref: ProviderRef,
    app_build_revision: str,
) -> ProviderAdapter:
    """Decrypt credential and build adapter only after eligibility + recheck."""
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
        mode: AssistantMainAgentMode | str,
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
        mode = self._settings.assistant_main_agent_mode
        if self._admission is None:
            try:
                self.admit(mode=mode, execution_kind=request.execution_kind)
            except MainAgentAdmissionError as exc:
                return self._admission_failure_result(
                    request=request,
                    reason_code=exc.reason_code,
                    events=events,
                )

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
        fallback = self._state.fallback

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
                    return self._maybe_fallback(
                        request=request,
                        reason_code=exc.reason_code,
                        events=events,
                        fallback=fallback,
                        admission=admission,
                    )

            ports = self._injected_ports
            if ports is None:
                # Production composition of full Gateway ports is completed as
                # residual inject work; without injected ports we fail closed
                # before a Provider request so fallback can still apply.
                return self._maybe_fallback(
                    request=request,
                    reason_code=ADAPTER_UNAVAILABLE_BEFORE_REQUEST,
                    events=events,
                    fallback=fallback,
                    admission=admission,
                )

            # Reject no-op lifecycle for Main Agent composition.
            if isinstance(
                getattr(ports, "manifest_effect_lifecycle", None),
                NoOpManifestEffectLifecyclePort,
            ):
                return self._maybe_fallback(
                    request=request,
                    reason_code=ADAPTER_UNAVAILABLE_BEFORE_REQUEST,
                    events=events,
                    fallback=fallback,
                    admission=admission,
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
                return self._maybe_fallback(
                    request=request,
                    reason_code=BUDGET_INVALID,
                    events=events,
                    fallback=fallback,
                    admission=admission,
                )

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

            # Bridge cancellation into ports if caller provided one.
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
                )

            fallback.mark_provider_request()
            result: ProviderLoopResult = self._loop.start(loop_request, ports=ports)
            # Any capability dispatch that actually started blocks automatic Legacy
            # fallback under §4.3 (read completions still count as started dispatches).
            tool_records = getattr(result, "tool_calls", None) or ()
            for record in tool_records:
                side = None
                try:
                    # Prefer descriptor-level side effect if present on the record.
                    side = getattr(record, "side_effect", None) or getattr(
                        getattr(record, "call", None), "side_effect", None
                    )
                except Exception:
                    side = None
                fallback.mark_capability_dispatch(side_effect=side if isinstance(side, str) else "read")
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
                if (
                    result.error is not None
                    and getattr(result.error, "retry_disposition", None) == "retryable"
                    and not final_text
                ):
                    reason = "provider_retry_safe_before_output"
                    return self._maybe_fallback(
                        request=request,
                        reason_code=reason,
                        events=events,
                        fallback=fallback,
                        admission=admission,
                    )
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
                fallback.mark_user_output()
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
            return self._maybe_fallback(
                request=request,
                reason_code=exc.reason_code,
                events=events,
                fallback=fallback,
                admission=admission,
            )
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

    def _admission_failure_result(
        self,
        *,
        request: AssistantRuntimeRequest,
        reason_code: str,
        events: MainAgentEventAdapter,
    ) -> AssistantRuntimeResult:
        # No admission context: use conservative fallback policy from defaults.
        legacy_allowed = True
        before_side_effects_only = True
        fallback = MainAgentFallbackState()
        if fallback.allows_automatic_fallback(
            reason_code=reason_code,
            legacy_runtime_allowed=legacy_allowed,
            before_side_effects_only=before_side_effects_only,
            cancel_requested=bool(request.cancel_checker and request.cancel_checker()),
        ):
            events.fallback_selected(
                run_id=request.run_id,
                source_runtime="main_agent",
                target_runtime="legacy",
                reason_code=reason_code,
            )
            return AssistantRuntimeResult(
                runtime="main_agent",
                status="fallback",
                final_text="",
                reason_code=reason_code,
                write_message=False,
                write_l1=False,
                write_l2=False,
                write_title=False,
                fallback_to_legacy=True,
            )
        return AssistantRuntimeResult(
            runtime="main_agent",
            status="failed",
            final_text="",
            reason_code=reason_code or FALLBACK_DISALLOWED,
            write_message=False,
            write_l1=False,
            write_l2=False,
            write_title=False,
            fallback_to_legacy=False,
        )

    def _maybe_fallback(
        self,
        *,
        request: AssistantRuntimeRequest,
        reason_code: str,
        events: MainAgentEventAdapter,
        fallback: MainAgentFallbackState,
        admission: AdmissionContext,
    ) -> AssistantRuntimeResult:
        cancel_requested = bool(request.cancel_checker and request.cancel_checker())
        if fallback.allows_automatic_fallback(
            reason_code=reason_code,
            legacy_runtime_allowed=admission.legacy_runtime_allowed,
            before_side_effects_only=admission.before_side_effects_only,
            cancel_requested=cancel_requested,
        ):
            snap = fallback.snapshot()
            events.fallback_selected(
                run_id=request.run_id,
                source_runtime="main_agent",
                target_runtime="legacy",
                reason_code=reason_code,
                provider_requests_started=int(snap["provider_requests_started"]),
                capability_dispatches_started=int(snap["capability_dispatches_started"]),
                strongest_side_effect=snap["strongest_started_side_effect"],
            )
            return AssistantRuntimeResult(
                runtime="main_agent",
                status="fallback",
                final_text="",
                reason_code=reason_code,
                write_message=False,
                write_l1=False,
                write_l2=False,
                write_title=False,
                fallback_to_legacy=True,
            )
        return AssistantRuntimeResult(
            runtime="main_agent",
            status="failed",
            final_text="",
            reason_code=reason_code if reason_code else FALLBACK_DISALLOWED,
            write_message=False,
            write_l1=False,
            write_l2=False,
            write_title=False,
            fallback_to_legacy=False,
        )


class LegacyAssistantRuntimeRunner:
    """Wraps the existing ``_generate_response`` path."""

    def __init__(
        self,
        generate: Callable[..., Iterator[str]],
        *,
        conversation_id: UUID,
        message_id: UUID | None,
        run_id: UUID,
        stream_output: bool,
        locale: str,
        db: Session,
        cancel_checker: Callable[[], bool] | None = None,
        event_callbacks: Mapping[str, Any] | None = None,
    ) -> None:
        self._generate = generate
        self._conversation_id = conversation_id
        self._message_id = message_id
        self._run_id = run_id
        self._stream_output = stream_output
        self._locale = locale
        self._db = db
        self._cancel_checker = cancel_checker
        self._event_callbacks = dict(event_callbacks or {})

    def run(self, request: AssistantRuntimeRequest) -> AssistantRuntimeResult:
        del request  # parameters already bound at construction for Legacy path
        parts: list[str] = []
        for delta in self._generate(
            self._conversation_id,
            message_id=self._message_id,
            run_id=self._run_id,
            stream_output=self._stream_output,
            locale=self._locale,
            db=self._db,
            cancel_checker=self._cancel_checker,
            **self._event_callbacks,
        ):
            if delta:
                parts.append(str(delta))
        text = "".join(parts)
        return AssistantRuntimeResult(
            runtime="legacy",
            status="completed",
            final_text=text,
            reason_code=None,
            write_message=True,
            write_l1=True,
            write_l2=True,
            write_title=True,
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
    "FALLBACK_DISALLOWED",
    "FALLBACK_SAFE_REASONS",
    "LegacyAssistantRuntimeRunner",
    "MAIN_AGENT_COMPLETED",
    "MAIN_AGENT_FAILED",
    "MODE_OFF",
    "MODE_SHADOW_PRODUCTION",
    "MainAgentAdmissionError",
    "MainAgentFallbackState",
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
    "load_default_published_profile",
    "resolve_assistant_model_identity",
    "select_runtime_for_mode",
    "should_construct_main_agent",
    "validate_profile_for_assistant_chat",
]
