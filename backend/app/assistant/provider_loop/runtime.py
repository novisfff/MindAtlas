"""Internal/test-only Provider Loop ↔ Capability Gateway composition (Plan 03 Task 9).

This module is deliberately not a production assistant entrypoint. It wires:

- a grant-filtered ``ToolsProvider`` that describes exact frozen bindings through
  Plan 02 ``CapabilityGateway.describe`` before every Provider round;
- a short-lived Gateway ``CurrentCapabilityDescriptorVerifier``;
- an ``issuer=test`` authorization-evidence factory (no production
  ``main_agent`` verifier);
- a Gateway ``ToolDispatcher`` that re-describes immediately before execute and
  never imports Tool/Workflow/Agent adapters directly.

Sessions and Gateway instances are request-scoped and closed before frozen data
or dispatch results leave these ports.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capabilities.classification import CapabilityClassifier
from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityMetrics,
    CapabilityOwnerRef,
    CapabilityResult,
    EvidenceVerifierKey,
    FrozenCapabilityBinding,
    VerifiedAuthorizationEvidence,
)
from app.assistant.capabilities.gateway import CapabilityGateway
from app.assistant.capabilities.policy import (
    AtomicSingleUseDispatchPermit,
    AuthorizationEvidenceVerificationError,
    AuthorizationEvidenceVerifier,
)
from app.assistant.capabilities.ports import CapabilityRuntimePorts
from app.assistant.capabilities.runtime import build_capability_runtime
from app.assistant.domain.contracts import (
    ModelRef,
    ResolvedCapabilityRef,
    ResolvedRunManifestRevision,
    ResolvedSkillRef,
    append_skill_activation,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.provider_loop.aliases import (
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (
    CancellationPort,
    CurrentCapabilityDescriptorVerifier,
    ProviderAuthorizationEvidenceFactory,
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderExecutionScope,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderLoopResult,
    ProviderToolCall,
    ToolDispatcher,
    ToolSurfaceResolution,
    ToolsProvider,
)
from app.assistant.provider_loop.loop import run_provider_agent_loop
from app.assistant.provider_loop.scheduler import (
    BoundedIsolatedSiblingExecutor,
    SequentialSiblingExecutor,
)

SessionFactory = Callable[[], Session]
GatewayFactory = Callable[..., CapabilityGateway]


class ClassificationDriftError(RuntimeError):
    """Raised when current Gateway.describe digests diverge from the exposed surface."""

    def __init__(self, *, safe_code: str = "classification_changed") -> None:
        self.safe_code = safe_code
        super().__init__(safe_code)


def capability_ref_from_binding(binding: FrozenCapabilityBinding) -> ResolvedCapabilityRef:
    """Project a frozen binding into the Manifest capability-ref slot."""
    return ResolvedCapabilityRef(
        capability_type=binding.ref.capability_type,
        capability_key=binding.ref.capability_key,
        target_identity=binding.ref.target_identity,
        target_id=binding.ref.target_id,
        target_version_id=binding.ref.target_version_id,
        target_revision=binding.ref.target_revision,
        input_schema_digest=binding.ref.input_schema_digest,
        output_schema_digest=binding.ref.output_schema_digest,
        resolution_digest=binding.ref.resolution_digest,
        dependency_closure_digest=binding.ref.dependency_closure_digest,
        binding_contract_digest=binding.ref.binding_contract_digest,
    )


def append_test_capability_grant(
    current: ResolvedRunManifestRevision,
    *,
    binding: FrozenCapabilityBinding,
    skill: ResolvedSkillRef | None = None,
) -> ResolvedRunManifestRevision:
    """Append one exact capability grant onto a Manifest via skill-activation lineage.

    Named generically so tests never invent a production Skill-control capability.
    """
    capability = capability_ref_from_binding(binding)
    if skill is None:
        # Synthetic owner skill identity for test-only grant lineage. Digests are
        # deterministic from the binding so re-applying the same grant is stable.
        seed = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "kind": "test_grant_skill",
                "capabilityKey": binding.ref.capability_key,
                "bindingDigest": binding.ref.binding_contract_digest,
            }
        )
        namespace = UUID("00000000-0000-4000-8000-00000000t9s1".replace("t9s1", "0001"))
        skill = ResolvedSkillRef(
            package_id=uuid.uuid5(namespace, f"pkg:{seed}"),
            version_id=uuid.uuid5(namespace, f"ver:{seed}"),
            canonical_name=f"test.grant.{binding.ref.capability_key}",
            sequence=1,
            content_digest=seed,
            version_digest=seed,
            requested_name_normalized=None,
            resolved_via_alias_id=None,
        )
    return append_skill_activation(current, skill=skill, capabilities=(capability,))


def descriptors_equal_for_freshness(
    *,
    exposed: CapabilityDescriptor,
    current: CapabilityDescriptor,
) -> bool:
    """Strict Plan 03 freshness equality (no silent upgrade/downgrade)."""
    return (
        current.descriptor_digest == exposed.descriptor_digest
        and current.behavior.behavior_digest == exposed.behavior.behavior_digest
        and current.behavior.classification.revision
        == exposed.behavior.classification.revision
        and current.behavior.classification.ruleset_digest
        == exposed.behavior.classification.ruleset_digest
        and current.availability.status == exposed.availability.status
        and current.behavior.parallel_safe == exposed.behavior.parallel_safe
        and current.behavior.side_effect == exposed.behavior.side_effect
        and current.behavior.interrupt_mode == exposed.behavior.interrupt_mode
        and current.behavior.timeout_policy.mode == exposed.behavior.timeout_policy.mode
        and current.behavior.timeout_policy.timeout_seconds
        == exposed.behavior.timeout_policy.timeout_seconds
        and current.behavior.timeout_policy.cancellation_supported
        == exposed.behavior.timeout_policy.cancellation_supported
    )


@dataclass
class TestGrantRegistry:
    """Mutable exact-grant catalog for internal/test ToolsProvider rounds.

    Never queries "all published Skills". Callers append grants explicitly.
    """

    _by_key: MutableMapping[str, FrozenCapabilityBinding] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def put(self, binding: FrozenCapabilityBinding) -> None:
        if not isinstance(binding, FrozenCapabilityBinding):
            raise TypeError("binding must be a FrozenCapabilityBinding")
        with self._lock:
            self._by_key[binding.ref.capability_key] = binding

    def get(self, capability_key: str) -> FrozenCapabilityBinding | None:
        with self._lock:
            return self._by_key.get(capability_key)

    def snapshot(self) -> tuple[FrozenCapabilityBinding, ...]:
        with self._lock:
            return tuple(
                self._by_key[key]
                for key in sorted(self._by_key.keys())
            )

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._by_key.keys()))


@dataclass
class _SessionRecord:
    session_id: str
    closed: bool = False
    session: Session | None = None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.session is not None:
            try:
                self.session.close()
            finally:
                self.session = None


class TrackingSessionFactory:
    """Wraps a Session factory and records open/close identity for isolation tests."""

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self.opened: list[_SessionRecord] = []
        self._lock = threading.Lock()

    def __call__(self) -> Session:
        session = self._factory()
        record = _SessionRecord(session_id=str(uuid.uuid4()), session=session)
        # Attach identity for tests that inspect the live Session.
        setattr(session, "provider_loop_session_id", record.session_id)

        original_close = session.close

        def _close() -> None:
            record.closed = True
            record.session = None
            original_close()

        session.close = _close  # type: ignore[method-assign]
        with self._lock:
            self.opened.append(record)
        return session

    @property
    def open_count(self) -> int:
        return len(self.opened)

    @property
    def all_closed(self) -> bool:
        return all(item.closed for item in self.opened)

    def session_ids(self) -> list[str]:
        return [item.session_id for item in self.opened]


@dataclass
class TestAuthorizationEvidenceVerifier:
    """Request-scoped single-use verifier for ``issuer=test`` / ``entrypoint=test`` only.

    Production ``main_agent`` remains denied by the policy engine because no
    verifier is registered for that entrypoint here.
    """

    allowed_side_effects: tuple[str, ...] = ("none", "compute", "read")
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _consumed_digests: set[str] = field(default_factory=set, repr=False)

    def verify(
        self,
        *,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
        context: CapabilityExecutionContext,
    ) -> VerifiedAuthorizationEvidence:
        del descriptor  # identity already enforced by policy before/after verify
        self.calls.append(
            {
                "call_id": evidence.call_id,
                "entrypoint": evidence.entrypoint,
                "issuer": evidence.issuer,
                "capability_key": evidence.capability_key,
                "context_call_id": context.call_id,
            }
        )
        if evidence.issuer != "test" or evidence.entrypoint != "test":
            raise AuthorizationEvidenceVerificationError("unknown_issuer_entrypoint")
        if evidence.call_id != context.call_id:
            raise AuthorizationEvidenceVerificationError("call_id_mismatch")
        with self._lock:
            if evidence.evidence_digest in self._consumed_digests:
                raise AuthorizationEvidenceVerificationError("evidence_already_consumed")
            self._consumed_digests.add(evidence.evidence_digest)
        return VerifiedAuthorizationEvidence(
            call_id=evidence.call_id,
            verifier_key=(evidence.issuer, evidence.entrypoint),
            verifier_instance_id=self.instance_id,
            principal=evidence.principal,
            entrypoint=evidence.entrypoint,
            owner=evidence.owner,
            capability_key=evidence.capability_key,
            resolution_digest=evidence.resolution_digest,
            binding_contract_digest=evidence.binding_contract_digest,
            dependency_closure_digest=evidence.dependency_closure_digest,
            allowed_side_effects=tuple(evidence.allowed_side_effects),  # type: ignore[arg-type]
            grant_source_digest=evidence.grant_source_digest,
            evidence_digest=evidence.evidence_digest,
            verification_digest=sha256_canonical_json(
                {
                    "callId": evidence.call_id,
                    "verifierInstanceId": self.instance_id,
                    "evidenceDigest": evidence.evidence_digest,
                }
            ),
            dispatch_permit=AtomicSingleUseDispatchPermit(),
        )


def default_test_evidence_verifiers(
    *,
    allowed_side_effects: tuple[str, ...] = ("none", "compute", "read"),
) -> dict[EvidenceVerifierKey, AuthorizationEvidenceVerifier]:
    return {("test", "test"): TestAuthorizationEvidenceVerifier(
        allowed_side_effects=allowed_side_effects
    )}


def _open_gateway(
    *,
    session_factory: SessionFactory,
    locale: str | None,
    classifier: CapabilityClassifier | None,
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier],
) -> tuple[Session, CapabilityGateway]:
    session = session_factory()
    try:
        gateway = build_capability_runtime(
            db=session,
            evidence_verifiers=evidence_verifiers,
            locale=locale,
            classifier=classifier,
        )
    except Exception:
        session.close()
        raise
    return session, gateway


@dataclass
class GatewayCurrentDescriptorVerifier:
    """Plan 02 re-describe verifier with a short-lived Session per call."""

    session_factory: SessionFactory
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier] = field(
        default_factory=default_test_evidence_verifiers
    )
    classifier: CapabilityClassifier | None = None
    locale: str | None = None
    describe_calls: list[dict[str, Any]] = field(default_factory=list)
    gateway_ids: list[int] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def require_current(
        self,
        *,
        binding: FrozenCapabilityBinding,
        exposed_descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityDescriptor:
        del scope
        session, gateway = _open_gateway(
            session_factory=self.session_factory,
            locale=self.locale,
            classifier=self.classifier,
            evidence_verifiers=self.evidence_verifiers,
        )
        session_id = getattr(session, "provider_loop_session_id", None) or str(id(session))
        try:
            with self._lock:
                self.gateway_ids.append(id(gateway))
                self.session_ids.append(session_id)
                self.describe_calls.append(
                    {
                        "binding_digest": binding.ref.binding_contract_digest,
                        "exposed_descriptor_digest": exposed_descriptor.descriptor_digest,
                        "session_id": session_id,
                        "gateway_id": id(gateway),
                    }
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


@dataclass
class TestAuthorizationEvidenceFactory:
    """Issues ``issuer=test`` / ``entrypoint=test`` evidence only."""

    owner: CapabilityOwnerRef = field(
        default_factory=lambda: CapabilityOwnerRef(
            owner_kind="test",
            owner_id="provider-loop-test",
            owner_version_id=None,
        )
    )
    allowed_side_effects: tuple[str, ...] = ("none", "compute", "read")
    grant_source_digest: str = field(
        default_factory=lambda: sha256_canonical_json(
            {"schemaVersion": 1, "kind": "test_grant_source"}
        )
    )
    issued: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _counter: int = 0

    def issue(
        self,
        *,
        call: ProviderToolCall,
        binding: FrozenCapabilityBinding,
        descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityAuthorizationEvidence:
        del descriptor
        with self._lock:
            self._counter += 1
            counter = self._counter
        evidence_digest = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "kind": "test_authorization_evidence",
                "callId": call.call_id,
                "counter": counter,
                "bindingDigest": binding.ref.binding_contract_digest,
                "scopeDigest": scope.scope_digest,
                "principalId": scope.principal.principal_id,
            }
        )
        evidence = CapabilityAuthorizationEvidence(
            issuer="test",
            call_id=call.call_id,
            principal=scope.principal,
            entrypoint="test",
            owner=self.owner,
            capability_key=call.domain_key,
            resolution_digest=binding.ref.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=binding.ref.dependency_closure_digest,
            allowed_side_effects=self.allowed_side_effects,  # type: ignore[arg-type]
            grant_source_digest=self.grant_source_digest,
            evidence_digest=evidence_digest,
        )
        with self._lock:
            self.issued.append(
                {
                    "call_id": call.call_id,
                    "domain_key": call.domain_key,
                    "binding_digest": binding.ref.binding_contract_digest,
                    "scope_digest": scope.scope_digest,
                    "principal_id": scope.principal.principal_id,
                    "evidence_digest": evidence_digest,
                    "entrypoint": "test",
                    "issuer": "test",
                }
            )
        return evidence


@dataclass
class _NullCapabilityEventSink:
    def emit(self, event: Any) -> None:
        del event


@dataclass
class _CancellationBridge:
    """Adapts Provider Loop CancellationPort to Capability Runtime CancellationPort."""

    inner: CancellationPort

    def is_cancelled(self) -> bool:
        return bool(self.inner.is_cancelled())

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RuntimeError("cancelled")


@dataclass
class GatewayToolDispatcher:
    """Dispatch one Tool Call through Plan 02 CapabilityGateway exactly once.

    Pre-execute path:
      reverse alias already resolved on the call
      -> verify Manifest/surface/binding identity
      -> Gateway.describe(exact_binding) freshness gate
      -> CapabilityExecutionRequest with injected authorization
      -> CapabilityExecutionContext from exact scope + call_id
      -> Gateway.execute once
      -> return result + append-only next Manifest (default: unchanged)
    """

    session_factory: SessionFactory
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier] = field(
        default_factory=default_test_evidence_verifiers
    )
    classifier: CapabilityClassifier | None = None
    locale: str | None = None
    next_manifest_hook: (
        Callable[[ProviderDispatchRequest, CapabilityResult], ResolvedRunManifestRevision]
        | None
    ) = None
    result_override: (
        Callable[[ProviderDispatchRequest], CapabilityResult | None] | None
    ) = None
    trust_exposed_for_override: bool = False
    # Plan 05 additive: shared CapabilityDispatchGuard (BudgetLedger-backed or no-op).
    dispatch_guard: Any | None = None
    dispatch_calls: list[dict[str, Any]] = field(default_factory=list)
    gateway_ids: list[int] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    adapter_invocations: list[str] = field(default_factory=list)
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
        # call.manifest_digest is the exposure-time surface Manifest; sequential
        # siblings may already have advanced current_manifest via next_manifest.
        if request.execution_scope.run_id != request.current_manifest.run_id:
            raise ValueError("dispatch scope run_id mismatch")
        if request.authorization.call_id != call.call_id:
            raise ValueError("authorization call_id mismatch")
        if (
            request.authorization.principal.principal_id
            != request.execution_scope.principal.principal_id
        ):
            raise ValueError("authorization principal mismatch")
        if request.authorization.issuer != "test" or request.authorization.entrypoint != "test":
            # Composition rule: this internal entry only issues/accepts test evidence.
            raise ValueError("gateway dispatcher accepts only issuer=test entrypoint=test")

        # Optional pure override (waiting fakes / control tools) still re-describes first
        # so classification drift is fail-closed before any authorization use.
        session, gateway = _open_gateway(
            session_factory=self.session_factory,
            locale=self.locale,
            classifier=self.classifier,
            evidence_verifiers=self.evidence_verifiers,
        )
        session_id = getattr(session, "provider_loop_session_id", None) or str(id(session))
        try:
            with self._lock:
                self.gateway_ids.append(id(gateway))
                self.session_ids.append(session_id)
                self.dispatch_calls.append(
                    {
                        "call_id": call.call_id,
                        "domain_key": call.domain_key,
                        "binding_digest": binding.ref.binding_contract_digest,
                        "session_id": session_id,
                        "gateway_id": id(gateway),
                        "scope_digest": request.execution_scope.scope_digest,
                    }
                )
            def _release_unstarted(reason_code: str) -> None:
                guard = self.dispatch_guard
                if guard is None:
                    return
                try:
                    guard.release_unstarted(call_id=call.call_id, reason_code=reason_code)
                except Exception:
                    # Release failures must not mask the primary deny result.
                    return

            def _finish_started(status: str) -> None:
                guard = self.dispatch_guard
                if guard is None:
                    return
                try:
                    guard.finish(call_id=call.call_id, status=status)
                except Exception:
                    return

            current = gateway.describe(binding)
            if self.result_override is not None:
                override = self.result_override(request)
                if override is not None:
                    if (
                        not self.trust_exposed_for_override
                        and not descriptors_equal_for_freshness(
                            exposed=exposed, current=current
                        )
                    ):
                        _release_unstarted("classification_changed")
                        result = CapabilityResult(
                            status="failed",
                            user_text=None,
                            structured_output=None,
                            artifact_refs=(),
                            continuation=None,
                            terminal_output=False,
                            needs_followup=False,
                            error=CapabilityError(
                                error_type="version_drift",
                                safe_code="classification_changed",
                                safe_message=(
                                    "capability classification changed before "
                                    "gateway execute"
                                ),
                                retry_disposition="never",
                                call_id=call.call_id,
                                target_identity=binding.ref.target_identity,
                            ),
                            metrics=CapabilityMetrics(
                                duration_ms=0.0, input_bytes=0, output_bytes=0
                            ),
                        )
                        return ProviderDispatchResult(
                            capability_result=result,
                            next_manifest=request.current_manifest,
                        )
                    # Override path bypasses Gateway adapter start; still account honestly.
                    # Treat override as a virtual start+finish so reserved budget is consumed.
                    try:
                        if self.dispatch_guard is not None:
                            self.dispatch_guard.mark_started(
                                call_id=call.call_id,
                                validated_arguments_digest=call.arguments_digest,
                            )
                            _finish_started(override.status)
                    except Exception:
                        _release_unstarted("override_guard_error")
                    next_manifest = request.current_manifest
                    if self.next_manifest_hook is not None:
                        next_manifest = self.next_manifest_hook(request, override)
                    return ProviderDispatchResult(
                        capability_result=override,
                        next_manifest=next_manifest,
                    )

            if not descriptors_equal_for_freshness(exposed=exposed, current=current):
                # Fail closed without adapter execution. Return a blocked CapabilityResult
                # so the loop pairs the call; do not raise after evidence was already
                # issued by the loop (loop issues evidence before dispatch).
                _release_unstarted("classification_changed")
                result = CapabilityResult(
                    status="failed",
                    user_text=None,
                    structured_output=None,
                    artifact_refs=(),
                    continuation=None,
                    terminal_output=False,
                    needs_followup=False,
                    error=CapabilityError(
                        error_type="version_drift",
                        safe_code="classification_changed",
                        safe_message="capability classification changed before gateway execute",
                        retry_disposition="never",
                        call_id=call.call_id,
                        target_identity=binding.ref.target_identity,
                    ),
                    metrics=CapabilityMetrics(
                        duration_ms=0.0, input_bytes=0, output_bytes=0
                    ),
                )
                return ProviderDispatchResult(
                    capability_result=result,
                    next_manifest=request.current_manifest,
                )

            context = CapabilityExecutionContext(
                call_id=call.call_id,
                run_id=request.execution_scope.run_id,
                conversation_id=request.execution_scope.conversation_id,
                locale=self.locale,
                request_source="provider_loop_test",
                request_channel="internal_test",
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
            if self.dispatch_guard is not None:
                ports_kwargs["dispatch_guard"] = self.dispatch_guard
            ports = CapabilityRuntimePorts(**ports_kwargs)
            with self._lock:
                self.adapter_invocations.append(call.call_id)
            result = gateway.execute(execution_request, ports=ports)
            next_manifest = request.current_manifest
            if self.next_manifest_hook is not None:
                next_manifest = self.next_manifest_hook(request, result)
            return ProviderDispatchResult(
                capability_result=result,
                next_manifest=next_manifest,
            )
        finally:
            session.close()


@dataclass
class TestOnlyToolsProvider:
    """Grant-filtered ToolsProvider that resolves descriptors only via Gateway.describe.

    Does not query published Skills or activate anything implicitly. Closes the
    resolution Session before returning the frozen surface.
    """

    grants: TestGrantRegistry
    session_factory: SessionFactory
    provider_protocol: str = OPENAI_CHAT_PROVIDER_PROTOCOL
    expected_model_ref: ModelRef | None = None
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier] = field(
        default_factory=default_test_evidence_verifiers
    )
    classifier: CapabilityClassifier | None = None
    resolve_calls: list[dict[str, Any]] = field(default_factory=list)
    gateway_ids: list[int] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def resolve(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        scope: ProviderExecutionScope,
        locale: str,
    ) -> ToolSurfaceResolution:
        if not isinstance(manifest, ResolvedRunManifestRevision):
            raise TypeError("manifest must be a ResolvedRunManifestRevision")
        if scope.run_id != manifest.run_id:
            raise ValueError("tools provider scope run_id must match manifest.run_id")
        if self.expected_model_ref is not None:
            if manifest.model is None:
                raise ValueError("manifest missing model ref")
            if manifest.model.model_ref_digest != self.expected_model_ref.model_ref_digest:
                raise ValueError("manifest model_ref_digest mismatch")
            if (
                self.expected_model_ref.model_config_digest is not None
                and manifest.model.model_config_digest
                != self.expected_model_ref.model_config_digest
            ):
                raise ValueError("manifest model_config_digest mismatch")

        # Exact grants only. When Manifest already lists capabilities, intersect so
        # dynamic appends become visible only after Manifest lineage advances.
        grant_snapshot = self.grants.snapshot()
        if manifest.capabilities:
            allowed_digests = {
                item.binding_contract_digest for item in manifest.capabilities
            }
            visible_bindings = tuple(
                binding
                for binding in grant_snapshot
                if binding.ref.binding_contract_digest in allowed_digests
            )
        else:
            # Empty Manifest capabilities: expose the full explicit grant set for the
            # first internal test round (base manifest has no capabilities yet).
            visible_bindings = grant_snapshot

        session, gateway = _open_gateway(
            session_factory=self.session_factory,
            locale=locale,
            classifier=self.classifier,
            evidence_verifiers=self.evidence_verifiers,
        )
        session_id = getattr(session, "provider_loop_session_id", None) or str(id(session))
        try:
            with self._lock:
                self.gateway_ids.append(id(gateway))
                self.session_ids.append(session_id)
                self.resolve_calls.append(
                    {
                        "manifest_digest": manifest.manifest_digest,
                        "manifest_revision": manifest.revision,
                        "scope_digest": scope.scope_digest,
                        "locale": locale,
                        "session_id": session_id,
                        "gateway_id": id(gateway),
                        "grant_keys": [b.ref.capability_key for b in visible_bindings],
                    }
                )
            pairs: list[tuple[FrozenCapabilityBinding, CapabilityDescriptor]] = []
            for binding in visible_bindings:
                descriptor = gateway.describe(binding)
                # Only available exact grants are exposed to the Provider.
                if descriptor.availability.status != "available":
                    continue
                pairs.append((binding, descriptor))
            # Close Session before returning frozen data (build surface is pure).
        finally:
            session.close()

        return build_provider_tool_surface(
            manifest=manifest,
            provider_protocol=self.provider_protocol,
            visible=pairs,
            scope=scope,
        )


@dataclass
class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


@dataclass
class RecordingEventSink:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


@dataclass
class IsolatedGatewayDispatcherFactory:
    """Creates an independent Gateway dispatcher + Session identity per worker call."""

    session_factory: SessionFactory
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier] = field(
        default_factory=default_test_evidence_verifiers
    )
    classifier: CapabilityClassifier | None = None
    locale: str | None = None
    opened: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def open(
        self,
        *,
        call: ProviderToolCall,
        parent_session_id: str | None,
    ) -> tuple[GatewayToolDispatcher, _SessionRecord]:
        del parent_session_id
        # Dispatcher itself opens per-dispatch sessions; the SiblingSession identity
        # is a lightweight handle proving the factory was used per call.
        record = _SessionRecord(session_id=str(uuid.uuid4()))
        dispatcher = GatewayToolDispatcher(
            session_factory=self.session_factory,
            evidence_verifiers=self.evidence_verifiers,
            classifier=self.classifier,
            locale=self.locale,
        )
        with self._lock:
            self.opened.append(
                {
                    "call_id": call.call_id,
                    "session_id": record.session_id,
                    "dispatcher_id": id(dispatcher),
                }
            )
        return dispatcher, record


def build_test_provider_loop_ports(
    *,
    provider: Any,
    grants: TestGrantRegistry,
    session_factory: SessionFactory,
    model_ref: ModelRef | None = None,
    provider_protocol: str = OPENAI_CHAT_PROVIDER_PROTOCOL,
    allowed_side_effects: tuple[str, ...] = ("none", "compute", "read"),
    classifier: CapabilityClassifier | None = None,
    locale: str | None = "en",
    isolated_parallel: bool = False,
    max_workers: int = 4,
    cancellation: CancellationPort | None = None,
    events: Any | None = None,
    dispatcher: ToolDispatcher | None = None,
    tools_provider: ToolsProvider | None = None,
    current_descriptors: CurrentCapabilityDescriptorVerifier | None = None,
    authorization_evidence: ProviderAuthorizationEvidenceFactory | None = None,
    next_manifest_hook: (
        Callable[[ProviderDispatchRequest, CapabilityResult], ResolvedRunManifestRevision]
        | None
    ) = None,
    result_override: (
        Callable[[ProviderDispatchRequest], CapabilityResult | None] | None
    ) = None,
    trust_exposed_for_override: bool = False,
) -> ProviderLoopPorts:
    """Compose internal/test ProviderLoopPorts against the Capability Gateway."""
    verifiers = default_test_evidence_verifiers(allowed_side_effects=allowed_side_effects)
    tools = tools_provider or TestOnlyToolsProvider(
        grants=grants,
        session_factory=session_factory,
        provider_protocol=provider_protocol,
        expected_model_ref=model_ref,
        evidence_verifiers=verifiers,
        classifier=classifier,
    )
    verifier = current_descriptors or GatewayCurrentDescriptorVerifier(
        session_factory=session_factory,
        evidence_verifiers=verifiers,
        classifier=classifier,
        locale=locale,
    )
    auth = authorization_evidence or TestAuthorizationEvidenceFactory(
        allowed_side_effects=allowed_side_effects
    )
    tool_dispatcher = dispatcher or GatewayToolDispatcher(
        session_factory=session_factory,
        evidence_verifiers=verifiers,
        classifier=classifier,
        locale=locale,
        next_manifest_hook=next_manifest_hook,
        result_override=result_override,
        trust_exposed_for_override=trust_exposed_for_override,
    )
    if isolated_parallel:
        sibling_executor: Any = BoundedIsolatedSiblingExecutor(max_workers=max_workers)
    else:
        sibling_executor = SequentialSiblingExecutor()
    return ProviderLoopPorts(
        provider=provider,
        tools_provider=tools,
        current_descriptors=verifier,
        authorization_evidence=auth,
        tool_dispatcher=tool_dispatcher,
        sibling_executor=sibling_executor,
        cancellation=cancellation or NeverCancelled(),
        events=events or RecordingEventSink(),
    )


def run_internal_test_provider_loop(
    request: ProviderLoopRequest,
    ports: ProviderLoopPorts,
) -> ProviderLoopResult:
    """Internal/test entry only. Does not register routes or workers."""
    return run_provider_agent_loop(request, ports)


__all__ = [
    "ClassificationDriftError",
    "GatewayCurrentDescriptorVerifier",
    "GatewayToolDispatcher",
    "IsolatedGatewayDispatcherFactory",
    "RecordingEventSink",
    "TestAuthorizationEvidenceFactory",
    "TestAuthorizationEvidenceVerifier",
    "TestGrantRegistry",
    "TestOnlyToolsProvider",
    "TrackingSessionFactory",
    "append_test_capability_grant",
    "build_test_provider_loop_ports",
    "capability_ref_from_binding",
    "default_test_evidence_verifiers",
    "descriptors_equal_for_freshness",
    "run_internal_test_provider_loop",
]
