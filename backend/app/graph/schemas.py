from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from app.common.schemas import CamelModel
from app.entry.models import TimeMode


class GraphNode(CamelModel):
    id: str
    label: str
    type_id: str
    type_name: str
    color: Optional[str] = None
    created_at: Optional[datetime] = None
    summary: Optional[str] = None
    time_mode: Optional[TimeMode] = None
    time_at: Optional[datetime] = None
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None


class GraphLink(CamelModel):
    id: str
    source: str
    target: str
    label: str
    color: Optional[str] = None


class GraphData(CamelModel):
    nodes: List[GraphNode]
    links: List[GraphLink]
