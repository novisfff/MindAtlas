"""Request-scoped capability runtime composition (Plan 02 Task 7).

``build_capability_runtime`` wires Registry + Policy + one adapter per type.
Production OpenClaw construction injects only the OpenClaw verifier mapping;
tests inject fixture verifiers. No permissive global verifier is registered
here or in package ``__init__``.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
from app.assistant.capabilities.classification import CapabilityClassifier
from app.assistant.capabilities.contracts import EvidenceVerifierKey
from app.assistant.capabilities.gateway import CapabilityGateway
from app.assistant.capabilities.policy import (
    AuthorizationEvidenceVerifier,
    CapabilityPolicyEngine,
)
from app.assistant.capabilities.registry import CapabilityRegistry


def build_adapter_registry() -> dict[str, object]:
    """Construct exactly one adapter instance per capability type."""
    return {
        "tool": ToolCapabilityAdapter(),
        "workflow": WorkflowCapabilityAdapter(),
        "agent": AgentCapabilityAdapter(),
    }


def build_capability_runtime(
    *,
    db: Session,
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier],
    locale: str | None = None,
    classifier: CapabilityClassifier | None = None,
) -> CapabilityGateway:
    """Build a request/session-scoped CapabilityGateway.

    The Gateway and its Registry share the supplied Session. Callers (OpenClaw
    ``runtime_worker``) must create this factory *inside* the worker with a
    worker-owned Session and close both before returning to the event loop.
    """
    registry = CapabilityRegistry(db, locale=locale, classifier=classifier)
    policy = CapabilityPolicyEngine(evidence_verifiers)
    adapters = build_adapter_registry()
    return CapabilityGateway(registry=registry, policy=policy, adapters=adapters)


__all__ = [
    "build_adapter_registry",
    "build_capability_runtime",
]
