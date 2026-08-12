from __future__ import annotations

from app.assistant.workflow.system_assets.loader import (
    clear_system_asset_loader_cache,
    load_system_agent_asset,
    load_system_workflow_asset,
)
from app.assistant.workflow.system_assets.registry import (
    GENERAL_CHAT_ASSET_KEY,
    MONTHLY_REPORT_ASSET_KEY,
    PERIODIC_REVIEW_ASSET_KEY,
    PERIODIC_REVIEW_CORE_ASSET_KEY,
    QUICK_STATS_ASSET_KEY,
    SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
    WEEKLY_REPORT_ASSET_KEY,
    SystemAgentAssetDefinition,
    SystemAssistantAssetDefinition,
    SystemWorkflowAssetDefinition,
    clear_system_asset_registry_cache,
    get_system_asset,
    get_system_asset_by_canonical_name,
    get_system_skill_asset,
    list_system_assets,
)

__all__ = [
    "GENERAL_CHAT_ASSET_KEY",
    "MONTHLY_REPORT_ASSET_KEY",
    "PERIODIC_REVIEW_ASSET_KEY",
    "PERIODIC_REVIEW_CORE_ASSET_KEY",
    "QUICK_STATS_ASSET_KEY",
    "SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY",
    "WEEKLY_REPORT_ASSET_KEY",
    "SystemAgentAssetDefinition",
    "SystemAssistantAssetDefinition",
    "SystemWorkflowAssetDefinition",
    "clear_system_asset_loader_cache",
    "clear_system_asset_registry_cache",
    "get_system_asset",
    "get_system_asset_by_canonical_name",
    "get_system_skill_asset",
    "list_system_assets",
    "load_system_agent_asset",
    "load_system_workflow_asset",
]
