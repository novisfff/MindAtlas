from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, get_args, get_origin

from sqlalchemy.orm import Session, joinedload, selectinload

from app.assistant_config.models import (
    AssistantSkill,
    AssistantTool,
    AssistantWorkflow,
)
from app.assistant_config.remote_tool import RemoteTool


@dataclass(frozen=True)
class SystemToolDefinition:
    name: str
    description: str
    display_name: str
    display_description: str | None = None


@dataclass(frozen=True)
class SystemToolParamDefinition:
    name: str
    description: str | None
    param_type: str
    required: bool


@dataclass(frozen=True)
class SystemToolOutputDefinition:
    name: str
    description: str | None
    param_type: str


@dataclass(frozen=True)
class SystemToolFullDefinition:
    name: str
    description: str
    display_name: str
    display_description: str | None
    input_params: list[SystemToolParamDefinition]
    output_params: list[SystemToolOutputDefinition]
    returns: str | None
    json_schema: dict | None


@dataclass(frozen=True)
class SystemToolDisplayDefinition:
    display_name: str
    display_description: str | None = None


@dataclass(frozen=True)
class SystemSkillDefinition:
    name: str
    description: str


@dataclass(frozen=True)
class SystemSkillFullDefinition:
    name: str
    description: str
    intent_examples: list[str]
    tools: list[str]
    mode: str
    langgraph_pattern: str | None
    system_prompt: str | None
    kb_config: dict | None
    hidden: bool
    workflow_nodes: list[dict[str, Any]] | None = None
    workflow_edges: list[dict[str, Any]] | None = None
    workflow_version: int = 1
    workflow_viewport: dict | None = None


class _BaseRegistry:
    """DB 驱动注册表基类。"""

    def __init__(self, db: Session):
        self.db = db


_SYSTEM_TOOL_DISPLAY_METADATA: dict[str, dict[str, dict[str, str]]] = {
    "search_entries": {
        "zh": {
            "source_name": "搜索记录",
            "source_description": "按关键词、类型、标签或时间范围检索已保存的记录。",
        },
        "en": {
            "source_name": "Search Entries",
            "source_description": "Search saved entries by keyword, type, tags, or time range.",
        },
    },
    "get_entry_detail": {
        "zh": {
            "source_name": "获取记录详情",
            "source_description": "根据记录 ID 读取单条记录的完整详情。",
        },
        "en": {
            "source_name": "Get Entry Detail",
            "source_description": "Load the full details of a specific entry by entry ID.",
        },
    },
    "create_entry": {
        "zh": {
            "source_name": "创建记录",
            "source_description": "向 MindAtlas 新建一条结构化记录。",
        },
        "en": {
            "source_name": "Create Entry",
            "source_description": "Create a new structured entry in MindAtlas.",
        },
    },
    "update_entry": {
        "zh": {
            "source_name": "更新记录",
            "source_description": "根据记录 ID 更新一条已有记录的内容和字段。",
        },
        "en": {
            "source_name": "Update Entry",
            "source_description": "Update an existing entry by entry ID.",
        },
    },
    "create_relation": {
        "zh": {
            "source_name": "创建关联",
            "source_description": "在两条已有记录之间创建关系。",
        },
        "en": {
            "source_name": "Create Relation",
            "source_description": "Create a relation between two existing entries.",
        },
    },
    "query_knowledge_graph": {
        "zh": {
            "source_name": "查询知识图谱",
            "source_description": "查询跨记录关系并返回综合知识回答。",
        },
        "en": {
            "source_name": "Query Knowledge Graph",
            "source_description": "Query cross-entry relationships and return a synthesized knowledge answer.",
        },
    },
    "generate_weekly_report": {
        "zh": {
            "source_name": "生成周报",
            "source_description": "生成或读取指定周的结构化周报。",
        },
        "en": {
            "source_name": "Generate Weekly Report",
            "source_description": "Generate or retrieve a structured weekly report.",
        },
    },
    "generate_monthly_report": {
        "zh": {
            "source_name": "生成月报",
            "source_description": "生成或读取指定月份的结构化月报。",
        },
        "en": {
            "source_name": "Generate Monthly Report",
            "source_description": "Generate or retrieve a structured monthly report.",
        },
    },
    "get_statistics": {
        "zh": {
            "source_name": "获取统计",
            "source_description": "读取记录总量、类型分布等整体统计信息。",
        },
        "en": {
            "source_name": "Get Statistics",
            "source_description": "Read high-level statistics such as total entries and type distribution.",
        },
    },
    "get_entries_by_time_range": {
        "zh": {
            "source_name": "时间范围查询",
            "source_description": "按时间范围列出符合条件的记录。",
        },
        "en": {
            "source_name": "Query by Time Range",
            "source_description": "List entries that fall within a specific time range.",
        },
    },
    "analyze_activity": {
        "zh": {
            "source_name": "活动分析",
            "source_description": "分析记录活动的时间分布和行为模式。",
        },
        "en": {
            "source_name": "Analyze Activity",
            "source_description": "Analyze activity distribution and behavior patterns over time.",
        },
    },
    "get_tag_statistics": {
        "zh": {
            "source_name": "标签统计",
            "source_description": "汇总各标签下的记录数量与占比。",
        },
        "en": {
            "source_name": "Get Tag Statistics",
            "source_description": "Summarize entry counts and ratios for each tag.",
        },
    },
    "list_entry_types": {
        "zh": {
            "source_name": "列出记录类型",
            "source_description": "列出当前可用的记录类型。",
        },
        "en": {
            "source_name": "List Entry Types",
            "source_description": "List the currently available entry types.",
        },
    },
    "list_tags": {
        "zh": {
            "source_name": "列出标签",
            "source_description": "列出当前可用的标签。",
        },
        "en": {
            "source_name": "List Tags",
            "source_description": "List the currently available tags.",
        },
    },
    "kb_relation_recommendations": {
        "zh": {
            "source_name": "关系推荐",
            "source_description": "基于现有知识图谱推荐可能需要建立的记录关联。",
        },
        "en": {
            "source_name": "Relation Recommendations",
            "source_description": "Recommend likely entry relations from the current knowledge graph context.",
        },
    },
}


