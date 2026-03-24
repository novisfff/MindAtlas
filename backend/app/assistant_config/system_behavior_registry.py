from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from app.system_settings.service import get_default_system_locale, normalize_system_locale


SystemBehaviorKey = Literal[
    "weekly_report_generation",
    "monthly_report_generation",
]
SystemBehaviorTargetType = Literal["workflow", "agent"]
SystemBehaviorFieldType = Literal["string", "number", "integer", "boolean", "object", "array"]


@dataclass(frozen=True)
class SystemBehaviorFieldDefinition:
    name: str
    type: SystemBehaviorFieldType
    required: bool = True
    description: str = ""
    items_type: SystemBehaviorFieldType | None = None


@dataclass(frozen=True)
class SystemBehaviorDefaultTarget:
    target_type: SystemBehaviorTargetType
    canonical_name: str
    workflow_preset_file: str | None = None


@dataclass(frozen=True)
class SystemBehaviorDefinition:
    key: SystemBehaviorKey
    name: str
    description: str
    supported_target_types: tuple[SystemBehaviorTargetType, ...]
    input_fields: tuple[SystemBehaviorFieldDefinition, ...]
    output_fields: tuple[SystemBehaviorFieldDefinition, ...]
    fallback_policy: str
    default_target: SystemBehaviorDefaultTarget


@dataclass(frozen=True)
class _LocalizedText:
    zh: str
    en: str

    def resolve(self, locale: str) -> str:
        return self.zh if locale == "zh" else self.en


@dataclass(frozen=True)
class _SystemBehaviorFieldTemplate:
    name: str
    type: SystemBehaviorFieldType
    required: bool = True
    description: _LocalizedText = _LocalizedText(zh="", en="")
    items_type: SystemBehaviorFieldType | None = None


@dataclass(frozen=True)
class _SystemBehaviorTemplate:
    key: SystemBehaviorKey
    name: _LocalizedText
    description: _LocalizedText
    supported_target_types: tuple[SystemBehaviorTargetType, ...]
    input_fields: tuple[_SystemBehaviorFieldTemplate, ...]
    output_fields: tuple[_SystemBehaviorFieldTemplate, ...]
    fallback_policy: str
    default_target: SystemBehaviorDefaultTarget


REPORT_INPUT_FIELDS: tuple[_SystemBehaviorFieldTemplate, ...] = (
    _SystemBehaviorFieldTemplate(
        name="periodType",
        type="string",
        description=_LocalizedText(
            zh="报告周期类型，例如 weekly 或 monthly。",
            en="Report period type, for example weekly or monthly.",
        ),
    ),
    _SystemBehaviorFieldTemplate(
        name="periodStart",
        type="string",
        description=_LocalizedText(
            zh="报告周期开始日期，格式为 YYYY-MM-DD，包含当天。",
            en="Report period start date in YYYY-MM-DD format, inclusive.",
        ),
    ),
    _SystemBehaviorFieldTemplate(
        name="periodEnd",
        type="string",
        description=_LocalizedText(
            zh="报告周期结束日期，格式为 YYYY-MM-DD，包含当天。",
            en="Report period end date in YYYY-MM-DD format, inclusive.",
        ),
    ),
    _SystemBehaviorFieldTemplate(
        name="entryCount",
        type="integer",
        description=_LocalizedText(
            zh="落在当前报告周期内的记录数量。",
            en="Number of entries that fall within the current report period.",
        ),
    ),
)

REPORT_OUTPUT_FIELDS: tuple[_SystemBehaviorFieldTemplate, ...] = (
    _SystemBehaviorFieldTemplate(
        name="summary",
        type="string",
        description=_LocalizedText(
            zh="面向用户展示的报告摘要。",
            en="Report summary shown to the user.",
        ),
    ),
    _SystemBehaviorFieldTemplate(
        name="suggestions",
        type="array",
        description=_LocalizedText(
            zh="从报告中提炼出的后续行动建议。",
            en="Follow-up action suggestions distilled from the report.",
        ),
        items_type="string",
    ),
    _SystemBehaviorFieldTemplate(
        name="trends",
        type="string",
        description=_LocalizedText(
            zh="对当前周期趋势的观察与总结。",
            en="Observations and summary of trends in the current period.",
        ),
    ),
)


def _materialize_field(template: _SystemBehaviorFieldTemplate, locale: str) -> SystemBehaviorFieldDefinition:
    return SystemBehaviorFieldDefinition(
        name=template.name,
        type=template.type,
        required=template.required,
        description=template.description.resolve(locale),
        items_type=template.items_type,
    )


def _normalize_registry_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


@lru_cache(maxsize=4)
def _registry(locale: str) -> dict[SystemBehaviorKey, SystemBehaviorDefinition]:
    return {
        "weekly_report_generation": SystemBehaviorDefinition(
            key="weekly_report_generation",
            name=_LocalizedText(zh="周报生成", en="Weekly Report Generation").resolve(locale),
            description=_LocalizedText(
                zh="通过可复用的 Workflow 或 Agent 生成系统周报。",
                en="Generate system weekly reports through reusable workflows or agents.",
            ).resolve(locale),
            supported_target_types=("workflow", "agent"),
            input_fields=tuple(_materialize_field(field, locale) for field in REPORT_INPUT_FIELDS),
            output_fields=tuple(_materialize_field(field, locale) for field in REPORT_OUTPUT_FIELDS),
            fallback_policy="canonical_default_target",
            default_target=SystemBehaviorDefaultTarget(
                target_type="workflow",
                canonical_name="system_weekly_report__workflow",
                workflow_preset_file="workflows/system_weekly_report.json",
            ),
        ),
        "monthly_report_generation": SystemBehaviorDefinition(
            key="monthly_report_generation",
            name=_LocalizedText(zh="月报生成", en="Monthly Report Generation").resolve(locale),
            description=_LocalizedText(
                zh="通过可复用的 Workflow 或 Agent 生成系统月报。",
                en="Generate system monthly reports through reusable workflows or agents.",
            ).resolve(locale),
            supported_target_types=("workflow", "agent"),
            input_fields=tuple(_materialize_field(field, locale) for field in REPORT_INPUT_FIELDS),
            output_fields=tuple(_materialize_field(field, locale) for field in REPORT_OUTPUT_FIELDS),
            fallback_policy="canonical_default_target",
            default_target=SystemBehaviorDefaultTarget(
                target_type="workflow",
                canonical_name="system_monthly_report__workflow",
                workflow_preset_file="workflows/system_monthly_report.json",
            ),
        ),
    }


def list_system_behavior_definitions(locale: str | None = None) -> list[SystemBehaviorDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    return list(_registry(normalized_locale).values())


def get_system_behavior_definition(key: str, locale: str | None = None) -> SystemBehaviorDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    try:
        return _registry(normalized_locale)[key]  # type: ignore[index]
    except Exception:
        return None


def clear_system_behavior_registry_cache() -> None:
    _registry.cache_clear()
