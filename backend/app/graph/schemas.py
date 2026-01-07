from __future__ import annotations

from typing import List, Optional

from app.common.schemas import CamelModel


class GraphNode(CamelModel):
    id: str
    label: str
    type_id: str
    type_name: str
    color: Optional[str] = None


class GraphLink(CamelModel):
    id: str
    source: str
    target: str
    label: str
    color: Optional[str] = None


class GraphData(CamelModel):
    nodes: List[GraphNode]
    links: List[GraphLink]
