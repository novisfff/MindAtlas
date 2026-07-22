"""Evaluation RuntimeIsolationContext enforcement (Plan 09 Task 4).

Mandatory typed execution scope for every workbench Eval Run. Installs
tripwires on production writers and wraps Capability Gateway so nested
Workflow/Agent children cannot escape the evaluation namespace.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Mapping, Sequence
from uuid import UUID, uuid4

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.evaluation.contracts import (
    EVAL_ARTIFACT_NAMESPACE,
    EVAL_EVENT_NAMESPACE,
    EVAL_OWNER_KIND,
    EvalExecutionIdentity,
    RuntimeIsolationContext,
    assert_evaluation_object_key,
    is_evaluation_object_key,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stable codes
# ---------------------------------------------------------------------------

ISOLATION_BREACH = "isolation_breach"
CODE_MISSING_SCOPE = "missing_isolation_scope"
CODE_PRODUCTION_SCOPE = "production_scope_forbidden"
CODE_NAMESPACE_MISMATCH = "isolation_namespace_mismatch"
CODE_OWNERSHIP_CONFLATED = "subject_test_ownership_conflated"
CODE_MIXED_NAMESPACE = "mixed_namespace"
CODE_UNKNOWN_SIDE_EFFECT = "unknown_side_effect_denied"
CODE_RAW_INVOKE = "raw_invoke_forbidden"
CODE_WORKER_UNAVAILABLE = "eval_worker_unavailable"

EVAL_SIDE_EFFECT_NONE = "none"
EVAL_SIDE_EFFECT_COMPUTE = "compute"
EVAL_SIDE_EFFECT_READ = "read"
EVAL_SIDE_EFFECT_DRAFT = "draft"
EVAL_SIDE_EFFECT_WRITE_LOCAL = "write_local"
EVAL_SIDE_EFFECT_WRITE_EXTERNAL = "write_external"
EVAL_SIDE_EFFECT_UNKNOWN = "unknown"

ALLOWED_ISOLATED_SIDE_EFFECTS = frozenset(
    {EVAL_SIDE_EFFECT_NONE, EVAL_SIDE_EFFECT_COMPUTE, EVAL_SIDE_EFFECT_READ}
)
SIMULATED_SIDE_EFFECTS = frozenset(
    {
        EVAL_SIDE_EFFECT_DRAFT,
        EVAL_SIDE_EFFECT_WRITE_LOCAL,
        EVAL_SIDE_EFFECT_WRITE_EXTERNAL,
    }
)

# Production writer sites (Task 0 §6.3 freeze map).
PRODUCTION_TRIPWIRE_SITES = frozenset(
    {
        "EntryService.create",
        "EntryService.create_in_uow",
        "EntryService.commit",
        "CapabilityCallRepository.create_or_verify_proposed",
        "DurableCapabilityLedgerAggregate.commit",
        "DurableRunRepository.commit",
        "run_service.create_run",
        "run_service.append_event",
        "AssistantMemoryService.write",
        "L1MemoryWriter",
        "L2MemoryWriter",
        "DurableArtifactService.commit_row",
        "production_object_prefix",
        "EntryIndexOutbox",
        "AttachmentIndexOutbox",
        "production_write_adapter",
        "production_external_adapter",
    }
)

_active_scope: contextvars.ContextVar["EvalExecutionScope | None"] = contextvars.ContextVar(
    "eval_execution_scope", default=None
)


class IsolationError(RuntimeError):
    """Hard isolation failure. Marks the Eval Run permanently gate-ineligible."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        site: str | None = None,
        permanently_gate_ineligible: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.site = site
        self.permanently_gate_ineligible = permanently_gate_ineligible


class IsolationBreach(IsolationError):
    """Eval scope touched a production writer/adapter — not a metric."""

    def __init__(self, message: str, *, site: str) -> None:
        super().__init__(
            ISOLATION_BREACH,
            message,
            site=site,
            permanently_gate_ineligible=True,
        )


