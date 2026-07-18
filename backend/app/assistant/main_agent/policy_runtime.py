"""Plan 05 Main Agent per-Run policy composition (Task 8).

Composes one frozen EffectiveRunPolicySnapshot, BudgetLedger, ObligationLedger,
sibling-isolated frame port, evaluator/evidence factory, scheduler/Gateway
guards, and completion guard for every admitted Main Agent Run.

Provider Loop / Capability packages never import ledger state types; this module
closes over process-local ledgers and projects only provider-neutral ports.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capabilities.classification import CapabilityClassifier
from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityError,
    CapabilityResult,
    EvidenceVerifierKey,
    FrozenCapabilityBinding,
)
from app.assistant.capabilities.policy import AuthorizationEvidenceVerifier
from app.assistant.capabilities.ports import (
    CapabilityRuntimePorts,
    MainAgentControlCallPort,
)
from app.assistant.capabilities.runtime import build_capability_runtime
from app.assistant.domain.contracts import ResolvedRunManifestRevision
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.main_agent.authorization import (
    LOCAL_ASSISTANT_PRINCIPAL,
    MainAgentAuthorizationEvidenceFactory,
    owner_ref_from_binding_provenance,
)
from app.assistant.main_agent.control_capabilities import (
    MAIN_AGENT_CONTROL_KEYS,
    build_all_main_agent_control_bindings,
)
from app.assistant.main_agent.control_runtime import MainAgentControlRuntime
from app.assistant.main_agent.dispatch_hooks import next_manifest_from_control_effect
from app.assistant.main_agent.manifest_runtime import (
    CandidateExposureView,
    MainAgentManifestEffectLifecycle,
    SkillInjectionPolicyContext,
)
from app.assistant.policy.budgets import BudgetLedger
from app.assistant.policy.completion import ObligationLedgerCompletionGuard
from app.assistant.policy.contracts import (
    EffectiveRunPolicySnapshot,
    OwnerBudgetLimits,
    RunBudgetLimits,
    build_effective_run_policy_snapshot,
    build_owner_policy_ref,
    compute_owner_policy_digest,
    normalize_owner_budget_limits,
    normalize_run_budget_limits,
)
from app.assistant.policy.evaluator import OwnerGrantMaterial
from app.assistant.policy.exposures import (
    ExposureBindingInput,
    build_manifest_exposure_index_from_inputs,
    resolve_owner_from_binding,
)
from app.assistant.policy.obligations import ObligationLedger
from app.assistant.policy.recursion import ProcessLocalCapabilityCallFramePort
from app.assistant.policy.runtime import (
    BudgetLedgerDispatchGuard,
    BudgetLedgerReservationPort,
    BudgetLedgerRoundGuard,
    DomainKeyOwnerResolver,
)
from app.assistant.provider_loop.aliases import (
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (
    CapabilityLedgerAggregatePort,
    CancellationPort,
    ProviderAdapter,
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderExecutionScope,
    ProviderLoopEventSink,
    ProviderLoopPorts,
    create_execution_scope,
)
from app.assistant.provider_loop.runtime import (
    ClassificationDriftError,
    _CancellationBridge,
    _NullCapabilityEventSink,
    descriptors_equal_for_freshness,
)
from app.assistant.provider_loop.scheduler import (
    BoundedIsolatedSiblingExecutor,
    SequentialSiblingExecutor as SeqExecutor,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


# ---------------------------------------------------------------------------
# Process-local Run policy bundle
# ---------------------------------------------------------------------------


@dataclass
class MainAgentPolicyRuntime:
    """One admitted Run's process-local Plan 05 policy/ledger/frame state."""

    run_id: UUID
    conversation_id: UUID
    # Base Manifest aligned so effective_policy_digest == policy_snapshot digest.
    manifest: ResolvedRunManifestRevision
    policy_snapshot: EffectiveRunPolicySnapshot
    budget_ledger: BudgetLedger
    obligation_ledger: ObligationLedger
    call_frames: ProcessLocalCapabilityCallFramePort
    dispatch_guard: BudgetLedgerDispatchGuard
    round_budget_guard: BudgetLedgerRoundGuard
    call_reservation: BudgetLedgerReservationPort
    completion_guard: ObligationLedgerCompletionGuard
    authorization_factory: MainAgentAuthorizationEvidenceFactory
    lifecycle: MainAgentManifestEffectLifecycle
    control_runtime: MainAgentControlRuntime
    control_bindings: tuple[FrozenCapabilityBinding, ...]
    tools_provider: Any
    owner_materials: dict[tuple[str, str, UUID], OwnerGrantMaterial]
    owners_by_domain_key: dict[str, tuple[str, UUID]]
    run_budget_limits: RunBudgetLimits
    app_build_revision: str
    profile_key: str
    profile_version_id: UUID
    profile_content_digest: str
    # Exact accepted Skill policy metadata used by later sequential injection
    # preflight. Updated only inside the lifecycle accept hook.
    active_skill_candidates_by_version: dict[UUID, Any] = field(default_factory=dict)
    enforce_skill_inject_reservation: bool = True
    # Mutable ownership map rebuilt on skill.inject accept.
    _owner_resolver: DomainKeyOwnerResolver = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._owner_resolver = DomainKeyOwnerResolver(
            owners_by_domain_key=dict(self.owners_by_domain_key),
            default_owner_kind="main_agent",
            default_owner_version_id=self.profile_version_id,
        )

    @property
    def call_owner_resolver(self) -> DomainKeyOwnerResolver:
        return self._owner_resolver

    def rebind_owners(
        self,
        owners_by_domain_key: Mapping[str, tuple[str, UUID]],
    ) -> None:
        # Mutate the shared DomainKeyOwnerResolver in place so ProviderLoopPorts
        # that hold the same instance see skill ownership after inject accept.
        self.owners_by_domain_key = dict(owners_by_domain_key)
        self._owner_resolver.rebind(
            owners_by_domain_key,
            default_owner_kind="main_agent",
            default_owner_version_id=self.profile_version_id,
        )

    def rebind_policy_snapshot(self, snapshot: EffectiveRunPolicySnapshot) -> None:
        self.policy_snapshot = snapshot
        self.authorization_factory.rebind_manifest(
            self.authorization_factory.manifest,
            policy_snapshot=snapshot,
            owner_materials=self.owner_materials,
        )
        self.lifecycle.register_policy_snapshot(snapshot)
        self.lifecycle.bind_policy_ledgers(
            budget_ledger=self.budget_ledger,
            obligation_ledger=self.obligation_ledger,
            policy_snapshot=snapshot,
        )


