from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from app.openclaw_integration.schemas import (
    OPENCLAW_SYSTEM_CAPABILITY_INPUT_MODELS,
    OPENCLAW_SYSTEM_CAPABILITY_OUTPUT_MODELS,
    OpenClawSystemCapabilityKey,
    OpenClawSystemDefaultKey,
)
from app.system_settings.service import get_default_system_locale, normalize_system_locale

OpenClawSystemItemSourceType = Literal["tool", "workflow", "agent"]
OpenClawSystemImplementationType = Literal["entry", "relation", "knowledge_graph", "report", "workflow", "agent"]

OPENCLAW_CONTEXT_CAPTURE_DEFAULT_KEY: OpenClawSystemDefaultKey = "submit_context_capture"
OPENCLAW_CONTEXT_CAPTURE_WORKFLOW_NAME = "system_openclaw_context_capture__workflow"
OPENCLAW_SYSTEM_DEFAULT_TOOL_SOURCE_NAMES: dict[OpenClawSystemCapabilityKey, str] = {
    "search_entries": "search_entries",
    "get_entry": "get_entry_detail",
    "create_relation": "create_relation",
    "query_knowledge_graph": "query_knowledge_graph",
    "generate_weekly_report": "generate_weekly_report",
    "generate_monthly_report": "generate_monthly_report",
}


@dataclass(frozen=True)
class _LocalizedText:
    zh: str
    en: str

    def resolve(self, locale: str) -> str:
        return self.zh if locale == "zh" else self.en


@dataclass(frozen=True)
class _ToolSystemItemTemplate:
    key: OpenClawSystemCapabilityKey
    tool_name: str
    enabled_by_default: bool
    implementation_type: OpenClawSystemImplementationType
    title: _LocalizedText
    description: _LocalizedText
    input_summary: _LocalizedText
    output_summary: _LocalizedText


@dataclass(frozen=True)
class _WorkflowSystemItemTemplate:
    key: OpenClawSystemDefaultKey
    tool_name: str
    enabled_by_default: bool
    title: _LocalizedText
    description: _LocalizedText
    workflow_canonical_name: str
    workflow_preset_file_zh: str
    workflow_preset_file_en: str
    input_summary: _LocalizedText | None = None
    output_summary: _LocalizedText | None = None


@dataclass(frozen=True)
class OpenClawSystemItemDefinition:
    key: OpenClawSystemDefaultKey
    source_type: OpenClawSystemItemSourceType
    tool_name: str
    enabled_by_default: bool
    implementation_type: OpenClawSystemImplementationType
    title: str
    description: str
    source_tool_name: str | None = None
    workflow_canonical_name: str | None = None
    workflow_preset_file: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


