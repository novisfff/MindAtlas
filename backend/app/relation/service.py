from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.common.exceptions import ApiException
from app.entry.models import Entry
from app.relation.models import Relation, RelationType
from app.relation.schemas import RelationRequest


class RelationService:
    def __init__(self, db: Session):
        self.db = db

    def _ensure_entry_exists(self, entry_id: UUID, *, role: str) -> None:
        exists = self.db.query(Entry.id).filter(Entry.id == entry_id).first()
        if not exists:
            raise ApiException(status_code=404, code=40400, message=f"{role} entry not found: {entry_id}")

    def _ensure_relation_type_exists(self, relation_type_id: UUID) -> None:
        exists = self.db.query(RelationType.id).filter(RelationType.id == relation_type_id).first()
        if not exists:
            raise ApiException(status_code=404, code=40400, message=f"RelationType not found: {relation_type_id}")

    def _validate_request(self, request: RelationRequest) -> None:
        self._ensure_entry_exists(request.source_entry_id, role="Source")
        self._ensure_entry_exists(request.target_entry_id, role="Target")
        self._ensure_relation_type_exists(request.relation_type_id)

    def _commit_and_reload(self, relation: Relation, *, action: str) -> Relation:
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=400,
                code=40002,
                message=f"Failed to {action} relation due to integrity constraints",
            ) from exc
        self.db.refresh(relation)
        return self.find_by_id(relation.id)

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
        self._validate_request(request)
        relation = Relation(
            source_entry_id=request.source_entry_id,
            target_entry_id=request.target_entry_id,
            relation_type_id=request.relation_type_id,
            description=request.description,
        )
        self.db.add(relation)
        return self._commit_and_reload(relation, action="create")

    def update(self, id: UUID, request: RelationRequest) -> Relation:
        relation = self.find_by_id(id)
        self._validate_request(request)

        relation.source_entry_id = request.source_entry_id
        relation.target_entry_id = request.target_entry_id
        relation.relation_type_id = request.relation_type_id
        relation.description = request.description

        return self._commit_and_reload(relation, action="update")

    def delete(self, id: UUID) -> None:
        relation = self.find_by_id(id)
        self.db.delete(relation)
        self.db.commit()