@dataclass(frozen=True, slots=True)
class EvalCallRecord:
    """In-memory synthetic capability call evidence (before repository append)."""

    eval_call_id: UUID
    logical_call_key: str
    attempt: int
    side_effect: str
    capability_key: str
    outcome: Literal["succeeded_isolated", "simulated", "denied", "failed"]
    parent_ordinal: int | None
    child_ordinal: int
    input_digest: str
    descriptor_digest: str
    binding_digest: str
    policy_digest: str
    decision: Mapping[str, Any]


@dataclass
class EvalExecutionScope:
    """Typed process-local scope for one Eval case execution."""

    isolation: RuntimeIsolationContext
    identity: EvalExecutionIdentity
    subject_digest: str
    call_records: list[EvalCallRecord] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    breached: bool = False
    breach_site: str | None = None
    breach_message: str | None = None
    cancelled: bool = False
    call_ordinal: int = 0
    nested_depth: int = 0
    fixture_store: dict[str, Any] = field(default_factory=dict)
    snapshot_store: dict[str, Any] = field(default_factory=dict)
    simulated_writes: list[dict[str, Any]] = field(default_factory=list)

    def record_event(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "payload": dict(payload or {}),
                "seq": len(self.events) + 1,
            }
        )

    def mark_breach(self, *, site: str, message: str) -> IsolationBreach:
        self.breached = True
        self.breach_site = site
        self.breach_message = message
        self.record_event(
            "eval.isolation_breach",
            {"site": site, "safe_message": "isolation_breach", "code": ISOLATION_BREACH},
        )
        return IsolationBreach(message, site=site)


def validate_isolation_context(ctx: RuntimeIsolationContext) -> RuntimeIsolationContext:
    """Validate frozen isolation context shape for persistence writers."""
    if ctx.owner_kind != EVAL_OWNER_KIND:
        raise ValueError("isolation owner_kind must be 'test'")
    if ctx.event_namespace != EVAL_EVENT_NAMESPACE:
        raise ValueError("event_namespace must be 'evaluation'")
    if ctx.artifact_namespace != EVAL_ARTIFACT_NAMESPACE:
        raise ValueError("artifact_namespace must be 'evaluation'")
    if ctx.side_effect_mode != "simulate_only":
        raise ValueError("side_effect_mode must be 'simulate_only'")
    return ctx


def validate_execution_identity(
    identity: EvalExecutionIdentity,
    isolation: RuntimeIsolationContext,
) -> EvalExecutionIdentity:
    """Fail closed on missing/mismatched/conflated identity before Provider/Gateway."""
    if identity.owner_kind != EVAL_OWNER_KIND:
        raise IsolationError(
            CODE_OWNERSHIP_CONFLATED,
            "EvalExecutionIdentity.owner_kind must be 'test'",
        )
    if identity.namespace_id != isolation.namespace_id:
        raise IsolationError(
            CODE_NAMESPACE_MISMATCH,
            "EvalExecutionIdentity.namespace_id must match RuntimeIsolationContext.namespace_id",
        )
    # Subject ownership is separate from execution ownership; never project
    # subject aggregate into owner_kind or treat subject as production principal.
    if str(identity.subject_aggregate_id) == str(identity.eval_run_id):
        raise IsolationError(
            CODE_OWNERSHIP_CONFLATED,
            "subject_aggregate_id must not equal eval_run_id",
        )
    return identity


def get_active_eval_scope() -> EvalExecutionScope | None:
    return _active_scope.get()


def require_active_eval_scope() -> EvalExecutionScope:
    scope = _active_scope.get()
    if scope is None:
        raise IsolationError(
            CODE_MISSING_SCOPE,
            "RuntimeIsolationContext + EvalExecutionIdentity required before Provider/Gateway",
        )
    return scope


def is_eval_scope_active() -> bool:
    return _active_scope.get() is not None


