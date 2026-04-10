from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from app.system_settings.service import (
    get_system_language_name,
    resolve_system_locale,
)


@dataclass(frozen=True)
class ParsedExecutionContext:
    raw_context: dict[str, Any]
    stream_output_enabled: bool
    structured_input: dict[str, Any] | None
    run_id: str
    channel_type: str
    conversation_id: str
    conversation_id_uuid: UUID | None
    message_id_uuid: UUID | None
    workflow_id_uuid: UUID | None
    skill_id_uuid: UUID | None
    locale: str
    language: str
    sys_vars: dict[str, str]


def _parse_uuid_context(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return UUID(text)
    except Exception:
        return None


def parse_execution_context(
    *,
    runtime_context: dict[str, Any] | None,
    parse_output_boolean: Callable[[Any], bool],
) -> ParsedExecutionContext:
    context = runtime_context or {}
    raw_stream_output = context.get("stream_output", context.get("streamOutput", True))
    try:
        stream_output_enabled = parse_output_boolean(raw_stream_output)
    except Exception:
        stream_output_enabled = True

    raw_structured_input = context.get("structured_input", context.get("structuredInput"))
    structured_input: dict[str, Any] | None = raw_structured_input if isinstance(raw_structured_input, dict) else None

    run_id = str(context.get("run_id", context.get("runId", "")) or "").strip()
    if not run_id:
        run_id = uuid4().hex

    channel_type = str(context.get("channel_type", context.get("channelType", "")) or "").strip()
    if not channel_type:
        channel_type = "assistant_chat"

    conversation_id = str(context.get("conversation_id", context.get("conversationId", "")) or "")
    conversation_id_uuid = _parse_uuid_context(context.get("conversation_id", context.get("conversationId")))
    message_id_uuid = _parse_uuid_context(context.get("message_id", context.get("messageId")))
    workflow_id_uuid = _parse_uuid_context(context.get("workflow_id", context.get("workflowId")))
    skill_id_uuid = _parse_uuid_context(context.get("skill_id", context.get("skillId")))
    locale = resolve_system_locale(
        preferred_locale=str(context.get("locale", context.get("systemLocale", "")) or "").strip() or None
    )
    language = get_system_language_name(locale)
    request_source = str(
        context.get(
            "request_source",
            context.get("requestSource", context.get("openclaw_source", context.get("openclawSource", ""))),
        )
        or ""
    ).strip()
    request_channel = str(
        context.get(
            "request_channel",
            context.get("requestChannel", context.get("openclaw_channel", context.get("openclawChannel", ""))),
        )
        or ""
    ).strip()
    request_session = str(
        context.get(
            "request_session",
            context.get("requestSession", context.get("openclaw_session", context.get("openclawSession", ""))),
        )
        or ""
    ).strip()
    request_tool = str(
        context.get(
            "request_tool",
            context.get("requestTool", context.get("openclaw_tool", context.get("openclawTool", ""))),
        )
        or ""
    ).strip()

    now_utc = datetime.now(timezone.utc)
    sys_vars = {
        "date": now_utc.date().isoformat(),
        "datetime": now_utc.replace(microsecond=0, tzinfo=None).isoformat(),
        "conversation_id": conversation_id,
        "locale": locale,
        "language": language,
        "request_source": request_source,
        "request_channel": request_channel,
        "request_session": request_session,
        "request_tool": request_tool,
        # Keep legacy aliases so older copied workflows that still reference sys.openclaw_* keep working.
        "openclaw_source": request_source,
        "openclaw_channel": request_channel,
        "openclaw_session": request_session,
        "openclaw_tool": request_tool,
    }

    return ParsedExecutionContext(
        raw_context=context,
        stream_output_enabled=stream_output_enabled,
        structured_input=structured_input,
        run_id=run_id,
        channel_type=channel_type,
        conversation_id=conversation_id,
        conversation_id_uuid=conversation_id_uuid,
        message_id_uuid=message_id_uuid,
        workflow_id_uuid=workflow_id_uuid,
        skill_id_uuid=skill_id_uuid,
        locale=locale,
        language=language,
        sys_vars=sys_vars,
    )
