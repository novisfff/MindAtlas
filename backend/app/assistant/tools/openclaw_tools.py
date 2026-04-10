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
from app.common.color_utils import pick_material_600_color
from app.common.exceptions import ApiException
from app.common.time import utcnow
from app.entry.models import TimeMode
from app.entry.schemas import EntryRequest, EntryResponse, EntrySearchRequest
from app.entry.service import EntryService
from app.entry_type.models import EntryType
from app.lightrag.schemas import LightRagQueryResponse
from app.lightrag.service import LightRagService
from app.openclaw_integration.schemas import (
    OpenClawCaptureEntryRequest,
    OpenClawCreateRelationRequest,
    OpenClawEntryRecordResponse,
    OpenClawGenerateMonthlyReportRequest,
    OpenClawGenerateWeeklyReportRequest,
    OpenClawGetEntryRequest,
    OpenClawQueryKnowledgeGraphRequest,
    OpenClawRelationRecordResponse,
    OpenClawSearchEntriesRequest,
    OpenClawSearchEntriesResponse,
)
from app.relation.models import RelationType
from app.relation.schemas import RelationRequest, RelationResponse
from app.relation.service import RelationService
from app.report.schemas import MonthlyReportResponse, WeeklyReportResponse
from app.report.service import MonthlyReportService, WeeklyReportService
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


def _serialize_relation(relation: Any) -> dict[str, Any]:
    relation_response = RelationResponse.model_validate(relation)
    return OpenClawRelationRecordResponse(
        id=relation_response.id,
        source_entry_id=relation_response.source_entry.id,
        source_entry_title=relation_response.source_entry.title,
        target_entry_id=relation_response.target_entry.id,
        target_entry_title=relation_response.target_entry.title,
        relation_type_code=relation_response.relation_type.code,
        relation_type_name=relation_response.relation_type.name,
        description=relation_response.description,
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


def _resolve_relation_type_id(relation_type: str) -> UUID:
    db = _get_db()
    normalized = (relation_type or "").strip()
    if not normalized:
        raise ApiException(status_code=400, code=40065, message="relationType is required")
    row = (
        db.query(RelationType)
        .filter(
            RelationType.enabled.is_(True),
            (func.lower(RelationType.code) == normalized.lower()) | (func.lower(RelationType.name) == normalized.lower()),
        )
        .first()
    )
    if row is None:
        available = [
            {"code": item.code, "name": item.name}
            for item in db.query(RelationType).filter(RelationType.enabled.is_(True)).all()
        ]
        raise ApiException(
            status_code=400,
            code=40066,
            message=f"Unknown relation type: {normalized}",
            details={"availableRelationTypes": available},
        )
    return row.id


def _resolve_tag_ids(tag_names: list[str]) -> list[UUID]:
    db = _get_db()
    resolved_ids: list[UUID] = []
    for tag_name in tag_names:
        normalized = tag_name.strip()
        if not normalized:
            continue
        existing = db.query(Tag).filter(func.lower(Tag.name) == normalized.lower()).first()
        if existing is None:
            existing = Tag(
                name=normalized,
                color=pick_material_600_color(normalized),
                description=None,
            )
            db.add(existing)
            db.flush()
        resolved_ids.append(existing.id)
    return resolved_ids


def _tool_result(factory: Callable[[], dict[str, Any]]) -> str:
    return _dump_json(factory())


@tool
def openclaw_capture_entry(
    title: str,
    entryType: str,
    summary: str | None = None,
    content: str | None = None,
    tagNames: list[str] | None = None,
    timeAt: str | None = None,
    timeFrom: str | None = None,
    timeTo: str | None = None,
) -> str:
    """Create a MindAtlas entry using the compatibility field-level capture contract."""

    payload = _validate_payload(
        OpenClawCaptureEntryRequest,
        {
            "title": title,
            "entryType": entryType,
            "summary": summary,
            "content": content,
            "tagNames": tagNames or [],
            "timeAt": timeAt,
            "timeFrom": timeFrom,
            "timeTo": timeTo,
        },
    )

    def _run() -> dict[str, Any]:
        time_mode = TimeMode.POINT
        time_at = payload.time_at
        time_from = payload.time_from
        time_to = payload.time_to
        if time_from is not None or time_to is not None:
            if time_from is None or time_to is None:
                raise ApiException(
                    status_code=400,
                    code=40067,
                    message="timeFrom and timeTo must be provided together for a ranged entry",
                )
            time_mode = TimeMode.RANGE
            time_at = None
        elif time_at is None:
            time_at = utcnow()

        entry = EntryService(_get_db()).create(
            EntryRequest.model_validate(
                {
                    "title": payload.title,
                    "summary": payload.summary,
                    "content": payload.content,
                    "typeId": _resolve_entry_type_id(payload.entry_type),
                    "tagIds": _resolve_tag_ids(payload.tag_names),
                    "timeMode": time_mode.value,
                    "timeAt": time_at,
                    "timeFrom": time_from,
                    "timeTo": time_to,
                }
            )
        )
        return _serialize_entry(entry)

    return _tool_result(_run)


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
def openclaw_create_relation(
    sourceEntryId: str,
    targetEntryId: str,
    relationType: str,
    description: str | None = None,
) -> str:
    """Create a MindAtlas relation using the compatibility relation contract."""

    payload = _validate_payload(
        OpenClawCreateRelationRequest,
        {
            "sourceEntryId": sourceEntryId,
            "targetEntryId": targetEntryId,
            "relationType": relationType,
            "description": description,
        },
    )
    return _tool_result(
        lambda: _serialize_relation(
            RelationService(_get_db()).create(
                RelationRequest.model_validate(
                    {
                        "sourceEntryId": payload.source_entry_id,
                        "targetEntryId": payload.target_entry_id,
                        "relationTypeId": _resolve_relation_type_id(payload.relation_type),
                        "description": payload.description,
                    }
                )
            )
        )
    )


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


@tool
def openclaw_generate_weekly_report(
    weekStart: str | None = None,
    forceRegenerate: bool = False,
) -> str:
    """Generate or return a MindAtlas weekly report using the compatibility report contract."""

    payload = _validate_payload(
        OpenClawGenerateWeeklyReportRequest,
        {
            "weekStart": weekStart,
            "forceRegenerate": forceRegenerate,
        },
    )

    def _run() -> dict[str, Any]:
        service = WeeklyReportService(_get_db())
        week_start = payload.week_start or service.get_last_monday()
        report = service.get_or_create_for_week(week_start)
        if payload.force_regenerate or service.should_generate_report(report):
            report = service.generate_report(report)
        return WeeklyReportResponse.model_validate(report).model_dump(mode="json", by_alias=True)

    return _tool_result(_run)


@tool
def openclaw_generate_monthly_report(
    monthStart: str | None = None,
    forceRegenerate: bool = False,
) -> str:
    """Generate or return a MindAtlas monthly report using the compatibility report contract."""

    payload = _validate_payload(
        OpenClawGenerateMonthlyReportRequest,
        {
            "monthStart": monthStart,
            "forceRegenerate": forceRegenerate,
        },
    )

    def _run() -> dict[str, Any]:
        service = MonthlyReportService(_get_db())
        month_start = payload.month_start or service.get_last_month_start()
        report = service.get_or_create_for_month(month_start)
        if payload.force_regenerate or service.should_generate_report(report):
            report = service.generate_report(report)
        return MonthlyReportResponse.model_validate(report).model_dump(mode="json", by_alias=True)

    return _tool_result(_run)
