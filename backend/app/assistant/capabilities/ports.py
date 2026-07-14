"""Ephemeral runtime ports and executable target handles (process-local)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityExecutionContext,
    CapabilityPolicyDecision,
    CapabilityResult,
    CapabilityRuntimeEvent,
    FrozenCapabilityBinding,
)
from app.assistant.domain.digests import JsonValue


class SingleUseDispatchPermit(Protocol):
    permit_id: str

    def consume(self, *, call_id: str, descriptor_digest: str) -> None: ...


class CancellationPort(Protocol):
    def is_cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


class CapabilityEventSink(Protocol):
    def emit(self, event: CapabilityRuntimeEvent) -> None: ...


@dataclass(frozen=True)
class CapabilityRuntimePorts:
    cancellation: CancellationPort
    events: CapabilityEventSink


@dataclass(frozen=True)
class ExecutableToolTarget:
    target_identity: str
    tool_id: UUID | None
    config_revision: int | None
    config_digest: str | None
    is_system: bool
    tool_object_or_record: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class ExecutableWorkflowVersionTarget:
    workflow_id: UUID
    version_id: UUID
    snapshot_digest: str
    parsed_published_input: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class ExecutableAgentVersionTarget:
    agent_profile_id: UUID
    version_id: UUID
    snapshot_digest: str
    parsed_snapshot: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedModelTarget:
    source_locator: str
    model_id: UUID
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    model_config_digest: str
    credential_config_digest: str


@dataclass(frozen=True)
class AuthorizedModelRuntimeConfig:
    verified: VerifiedModelTarget
    provider_protocol: str
    model_name: str
    client_or_credential_handle: object = field(repr=False, compare=False)


class ExactRuntimeDependencyResolver(Protocol):
    def require_tool(
        self,
        *,
        source_locator: str,
        tool_name: str,
    ) -> ExecutableToolTarget: ...

    def require_workflow_version(
        self,
        *,
        source_locator: str,
        workflow_id: UUID,
        version_id: UUID,
    ) -> ExecutableWorkflowVersionTarget: ...

    def require_model(
        self,
        *,
        source_locator: str,
        requested_model_id: UUID | None,
    ) -> AuthorizedModelRuntimeConfig: ...


class FrozenClosureRuntimeResolver(Protocol):
    binding_contract_digest: str
    dependency_closure_digest: str

    def bind_authorized(
        self,
        *,
        decision: CapabilityPolicyDecision,
    ) -> ExactRuntimeDependencyResolver: ...


@dataclass(frozen=True)
class MainAgentControlExecutable:
    """Marker executable for code-native Main Agent controls (no ToolRegistry)."""

    capability_key: str
    target_identity: str
    control_port: "MainAgentControlCallPort" = field(repr=False, compare=False)


@runtime_checkable
class MainAgentControlCallPort(Protocol):
    """Generic call-local control port.

    Implemented by Main Agent; capabilities must not import main_agent.
    ``take_manifest_effect`` returns a process-local pending package or None.
    """

    def execute(
        self,
        *,
        call_id: str,
        capability_key: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult: ...

    def take_manifest_effect(self, *, call_id: str) -> Any | None: ...


@dataclass(frozen=True)
class ResolvedCapabilitySurface:
    binding: FrozenCapabilityBinding
    executable: (
        ExecutableToolTarget
        | ExecutableWorkflowVersionTarget
        | ExecutableAgentVersionTarget
        | MainAgentControlExecutable
    )
    execution_closure: FrozenClosureRuntimeResolver = field(repr=False, compare=False)
    display_name: str
    description: str
    availability: CapabilityAvailability


@dataclass(frozen=True)
class ResolvedCapabilityTarget:
    descriptor: CapabilityDescriptor
    binding: FrozenCapabilityBinding
    executable: (
        ExecutableToolTarget
        | ExecutableWorkflowVersionTarget
        | ExecutableAgentVersionTarget
        | MainAgentControlExecutable
    )
    execution_closure: FrozenClosureRuntimeResolver = field(repr=False, compare=False)


@dataclass(frozen=True)
class CapabilityAdapterRequest:
    target: ResolvedCapabilityTarget
    validated_input: dict[str, JsonValue]
    context: CapabilityExecutionContext
    decision: CapabilityPolicyDecision


__all__ = [
    "AuthorizedModelRuntimeConfig",
    "CancellationPort",
    "CapabilityAdapterRequest",
    "CapabilityEventSink",
    "CapabilityRuntimePorts",
    "ExactRuntimeDependencyResolver",
    "ExecutableAgentVersionTarget",
    "ExecutableToolTarget",
    "ExecutableWorkflowVersionTarget",
    "FrozenClosureRuntimeResolver",
    "MainAgentControlCallPort",
    "MainAgentControlExecutable",
    "ResolvedCapabilitySurface",
    "ResolvedCapabilityTarget",
    "SingleUseDispatchPermit",
    "VerifiedModelTarget",
]
