"""Pydantic schemas for LightRAG API (Phase 5)."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel

# Query modes supported by LightRAG
LightRagQueryMode = Literal["naive", "local", "global", "hybrid", "mix"]


class LightRagQueryRequest(CamelModel):
    """Request payload for LightRAG query endpoint."""

    query: str = Field(..., min_length=1, description="User query text")
    mode: LightRagQueryMode = Field(default="hybrid", description="Query mode")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of results to return")
    stream: bool = Field(default=False, description="Whether to return streaming response (SSE)")


class LightRagSource(CamelModel):
    """Source document chunk."""

    doc_id: str | None = None
    content: str | None = None
    score: float | None = None
    metadata: dict | None = None


class LightRagQueryMetadata(CamelModel):
    """Query metadata."""

    mode: LightRagQueryMode
    top_k: int
    latency_ms: int
    cache_hit: bool = False


class LightRagQueryResponse(CamelModel):
    """Response payload for LightRAG query."""

    answer: str
    sources: list[LightRagSource] = Field(default_factory=list)
    metadata: LightRagQueryMetadata


class LightRagEntryRelationRecommendationItem(CamelModel):
    """A single recommended Entry relation candidate."""

    target_entry_id: UUID = Field(..., description="Recommended Entry ID")
    relation_type: str | None = Field(
        default=None,
        max_length=64,
        description="Predicted RelationType.code for the relation from source -> target",
    )
    score: float = Field(..., ge=0.0, le=1.0, description="Normalized similarity score (0.0-1.0)")


class LightRagEntryRelationRecommendationsResponse(CamelModel):
    """Response payload for Entry relation recommendations."""

    items: list[LightRagEntryRelationRecommendationItem] = Field(default_factory=list)


class GraphEntity(CamelModel):
    """Entity extracted from knowledge graph context."""

    name: str = Field(..., description="Entity name")
    type: str | None = Field(default=None, description="Entity type (concept, method, person, etc.)")
    description: str | None = Field(default=None, description="Entity description")
    entry_id: str | None = Field(default=None, description="Associated Entry UUID if available")


class GraphRelationship(CamelModel):
    """Relationship extracted from knowledge graph context."""

    source: str = Field(..., description="Source entity name")
    target: str = Field(..., description="Target entity name")
    description: str | None = Field(default=None, description="Relationship description")
    keywords: str | None = Field(default=None, description="Relationship keywords")
    entry_id: str | None = Field(default=None, description="Associated Entry UUID if available")


class GraphContext(CamelModel):
    """Knowledge graph context for AI augmentation."""

    entities: list[GraphEntity] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)