# ---------------------------------------------------------------------------
# Main Agent Gateway dispatcher (skill_policy + control port)
# ---------------------------------------------------------------------------


@dataclass
class MainAgentGatewayToolDispatcher:
    """Production Main Agent dispatcher: skill_policy evidence + control port.

    Dual-wires ``dispatch_guard`` into both ProviderLoopPorts and Gateway ports.
    Accepts issuer=skill_policy / entrypoint=main_agent only.
    Applies CapabilityResult-owned obligation transitions after Gateway execute
    (Plan 05 call-order step 10).
    """

    session_factory: SessionFactory
    authorization_factory: MainAgentAuthorizationEvidenceFactory
    control_port: MainAgentControlCallPort
    durable_workflow: Any | None = None
    locale: str | None = None
    classifier: CapabilityClassifier | None = None
    next_manifest_hook: (
        Callable[[ProviderDispatchRequest, CapabilityResult], ResolvedRunManifestRevision]
        | None
    ) = None
    pending_effect_cleanup_hook: Callable[[str, str], None] | None = None
    dispatch_guard: Any | None = None
    call_frames: Any | None = None
    obligation_ledger: ObligationLedger | None = None
    dispatch_calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def dispatch(
        self,
        request: ProviderDispatchRequest,
        *,
        cancellation: CancellationPort,
    ) -> ProviderDispatchResult:
        call = request.call
        binding = request.binding
        exposed = request.descriptor
        if call.binding_contract_digest != binding.ref.binding_contract_digest:
            raise ValueError("dispatch binding digest mismatch")
        if call.descriptor_digest != exposed.descriptor_digest:
            raise ValueError("dispatch descriptor digest mismatch")
        if request.execution_scope.run_id != request.current_manifest.run_id:
            raise ValueError("dispatch scope run_id mismatch")
        if request.authorization.call_id != call.call_id:
            raise ValueError("authorization call_id mismatch")
        if (
            request.authorization.principal.principal_id
            != request.execution_scope.principal.principal_id
        ):
            raise ValueError("authorization principal mismatch")
        if (
            request.authorization.issuer != "skill_policy"
            or request.authorization.entrypoint != "main_agent"
        ):
            raise ValueError(
                "main agent dispatcher accepts only issuer=skill_policy entrypoint=main_agent"
            )

        def _release_unstarted(reason_code: str) -> None:
            guard = self.dispatch_guard
            if guard is None:
                return
            try:
                guard.release_unstarted(call_id=call.call_id, reason_code=reason_code)
            except Exception:
                return

        def _cleanup_pending_effect(reason_code: str) -> None:
            hook = self.pending_effect_cleanup_hook
            if hook is None:
                return
            try:
                hook(call.call_id, reason_code)
            except Exception:
                logger.debug(
                    "pending Manifest effect cleanup failed for call_id=%s",
                    call.call_id,
                    exc_info=True,
                )

        session = self.session_factory()
        try:
            # Per-call verifier: take one-shot verifier issued with evidence.
            try:
                skill_verifier = self.authorization_factory.take_verifier(
                    call_id=call.call_id
                )
            except Exception:
                _release_unstarted("verifier_not_found")
                return ProviderDispatchResult(
                    capability_result=_blocked(
                        call_id=call.call_id,
                        safe_code="capability_denied",
                        safe_message="authorization verifier unavailable",
                        target_identity=binding.ref.target_identity,
                    ),
                    next_manifest=request.current_manifest,
                )

            evidence_verifiers: dict[
                EvidenceVerifierKey, AuthorizationEvidenceVerifier
            ] = {
                ("skill_policy", "main_agent"): skill_verifier,  # type: ignore[dict-item]
            }
            gateway = build_capability_runtime(
                db=session,
                evidence_verifiers=evidence_verifiers,
                locale=self.locale,
                classifier=self.classifier,
                main_agent_control_port=self.control_port,
            )
            with self._lock:
                self.dispatch_calls.append(
                    {
                        "call_id": call.call_id,
                        "domain_key": call.domain_key,
                        "binding_digest": binding.ref.binding_contract_digest,
                    }
                )

            current = gateway.describe(binding)
            if not descriptors_equal_for_freshness(exposed=exposed, current=current):
                _release_unstarted("classification_changed")
                return ProviderDispatchResult(
                    capability_result=_blocked(
                        call_id=call.call_id,
                        safe_code="classification_changed",
                        safe_message="capability classification changed before gateway execute",
                        target_identity=binding.ref.target_identity,
                        error_type="version_drift",
                    ),
                    next_manifest=request.current_manifest,
                )

            from app.assistant.capabilities.contracts import (
                CapabilityExecutionContext,
                CapabilityExecutionRequest,
            )

            context = CapabilityExecutionContext(
                call_id=call.call_id,
                run_id=request.execution_scope.run_id,
                conversation_id=request.execution_scope.conversation_id,
                locale=self.locale,
                request_source="main_agent",
                request_channel="assistant_chat",
                request_session=str(request.execution_scope.conversation_id),
                request_tool=call.domain_key,
                nesting_depth=0,
            )
            execution_request = CapabilityExecutionRequest(
                binding=binding,
                input=dict(call.arguments),
                context=context,
                authorization=request.authorization,
            )
            ports_kwargs: dict[str, Any] = {
                "cancellation": _CancellationBridge(cancellation),
                "events": _NullCapabilityEventSink(),  # type: ignore[arg-type]
            }
            if self.durable_workflow is not None:
                ports_kwargs["durable_workflow"] = self.durable_workflow
            if self.dispatch_guard is not None:
                ports_kwargs["dispatch_guard"] = self.dispatch_guard
            if self.call_frames is not None:
                ports_kwargs["call_frames"] = self.call_frames
            ports = CapabilityRuntimePorts(**ports_kwargs)
            result = gateway.execute(execution_request, ports=ports)
            result = self._apply_result_obligations(
                call=call,
                binding=binding,
                result=result,
                run_id=request.execution_scope.run_id,
            )
            next_manifest = request.current_manifest
            if self.next_manifest_hook is not None:
                next_manifest = self.next_manifest_hook(request, result)
            if call.domain_key == "skill.inject" and result.status != "completed":
                _cleanup_pending_effect("control_result_not_completed")
                next_manifest = request.current_manifest
            return ProviderDispatchResult(
                capability_result=result,
                next_manifest=next_manifest,
            )
        except BaseException:
            _cleanup_pending_effect("dispatcher_error")
            raise
        finally:
            try:
                session.close()
            except BaseException:
                _cleanup_pending_effect("dispatcher_session_close_error")
                raise

    def _apply_result_obligations(
        self,
        *,
        call: Any,
        binding: FrozenCapabilityBinding,
        result: CapabilityResult,
        run_id: UUID,
    ) -> CapabilityResult:
        """Plan 05 call-order step 10: result-owned obligation transitions.

        Failures are contained — obligation ledger errors must not change the
        already-returned CapabilityResult or double-charge budgets.
        """
        ledger = self.obligation_ledger
        if ledger is None or self.authorization_factory is None:
            return result
        factory = self.authorization_factory
        try:
            owner = owner_ref_from_binding_provenance(
                binding,
                profile_key=factory.profile_key,
                skill_package_id_by_version=factory.skill_package_id_by_version or None,
            )
        except Exception:
            # Plan 04 path / tests without package map: fall back to version_id owner.
            try:
                owner = owner_ref_from_binding_provenance(
                    binding,
                    profile_key=factory.profile_key,
                )
            except Exception:
                return result
        owner_kind = owner.owner_kind
        if owner_kind not in {"main_agent", "skill_version", "capability_call"}:
            owner_kind = "capability_call"
        try:
            output_digest = _capability_result_output_digest(result)
            compatible_consumers: tuple[UUID, ...] = ()
            policy_snapshot = getattr(factory, "policy_snapshot", None)
            exposure_index = getattr(policy_snapshot, "exposure_index", None)
            if exposure_index is not None:
                matching_exposures = tuple(
                    exposure
                    for exposure in exposure_index.exposures
                    if exposure.domain_key == binding.ref.capability_key
                    and exposure.binding_contract_digest
                    == binding.ref.binding_contract_digest
                    and exposure.owner_kind == owner_kind
                    and exposure.owner_id == str(owner.owner_id)
                    and exposure.owner_version_id == owner.owner_version_id
                )
                if len(matching_exposures) == 1:
                    compatible_consumers = matching_exposures[
                        0
                    ].compatible_consumer_version_ids
            pending_consumer_ids = tuple(
                obligation.obligation_id
                for obligation in ledger.snapshot().obligations
                if obligation.status == "pending"
                and obligation.obligation_type == "terminal_output"
                and obligation.owner_version_id in set(compatible_consumers)
            )
            completion = binding.resolved.completion
            completion_contract_digest = sha256_canonical_json(
                {
                    "terminalOutput": bool(completion.terminal_output),
                    "needsFollowup": bool(completion.needs_followup),
                    "followupHint": completion.followup_hint,
                }
            )
            decision = ledger.apply_capability_result(
                call_id=call.call_id,
                result_status=str(result.status),
                terminal_output=bool(result.terminal_output),
                needs_followup=bool(result.needs_followup),
                output_digest=output_digest,
                owner_kind=owner_kind,  # type: ignore[arg-type]
                owner_id=str(owner.owner_id),
                owner_version_id=owner.owner_version_id,
                run_id=run_id,
                binding_contract_digest=binding.ref.binding_contract_digest,
                compatible_consumer_version_ids=compatible_consumers,
                completion_contract_digest=completion_contract_digest,
                target_consumer_obligation_ids=pending_consumer_ids,
            )
            if not decision.allowed:
                return _obligation_protocol_failure(
                    call_id=call.call_id,
                    target_identity=binding.ref.target_identity,
                    metrics=result.metrics,
                )
            return result
        except Exception:
            logging.getLogger(__name__).debug(
                "obligation apply_capability_result failed for call_id=%s",
                getattr(call, "call_id", None),
                exc_info=True,
            )
            return _obligation_protocol_failure(
                call_id=str(getattr(call, "call_id", "") or "unknown"),
                target_identity=binding.ref.target_identity,
                metrics=result.metrics,
            )


