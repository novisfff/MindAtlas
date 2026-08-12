from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.assistant.workflow.system_assets import (
    PERIODIC_REVIEW_CORE_ASSET_KEY,
    clear_system_asset_registry_cache,
    get_system_asset,
    get_system_asset_by_canonical_name,
    list_system_assets,
)

StandaloneSystemWorkflowAssetKey = Literal[
    "periodic_review_core",
]


@dataclass(frozen=True)
class StandaloneSystemWorkflowDefinition:
    asset_key: StandaloneSystemWorkflowAssetKey
    canonical_name: str
    display_name: str
    description: str
    enabled_by_default: bool
    legacy_canonical_names: tuple[str, ...] = ()


def _to_definition(asset) -> StandaloneSystemWorkflowDefinition:
    return StandaloneSystemWorkflowDefinition(
        asset_key=asset.asset_key,
        canonical_name=asset.canonical_name,
        display_name=asset.display_name,
        description=asset.description,
        enabled_by_default=asset.enabled_by_default,
        legacy_canonical_names=tuple(asset.legacy_canonical_names),
    )


def list_standalone_system_workflow_definitions(
    locale: str | None = None,
) -> list[StandaloneSystemWorkflowDefinition]:
    return [
        _to_definition(asset)
        for asset in list_system_assets(kind="workflow", usage_tag="standalone_target", locale=locale)
    ]


def get_standalone_system_workflow_definition(
    asset_key: str,
    locale: str | None = None,
) -> StandaloneSystemWorkflowDefinition | None:
    asset = get_system_asset(asset_key, locale=locale)
    if asset is None or asset.kind != "workflow" or "standalone_target" not in asset.usage_tags:
        return None
    return _to_definition(asset)


def get_standalone_system_workflow_definition_by_canonical_name(
    canonical_name: str,
    locale: str | None = None,
) -> StandaloneSystemWorkflowDefinition | None:
    asset = get_system_asset_by_canonical_name(canonical_name, kind="workflow", locale=locale)
    if asset is None or "standalone_target" not in asset.usage_tags:
        return None
    return _to_definition(asset)


def clear_standalone_system_target_registry_cache() -> None:
    clear_system_asset_registry_cache()


__all__ = [
    "PERIODIC_REVIEW_CORE_ASSET_KEY",
    "StandaloneSystemWorkflowAssetKey",
    "StandaloneSystemWorkflowDefinition",
    "clear_standalone_system_target_registry_cache",
    "get_standalone_system_workflow_definition",
    "get_standalone_system_workflow_definition_by_canonical_name",
    "list_standalone_system_workflow_definitions",
]
