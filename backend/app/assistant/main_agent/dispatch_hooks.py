"""Dispatch hooks bridging Main Agent control effects into Provider Loop Manifest lineage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.assistant.domain.contracts import ResolvedRunManifestRevision
from app.assistant.provider_loop.contracts import ProviderDispatchRequest
from app.assistant.capabilities.contracts import CapabilityResult


class _ControlEffectSource(Protocol):
    def take_manifest_effect(self, *, call_id: str) -> Any | None: ...

    def discard_pending(self, *, call_id: str) -> None: ...


def next_manifest_from_control_effect(
    control_runtime: _ControlEffectSource,
    request: ProviderDispatchRequest,
    result: CapabilityResult,
    *,
    discard_effect: Callable[[str, str], None] | None = None,
) -> ResolvedRunManifestRevision:
    """If skill.inject staged a child Manifest for this call, return it; else unchanged.

    Used as GatewayToolDispatcher.next_manifest_hook so lineage validation and
    ManifestEffectLifecyclePort.accept see the proposed child after control success.
    The control value is transferred exactly once. Lifecycle accept/discard
    still owns the staged activation package after this transfer.
    """
    call_id = request.call.call_id

    def _discard(reason_code: str) -> None:
        try:
            control_runtime.discard_pending(call_id=call_id)
        finally:
            if discard_effect is not None:
                discard_effect(call_id, reason_code)

    try:
        effect = control_runtime.take_manifest_effect(call_id=call_id)
    except BaseException:
        _discard("control_effect_take_error")
        raise
    if effect is None:
        return request.current_manifest
    if result.status != "completed":
        _discard("control_result_not_completed")
        return request.current_manifest
    proposed = getattr(effect, "proposed_manifest", None)
    if not isinstance(proposed, ResolvedRunManifestRevision):
        _discard("control_effect_invalid")
        return request.current_manifest
    if proposed.run_id != request.current_manifest.run_id:
        _discard("control_effect_run_mismatch")
        return request.current_manifest
    return proposed


__all__ = ["next_manifest_from_control_effect"]
