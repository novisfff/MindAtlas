from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, TypeAlias, TypeVar

HUMAN_FIELD_TYPES = {"string", "number", "integer", "boolean", "array"}
HUMAN_FIELD_WIDGET_ALLOWED_TYPES: dict[str, set[str]] = {
    "input": {"string", "number", "integer"},
    "textarea": {"string"},
    "switch": {"boolean"},
    "select": {"string", "number", "integer"},
    "radio": {"string", "number", "integer"},
    "checkbox_group": {"array"},
    "tag_selector": {"array"},
    "date": {"string"},
    "time": {"string"},
}
HUMAN_FIELD_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HUMAN_FIELD_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
HUMAN_FIELD_OPTION_VALUE_KEYS = ("value", "code", "name", "label", "id", "type")
HUMAN_FIELD_OPTION_LABEL_KEYS = ("label", "name", "title", "code", "id", "type", "value")

_ErrorType = TypeVar("_ErrorType", bound=Exception)
HumanFieldOptionObject: TypeAlias = dict[str, str]
HumanFieldOption: TypeAlias = str | HumanFieldOptionObject


def normalize_human_field_type(raw: Any) -> str:
    field_type = str(raw or "string").strip().lower() or "string"
    if field_type not in HUMAN_FIELD_TYPES:
        return "string"
    return field_type


def default_human_field_widget(field_type: str) -> str:
    if field_type == "boolean":
        return "switch"
    if field_type == "array":
        return "tag_selector"
    return "input"


def normalize_human_field_widget(field_type: str, raw_widget: Any) -> str:
    widget = str(raw_widget or "").strip().lower() or default_human_field_widget(field_type)
    if widget not in HUMAN_FIELD_WIDGET_ALLOWED_TYPES:
        return default_human_field_widget(field_type)
    if field_type not in HUMAN_FIELD_WIDGET_ALLOWED_TYPES.get(widget, set()):
        return default_human_field_widget(field_type)
    return widget


def _extract_option_value_from_object(item: dict[str, Any]) -> str:
    for key in HUMAN_FIELD_OPTION_VALUE_KEYS:
        raw = item.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return ""


def _extract_option_label_from_object(item: dict[str, Any], *, fallback: str) -> str:
    for key in HUMAN_FIELD_OPTION_LABEL_KEYS:
        raw = item.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return fallback


def normalize_human_field_options(
    raw_options: Any,
    *,
    allow_objects: bool = False,
) -> list[HumanFieldOption]:
    if not isinstance(raw_options, list):
        return []
    options: list[HumanFieldOption] = []
    seen_values: set[str] = set()
    for item in raw_options:
        normalized: HumanFieldOption | None = None
        option_value = ""
        if isinstance(item, str):
            option_value = item.strip()
            normalized = option_value if option_value else None
        elif allow_objects and isinstance(item, dict):
            option_value = _extract_option_value_from_object(item)
            if option_value:
                option_label = _extract_option_label_from_object(item, fallback=option_value)
                option_description = str(item.get("description", "") or "").strip()
                normalized_object: HumanFieldOptionObject = {
                    "value": option_value,
                    "label": option_label,
                }
                if option_description:
                    normalized_object["description"] = option_description
                normalized = normalized_object
        if normalized is None or not option_value or option_value in seen_values:
            continue
        seen_values.add(option_value)
        options.append(normalized)
    return options


def human_field_option_value(option: HumanFieldOption) -> str:
    if isinstance(option, str):
        return option.strip()
    return str(option.get("value", "") or "").strip()


def _raise_field_error(
    *,
    error_cls: type[_ErrorType],
    subject: str,
    field_name: str,
    detail: str,
) -> None:
    raise error_cls(f"{subject} '{field_name}' {detail}")


def coerce_human_field_value_by_type(
    *,
    field_name: str,
    field_type: Any,
    value: Any,
    error_cls: type[_ErrorType] = ValueError,
    subject: str = "field",
) -> Any:
    normalized_type = normalize_human_field_type(field_type)

    if normalized_type == "string":
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    if normalized_type == "number":
        if isinstance(value, bool):
            _raise_field_error(
                error_cls=error_cls,
                subject=subject,
                field_name=field_name,
                detail="expects number",
            )
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            _raise_field_error(
                error_cls=error_cls,
                subject=subject,
                field_name=field_name,
                detail="expects number",
            )
        return float(text)

    if normalized_type == "integer":
        if isinstance(value, bool):
            _raise_field_error(
                error_cls=error_cls,
                subject=subject,
                field_name=field_name,
                detail="expects integer",
            )
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            if not value.is_integer():
                _raise_field_error(
                    error_cls=error_cls,
                    subject=subject,
                    field_name=field_name,
                    detail="expects integer",
                )
            return int(value)
        text = str(value or "").strip()
        if not text:
            _raise_field_error(
                error_cls=error_cls,
                subject=subject,
                field_name=field_name,
                detail="expects integer",
            )
        if text.startswith(("+", "-")):
            sign = text[0]
            body = text[1:]
        else:
            sign = ""
            body = text
        if not body.isdigit():
            _raise_field_error(
                error_cls=error_cls,
                subject=subject,
                field_name=field_name,
                detail="expects integer",
            )
        return int(f"{sign}{body}")

    if normalized_type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        _raise_field_error(
            error_cls=error_cls,
            subject=subject,
            field_name=field_name,
            detail="expects boolean",
        )

    if normalized_type == "array":
        if value is None:
            raw_items: list[Any] = []
        elif isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                raw_items = []
            else:
                parsed: Any = None
                if text.startswith("[") and text.endswith("]"):
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                if isinstance(parsed, list):
                    raw_items = parsed
                else:
                    raw_items = [segment.strip() for segment in text.split(",")]
        else:
            _raise_field_error(
                error_cls=error_cls,
                subject=subject,
                field_name=field_name,
                detail="expects string array",
            )

        normalized_items: list[str] = []
        for item in raw_items:
            if item is None:
                continue
            item_text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str)
            cleaned = item_text.strip()
            if cleaned:
                normalized_items.append(cleaned)
        return normalized_items

    _raise_field_error(
        error_cls=error_cls,
        subject=subject,
        field_name=field_name,
        detail=f"has unsupported type: {field_type}",
    )


def validate_human_field_date_value(
    *,
    field_name: str,
    value: Any,
    error_cls: type[_ErrorType] = ValueError,
    subject: str = "field",
) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if text.lower() == "null":
        return ""
    if "T" in text:
        text = text.split("T", 1)[0].strip()
    elif len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        text = text[:10]
    if not HUMAN_FIELD_DATE_RE.fullmatch(text):
        _raise_field_error(
            error_cls=error_cls,
            subject=subject,
            field_name=field_name,
            detail="expects date format YYYY-MM-DD",
        )
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise error_cls(f"{subject} '{field_name}' expects date format YYYY-MM-DD") from exc
    return text


def validate_human_field_time_value(
    *,
    field_name: str,
    value: Any,
    error_cls: type[_ErrorType] = ValueError,
    subject: str = "field",
) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if not HUMAN_FIELD_TIME_RE.fullmatch(text):
        _raise_field_error(
            error_cls=error_cls,
            subject=subject,
            field_name=field_name,
            detail="expects time format HH:mm",
        )
    return text
