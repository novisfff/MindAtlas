"""Minimal RuntimeIsolationContext helpers (contracts only for Task 3).

Task 4 owns full isolation enforcement / runner wiring. This module only
re-exports contracts and validates namespace prefixes used by persistence.
"""

from __future__ import annotations

from app.assistant.evaluation.contracts import (
    EVAL_ARTIFACT_NAMESPACE,
    EVAL_EVENT_NAMESPACE,
    EVAL_OWNER_KIND,
    RuntimeIsolationContext,
    assert_evaluation_object_key,
    is_evaluation_object_key,
)

__all__ = [
    "EVAL_ARTIFACT_NAMESPACE",
    "EVAL_EVENT_NAMESPACE",
    "EVAL_OWNER_KIND",
    "RuntimeIsolationContext",
    "assert_evaluation_object_key",
    "is_evaluation_object_key",
    "validate_isolation_context",
]


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
