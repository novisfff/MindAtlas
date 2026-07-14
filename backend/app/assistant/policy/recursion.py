"""Plan 05 process-local capability/agent call frames and recursion guards.

Ephemeral only: Plan 06 persists portable frame values at checkpoints.
Provider Loop and Capability packages must not import ledger state; this module
exposes pure checks + a process-local port that Main Agent / tests inject via
``CapabilityRuntimePorts.call_frames``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, ContextManager, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import JsonValue, sha256_canonical_json

CapabilityFrameType = Literal["tool", "workflow", "agent"]
FrameOwnerKind = Literal["main_agent", "skill_version"]

# Stable deny reason codes (budget depth + recursion).
REASON_CAPABILITY_DEPTH = "budget_exhausted_capability_depth"
REASON_AGENT_DEPTH = "budget_exhausted_agent_depth"
REASON_RECURSION_DENIED = "recursion_denied"
REASON_AGENT_CYCLE = "agent_cycle_denied"
REASON_MAIN_AGENT_RESTART = "main_agent_restart_denied"

# Default production ceilings (mirror RunBudgetLimits defaults).
DEFAULT_MAX_CAPABILITY_DEPTH = 4
DEFAULT_MAX_AGENT_DEPTH = 2

_DIGEST_RE_LEN = 64


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_digest(value: Any, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_RE_LEN
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return value


# ---------------------------------------------------------------------------
# Frame contract
# ---------------------------------------------------------------------------


class CapabilityCallFrame(FrozenContract):
    """Portable call-frame values for depth/cycle guards (Plan 05 §10.1)."""

    call_id: str
    capability_type: CapabilityFrameType
    domain_key: str
    target_identity: str
    target_version_id: UUID | None
    binding_contract_digest: str
    owner_kind: FrameOwnerKind
    owner_version_id: UUID
    capability_depth: int
    agent_depth: int
    frame_digest: str

    @field_validator("call_id", "domain_key", "target_identity")
    @classmethod
    def _non_empty(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("binding_contract_digest", "frame_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("capability_depth", "agent_depth")
    @classmethod
    def _depths(cls, value: int, info: Any) -> int:
        return _require_positive_int(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _digest_matches(self) -> CapabilityCallFrame:
        expected = compute_frame_digest(
            call_id=self.call_id,
            capability_type=self.capability_type,
            domain_key=self.domain_key,
            target_identity=self.target_identity,
            target_version_id=self.target_version_id,
            binding_contract_digest=self.binding_contract_digest,
            owner_kind=self.owner_kind,
            owner_version_id=self.owner_version_id,
            capability_depth=self.capability_depth,
            agent_depth=self.agent_depth,
        )
        if self.frame_digest != expected:
            raise ValueError("frame_digest does not match frame body")
        return self


def compute_frame_digest(
    *,
    call_id: str,
    capability_type: str,
    domain_key: str,
    target_identity: str,
    target_version_id: UUID | None,
    binding_contract_digest: str,
    owner_kind: str,
    owner_version_id: UUID,
    capability_depth: int,
    agent_depth: int,
) -> str:
    """Exact frame digest (excludes frame_digest itself)."""
    payload: dict[str, JsonValue] = {
        "callId": call_id,
        "capabilityType": capability_type,
        "domainKey": domain_key,
        "targetIdentity": target_identity,
        "targetVersionId": str(target_version_id) if target_version_id is not None else None,
        "bindingContractDigest": binding_contract_digest,
        "ownerKind": owner_kind,
        "ownerVersionId": str(owner_version_id),
        "capabilityDepth": capability_depth,
        "agentDepth": agent_depth,
    }
    return sha256_canonical_json(payload)


def build_capability_call_frame(
    *,
    call_id: str,
    capability_type: CapabilityFrameType,
    domain_key: str,
    target_identity: str,
    target_version_id: UUID | None,
    binding_contract_digest: str,
    owner_kind: FrameOwnerKind,
    owner_version_id: UUID,
    capability_depth: int,
    agent_depth: int,
) -> CapabilityCallFrame:
    digest = compute_frame_digest(
        call_id=call_id,
        capability_type=capability_type,
        domain_key=domain_key,
        target_identity=target_identity,
        target_version_id=target_version_id,
        binding_contract_digest=binding_contract_digest,
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        capability_depth=capability_depth,
        agent_depth=agent_depth,
    )
    return CapabilityCallFrame(
        call_id=call_id,
        capability_type=capability_type,
        domain_key=domain_key,
        target_identity=target_identity,
        target_version_id=target_version_id,
        binding_contract_digest=binding_contract_digest,
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        capability_depth=capability_depth,
        agent_depth=agent_depth,
        frame_digest=digest,
    )


# ---------------------------------------------------------------------------
# Pure depth / cycle checks
# ---------------------------------------------------------------------------


def compute_next_depths(
    stack: Sequence[CapabilityCallFrame] | Sequence[Any],
    *,
    capability_type: str,
) -> tuple[int, int]:
    """Return ``(capability_depth, agent_depth)`` for a call about to be pushed.

    Capability depth = current frame count + 1.
    Agent depth counts only ``capability_type=agent`` frames (plus one when this
    call is itself an agent). Non-agent calls report max(1, enclosing agent frames)
    so budget validators (depth >= 1) stay satisfied without inventing agent depth.
    """
    capability_depth = len(stack) + 1
    agent_frames = sum(
        1 for frame in stack if getattr(frame, "capability_type", None) == "agent"
    )
    if capability_type == "agent":
        agent_depth = agent_frames + 1
    else:
        agent_depth = max(1, agent_frames)
    return capability_depth, agent_depth


def active_agent_version_ids(
    stack: Sequence[CapabilityCallFrame] | Sequence[Any],
) -> frozenset[UUID]:
    """Exact Agent target versions present in the active Agent frame stack."""
    ids: set[UUID] = set()
    for frame in stack:
        if getattr(frame, "capability_type", None) != "agent":
            continue
        version_id = getattr(frame, "target_version_id", None)
        if isinstance(version_id, UUID):
            ids.add(version_id)
    return frozenset(ids)


def is_main_agent_restart_target(
    *,
    capability_type: str,
    target_identity: str,
    domain_key: str | None = None,
) -> bool:
    """True when the call would restart/re-enter the Main Agent loop.

    Production Agent/Workflow dependencies must not re-enter Main Agent.
    Heuristics are identity-prefix based; classification already marks nested
    Agent/Main-Agent restart bindings unavailable under Plan 02.
    """
    del capability_type  # identity is authoritative
    identity = (target_identity or "").strip().lower()
    domain = (domain_key or "").strip().lower()
    if identity.startswith("main-agent") or identity.startswith("main_agent"):
        return True
    if "main-agent-restart" in identity or "main_agent_restart" in identity:
        return True
    if domain in {"main_agent", "main-agent", "main.agent.restart"}:
        return True
    if domain.startswith("main_agent.") or domain.startswith("main-agent."):
        return True
    return False


def stack_has_agent_or_workflow(
    stack: Sequence[CapabilityCallFrame] | Sequence[Any],
) -> bool:
    for frame in stack:
        if getattr(frame, "capability_type", None) in {"agent", "workflow"}:
            return True
    return False


def evaluate_recursion_guard(
    stack: Sequence[CapabilityCallFrame] | Sequence[Any],
    *,
    capability_type: str,
    target_identity: str,
    target_version_id: UUID | None,
    domain_key: str | None = None,
    max_capability_depth: int = DEFAULT_MAX_CAPABILITY_DEPTH,
    max_agent_depth: int = DEFAULT_MAX_AGENT_DEPTH,
) -> str | None:
    """Return a stable deny reason code, or None when admission is allowed.

    Checks (before reservation / mark_started):
    1. Capability depth = len(stack)+1 against max_capability_depth
    2. Agent depth against max_agent_depth (agent frames only)
    3. Exact Agent target version already on the active Agent frame stack
    4. Main Agent restart from an Agent/Workflow dependency frame
    """
    capability_depth, agent_depth = compute_next_depths(
        stack, capability_type=capability_type
    )
    if capability_depth > max_capability_depth:
        return REASON_CAPABILITY_DEPTH
    if agent_depth > max_agent_depth:
        return REASON_AGENT_DEPTH

    if capability_type == "agent" and target_version_id is not None:
        if target_version_id in active_agent_version_ids(stack):
            return REASON_AGENT_CYCLE

    if stack_has_agent_or_workflow(stack) and is_main_agent_restart_target(
        capability_type=capability_type,
        target_identity=target_identity,
        domain_key=domain_key,
    ):
        return REASON_MAIN_AGENT_RESTART

    return None


# ---------------------------------------------------------------------------
# Port protocol + implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class CapabilityCallFramePort(Protocol):
    """Process-local frame stack. Never place locks on frozen Provider contracts."""

    def current(self) -> tuple[CapabilityCallFrame, ...]: ...

    def push(self, frame: CapabilityCallFrame) -> ContextManager[None]: ...


class NoOpCapabilityCallFramePort:
    """Default no-op frame port; preserves Plan 02 Gateway / OpenClaw behavior."""

    def current(self) -> tuple[CapabilityCallFrame, ...]:
        return ()

    @contextmanager
    def push(self, frame: CapabilityCallFrame) -> Iterator[None]:
        del frame
        yield


_NOOP_CALL_FRAMES = NoOpCapabilityCallFramePort()


class ProcessLocalCapabilityCallFramePort:
    """Thread-safe process-local stack for one admitted Run's call frames."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stack: list[CapabilityCallFrame] = []

    def current(self) -> tuple[CapabilityCallFrame, ...]:
        with self._lock:
            return tuple(self._stack)

    @contextmanager
    def push(self, frame: CapabilityCallFrame) -> Iterator[None]:
        if not isinstance(frame, CapabilityCallFrame):
            raise TypeError("frame must be CapabilityCallFrame")
        with self._lock:
            self._stack.append(frame)
        try:
            yield
        finally:
            with self._lock:
                if not self._stack:
                    return
                # Pop exact frame by identity (call_id + frame_digest) for safety.
                for index in range(len(self._stack) - 1, -1, -1):
                    top = self._stack[index]
                    if (
                        top.call_id == frame.call_id
                        and top.frame_digest == frame.frame_digest
                    ):
                        del self._stack[index]
                        break
                else:
                    # Fall back to LIFO pop when identity missing (should not happen).
                    if self._stack and self._stack[-1].call_id == frame.call_id:
                        self._stack.pop()


