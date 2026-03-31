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
    "search_entries": "openclaw_search_entries",
    "get_entry": "openclaw_get_entry",
    "create_relation": "openclaw_create_relation",
    "query_knowledge_graph": "openclaw_query_knowledge_graph",
    "generate_weekly_report": "openclaw_generate_weekly_report",
    "generate_monthly_report": "openclaw_generate_monthly_report",
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
        title=_LocalizedText(zh="检索记录", en="Search Entries"),
        description=_LocalizedText(
            zh="按关键词、类型、标签或时间范围检索 MindAtlas 记录。",
            en="Search MindAtlas entries by keyword, type, tags, or time range.",
        ),
        input_summary=_LocalizedText(
            zh="关键词，以及可选的类型、标签、时间范围和结果数量。",
            en="Keyword plus optional type, tags, time range, and result limit.",
        ),
        output_summary=_LocalizedText(
            zh="返回匹配记录列表与总数。",
            en="Returns matching entries and the total count.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="get_entry",
        tool_name="mindatlas_get_entry",
        enabled_by_default=True,
        implementation_type="entry",
        title=_LocalizedText(zh="查看记录详情", en="Get Entry"),
        description=_LocalizedText(
            zh="读取指定 MindAtlas 记录的详细内容。",
            en="Read the detailed content of a specific MindAtlas entry.",
        ),
        input_summary=_LocalizedText(zh="记录 ID。", en="Entry ID."),
        output_summary=_LocalizedText(
            zh="返回该记录的完整核心字段。",
            en="Returns the complete core fields of the entry.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="create_relation",
        tool_name="mindatlas_create_relation",
        enabled_by_default=True,
        implementation_type="relation",
        title=_LocalizedText(zh="创建关联", en="Create Relation"),
        description=_LocalizedText(
            zh="在两条 MindAtlas 记录之间建立一条关系。",
            en="Create a relation between two MindAtlas entries.",
        ),
        input_summary=_LocalizedText(
            zh="源记录、目标记录、关系类型，以及可选说明。",
            en="Source entry, target entry, relation type, and optional description.",
        ),
        output_summary=_LocalizedText(
            zh="返回新建关系的核心字段。",
            en="Returns the core fields of the created relation.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="query_knowledge_graph",
        tool_name="mindatlas_query_knowledge_graph",
        enabled_by_default=True,
        implementation_type="knowledge_graph",
        title=_LocalizedText(zh="查询知识图谱", en="Query Knowledge Graph"),
        description=_LocalizedText(
            zh="通过 LightRAG 查询 MindAtlas 的知识图谱和检索上下文。",
            en="Query the MindAtlas knowledge graph and retrieval context through LightRAG.",
        ),
        input_summary=_LocalizedText(
            zh="问题文本，以及可选的查询模式和 topK。",
            en="Question text plus optional query mode and topK.",
        ),
        output_summary=_LocalizedText(
            zh="返回回答、引用来源和查询元数据。",
            en="Returns the answer, cited sources, and query metadata.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="generate_weekly_report",
        tool_name="mindatlas_generate_weekly_report",
        enabled_by_default=True,
        implementation_type="report",
        title=_LocalizedText(zh="生成周报", en="Generate Weekly Report"),
        description=_LocalizedText(
            zh="生成或返回指定周的 MindAtlas 周报。",
            en="Generate or return the MindAtlas weekly report for a given week.",
        ),
        input_summary=_LocalizedText(
            zh="可选周起始日期，以及是否强制重新生成。",
            en="Optional week start date and whether to force regeneration.",
        ),
        output_summary=_LocalizedText(
            zh="返回周报状态、周期和生成内容。",
            en="Returns weekly report status, period, and generated content.",
        ),
    ),
    _ToolSystemItemTemplate(
        key="generate_monthly_report",
        tool_name="mindatlas_generate_monthly_report",
        enabled_by_default=True,
        implementation_type="report",
        title=_LocalizedText(zh="生成月报", en="Generate Monthly Report"),
        description=_LocalizedText(
            zh="生成或返回指定月份的 MindAtlas 月报。",
            en="Generate or return the MindAtlas monthly report for a given month.",
        ),
        input_summary=_LocalizedText(
            zh="可选月起始日期，以及是否强制重新生成。",
            en="Optional month start date and whether to force regeneration.",
        ),
        output_summary=_LocalizedText(
            zh="返回月报状态、周期和生成内容。",
            en="Returns monthly report status, period, and generated content.",
        ),
    ),
)

_WORKFLOW_SYSTEM_ITEM_TEMPLATES: tuple[_WorkflowSystemItemTemplate, ...] = (
    _WorkflowSystemItemTemplate(
        key=OPENCLAW_CONTEXT_CAPTURE_DEFAULT_KEY,
        tool_name="mindatlas_submit_context_capture",
        enabled_by_default=True,
        title=_LocalizedText(zh="智能创建记录", en="Smart Create Entry"),
        description=_LocalizedText(
            zh="向 MindAtlas 提交轻量上下文，由系统工作流自动物化最终记录字段并完成智能入库。",
            en="Submit thin context to MindAtlas so a system workflow can materialize the final entry fields and save the record intelligently.",
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