class ToolRegistry(_BaseRegistry):
    """工具注册表 - 解析系统本地工具和数据库自定义工具"""

    # 内部系统工具：默认不对外展示，但仍可在运行时被内部逻辑调用
    INTERNAL_TOOL_NAMES: frozenset[str] = frozenset({"kb_search"})
    SYSTEM_TOOL_OUTPUT_PARAMS: dict[str, list[dict[str, str]]] = {
        "search_entries": [
            {"name": "total", "param_type": "number", "description": "匹配记录总数。"},
            {
                "name": "items",
                "param_type": "array",
                "description": "记录列表。元素字段：id(string), title(string), content(string), type(string), type_code(string), summary(string), tags(array[string]), time_mode(string), time_at(string|null, ISO8601), time_from(string|null, ISO8601), time_to(string|null, ISO8601), created_at(string, ISO8601), updated_at(string, ISO8601)。",
            },
        ],
        "create_relation": [
            {"name": "id", "param_type": "string", "description": "关系 UUID。"},
            {"name": "source_entry_id", "param_type": "string", "description": "源记录 UUID。"},
            {"name": "source_entry_title", "param_type": "string", "description": "源记录标题。"},
            {"name": "target_entry_id", "param_type": "string", "description": "目标记录 UUID。"},
            {"name": "target_entry_title", "param_type": "string", "description": "目标记录标题。"},
            {"name": "relation_type_code", "param_type": "string", "description": "关系类型编码。"},
            {"name": "relation_type_name", "param_type": "string", "description": "关系类型名称。"},
            {"name": "description", "param_type": "string", "description": "关系说明。"},
            {"name": "created_at", "param_type": "string", "description": "创建时间（ISO8601）。"},
            {"name": "updated_at", "param_type": "string", "description": "更新时间（ISO8601）。"},
        ],
        "query_knowledge_graph": [
            {"name": "answer", "param_type": "string", "description": "综合回答。"},
            {"name": "sources", "param_type": "array", "description": "引用来源列表。"},
            {"name": "metadata", "param_type": "object", "description": "查询元信息。"},
        ],
        "generate_weekly_report": [
            {"name": "id", "param_type": "string", "description": "周报 UUID。"},
            {"name": "week_start", "param_type": "string", "description": "周起始日期。"},
            {"name": "week_end", "param_type": "string", "description": "周结束日期。"},
            {"name": "entry_count", "param_type": "number", "description": "记录数量。"},
            {"name": "content", "param_type": "object", "description": "周报内容对象。"},
            {"name": "content_locale", "param_type": "string", "description": "内容语言。"},
            {"name": "status", "param_type": "string", "description": "生成状态。"},
            {"name": "attempts", "param_type": "number", "description": "生成尝试次数。"},
            {"name": "last_error", "param_type": "string", "description": "最后一次错误。"},
            {"name": "generated_at", "param_type": "string", "description": "生成时间。"},
            {"name": "created_at", "param_type": "string", "description": "创建时间。"},
            {"name": "updated_at", "param_type": "string", "description": "更新时间。"},
        ],
        "generate_monthly_report": [
            {"name": "id", "param_type": "string", "description": "月报 UUID。"},
            {"name": "month_start", "param_type": "string", "description": "月起始日期。"},
            {"name": "month_end", "param_type": "string", "description": "月结束日期。"},
            {"name": "entry_count", "param_type": "number", "description": "记录数量。"},
            {"name": "content", "param_type": "object", "description": "月报内容对象。"},
            {"name": "content_locale", "param_type": "string", "description": "内容语言。"},
            {"name": "status", "param_type": "string", "description": "生成状态。"},
            {"name": "attempts", "param_type": "number", "description": "生成尝试次数。"},
            {"name": "last_error", "param_type": "string", "description": "最后一次错误。"},
            {"name": "generated_at", "param_type": "string", "description": "生成时间。"},
            {"name": "created_at", "param_type": "string", "description": "创建时间。"},
            {"name": "updated_at", "param_type": "string", "description": "更新时间。"},
        ],
        "openclaw_capture_entry": [
            {"name": "id", "param_type": "string", "description": "新建记录 UUID。"},
            {"name": "title", "param_type": "string", "description": "记录标题。"},
            {"name": "summary", "param_type": "string", "description": "记录摘要。"},
            {"name": "content", "param_type": "string", "description": "记录正文。"},
            {"name": "entryTypeCode", "param_type": "string", "description": "记录类型编码。"},
            {"name": "entryTypeName", "param_type": "string", "description": "记录类型名称。"},
            {"name": "tagNames", "param_type": "array", "description": "标签名称列表。"},
            {"name": "timeMode", "param_type": "string", "description": "时间模式。"},
            {"name": "timeAt", "param_type": "string", "description": "POINT 模式时间。"},
            {"name": "timeFrom", "param_type": "string", "description": "RANGE 起始时间。"},
            {"name": "timeTo", "param_type": "string", "description": "RANGE 结束时间。"},
            {"name": "createdAt", "param_type": "string", "description": "创建时间。"},
            {"name": "updatedAt", "param_type": "string", "description": "更新时间。"},
        ],
        "openclaw_search_entries": [
            {"name": "total", "param_type": "number", "description": "匹配记录总数。"},
            {"name": "items", "param_type": "array", "description": "匹配记录列表。"},
        ],
        "openclaw_get_entry": [
            {"name": "id", "param_type": "string", "description": "记录 UUID。"},
            {"name": "title", "param_type": "string", "description": "记录标题。"},
            {"name": "summary", "param_type": "string", "description": "记录摘要。"},
            {"name": "content", "param_type": "string", "description": "记录正文。"},
            {"name": "entryTypeCode", "param_type": "string", "description": "记录类型编码。"},
            {"name": "entryTypeName", "param_type": "string", "description": "记录类型名称。"},
            {"name": "tagNames", "param_type": "array", "description": "标签名称列表。"},
            {"name": "timeMode", "param_type": "string", "description": "时间模式。"},
            {"name": "timeAt", "param_type": "string", "description": "POINT 模式时间。"},
            {"name": "timeFrom", "param_type": "string", "description": "RANGE 起始时间。"},
            {"name": "timeTo", "param_type": "string", "description": "RANGE 结束时间。"},
            {"name": "createdAt", "param_type": "string", "description": "创建时间。"},
            {"name": "updatedAt", "param_type": "string", "description": "更新时间。"},
        ],
        "openclaw_create_relation": [
            {"name": "id", "param_type": "string", "description": "关系 UUID。"},
            {"name": "sourceEntryId", "param_type": "string", "description": "源记录 UUID。"},
            {"name": "sourceEntryTitle", "param_type": "string", "description": "源记录标题。"},
            {"name": "targetEntryId", "param_type": "string", "description": "目标记录 UUID。"},
            {"name": "targetEntryTitle", "param_type": "string", "description": "目标记录标题。"},
            {"name": "relationTypeCode", "param_type": "string", "description": "关系类型编码。"},
            {"name": "relationTypeName", "param_type": "string", "description": "关系类型名称。"},
            {"name": "description", "param_type": "string", "description": "关系说明。"},
        ],
        "openclaw_query_knowledge_graph": [
            {"name": "answer", "param_type": "string", "description": "知识图谱回答。"},
            {"name": "sources", "param_type": "array", "description": "引用来源列表。"},
            {"name": "metadata", "param_type": "object", "description": "查询元数据。"},
        ],
        "openclaw_generate_weekly_report": [
            {"name": "id", "param_type": "string", "description": "周报 UUID。"},
            {"name": "weekStart", "param_type": "string", "description": "周起始日期。"},
            {"name": "weekEnd", "param_type": "string", "description": "周结束日期。"},
            {"name": "entryCount", "param_type": "number", "description": "记录数量。"},
            {"name": "content", "param_type": "object", "description": "周报内容对象。"},
            {"name": "contentLocale", "param_type": "string", "description": "内容语言。"},
            {"name": "status", "param_type": "string", "description": "生成状态。"},
            {"name": "attempts", "param_type": "number", "description": "生成尝试次数。"},
            {"name": "lastError", "param_type": "string", "description": "最后一次错误。"},
            {"name": "generatedAt", "param_type": "string", "description": "生成时间。"},
            {"name": "createdAt", "param_type": "string", "description": "创建时间。"},
            {"name": "updatedAt", "param_type": "string", "description": "更新时间。"},
        ],
        "openclaw_generate_monthly_report": [
            {"name": "id", "param_type": "string", "description": "月报 UUID。"},
            {"name": "monthStart", "param_type": "string", "description": "月起始日期。"},
            {"name": "monthEnd", "param_type": "string", "description": "月结束日期。"},
            {"name": "entryCount", "param_type": "number", "description": "记录数量。"},
            {"name": "content", "param_type": "object", "description": "月报内容对象。"},
            {"name": "contentLocale", "param_type": "string", "description": "内容语言。"},
            {"name": "status", "param_type": "string", "description": "生成状态。"},
            {"name": "attempts", "param_type": "number", "description": "生成尝试次数。"},
            {"name": "lastError", "param_type": "string", "description": "最后一次错误。"},
            {"name": "generatedAt", "param_type": "string", "description": "生成时间。"},
            {"name": "createdAt", "param_type": "string", "description": "创建时间。"},
            {"name": "updatedAt", "param_type": "string", "description": "更新时间。"},
        ],
        "get_entry_detail": [
            {"name": "id", "param_type": "string", "description": "记录 UUID。"},
            {"name": "title", "param_type": "string", "description": "记录标题。"},
            {"name": "content", "param_type": "string", "description": "记录正文。"},
            {"name": "type", "param_type": "string", "description": "记录类型名称。"},
            {"name": "type_code", "param_type": "string", "description": "记录类型编码。"},
            {"name": "summary", "param_type": "string", "description": "记录摘要。"},
            {"name": "tags", "param_type": "array", "description": "标签名称数组。"},
            {"name": "time_mode", "param_type": "string", "description": "时间模式（POINT/RANGE）。"},
            {"name": "time_at", "param_type": "string", "description": "POINT 模式时间（ISO8601 或 null）。"},
            {"name": "time_from", "param_type": "string", "description": "RANGE 起始时间（ISO8601 或 null）。"},
            {"name": "time_to", "param_type": "string", "description": "RANGE 结束时间（ISO8601 或 null）。"},
            {"name": "created_at", "param_type": "string", "description": "创建时间（ISO8601）。"},
            {"name": "updated_at", "param_type": "string", "description": "更新时间（ISO8601）。"},
        ],
        "create_entry": [
            {"name": "id", "param_type": "string", "description": "新建记录 UUID。"},
            {"name": "title", "param_type": "string", "description": "最终写入的标题。"},
            {"name": "summary", "param_type": "string", "description": "最终写入的摘要。"},
            {"name": "type", "param_type": "string", "description": "记录类型名称。"},
            {"name": "type_code", "param_type": "string", "description": "记录类型编码。"},
            {"name": "tags", "param_type": "array", "description": "标签名称数组。"},
            {"name": "time_mode", "param_type": "string", "description": "时间模式（POINT/RANGE）。"},
            {"name": "time_at", "param_type": "string", "description": "POINT 模式日期（YYYY-MM-DD 或 null）。"},
            {"name": "time_from", "param_type": "string", "description": "RANGE 起始日期（YYYY-MM-DD 或 null）。"},
            {"name": "time_to", "param_type": "string", "description": "RANGE 结束日期（YYYY-MM-DD 或 null）。"},
            {"name": "created_at", "param_type": "string", "description": "创建时间（ISO8601）。"},
            {"name": "updated_at", "param_type": "string", "description": "更新时间（ISO8601）。"},
        ],
        "update_entry": [
            {"name": "id", "param_type": "string", "description": "更新记录 UUID。"},
            {"name": "title", "param_type": "string", "description": "最终写入的标题。"},
            {"name": "summary", "param_type": "string", "description": "最终写入的摘要。"},
            {"name": "type", "param_type": "string", "description": "记录类型名称。"},
            {"name": "type_code", "param_type": "string", "description": "记录类型编码。"},
            {"name": "tags", "param_type": "array", "description": "标签名称数组。"},
            {"name": "time_mode", "param_type": "string", "description": "时间模式（POINT/RANGE）。"},
            {"name": "time_at", "param_type": "string", "description": "POINT 模式日期（YYYY-MM-DD 或 null）。"},
            {"name": "time_from", "param_type": "string", "description": "RANGE 起始日期（YYYY-MM-DD 或 null）。"},
            {"name": "time_to", "param_type": "string", "description": "RANGE 结束日期（YYYY-MM-DD 或 null）。"},
            {"name": "created_at", "param_type": "string", "description": "创建时间（ISO8601）。"},
            {"name": "updated_at", "param_type": "string", "description": "更新时间（ISO8601）。"},
        ],
        "get_statistics": [
            {"name": "total_entries", "param_type": "number", "description": "记录总数。"},
            {"name": "total_tags", "param_type": "number", "description": "标签总数。"},
            {"name": "total_types", "param_type": "number", "description": "类型总数。"},
            {"name": "entries_by_type", "param_type": "object", "description": "按类型名称聚合计数。"},
            {"name": "entries_by_tag", "param_type": "object", "description": "按标签名称聚合计数。"},
        ],
        "get_entries_by_time_range": [
            {
                "name": "items",
                "param_type": "array",
                "description": "时间范围内记录列表。元素字段：id(string), title(string), type(string), summary(string), time_mode(string), time_at(string|null), time_from(string|null), time_to(string|null)。",
            },
        ],
        "analyze_activity": [
            {"name": "start_date", "param_type": "string", "description": "统计开始日期（YYYY-MM-DD）。"},
            {"name": "end_date", "param_type": "string", "description": "统计结束日期（YYYY-MM-DD）。"},
            {"name": "days", "param_type": "number", "description": "覆盖天数。"},
            {"name": "period", "param_type": "string", "description": "请求周期参数（week/month/year）。"},
            {"name": "entries_created", "param_type": "number", "description": "时间范围内创建记录数。"},
            {"name": "avg_per_day", "param_type": "number", "description": "日均创建量。"},
            {"name": "trend_unit", "param_type": "string", "description": "趋势粒度（day/month）。"},
            {
                "name": "trend",
                "param_type": "array",
                "description": "趋势点列表。元素字段：date(string), count(number)。",
            },
        ],
        "get_tag_statistics": [
            {"name": "total_tags", "param_type": "number", "description": "标签总数。"},
            {
                "name": "tags",
                "param_type": "array",
                "description": "标签统计列表。元素字段：id(string), name(string), color(string), entry_count(number)。",
            },
        ],
        "list_entry_types": [
            {
                "name": "items",
                "param_type": "array",
                "description": "类型列表。元素字段：id(string), code(string), type(string), name(string), color(string)。",
            },
        ],
        "list_tags": [
            {
                "name": "items",
                "param_type": "array",
                "description": "标签列表。元素字段：id(string), name(string), color(string), entry_count(number)。",
            },
        ],
        "kb_relation_recommendations": [
            {
                "name": "items",
                "param_type": "array",
                "description": "推荐关联列表。元素字段：targetEntryId(string), relationType(string|null), score(number)。",
            },
        ],
        "kb_search": [
            {"name": "mode", "param_type": "string", "description": "召回模式。"},
            {"name": "query", "param_type": "string", "description": "检索原始查询。"},
            {
                "name": "references",
                "param_type": "array",
                "description": "引用列表。元素字段至少包含 index(number), type(string) 以及各类型对应上下文字段。",
            },
        ],
    }

    @staticmethod
    def _resolve_display_locale(locale: str | None) -> str:
        from app.system_settings.service import get_default_system_locale, normalize_system_locale

        return normalize_system_locale(locale) or get_default_system_locale()

    @classmethod
    def get_system_tool_display(
        cls,
        tool_name: str,
        *,
        locale: str | None = None,
    ) -> SystemToolDisplayDefinition:
        normalized_locale = cls._resolve_display_locale(locale)
        localized = (
            _SYSTEM_TOOL_DISPLAY_METADATA.get(tool_name, {}).get(normalized_locale)
            or _SYSTEM_TOOL_DISPLAY_METADATA.get(tool_name, {}).get("zh")
            or _SYSTEM_TOOL_DISPLAY_METADATA.get(tool_name, {}).get("en")
            or {}
        )
        source_name = str(localized.get("source_name") or tool_name).strip() or tool_name
        source_description = str(localized.get("source_description") or "").strip() or None
        return SystemToolDisplayDefinition(
            display_name=source_name,
            display_description=source_description,
        )

    @staticmethod
    def list_system_tools(locale: str | None = None) -> list[SystemToolDefinition]:
        from app.assistant import tools as assistant_tools

        results: list[SystemToolDefinition] = []
        for tool_name in getattr(assistant_tools, "__all__", []):
            if tool_name in ToolRegistry.INTERNAL_TOOL_NAMES:
                continue
            tool_obj = getattr(assistant_tools, tool_name, None)
            description = ""
            if tool_obj is not None:
                description = (
                    getattr(tool_obj, "description", None)
                    or getattr(tool_obj, "__doc__", "")
                    or ""
                ).strip()
            display = ToolRegistry.get_system_tool_display(tool_name, locale=locale)
            results.append(
                SystemToolDefinition(
                    name=tool_name,
                    description=description,
                    display_name=display.display_name,
                    display_description=display.display_description,
                )
            )
        return results

    @staticmethod
    def list_system_tool_definitions(locale: str | None = None) -> list[SystemToolFullDefinition]:
        """从代码定义获取系统工具完整信息（名称/描述/参数签名/JSON Schema）。"""
        from app.assistant import tools as assistant_tools

        visible_tool_names = [
            tool_name
            for tool_name in getattr(assistant_tools, "__all__", [])
            if tool_name not in ToolRegistry.INTERNAL_TOOL_NAMES
        ]
        ToolRegistry._validate_system_tool_output_contracts(visible_tool_names)

        results: list[SystemToolFullDefinition] = []
        for tool_name in visible_tool_names:
            tool_obj = getattr(assistant_tools, tool_name, None)
            if tool_obj is None:
                continue

            description = (
                getattr(tool_obj, "description", None)
                or getattr(tool_obj, "__doc__", "")
                or ""
            ).strip()
            display = ToolRegistry.get_system_tool_display(tool_name, locale=locale)

            input_params, doc_returns, json_schema = ToolRegistry._extract_tool_params(tool_obj)
            output_params = ToolRegistry._extract_system_tool_output_params(tool_name)
            returns = ToolRegistry._format_output_contract(output_params) or doc_returns
            results.append(SystemToolFullDefinition(
                name=tool_name,
                description=description,
                display_name=display.display_name,
                display_description=display.display_description,
                input_params=input_params,
                output_params=output_params,
                returns=returns,
                json_schema=json_schema,
            ))
        return results

    @staticmethod
    def resolve_system_tool(tool_name: str) -> Any | None:
        from app.assistant import tools as assistant_tools
        return getattr(assistant_tools, tool_name, None)

    @classmethod
    def has_system_tool(cls, tool_name: str) -> bool:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return False
        if normalized in cls.INTERNAL_TOOL_NAMES:
            return True
        return cls.resolve_system_tool(normalized) is not None

    def list_db_tools(self, include_disabled: bool = False) -> list[AssistantTool]:
        """获取数据库中的工具配置。

        Args:
            include_disabled: 是否包含禁用工具。
        """
        query = self.db.query(AssistantTool)
        if not include_disabled:
            query = query.filter(AssistantTool.enabled.is_(True))
        return query.order_by(AssistantTool.created_at.desc()).all()

    def resolve(self, tool_name: str) -> Any | None:
        """解析工具 - 优先从数据库查找，正确处理禁用状态

        逻辑:
        1. 内部工具直接返回系统工具（绕过 DB 禁用）
        2. 先查询数据库中是否存在该工具（不管启用状态）
        3. 如果存在且被禁用，返回 None（不回退到系统工具）
        4. 如果存在且启用，返回对应工具
        5. 如果不存在，回退到系统工具
        """
        # 内部工具：绕过 DB 禁用逻辑
        if tool_name in self.INTERNAL_TOOL_NAMES:
            return self.resolve_system_tool(tool_name)

        # 先查询是否存在该工具（不过滤 enabled）
        record = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.name == tool_name)
            .first()
        )

        if record:
            # 工具存在于数据库
            if not record.enabled:
                # 工具被禁用，返回 None（不回退到系统工具）
                return None
            # 工具启用
            if (record.kind or "").lower() == "remote":
                return RemoteTool.from_model(record)
            return self.resolve_system_tool(tool_name)

        # 工具不在数据库中，回退到系统工具
        return self.resolve_system_tool(tool_name)

    # -------------------------
    # Tool signature extraction
    # -------------------------
    @staticmethod
    def _tool_schema_json(tool: Any) -> dict | None:
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is None:
            return None
        if hasattr(args_schema, "model_json_schema"):
            try:
                return args_schema.model_json_schema()
            except Exception:
                return None
        if hasattr(args_schema, "schema"):
            try:
                return args_schema.schema()
            except Exception:
                return None
        return None

    @staticmethod
    def _parse_docstring_args(doc: str) -> dict[str, str]:
        """解析 Google-style docstring 中的 Args 段。"""
        args: dict[str, str] = {}
        lines = (doc or "").splitlines()
        in_args = False
        current: str | None = None
        for raw in lines:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not in_args:
                if stripped == "Args:":
                    in_args = True
                continue
            if stripped in {"Returns:", "Raises:", "Note:", "Notes:", "注意:"}:
                break
            if not stripped:
                continue
            if ":" in stripped:
                name, desc = stripped.split(":", 1)
                name = name.strip()
                desc = desc.strip()
                if name and " " not in name:
                    args[name] = desc
                    current = name
                    continue
            if current:
                args[current] = (args[current] + " " + stripped).strip()
        return args

    @staticmethod
    def _parse_docstring_returns(doc: str) -> str | None:
        """解析 Google-style docstring 中的 Returns 段。"""
        lines = (doc or "").splitlines()
        in_returns = False
        result_lines: list[str] = []
        for raw in lines:
            stripped = raw.strip()
            if not in_returns:
                if stripped in {"Returns:", "返回:"}:
                    in_returns = True
                continue
            if stripped in {"Raises:", "Note:", "Notes:", "注意:", "Args:"}:
                break
            if stripped:
                result_lines.append(stripped)
        return "\n".join(result_lines) if result_lines else None

    @staticmethod
    def _param_type_from_json_schema(prop: dict) -> str:
        if not isinstance(prop, dict):
            return "string"
        t = prop.get("type")
        if isinstance(t, str):
            if t == "integer":
                return "number"
            if t in {"string", "number", "boolean", "array", "object"}:
                return t
        for key in ("anyOf", "oneOf"):
            items = prop.get(key)
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    if it.get("type") == "null":
                        continue
                    return ToolRegistry._param_type_from_json_schema(it)
        return "string"

    @staticmethod
    def _param_type_from_annotation(annotation: Any) -> str:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is list or origin is tuple or origin is set:
            return "array"
        if origin is dict:
            return "object"
        if args:
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                return ToolRegistry._param_type_from_annotation(non_none[0])
        if annotation in {int, float}:
            return "number"
        if annotation is bool:
            return "boolean"
        if annotation is dict:
            return "object"
        if annotation is list:
            return "array"
        return "string"

    @staticmethod
    def _extract_tool_params(tool: Any) -> tuple[list[SystemToolParamDefinition], str | None, dict | None]:
        """从 LangChain tool 对象中提取参数列表、返回值描述与 JSON Schema。"""
        schema_json = ToolRegistry._tool_schema_json(tool)
        args_schema = getattr(tool, "args_schema", None)

        doc = ""
        tool_func = getattr(tool, "func", None)
        if callable(tool_func):
            doc = inspect.getdoc(tool_func) or ""
        if not doc:
            doc = inspect.getdoc(tool) or ""
        doc_args = ToolRegistry._parse_docstring_args(doc)
        doc_returns = ToolRegistry._parse_docstring_returns(doc)

        # 优先从 JSON Schema 解析
        if isinstance(schema_json, dict):
            props = schema_json.get("properties") or {}
            required = set(schema_json.get("required") or [])
            ordered_names: list[str] = []
            if args_schema is not None:
                mf = getattr(args_schema, "model_fields", None)
                if isinstance(mf, dict):
                    ordered_names = list(mf.keys())
                else:
                    f = getattr(args_schema, "__fields__", None)
                    if isinstance(f, dict):
                        ordered_names = list(f.keys())
            if not ordered_names:
                ordered_names = list(props.keys())

            params: list[SystemToolParamDefinition] = []
            for name in ordered_names:
                prop = props.get(name) or {}
                desc = (prop.get("description") or "").strip() or doc_args.get(name) or None
                ptype = ToolRegistry._param_type_from_json_schema(prop)
                params.append(SystemToolParamDefinition(
                    name=name,
                    description=desc,
                    param_type=ptype,
                    required=name in required,
                ))
            return params, doc_returns, schema_json

        # fallback：函数签名
        if callable(tool_func):
            sig = inspect.signature(tool_func)
            params = []
            for p in sig.parameters.values():
                if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                if p.name in {"self", "cls"}:
                    continue
                req = (p.default is inspect.Parameter.empty)
                ptype = ToolRegistry._param_type_from_annotation(p.annotation)
                desc = doc_args.get(p.name) or None
                params.append(SystemToolParamDefinition(
                    name=p.name,
                    description=desc,
                    param_type=ptype,
                    required=req,
                ))
            return params, doc_returns, None

        return [], doc_returns, None

    @staticmethod
    def _extract_system_tool_output_params(tool_name: str) -> list[SystemToolOutputDefinition]:
        definitions = ToolRegistry.SYSTEM_TOOL_OUTPUT_PARAMS.get(tool_name, [])
        output_params: list[SystemToolOutputDefinition] = []
        for item in definitions:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            output_params.append(
                SystemToolOutputDefinition(
                    name=name,
                    description=(item.get("description") or None),
                    param_type=(item.get("param_type") or "string"),
                )
            )
        return output_params

    @staticmethod
    def _validate_system_tool_output_contracts(tool_names: list[str]) -> None:
        missing = [name for name in tool_names if name not in ToolRegistry.SYSTEM_TOOL_OUTPUT_PARAMS]
        empty = [
            name
            for name in tool_names
            if name in ToolRegistry.SYSTEM_TOOL_OUTPUT_PARAMS
            and not ToolRegistry.SYSTEM_TOOL_OUTPUT_PARAMS.get(name)
        ]
        if not missing and not empty:
            return

        details: list[str] = []
        if missing:
            details.append(f"missing={','.join(sorted(missing))}")
        if empty:
            details.append(f"empty={','.join(sorted(empty))}")
        raise RuntimeError(
            "System tool output contracts must be declared for every visible tool: " + "; ".join(details)
        )

    @staticmethod
    def _format_output_contract(output_params: list[SystemToolOutputDefinition]) -> str | None:
        if not output_params:
            return None
        lines: list[str] = []
        for p in output_params:
            line = f"- {p.name} ({p.param_type})"
            if p.description:
                line += f": {p.description}"
            lines.append(line)
        return "\n".join(lines)


