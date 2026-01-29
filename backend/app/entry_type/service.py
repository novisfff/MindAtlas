from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.entry_type.models import EntryType
from app.entry_type.schemas import EntryTypeRequest, EntryTypeUpdateRequest


class EntryTypeService:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> List[EntryType]:
        return self.db.query(EntryType).all()

    def find_by_id(self, id: UUID) -> EntryType:
        entry_type = self.db.query(EntryType).filter(EntryType.id == id).first()
        if not entry_type:
            raise ApiException(status_code=404, code=40400, message=f"EntryType not found: {id}")
        return entry_type

    def find_by_code(self, code: str) -> EntryType:
        entry_type = self.db.query(EntryType).filter(EntryType.code == code).first()
        if not entry_type:
            raise ApiException(status_code=404, code=40400, message=f"EntryType not found: {code}")
        return entry_type

    def create(self, request: EntryTypeRequest) -> EntryType:
        # Check if code already exists
        existing = self.db.query(EntryType).filter(EntryType.code == request.code).first()
        if existing:
            raise ApiException(
                status_code=400,
                code=40001,
                message=f"EntryType code already exists: {request.code}"
            )

        entry_type = EntryType(
            code=request.code,
            name=request.name,
            description=request.description,
            color=request.color,
            icon=request.icon,
            graph_enabled=request.graph_enabled,
            ai_enabled=request.ai_enabled,
            enabled=request.enabled,
        )
        self.db.add(entry_type)
        self.db.commit()
        self.db.refresh(entry_type)
        return entry_type

    def update(self, id: UUID, request: EntryTypeUpdateRequest) -> EntryType:
        entry_type = self.find_by_id(id)

        if "code" in request.model_fields_set and request.code is not None and entry_type.code != request.code:
            existing = self.db.query(EntryType).filter(EntryType.code == request.code).first()
            if existing:
                raise ApiException(
                    status_code=400,
                    code=40001,
                    message=f"EntryType code already exists: {request.code}",
                )
            entry_type.code = request.code

        if "name" in request.model_fields_set and request.name is not None:
            entry_type.name = request.name
        if "description" in request.model_fields_set:
            entry_type.description = request.description
        if "color" in request.model_fields_set:
            entry_type.color = request.color
        if "icon" in request.model_fields_set:
            entry_type.icon = request.icon
        if "graph_enabled" in request.model_fields_set and request.graph_enabled is not None:
            entry_type.graph_enabled = request.graph_enabled
        if "ai_enabled" in request.model_fields_set and request.ai_enabled is not None:
            entry_type.ai_enabled = request.ai_enabled
        if "enabled" in request.model_fields_set and request.enabled is not None:
            entry_type.enabled = request.enabled

        self.db.commit()
        self.db.refresh(entry_type)
        return entry_type

    def delete(self, id: UUID) -> None:
        entry_type = self.find_by_id(id)
        self.db.delete(entry_type)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40900,
                message="EntryType is referenced by entries; delete/move entries first",
            ) from exc
