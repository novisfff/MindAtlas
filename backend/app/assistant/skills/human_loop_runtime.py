from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.assistant_config.models import AssistantHumanApproval


ApprovalDecision = Literal["approved", "rejected"]
ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled"]
_HUMAN_FIELD_TYPES = {"string", "number", "integer", "boolean", "array"}
_HUMAN_FIELD_WIDGET_ALLOWED_TYPES: dict[str, set[str]] = {
    "input": {"string", "number", "integer"},
    "textarea": {"string"},
    "switch": {"boolean"},
    "select": {"string", "number", "integer"},
    "radio": {"string", "number", "integer"},
    "tag_selector": {"array"},
    "date": {"string"},
    "time": {"string"},
}
_HUMAN_FIELD_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HUMAN_FIELD_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_decision(raw: Any) -> ApprovalDecision:
    decision = str(raw or "").strip().lower()
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    return decision  # type: ignore[return-value]


def _default_widget(field_type: str) -> str:
    return "switch" if field_type == "boolean" else "input"


def _normalize_widget(field_type: str, field_schema: dict[str, Any]) -> str:
    raw_widget = field_schema.get("widget")
    widget = str(raw_widget or "").strip().lower() or _default_widget(field_type)
    if widget not in _HUMAN_FIELD_WIDGET_ALLOWED_TYPES:
        return _default_widget(field_type)
    if field_type not in _HUMAN_FIELD_WIDGET_ALLOWED_TYPES.get(widget, set()):
        return _default_widget(field_type)
    return widget


def _normalize_options(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    options: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            options.append(text)
    return list(dict.fromkeys(options))


def _coerce_field_value(field_schema: dict[str, Any], value: Any, *, field_name: str) -> Any:
    normalized = str(field_schema.get("type", "string") or "string").strip().lower()
    if normalized not in _HUMAN_FIELD_TYPES:
        normalized = "string"

    widget = _normalize_widget(normalized, field_schema)
    coerced: Any

    if normalized == "string":
        if value is None:
            coerced = ""
        elif isinstance(value, str):
            coerced = value
        elif isinstance(value, (dict, list)):
            coerced = json.dumps(value, ensure_ascii=False)
        else:
            coerced = str(value)
    elif normalized == "number":
        if isinstance(value, bool):
            raise ValueError(f"field '{field_name}' expects number")
        if isinstance(value, (int, float)):
            coerced = float(value)
        else:
            text = str(value).strip()
            if not text:
                raise ValueError(f"field '{field_name}' expects number")
            coerced = float(text)
    elif normalized == "integer":
        if isinstance(value, bool):
            raise ValueError(f"field '{field_name}' expects integer")
        if isinstance(value, int):
            coerced = int(value)
        elif isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f"field '{field_name}' expects integer")
            coerced = int(value)
        else:
            text = str(value).strip()
            if not text:
                raise ValueError(f"field '{field_name}' expects integer")
            if text.startswith(("+", "-")):
                sign = text[0]
                body = text[1:]
            else:
                sign = ""
                body = text
            if not body.isdigit():
                raise ValueError(f"field '{field_name}' expects integer")
            coerced = int(f"{sign}{body}")
    elif normalized == "boolean":
        if isinstance(value, bool):
            coerced = value
        else:
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "y", "on"}:
                coerced = True
            elif text in {"0", "false", "no", "n", "off"}:
                coerced = False
            else:
                raise ValueError(f"field '{field_name}' expects boolean")
    elif normalized == "array":
        if value is None:
            raw_items: list[Any] = []
        elif isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                raw_items = []
            else:
                parsed: Any = None
                if text.startswith("[") and text.endswith("]"):
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                if isinstance(parsed, list):
                    raw_items = parsed
                else:
                    raw_items = [segment.strip() for segment in text.split(",")]
        else:
            raise ValueError(f"field '{field_name}' expects string array")

        normalized_items: list[str] = []
        for item in raw_items:
            if item is None:
                continue
            item_text = item if isinstance(item, str) else str(item)
            cleaned = item_text.strip()
            if cleaned:
                normalized_items.append(cleaned)
        coerced = normalized_items
    else:
        raise ValueError(f"field '{field_name}' has unsupported type: {normalized}")

    options = _normalize_options(field_schema.get("options"))

    if widget in {"select", "radio"}:
        if not options:
            raise ValueError(f"field '{field_name}' requires options")
        option_set = set(options)
        candidates = {str(coerced).strip()}
        if isinstance(coerced, float) and coerced.is_integer():
            candidates.add(str(int(coerced)))
        if not any(candidate in option_set for candidate in candidates):
            raise ValueError(f"field '{field_name}' must be one of configured options")

    if widget == "tag_selector":
        if not isinstance(coerced, list):
            raise ValueError(f"field '{field_name}' expects string array")
        raw_allow_custom = field_schema.get("allowCustom", field_schema.get("allow_custom", None))
        allow_custom = bool(raw_allow_custom) if isinstance(raw_allow_custom, bool) else True
        if not allow_custom and options:
            unknown = [tag for tag in coerced if tag not in options]
            if unknown:
                raise ValueError(f"field '{field_name}' contains unsupported tag values")

    if widget == "date":
        text = str(coerced).strip()
        if not text:
            return ""
        if not _HUMAN_FIELD_DATE_RE.fullmatch(text):
            raise ValueError(f"field '{field_name}' expects date format YYYY-MM-DD")
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"field '{field_name}' expects date format YYYY-MM-DD") from exc
        coerced = text

    if widget == "time":
        text = str(coerced).strip()
        if not text:
            return ""
        if not _HUMAN_FIELD_TIME_RE.fullmatch(text):
            raise ValueError(f"field '{field_name}' expects time format HH:mm")
        coerced = text

    return coerced