def _obligation_protocol_failure(
    *,
    call_id: str,
    target_identity: str,
    metrics: Any,
) -> CapabilityResult:
    from app.assistant.capabilities.contracts import CapabilityError, failed_result

    return failed_result(
        error=CapabilityError(
            error_type="protocol_error",
            safe_code="obligation_state_protocol_error",
            safe_message="capability result obligation transition failed",
            retry_disposition="never",
            call_id=call_id,
            target_identity=target_identity,
        ),
        metrics=metrics,
    )


def _capability_result_output_digest(result: CapabilityResult) -> str:
    """Deterministic digest of user/structured/artifact output for obligation evidence."""
    structured = result.structured_output
    has_structured_output = False
    if isinstance(structured, str):
        has_structured_output = bool(structured.strip())
    elif isinstance(structured, (dict, list, tuple)):
        has_structured_output = bool(structured)
    elif structured is not None:
        # JSON scalar values, including false and zero, are material output.
        has_structured_output = True
    if not (
        (isinstance(result.user_text, str) and bool(result.user_text.strip()))
        or has_structured_output
        or bool(result.artifact_refs)
    ):
        return ""

    payload: dict[str, Any] = {
        "status": result.status,
        "terminalOutput": bool(result.terminal_output),
        "needsFollowup": bool(result.needs_followup),
        "userText": result.user_text,
        "structuredOutput": result.structured_output,
        "artifactRefs": [
            {
                "artifactId": str(getattr(ref, "artifact_id", "") or ""),
                "digest": getattr(ref, "content_digest", None)
                or getattr(ref, "digest", None),
            }
            for ref in (result.artifact_refs or ())
        ],
    }
    return sha256_canonical_json(payload)  # type: ignore[arg-type]


