from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from app.openclaw_integration.schemas import (
    OPENCLAW_SYSTEM_CAPABILITY_INPUT_MODELS,
    OPENCLAW_SYSTEM_CAPABILITY_OUTPUT_MODELS,
    OpenClawSystemCapabilityKey,
)
from app.system_settings.service import get_default_system_locale, normalize_system_locale

OpenClawSystemImplementationType = Literal["entry", "relation", "knowledge_graph", "report"]


@dataclass(frozen=True)
class _LocalizedText:
    zh: str
    en: str

    def resolve(self, locale: str) -> str:
        return self.zh if locale == "zh" else self.en


@dataclass(frozen=True)
class _SystemCapabilityTemplate:
    key: OpenClawSystemCapabilityKey
    tool_name: str
    enabled_by_default: bool
    implementation_type: OpenClawSystemImplementationType
    title: _LocalizedText
    description: _LocalizedText
    input_summary: _LocalizedText
    output_summary: _LocalizedText


@dataclass(frozen=True)
class OpenClawSystemCapabilityDefinition:
    key: OpenClawSystemCapabilityKey
    tool_name: str
    enabled_by_default: bool
    implementation_type: OpenClawSystemImplementationType
    title: str
    description: str
    input_summary: str
    output_summary: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


_SYSTEM_CAPABILITY_TEMPLATES: tuple[_SystemCapabilityTemplate, ...] = (
    _SystemCapabilityTemplate(
        key="capture_entry",
        tool_name="mindatlas_capture_entry",
        enabled_by_default=True,
        implementation_type="entry",
        title=_LocalizedText(zh="记录内容", en="Capture Entry"),
        description=_LocalizedText(
            zh="向 MindAtlas 新增一条记录，可附带类型、标签和时间信息。",
            en="Create a new MindAtlas entry with type, tags, and time information.",
        ),
        input_summary=_LocalizedText(
            zh="标题、类型，以及可选的摘要、正文、标签和时间。",
            en="Title, entry type, plus optional summary, content, tags, and time.",
        ),
        output_summary=_LocalizedText(
            zh="返回新建记录的核心字段。",
            en="Returns the core fields of the created entry.",
        ),
    ),
    _SystemCapabilityTemplate(
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
    _SystemCapabilityTemplate(
        key="get_entry",
        tool_name="mindatlas_get_entry",
        enabled_by_default=True,
        implementation_type="entry",
        title=_LocalizedText(zh="查看记录详情", en="Get Entry"),
        description=_LocalizedText(
            zh="读取指定 MindAtlas 记录的详细内容。",
            en="Read the detailed content of a specific MindAtlas entry.",
        ),
        input_summary=_LocalizedText(
            zh="记录 ID。",
            en="Entry ID.",
        ),
        output_summary=_LocalizedText(
            zh="返回该记录的完整核心字段。",
            en="Returns the complete core fields of the entry.",
        ),
    ),
    _SystemCapabilityTemplate(
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
    _SystemCapabilityTemplate(
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
    _SystemCapabilityTemplate(
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
    _SystemCapabilityTemplate(
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


def _normalize_registry_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


def _model_schema(model_cls: type[Any]) -> dict[str, Any]:
    return model_cls.model_json_schema(by_alias=True)


@lru_cache(maxsize=4)
def _registry(locale: str) -> dict[OpenClawSystemCapabilityKey, OpenClawSystemCapabilityDefinition]:
    definitions: dict[OpenClawSystemCapabilityKey, OpenClawSystemCapabilityDefinition] = {}
    for template in _SYSTEM_CAPABILITY_TEMPLATES:
        definitions[template.key] = OpenClawSystemCapabilityDefinition(
            key=template.key,
            tool_name=template.tool_name,
            enabled_by_default=template.enabled_by_default,
            implementation_type=template.implementation_type,
            title=template.title.resolve(locale),
            description=template.description.resolve(locale),
            input_summary=template.input_summary.resolve(locale),
            output_summary=template.output_summary.resolve(locale),
            input_schema=_model_schema(OPENCLAW_SYSTEM_CAPABILITY_INPUT_MODELS[template.key]),
            output_schema=_model_schema(OPENCLAW_SYSTEM_CAPABILITY_OUTPUT_MODELS[template.key]),
        )
    return definitions


def list_openclaw_system_capability_definitions(
    locale: str | None = None,
) -> list[OpenClawSystemCapabilityDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    return list(_registry(normalized_locale).values())


def get_openclaw_system_capability_definition(
    key: str,
    locale: str | None = None,
) -> OpenClawSystemCapabilityDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    try:
        return _registry(normalized_locale)[key]  # type: ignore[index]
    except Exception:
        return None


def clear_openclaw_integration_registry_cache() -> None:
    _registry.cache_clear()
