from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.color_utils import is_valid_hex_color, pick_material_600_color
from app.common.exceptions import ApiException
from app.entry.models import entry_tag
from app.tag.models import Tag
from app.tag.schemas import TagRequest


class TagService:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> List[Tag]:
        return self.db.query(Tag).all()

    def find_by_id(self, id: UUID) -> Tag:
        tag = self.db.query(Tag).filter(Tag.id == id).first()
        if not tag:
            raise ApiException(status_code=404, code=40400, message=f"Tag not found: {id}")
        return tag

    def find_by_ids(self, ids: List[UUID]) -> List[Tag]:
        return self.db.query(Tag).filter(Tag.id.in_(ids)).all()

    def create(self, request: TagRequest) -> Tag:
        # Check if name already exists (case-insensitive)
        existing = self.db.query(Tag).filter(Tag.name.ilike(request.name)).first()
        if existing:
            raise ApiException(
                status_code=400,
                code=40001,
                message=f"Tag name already exists: {request.name}"
            )

        # Fallback color if not provided or invalid
        color = request.color
        if not is_valid_hex_color(color):
            color = pick_material_600_color(request.name)

        tag = Tag(
            name=request.name,
            color=color,
            description=request.description,
        )
        self.db.add(tag)
        self.db.commit()
        self.db.refresh(tag)
        return tag

    def update(self, id: UUID, request: TagRequest) -> Tag:
        tag = self.find_by_id(id)

        # Check if name is being changed and if new name already exists
        if tag.name.lower() != request.name.lower():
            existing = self.db.query(Tag).filter(Tag.name.ilike(request.name)).first()
            if existing:
                raise ApiException(
                    status_code=400,
                    code=40001,
                    message=f"Tag name already exists: {request.name}"
                )

        tag.name = request.name
        tag.color = request.color
        tag.description = request.description

        self.db.commit()
        self.db.refresh(tag)
        return tag

    def delete(self, id: UUID) -> None:
        tag = self.find_by_id(id)
        self.db.execute(delete(entry_tag).where(entry_tag.c.tag_id == id))
        self.db.delete(tag)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40900,
                message="Tag is referenced by other resources; delete them first",
            ) from exc
