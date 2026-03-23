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
        description="Logical report period kind, for example weekly or monthly.",
    ),
    SystemBehaviorFieldDefinition(
        name="periodStart",
        type="string",
        description="Inclusive report period start date in YYYY-MM-DD format.",
    ),
    SystemBehaviorFieldDefinition(
        name="periodEnd",
        type="string",
        description="Inclusive report period end date in YYYY-MM-DD format.",
    ),
    SystemBehaviorFieldDefinition(
        name="entryCount",
        type="integer",
        description="Number of entries that fall within the report period.",
    ),
)

REPORT_OUTPUT_FIELDS: tuple[SystemBehaviorFieldDefinition, ...] = (
    SystemBehaviorFieldDefinition(
        name="summary",
        type="string",
        description="Human-readable report summary.",
    ),
    SystemBehaviorFieldDefinition(
        name="suggestions",
        type="array",
        description="Follow-up suggestions extracted from the report.",
        items_type="string",
    ),
    SystemBehaviorFieldDefinition(
        name="trends",
        type="string",
        description="Observed trends for the period.",
    ),
)


@lru_cache(maxsize=1)
def _registry() -> dict[SystemBehaviorKey, SystemBehaviorDefinition]:
    return {
        "weekly_report_generation": SystemBehaviorDefinition(
            key="weekly_report_generation",
            name="Weekly Report Generation",
            description="Generate the system weekly report through a reusable assistant target.",
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
            name="Monthly Report Generation",
            description="Generate the system monthly report through a reusable assistant target.",
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
