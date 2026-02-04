from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.common.exceptions import ApiException
from app.database import get_db
from app.attachment.schemas import AttachmentResponse, AttachmentMarkdownResponse
from app.attachment.service import AttachmentService
from app.attachment.preview import (
    is_inline_previewable,
    is_text_file,
    get_canonical_mime_type,
    MAX_TEXT_PREVIEW_SIZE,
)

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

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.original_filename, safe='')}",
    }
    size = getattr(stat, "size", None)
    if size is not None:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        content=service.iter_stream(stream),
        media_type=attachment.content_type,
        headers=headers,
    )


@router.get("/{id}/view")
async def view_attachment(id: UUID, db: Session = Depends(get_db)) -> StreamingResponse:
    """Inline preview for images and PDF files."""
    service = AttachmentService(db)
    attachment = service.find_by_id(id)

    if not is_inline_previewable(attachment.original_filename):
        raise ApiException(
            status_code=415,
            code=41510,
            message="Unsupported attachment type for inline preview",
        )

    stream, stat = service.get_object_stream(attachment.file_path)

    mime_type = get_canonical_mime_type(attachment.original_filename, attachment.content_type)
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(attachment.original_filename, safe='')}",
        "X-Content-Type-Options": "nosniff",
    }
    size = getattr(stat, "size", None)
    if size is not None:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        content=service.iter_stream(stream),
        media_type=mime_type,
        headers=headers,
    )


@router.get("/{id}/markdown", response_model=ApiResponse)
def get_attachment_markdown(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    """Get markdown content for text/document preview."""
    service = AttachmentService(db)
    attachment = service.find_by_id(id)

    response_data = AttachmentMarkdownResponse(
        attachment_id=attachment.id,
        state="unsupported",
        content_type=attachment.content_type,
        original_filename=attachment.original_filename,
        parse_status=attachment.parse_status,
        parse_last_error=attachment.parse_last_error,
    )

    # Text files: read directly from storage
    if is_text_file(attachment.original_filename):
        content = service.read_text_content(attachment.file_path, MAX_TEXT_PREVIEW_SIZE)
        response_data.state = "ready"
        response_data.source = "file"
        response_data.markdown = content
        return ApiResponse.ok(response_data.model_dump(by_alias=True))

    # Other files: use parsed_text if available
    if attachment.parsed_text:
        response_data.state = "ready"
        response_data.source = "parsed_text"
        response_data.markdown = attachment.parsed_text
    elif attachment.parse_status in ("pending", "processing"):
        response_data.state = "processing"
    elif attachment.parse_status == "failed":
        response_data.state = "failed"

    return ApiResponse.ok(response_data.model_dump(by_alias=True))


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


@router.post("/{id}/retry-index", response_model=ApiResponse)
def retry_attachment_index(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AttachmentService(db)
    attachment = service.retry_index(id)
    kg_map = service.get_latest_kg_index_map([attachment.id])
    return ApiResponse.ok(_attachment_to_response(attachment=attachment, kg_outbox=kg_map.get(attachment.id)))
