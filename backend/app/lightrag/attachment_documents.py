"""Attachment document payload builder for LightRAG indexing."""
from __future__ import annotations


def render_attachment_text(
    *,
    entry_id: str,
    entry_title: str | None,
    original_filename: str,
    content_type: str,
    parsed_text: str,
) -> str:
    safe_entry_id = (entry_id or "").strip()
    safe_entry_title = (entry_title or "").strip() or None
    safe_name = (original_filename or "").strip() or "attachment"
    safe_type = (content_type or "").strip() or "application/octet-stream"
    body = (parsed_text or "").strip()

    lines: list[str] = []
    lines.append(f"Attachment: {safe_name}")
    lines.append(f"Content-Type: {safe_type}")
    lines.append(f"EntryId: {safe_entry_id}")
    if safe_entry_title:
        lines.append(f"EntryTitle: {safe_entry_title}")

    if body:
        lines.append("")
        lines.append("Content:")
        lines.append(body)

    return "\n".join(lines).strip()

