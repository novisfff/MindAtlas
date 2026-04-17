from __future__ import annotations

from app.assistant.workflow.human_fields import normalize_human_field_options
from app.assistant.workflow.validation.contracts import (
    _HUMAN_FIELD_TYPES,
    _HUMAN_FIELD_WIDGET_ALLOWED_TYPES,
    _HUMAN_FIELD_WIDGETS,
    _START_INPUT_FIELD_NAME_RE,
)
from app.assistant.workflow.validation.models import ValidationError
from app.assistant.workflow.validation.rules.common import cfg_get

def validate_human_in_loop_node_config(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str,
) -> None:
    instruction = cfg_get(cfg, "instruction", default=None)
    if not isinstance(instruction, str) or not instruction.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} instruction is required",
            )
        )

    title = cfg_get(cfg, "title", default=None)
    if title is not None and not isinstance(title, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} title must be a string",
            )
        )

    approve_label = cfg_get(cfg, "approve_label", "approveLabel", default=None)
    if approve_label is not None and not isinstance(approve_label, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} approveLabel must be a string",
            )
        )

    reject_label = cfg_get(cfg, "reject_label", "rejectLabel", default=None)
    if reject_label is not None and not isinstance(reject_label, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} rejectLabel must be a string",
            )
        )

    require_reject_comment = cfg_get(cfg, "require_reject_comment", "requireRejectComment", default=True)
    if not isinstance(require_reject_comment, bool):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} requireRejectComment must be boolean",
            )
        )

    fields_raw = cfg_get(cfg, "fields", default=None)
    if not isinstance(fields_raw, list) or not fields_raw:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} fields must be a non-empty list",
            )
        )
        return

    seen_names: set[str] = set()
    for idx, item in enumerate(fields_raw, start=1):
        if not isinstance(item, dict):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field #{idx} must be an object",
                )
            )
            continue
        field_name = str(item.get("name", "") or "").strip()
        if not field_name:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field #{idx} requires name",
                )
            )
            continue
        if not _START_INPUT_FIELD_NAME_RE.fullmatch(field_name):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field name is invalid: {field_name}",
                )
            )
            continue
        if field_name in seen_names:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field name duplicated: {field_name}",
                )
            )
            continue
        seen_names.add(field_name)

        field_type_raw = item.get("type", "string")
        field_type = str(field_type_raw or "string").strip().lower() or "string"
        if field_type not in _HUMAN_FIELD_TYPES:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' has invalid type: {field_type_raw}",
                )
            )
            field_type = "string"

        raw_widget = item.get("widget", None)
        if raw_widget is None:
            widget = "switch" if field_type == "boolean" else "input"
        else:
            widget = str(raw_widget or "").strip().lower()
            if not widget:
                widget = "switch" if field_type == "boolean" else "input"
        if widget not in _HUMAN_FIELD_WIDGETS:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' has invalid widget: {raw_widget}",
                )
            )
        else:
            allowed_types = _HUMAN_FIELD_WIDGET_ALLOWED_TYPES.get(widget, set())
            if field_type not in allowed_types:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=(
                            f"{subject} field '{field_name}' widget '{widget}' is incompatible "
                            f"with type '{field_type}'"
                        ),
                    )
                )

        required = item.get("required", False)
        if not isinstance(required, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' required must be boolean",
                )
            )

        label = item.get("label")
        if label is not None and not isinstance(label, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' label must be a string",
                )
            )

        placeholder = item.get("placeholder")
        if placeholder is not None and not isinstance(placeholder, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' placeholder must be a string",
                )
            )

        options = item.get("options")
        options_template = item.get("options_template", item.get("optionsTemplate"))
        has_options_template = isinstance(options_template, str) and bool(options_template.strip())
        if options_template is not None and not isinstance(options_template, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' optionsTemplate must be a string",
                )
            )

        raw_option_value_key = item.get("option_value_key", item.get("optionValueKey", None))
        option_value_key = ""
        if raw_option_value_key is not None and not isinstance(raw_option_value_key, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' optionValueKey must be a string",
                )
            )
        elif isinstance(raw_option_value_key, str):
            option_value_key = raw_option_value_key.strip()

        if option_value_key and widget not in {"select", "radio", "checkbox_group", "tag_selector"}:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' optionValueKey is only supported for select/radio/checkbox_group/tag_selector",
                )
            )

        if options is not None:
            if not isinstance(options, list):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} field '{field_name}' options must be a list",
                    )
                )
            else:
                normalized_options = normalize_human_field_options(
                    options,
                    allow_objects=widget in {"select", "radio", "checkbox_group"},
                )
                if len(normalized_options) != len(options):
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=(
                                f"{subject} field '{field_name}' options must be non-empty strings"
                                if widget not in {"select", "radio", "checkbox_group"}
                                else (
                                    f"{subject} field '{field_name}' options must be non-empty strings or option "
                                    "objects with value/label"
                                )
                            ),
                        )
                    )
                if widget in {"select", "radio", "checkbox_group"} and not normalized_options:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"{subject} field '{field_name}' options must be non-empty for {widget}",
                        )
                    )
        elif widget in {"select", "radio", "checkbox_group"} and not has_options_template:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' options or optionsTemplate are required for {widget}",
                )
            )

        allow_custom = item.get("allow_custom", item.get("allowCustom", None))
        if allow_custom is not None and not isinstance(allow_custom, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' allowCustom must be boolean",
                )
            )
        if widget != "tag_selector" and allow_custom is True:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' allowCustom is only supported for tag_selector",
                )
            )

        value_template = item.get("value_template", item.get("valueTemplate", ""))
        if value_template is not None and not isinstance(value_template, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' valueTemplate must be a string",
                )
            )
