from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.entry.models import Entry
from app.entry_type.models import EntryType
from app.relation.models import Relation
from app.stats.schemas import DashboardStats, TypeCount
from app.tag.models import Tag


class StatsService:
    def __init__(self, db: Session):
        self.db = db

    def get_dashboard_stats(self) -> DashboardStats:
        # Count total entries
        total_entries = self.db.query(func.count(Entry.id)).scalar() or 0

        # Count total tags
        total_tags = self.db.query(func.count(Tag.id)).scalar() or 0

        # Count total relations
        total_relations = self.db.query(func.count(Relation.id)).scalar() or 0

        # Entries by type
        counts = dict(
            self.db.query(Entry.type_id, func.count(Entry.id))
            .group_by(Entry.type_id)
            .all()
        )
        entry_types = self.db.query(EntryType).all()
        entries_by_type = [
            TypeCount(
                type_id=str(et.id),
                type_name=et.name,
                type_color=et.color,
                count=int(counts.get(et.id, 0) or 0),
            )
            for et in entry_types
        ]

        return DashboardStats(
            total_entries=total_entries,
            total_tags=total_tags,
            total_relations=total_relations,
            entries_by_type=entries_by_type,
        )