def resolve_frame_port(ports: Any) -> CapabilityCallFramePort:
    """Resolve optional call_frames from CapabilityRuntimePorts / SimpleNamespace."""
    port = getattr(ports, "call_frames", None)
    if port is None:
        return _NOOP_CALL_FRAMES
    return port  # type: ignore[return-value]


def depths_from_ports(
    ports: Any,
    *,
    capability_type: str,
) -> tuple[int, int]:
    """Capability/agent depths for reservation from the active frame stack."""
    port = resolve_frame_port(ports)
    return compute_next_depths(port.current(), capability_type=capability_type)


__all__ = [
    "DEFAULT_MAX_AGENT_DEPTH",
    "DEFAULT_MAX_CAPABILITY_DEPTH",
    "REASON_AGENT_CYCLE",
    "REASON_AGENT_DEPTH",
    "REASON_CAPABILITY_DEPTH",
    "REASON_MAIN_AGENT_RESTART",
    "REASON_RECURSION_DENIED",
    "CapabilityCallFrame",
    "CapabilityCallFramePort",
    "NoOpCapabilityCallFramePort",
    "ProcessLocalCapabilityCallFramePort",
    "active_agent_version_ids",
    "build_capability_call_frame",
    "compute_frame_digest",
    "compute_next_depths",
    "depths_from_ports",
    "evaluate_recursion_guard",
    "is_main_agent_restart_target",
    "resolve_frame_port",
    "stack_has_agent_or_workflow",
]
