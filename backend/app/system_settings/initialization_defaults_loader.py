from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.system_settings.service import get_default_system_locale, normalize_system_locale


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class InitializationDefaultEntryType(_CamelModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    color: str = ""
    icon: str = ""
    graph_enabled: bool = Field(default=True, alias="graphEnabled")
    ai_enabled: bool = Field(default=True, alias="aiEnabled")
    enabled: bool = True


class InitializationDefaultRelationType(_CamelModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    inverse_name: str = Field(default="", alias="inverseName")
    description: str = ""
    color: str = ""
    directed: bool = True
    enabled: bool = True


class _EntryTypesDocument(_CamelModel):
    schema_version: int = Field(alias="schemaVersion")
    entry_types: list[InitializationDefaultEntryType] = Field(default_factory=list, alias="entryTypes")


class _RelationTypesDocument(_CamelModel):
    schema_version: int = Field(alias="schemaVersion")
    relation_types: list[InitializationDefaultRelationType] = Field(default_factory=list, alias="relationTypes")


def _defaults_dir() -> Path:
    return Path(__file__).resolve().parent / "initialization_defaults"


def _normalize_defaults_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


def _read_json_file(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Initialization defaults JSON not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Initialization defaults JSON unreadable: {path}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Initialization defaults JSON invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Initialization defaults JSON root must be object: {path}")
    return payload


@lru_cache(maxsize=4)
def _load_entry_type_defaults_cached(locale: str) -> tuple[InitializationDefaultEntryType, ...]:
    payload = _read_json_file(_defaults_dir() / f"entry_types.{locale}.json")
    document = _EntryTypesDocument.model_validate(payload)
    if document.schema_version != 1:
        raise RuntimeError(f"Unsupported initialization entry type schemaVersion: {document.schema_version}")
    return tuple(document.entry_types)


@lru_cache(maxsize=4)
def _load_relation_type_defaults_cached(locale: str) -> tuple[InitializationDefaultRelationType, ...]:
    payload = _read_json_file(_defaults_dir() / f"relation_types.{locale}.json")
    document = _RelationTypesDocument.model_validate(payload)
    if document.schema_version != 1:
        raise RuntimeError(f"Unsupported initialization relation type schemaVersion: {document.schema_version}")
    return tuple(document.relation_types)


def load_initialization_entry_type_defaults(locale: str | None = None) -> list[InitializationDefaultEntryType]:
    normalized_locale = _normalize_defaults_locale(locale)
    return list(_load_entry_type_defaults_cached(normalized_locale))


def load_initialization_relation_type_defaults(locale: str | None = None) -> list[InitializationDefaultRelationType]:
    normalized_locale = _normalize_defaults_locale(locale)
    return list(_load_relation_type_defaults_cached(normalized_locale))


def clear_initialization_defaults_cache() -> None:
    _load_entry_type_defaults_cached.cache_clear()
    _load_relation_type_defaults_cached.cache_clear()
