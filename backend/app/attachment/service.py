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
from app.attachment.models import Attachment


class AttachmentService:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> List[Attachment]:
        return self.db.query(Attachment).all()

    def find_by_id(self, id: UUID) -> Attachment:
        attachment = self.db.query(Attachment).filter(Attachment.id == id).first()
        if not attachment:
            raise ApiException(status_code=404, code=40400, message=f"Attachment not found: {id}")
        return attachment

    def find_by_entry(self, entry_id: UUID) -> List[Attachment]:
        return self.db.query(Attachment).filter(Attachment.entry_id == entry_id).all()

    async def upload(self, entry_id: UUID, file: UploadFile) -> Attachment:
        original_filename = file.filename or "file"
        file_ext = Path(original_filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        object_key = f"attachments/{entry_id}/{unique_filename}"
        content_type = file.content_type or "application/octet-stream"

        try:
            client, bucket = get_minio_client()
        except StorageError as exc:
            raise ApiException(status_code=500, code=50002, message="Storage service unavailable") from exc

        # Upload to MinIO
        try:
            try:
                file.file.seek(0)
            except Exception:
                pass
            client.put_object(
                bucket_name=bucket,
                object_name=object_key,
                data=file.file,
                length=-1,
                part_size=10 * 1024 * 1024,
                content_type=content_type,
            )
        except S3Error as exc:
            raise ApiException(
                status_code=500,
                code=50001,
                message="Failed to upload attachment",
            ) from exc

        # Get file size (separate try block to handle stat failure)
        try:
            stat = client.stat_object(bucket, object_key)
            size = int(getattr(stat, "size", 0) or 0)
        except S3Error:
            # stat failed but upload succeeded - use 0 as fallback
            size = 0

        # Save to database
        attachment = Attachment(
            entry_id=entry_id,
            filename=unique_filename,
            original_filename=original_filename,
            file_path=object_key,
            size=size,
            content_type=content_type,
        )
        try:
            self.db.add(attachment)
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
        self.db.commit()

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
