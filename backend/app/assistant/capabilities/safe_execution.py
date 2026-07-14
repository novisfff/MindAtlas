"""Safe diagnostics and event projection for Capability Workflow execution.

Capability mode must never forward raw node input/output, Tool args/results,
prompts, Schema bodies, exception text, HTTP/provider bodies, or credentials.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from app.assistant.capabilities.contracts import (
    CapabilityEventMetadata,
    CapabilityRuntimeEvent,
)

logger = logging.getLogger(__name__)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_SAFE_STATUS = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _safe_token(value: Any, *, fallback: str = "unknown", max_len: int = 64) -> str:
    text = str(value or "").strip()
    text = _CONTROL_RE.sub("", text)
    if not text:
        text = fallback
    if len(text) > max_len:
        text = text[:max_len]
    if not _SAFE_STATUS.fullmatch(text):
        # Collapse to a conservative token when the value is untrusted.
        cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)[:max_len].strip("_")
        return cleaned or fallback
    return text


def safe_log_exception(
    *,
    stage: str,
    call_id: str | None = None,
    target_identity: str | None = None,
    node_id: str | None = None,
    node_type: str | None = None,
    exc: BaseException | None = None,
    level: int = logging.ERROR,
) -> None:
    """Log only stable identifiers and exception class name."""
    exc_class = type(exc).__name__ if exc is not None else "-"
    logger.log(
        level,
        "capability_safe_execution stage=%s call_id=%s target_identity=%s "
        "node_id=%s node_type=%s exc_class=%s",
        _safe_token(stage, fallback="stage"),
        call_id or "-",
        target_identity or "-",
        node_id or "-",
        node_type or "-",
        exc_class,
    )


def project_child_event(
    *,
    call_id: str,
    capability_key: str,
    target_identity: str,
    child_event_type: str,
    safe_status: str | None = None,
    child_node_id: str | None = None,
    child_node_type: str | None = None,
    binding_contract_digest: str | None = None,
    dependency_closure_digest: str | None = None,
    duration_ms: float | None = None,
    compatibility_only: bool = False,
) -> CapabilityRuntimeEvent:
    """Build a secret-safe capability.child_event (no payloads/args/results)."""
    return CapabilityRuntimeEvent(
        event_type="capability.child_event",
        call_id=call_id,
        capability_key=capability_key,
        target_identity=target_identity,
        capability_type="workflow",
        safe_status=_safe_token(safe_status, fallback="ok") if safe_status else None,
        child_event_type=_safe_token(child_event_type, fallback="event", max_len=64),
        metadata=CapabilityEventMetadata(
            binding_contract_digest=binding_contract_digest,
            dependency_closure_digest=dependency_closure_digest,
            duration_ms=duration_ms,
            child_node_id=_safe_token(child_node_id, fallback="node") if child_node_id else None,
            child_node_type=_safe_token(child_node_type, fallback="node") if child_node_type else None,
            compatibility_only=bool(compatibility_only),
        ),
    )


def make_safe_child_event_forwarder(
    *,
    emit: Callable[[CapabilityRuntimeEvent], None],
    call_id: str,
    capability_key: str,
    target_identity: str,
    binding_contract_digest: str | None,
    dependency_closure_digest: str | None,
) -> dict[str, Callable[..., None]]:
    """Return engine callback handlers that forward only safe child projections."""

    def _node_start(node_id: str, node_type: str, **_extra: Any) -> None:
        emit(
            project_child_event(
                call_id=call_id,
                capability_key=capability_key,
                target_identity=target_identity,
                child_event_type="node_start",
                safe_status="started",
                child_node_id=str(node_id or ""),
                child_node_type=str(node_type or ""),
                binding_contract_digest=binding_contract_digest,
                dependency_closure_digest=dependency_closure_digest,
            )
        )

    def _node_end(node_id: str, status: str, **_extra: Any) -> None:
        emit(
            project_child_event(
                call_id=call_id,
                capability_key=capability_key,
                target_identity=target_identity,
                child_event_type="node_end",
                safe_status=str(status or "ok"),
                child_node_id=str(node_id or ""),
                binding_contract_digest=binding_contract_digest,
                dependency_closure_digest=dependency_closure_digest,
            )
        )

    def _tool_start(tool_call_id: str, tool_name: str, args: Any = None, **_extra: Any) -> None:
        # Intentionally drop args.
        _ = (tool_call_id, args)
        emit(
            project_child_event(
                call_id=call_id,
                capability_key=capability_key,
                target_identity=target_identity,
                child_event_type="tool_call_start",
                safe_status="started",
                child_node_id=_safe_token(tool_name, fallback="tool"),
                child_node_type="tool",
                binding_contract_digest=binding_contract_digest,
                dependency_closure_digest=dependency_closure_digest,
            )
        )

    def _tool_end(tool_call_id: str, status: str, result: Any = None, **_extra: Any) -> None:
        _ = (tool_call_id, result)
        emit(
            project_child_event(
                call_id=call_id,
                capability_key=capability_key,
                target_identity=target_identity,
                child_event_type="tool_call_end",
                safe_status=str(status or "completed"),
                child_node_type="tool",
                binding_contract_digest=binding_contract_digest,
                dependency_closure_digest=dependency_closure_digest,
            )
        )

    def _human_requested(payload: dict[str, Any]) -> None:
        node_id = ""
        if isinstance(payload, dict):
            node_id = str(payload.get("nodeId") or payload.get("node_id") or "")
        emit(
            project_child_event(
                call_id=call_id,
                capability_key=capability_key,
                target_identity=target_identity,
                child_event_type="human_approval_requested",
                safe_status="requested",
                child_node_id=node_id or None,
                child_node_type="human_in_loop",
                binding_contract_digest=binding_contract_digest,
                dependency_closure_digest=dependency_closure_digest,
                compatibility_only=True,
            )
        )

    def _human_resolved(payload: dict[str, Any]) -> None:
        node_id = ""
        status = "resolved"
        if isinstance(payload, dict):
            node_id = str(payload.get("nodeId") or payload.get("node_id") or "")
            status = str(payload.get("status") or status)
        emit(
            project_child_event(
                call_id=call_id,
                capability_key=capability_key,
                target_identity=target_identity,
                child_event_type="human_approval_resolved",
                safe_status=status,
                child_node_id=node_id or None,
                child_node_type="human_in_loop",
                binding_contract_digest=binding_contract_digest,
                dependency_closure_digest=dependency_closure_digest,
                compatibility_only=True,
            )
        )

    def _ignore(*_args: Any, **_kwargs: Any) -> None:
        return None

    return {
        "on_node_start": _node_start,
        "on_node_end": _node_end,
        "on_tool_call_start": _tool_start,
        "on_tool_call_end": _tool_end,
        "on_human_approval_requested": _human_requested,
        "on_human_approval_resolved": _human_resolved,
        # Deltas / snapshots / analysis may carry raw content — drop entirely.
        "on_node_output_delta": _ignore,
        "on_node_snapshot": _ignore,
        "on_analysis_start": _ignore,
        "on_analysis_delta": _ignore,
        "on_analysis_end": _ignore,
        "on_branch_decision": _ignore,
    }


def safe_error_message(*, code: str, fallback: str = "workflow execution failed") -> str:
    token = _safe_token(code, fallback="execution_failed", max_len=64)
    text = f"workflow {token}"
    return text[:256] if text else fallback


__all__ = [
    "make_safe_child_event_forwarder",
    "project_child_event",
    "safe_error_message",
    "safe_log_exception",
]
