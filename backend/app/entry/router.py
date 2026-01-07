from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.params import parse_uuid_csv
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db
from app.entry.schemas import EntryRequest, EntryResponse, EntrySearchRequest
from app.entry.service import EntryService

router = APIRouter(prefix="/api/entries", tags=["entries"])


@router.get("", response_model=ApiResponse)
def search_entries(
    q: str | None = Query(default=None, alias="q"),
    type_id: UUID | None = Query(default=None, alias="typeId"),
    tag_ids: str | None = Query(default=None, alias="tagIds"),
    time_from: datetime | None = Query(default=None, alias="timeFrom"),
    time_to: datetime | None = Query(default=None, alias="timeTo"),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        parsed_tag_ids = parse_uuid_csv(tag_ids)
    except ValueError:
        raise ApiException(status_code=422, code=42200, message="Validation Error", details={"tagIds": tag_ids})

    service = EntryService(db)
    search_request = EntrySearchRequest(
        keyword=q,
        type_id=type_id,
        tag_ids=parsed_tag_ids,
        time_from=time_from,
        time_to=time_to,
        page=page,
        size=size
    )
    result = service.search(search_request)

    total = result["total"]
    page_num = result["page"]
    page_size = result["size"]
    total_pages = result["total_pages"]

    return ApiResponse.ok({
        "content": [EntryResponse.model_validate(entry).model_dump(by_alias=True) for entry in result["content"]],
        "pageNumber": page_num,
        "pageSize": page_size,
        "totalElements": total,
        "totalPages": total_pages,
        "last": page_num >= total_pages - 1,
        "first": page_num == 0,
        "empty": total == 0,
    })


@router.get("/{id}", response_model=ApiResponse)
def get_entry(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryService(db)
    entry = service.find_by_id(id)
    return ApiResponse.ok(EntryResponse.model_validate(entry).model_dump(by_alias=True))


@router.post("", response_model=ApiResponse)
def create_entry(request: EntryRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryService(db)
    entry = service.create(request)
    return ApiResponse.ok(EntryResponse.model_validate(entry).model_dump(by_alias=True))


@router.put("/{id}", response_model=ApiResponse)
def update_entry(id: UUID, request: EntryRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryService(db)
    entry = service.update(id, request)
    return ApiResponse.ok(EntryResponse.model_validate(entry).model_dump(by_alias=True))


@router.delete("/{id}", response_model=ApiResponse)
def delete_entry(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryService(db)
    service.delete(id)
    return ApiResponse.ok(None, "Entry deleted successfully")
