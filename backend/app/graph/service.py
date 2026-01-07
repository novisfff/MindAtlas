from __future__ import annotations

from typing import Set

from sqlalchemy.orm import Session, joinedload

from app.entry.models import Entry
from app.graph.schemas import GraphData, GraphLink, GraphNode
from app.relation.models import Relation


class GraphService:
    def __init__(self, db: Session):
        self.db = db

    def get_graph_data(self) -> GraphData:
        # Fetch all entries with their types
        entries = self.db.query(Entry).options(
            joinedload(Entry.type)
        ).all()

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
            color=entry.type.color
        )

    def _to_link(self, relation: Relation) -> GraphLink:
        return GraphLink(
            id=str(relation.id),
            source=str(relation.source_entry_id),
            target=str(relation.target_entry_id),
            label=relation.relation_type.name,
            color=relation.relation_type.color
        )
