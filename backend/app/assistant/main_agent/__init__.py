"""Main Agent application layer (Plan 04).

Protected prompt building, catalog recall, control capabilities, and runtime
admission live here. This package may import Plan 01–03 contracts; Plan 03 must
not import this package.
"""

from app.assistant.main_agent.catalog import (
    CatalogSearchResult,
    CatalogSearchState,
    SkillCatalogRecord,
    SkillCatalogSnapshot,
    build_catalog_snapshot,
)
from app.assistant.main_agent.contracts import (
    ActiveSkillInstruction,
    CatalogSummaryRecord,
    MainAgentPromptBudgetExceeded,
    PromptBudgetCaps,
    PromptBudgetLimits,
    PromptBuildReport,
    PromptBuildResult,
    PromptLayerKind,
    PromptLayerReport,
    SkillContextBuildResult,
    ToolArtifactSummary,
)
from app.assistant.main_agent.events import MainAgentEventAdapter, is_internal_event
from app.assistant.main_agent.model_eligibility import (
    ModelEligibilityError,
    ModelEligibilityReport,
    evaluate_probe_eligibility,
)
from app.assistant.main_agent.prompt_builder import MainAgentPromptBuilder
from app.assistant.main_agent.service import (
    AssistantRuntimeRequest,
    AssistantRuntimeResult,
    MainAgentAdmissionError,
    MainAgentFallbackState,
    MainAgentService,
    select_runtime_for_mode,
    should_construct_main_agent,
)
from app.assistant.main_agent.rollout import (
    RolloutError,
    RolloutExpectedState,
    RolloutReport,
    disable_rollout,
    enable_rollout,
    plan_rollout,
    run_rollout,
)

__all__ = [
    "ActiveSkillInstruction",
    "AssistantRuntimeRequest",
    "AssistantRuntimeResult",
    "CatalogSearchResult",
    "CatalogSearchState",
    "CatalogSummaryRecord",
    "MainAgentAdmissionError",
    "MainAgentEventAdapter",
    "MainAgentFallbackState",
    "MainAgentPromptBudgetExceeded",
    "MainAgentPromptBuilder",
    "MainAgentService",
    "ModelEligibilityError",
    "ModelEligibilityReport",
    "PromptBudgetCaps",
    "PromptBudgetLimits",
    "PromptBuildReport",
    "PromptBuildResult",
    "PromptLayerKind",
    "PromptLayerReport",
    "RolloutError",
    "RolloutExpectedState",
    "RolloutReport",
    "SkillCatalogRecord",
    "SkillCatalogSnapshot",
    "SkillContextBuildResult",
    "ToolArtifactSummary",
    "build_catalog_snapshot",
    "disable_rollout",
    "enable_rollout",
    "evaluate_probe_eligibility",
    "is_internal_event",
    "plan_rollout",
    "run_rollout",
    "select_runtime_for_mode",
    "should_construct_main_agent",
]