@contextmanager
def eval_execution_scope(
    *,
    isolation: RuntimeIsolationContext,
    identity: EvalExecutionIdentity,
    fixture_store: Mapping[str, Any] | None = None,
    snapshot_store: Mapping[str, Any] | None = None,
) -> Iterator[EvalExecutionScope]:
    """Install mandatory isolation scope. Nested scopes with mixed namespace fail."""
    isolation = validate_isolation_context(isolation)
    identity = validate_execution_identity(identity, isolation)
    parent = _active_scope.get()
    if parent is not None:
        if parent.isolation.namespace_id != isolation.namespace_id:
            raise IsolationError(
                CODE_MIXED_NAMESPACE,
                "nested eval scope cannot mix isolation namespaces",
            )
        if parent.identity.eval_run_id != identity.eval_run_id:
            raise IsolationError(
                CODE_MIXED_NAMESPACE,
                "nested eval scope cannot mix eval_run_id",
            )
    scope = EvalExecutionScope(
        isolation=isolation,
        identity=identity,
        subject_digest=isolation.subject_digest,
        fixture_store=dict(fixture_store or {}),
        snapshot_store=dict(snapshot_store or {}),
    )
    token = _active_scope.set(scope)
    try:
        yield scope
    finally:
        _active_scope.reset(token)


def tripwire_production_writer(site: str, *, detail: str | None = None) -> None:
    """Hard tripwire for production writer/adapter sites.

    Outside eval scope: no-op (production path continues).
    Inside eval scope: abort as gate-ineligible isolation_breach.
    """
    scope = _active_scope.get()
    if scope is None:
        return
    message = f"eval scope reached production writer site={site}"
    if detail:
        message = f"{message}: {detail}"
    raise scope.mark_breach(site=site, message=message)


def tripwire_production_object_key(object_key: str | None) -> None:
    """Reject production object-prefix writes from eval scope; reject eval keys from prod."""
    if not object_key:
        return
    scope = _active_scope.get()
    key = str(object_key)
    if scope is not None:
        # Eval must never write production assistant-runs/ prefix.
        if key.startswith("assistant-runs/") or not is_evaluation_object_key(key):
            raise scope.mark_breach(
                site="production_object_prefix",
                message=f"eval scope attempted production object key {key!r}",
            )
        assert_evaluation_object_key(key)
        return
    # Production path: reject evaluation keys (defense in depth).
    if is_evaluation_object_key(key):
        raise ValueError("production artifact APIs reject evaluation object keys")


def assert_not_production_scope_for_eval() -> None:
    """Call before Provider/Gateway construction when eval is expected."""
    require_active_eval_scope()


def isolation_digest(ctx: RuntimeIsolationContext) -> str:
    """Canonical digest of isolation envelope for run persistence."""
    return sha256_canonical_json(
        {
            "namespace_id": str(ctx.namespace_id),
            "owner_kind": ctx.owner_kind,
            "subject_digest": ctx.subject_digest,
            "dataset_version_ids": [str(x) for x in ctx.dataset_version_ids],
            "memory_mode": ctx.memory_mode,
            "data_mode": ctx.data_mode,
            "data_snapshot_id": (
                str(ctx.data_snapshot_id) if ctx.data_snapshot_id is not None else None
            ),
            "snapshot_projection_policy_digest": ctx.snapshot_projection_policy_digest,
            "side_effect_mode": ctx.side_effect_mode,
            "event_namespace": ctx.event_namespace,
            "artifact_namespace": ctx.artifact_namespace,
        }
    )


def classify_side_effect(descriptor: Any) -> str:
    """Extract side_effect class from a CapabilityDescriptor-like object."""
    behavior = getattr(descriptor, "behavior", None)
    if behavior is None and isinstance(descriptor, Mapping):
        behavior = descriptor.get("behavior")
    if behavior is None:
        return EVAL_SIDE_EFFECT_UNKNOWN
    effect = getattr(behavior, "side_effect", None)
    if effect is None and isinstance(behavior, Mapping):
        effect = behavior.get("side_effect")
    text = str(effect or EVAL_SIDE_EFFECT_UNKNOWN).strip()
    return text or EVAL_SIDE_EFFECT_UNKNOWN


