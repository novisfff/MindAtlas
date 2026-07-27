"""Assistant orchestration package.

Legacy Supervisor/IntentRouter/AssistantAgent were removed in Plan 10 B2.
Shared light helpers (chat events, OpenAI fallback client) remain.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ChatEventAdapter"]

_EXPORTS: dict[str, tuple[str, str]] = {
    "ChatEventAdapter": ("app.assistant.orchestration.chat_events", "ChatEventAdapter"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r} "
            f"(legacy Supervisor/IntentRouter removed in Plan 10 B2)"
        )
    module_name, attr_name = target
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
