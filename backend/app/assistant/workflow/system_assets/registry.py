from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME
from app.system_settings.service import get_default_system_locale, normalize_system_locale

SystemAssistantAssetKind = Literal["workflow", "agent"]
SystemAssistantAssetUsageTag = Literal["skill_default", "standalone_target", "system_behavior_default"]

QUICK_STATS_ASSET_KEY = "quick_stats"
SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY = "smart_capture_golden_create"
PERIODIC_REVIEW_ASSET_KEY = "periodic_review"
PERIODIC_REVIEW_CORE_ASSET_KEY = "periodic_review_core"
GENERAL_CHAT_ASSET_KEY = DEFAULT_SKILL_NAME
WEEKLY_REPORT_ASSET_KEY = "weekly_report"
MONTHLY_REPORT_ASSET_KEY = "monthly_report"


@dataclass(frozen=True)
class _LocalizedText:
    zh: str
    en: str

    def resolve(self, locale: str) -> str:
        return self.zh if locale == "zh" else self.en


@dataclass(frozen=True)
class _LocalizedTextList:
    zh: tuple[str, ...]
    en: tuple[str, ...]

    def resolve(self, locale: str) -> tuple[str, ...]:
        return self.zh if locale == "zh" else self.en


@dataclass(frozen=True)
class _SystemAssistantAssetTemplate:
    asset_key: str
    kind: SystemAssistantAssetKind
    canonical_name: str
    display_name: _LocalizedText
    description: _LocalizedText
    enabled_by_default: bool = True
    hidden: bool = False
    legacy_canonical_names: tuple[str, ...] = ()
    usage_tags: tuple[SystemAssistantAssetUsageTag, ...] = ()
    skill_name: str | None = None
    skill_intent_examples: _LocalizedTextList | None = None
    behavior_key: str | None = None


@dataclass(frozen=True)
class SystemAssistantAssetDefinition:
    asset_key: str
    kind: SystemAssistantAssetKind
    canonical_name: str
    display_name: str
    description: str
    enabled_by_default: bool
    hidden: bool
    legacy_canonical_names: tuple[str, ...]
    usage_tags: tuple[SystemAssistantAssetUsageTag, ...]
    skill_name: str | None = None
    skill_intent_examples: tuple[str, ...] = ()
    behavior_key: str | None = None


class SystemWorkflowAssetDefinition(SystemAssistantAssetDefinition):
    kind: Literal["workflow"]


class SystemAgentAssetDefinition(SystemAssistantAssetDefinition):
    kind: Literal["agent"]


