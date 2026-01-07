from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.entry_type.schemas import EntryTypeRequest, EntryTypeResponse
from app.entry_type.service import EntryTypeService

router = APIRouter(prefix="/api/entry-types", tags=["entry-types"])


@router.get("", response_model=ApiResponse)
def list_entry_types(db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryTypeService(db)
    entry_types = service.find_all()
    return ApiResponse.ok([EntryTypeResponse.model_validate(et).model_dump(by_alias=True) for et in entry_types])


@router.get("/{id}", response_model=ApiResponse)
def get_entry_type(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryTypeService(db)
    entry_type = service.find_by_id(id)
    return ApiResponse.ok(EntryTypeResponse.model_validate(entry_type).model_dump(by_alias=True))


@router.post("", response_model=ApiResponse)
def create_entry_type(request: EntryTypeRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryTypeService(db)
    entry_type = service.create(request)
    return ApiResponse.ok(EntryTypeResponse.model_validate(entry_type).model_dump(by_alias=True))


@router.put("/{id}", response_model=ApiResponse)
def update_entry_type(id: UUID, request: EntryTypeRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryTypeService(db)
    entry_type = service.update(id, request)
    return ApiResponse.ok(EntryTypeResponse.model_validate(entry_type).model_dump(by_alias=True))


@router.delete("/{id}", response_model=ApiResponse)
def delete_entry_type(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryTypeService(db)
    service.delete(id)
    return ApiResponse.ok(None)
