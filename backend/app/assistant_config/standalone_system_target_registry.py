from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from app.system_settings.service import get_default_system_locale, normalize_system_locale

StandaloneSystemWorkflowAssetKey = Literal["context_capture"]


@dataclass(frozen=True)
class _LocalizedText:
    zh: str
    en: str

    def resolve(self, locale: str) -> str:
        return self.zh if locale == "zh" else self.en


@dataclass(frozen=True)
class _StandaloneSystemWorkflowTemplate:
    asset_key: StandaloneSystemWorkflowAssetKey
    canonical_name: str
    display_name: _LocalizedText
    description: _LocalizedText
    preset_file_zh: str
    preset_file_en: str
    enabled_by_default: bool = True
    legacy_canonical_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class StandaloneSystemWorkflowDefinition:
    asset_key: StandaloneSystemWorkflowAssetKey
    canonical_name: str
    display_name: str
    description: str
    preset_file: str
    enabled_by_default: bool
    legacy_canonical_names: tuple[str, ...] = ()


_WORKFLOW_TEMPLATES: tuple[_StandaloneSystemWorkflowTemplate, ...] = (
    _StandaloneSystemWorkflowTemplate(
        asset_key="context_capture",
        canonical_name="system_context_capture__workflow",
        display_name=_LocalizedText(
            zh="智能上下文入库工作流",
            en="Smart Context Capture Workflow",
        ),
        description=_LocalizedText(
            zh="将一段高价值上下文整理、判定新建或合并，并最终写入 MindAtlas 的系统工作流。",
            en="A system workflow that materializes one high-value context block, decides create versus merge, and saves it into MindAtlas.",
        ),
        preset_file_zh="workflows/system_context_capture.json",
        preset_file_en="workflows/system_context_capture.en.json",
        enabled_by_default=True,
        legacy_canonical_names=("system_openclaw_context_capture__workflow",),
    ),
)


def _normalize_registry_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


@lru_cache(maxsize=4)
def _workflow_registry(locale: str) -> dict[str, StandaloneSystemWorkflowDefinition]:
    definitions: dict[str, StandaloneSystemWorkflowDefinition] = {}
    for template in _WORKFLOW_TEMPLATES:
        definitions[template.asset_key] = StandaloneSystemWorkflowDefinition(
            asset_key=template.asset_key,
            canonical_name=template.canonical_name,
            display_name=template.display_name.resolve(locale),
            description=template.description.resolve(locale),
            preset_file=template.preset_file_zh if locale == "zh" else template.preset_file_en,
            enabled_by_default=template.enabled_by_default,
            legacy_canonical_names=tuple(template.legacy_canonical_names),
        )
    return definitions


def list_standalone_system_workflow_definitions(
    locale: str | None = None,
) -> list[StandaloneSystemWorkflowDefinition]:
    normalized_locale = _normalize_registry_locale(locale)
    return list(_workflow_registry(normalized_locale).values())


def get_standalone_system_workflow_definition(
    asset_key: str,
    locale: str | None = None,
) -> StandaloneSystemWorkflowDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    return _workflow_registry(normalized_locale).get(asset_key)


def get_standalone_system_workflow_definition_by_canonical_name(
    canonical_name: str,
    locale: str | None = None,
) -> StandaloneSystemWorkflowDefinition | None:
    normalized_locale = _normalize_registry_locale(locale)
    needle = str(canonical_name or "").strip()
    if not needle:
        return None
    for definition in _workflow_registry(normalized_locale).values():
        if definition.canonical_name == needle:
            return definition
        if needle in set(definition.legacy_canonical_names or ()):
            return definition
    return None


def clear_standalone_system_target_registry_cache() -> None:
    _workflow_registry.cache_clear()
