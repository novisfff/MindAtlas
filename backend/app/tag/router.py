from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.tag.schemas import TagRequest, TagResponse
from app.tag.service import TagService

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", response_model=ApiResponse)
def list_tags(db: Session = Depends(get_db)) -> ApiResponse:
    service = TagService(db)
    tags = service.find_all()
    return ApiResponse.ok([TagResponse.model_validate(tag).model_dump(by_alias=True) for tag in tags])


@router.get("/{id}", response_model=ApiResponse)
def get_tag(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = TagService(db)
    tag = service.find_by_id(id)
    return ApiResponse.ok(TagResponse.model_validate(tag).model_dump(by_alias=True))


@router.post("", response_model=ApiResponse)
def create_tag(request: TagRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = TagService(db)
    tag = service.create(request)
    return ApiResponse.ok(TagResponse.model_validate(tag).model_dump(by_alias=True))


@router.put("/{id}", response_model=ApiResponse)
def update_tag(id: UUID, request: TagRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = TagService(db)
    tag = service.update(id, request)
    return ApiResponse.ok(TagResponse.model_validate(tag).model_dump(by_alias=True))


@router.delete("/{id}", response_model=ApiResponse)
def delete_tag(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = TagService(db)
    service.delete(id)
    return ApiResponse.ok(None, "Tag deleted successfully")
