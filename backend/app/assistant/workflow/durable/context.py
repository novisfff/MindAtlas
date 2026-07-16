"""Ephemeral Workflow runtime context (never durable).

Reconstructed after every claim from application wiring plus exact frozen
references. Must never enter a Checkpoint, Artifact, Provider message, event,
log, or model-visible result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.assistant.workflow.engine.runtime_dependency_resolver import (
        ExactRuntimeDependencyResolver,
    )


class ProviderResolver(Protocol):
    """Structural type for exact provider/model reconstruction."""


class CapabilityGateway(Protocol):
    """Structural type for Gateway invocation (never business-tool direct)."""


class ArtifactStore(Protocol):
    """Structural type for private Artifact store."""


class EventSink(Protocol):
    """Structural type for durable event append."""


class CancellationProbe(Protocol):
    """Structural type for cooperative cancellation checks."""


class Clock(Protocol):
    """Structural type for time source (tests inject fixed clocks)."""


class DurableNodeAdapterRegistry(Protocol):
    """Structural type for node adapter lookup."""


@dataclass(slots=True)
class EphemeralWorkflowContext:
    """Process-local wiring for one claimed durable unit. Never serialize."""

    session_factory: Callable[[], Any]
    provider_resolver: Any
    capability_gateway: Any
    artifact_store: Any
    event_sink: Any
    cancellation_probe: Any
    clock: Any
    exact_dependency_resolver: Any
    node_adapters: Any


__all__ = [
    "ArtifactStore",
    "CancellationProbe",
    "CapabilityGateway",
    "Clock",
    "DurableNodeAdapterRegistry",
    "EphemeralWorkflowContext",
    "EventSink",
    "ProviderResolver",
]