def _blocked(
    *,
    call_id: str,
    safe_code: str,
    safe_message: str,
    target_identity: str | None = None,
    error_type: str = "unauthorized",
) -> CapabilityResult:
    from app.assistant.capabilities.contracts import CapabilityMetrics

    return CapabilityResult(
        status="failed",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=None,
        terminal_output=False,
        needs_followup=False,
        error=CapabilityError(
            error_type=error_type,  # type: ignore[arg-type]
            safe_code=safe_code[:64],
            safe_message=safe_message,
            retry_disposition="never",
            call_id=call_id,
            target_identity=target_identity,
        ),
        metrics=CapabilityMetrics(duration_ms=0.0, input_bytes=0, output_bytes=0),
    )


# ---------------------------------------------------------------------------
# Tools provider with Gateway.describe
# ---------------------------------------------------------------------------


@dataclass
class MainAgentGatewayToolsProvider:
    """ToolsProvider: base controls + active skill bindings, described via Gateway."""

    session_factory: SessionFactory
    control_bindings: tuple[FrozenCapabilityBinding, ...]
    control_port: MainAgentControlCallPort
    authorization_factory: MainAgentAuthorizationEvidenceFactory
    locale: str | None = "en"
    classifier: CapabilityClassifier | None = None
    provider_protocol: str = OPENAI_CHAT_PROVIDER_PROTOCOL
    active_bindings_by_version: dict[UUID, tuple[FrozenCapabilityBinding, ...]] = field(
        default_factory=dict
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def register_active_bindings(
        self,
        version_id: UUID,
        bindings: Sequence[FrozenCapabilityBinding],
    ) -> None:
        with self._lock:
            self.active_bindings_by_version[version_id] = tuple(bindings)

    def restore_active_bindings(
        self,
        bindings_by_version: Mapping[UUID, Sequence[FrozenCapabilityBinding]],
    ) -> None:
        """Replace the active binding projection under the provider lock."""
        with self._lock:
            self.active_bindings_by_version.clear()
            self.active_bindings_by_version.update(
                {
                    version_id: tuple(bindings)
                    for version_id, bindings in bindings_by_version.items()
                }
            )

    def active_bindings_for_manifest(
        self,
        manifest: ResolvedRunManifestRevision,
    ) -> tuple[FrozenCapabilityBinding, ...]:
        """Return already-registered bindings for active versions, deterministically."""
        active_ids = sorted(
            (skill.version_id for skill in manifest.active_skills),
            key=lambda item: item.bytes,
        )
        with self._lock:
            return tuple(
                binding
                for version_id in active_ids
                for binding in self.active_bindings_by_version.get(version_id, ())
            )

    def resolve(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        scope: ProviderExecutionScope,
        locale: str,
    ) -> Any:
        if scope.run_id != manifest.run_id:
            raise ValueError("tools provider scope run_id must match manifest.run_id")
        active_ids = {s.version_id for s in manifest.active_skills}
        with self._lock:
            visible_bindings: list[FrozenCapabilityBinding] = list(self.control_bindings)
            for version_id in active_ids:
                visible_bindings.extend(
                    self.active_bindings_by_version.get(version_id, ())
                )

        # Intersect with Manifest capability digests when present.
        if manifest.capabilities:
            allowed = {c.binding_contract_digest for c in manifest.capabilities}
            visible_bindings = [
                b
                for b in visible_bindings
                if b.ref.binding_contract_digest in allowed
            ]

        session = self.session_factory()
        try:
            # Describe does not consume evidence; empty verifier map is fine for describe.
            gateway = build_capability_runtime(
                db=session,
                evidence_verifiers={},
                locale=locale or self.locale,
                classifier=self.classifier,
                main_agent_control_port=self.control_port,
            )
            pairs: list[tuple[FrozenCapabilityBinding, CapabilityDescriptor]] = []
            for binding in visible_bindings:
                try:
                    descriptor = gateway.describe(binding)
                except Exception:
                    continue
                if descriptor.availability.status != "available":
                    continue
                pairs.append((binding, descriptor))
        finally:
            session.close()

        return build_provider_tool_surface(
            manifest=manifest,
            provider_protocol=self.provider_protocol,
            visible=pairs,
            scope=scope,
        )


# ---------------------------------------------------------------------------
# Current descriptor verifier with control port
# ---------------------------------------------------------------------------


@dataclass
class MainAgentCurrentDescriptorVerifier:
    """Re-describe verifier with Main Agent control port bound."""

    session_factory: SessionFactory
    control_port: MainAgentControlCallPort
    locale: str | None = None
    classifier: CapabilityClassifier | None = None

    def require_current(
        self,
        *,
        binding: FrozenCapabilityBinding,
        exposed_descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityDescriptor:
        del scope
        session = self.session_factory()
        try:
            gateway = build_capability_runtime(
                db=session,
                evidence_verifiers={},
                locale=self.locale,
                classifier=self.classifier,
                main_agent_control_port=self.control_port,
            )
            current = gateway.describe(binding)
            if not descriptors_equal_for_freshness(
                exposed=exposed_descriptor,
                current=current,
            ):
                raise ClassificationDriftError()
            return current
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Snapshot / ledger builders
# ---------------------------------------------------------------------------


def build_control_exposure_inputs(
    *,
    control_bindings: Sequence[FrozenCapabilityBinding],
    control_port: MainAgentControlCallPort,
    session: Session,
    profile_key: str,
    profile_version_id: UUID,
    locale: str | None = "en",
) -> list[ExposureBindingInput]:
    """Describe base controls and build ExposureBindingInput list."""
    gateway = build_capability_runtime(
        db=session,
        evidence_verifiers={},
        locale=locale,
        main_agent_control_port=control_port,
    )
    inputs: list[ExposureBindingInput] = []
    for binding in control_bindings:
        descriptor = gateway.describe(binding)
        owner = resolve_owner_from_binding(
            binding,
            profile_key=profile_key,
            profile_version_id=profile_version_id,
        )
        inputs.append(
            ExposureBindingInput(
                binding=binding,
                descriptor=descriptor,
                owner=owner,
            )
        )
    return inputs


def build_main_agent_owner_material(
    *,
    profile_key: str,
    profile_version_id: UUID,
    profile_content_digest: str,
    control_keys: Sequence[str] = MAIN_AGENT_CONTROL_KEYS,
) -> OwnerGrantMaterial:
    policy_digest = compute_owner_policy_digest(
        owner_kind="main_agent",
        owner_id=profile_key,
        owner_version_id=profile_version_id,
        content_or_policy_digest=profile_content_digest,
        allowed_side_effects=("none", "read", "compute"),
    )
    return OwnerGrantMaterial(
        owner_kind="main_agent",
        owner_id=profile_key,
        owner_version_id=profile_version_id,
        policy_digest=policy_digest,
        author_allowed_side_effects=("none", "read", "compute"),
        declared_capability_keys=frozenset(control_keys),
        is_instruction_only=False,
    )


def build_initial_policy_snapshot(
    *,
    run_id: UUID,
    app_build_revision: str,
    profile_key: str,
    profile_version_id: UUID,
    profile_content_digest: str,
    manifest: ResolvedRunManifestRevision,
    exposure_inputs: Sequence[ExposureBindingInput],
    run_budget_limits: RunBudgetLimits,
    owner_material: OwnerGrantMaterial,
) -> EffectiveRunPolicySnapshot:
    exposure_index = build_manifest_exposure_index_from_inputs(
        manifest=manifest,
        binding_inputs=exposure_inputs,
        profile_key=profile_key,
        allow_empty=False,
    )
    owner_ref = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id=profile_key,
        owner_version_id=profile_version_id,
        content_or_policy_digest=owner_material.policy_digest,
        allowed_side_effects=tuple(owner_material.author_allowed_side_effects),
    )
    return build_effective_run_policy_snapshot(
        app_build_revision=app_build_revision,
        run_id=run_id,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=profile_version_id,
        main_agent_profile_digest=profile_content_digest,
        exposure_index=exposure_index,
        owner_policy_refs=(owner_ref,),
        run_budget_limits=run_budget_limits,
    )


def build_main_agent_owner_budget(
    *,
    profile_version_id: UUID,
    run_limits: RunBudgetLimits,
) -> OwnerBudgetLimits:
    return normalize_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=profile_version_id,
        run_limits=run_limits,
    )