_ASSET_TEMPLATES: tuple[_SystemAssistantAssetTemplate, ...] = (
    _SystemAssistantAssetTemplate(
        asset_key=QUICK_STATS_ASSET_KEY,
        kind="workflow",
        canonical_name="quick_stats__workflow",
        display_name=_LocalizedText(
            zh="快速统计工作流",
            en="Quick Stats Workflow",
        ),
        description=_LocalizedText(
            zh="快速统计（仅统计 MindAtlas 系统内数据，如记录、标签、类型、关系、近期录入活动或指定时间范围内的概况；不用于对外部组织/项目/人物的情况总结）",
            en="Quick statistics for data inside MindAtlas only, such as entries, tags, types, relations, recent capture activity, or scoped summaries for a specified time range. Do not use this for summarizing external companies, projects, or people.",
        ),
        usage_tags=("skill_default",),
        skill_name="quick_stats",
        skill_intent_examples=_LocalizedTextList(
            zh=(
                "统计一下我在 MindAtlas 里有多少条记录",
                "看下我的仪表盘数据/数据概况",
                "我有多少个标签、多少种类型？",
                "看下我近7天的录入趋势",
                "按标签统计一下我最近常用什么",
                "按类型看看我的记录分布",
                "统计 2026-03-01 到 2026-03-31 的记录概况",
                "看下我上周的标签分布",
            ),
            en=(
                "How many entries do I have in MindAtlas?",
                "Show me my dashboard stats",
                "How many tags and types do I have?",
                "Show my capture trend for the last 7 days",
                "Summarize my most-used tags",
                "Break down my entries by type",
                "Summarize my entries from 2026-03-01 to 2026-03-31",
                "Show my tag distribution from last week",
            ),
        ),
    ),
    # Production's smart_capture skill uses this audited create-only path.
    # Topology: start -> reviewed structured create input -> create_entry -> output.
    # Approval is call-owned via LedgerDispatcher (no Workflow human_in_loop node).
    _SystemAssistantAssetTemplate(
        asset_key=SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
        kind="workflow",
        canonical_name="smart-capture-golden-create__workflow",
        display_name=_LocalizedText(
            zh="智能创建记录（Golden 仅创建）",
            en="Smart Capture Golden Create",
        ),
        description=_LocalizedText(
            zh="受审计的 create-only 创建记录工作流：结构化准备后仅调用 create_entry。"
            "无人工节点、无更新/合并/关系/子流程。审批由 CapabilityCall 账本持有。",
            en="Audited create-only workflow: structured preparation then "
            "exactly one create_entry call. No human nodes, update/merge/relation/"
            "follow-up edges. Approval is owned by the CapabilityCall ledger.",
        ),
        hidden=True,
        usage_tags=("skill_default",),
        skill_name="smart_capture",
        skill_intent_examples=_LocalizedTextList(
            zh=(
                "帮我记录一下今天学到的 Python 技巧",
                "把下面内容保存为一条笔记：……",
                "创建一个新任务：下周一交报告",
            ),
            en=(
                "Please save what I learned about Python today",
                "Save the following as a note: ...",
                "Create a new task: submit the report next Monday",
            ),
        ),
    ),
    _SystemAssistantAssetTemplate(
        asset_key=PERIODIC_REVIEW_ASSET_KEY,
        kind="workflow",
        canonical_name="periodic_review__workflow",
        display_name=_LocalizedText(
            zh="周期性回顾工作流",
            en="Periodic Review Workflow",
        ),
        description=_LocalizedText(
            zh="周期性回顾与分析（按时间范围回顾 MindAtlas 中我的记录并生成周报/月报/复盘；需要明确时间范围，如上周/本月/某日期区间）",
            en="Periodic review and analysis across a time range in MindAtlas, such as generating weekly reviews, monthly reviews, or retrospectives. The user should imply or specify a time range like last week, this month, or a concrete date range.",
        ),
        usage_tags=("skill_default",),
        skill_name="periodic_review",
        skill_intent_examples=_LocalizedTextList(
            zh=(
                "回顾我上周在 MindAtlas 里的记录并生成周报",
                "复盘我本月的记录产出",
                "分析 2025-01-01 到 2025-01-31 的我的记录",
                "查看我这周的学习记录进度",
            ),
            en=(
                "Review my MindAtlas entries from last week and generate a weekly report",
                "Retrospect on what I recorded this month",
                "Analyze my entries from 2025-01-01 to 2025-01-31",
                "Show me my learning progress for this week",
            ),
        ),
    ),
    _SystemAssistantAssetTemplate(
        asset_key=PERIODIC_REVIEW_CORE_ASSET_KEY,
        kind="workflow",
        canonical_name="system_periodic_review_core__workflow",
        display_name=_LocalizedText(
            zh="周期回顾核心工作流",
            en="Periodic Review Core Workflow",
        ),
        description=_LocalizedText(
            zh="供聊天工作流和 OpenClaw 统一复用的结构化周期回顾核心流程，接收关注点和时间范围参数后输出一段用户可直接阅读的回顾内容。",
            en="A structured periodic-review core workflow shared by chat wrappers and OpenClaw. It accepts focus plus time-range parameters and returns one user-ready review.",
        ),
        hidden=True,
        usage_tags=("standalone_target",),
    ),
    _SystemAssistantAssetTemplate(
        asset_key=GENERAL_CHAT_ASSET_KEY,
        kind="agent",
        canonical_name="general_chat__agent",
        display_name=_LocalizedText(
            zh="默认对话智能体",
            en="General Chat Agent",
        ),
        description=_LocalizedText(
            zh="默认兜底对话（未匹配到任何 Skill 时使用）：支持知识问答/总结/写作/翻译等；可结合知识库检索结果回答；默认不执行写入/创建操作",
            en="Default fallback conversation when no other skill matches. Supports knowledge Q&A, summarization, writing help, translation, and similar tasks. It may use knowledge-base retrieval results, but it should not perform write/create actions by default.",
        ),
        hidden=True,
        usage_tags=("skill_default",),
        skill_name=DEFAULT_SKILL_NAME,
        skill_intent_examples=_LocalizedTextList(
            zh=(),
            en=(),
        ),
    ),
    _SystemAssistantAssetTemplate(
        asset_key=WEEKLY_REPORT_ASSET_KEY,
        kind="workflow",
        canonical_name="system_weekly_report__workflow",
        display_name=_LocalizedText(
            zh="周报生成工作流",
            en="Weekly Report Generation Workflow",
        ),
        description=_LocalizedText(
            zh="通过可复用的 Workflow 或 Agent 生成系统周报。",
            en="Generate system weekly reports through reusable workflows or agents.",
        ),
        usage_tags=("system_behavior_default",),
        behavior_key="weekly_report_generation",
    ),
    _SystemAssistantAssetTemplate(
        asset_key=MONTHLY_REPORT_ASSET_KEY,
        kind="workflow",
        canonical_name="system_monthly_report__workflow",
        display_name=_LocalizedText(
            zh="月报生成工作流",
            en="Monthly Report Generation Workflow",
        ),
        description=_LocalizedText(
            zh="通过可复用的 Workflow 或 Agent 生成系统月报。",
            en="Generate system monthly reports through reusable workflows or agents.",
        ),
        usage_tags=("system_behavior_default",),
        behavior_key="monthly_report_generation",
    ),
)


