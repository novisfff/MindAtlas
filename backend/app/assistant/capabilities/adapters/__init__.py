"""Capability type adapters (Plan 02).

Adapters receive an already resolved, validated, and authorized target and produce
one normalized ``CapabilityResult``. They never authorize, allocate Provider
aliases, or re-resolve by name/latest.
"""

from __future__ import annotations

from app.assistant.capabilities.adapters.base import CapabilityAdapter
from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter

__all__ = [
    "CapabilityAdapter",
    "ToolCapabilityAdapter",
]