# ---------------------------------------------------------------------------
# Full per-Run composition
# ---------------------------------------------------------------------------


def compose_main_agent_policy_runtime(
    *,
    db: Session,
    run_id: UUID,
    conversation_id: UUID,
    manifest: ResolvedRunManifestRevision,
    profile_key: str,
    profile_version_id: UUID,
    profile_content_digest: str,
    app_build_revision: str,
    provider: ProviderAdapter,
    events: ProviderLoopEventSink | None = None,
    locale: str | None = "en",
    cancel_checker: Callable[[], bool] | None = None,
    catalog_state: Any | None = None,
    inject_handler: Callable[..., Any] | None = None,
    resource_handler: Callable[..., Any] | None = None,
    artifact_handler: Callable[..., Any] | None = None,
    session_factory: SessionFactory | None = None,
    profile_budget_fields: Any | None = None,
    profile_context_budget: Any | None = None,
    operator_budget_limits: Mapping[str, int | None] | None = None,
    isolated_parallel: bool = False,
    durable_workflow: Any | None = None,
    restored_policy_snapshot: EffectiveRunPolicySnapshot | None = None,
    restored_budget_state: Any | None = None,
    restored_obligation_state: Any | None = None,
    capability_ledger_mode: str = "legacy_read_only",
    capability_ledger: CapabilityLedgerAggregatePort | None = None,
    policy_contract_version: int = 1,
    golden_write_release: Any | None = None,
    admission_context_resolver: Any | None = None,
    capability_ledger_lease: Any | None = None,
    capability_ledger_idempotency_secret: str | bytes | None = None,
) -> tuple[MainAgentPolicyRuntime, ProviderLoopPorts]:
    """Compose Plan 05 policy ledgers + ProviderLoopPorts for one admitted Run.

    When composition cannot complete (missing session factory for control
    describe), raises RuntimeError with a safe code — caller falls back.

    ``profile_budget_fields`` is the Profile outputBudget (or mapping).
    ``profile_context_budget`` supplies ``max_active_skills`` (contextBudget).
    ``operator_budget_limits`` may lower ceilings only (e.g. settings max_active_skills).
    """
    control_bindings = build_all_main_agent_control_bindings(
        owner_version_id=profile_version_id,
        source_snapshot_digest=profile_content_digest,
        app_build_revision=app_build_revision,
    )

    # Control runtime (shared across Gateway sessions for this Run).
    control_runtime = MainAgentControlRuntime(
        catalog_state=catalog_state,
        current_manifest=manifest,
        inject_handler=inject_handler,
        resource_handler=resource_handler,
        artifact_handler=artifact_handler,
    )

    # Run budget limits: hard ∩ entrypoint ∩ operator lower-only ∩ profile
    # outputBudget + contextBudget.max_active_skills.
    run_budget_limits = (
        restored_policy_snapshot.run_budget_limits
        if restored_policy_snapshot is not None
        else normalize_run_budget_limits(
            profile_output_budget=profile_budget_fields,
            profile_context_budget=profile_context_budget,
            operator_limits=operator_budget_limits,
        )
    )
    owner_budget = build_main_agent_owner_budget(
        profile_version_id=profile_version_id,
        run_limits=run_budget_limits,
    )

    # Exposure inputs require Gateway.describe with control port.
    exposure_inputs = build_control_exposure_inputs(
        control_bindings=control_bindings,
        control_port=control_runtime,
        session=db,
        profile_key=profile_key,
        profile_version_id=profile_version_id,
        locale=locale,
    )

    owner_material = build_main_agent_owner_material(
        profile_key=profile_key,
        profile_version_id=profile_version_id,
        profile_content_digest=profile_content_digest,
        control_keys=tuple(b.ref.capability_key for b in control_bindings),
    )

    # Build Plan 05 policy snapshot, then rebuild the base Manifest so
    # Manifest.effective_policy_digest == snapshot.effective_policy_digest.
    # Exposure-index digest no longer embeds manifest_digest, so this is
    # cycle-free: policy digest → Manifest.effective_policy_digest →
    # Manifest.manifest_digest, with exposure_index re-associated to the
    # new Manifest digest without changing exposure_index_digest.
    if restored_policy_snapshot is not None:
        policy_snapshot = restored_policy_snapshot
        if policy_snapshot.run_id != run_id:
            raise ValueError("restored policy run_id mismatch")
        if policy_snapshot.app_build_revision != app_build_revision:
            raise ValueError("restored policy app build mismatch")
        if policy_snapshot.main_agent_profile_version_id != profile_version_id:
            raise ValueError("restored policy profile version mismatch")
        if manifest.effective_policy_digest != policy_snapshot.effective_policy_digest:
            raise ValueError("restored policy Manifest digest mismatch")
    else:
        policy_snapshot = build_initial_policy_snapshot(
            run_id=run_id,
            app_build_revision=app_build_revision,
            profile_key=profile_key,
            profile_version_id=profile_version_id,
            profile_content_digest=profile_content_digest,
            manifest=manifest,
            exposure_inputs=exposure_inputs,
            run_budget_limits=run_budget_limits,
            owner_material=owner_material,
        )
    if (
        restored_policy_snapshot is None
        and manifest.effective_policy_digest != policy_snapshot.effective_policy_digest
    ):
        from app.assistant.domain.contracts import compute_manifest_digest

        aligned_digest = compute_manifest_digest(
            run_id=manifest.run_id,
            revision=manifest.revision,
            parent_digest=manifest.parent_digest,
            main_agent=manifest.main_agent,
            active_skills=manifest.active_skills,
            capabilities=manifest.capabilities,
            provider=manifest.provider,
            model=manifest.model,
            provider_aliases=manifest.provider_aliases,
            effective_policy_digest=policy_snapshot.effective_policy_digest,
        )
        manifest = ResolvedRunManifestRevision(
            run_id=manifest.run_id,
            revision=manifest.revision,
            parent_digest=manifest.parent_digest,
            main_agent=manifest.main_agent,
            active_skills=manifest.active_skills,
            capabilities=manifest.capabilities,
            provider=manifest.provider,
            model=manifest.model,
            provider_aliases=manifest.provider_aliases,
            effective_policy_digest=policy_snapshot.effective_policy_digest,
            manifest_digest=aligned_digest,
        )
        # Rebuild snapshot so exposure_index.manifest_digest association matches.
        policy_snapshot = build_initial_policy_snapshot(
            run_id=run_id,
            app_build_revision=app_build_revision,
            profile_key=profile_key,
            profile_version_id=profile_version_id,
            profile_content_digest=profile_content_digest,
            manifest=manifest,
            exposure_inputs=exposure_inputs,
            run_budget_limits=run_budget_limits,
            owner_material=owner_material,
        )
        # Keep control runtime on the aligned Manifest.
        control_runtime.bind_manifest(manifest)

    if restored_budget_state is not None:
        if restored_budget_state.limits != run_budget_limits:
            raise ValueError("restored budget limits mismatch")
        budget_ledger = BudgetLedger(restored_budget_state)
    else:
        budget_ledger = BudgetLedger.create(
            limits=run_budget_limits,
            owner_limits=(owner_budget,),
        )
    if restored_obligation_state is not None:
        obligation_ledger = ObligationLedger(
            restored_obligation_state,
            run_id=run_id,
        )
    else:
        # create() installs the main-agent terminal obligation by default.
        obligation_ledger = ObligationLedger.create(run_id=run_id)

    call_frames = ProcessLocalCapabilityCallFramePort()
    dispatch_guard = BudgetLedgerDispatchGuard(ledger=budget_ledger)
    round_budget_guard = BudgetLedgerRoundGuard(ledger=budget_ledger)
    call_reservation = BudgetLedgerReservationPort(ledger=budget_ledger)
    def _budget_start_followup() -> None:
        decision = budget_ledger.start_completion_followup()
        if not decision.allowed:
            raise RuntimeError(decision.reason_code or "budget_exhausted")

    def _can_continue_completion() -> bool:
        # Follow-up only when completion tokens, provider rounds, and wall
        # deadline all still have headroom. Avoid burning a followup slot that
        # will immediately fail the next provider round.
        snap = budget_ledger.snapshot()
        remaining_tokens = budget_ledger.remaining_completion_tokens()
        if remaining_tokens is not None and remaining_tokens < 1:
            return False
        if snap.provider_rounds_started >= snap.limits.max_provider_rounds:
            return False
        if budget_ledger.remaining_wall_time_ms() < 1:
            return False
        return True

    completion_guard = ObligationLedgerCompletionGuard(
        obligation_ledger=obligation_ledger,
        locale=locale or "en",
        max_completion_followup_rounds=run_budget_limits.max_completion_followup_rounds,
        can_continue_fn=_can_continue_completion,
        budget_start_followup_fn=_budget_start_followup,
    )

    owner_materials: dict[tuple[str, str, UUID], OwnerGrantMaterial] = {
        (
            owner_material.owner_kind,
            owner_material.owner_id,
            owner_material.owner_version_id,
        ): owner_material
    }
    owners_by_domain_key: dict[str, tuple[str, UUID]] = {
        key: ("main_agent", profile_version_id) for key in MAIN_AGENT_CONTROL_KEYS
    }

    scope = create_execution_scope(
        run_id=run_id,
        conversation_id=conversation_id,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        tenant_scope_id=None,
    )
    auth_factory = MainAgentAuthorizationEvidenceFactory(
        scope=scope,
        manifest=manifest,
        profile_key=profile_key,
        profile_content_digest=profile_content_digest,
        policy_snapshot=policy_snapshot,
        owner_materials=owner_materials,
        policy_contract_version=policy_contract_version,
        golden_write_release=golden_write_release,
        admission_context_resolver=admission_context_resolver,
    )

    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(
        budget_ledger=budget_ledger,
        obligation_ledger=obligation_ledger,
        policy_snapshot=policy_snapshot,
    )
    lifecycle.register_policy_snapshot(policy_snapshot)

    # Session factory for Gateway open per dispatch/describe.
    if session_factory is None:
        # Fall back to a factory that reuses the same Session identity class
        # but opens new sessions from the same engine when possible.
        bind = db.get_bind()

        def _factory() -> Session:
            return Session(bind=bind)

        session_factory = _factory

    tools_provider = MainAgentGatewayToolsProvider(
        session_factory=session_factory,
        control_bindings=control_bindings,
        control_port=control_runtime,
        authorization_factory=auth_factory,
        locale=locale,
    )
    current_descriptors = MainAgentCurrentDescriptorVerifier(
        session_factory=session_factory,
        control_port=control_runtime,
        locale=locale,
    )

    def _discard_lifecycle_effect(call_id: str, reason_code: str) -> None:
        lifecycle.discard(call_id=call_id, reason_code=reason_code)

    def _cleanup_pending_effect(call_id: str, reason_code: str) -> None:
        try:
            control_runtime.discard_pending(call_id=call_id)
        finally:
            lifecycle.discard(call_id=call_id, reason_code=reason_code)

    def _next_manifest(
        request: ProviderDispatchRequest, result: CapabilityResult
    ) -> ResolvedRunManifestRevision:
        return next_manifest_from_control_effect(
            control_runtime,
            request,
            result,
            discard_effect=_discard_lifecycle_effect,
        )

    gateway_dispatcher = MainAgentGatewayToolDispatcher(
        session_factory=session_factory,
        authorization_factory=auth_factory,
        control_port=control_runtime,
        durable_workflow=durable_workflow,
        locale=locale,
        next_manifest_hook=_next_manifest,
        pending_effect_cleanup_hook=_cleanup_pending_effect,
        dispatch_guard=dispatch_guard,  # dual-wire: also on ProviderLoopPorts
        call_frames=call_frames,
        obligation_ledger=obligation_ledger,
    )
    if capability_ledger_mode == "enforced":
        if capability_ledger is None and capability_ledger_lease is not None:
            if not capability_ledger_idempotency_secret:
                raise RuntimeError(
                    "enforced capability ledger requires idempotency secret"
                )
            from app.assistant.capability_calls.aggregate import (
                DurableCapabilityLedgerAggregate,
            )

            capability_ledger = DurableCapabilityLedgerAggregate(
                db=db,
                authorization_factory=auth_factory,
                idempotency_secret=capability_ledger_idempotency_secret,
                lease=capability_ledger_lease,
            )
        if capability_ledger is None:
            raise RuntimeError(
                "enforced capability ledger requires durable aggregate port"
            )
        from app.assistant.capability_calls.dispatcher import LedgerDispatcher

        tool_dispatcher: Any = LedgerDispatcher(
            inner=gateway_dispatcher,
            aggregate=capability_ledger,
        )
    elif capability_ledger_mode == "legacy_read_only":
        tool_dispatcher = gateway_dispatcher
    else:
        raise ValueError(
            "capability_ledger_mode must be legacy_read_only or enforced"
        )

    if isolated_parallel:
        sibling_executor: Any = BoundedIsolatedSiblingExecutor(max_workers=4)
    else:
        sibling_executor = SeqExecutor()

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

    cancellation: CancellationPort = _CancelBridge(cancel_checker)  # type: ignore[assignment]

    # One shared resolver instance for runtime + ports so rebind_owners after
    # skill.inject accept updates reservation ownership without rebuilding ports.
    owner_resolver = DomainKeyOwnerResolver(
        owners_by_domain_key=owners_by_domain_key,
        default_owner_kind="main_agent",
        default_owner_version_id=profile_version_id,
    )

    ports = ProviderLoopPorts(
        provider=provider,
        tools_provider=tools_provider,  # type: ignore[arg-type]
        current_descriptors=current_descriptors,  # type: ignore[arg-type]
        authorization_evidence=auth_factory,  # type: ignore[arg-type]
        tool_dispatcher=tool_dispatcher,  # type: ignore[arg-type]
        sibling_executor=sibling_executor,
        cancellation=cancellation,
        events=events or _NullEventSink(),  # type: ignore[arg-type]
        capability_ledger=capability_ledger if capability_ledger_mode == "enforced" else None,
        manifest_effect_lifecycle=lifecycle,  # type: ignore[arg-type]
        round_budget_guard=round_budget_guard,
        call_reservation=call_reservation,
        call_owner_resolver=owner_resolver,
        dispatch_guard=dispatch_guard,  # dual-wire with tool_dispatcher
        call_frames=call_frames,
        completion_guard=completion_guard,
    )

    runtime = MainAgentPolicyRuntime(
        run_id=run_id,
        conversation_id=conversation_id,
        manifest=manifest,
        policy_snapshot=policy_snapshot,
        budget_ledger=budget_ledger,
        obligation_ledger=obligation_ledger,
        call_frames=call_frames,
        dispatch_guard=dispatch_guard,
        round_budget_guard=round_budget_guard,
        call_reservation=call_reservation,
        completion_guard=completion_guard,
        authorization_factory=auth_factory,
        lifecycle=lifecycle,
        control_runtime=control_runtime,
        control_bindings=control_bindings,
        tools_provider=tools_provider,
        owner_materials=owner_materials,
        owners_by_domain_key=owners_by_domain_key,
        run_budget_limits=run_budget_limits,
        app_build_revision=app_build_revision,
        profile_key=profile_key,
        profile_version_id=profile_version_id,
        profile_content_digest=profile_content_digest,
    )
    # Replace the post_init-built resolver with the shared ports instance.
    runtime._owner_resolver = owner_resolver  # noqa: SLF001

    # Plan 05 enablement: install production skill.inject handler + accept rebind
    # when the caller did not inject a test/scripted handler.
    from app.assistant.main_agent.inject_wiring import (
        build_production_inject_handler,
        install_accept_rebind_hooks,
    )

    install_accept_rebind_hooks(runtime=runtime, tools_provider=tools_provider)
    if inject_handler is None:
        production_inject = build_production_inject_handler(
            runtime=runtime,
            tools_provider=tools_provider,
            session_factory=session_factory,
            catalog_state=catalog_state,
            locale=locale,
        )
        control_runtime._inject_handler = production_inject  # noqa: SLF001
    return runtime, ports


