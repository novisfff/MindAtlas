"""Utilities for consistent doc_id/file_path formatting in LightRAG indexing.

Convention:
- Entry doc: doc_id = entry_id, file_path = entry_id
- Attachment doc: doc_id = "attachment:{attachment_id}", file_path = "{entry_id}/attachments/{attachment_id}"
"""
from __future__ import annotations

from uuid import UUID

# Attachment doc_id prefix
ATTACHMENT_PREFIX = "attachment:"


def build_attachment_doc_id(attachment_id: str) -> str:
    """Build doc_id for attachment document.

    Args:
        attachment_id: Attachment UUID string

    Returns:
        doc_id in format "attachment:{attachment_id}"
    """
    return f"{ATTACHMENT_PREFIX}{attachment_id}"


def build_attachment_file_path(entry_id: str, attachment_id: str) -> str:
    """Build unique file_path for attachment document.

    Args:
        entry_id: Entry UUID string
        attachment_id: Attachment UUID string

    Returns:
        file_path in format "{entry_id}/attachments/{attachment_id}"
    """
    return f"{entry_id}/attachments/{attachment_id}"


def is_attachment_doc_id(doc_id: str) -> bool:
    """Check if doc_id is an attachment document.

    Args:
        doc_id: Document ID string

    Returns:
        True if doc_id starts with "attachment:"
    """
    return (doc_id or "").startswith(ATTACHMENT_PREFIX)


def parse_attachment_id_from_doc_id(doc_id: str) -> str | None:
    """Extract attachment_id from attachment doc_id.

    Args:
        doc_id: Document ID in format "attachment:{attachment_id}"

    Returns:
        attachment_id string or None if not an attachment doc_id
    """
    if not is_attachment_doc_id(doc_id):
        return None
    return doc_id[len(ATTACHMENT_PREFIX):].strip() or None


def parse_entry_id_from_attachment_file_path(file_path: str) -> str | None:
    """Extract entry_id from attachment file_path.

    Supports both old and new formats for backward compatibility:
    - Old format: file_path = entry_id (UUID string)
    - New format: file_path = "{entry_id}/attachments/{attachment_id}"

    Args:
        file_path: File path string

    Returns:
        entry_id string or None if cannot be parsed
    """
    fp = (file_path or "").strip()
    if not fp:
        return None

    # New format: "{entry_id}/attachments/{attachment_id}"
    if "/attachments/" in fp:
        entry_part = fp.split("/attachments/")[0].strip()
        if entry_part:
            try:
                UUID(entry_part)
                return entry_part
            except (ValueError, TypeError):
                pass
        return None

    # Old format: file_path is just entry_id (UUID)
    try:
        UUID(fp)
        return fp
    except (ValueError, TypeError):
        return None


def parse_attachment_id_from_attachment_file_path(file_path: str) -> str | None:
    """Extract attachment_id from attachment file_path.

    Format:
        file_path = "{entry_id}/attachments/{attachment_id}"

    Args:
        file_path: File path string

    Returns:
        attachment_id string or None if cannot be parsed
    """
    fp = (file_path or "").strip()
    if not fp or "/attachments/" not in fp:
        return None

    tail = fp.split("/attachments/", 1)[1].strip().strip("/")
    if not tail:
        return None
    attachment_part = tail.split("/", 1)[0].strip()
    if not attachment_part:
        return None
    try:
        UUID(attachment_part)
        return attachment_part
    except (ValueError, TypeError):
        return None
