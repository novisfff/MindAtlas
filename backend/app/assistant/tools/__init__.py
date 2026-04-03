from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "search_entries": ("app.assistant.tools.entry_tools", "search_entries"),
    "get_entry_detail": ("app.assistant.tools.entry_tools", "get_entry_detail"),
    "create_entry": ("app.assistant.tools.entry_tools", "create_entry"),
    "update_entry": ("app.assistant.tools.entry_tools", "update_entry"),
    "openclaw_capture_entry": ("app.assistant.tools.openclaw_tools", "openclaw_capture_entry"),
    "openclaw_search_entries": ("app.assistant.tools.openclaw_tools", "openclaw_search_entries"),
    "openclaw_get_entry": ("app.assistant.tools.openclaw_tools", "openclaw_get_entry"),
    "openclaw_create_relation": ("app.assistant.tools.openclaw_tools", "openclaw_create_relation"),
    "openclaw_query_knowledge_graph": ("app.assistant.tools.openclaw_tools", "openclaw_query_knowledge_graph"),
    "openclaw_generate_weekly_report": ("app.assistant.tools.openclaw_tools", "openclaw_generate_weekly_report"),
    "openclaw_generate_monthly_report": ("app.assistant.tools.openclaw_tools", "openclaw_generate_monthly_report"),
    "get_statistics": ("app.assistant.tools.stats_tools", "get_statistics"),
    "get_entries_by_time_range": ("app.assistant.tools.stats_tools", "get_entries_by_time_range"),
    "analyze_activity": ("app.assistant.tools.stats_tools", "analyze_activity"),
    "get_tag_statistics": ("app.assistant.tools.stats_tools", "get_tag_statistics"),
    "list_entry_types": ("app.assistant.tools.helper_tools", "list_entry_types"),
    "list_tags": ("app.assistant.tools.helper_tools", "list_tags"),
    "kb_search": ("app.assistant.tools.kb_tools", "kb_search"),
    "kb_relation_recommendations": ("app.assistant.tools.kb_tools", "kb_relation_recommendations"),
}

__all__ = [
    "search_entries",
    "get_entry_detail",
    "create_entry",
    "openclaw_capture_entry",
    "openclaw_search_entries",
    "openclaw_get_entry",
    "openclaw_create_relation",
    "openclaw_query_knowledge_graph",
    "openclaw_generate_weekly_report",
    "openclaw_generate_monthly_report",
    "get_statistics",
    "get_entries_by_time_range",
    "analyze_activity",
    "get_tag_statistics",
    "list_entry_types",
    "list_tags",
    "kb_relation_recommendations",
]


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
