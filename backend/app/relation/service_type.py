from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.relation.models import RelationType
from app.relation.schemas import RelationTypeRequest


class RelationTypeService:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> List[RelationType]:
        return self.db.query(RelationType).all()

    def find_by_id(self, id: UUID) -> RelationType:
        relation_type = self.db.query(RelationType).filter(RelationType.id == id).first()
        if not relation_type:
            raise ApiException(status_code=404, code=40400, message=f"RelationType not found: {id}")
        return relation_type

    def create(self, request: RelationTypeRequest) -> RelationType:
        existing = self.db.query(RelationType).filter(RelationType.code == request.code).first()
        if existing:
            raise ApiException(
                status_code=400,
                code=40001,
                message=f"RelationType code already exists: {request.code}"
            )

        relation_type = RelationType(
            code=request.code,
            name=request.name,
            inverse_name=request.inverse_name,
            description=request.description,
            color=request.color,
            directed=request.directed,
            enabled=request.enabled,
        )
        self.db.add(relation_type)
        self.db.commit()
        self.db.refresh(relation_type)
        return relation_type

    def update(self, id: UUID, request: RelationTypeRequest) -> RelationType:
        relation_type = self.find_by_id(id)

        if relation_type.code != request.code:
            existing = self.db.query(RelationType).filter(RelationType.code == request.code).first()
            if existing:
                raise ApiException(
                    status_code=400,
                    code=40001,
                    message=f"RelationType code already exists: {request.code}"
                )

        relation_type.code = request.code
        relation_type.name = request.name
        relation_type.inverse_name = request.inverse_name
        relation_type.description = request.description
        relation_type.color = request.color
        relation_type.directed = request.directed
        relation_type.enabled = request.enabled

        self.db.commit()
        self.db.refresh(relation_type)
        return relation_type
