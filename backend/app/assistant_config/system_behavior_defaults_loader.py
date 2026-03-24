from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.assistant_config.schemas import WorkflowInput
from app.assistant_config.system_behavior_registry import (
    SystemBehaviorDefinition,
    get_system_behavior_definition,
)
from app.system_settings.service import get_default_system_locale, normalize_system_locale


def _defaults_dir() -> Path:
    return Path(__file__).resolve().parent / "system_behavior_defaults"


def _normalize_behavior_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


def _localized_preset_file(preset_file: str, locale: str) -> str:
    if locale == "zh":
        return preset_file
    path = Path(preset_file)
    return str(path.with_name(f"{path.stem}.{locale}{path.suffix}"))


def _resolve_preset_path(preset_file: str) -> Path:
    base_dir = _defaults_dir().resolve()
    path = (base_dir / preset_file).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise RuntimeError(f"System behavior preset path escapes base dir: {preset_file}") from exc
    return path


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"System behavior preset JSON not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"System behavior preset JSON unreadable: {path}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"System behavior preset JSON invalid: {path}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"System behavior preset root must be object: {path}")
    return payload


@lru_cache(maxsize=8)
def _load_system_behavior_workflow_preset_cached(preset_file: str, locale: str) -> WorkflowInput:
    payload = _read_json(_resolve_preset_path(_localized_preset_file(preset_file, locale)))
    return WorkflowInput.model_validate(payload)


def load_system_behavior_workflow_preset(preset_file: str, locale: str | None = None) -> WorkflowInput:
    normalized_locale = _normalize_behavior_locale(locale)
    return _load_system_behavior_workflow_preset_cached(preset_file, normalized_locale)


def get_system_behavior_default_workflow(
    definition: SystemBehaviorDefinition,
    locale: str | None = None,
) -> WorkflowInput:
    preset_file = definition.default_target.workflow_preset_file
    if not preset_file:
        raise RuntimeError(f"System behavior '{definition.key}' does not define a workflow preset")
    return load_system_behavior_workflow_preset(preset_file, locale=locale)


def get_system_behavior_default_workflow_by_key(
    behavior_key: str,
    locale: str | None = None,
) -> WorkflowInput | None:
    definition = get_system_behavior_definition(behavior_key, locale=locale)
    if definition is None or definition.default_target.target_type != "workflow":
        return None
    return get_system_behavior_default_workflow(definition, locale=locale)


def clear_system_behavior_defaults_cache() -> None:
    _load_system_behavior_workflow_preset_cached.cache_clear()
