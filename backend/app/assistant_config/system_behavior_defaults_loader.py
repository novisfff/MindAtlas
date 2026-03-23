from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.assistant_config.schemas import WorkflowInput
from app.assistant_config.system_behavior_registry import (
    SystemBehaviorDefinition,
    get_system_behavior_definition,
)


def _defaults_dir() -> Path:
    return Path(__file__).resolve().parent / "system_behavior_defaults"


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


@lru_cache(maxsize=16)
def load_system_behavior_workflow_preset(preset_file: str) -> WorkflowInput:
    payload = _read_json(_resolve_preset_path(preset_file))
    return WorkflowInput.model_validate(payload)


def get_system_behavior_default_workflow(definition: SystemBehaviorDefinition) -> WorkflowInput:
    preset_file = definition.default_target.workflow_preset_file
    if not preset_file:
        raise RuntimeError(f"System behavior '{definition.key}' does not define a workflow preset")
    return load_system_behavior_workflow_preset(preset_file)


def get_system_behavior_default_workflow_by_key(behavior_key: str) -> WorkflowInput | None:
    definition = get_system_behavior_definition(behavior_key)
    if definition is None or definition.default_target.target_type != "workflow":
        return None
    return get_system_behavior_default_workflow(definition)