_TOOL_SYSTEM_ITEM_TEMPLATES: tuple[_ToolSystemItemTemplate, ...] = (
    _ToolSystemItemTemplate(
        key="search_entries",
        tool_name="mindatlas_search_entries",
        enabled_by_default=True,
        implementation_type="entry",
        title=_LocalizedText(zh="检索历史记录", en="Search Previous Records"),
        description=_LocalizedText(
            zh="在 MindAtlas 中搜索之前保存的记录，适合“我记过吗”“帮我搜一下”“最近记录了什么”这类按关键词、标签、类型或时间范围检索的问题。",
            en="Search previously stored MindAtlas records for requests like 'did I record this before', 'search what I saved', or recent and time-bounded lookups by keyword, tags, type, or time range.",
        ),
        input_summary=_LocalizedText(
            zh="关键词，以及可选的类型编码、标签、时间范围和结果数量。推荐省略或传 null 表示不筛选；空字符串目前也兼容为空值，但不是推荐写法。entryType 优先传当前启用的 type code，名称仅作兼容；query 中的 '.' 或 '*' 不表示查询全部。timeFrom/timeTo 推荐 YYYY-MM-DD，也兼容完整 ISO 8601 时间。",
            en="Keyword plus optional type code, tags, time range, and result limit. Prefer omitting fields or passing null for no filter; empty strings are still accepted as compatibility inputs but are not the recommended contract. Prefer current enabled type codes for entryType; names remain compatibility-only. '.' and '*' in query are literal keywords, not match-all syntax. Prefer YYYY-MM-DD for timeFrom/timeTo, though full ISO 8601 datetimes are also accepted.",
        ),
        output_summary=_LocalizedText(
            zh="返回匹配记录列表与总数，便于回答是否记过、列出最近记录，或继续查看详情。",
            en="Returns matching entries and the total count so the agent can confirm whether something was recorded, list recent records, or continue to exact detail lookup.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="get_entry",
        tool_name="mindatlas_get_entry",
        enabled_by_default=True,
        implementation_type="entry",
        title=_LocalizedText(zh="查看记录详情", en="Get Entry"),
        description=_LocalizedText(
            zh="读取某条已命中的 MindAtlas 记录详情，适合已知记录 ID 或需要展开搜索结果中的某一条记录。",
            en="Read the exact details of a specific MindAtlas record when the entry ID is known or a search hit needs to be expanded.",
        ),
        input_summary=_LocalizedText(
            zh="记录 ID（entryId），通常来自上一轮检索结果。这是规范输入；携带 id 的搜索结果对象目前也兼容，但不作为主契约推荐。",
            en="Entry ID (`entryId`), usually from a previous search result. This is the canonical input. A search-hit object that includes id may still work for compatibility, but it is not the primary contract.",
        ),
        output_summary=_LocalizedText(
            zh="返回该记录的完整核心字段，便于继续总结、关联、核对或引用。",
            en="Returns the complete core fields of the entry for follow-up summary, relation work, verification, or quoting.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="create_relation",
        tool_name="mindatlas_create_relation",
        enabled_by_default=True,
        implementation_type="relation",
        title=_LocalizedText(zh="关联两条记录", en="Create Relation"),
        description=_LocalizedText(
            zh="在两条 MindAtlas 记录之间建立关系，适合“关联一下”“连接起来”“说明这两条为什么相关”这类任务。",
            en="Create a relation between two MindAtlas records for requests like connect these items, link them, or make their relationship explicit.",
        ),
        input_summary=_LocalizedText(
            zh="源记录、目标记录、关系类型编码，以及可选说明。relationType 优先传当前启用的关系 type code；名称仅作兼容输入。",
            en="Source entry, target entry, relation type code, and an optional description. Prefer current enabled relation type codes for relationType; names remain compatibility-only.",
        ),
        output_summary=_LocalizedText(
            zh="返回新建关系的核心字段，便于后续检查、展示或继续图谱分析。",
            en="Returns the core fields of the created relation for follow-up inspection, presentation, or graph analysis.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="query_knowledge_graph",
        tool_name="mindatlas_query_knowledge_graph",
        enabled_by_default=True,
        implementation_type="knowledge_graph",
        title=_LocalizedText(zh="查询跨记录关系", en="Query Knowledge Graph"),
        description=_LocalizedText(
            zh="通过 LightRAG 查询 MindAtlas 的跨记录关系、模式和检索上下文，适合“这些记录有什么关系”“为什么相关”“能综合回答吗”这类问题。",
            en="Query MindAtlas cross-record relationships, patterns, and retrieval context through LightRAG for questions like what is related, why items are related, or what answer emerges across records.",
        ),
        input_summary=_LocalizedText(
            zh="问题文本，以及可选的查询模式和 topK；适合关系、模式和综合知识问答。",
            en="Question text plus optional query mode and topK for relation, pattern, and synthesized knowledge questions.",
        ),
        output_summary=_LocalizedText(
            zh="返回综合回答、引用来源和查询元数据。",
            en="Returns a synthesized answer, cited sources, and query metadata.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="generate_weekly_report",
        tool_name="mindatlas_generate_weekly_report",
        enabled_by_default=True,
        implementation_type="report",
        title=_LocalizedText(zh="生成本周/上周回顾", en="Generate Weekly Report"),
        description=_LocalizedText(
            zh="生成或返回指定周的 MindAtlas 周报，适合“我最近一周干了啥”“上周我做了什么”“帮我做个周回顾”这类问题。",
            en="Generate or return the MindAtlas weekly report for requests like what did I do this week, what did I do last week, or help me make a weekly recap.",
        ),
        input_summary=_LocalizedText(
            zh="可选 weekStart（YYYY-MM-DD）以及是否强制重新生成。推荐省略或传 null 走默认周；空字符串目前也兼容为未提供，但不是推荐写法。",
            en="Optional weekStart (YYYY-MM-DD) and whether to force regeneration. Prefer omitting it or passing null for the default week; an empty string is still accepted as a compatibility input but is not the recommended contract.",
        ),
        output_summary=_LocalizedText(
            zh="返回周报状态、周期和结构化内容，便于输出 recap、digest 或工作回顾。",
            en="Returns weekly report status, period, and structured content for recap, digest, or review output.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="generate_monthly_report",
        tool_name="mindatlas_generate_monthly_report",
        enabled_by_default=True,
        implementation_type="report",
        title=_LocalizedText(zh="生成本月回顾", en="Generate Monthly Report"),
        description=_LocalizedText(
            zh="生成或返回指定月份的 MindAtlas 月报，适合“这个月我做了什么”“帮我做个月回顾”“给我一份月度 digest”这类问题。",
            en="Generate or return the MindAtlas monthly report for requests like what did I do this month, help me make a monthly recap, or give me a monthly digest.",
        ),
        input_summary=_LocalizedText(
            zh="可选 monthStart（YYYY-MM-DD）以及是否强制重新生成。推荐省略或传 null 走默认月份；空字符串目前也兼容为未提供，但不是推荐写法。",
            en="Optional monthStart (YYYY-MM-DD) and whether to force regeneration. Prefer omitting it or passing null for the default month; an empty string is still accepted as a compatibility input but is not the recommended contract.",
        ),
        output_summary=_LocalizedText(
            zh="返回月报状态、周期和结构化内容，便于输出 recap、digest 或月度回顾。",
            en="Returns monthly report status, period, and structured content for recap, digest, or monthly review output.",
        ),
    ),
)

_WORKFLOW_SYSTEM_ITEM_TEMPLATES: tuple[_WorkflowSystemItemTemplate, ...] = (
    _WorkflowSystemItemTemplate(
        key=OPENCLAW_CONTEXT_CAPTURE_DEFAULT_KEY,
        tool_name="mindatlas_submit_context_capture",
        enabled_by_default=True,
        title=_LocalizedText(zh="智能记住并入库", en="Smart Save To MindAtlas"),
        description=_LocalizedText(
            zh="向 MindAtlas 提交一段高价值上下文，由系统工作流自动提取最终字段、判定是否应合并到已有记录，并结合 OpenClaw 请求元数据完成智能入库。",
            en="Submit one high-value context block to MindAtlas so the system workflow can extract final fields, decide whether to merge into an existing entry, and save it intelligently with OpenClaw request metadata.",
        ),
        input_summary=_LocalizedText(
            zh="只需提供 context（一段高价值上下文），其中尽量包含发生了什么、结果是什么、为什么值得记，以及明确的时间线索；source/channel/session/tool 等上下文由 OpenClaw 请求元数据自动提供。",
            en="Provide only `context`: one high-value context block that ideally includes what happened, the result, why it matters later, and any clear time clues. Source/channel/session/tool context is provided automatically from OpenClaw request metadata.",
        ),
        output_summary=_LocalizedText(
            zh="返回统一的 created/merged 结果，包含记录 ID、标题、类型、摘要、标签、时间字段，以及创建/更新时间。",
            en="Returns a unified created-or-merged result with the entry id, title, type, summary, tags, time fields, and created/updated timestamps.",
        ),
        workflow_canonical_name=OPENCLAW_CONTEXT_CAPTURE_WORKFLOW_NAME,
        workflow_preset_file_zh="workflows/openclaw_context_capture.json",
        workflow_preset_file_en="workflows/openclaw_context_capture.en.json",
    ),
)


def _normalize_registry_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


def _model_schema(model_cls: type[Any]) -> dict[str, Any]:
    return model_cls.model_json_schema(by_alias=True)


@lru_cache(maxsize=4)
def _system_item_registry(locale: str) -> dict[OpenClawSystemDefaultKey, OpenClawSystemItemDefinition]:
    definitions: dict[OpenClawSystemDefaultKey, OpenClawSystemItemDefinition] = {}

    for workflow_template in _WORKFLOW_SYSTEM_ITEM_TEMPLATES:
        definitions[workflow_template.key] = OpenClawSystemItemDefinition(
            key=workflow_template.key,
            source_type="workflow",
            tool_name=workflow_template.tool_name,
            enabled_by_default=workflow_template.enabled_by_default,
            implementation_type="workflow",
            title=workflow_template.title.resolve(locale),
            description=workflow_template.description.resolve(locale),
            input_summary=workflow_template.input_summary.resolve(locale) if workflow_template.input_summary else None,
            output_summary=workflow_template.output_summary.resolve(locale) if workflow_template.output_summary else None,
            workflow_canonical_name=workflow_template.workflow_canonical_name,
            workflow_preset_file=(
                workflow_template.workflow_preset_file_zh
                if locale == "zh"
                else workflow_template.workflow_preset_file_en
            ),
        )

    for tool_template in _TOOL_SYSTEM_ITEM_TEMPLATES:
        definitions[tool_template.key] = OpenClawSystemItemDefinition(
            key=tool_template.key,
            source_type="tool",
            tool_name=tool_template.tool_name,
            enabled_by_default=tool_template.enabled_by_default,
            implementation_type=tool_template.implementation_type,
            title=tool_template.title.resolve(locale),
            description=tool_template.description.resolve(locale),
            source_tool_name=OPENCLAW_SYSTEM_DEFAULT_TOOL_SOURCE_NAMES[tool_template.key],
            input_summary=tool_template.input_summary.resolve(locale),
            output_summary=tool_template.output_summary.resolve(locale),
            input_schema=_model_schema(OPENCLAW_SYSTEM_CAPABILITY_INPUT_MODELS[tool_template.key]),
            output_schema=_model_schema(OPENCLAW_SYSTEM_CAPABILITY_OUTPUT_MODELS[tool_template.key]),
        )

    return definitions


def list_openclaw_system_item_definitions(
    locale: str | None = None,
) -> list[OpenClawSystemItemDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    return list(_system_item_registry(normalized_locale).values())


def get_openclaw_system_item_definition(
    key: str,
    locale: str | None = None,
) -> OpenClawSystemItemDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    return _system_item_registry(normalized_locale).get(key)  # type: ignore[arg-type]


def get_openclaw_system_item_definition_by_source_tool_name(
    source_tool_name: str,
    locale: str | None = None,
) -> OpenClawSystemItemDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    for definition in _system_item_registry(normalized_locale).values():
        if definition.source_type == "tool" and definition.source_tool_name == source_tool_name:
            return definition
    return None


def clear_openclaw_integration_registry_cache() -> None:
    _system_item_registry.cache_clear()
