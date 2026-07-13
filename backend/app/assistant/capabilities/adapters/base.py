"""Shared adapter port for Tool / Workflow / Agent capability execution."""

from __future__ import annotations

from typing import Literal, Protocol

from app.assistant.capabilities.contracts import CapabilityResult
from app.assistant.capabilities.ports import CapabilityAdapterRequest, CapabilityRuntimePorts


class CapabilityAdapter(Protocol):
    """Process-local adapter invoked only after Gateway allow + permit consume."""

    capability_type: Literal["tool", "workflow", "agent"]

    def execute(
        self,
        request: CapabilityAdapterRequest,
        *,
        ports: CapabilityRuntimePorts,
    ) -> CapabilityResult: ...


__all__ = ["CapabilityAdapter"]