def resolve_eval_dispatch(
    *,
    side_effect: str,
    capability_key: str,
    arguments: Mapping[str, Any] | None,
    scope: EvalExecutionScope,
    descriptor_digest: str,
    binding_digest: str,
    policy_digest: str,
    logical_call_key: str | None = None,
    attempt: int = 1,
    parent_ordinal: int | None = None,
) -> EvalCallRecord:
    """Route a capability call through evaluation adapters / simulation / deny.

    - none/compute/read → succeeded_isolated (eval-owned fixture adapters)
    - draft/write_local/write_external → simulated (never production)
    - unknown → denied before adapter lookup
    """
    scope.call_ordinal += 1
    ordinal = scope.call_ordinal
    key = logical_call_key or f"eval-call-{ordinal}"
    input_digest = sha256_canonical_json(
        {
            "capability_key": capability_key,
            "arguments": dict(arguments or {}),
            "namespace_id": str(scope.isolation.namespace_id),
        }
    )
    effect = str(side_effect or EVAL_SIDE_EFFECT_UNKNOWN)

    if effect == EVAL_SIDE_EFFECT_UNKNOWN:
        record = EvalCallRecord(
            eval_call_id=uuid4(),
            logical_call_key=key,
            attempt=attempt,
            side_effect=effect,
            capability_key=capability_key,
            outcome="denied",
            parent_ordinal=parent_ordinal,
            child_ordinal=ordinal,
            input_digest=input_digest,
            descriptor_digest=descriptor_digest,
            binding_digest=binding_digest,
            policy_digest=policy_digest,
            decision={
                "disposition": "deny",
                "reason_code": CODE_UNKNOWN_SIDE_EFFECT,
                "side_effect": effect,
            },
        )
        scope.call_records.append(record)
        scope.record_event(
            "eval.capability_denied",
            {"logical_call_key": key, "side_effect": effect, "reason": CODE_UNKNOWN_SIDE_EFFECT},
        )
        return record

    if effect in SIMULATED_SIDE_EFFECTS:
        sim_result = {
            "simulated": True,
            "side_effect": effect,
            "capability_key": capability_key,
            "namespace_id": str(scope.isolation.namespace_id),
            "arguments_digest": input_digest,
        }
        scope.simulated_writes.append(sim_result)
        record = EvalCallRecord(
            eval_call_id=uuid4(),
            logical_call_key=key,
            attempt=attempt,
            side_effect=effect,
            capability_key=capability_key,
            outcome="simulated",
            parent_ordinal=parent_ordinal,
            child_ordinal=ordinal,
            input_digest=input_digest,
            descriptor_digest=descriptor_digest,
            binding_digest=binding_digest,
            policy_digest=policy_digest,
            decision={
                "disposition": "simulate",
                "reason_code": "eval_simulate_only",
                "side_effect": effect,
                "result_digest": sha256_canonical_json(sim_result),
            },
        )
        scope.call_records.append(record)
        scope.record_event(
            "eval.capability_simulated",
            {"logical_call_key": key, "side_effect": effect},
        )
        return record

    if effect in ALLOWED_ISOLATED_SIDE_EFFECTS:
        # Evaluation-owned fixture / compute / read adapters only.
        if effect == EVAL_SIDE_EFFECT_READ:
            _resolve_read_fixture(scope, capability_key=capability_key, arguments=arguments)
        result_payload = {
            "isolated": True,
            "side_effect": effect,
            "capability_key": capability_key,
            "namespace_id": str(scope.isolation.namespace_id),
        }
        record = EvalCallRecord(
            eval_call_id=uuid4(),
            logical_call_key=key,
            attempt=attempt,
            side_effect=effect,
            capability_key=capability_key,
            outcome="succeeded_isolated",
            parent_ordinal=parent_ordinal,
            child_ordinal=ordinal,
            input_digest=input_digest,
            descriptor_digest=descriptor_digest,
            binding_digest=binding_digest,
            policy_digest=policy_digest,
            decision={
                "disposition": "allow_isolated",
                "reason_code": "eval_fixture_adapter",
                "side_effect": effect,
                "result_digest": sha256_canonical_json(result_payload),
            },
        )
        scope.call_records.append(record)
        scope.record_event(
            "eval.capability_isolated",
            {"logical_call_key": key, "side_effect": effect},
        )
        return record

    # Fail closed on unexpected vocabulary.
    record = EvalCallRecord(
        eval_call_id=uuid4(),
        logical_call_key=key,
        attempt=attempt,
        side_effect=effect,
        capability_key=capability_key,
        outcome="denied",
        parent_ordinal=parent_ordinal,
        child_ordinal=ordinal,
        input_digest=input_digest,
        descriptor_digest=descriptor_digest,
        binding_digest=binding_digest,
        policy_digest=policy_digest,
        decision={
            "disposition": "deny",
            "reason_code": "unsupported_side_effect",
            "side_effect": effect,
        },
    )
    scope.call_records.append(record)
    return record