def _normalize_registry_locale(locale: str | None) -> str:
    raw_locale = str(locale or "").strip()
    if not raw_locale:
        return get_default_system_locale()
    normalized = normalize_system_locale(raw_locale)
    if normalized is None:
        raise RuntimeError(f"Unsupported system asset locale: {locale}")
    return normalized


def _materialize_asset_definition(
    template: _SystemAssistantAssetTemplate,
    locale: str,
) -> SystemAssistantAssetDefinition:
    payload = {
        "asset_key": template.asset_key,
        "kind": template.kind,
        "canonical_name": template.canonical_name,
        "display_name": template.display_name.resolve(locale),
        "description": template.description.resolve(locale),
        "enabled_by_default": template.enabled_by_default,
        "hidden": template.hidden,
        "legacy_canonical_names": tuple(template.legacy_canonical_names),
        "usage_tags": tuple(template.usage_tags),
        "skill_name": template.skill_name,
        "skill_intent_examples": template.skill_intent_examples.resolve(locale) if template.skill_intent_examples else (),
        "behavior_key": template.behavior_key,
    }
    if template.kind == "workflow":
        return SystemWorkflowAssetDefinition(**payload)
    return SystemAgentAssetDefinition(**payload)


@lru_cache(maxsize=4)
def _asset_registry(locale: str) -> dict[str, SystemAssistantAssetDefinition]:
    return {
        template.asset_key: _materialize_asset_definition(template, locale)
        for template in _ASSET_TEMPLATES
    }


def list_system_assets(
    *,
    kind: SystemAssistantAssetKind | None = None,
    usage_tag: SystemAssistantAssetUsageTag | None = None,
    locale: str | None = None,
) -> list[SystemAssistantAssetDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    items = list(_asset_registry(normalized_locale).values())
    if kind is not None:
        items = [item for item in items if item.kind == kind]
    if usage_tag is not None:
        items = [item for item in items if usage_tag in item.usage_tags]
    return items


def get_system_asset(
    asset_key: str,
    locale: str | None = None,
) -> SystemAssistantAssetDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    return _asset_registry(normalized_locale).get(str(asset_key or "").strip())


def get_system_asset_by_canonical_name(
    canonical_name: str,
    *,
    kind: SystemAssistantAssetKind | None = None,
    locale: str | None = None,
) -> SystemAssistantAssetDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    needle = str(canonical_name or "").strip()
    if not needle:
        return None
    for item in _asset_registry(normalized_locale).values():
        if kind is not None and item.kind != kind:
            continue
        if item.canonical_name == needle or needle in set(item.legacy_canonical_names or ()):
            return item
    return None


def get_system_skill_asset(
    skill_name: str,
    locale: str | None = None,
) -> SystemAssistantAssetDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    needle = str(skill_name or "").strip()
    if not needle:
        return None
    for item in _asset_registry(normalized_locale).values():
        if item.skill_name == needle:
            return item
    return None


def clear_system_asset_registry_cache() -> None:
    _asset_registry.cache_clear()