def _validate_submitted_values(field_schema: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    schema_by_name: dict[str, dict[str, Any]] = {}
    for field in field_schema:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "") or "").strip()
        if not name:
            continue
        schema_by_name[name] = field

    for name, schema in schema_by_name.items():
        required = bool(schema.get("required", False))
        if name not in values:
            if required:
                raise ValueError(f"field '{name}' is required")
            continue
        coerced = _coerce_field_value(schema, values.get(name), field_name=name)
        if required:
            if isinstance(coerced, str) and not coerced.strip():
                raise ValueError(f"field '{name}' is required")
            if isinstance(coerced, list) and not coerced:
                raise ValueError(f"field '{name}' is required")
        validated[name] = coerced

    unknown = sorted(set(values.keys()) - set(schema_by_name.keys()))
    if unknown:
        raise ValueError(f"unknown approval fields: {', '.join(unknown)}")

    return validated


def serialize_human_approval(row: AssistantHumanApproval) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "runId": row.run_id,
        "channelType": row.channel_type,
        "conversationId": str(row.conversation_id) if row.conversation_id else None,
        "messageId": str(row.message_id) if row.message_id else None,
        "workflowId": str(row.workflow_id) if row.workflow_id else None,
        "skillId": str(row.skill_id) if row.skill_id else None,
        "nodeId": row.node_id,
        "nodeLabel": row.node_label,
        "status": row.status,
        "requestPayload": copy.deepcopy(row.request_payload) if isinstance(row.request_payload, dict) else {},
        "fieldSchema": copy.deepcopy(row.field_schema) if isinstance(row.field_schema, list) else [],
        "initialValues": copy.deepcopy(row.initial_values) if isinstance(row.initial_values, dict) else {},
        "submittedValues": copy.deepcopy(row.submitted_values) if isinstance(row.submitted_values, dict) else {},
        "decision": row.decision,
        "comment": row.comment,
        "resolvedAt": row.resolved_at.isoformat() if isinstance(row.resolved_at, datetime) else None,
        "createdAt": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "updatedAt": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else None,
    }


