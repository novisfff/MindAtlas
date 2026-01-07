from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.common.exceptions import ApiException
from app.relation.models import Relation
from app.relation.schemas import RelationRequest


class RelationService:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> List[Relation]:
        return (
            self.db.query(Relation)
            .options(
                joinedload(Relation.source_entry),
                joinedload(Relation.target_entry),
                joinedload(Relation.relation_type),
            )
            .all()
        )

    def find_by_id(self, id: UUID) -> Relation:
        relation = (
            self.db.query(Relation)
            .options(
                joinedload(Relation.source_entry),
                joinedload(Relation.target_entry),
                joinedload(Relation.relation_type),
            )
            .filter(Relation.id == id)
            .first()
        )
        if not relation:
            raise ApiException(status_code=404, code=40400, message=f"Relation not found: {id}")
        return relation

    def find_by_entry(self, entry_id: UUID) -> List[Relation]:
        return (
            self.db.query(Relation)
            .options(
                joinedload(Relation.source_entry),
                joinedload(Relation.target_entry),
                joinedload(Relation.relation_type),
            )
            .filter((Relation.source_entry_id == entry_id) | (Relation.target_entry_id == entry_id))
            .all()
        )

    def create(self, request: RelationRequest) -> Relation:
        # Validate entries exist (will be done by foreign key constraint)
        relation = Relation(
            source_entry_id=request.source_entry_id,
            target_entry_id=request.target_entry_id,
            relation_type_id=request.relation_type_id,
            description=request.description,
        )
        self.db.add(relation)
        self.db.commit()
        self.db.refresh(relation)
        return self.find_by_id(relation.id)

    def update(self, id: UUID, request: RelationRequest) -> Relation:
        relation = self.find_by_id(id)

        relation.source_entry_id = request.source_entry_id
        relation.target_entry_id = request.target_entry_id
        relation.relation_type_id = request.relation_type_id
        relation.description = request.description

        self.db.commit()
        self.db.refresh(relation)
        return self.find_by_id(relation.id)

    def delete(self, id: UUID) -> None:
        relation = self.find_by_id(id)
        self.db.delete(relation)
        self.db.commit()
