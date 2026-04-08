"""Relation-related assistant tools."""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import func

from app.relation.models import RelationType
from app.relation.schemas import RelationRequest, RelationResponse
from app.relation.service import RelationService


def _get_db():
    from app.assistant.tools._context import get_current_db

    return get_current_db()


def _resolve_relation_type_id(relation_type: str) -> UUID:
    db = _get_db()
    normalized = str(relation_type or "").strip()
    if not normalized:
        raise ValueError("relation_type is required")

    row = (
        db.query(RelationType)
        .filter(
            RelationType.enabled.is_(True),
            (func.lower(RelationType.code) == normalized.lower())
            | (func.lower(RelationType.name) == normalized.lower()),
        )
        .first()
    )
    if row is None:
        raise ValueError(f"unknown relation_type: {relation_type}")
    return row.id


def build_relation_payload(
    *,
    source_entry_id: str,
    target_entry_id: str,
    relation_type: str,
    description: Optional[str] = None,
) -> dict[str, Any]:
    relation = RelationService(_get_db()).create(
        RelationRequest.model_validate(
            {
                "sourceEntryId": source_entry_id,
                "targetEntryId": target_entry_id,
                "relationTypeId": _resolve_relation_type_id(relation_type),
                "description": description,
            }
        )
    )
    response = RelationResponse.model_validate(relation)
    return {
        "id": str(response.id),
        "source_entry_id": str(response.source_entry.id),
        "source_entry_title": response.source_entry.title,
        "target_entry_id": str(response.target_entry.id),
        "target_entry_title": response.target_entry.title,
        "relation_type_code": response.relation_type.code,
        "relation_type_name": response.relation_type.name,
        "description": response.description,
        "created_at": response.created_at.isoformat() if response.created_at else "",
        "updated_at": response.updated_at.isoformat() if response.updated_at else "",
    }


@tool
def create_relation(
    source_entry_id: str,
    target_entry_id: str,
    relation_type: str,
    description: Optional[str] = None,
) -> str:
    """在两条记录之间创建关联关系。

    Args:
        source_entry_id: 源记录 UUID
        target_entry_id: 目标记录 UUID
        relation_type: 关系类型编码或名称
        description: 关系说明（可选）

    Returns:
        新建关系的结构化结果（JSON格式）
    """

    payload = build_relation_payload(
        source_entry_id=source_entry_id,
        target_entry_id=target_entry_id,
        relation_type=relation_type,
        description=description,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)