class _NullEventSink:
    def emit(self, event_type: str, payload: dict[str, JsonValue]) -> None:
        del event_type, payload


def skill_injection_policy_context_from_runtime(
    runtime: MainAgentPolicyRuntime,
) -> SkillInjectionPolicyContext:
    """Build SkillInjectionPolicyContext from the live Run policy runtime."""
    snap = runtime.budget_ledger.snapshot()
    # BudgetLedgerState field is completion_followups_started (not
    # completion_followup_rounds_started — that name does not exist).
    remaining_rounds = max(
        0,
        snap.limits.max_provider_rounds
        - snap.provider_rounds_started
        - snap.completion_followups_started,
    )
    active_candidates = dict(
        getattr(runtime, "active_skill_candidates_by_version", {}) or {}
    )
    ordered_active = sorted(active_candidates.items(), key=lambda item: item[0].bytes)
    active_conflict_rules = tuple(
        (version_id, tuple(candidate.conflict_rules))
        for version_id, candidate in ordered_active
    )
    active_aliases = tuple(
        (version_id, tuple(candidate.aliases))
        for version_id, candidate in ordered_active
    )

    existing_exposures: list[CandidateExposureView] = []
    policy_snapshot = getattr(runtime, "policy_snapshot", None)
    exposure_index = getattr(policy_snapshot, "exposure_index", None)
    for exposure in getattr(exposure_index, "exposures", ()) or ():
        owner_candidate = active_candidates.get(exposure.owner_version_id)
        owner_view = None
        if owner_candidate is not None:
            owner_view = next(
                (
                    view
                    for view in owner_candidate.exposure_views
                    if view.domain_key == exposure.domain_key
                ),
                None,
            )
        existing_exposures.append(
            CandidateExposureView(
                domain_key=exposure.domain_key,
                resolved_ref=exposure.resolved_ref,
                binding_contract_digest=exposure.binding_contract_digest,
                descriptor_digest=exposure.descriptor_digest,
                max_skill_calls=(
                    owner_candidate.max_skill_calls
                    if owner_candidate is not None
                    and exposure.owner_kind == "skill_version"
                    else None
                ),
                max_same_read_calls=(
                    owner_candidate.max_same_read_calls
                    if owner_candidate is not None
                    and exposure.owner_kind == "skill_version"
                    else None
                ),
                requires_terminal_output=(
                    owner_candidate.requires_terminal_output
                    if owner_candidate is not None
                    and exposure.owner_kind == "skill_version"
                    else None
                ),
                terminal_text_allowed=(
                    owner_candidate.terminal_text_allowed
                    if owner_candidate is not None
                    and exposure.owner_kind == "skill_version"
                    else None
                ),
                grant_admits_side_effect=(
                    owner_view.grant_admits_side_effect
                    if owner_view is not None
                    else None
                ),
                descriptor_fields_frozen=bool(
                    owner_view is not None
                    and owner_view.descriptor_fields_frozen
                ),
                side_effect=(
                    owner_view.side_effect if owner_view is not None else "read"
                ),
                executable_revision=(
                    owner_view.executable_revision
                    if owner_view is not None
                    else ""
                ),
                timeout_mode=(
                    owner_view.timeout_mode if owner_view is not None else "none"
                ),
                timeout_seconds=(
                    owner_view.timeout_seconds if owner_view is not None else None
                ),
                interrupt_mode=(
                    owner_view.interrupt_mode if owner_view is not None else "none"
                ),
                parallel_safe=(
                    owner_view.parallel_safe if owner_view is not None else True
                ),
                terminal_output=(
                    owner_view.terminal_output if owner_view is not None else False
                ),
                needs_followup=(
                    owner_view.needs_followup if owner_view is not None else True
                ),
                followup_hint=(
                    owner_view.followup_hint if owner_view is not None else None
                ),
            )
        )

    return SkillInjectionPolicyContext(
        run_max_total_capability_calls=runtime.run_budget_limits.max_total_capability_calls,
        run_max_same_read_signature=runtime.run_budget_limits.max_same_read_signature,
        run_max_active_skills=runtime.run_budget_limits.max_active_skills,
        remaining_provider_slots=remaining_rounds,
        active_conflict_rules=active_conflict_rules,
        active_aliases=active_aliases,
        existing_exposures=tuple(existing_exposures),
    )


__all__ = [
    "MainAgentCurrentDescriptorVerifier",
    "MainAgentGatewayToolDispatcher",
    "MainAgentGatewayToolsProvider",
    "MainAgentPolicyRuntime",
    "build_control_exposure_inputs",
    "build_initial_policy_snapshot",
    "build_main_agent_owner_budget",
    "build_main_agent_owner_material",
    "compose_main_agent_policy_runtime",
    "skill_injection_policy_context_from_runtime",
]
