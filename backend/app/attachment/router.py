from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.attachment.schemas import AttachmentResponse
from app.attachment.service import AttachmentService

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

def _attachment_to_response(*, attachment, kg_outbox=None) -> dict:
    resp = AttachmentResponse.model_validate(attachment)
    if kg_outbox is not None:
        resp.kg_index_status = getattr(kg_outbox, "status", None)
        resp.kg_index_attempts = getattr(kg_outbox, "attempts", None)
        resp.kg_index_last_error = getattr(kg_outbox, "last_error", None)
        resp.kg_index_updated_at = getattr(kg_outbox, "updated_at", None)
    return resp.model_dump(by_alias=True)


@router.get("", response_model=ApiResponse)
def list_attachments(db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachments = service.find_all()
    kg_map = service.get_latest_kg_index_map([a.id for a in attachments])
    return ApiResponse.ok([_attachment_to_response(attachment=a, kg_outbox=kg_map.get(a.id)) for a in attachments])


@router.get("/{id}", response_model=ApiResponse)
def get_attachment(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachment = service.find_by_id(id)
    kg_map = service.get_latest_kg_index_map([attachment.id])
    return ApiResponse.ok(_attachment_to_response(attachment=attachment, kg_outbox=kg_map.get(attachment.id)))


@router.get("/entry/{entry_id}", response_model=ApiResponse)
def get_attachments_by_entry(entry_id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachments = service.find_by_entry(entry_id)
    kg_map = service.get_latest_kg_index_map([a.id for a in attachments])
    return ApiResponse.ok([_attachment_to_response(attachment=a, kg_outbox=kg_map.get(a.id)) for a in attachments])


@router.post("/entry/{entry_id}", response_model=ApiResponse)
async def upload_attachment(
    entry_id: UUID,
    file: UploadFile = File(...),
    index_to_knowledge_graph: bool = Form(default=False),
    db: Session = Depends(get_db)
) -> ApiResponse:
    service = AttachmentService(db)
    attachment = await service.upload(
        entry_id,
        file,
        index_to_knowledge_graph=index_to_knowledge_graph,
    )
    kg_map = service.get_latest_kg_index_map([attachment.id])
    return ApiResponse.ok(_attachment_to_response(attachment=attachment, kg_outbox=kg_map.get(attachment.id)))


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


@router.post("/{id}/retry", response_model=ApiResponse)
def retry_attachment_parse(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachment = service.retry_parse(id)
    kg_map = service.get_latest_kg_index_map([attachment.id])
    return ApiResponse.ok(_attachment_to_response(attachment=attachment, kg_outbox=kg_map.get(attachment.id)))
