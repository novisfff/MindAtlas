"""Capability type adapters (Plan 02).

Adapters receive an already resolved, validated, and authorized target and produce
one normalized ``CapabilityResult``. They never authorize, allocate Provider
aliases, or re-resolve by name/latest.

Runtime composition (``build_capability_runtime`` / adapter registry) lives in
``app.assistant.capabilities.runtime`` so adapters stay free of Gateway/policy
imports.
"""

from __future__ import annotations

from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
from app.assistant.capabilities.adapters.base import CapabilityAdapter
from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter

__all__ = [
    "AgentCapabilityAdapter",
    "CapabilityAdapter",
    "ToolCapabilityAdapter",
    "WorkflowCapabilityAdapter",
]
