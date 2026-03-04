from __future__ import annotations

from typing import Any


def _normalize_history_message(item: dict[str, Any]) -> tuple[str, str] | None:
    role = str(item.get("role") or "").strip().lower()
    if role not in {"user", "assistant"}:
        return None
    content = str(item.get("content") or "").strip()
    if not content:
        return None
    return role, content


def _render_prefixed_line(role: str, content: str) -> str:
    prefix = "User" if role == "user" else "Assistant"
    return f"{prefix}: {content}"


def _truncate_single_line(line: str, chars_limit: int) -> str:
    if len(line) <= chars_limit:
        return line
    if chars_limit <= 3:
        return line[:chars_limit]
    return f"{line[: chars_limit - 3]}..."


def build_l0_window(
    history: list[dict[str, Any]] | None,
    user_input: str,
    turns_limit: int,
    chars_limit: int,
) -> dict[str, Any]:
    normalized_turns = max(1, int(turns_limit or 1))
    normalized_chars = max(1, int(chars_limit or 1))

    messages: list[tuple[str, str]] = []
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_history_message(raw)
        if normalized is None:
            continue
        messages.append(normalized)

    current_input = str(user_input or "").strip()
    if messages and current_input:
        last_role, last_content = messages[-1]
        if last_role == "user" and last_content.strip() == current_input:
            messages.pop()

    max_messages = normalized_turns * 2
    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    l0_messages: list[dict[str, str]] = [
        {"role": role, "content": content}
        for role, content in messages
    ]
    lines = [_render_prefixed_line(role, content) for role, content in messages]
    original_text = "\n".join(lines)

    while len(lines) > 1 and len("\n".join(lines)) > normalized_chars:
        lines.pop(0)
        l0_messages.pop(0)

    if lines and len("\n".join(lines)) > normalized_chars:
        line = _truncate_single_line(lines[0], normalized_chars)
        lines[0] = line
        role = str(l0_messages[0].get("role", "") or "").strip().lower()
        prefix = "User: " if role == "user" else "Assistant: "
        if line.startswith(prefix):
            l0_messages[0]["content"] = line[len(prefix) :]
        else:
            l0_messages[0]["content"] = ""

    l0_messages = [
        {
            "role": str(item.get("role", "") or "").strip().lower(),
            "content": str(item.get("content", "") or "").strip(),
        }
        for item in l0_messages
        if isinstance(item, dict)
        and str(item.get("role", "") or "").strip().lower() in {"user", "assistant"}
        and str(item.get("content", "") or "").strip()
    ]

    l0_text = "\n".join(lines)
    return {
        "l0_text": l0_text,
        "l0_messages": l0_messages,
        "l0_source_count": len(lines),
        "l0_trimmed_chars": max(0, len(original_text) - len(l0_text)),
    }
