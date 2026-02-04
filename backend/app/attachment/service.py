from __future__ import annotations

import uuid
from pathlib import Path
from typing import List
from uuid import UUID

from fastapi import UploadFile
from minio.error import S3Error
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.common.storage import get_minio_client, remove_object_safe, StorageError
from app.attachment.models import Attachment, AttachmentParseOutbox
from app.attachment.parser import SUPPORTED_EXTENSIONS as SUPPORTED_PARSE_EXTENSIONS
from app.config import get_settings


class AttachmentService:
    def __init__(self, db: Session):
        self.db = db

    def get_latest_kg_index_map(self, attachment_ids: list[UUID]) -> dict[UUID, object]:
        """Return latest AttachmentIndexOutbox row per attachment_id (best-effort).

        Uses PostgreSQL DISTINCT ON for efficient single-row-per-group retrieval.
        Note: returns an empty map if the outbox table is not available (e.g. DB not migrated yet).
        """
        if not attachment_ids:
            return {}

        try:
            from app.lightrag.models import AttachmentIndexOutbox
        except Exception:
            return {}

        try:
            from sqlalchemy import func

            # Use DISTINCT ON to get latest row per attachment_id efficiently
            rows = (
                self.db.query(AttachmentIndexOutbox)
                .filter(AttachmentIndexOutbox.attachment_id.in_(attachment_ids))
                .distinct(AttachmentIndexOutbox.attachment_id)
                .order_by(
                    AttachmentIndexOutbox.attachment_id.asc(),
                    AttachmentIndexOutbox.updated_at.desc(),
                    AttachmentIndexOutbox.created_at.desc(),
                )
                .all()
            )
        except Exception:
            # Most commonly: relation/table doesn't exist yet. Do not break attachment UI.
            return {}

        return {row.attachment_id: row for row in rows if row.attachment_id}

    def find_all(self) -> List[Attachment]:
        return self.db.query(Attachment).all()

    def find_by_id(self, id: UUID) -> Attachment:
        attachment = self.db.query(Attachment).filter(Attachment.id == id).first()
        if not attachment:
            raise ApiException(status_code=404, code=40400, message=f"Attachment not found: {id}")
        return attachment

    def find_by_entry(self, entry_id: UUID) -> List[Attachment]:
        return self.db.query(Attachment).filter(Attachment.entry_id == entry_id).all()

    async def upload(
        self,
        entry_id: UUID,
        file: UploadFile,
        *,
        index_to_knowledge_graph: bool = False,
    ) -> Attachment:
        settings = get_settings()
        original_filename = file.filename or "file"
        file_ext = Path(original_filename).suffix
        ext_lower = file_ext.lower()
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        object_key = f"attachments/{entry_id}/{unique_filename}"
        content_type = file.content_type or "application/octet-stream"

        # Validate file type BEFORE upload if indexing requested
        should_index = False
        if index_to_knowledge_graph:
            if ext_lower not in SUPPORTED_PARSE_EXTENSIONS:
                raise ApiException(
                    status_code=400,
                    code=40001,
                    message=f"Unsupported file type for indexing: {ext_lower}",
                )
            should_index = True

        # Validate file size BEFORE upload
        max_size_bytes = settings.docling_max_file_size_mb * 1024 * 1024
        try:
            file.file.seek(0, 2)  # Seek to end
            file_size = file.file.tell()
            file.file.seek(0)  # Reset to beginning
        except Exception:
            file_size = 0  # Cannot determine size, will check after upload

        if file_size > max_size_bytes:
            raise ApiException(
                status_code=413,
                code=41300,
                message=f"File too large. Maximum size is {settings.docling_max_file_size_mb}MB",
            )

        try:
            client, bucket = get_minio_client()
        except StorageError as exc:
            raise ApiException(status_code=500, code=50002, message="Storage service unavailable") from exc

        # Upload to MinIO
        try:
            client.put_object(
                bucket_name=bucket,
                object_name=object_key,
                data=file.file,
                length=file_size if file_size > 0 else -1,
                part_size=10 * 1024 * 1024,
                content_type=content_type,
            )
        except S3Error as exc:
            raise ApiException(
                status_code=500,
                code=50001,
                message="Failed to upload attachment",
            ) from exc

        # Get actual file size if we couldn't determine it before
        if file_size == 0:
            try:
                stat = client.stat_object(bucket, object_key)
                file_size = int(getattr(stat, "size", 0) or 0)
                # Check size limit after upload if we couldn't check before
                if file_size > max_size_bytes:
                    remove_object_safe(client, bucket, object_key)
                    raise ApiException(
                        status_code=413,
                        code=41300,
                        message=f"File too large. Maximum size is {settings.docling_max_file_size_mb}MB",
                    )
            except S3Error:
                pass

        # Save to database (single transaction for atomicity)
        attachment = Attachment(
            entry_id=entry_id,
            filename=unique_filename,
            original_filename=original_filename,
            file_path=object_key,
            size=file_size,
            content_type=content_type,
            index_to_knowledge_graph=should_index,
            parse_status="pending" if should_index else None,
        )
        try:
            self.db.add(attachment)
            self.db.flush()  # Get attachment.id without committing

            # Create parse outbox if indexing requested (same transaction)
            if should_index:
                outbox = AttachmentParseOutbox(
                    attachment_id=attachment.id,
                    entry_id=entry_id,
                    status="pending",
                )
                self.db.add(outbox)

            self.db.commit()
            self.db.refresh(attachment)
        except Exception as exc:
            self.db.rollback()
            # Cleanup uploaded object on DB failure
            remove_object_safe(client, bucket, object_key)
            raise ApiException(
                status_code=500,
                code=50002,
                message="Failed to save attachment metadata",
            ) from exc
        return attachment

    def delete(self, id: UUID) -> None:
        attachment = self.find_by_id(id)
        entry_id = attachment.entry_id
        should_cleanup_index = bool(attachment.index_to_knowledge_graph)

        try:
            client, bucket = get_minio_client()
        except StorageError as exc:
            raise ApiException(status_code=500, code=50002, message="Storage service unavailable") from exc

        if not remove_object_safe(client, bucket, attachment.file_path):
            raise ApiException(
                status_code=500,
                code=50001,
                message="Failed to delete attachment from storage",
            )

        self.db.delete(attachment)
        if should_cleanup_index:
            from app.lightrag.models import AttachmentIndexOutbox

            self.db.add(
                AttachmentIndexOutbox(
                    attachment_id=attachment.id,
                    entry_id=entry_id,
                    op="delete",
                    status="pending",
                )
            )
        self.db.commit()

    def retry_parse(self, id: UUID) -> Attachment:
        attachment = self.find_by_id(id)

        if attachment.parse_status != "failed":
            raise ApiException(
                status_code=400,
                code=40002,
                message="Only failed attachments can be retried",
            )

        attachment.parse_status = "pending"
        attachment.parse_last_error = None

        outbox = AttachmentParseOutbox(
            attachment_id=attachment.id,
            entry_id=attachment.entry_id,
            status="pending",
        )
        self.db.add(outbox)
        self.db.commit()
        self.db.refresh(attachment)

        return attachment

    def retry_index(self, id: UUID) -> Attachment:
        """Re-enqueue LightRAG indexing for an already-parsed attachment."""
        attachment = self.find_by_id(id)

        if not bool(attachment.index_to_knowledge_graph):
            raise ApiException(
                status_code=400,
                code=40003,
                message="Attachment is not configured to index to knowledge graph",
            )

        if attachment.parse_status != "completed":
            raise ApiException(
                status_code=400,
                code=40004,
                message="Only parsed attachments can be re-indexed",
            )

        from app.lightrag.models import AttachmentIndexOutbox

        self.db.add(
            AttachmentIndexOutbox(
                attachment_id=attachment.id,
                entry_id=attachment.entry_id,
                op="upsert",
                status="pending",
            )
        )
        self.db.commit()
        self.db.refresh(attachment)
        return attachment

    def get_object_stream(self, object_key: str):
        try:
            client, bucket = get_minio_client()
        except StorageError as exc:
            raise ApiException(status_code=500, code=50002, message="Storage service unavailable") from exc

        try:
            stat = client.stat_object(bucket, object_key)
            stream = client.get_object(bucket, object_key)
            return stream, stat
        except S3Error as exc:
            if getattr(exc, "code", "") in ("NoSuchKey", "NoSuchObject"):
                raise ApiException(
                    status_code=404,
                    code=40400,
                    message="Attachment file not found"
                ) from exc
            raise ApiException(
                status_code=500,
                code=50001,
                message="Failed to download attachment",
            ) from exc

    @staticmethod
    def iter_stream(stream, chunk_size: int = 32 * 1024):
        """Iterate over stream in chunks, ensuring proper cleanup."""
        try:
            for chunk in stream.stream(chunk_size):
                yield chunk
        finally:
            try:
                stream.close()
            finally:
                try:
                    stream.release_conn()
                except Exception:
                    pass

    def read_text_content(self, object_key: str, max_size: int) -> str:
        """Read text content from storage with size limit."""
        try:
            client, bucket = get_minio_client()
        except StorageError as exc:
            raise ApiException(status_code=500, code=50002, message="Storage service unavailable") from exc

        try:
            stat = client.stat_object(bucket, object_key)
            file_size = getattr(stat, "size", 0) or 0
            if file_size > max_size:
                raise ApiException(
                    status_code=413,
                    code=41310,
                    message="Attachment text too large to preview",
                )

            stream = client.get_object(bucket, object_key)
            try:
                content = stream.read().decode("utf-8", errors="replace")
            finally:
                stream.close()
                stream.release_conn()
            return content
        except S3Error as exc:
            if getattr(exc, "code", "") in ("NoSuchKey", "NoSuchObject"):
                raise ApiException(
                    status_code=404,
                    code=40400,
                    message="Attachment file not found"
                ) from exc
            raise ApiException(
                status_code=500,
                code=50001,
                message="Failed to read attachment content",
            ) from exc
        except ApiException:
            raise
        except Exception as exc:
            raise ApiException(
                status_code=500,
                code=50001,
                message="Failed to read attachment content",
            ) from exc
