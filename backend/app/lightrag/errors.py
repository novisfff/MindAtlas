"""LightRAG integration domain errors (Phase 5)."""
from __future__ import annotations


class LightRagError(RuntimeError):
    """Base error for LightRAG integration."""


class LightRagNotEnabledError(LightRagError):
    """Raised when LightRAG is disabled by configuration."""


class LightRagConfigError(LightRagError):
    """Raised when required LightRAG configuration is missing/invalid."""


class LightRagDependencyError(LightRagError):
    """Raised when required dependencies (lightrag-hku/neo4j) are missing."""
