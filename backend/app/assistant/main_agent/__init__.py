"""Main Agent application layer (Plan 04).

Protected prompt building, catalog recall, control capabilities, and runtime
admission live here. This package may import Plan 01–03 contracts; Plan 03 must
not import this package.
"""

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
from app.assistant.main_agent.prompt_builder import MainAgentPromptBuilder

__all__ = [
    "ActiveSkillInstruction",
    "CatalogSummaryRecord",
    "MainAgentPromptBudgetExceeded",
    "MainAgentPromptBuilder",
    "PromptBudgetCaps",
    "PromptBudgetLimits",
    "PromptBuildReport",
    "PromptBuildResult",
    "PromptLayerKind",
    "PromptLayerReport",
    "SkillContextBuildResult",
    "ToolArtifactSummary",
]