class HumanLoopCoordinator:
    """In-process wait/notify bridge for approval resolution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters: dict[str, threading.Event] = {}
        self._payloads: dict[str, dict[str, Any]] = {}

    def register(self, approval_id: str) -> None:
        with self._lock:
            self._waiters.setdefault(approval_id, threading.Event())

    def unregister(self, approval_id: str) -> None:
        with self._lock:
            self._waiters.pop(approval_id, None)
            self._payloads.pop(approval_id, None)

    def resolve(self, approval_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._payloads[approval_id] = payload
            waiter = self._waiters.get(approval_id)
            if waiter is not None:
                waiter.set()

    def wait_once(self, approval_id: str, timeout: float) -> dict[str, Any] | None:
        with self._lock:
            payload = self._payloads.pop(approval_id, None)
            waiter = self._waiters.get(approval_id)
        if payload is not None:
            return payload
        if waiter is None:
            return None
        waiter.wait(timeout)
        waiter.clear()
        with self._lock:
            return self._payloads.pop(approval_id, None)


GLOBAL_HUMAN_LOOP_COORDINATOR = HumanLoopCoordinator()


@dataclass
class HumanLoopContext:
    run_id: str
    channel_type: str
    conversation_id: UUID | None = None
    workflow_id: UUID | None = None
    skill_id: UUID | None = None
    message_id: UUID | None = None


class HumanLoopRuntime:
    """Persists approval records and blocks execution until user decision arrives."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        context: HumanLoopContext,
        on_requested: Callable[[dict[str, Any]], None] | None = None,
        on_resolved: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._context = context
        self._on_requested = on_requested
        self._on_resolved = on_resolved

    def create_and_wait(
        self,
        *,
        node_id: str,
        node_label: str,
        request_payload: dict[str, Any],
        field_schema: list[dict[str, Any]],
        initial_values: dict[str, Any],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            approval = AssistantHumanApproval(
                run_id=self._context.run_id,
                channel_type=self._context.channel_type,
                conversation_id=self._context.conversation_id,
                message_id=self._context.message_id,
                workflow_id=self._context.workflow_id,
                skill_id=self._context.skill_id,
                node_id=node_id,
                node_label=node_label,
                status="pending",
                request_payload=copy.deepcopy(request_payload),
                field_schema=copy.deepcopy(field_schema),
                initial_values=copy.deepcopy(initial_values),
            )
            session.add(approval)
            session.commit()
            session.refresh(approval)
            payload = serialize_human_approval(approval)

        approval_id = payload["id"]
        GLOBAL_HUMAN_LOOP_COORDINATOR.register(approval_id)
        try:
            if callable(self._on_requested):
                self._on_requested(payload)

            while True:
                notified = GLOBAL_HUMAN_LOOP_COORDINATOR.wait_once(approval_id, timeout=0.5)
                if notified is not None:
                    if callable(self._on_resolved):
                        self._on_resolved(notified)
                    return notified

                with self._session_factory() as session:
                    refreshed = session.get(AssistantHumanApproval, UUID(approval_id))
                    if refreshed is None:
                        raise RuntimeError("human approval record not found")
                    if refreshed.status != "pending":
                        resolved = serialize_human_approval(refreshed)
                        if callable(self._on_resolved):
                            self._on_resolved(resolved)
                        return resolved
        finally:
            GLOBAL_HUMAN_LOOP_COORDINATOR.unregister(approval_id)


def list_pending_approvals_for_conversation(db: Session, conversation_id: UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(AssistantHumanApproval)
        .filter(
            AssistantHumanApproval.conversation_id == conversation_id,
            AssistantHumanApproval.status == "pending",
        )
        .order_by(AssistantHumanApproval.created_at.asc())
        .all()
    )
    return [serialize_human_approval(row) for row in rows]


def submit_human_approval_decision(
    db: Session,
    *,
    approval_id: UUID,
    decision: Any,
    values: Any,
    comment: Any,
    expected_run_id: str | None = None,
    expected_conversation_id: UUID | None = None,
) -> dict[str, Any]:
    row = db.get(AssistantHumanApproval, approval_id)
    if row is None:
        raise ValueError("approval not found")

    if expected_run_id and row.run_id != expected_run_id:
        raise ValueError("approval does not belong to the specified run")
    if expected_conversation_id and row.conversation_id != expected_conversation_id:
        raise ValueError("approval does not belong to the specified conversation")

    if row.status != "pending":
        raise ValueError("approval is already resolved")

    normalized_values = values if isinstance(values, dict) else {}
    validated_values = _validate_submitted_values(
        row.field_schema if isinstance(row.field_schema, list) else [],
        normalized_values,
    )

    normalized_comment = None
    if comment is not None:
        normalized_comment = str(comment).strip() or None

    request_payload = row.request_payload if isinstance(row.request_payload, dict) else {}
    require_reject_comment = bool(request_payload.get("requireRejectComment", True))

    normalized_decision = _normalize_decision(decision)
    if normalized_decision == "rejected" and require_reject_comment and not normalized_comment:
        raise ValueError("comment is required when rejecting this approval")

    resolved_at = utcnow()
    row.submitted_values = validated_values
    row.decision = normalized_decision
    row.status = normalized_decision
    row.comment = normalized_comment
    row.resolved_at = resolved_at
    db.commit()
    db.refresh(row)

    payload = serialize_human_approval(row)
    GLOBAL_HUMAN_LOOP_COORDINATOR.resolve(str(row.id), payload)
    return payload