def _resolve_read_fixture(
    scope: EvalExecutionScope,
    *,
    capability_key: str,
    arguments: Mapping[str, Any] | None,
) -> Any:
    """Read only evaluation fixtures or prebuilt allowlisted snapshot projection."""
    isolation = scope.isolation
    if isolation.data_mode == "fixture":
        # Fixtures are evaluation-owned; never open a production Session.
        key = str((arguments or {}).get("fixture_key") or capability_key)
        return scope.fixture_store.get(key)
    if isolation.data_mode == "read_snapshot":
        if isolation.data_snapshot_id is None:
            raise IsolationError(CODE_MISSING_SCOPE, "read_snapshot requires data_snapshot_id")
        snap_key = str(isolation.data_snapshot_id)
        if snap_key not in scope.snapshot_store:
            raise IsolationError(
                "snapshot_missing",
                "read_snapshot projection not prebuilt in evaluation namespace",
            )
        return scope.snapshot_store[snap_key]
    raise IsolationError("invalid_data_mode", f"unsupported data_mode={isolation.data_mode}")


class IsolationWrappedGateway:
    """Wraps CapabilityGateway so every top-level + nested dispatch is isolated.

    For allowlisted ``none|compute|read`` side effects, delegates to a real
    inner ``CapabilityGateway`` (or thin adapter) after isolation checks when
    provided. Draft/write_* are always simulated; unknown is denied. Nested
    Workflow/Agent children re-enter through this same wrapper; raw Tool invoke
    shortcuts are rejected.
    """

    def __init__(
        self,
        *,
        inner: Any | None = None,
        scope: EvalExecutionScope | None = None,
        memory_provider: Any | None = None,
        data_provider: Any | None = None,
    ) -> None:
        self._inner = inner
        self._scope = scope
        self._memory_provider = memory_provider
        self._data_provider = data_provider

    def execute(
        self,
        request: Any,
        *,
        ports: Any | None = None,
        side_effect: str | None = None,
        capability_key: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        descriptor_digest: str | None = None,
        binding_digest: str | None = None,
        policy_digest: str | None = None,
        logical_call_key: str | None = None,
        attempt: int = 1,
        parent_ordinal: int | None = None,
        allow_inner: bool = False,
        nested: bool = False,
    ) -> Any:
        scope = self._scope or require_active_eval_scope()
        if scope.breached:
            raise IsolationBreach(
                scope.breach_message or "scope already breached",
                site=scope.breach_site or "unknown",
            )
        if scope.cancelled:
            return {
                "status": "cancelled",
                "safe_code": "cancelled",
            }

        # Extract fields from CapabilityExecutionRequest when provided.
        cap_key = capability_key
        args = arguments
        effect = side_effect
        desc_digest = descriptor_digest or ("0" * 64)
        bind_digest = binding_digest or ("0" * 64)
        pol_digest = policy_digest or ("0" * 64)

        if request is not None:
            binding = getattr(request, "binding", None)
            if binding is not None:
                resolved = getattr(binding, "resolved", binding)
                cap_key = cap_key or getattr(resolved, "capability_key", None)
                bind_digest = getattr(resolved, "binding_contract_digest", None) or bind_digest
            if hasattr(request, "input"):
                args = args if args is not None else getattr(request, "input", None)
            # Prefer descriptor side_effect if available via ports/registry later.
            if effect is None and hasattr(request, "side_effect"):
                effect = getattr(request, "side_effect")
            # Classify from descriptor when request carries one.
            if effect is None:
                descriptor = getattr(request, "descriptor", None)
                if descriptor is not None:
                    effect = classify_side_effect(descriptor)

        cap_key = str(cap_key or "unknown.capability")
        if isinstance(args, Mapping):
            arg_map: Mapping[str, Any] | None = args
        elif args is None:
            arg_map = None
        else:
            arg_map = {"value": args}

        # Nested depth tracking for Workflow/Agent children.
        scope.nested_depth += 1
        try:
            # Reject raw production adapter invoke shortcuts.
            if allow_inner and self._inner is not None:
                # Even with inner, never allow production write adapters under eval.
                tripwire_production_writer(
                    "production_write_adapter",
                    detail="raw inner gateway invoke forbidden under eval",
                )

            effect_str = str(effect or EVAL_SIDE_EFFECT_UNKNOWN)

            # Route through isolation classification first (deny/simulate/allow).
            record = resolve_eval_dispatch(
                side_effect=effect_str,
                capability_key=cap_key,
                arguments=arg_map,
                scope=scope,
                descriptor_digest=str(desc_digest),
                binding_digest=str(bind_digest),
                policy_digest=str(pol_digest),
                logical_call_key=logical_call_key,
                attempt=attempt,
                parent_ordinal=parent_ordinal,
            )

            # For allowlisted isolated effects, optionally delegate to a real
            # inner CapabilityGateway after isolation checks. Draft/write are
            # never delegated; unknown is never delegated.
            inner_result: Any | None = None
            if (
                record.outcome == "succeeded_isolated"
                and self._inner is not None
                and effect_str in ALLOWED_ISOLATED_SIDE_EFFECTS
            ):
                inner_result = self._delegate_isolated_to_inner(
                    request=request,
                    ports=ports,
                    capability_key=cap_key,
                    arguments=arg_map,
                    side_effect=effect_str,
                    scope=scope,
                )

            result: dict[str, Any] = {
                "status": record.outcome,
                "eval_call_id": str(record.eval_call_id),
                "logical_call_key": record.logical_call_key,
                "side_effect": record.side_effect,
                "decision": dict(record.decision),
                "nested_depth": scope.nested_depth,
                "nested": bool(nested) or parent_ordinal is not None,
            }
            if inner_result is not None:
                result["inner"] = inner_result
                result["delegated_to_inner"] = True
            else:
                result["delegated_to_inner"] = False
            return result
        finally:
            scope.nested_depth = max(0, scope.nested_depth - 1)

    def _delegate_isolated_to_inner(
        self,
        *,
        request: Any,
        ports: Any | None,
        capability_key: str,
        arguments: Mapping[str, Any] | None,
        side_effect: str,
        scope: EvalExecutionScope,
    ) -> Any:
        """Delegate allowlisted isolated dispatch to real Gateway / thin adapter.

        Never mutates production state: memory/data ports are eval-scoped
        fixture/empty providers. Failures are recorded as isolated adapter
        errors without escaping isolation.
        """
        # Prefer memory/data provider seams when present.
        if side_effect == EVAL_SIDE_EFFECT_READ and self._data_provider is not None:
            try:
                return self._data_provider.read(
                    capability_key=capability_key,
                    arguments=dict(arguments or {}),
                    scope=scope,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": "failed",
                    "safe_code": "eval_data_provider_error",
                    "error_type": type(exc).__name__,
                }
        if self._memory_provider is not None and str(capability_key).startswith(
            ("memory.", "l1.", "l2.")
        ):
            try:
                return self._memory_provider.read(
                    capability_key=capability_key,
                    arguments=dict(arguments or {}),
                    scope=scope,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": "failed",
                    "safe_code": "eval_memory_provider_error",
                    "error_type": type(exc).__name__,
                }

        inner = self._inner
        if inner is None:
            return None
        # Thin adapter: record the call shape for tests / scripted paths.
        if callable(getattr(inner, "execute", None)):
            try:
                if request is not None and ports is not None:
                    return inner.execute(request, ports=ports)
                if request is not None:
                    # Some fakes accept request only.
                    try:
                        return inner.execute(request, ports=ports)
                    except TypeError:
                        return inner.execute(
                            request,
                            capability_key=capability_key,
                            arguments=dict(arguments or {}),
                            side_effect=side_effect,
                        )
                # Scripted path without full CapabilityExecutionRequest.
                execute = inner.execute
                try:
                    return execute(
                        None,
                        capability_key=capability_key,
                        arguments=dict(arguments or {}),
                        side_effect=side_effect,
                        ports=ports,
                    )
                except TypeError:
                    return execute(
                        {
                            "capability_key": capability_key,
                            "arguments": dict(arguments or {}),
                            "side_effect": side_effect,
                            "namespace_id": str(scope.isolation.namespace_id),
                        }
                    )
            except IsolationBreach:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("isolation-wrapped inner gateway failed")
                return {
                    "status": "failed",
                    "safe_code": "eval_inner_gateway_error",
                    "error_type": type(exc).__name__,
                }
        if callable(inner):
            try:
                return inner(
                    capability_key=capability_key,
                    arguments=dict(arguments or {}),
                    side_effect=side_effect,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": "failed",
                    "safe_code": "eval_inner_callable_error",
                    "error_type": type(exc).__name__,
                }
        return None

    def execute_nested_child(
        self,
        *,
        side_effect: str,
        capability_key: str,
        arguments: Mapping[str, Any] | None = None,
        parent_ordinal: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Nested Workflow/Agent child must re-enter isolation wrapper.

        Re-enters the same IsolationWrappedGateway (same isolation scope) rather
        than only flipping a boolean flag.
        """
        # Explicit re-entry: construct a same-scope nested gateway so child
        # dispatch cannot bypass isolation classification/policy.
        nested_gw = IsolationWrappedGateway(
            inner=self._inner,
            scope=self._scope or require_active_eval_scope(),
            memory_provider=self._memory_provider,
            data_provider=self._data_provider,
        )
        return nested_gw.execute(
            None,
            side_effect=side_effect,
            capability_key=capability_key,
            arguments=arguments,
            parent_ordinal=parent_ordinal,
            nested=True,
            **kwargs,
        )


@dataclass
class EvalMemoryProvider:
    """Narrow eval memory seam — fixture/empty modes only; never production writers."""

    mode: Literal["empty", "fixture"] = "empty"
    fixture_store: dict[str, Any] = field(default_factory=dict)

    def read(
        self,
        *,
        capability_key: str,
        arguments: Mapping[str, Any] | None = None,
        scope: EvalExecutionScope | None = None,
    ) -> Any:
        if self.mode == "empty":
            return {"mode": "empty", "items": [], "capability_key": capability_key}
        key = str((arguments or {}).get("fixture_key") or capability_key)
        store = self.fixture_store
        if scope is not None:
            store = {**scope.fixture_store, **store}
        return {
            "mode": "fixture",
            "capability_key": capability_key,
            "value": store.get(key),
        }

    def write(self, *args: Any, **kwargs: Any) -> None:
        tripwire_production_writer(
            "L1MemoryWriter",
            detail="eval memory provider forbids production writes",
        )


@dataclass
class EvalDataProvider:
    """Narrow eval data seam — fixture/read_snapshot only; never production writers."""

    mode: Literal["fixture", "read_snapshot"] = "fixture"
    fixture_store: dict[str, Any] = field(default_factory=dict)
    snapshot_store: dict[str, Any] = field(default_factory=dict)

    def read(
        self,
        *,
        capability_key: str,
        arguments: Mapping[str, Any] | None = None,
        scope: EvalExecutionScope | None = None,
    ) -> Any:
        if self.mode == "fixture":
            key = str((arguments or {}).get("fixture_key") or capability_key)
            store = self.fixture_store
            if scope is not None:
                store = {**scope.fixture_store, **store}
            return store.get(key)
        # read_snapshot
        if scope is not None and scope.isolation.data_snapshot_id is not None:
            snap_key = str(scope.isolation.data_snapshot_id)
            store = {**scope.snapshot_store, **self.snapshot_store}
            if snap_key not in store:
                raise IsolationError(
                    "snapshot_missing",
                    "read_snapshot projection not prebuilt in evaluation namespace",
                )
            return store[snap_key]
        raise IsolationError("invalid_data_mode", "read_snapshot requires data_snapshot_id")

    def write(self, *args: Any, **kwargs: Any) -> None:
        tripwire_production_writer(
            "production_write_adapter",
            detail="eval data provider forbids production writes",
        )


def reject_raw_tool_invoke() -> None:
    """Reject inner raw Tool invoke / direct business-service shortcuts."""
    scope = _active_scope.get()
    if scope is None:
        return
    raise scope.mark_breach(
        site="raw_tool_invoke",
        message="raw Tool invoke / direct business-service shortcut forbidden under eval",
    )


def build_isolation_context(
    *,
    namespace_id: UUID | None = None,
    subject_digest: str,
    dataset_version_ids: Sequence[UUID],
    memory_mode: Literal["empty", "fixture"] = "empty",
    data_mode: Literal["fixture", "read_snapshot"] = "fixture",
    data_snapshot_id: UUID | None = None,
    snapshot_projection_policy_digest: str | None = None,
) -> RuntimeIsolationContext:
    """Construct a validated RuntimeIsolationContext for interactive runs."""
    ctx = RuntimeIsolationContext(
        namespace_id=namespace_id or uuid4(),
        owner_kind=EVAL_OWNER_KIND,
        subject_digest=subject_digest,
        dataset_version_ids=tuple(dataset_version_ids),
        memory_mode=memory_mode,
        data_mode=data_mode,
        data_snapshot_id=data_snapshot_id,
        snapshot_projection_policy_digest=snapshot_projection_policy_digest,
        side_effect_mode="simulate_only",
        event_namespace=EVAL_EVENT_NAMESPACE,
        artifact_namespace=EVAL_ARTIFACT_NAMESPACE,
    )
    return validate_isolation_context(ctx)


def production_write_mode_ignored() -> bool:
    """ASSISTANT_MAIN_AGENT_WRITE_MODE is ignored for evaluation.

    Eval write simulation works when the production flag is ``off``, and enabling
    ``golden`` must not change Eval behavior. Always returns True under eval scope.
    """
    return is_eval_scope_active()


__all__ = [
    "ALLOWED_ISOLATED_SIDE_EFFECTS",
    "CODE_MISSING_SCOPE",
    "CODE_MIXED_NAMESPACE",
    "CODE_NAMESPACE_MISMATCH",
    "CODE_OWNERSHIP_CONFLATED",
    "CODE_PRODUCTION_SCOPE",
    "CODE_RAW_INVOKE",
    "CODE_UNKNOWN_SIDE_EFFECT",
    "CODE_WORKER_UNAVAILABLE",
    "EVAL_ARTIFACT_NAMESPACE",
    "EVAL_EVENT_NAMESPACE",
    "EVAL_OWNER_KIND",
    "EVAL_SIDE_EFFECT_COMPUTE",
    "EVAL_SIDE_EFFECT_DRAFT",
    "EVAL_SIDE_EFFECT_NONE",
    "EVAL_SIDE_EFFECT_READ",
    "EVAL_SIDE_EFFECT_UNKNOWN",
    "EVAL_SIDE_EFFECT_WRITE_EXTERNAL",
    "EVAL_SIDE_EFFECT_WRITE_LOCAL",
    "EvalCallRecord",
    "EvalDataProvider",
    "EvalExecutionScope",
    "EvalMemoryProvider",
    "ISOLATION_BREACH",
    "IsolationBreach",
    "IsolationError",
    "IsolationWrappedGateway",
    "PRODUCTION_TRIPWIRE_SITES",
    "RuntimeIsolationContext",
    "SIMULATED_SIDE_EFFECTS",
    "assert_evaluation_object_key",
    "assert_not_production_scope_for_eval",
    "build_isolation_context",
    "classify_side_effect",
    "eval_execution_scope",
    "get_active_eval_scope",
    "is_eval_scope_active",
    "is_evaluation_object_key",
    "isolation_digest",
    "production_write_mode_ignored",
    "reject_raw_tool_invoke",
    "require_active_eval_scope",
    "resolve_eval_dispatch",
    "tripwire_production_object_key",
    "tripwire_production_writer",
    "validate_execution_identity",
    "validate_isolation_context",
]
