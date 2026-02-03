from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy import and_, delete, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.attachment.models import Attachment
from app.common.exceptions import ApiException
from app.common.time import utcnow
from app.common.storage import get_minio_client, remove_object_safe, StorageError
from app.entry.models import Entry, TimeMode
from app.entry.schemas import EntryRequest, EntrySearchRequest, EntryTimePatch
from app.entry_type.service import EntryTypeService
from app.relation.models import Relation
from app.tag.models import Tag
from app.tag.service import TagService
from app.lightrag.models import EntryIndexOutbox


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
        self.db.flush()  # Ensure entry.id / timestamps are available in the same transaction.

        self.db.add(
            EntryIndexOutbox(
                entry_id=entry.id,
                op="upsert",
                entry_updated_at=entry.updated_at,
                status="pending",
            )
        )
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def _should_enqueue_index_for_update(self, *, entry: Entry, request: EntryRequest) -> bool:
        """Whether an update should enqueue LightRAG re-indexing.

        Policy:
        - Only re-index when textual content changes (title/summary/content).
        - Changes to type/tags/time alone do not trigger indexing (per product requirement).
        """
        return (
            entry.title != request.title
            or entry.summary != request.summary
            or entry.content != request.content
        )

    def _coalesce_upsert_outbox(self, *, entry_id: UUID, entry_updated_at) -> None:  # noqa: ANN001
        """Coalesce an upsert outbox event for an entry.

        If there is already an active (pending/processing) upsert event, do not enqueue a new row.
        Instead, best-effort update the existing row's entry_updated_at and clear any backoff.
        """
        now = utcnow()
        existing = (
            self.db.query(EntryIndexOutbox)
            .filter(
                EntryIndexOutbox.entry_id == entry_id,
                EntryIndexOutbox.op == "upsert",
                EntryIndexOutbox.status.in_(["pending", "processing"]),
            )
            .order_by(EntryIndexOutbox.created_at.desc())
            .first()
        )

        if existing:
            existing.entry_updated_at = entry_updated_at
            existing.last_error = None
            if existing.status == "pending" and existing.available_at and existing.available_at > now:
                existing.available_at = now
            return

        self.db.add(
            EntryIndexOutbox(
                entry_id=entry_id,
                op="upsert",
                entry_updated_at=entry_updated_at,
                status="pending",
            )
        )

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

        should_enqueue = self._should_enqueue_index_for_update(entry=entry, request=request)

        entry.title = request.title
        entry.summary = request.summary
        entry.content = request.content
        entry.type_id = request.type_id
        entry.time_mode = request.time_mode
        entry.time_at = request.time_at
        entry.time_from = request.time_from
        entry.time_to = request.time_to
        entry.tags = tags

        self.db.flush()  # Ensure updated_at is bumped before enqueuing outbox event.
        if should_enqueue:
            self._coalesce_upsert_outbox(entry_id=entry.id, entry_updated_at=entry.updated_at)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_index_status(self, entry_id: UUID) -> dict:
        """Get the latest LightRAG index status for an entry."""
        processing = (
            self.db.query(EntryIndexOutbox)
            .filter(EntryIndexOutbox.entry_id == entry_id, EntryIndexOutbox.status == "processing")
            .order_by(EntryIndexOutbox.created_at.desc())
            .first()
        )
        outbox = processing
        if not outbox:
            pending = (
                self.db.query(EntryIndexOutbox)
                .filter(EntryIndexOutbox.entry_id == entry_id, EntryIndexOutbox.status == "pending")
                .order_by(EntryIndexOutbox.created_at.desc())
                .first()
            )
            outbox = pending
        if not outbox:
            outbox = (
                self.db.query(EntryIndexOutbox)
                .filter(EntryIndexOutbox.entry_id == entry_id)
                .order_by(EntryIndexOutbox.created_at.desc())
                .first()
            )
        if not outbox:
            return {"status": "unknown", "attempts": 0, "lastError": None, "updatedAt": None}
        return {
            "status": outbox.status,
            "attempts": outbox.attempts,
            "lastError": outbox.last_error,
            "updatedAt": outbox.updated_at.isoformat() if outbox.updated_at else None,
        }

    def delete(self, id: UUID) -> None:
        entry = self.find_by_id(id)
        self.db.add(
            EntryIndexOutbox(
                entry_id=entry.id,
                op="delete",
                entry_updated_at=None,
                status="pending",
            )
        )

        attachments = self.db.query(Attachment).filter(Attachment.entry_id == id).all()
        if attachments:
            from app.lightrag.models import AttachmentIndexOutbox

            try:
                client, bucket = get_minio_client()
                for attachment in attachments:
                    if attachment.parse_status == "completed" and bool(attachment.index_to_knowledge_graph):
                        self.db.add(
                            AttachmentIndexOutbox(
                                attachment_id=attachment.id,
                                entry_id=entry.id,
                                op="delete",
                                status="pending",
                            )
                        )
                    remove_object_safe(client, bucket, attachment.file_path)
                    self.db.delete(attachment)
            except StorageError:
                # Storage unavailable - still allow entry deletion, attachments will be orphaned
                for attachment in attachments:
                    if attachment.parse_status == "completed" and bool(attachment.index_to_knowledge_graph):
                        self.db.add(
                            AttachmentIndexOutbox(
                                attachment_id=attachment.id,
                                entry_id=entry.id,
                                op="delete",
                                status="pending",
                            )
                        )
                    self.db.delete(attachment)

        self.db.execute(
            delete(Relation).where(or_(Relation.source_entry_id == id, Relation.target_entry_id == id))
        )

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

    def patch_time(self, id: UUID, request: EntryTimePatch) -> Entry:
        entry = self.find_by_id(id)

        if request.time_mode is not None:
            entry.time_mode = request.time_mode
        if request.time_at is not None:
            entry.time_at = request.time_at
        if request.time_from is not None:
            entry.time_from = request.time_from
        if request.time_to is not None:
            entry.time_to = request.time_to

        # Validate consistency
        if entry.time_mode == TimeMode.POINT and entry.time_at is None:
            raise ApiException(status_code=400, code=40002, message="time_at required for POINT mode")
        if entry.time_mode == TimeMode.RANGE:
            if entry.time_from is None or entry.time_to is None:
                raise ApiException(status_code=400, code=40002, message="time_from and time_to required for RANGE mode")
            if entry.time_from > entry.time_to:
                raise ApiException(status_code=400, code=40002, message="time_from must be <= time_to")

        # Time-only updates do not affect LightRAG ingestion text; do not enqueue indexing.
        self.db.flush()
        self.db.commit()
        self.db.refresh(entry)
        return entry
