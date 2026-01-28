from __future__ import annotations

from datetime import datetime
from typing import Set

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.entry.models import Entry, TimeMode
from app.graph.schemas import GraphData, GraphLink, GraphNode
from app.relation.models import Relation


class GraphService:
    def __init__(self, db: Session):
        self.db = db

    def get_graph_data(
        self,
        *,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> GraphData:
        # Fetch entries with their types (optionally time-filtered)
        entries_query = self.db.query(Entry).options(joinedload(Entry.type))

        if time_from is not None or time_to is not None:
            # Filter rules:
            # - NONE: always include
            # - POINT: time_at within [time_from, time_to]
            # - RANGE: [time_from, time_to] overlaps with query range
            point_filters = [Entry.time_mode == TimeMode.POINT, Entry.time_at.isnot(None)]
            if time_from is not None:
                point_filters.append(Entry.time_at >= time_from)
            if time_to is not None:
                point_filters.append(Entry.time_at <= time_to)
            point_clause = and_(*point_filters)

            range_filters = [
                Entry.time_mode == TimeMode.RANGE,
                Entry.time_from.isnot(None),
                Entry.time_to.isnot(None),
            ]
            if time_to is not None:
                range_filters.append(Entry.time_from <= time_to)
            if time_from is not None:
                range_filters.append(Entry.time_to >= time_from)
            range_clause = and_(*range_filters)

            none_clause = Entry.time_mode == TimeMode.NONE
            entries_query = entries_query.filter(or_(none_clause, point_clause, range_clause))

        entries = entries_query.all()

        # Fetch all relations with their related entities
        relations = self.db.query(Relation).options(
            joinedload(Relation.source_entry).joinedload(Entry.type),
            joinedload(Relation.target_entry).joinedload(Entry.type),
            joinedload(Relation.relation_type)
        ).all()

        # Filter entries with graph_enabled types and convert to nodes
        nodes = []
        node_ids: Set[str] = set()

        for entry in entries:
            if entry.type and entry.type.graph_enabled:
                node = self._to_node(entry)
                nodes.append(node)
                node_ids.add(node.id)

        # Filter relations where both source and target are in nodes
        links = []
        for relation in relations:
            source_id = str(relation.source_entry_id)
            target_id = str(relation.target_entry_id)

            if source_id in node_ids and target_id in node_ids:
                links.append(self._to_link(relation))

        return GraphData(nodes=nodes, links=links)

    def _to_node(self, entry: Entry) -> GraphNode:
        return GraphNode(
            id=str(entry.id),
            label=entry.title,
            type_id=str(entry.type_id),
            type_name=entry.type.name,
            color=entry.type.color,
            created_at=entry.created_at,
            summary=entry.summary,
            time_mode=entry.time_mode,
            time_at=entry.time_at,
            time_from=entry.time_from,
            time_to=entry.time_to,
        )

    def _to_link(self, relation: Relation) -> GraphLink:
        return GraphLink(
            id=str(relation.id),
            source=str(relation.source_entry_id),
            target=str(relation.target_entry_id),
            label=relation.relation_type.name,
            color=relation.relation_type.color,
            created_at=relation.created_at,
        )
