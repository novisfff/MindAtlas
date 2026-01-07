from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.attachment.schemas import AttachmentResponse
from app.attachment.service import AttachmentService

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("", response_model=ApiResponse)
def list_attachments(db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachments = service.find_all()
    return ApiResponse.ok([AttachmentResponse.model_validate(a).model_dump(by_alias=True) for a in attachments])


@router.get("/{id}", response_model=ApiResponse)
def get_attachment(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachment = service.find_by_id(id)
    return ApiResponse.ok(AttachmentResponse.model_validate(attachment).model_dump(by_alias=True))


@router.get("/entry/{entry_id}", response_model=ApiResponse)
def get_attachments_by_entry(entry_id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachments = service.find_by_entry(entry_id)
    return ApiResponse.ok([AttachmentResponse.model_validate(a).model_dump(by_alias=True) for a in attachments])


@router.post("/entry/{entry_id}", response_model=ApiResponse)
async def upload_attachment(
    entry_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
) -> ApiResponse:
    service = AttachmentService(db)
    attachment = await service.upload(entry_id, file)
    return ApiResponse.ok(AttachmentResponse.model_validate(attachment).model_dump(by_alias=True))


@router.post("/upload/{entry_id}", response_model=ApiResponse, include_in_schema=False)
async def upload_attachment_legacy(
    entry_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ApiResponse:
    return await upload_attachment(entry_id=entry_id, file=file, db=db)


@router.get("/{id}/download")
async def download_attachment(id: UUID, db: Session = Depends(get_db)) -> StreamingResponse:
    service = AttachmentService(db)
    attachment = service.find_by_id(id)
    stream, stat = service.get_object_stream(attachment.file_path)

    def iter_stream():
        try:
            for chunk in stream.stream(32 * 1024):
                yield chunk
        finally:
            try:
                stream.close()
            finally:
                try:
                    stream.release_conn()
                except Exception:
                    pass

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.original_filename, safe='')}",
    }
    size = getattr(stat, "size", None)
    if size is not None:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        content=iter_stream(),
        media_type=attachment.content_type,
        headers=headers,
    )


@router.get("/download/{id}", include_in_schema=False)
async def download_attachment_legacy(id: UUID, db: Session = Depends(get_db)) -> StreamingResponse:
    return await download_attachment(id=id, db=db)


@router.delete("/{id}", response_model=ApiResponse)
def delete_attachment(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    service.delete(id)
    return ApiResponse.ok(None, "Attachment deleted successfully")
