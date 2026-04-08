"""Assistant orchestration package.

Keep package exports lazy so importing light submodules like
`app.assistant.orchestration.chat_events` does not eagerly pull in the full
agent runtime and workflow engine during API startup.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AssistantAgent", "ChatEventAdapter", "SkillRouter"]

_EXPORTS: dict[str, tuple[str, str]] = {
    "AssistantAgent": ("app.assistant.orchestration.agent_runtime", "AssistantAgent"),
    "ChatEventAdapter": ("app.assistant.orchestration.chat_events", "ChatEventAdapter"),
    "SkillRouter": ("app.assistant.orchestration.intent_router", "SkillRouter"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
