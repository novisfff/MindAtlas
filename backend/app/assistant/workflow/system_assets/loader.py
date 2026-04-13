from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import re

from app.assistant.workflow.system_assets.registry import get_system_asset
from app.assistant_config.schemas import AgentPublishDraftInput, WorkflowInput
from app.system_settings.service import get_default_system_locale, normalize_system_locale

_ASSET_KEY_RE = re.compile(r"^[a-z0-9_]+$")


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent


def _normalize_asset_locale(locale: str | None) -> str:
    raw_locale = str(locale or "").strip()
    if not raw_locale:
        return get_default_system_locale()
    normalized = normalize_system_locale(raw_locale)
    if normalized is None:
        raise RuntimeError(f"Unsupported system asset locale: {locale}")
    return normalized


def _read_json_file(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"System asset JSON not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"System asset JSON unreadable: {path}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"System asset JSON invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"System asset JSON root must be object: {path}")
    return payload


def _resolve_asset_path(*, kind: str, asset_key: str, locale: str) -> Path:
    normalized_key = str(asset_key or "").strip()
    if not _ASSET_KEY_RE.fullmatch(normalized_key):
        raise RuntimeError(f"Invalid system asset key: {asset_key}")
    kind_dir = "workflows" if kind == "workflow" else "agents"
    base_dir = (_assets_dir() / kind_dir).resolve()
    file_name = f"{normalized_key}.json" if locale == "zh" else f"{normalized_key}.{locale}.json"
    path = (base_dir / file_name).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise RuntimeError(f"System asset path escapes base dir: {file_name}") from exc
    if not path.is_file():
        raise RuntimeError(f"System {kind} asset file not found: {asset_key} ({locale})")
    return path


@lru_cache(maxsize=16)
def _load_system_workflow_asset_cached(asset_key: str, locale: str) -> WorkflowInput:
    definition = get_system_asset(asset_key, locale=locale)
    if definition is None:
        raise RuntimeError(f"System workflow asset not found: {asset_key}")
    if definition.kind != "workflow":
        raise RuntimeError(f"System asset is not a workflow: {asset_key}")
    payload = _read_json_file(_resolve_asset_path(kind="workflow", asset_key=asset_key, locale=locale))
    return WorkflowInput.model_validate(payload)


@lru_cache(maxsize=16)
def _load_system_agent_asset_cached(asset_key: str, locale: str) -> AgentPublishDraftInput:
    definition = get_system_asset(asset_key, locale=locale)
    if definition is None:
        raise RuntimeError(f"System agent asset not found: {asset_key}")
    if definition.kind != "agent":
        raise RuntimeError(f"System asset is not an agent: {asset_key}")
    payload = _read_json_file(_resolve_asset_path(kind="agent", asset_key=asset_key, locale=locale))
    return AgentPublishDraftInput.model_validate(payload)


def load_system_workflow_asset(
    asset_key: str,
    locale: str | None = None,
) -> WorkflowInput:
    normalized_locale = _normalize_asset_locale(locale)
    return _load_system_workflow_asset_cached(str(asset_key or "").strip(), normalized_locale)


def load_system_agent_asset(
    asset_key: str,
    locale: str | None = None,
) -> AgentPublishDraftInput:
    normalized_locale = _normalize_asset_locale(locale)
    return _load_system_agent_asset_cached(str(asset_key or "").strip(), normalized_locale)


def clear_system_asset_loader_cache() -> None:
    _load_system_workflow_asset_cached.cache_clear()
    _load_system_agent_asset_cached.cache_clear()