class SkillRegistry(_BaseRegistry):
    """技能注册表 - 解析系统技能和数据库自定义技能"""

    @staticmethod
    def list_system_skills(locale: str | None = None) -> list[Any]:
        from app.assistant.skill_catalog.defaults_loader import load_system_skill_defaults

        return list(load_system_skill_defaults(locale=locale))

    @staticmethod
    def list_system_skill_definitions(locale: str | None = None) -> list[SystemSkillFullDefinition]:
        """获取系统 Skill 元数据定义。"""
        return [
            SkillRegistry._to_skill_full_definition(skill, include_workflow=True)
            for skill in SkillRegistry.list_system_skills(locale=locale)
        ]

    @staticmethod
    def resolve_system_skill(skill_name: str, locale: str | None = None) -> Any | None:
        """解析系统 Skill。"""
        from app.assistant.skill_catalog.definitions import get_skill_by_name

        return get_skill_by_name(skill_name, locale=locale)

    def list_db_skills(
        self,
        include_workflow: bool = False,
        include_disabled: bool = False,
    ) -> list[AssistantSkill]:
        """获取数据库 Skills。

        Args:
            include_workflow: 是否预加载 workflow nodes/edges。
            include_disabled: 是否包含禁用技能。
        """
        query = self.db.query(AssistantSkill)
        if not include_disabled:
            query = query.filter(AssistantSkill.enabled.is_(True))
        if include_workflow:
            query = query.options(
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.nodes),
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.edges),
                joinedload(AssistantSkill.agent_profile),
                joinedload(AssistantSkill.nodes),
                joinedload(AssistantSkill.edges),
            )
        return query.order_by(AssistantSkill.created_at.desc()).all()

    def list_db_skill_definitions(
        self,
        include_workflow: bool = False,
        include_disabled: bool = False,
    ) -> list[SystemSkillFullDefinition]:
        """获取数据库 Skill 元数据定义。"""
        skills = self.list_db_skills(
            include_workflow=include_workflow,
            include_disabled=include_disabled,
        )
        return [
            self._to_skill_full_definition(skill, include_workflow=include_workflow)
            for skill in skills
        ]

    def list_enabled_db_skills(self, include_workflow: bool = False) -> list[AssistantSkill]:
        """获取启用的数据库 Skills。"""
        return self.list_db_skills(include_workflow=include_workflow, include_disabled=False)

    def resolve(
        self,
        skill_name: str,
        include_workflow: bool = True,
        *,
        locale: str | None = None,
    ) -> Any | None:
        """解析 Skill - 优先从数据库查找，未命中时回退到系统定义。

        逻辑:
        1. 查询数据库中是否存在该 Skill（不管启用状态）
        2. 如果存在且被禁用，返回 None（不回退到系统 Skill）
           例外：默认 Skill (general_chat) 不可被禁用，始终回退到系统定义
        3. 如果存在且启用，返回对应 DB SkillDefinition
        4. 如果不存在，回退到系统 Skill

        Args:
            skill_name: Skill 名称。
            include_workflow: 是否加载 workflow nodes/edges 并进行完整转换。
        """
        from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME

        query = self.db.query(AssistantSkill)
        if include_workflow:
            query = query.options(
                # Avoid joined-loading multiple collection relationships in one query,
                # which can create large cartesian result sets and stall skill execution.
                joinedload(AssistantSkill.workflow).selectinload(AssistantWorkflow.nodes),
                joinedload(AssistantSkill.workflow).selectinload(AssistantWorkflow.edges),
                joinedload(AssistantSkill.agent_profile),
                selectinload(AssistantSkill.nodes),
                selectinload(AssistantSkill.edges),
            )
        record = query.filter(AssistantSkill.name == skill_name).first()

        if record:
            if not record.enabled:
                # 默认 Skill 不可被禁用，回退到系统定义
                if skill_name == DEFAULT_SKILL_NAME:
                    return self.resolve_system_skill(skill_name, locale=locale)
                return None

            from app.assistant.skill_catalog.converters import (
                db_skill_to_definition,
                db_skill_to_definition_light,
            )

            if include_workflow:
                try:
                    return db_skill_to_definition(record)
                except ValueError as exc:
                    raise ValueError(
                        f"Skill '{record.name}' has an invalid workflow definition: {exc}"
                    ) from exc
            return db_skill_to_definition_light(record)

        return self.resolve_system_skill(skill_name, locale=locale)

    def get_skill_by_name(
        self,
        skill_name: str,
        include_workflow: bool = True,
        *,
        locale: str | None = None,
    ) -> Any | None:
        """按名称获取 Skill（先查 DB，再回退系统定义）。

        Args:
            skill_name: Skill 名称。
            include_workflow: 解析 DB Skill 时是否加载 workflow nodes/edges。
        """
        return self.resolve(skill_name=skill_name, include_workflow=include_workflow, locale=locale)

    @staticmethod
    def _to_skill_full_definition(skill: Any, *, include_workflow: bool) -> SystemSkillFullDefinition:
        raw_intent_examples = getattr(skill, "intent_examples", None)
        if not isinstance(raw_intent_examples, list):
            raw_intent_examples = []

        target_pattern = (
            "workflow_dag"
            if getattr(skill, "workflow_id", None) is not None
            else "agent_loop"
            if getattr(skill, "agent_profile_id", None) is not None
            else getattr(skill, "langgraph_pattern", None)
        )
        raw_tools = getattr(skill, "tools", None)
        if (
            target_pattern == "agent_loop"
            and getattr(skill, "agent_profile", None) is not None
            and isinstance(getattr(skill.agent_profile, "tools", None), list)
        ):
            raw_tools = skill.agent_profile.tools
        if not isinstance(raw_tools, list):
            raw_tools = []

        raw_kb = getattr(skill, "kb_config", None)
        if (
            target_pattern == "agent_loop"
            and getattr(skill, "agent_profile", None) is not None
            and isinstance(getattr(skill.agent_profile, "kb_config", None), dict)
        ):
            raw_kb = skill.agent_profile.kb_config
        kb_config = raw_kb if isinstance(raw_kb, dict) else None
        if kb_config is None:
            kb = getattr(skill, "kb", None)
            if kb is not None:
                kb_config = {"enabled": bool(getattr(kb, "enabled", False))}

        # Workflow DAG data
        workflow_nodes: list[dict[str, Any]] | None = None
        workflow_edges: list[dict[str, Any]] | None = None
        if include_workflow:
            raw_nodes = getattr(skill, "nodes", None) or []
            raw_edges = getattr(skill, "edges", None) or []
            if getattr(skill, "workflow", None) is not None:
                raw_nodes = getattr(skill.workflow, "nodes", None) or raw_nodes
                raw_edges = getattr(skill.workflow, "edges", None) or raw_edges
            if raw_nodes:
                workflow_nodes = [
                    SkillRegistry._serialize_workflow_node(n) for n in raw_nodes
                ]
            if raw_edges:
                workflow_edges = [
                    SkillRegistry._serialize_workflow_edge(e) for e in raw_edges
                ]

        return SystemSkillFullDefinition(
            name=getattr(skill, "name", ""),
            description=(getattr(skill, "description", "") or "").strip(),
            intent_examples=[str(item) for item in raw_intent_examples if item is not None],
            tools=[str(item) for item in raw_tools if item is not None],
            mode=(getattr(skill, "mode", "langgraph") or "langgraph"),
            langgraph_pattern=target_pattern,
            system_prompt=(
                getattr(skill.agent_profile, "system_prompt", None)
                if target_pattern == "agent_loop" and getattr(skill, "agent_profile", None) is not None
                else getattr(skill, "system_prompt", None)
            ),
            kb_config=kb_config,
            hidden=bool(getattr(skill, "hidden", False)),
            workflow_nodes=workflow_nodes,
            workflow_edges=workflow_edges,
            workflow_version=(
                getattr(skill.workflow, "workflow_version", None)
                if getattr(skill, "workflow", None) is not None
                else getattr(skill, "workflow_version", 1)
            ) or 1,
            workflow_viewport=(
                getattr(skill.workflow, "workflow_viewport", None)
                if getattr(skill, "workflow", None) is not None
                else getattr(skill, "workflow_viewport", None)
            ),
        )

    @staticmethod
    def _serialize_workflow_node(node: Any) -> dict[str, Any]:
        return {
            "node_id": getattr(node, "node_id", ""),
            "node_type": getattr(node, "node_type", ""),
            "label": getattr(node, "label", ""),
            "position_x": getattr(node, "position_x", 0.0),
            "position_y": getattr(node, "position_y", 0.0),
            "config": getattr(node, "config", None) or {},
        }

    @staticmethod
    def _serialize_workflow_edge(edge: Any) -> dict[str, Any]:
        return {
            "edge_id": getattr(edge, "edge_id", ""),
            "source_node_id": getattr(edge, "source_node_id", ""),
            "target_node_id": getattr(edge, "target_node_id", ""),
            "source_handle": getattr(edge, "source_handle", "output"),
            "target_handle": getattr(edge, "target_handle", "input"),
            "condition_type": getattr(edge, "condition_type", None),
            "condition_expr": getattr(edge, "condition_expr", None),
            "label": getattr(edge, "label", None),
        }

    @staticmethod
    def _normalize_output_fields(raw: Any) -> list[dict[str, Any]] | list[str] | None:
        if not isinstance(raw, list):
            return None

        normalized: list[dict[str, Any]] | list[str] = []
        for item in raw:
            if isinstance(item, dict):
                normalized.append(item)
                continue
            if isinstance(item, str):
                normalized.append(item)
                continue
            if hasattr(item, "model_dump"):
                try:
                    normalized.append(item.model_dump())
                except Exception:
                    continue
        return normalized or None
