from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy import and_, delete, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.attachment.models import Attachment
from app.common.exceptions import ApiException
from app.common.storage import get_minio_client, remove_object_safe, StorageError
from app.entry.models import Entry, TimeMode, entry_tag
from app.entry.schemas import EntryRequest, EntrySearchRequest
from app.entry_type.service import EntryTypeService
from app.relation.models import Relation
from app.tag.models import Tag
from app.tag.service import TagService


class EntryService:
    def __init__(self, db: Session):
        self.db = db
        self.type_service = EntryTypeService(db)
        self.tag_service = TagService(db)

    def find_all(self) -> List[Entry]:
        return self.db.query(Entry).all()

    def find_by_id(self, id: UUID) -> Entry:
        entry = self.db.query(Entry).filter(Entry.id == id).first()
        if not entry:
            raise ApiException(status_code=404, code=40400, message=f"Entry not found: {id}")
        return entry

    def search(self, request: EntrySearchRequest) -> dict:
        query = self.db.query(Entry)

        # Apply filters
        if request.keyword:
            keyword_filter = f"%{request.keyword}%"
            query = query.filter(
                or_(
                    Entry.title.ilike(keyword_filter),
                    Entry.content.ilike(keyword_filter),
                )
            )

        if request.type_id:
            query = query.filter(Entry.type_id == request.type_id)

        if request.tag_ids:
            query = query.filter(Entry.tags.any(Tag.id.in_(request.tag_ids)))

        # Time intersection query: match POINT entries within range, or RANGE entries overlapping
        if request.time_from or request.time_to:
            point_filters = [Entry.time_mode == TimeMode.POINT, Entry.time_at.isnot(None)]
            if request.time_from:
                point_filters.append(Entry.time_at >= request.time_from)
            if request.time_to:
                point_filters.append(Entry.time_at <= request.time_to)
            point_clause = and_(*point_filters)

            range_filters = [
                Entry.time_mode == TimeMode.RANGE,
                Entry.time_from.isnot(None),
                Entry.time_to.isnot(None),
            ]
            if request.time_to:
                range_filters.append(Entry.time_from <= request.time_to)
            if request.time_from:
                range_filters.append(Entry.time_to >= request.time_from)
            range_clause = and_(*range_filters)

            query = query.filter(or_(point_clause, range_clause))

        # Count total
        total = query.count()

        # Apply pagination
        entries = query.order_by(Entry.created_at.desc()).offset(request.page * request.size).limit(request.size).all()

        return {
            "content": entries,
            "total": total,
            "page": request.page,
            "size": request.size,
            "total_pages": (total + request.size - 1) // request.size,
        }

    def create(self, request: EntryRequest) -> Entry:
        # Validate type exists
        self.type_service.find_by_id(request.type_id)

        # Validate tags exist
        tags = []
        if request.tag_ids:
            tags = self.tag_service.find_by_ids(request.tag_ids)
            if len(tags) != len(request.tag_ids):
                raise ApiException(status_code=400, code=40001, message="Some tag IDs are invalid")

        entry = Entry(
            title=request.title,
            summary=request.summary,
            content=request.content,
            type_id=request.type_id,
            time_mode=request.time_mode,
            time_at=request.time_at,
            time_from=request.time_from,
            time_to=request.time_to,
        )
        entry.tags = tags

        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def update(self, id: UUID, request: EntryRequest) -> Entry:
        entry = self.find_by_id(id)

        # Validate type exists
        self.type_service.find_by_id(request.type_id)

        # Validate tags exist
        tags = []
        if request.tag_ids:
            tags = self.tag_service.find_by_ids(request.tag_ids)
            if len(tags) != len(request.tag_ids):
                raise ApiException(status_code=400, code=40001, message="Some tag IDs are invalid")

        entry.title = request.title
        entry.summary = request.summary
        entry.content = request.content
        entry.type_id = request.type_id
        entry.time_mode = request.time_mode
        entry.time_at = request.time_at
        entry.time_from = request.time_from
        entry.time_to = request.time_to
        entry.tags = tags

        self.db.commit()
        self.db.refresh(entry)
        return entry

    def delete(self, id: UUID) -> None:
        entry = self.find_by_id(id)

        attachments = self.db.query(Attachment).filter(Attachment.entry_id == id).all()
        if attachments:
            try:
                client, bucket = get_minio_client()
                for attachment in attachments:
                    remove_object_safe(client, bucket, attachment.file_path)
                    self.db.delete(attachment)
            except StorageError:
                # Storage unavailable - still allow entry deletion, attachments will be orphaned
                for attachment in attachments:
                    self.db.delete(attachment)

        self.db.execute(
            delete(Relation).where(or_(Relation.source_entry_id == id, Relation.target_entry_id == id))
        )
        self.db.execute(delete(entry_tag).where(entry_tag.c.entry_id == id))

        self.db.delete(entry)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40900,
                message="Entry is referenced by other resources; delete them first",
            ) from exc
