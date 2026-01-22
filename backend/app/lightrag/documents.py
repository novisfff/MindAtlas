"""Document payload builder for LightRAG indexing (Phase 4, contract upgrade)."""
from __future__ import annotations

from datetime import datetime

from app.entry.models import Entry
from app.lightrag.types import DocumentPayload


def should_index(payload: DocumentPayload) -> bool:
    """Whether the entry should be indexed by LightRAG.

    Current policy (Phase 4):
    - Only index when EntryType.enabled && graph_enabled && ai_enabled are all true.
    - Otherwise, worker will translate the event to a delete to clean up residue.
    """
    return bool(payload.type_enabled and payload.graph_enabled and payload.ai_enabled)


def render_entry_text(
    *,
    title: str,
    summary: str | None,
    content: str | None,
    type_name: str | None,
    type_code: str | None,
    tags: list[str],
) -> str:
    """Render a stable text template for LightRAG ingestion.

    Template format:
        Title: <title>
        Type: <type_name> (<type_code>)
        Tags: a, b, c

        Summary:
        <summary>

        Content:
        <content>
    """
    safe_title = (title or "").strip()
    safe_summary = (summary or "").strip() or None
    safe_content = (content or "").strip() or None
    safe_tags = [t.strip() for t in (tags or []) if t and t.strip()]

    lines: list[str] = []
    lines.append(f"Title: {safe_title}")

    if type_name or type_code:
        if type_name and type_code:
            lines.append(f"Type: {type_name} ({type_code})")
        else:
            lines.append(f"Type: {type_name or type_code}")

    if safe_tags:
        lines.append(f"Tags: {', '.join(sorted(set(safe_tags)))}")

    if safe_summary:
        lines.append("")
        lines.append("Summary:")
        lines.append(safe_summary)

    if safe_content:
        lines.append("")
        lines.append("Content:")
        lines.append(safe_content)

    return "\n".join(lines).strip()


def build_document_payload(*, entry: Entry, entry_updated_at: datetime | None) -> DocumentPayload:
    """Build the Worker->Indexer payload from an Entry row.

    Args:
        entry: The Entry model instance (with type and tags loaded)
        entry_updated_at: The timestamp from outbox event

    Returns:
        DocumentPayload ready to be passed to Indexer
    """
    et = entry.type

    type_code = getattr(et, "code", None) if et else None
    type_name = getattr(et, "name", None) if et else None
    type_enabled = bool(getattr(et, "enabled", False)) if et else False
    graph_enabled = bool(getattr(et, "graph_enabled", False)) if et else False
    ai_enabled = bool(getattr(et, "ai_enabled", False)) if et else False

    tags = [t.name for t in (entry.tags or []) if getattr(t, "name", None)]
    tag_ids = [t.id for t in (entry.tags or []) if getattr(t, "id", None)]

    text = render_entry_text(
        title=entry.title,
        summary=entry.summary,
        content=entry.content,
        type_name=type_name,
        type_code=type_code,
        tags=tags,
    )

    return DocumentPayload(
        entry_id=entry.id,
        entry_updated_at=entry_updated_at,
        type_id=entry.type_id,
        type_code=type_code,
        type_name=type_name,
        type_enabled=type_enabled,
        graph_enabled=graph_enabled,
        ai_enabled=ai_enabled,
        title=entry.title,
        summary=entry.summary,
        content=entry.content,
        tags=sorted(set(tags)),
        tag_ids=tag_ids,
        text=text,
    )
