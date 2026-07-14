"""Dispatch hooks bridging Main Agent control effects into Provider Loop Manifest lineage."""

from __future__ import annotations

from typing import Any, Protocol

from app.assistant.domain.contracts import ResolvedRunManifestRevision
from app.assistant.provider_loop.contracts import ProviderDispatchRequest
from app.assistant.capabilities.contracts import CapabilityResult


class _ControlEffectSource(Protocol):
    def peek_manifest_effect(self, *, call_id: str) -> Any | None: ...


def next_manifest_from_control_effect(
    control_runtime: _ControlEffectSource,
    request: ProviderDispatchRequest,
    result: CapabilityResult,
) -> ResolvedRunManifestRevision:
    """If skill.inject staged a child Manifest for this call, return it; else unchanged.

    Used as GatewayToolDispatcher.next_manifest_hook so lineage validation and
    ManifestEffectLifecyclePort.accept see the proposed child after control success.
    Peek is non-destructive; lifecycle accept/discard still owns take/finalize.
    """
    del result  # success/failure already decided by Gateway; effect only on completed inject
    effect = control_runtime.peek_manifest_effect(call_id=request.call.call_id)
    if effect is None:
        return request.current_manifest
    proposed = getattr(effect, "proposed_manifest", None)
    if not isinstance(proposed, ResolvedRunManifestRevision):
        return request.current_manifest
    if proposed.run_id != request.current_manifest.run_id:
        return request.current_manifest
    return proposed


__all__ = ["next_manifest_from_control_effect"]
