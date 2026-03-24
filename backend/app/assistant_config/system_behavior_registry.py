from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal


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


REPORT_INPUT_FIELDS: tuple[SystemBehaviorFieldDefinition, ...] = (
    SystemBehaviorFieldDefinition(
        name="periodType",
        type="string",
        description="报告周期类型，例如 weekly 或 monthly。",
    ),
    SystemBehaviorFieldDefinition(
        name="periodStart",
        type="string",
        description="报告周期开始日期，格式为 YYYY-MM-DD，包含当天。",
    ),
    SystemBehaviorFieldDefinition(
        name="periodEnd",
        type="string",
        description="报告周期结束日期，格式为 YYYY-MM-DD，包含当天。",
    ),
    SystemBehaviorFieldDefinition(
        name="entryCount",
        type="integer",
        description="落在当前报告周期内的记录数量。",
    ),
)

REPORT_OUTPUT_FIELDS: tuple[SystemBehaviorFieldDefinition, ...] = (
    SystemBehaviorFieldDefinition(
        name="summary",
        type="string",
        description="面向用户展示的报告摘要。",
    ),
    SystemBehaviorFieldDefinition(
        name="suggestions",
        type="array",
        description="从报告中提炼出的后续行动建议。",
        items_type="string",
    ),
    SystemBehaviorFieldDefinition(
        name="trends",
        type="string",
        description="对当前周期趋势的观察与总结。",
    ),
)


@lru_cache(maxsize=1)
def _registry() -> dict[SystemBehaviorKey, SystemBehaviorDefinition]:
    return {
        "weekly_report_generation": SystemBehaviorDefinition(
            key="weekly_report_generation",
            name="周报生成",
            description="通过可复用的 Workflow 或 Agent 生成系统周报。",
            supported_target_types=("workflow", "agent"),
            input_fields=REPORT_INPUT_FIELDS,
            output_fields=REPORT_OUTPUT_FIELDS,
            fallback_policy="canonical_default_target",
            default_target=SystemBehaviorDefaultTarget(
                target_type="workflow",
                canonical_name="system_weekly_report__workflow",
                workflow_preset_file="workflows/system_weekly_report.json",
            ),
        ),
        "monthly_report_generation": SystemBehaviorDefinition(
            key="monthly_report_generation",
            name="月报生成",
            description="通过可复用的 Workflow 或 Agent 生成系统月报。",
            supported_target_types=("workflow", "agent"),
            input_fields=REPORT_INPUT_FIELDS,
            output_fields=REPORT_OUTPUT_FIELDS,
            fallback_policy="canonical_default_target",
            default_target=SystemBehaviorDefaultTarget(
                target_type="workflow",
                canonical_name="system_monthly_report__workflow",
                workflow_preset_file="workflows/system_monthly_report.json",
            ),
        ),
    }


def list_system_behavior_definitions() -> list[SystemBehaviorDefinition]:
    return list(_registry().values())


def get_system_behavior_definition(key: str) -> SystemBehaviorDefinition | None:
    try:
        return _registry()[key]  # type: ignore[index]
    except Exception:
        return None
