from __future__ import annotations

from typing import Any


def cfg_get(cfg: dict, *keys: str, default=None):
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return default


def cfg_bool(cfg: dict, *keys: str, default: bool = False) -> bool:
    value = cfg_get(cfg, *keys, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def cfg_str_list(cfg: dict, *keys: str) -> list[str]:
    value = cfg_get(cfg, *keys, default=None)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            out.append(text)
    return out


def cfg_list(cfg: dict, *keys: str) -> list[Any]:
    value = cfg_get(cfg, *keys, default=None)
    return value if isinstance(value, list) else []
