"""Compatibility tool wrappers for catalog-facing capability contracts."""
from __future__ import annotations

import json
from typing import Any, Callable, TypeVar
from uuid import UUID

from langchain_core.tools import tool
from pydantic import ValidationError
from sqlalchemy import func

from app.assistant.tools._context import get_current_db
from app.assistant.tools.kb_tools import _run_async
from app.common.exceptions import ApiException
from app.entry.schemas import EntryResponse, EntrySearchRequest
from app.entry.service import EntryService
from app.entry_type.models import EntryType
from app.lightrag.schemas import LightRagQueryResponse
from app.lightrag.service import LightRagService
from app.openclaw_integration.schemas import (
    OpenClawEntryRecordResponse,
    OpenClawGetEntryRequest,
    OpenClawQueryKnowledgeGraphRequest,
    OpenClawSearchEntriesRequest,
    OpenClawSearchEntriesResponse,
)
from app.tag.models import Tag

T = TypeVar("T")


def _get_db():
    return get_current_db()


def _validate_payload(model_cls: type[T], data: dict[str, Any]) -> T:
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ApiException(
            status_code=400,
            code=40068,
            message="Invalid capability tool input",
            details={"errors": exc.errors()},
        ) from exc


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _serialize_entry(entry: Any) -> dict[str, Any]:
    entry_response = EntryResponse.model_validate(entry)
    return OpenClawEntryRecordResponse(
        id=entry_response.id,
        title=entry_response.title,
        summary=entry_response.summary,
        content=entry_response.content,
        entry_type_code=entry_response.type.code,
        entry_type_name=entry_response.type.name,
        tag_names=[tag.name for tag in entry_response.tags],
        time_mode=entry_response.time_mode.value,
        time_at=entry_response.time_at,
        time_from=entry_response.time_from,
        time_to=entry_response.time_to,
        created_at=entry_response.created_at,
        updated_at=entry_response.updated_at,
    ).model_dump(mode="json", by_alias=True)


def _resolve_entry_type_id(entry_type: str) -> UUID:
    db = _get_db()
    normalized = (entry_type or "").strip()
    if not normalized:
        raise ApiException(status_code=400, code=40063, message="entryType is required")
    row = (
        db.query(EntryType)
        .filter(
            EntryType.enabled.is_(True),
            (func.lower(EntryType.code) == normalized.lower()) | (func.lower(EntryType.name) == normalized.lower()),
        )
        .first()
    )
    if row is None:
        available = [
            {"code": item.code, "name": item.name}
            for item in db.query(EntryType).filter(EntryType.enabled.is_(True)).all()
        ]
        raise ApiException(
            status_code=400,
            code=40064,
            message=f"Unknown entry type: {normalized}",
            details={"availableEntryTypes": available},
        )
    return row.id


def _tool_result(factory: Callable[[], dict[str, Any]]) -> str:
    return _dump_json(factory())


@tool
def openclaw_search_entries(
    query: str | None = None,
    entryType: str | None = None,
    tagNames: list[str] | None = None,
    timeFrom: str | None = None,
    timeTo: str | None = None,
    limit: int = 10,
) -> str:
    """Search MindAtlas entries using the compatibility retrieval contract."""

    payload = _validate_payload(
        OpenClawSearchEntriesRequest,
        {
            "query": query,
            "entryType": entryType,
            "tagNames": tagNames or [],
            "timeFrom": timeFrom,
            "timeTo": timeTo,
            "limit": limit,
        },
    )

    def _run() -> dict[str, Any]:
        db = _get_db()
        tag_ids: list[UUID] | None = None
        if payload.tag_names:
            tags = (
                db.query(Tag)
                .filter(func.lower(Tag.name).in_([name.lower() for name in payload.tag_names]))
                .all()
            )
            if len(tags) != len(payload.tag_names):
                return OpenClawSearchEntriesResponse(total=0, items=[]).model_dump(mode="json", by_alias=True)
            tag_ids = [tag.id for tag in tags]

        entry_type_id = _resolve_entry_type_id(payload.entry_type) if payload.entry_type else None
        result = EntryService(db).search(
            EntrySearchRequest(
                keyword=payload.query,
                type_id=entry_type_id,
                tag_ids=tag_ids,
                time_from=payload.time_from,
                time_to=payload.time_to,
                page=0,
                size=payload.limit,
            )
        )
        return OpenClawSearchEntriesResponse(
            total=result["total"],
            items=[OpenClawEntryRecordResponse.model_validate(_serialize_entry(entry)) for entry in result["content"]],
        ).model_dump(mode="json", by_alias=True)

    return _tool_result(_run)


@tool
def openclaw_get_entry(entryId: str) -> str:
    """Get a MindAtlas entry using the compatibility detail contract."""

    payload = _validate_payload(OpenClawGetEntryRequest, {"entryId": entryId})
    return _tool_result(lambda: _serialize_entry(EntryService(_get_db()).find_by_id(payload.entry_id)))


@tool
def openclaw_query_knowledge_graph(
    query: str,
    mode: str = "hybrid",
    topK: int = 5,
) -> str:
    """Query the MindAtlas knowledge graph using the compatibility graph contract."""

    payload = _validate_payload(
        OpenClawQueryKnowledgeGraphRequest,
        {
            "query": query,
            "mode": mode,
            "topK": topK,
        },
    )
    result = _run_async(
        lambda: LightRagService().query(
            query=payload.query,
            mode=payload.mode,
            top_k=payload.top_k,
        )
    )
    return _dump_json(LightRagQueryResponse.model_validate(result).model_dump(mode="json", by_alias=True))
