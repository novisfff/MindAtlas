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

OpenClawSystemPresetSourceType = Literal["system_adapter", "workflow"]
OpenClawSystemPresetKey = Literal[
    "submit_context_capture",
    "capture_entry",
    "search_entries",
    "get_entry",
    "create_relation",
    "query_knowledge_graph",
    "generate_weekly_report",
    "generate_monthly_report",
]
OpenClawSystemImplementationType = Literal["entry", "relation", "knowledge_graph", "report", "workflow"]

OPENCLAW_CONTEXT_CAPTURE_PRESET_KEY: OpenClawSystemPresetKey = "submit_context_capture"
OPENCLAW_CONTEXT_CAPTURE_WORKFLOW_NAME = "system_openclaw_context_capture__workflow"


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


@dataclass(frozen=True)
class _WorkflowSystemPresetTemplate:
    key: OpenClawSystemPresetKey
    tool_name: str
    enabled_by_default: bool
    title: _LocalizedText
    description: _LocalizedText
    workflow_canonical_name: str
    workflow_preset_file_zh: str
    workflow_preset_file_en: str


@dataclass(frozen=True)
class OpenClawSystemPresetDefinition:
    key: OpenClawSystemPresetKey
    source_type: OpenClawSystemPresetSourceType
    tool_name: str
    enabled_by_default: bool
    implementation_type: OpenClawSystemImplementationType
    title: str
    description: str
    input_summary: str | None = None
    output_summary: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    system_capability_key: OpenClawSystemCapabilityKey | None = None
    workflow_canonical_name: str | None = None
    workflow_preset_file: str | None = None


_SYSTEM_CAPABILITY_TEMPLATES: tuple[_SystemCapabilityTemplate, ...] = (
    _SystemCapabilityTemplate(
        key="capture_entry",
        tool_name="mindatlas_capture_entry",
        enabled_by_default=False,
        implementation_type="entry",
        title=_LocalizedText(zh="字段级记录内容", en="Field-Level Capture Entry"),
        description=_LocalizedText(
            zh="按完整字段创建一条 MindAtlas 记录，适合管理员仍需显式拼装字段时使用。",
            en="Create a MindAtlas entry from explicit fields when an administrator still needs field-level capture.",
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

_WORKFLOW_SYSTEM_PRESET_TEMPLATES: tuple[_WorkflowSystemPresetTemplate, ...] = (
    _WorkflowSystemPresetTemplate(
        key=OPENCLAW_CONTEXT_CAPTURE_PRESET_KEY,
        tool_name="mindatlas_submit_context_capture",
        enabled_by_default=True,
        title=_LocalizedText(zh="提交记录上下文", en="Submit Record Context"),
        description=_LocalizedText(
            zh="向 MindAtlas 提交轻量上下文，由系统工作流自动物化最终记录字段并完成入库。",
            en="Submit thin context to MindAtlas so a system workflow can materialize the final record fields and persist them.",
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
def _capability_registry(locale: str) -> dict[OpenClawSystemCapabilityKey, OpenClawSystemCapabilityDefinition]:
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


@lru_cache(maxsize=4)
def _preset_registry(locale: str) -> dict[OpenClawSystemPresetKey, OpenClawSystemPresetDefinition]:
    definitions: dict[OpenClawSystemPresetKey, OpenClawSystemPresetDefinition] = {}

    for workflow_template in _WORKFLOW_SYSTEM_PRESET_TEMPLATES:
        definitions[workflow_template.key] = OpenClawSystemPresetDefinition(
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

    for capability_definition in _capability_registry(locale).values():
        definitions[capability_definition.key] = OpenClawSystemPresetDefinition(
            key=capability_definition.key,
            source_type="system_adapter",
            tool_name=capability_definition.tool_name,
            enabled_by_default=capability_definition.enabled_by_default,
            implementation_type=capability_definition.implementation_type,
            title=capability_definition.title,
            description=capability_definition.description,
            input_summary=capability_definition.input_summary,
            output_summary=capability_definition.output_summary,
            input_schema=capability_definition.input_schema,
            output_schema=capability_definition.output_schema,
            system_capability_key=capability_definition.key,
        )
    return definitions


def list_openclaw_system_capability_definitions(
    locale: str | None = None,
) -> list[OpenClawSystemCapabilityDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    return list(_capability_registry(normalized_locale).values())


def get_openclaw_system_capability_definition(
    key: str,
    locale: str | None = None,
) -> OpenClawSystemCapabilityDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    try:
        return _capability_registry(normalized_locale)[key]  # type: ignore[index]
    except Exception:
        return None


def list_openclaw_system_preset_definitions(
    locale: str | None = None,
) -> list[OpenClawSystemPresetDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    return list(_preset_registry(normalized_locale).values())


def get_openclaw_system_preset_definition(
    key: str,
    locale: str | None = None,
) -> OpenClawSystemPresetDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    try:
        return _preset_registry(normalized_locale)[key]  # type: ignore[index]
    except Exception:
        return None


def clear_openclaw_integration_registry_cache() -> None:
    _capability_registry.cache_clear()
    _preset_registry.cache_clear()
