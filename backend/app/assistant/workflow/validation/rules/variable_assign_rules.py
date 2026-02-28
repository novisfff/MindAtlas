from __future__ import annotations

from app.assistant.workflow.validation.contracts import (
    _START_INPUT_FIELD_NAME_RE,
)
from app.assistant.workflow.validation.models import ValidationError
from app.assistant.workflow.validation.rules.common import cfg_get

def validate_variable_assign_node_config(
    *,
    node_id: str,
    cfg: dict,
    env_var_types: dict[str, str],
    errors: list[ValidationError],
    subject: str,
) -> None:
    variable_name_raw = cfg_get(cfg, "variable_name", "variableName", default="")
    variable_name = str(variable_name_raw or "").strip()
    if not variable_name:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} variableName is required",
            )
        )
        return
    if not _START_INPUT_FIELD_NAME_RE.fullmatch(variable_name):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} variableName is invalid: {variable_name_raw}",
            )
        )
        return
    if variable_name not in env_var_types:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} variable '{variable_name}' is not defined in start sessionVars",
            )
        )
        return

    operation_raw = cfg_get(cfg, "operation", default="set")
    operation = str(operation_raw or "set").strip().lower()
    if operation not in {"set", "increment", "append", "clear"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} operation is invalid: {operation_raw}",
            )
        )
        return

    value_template = cfg_get(cfg, "value_template", "valueTemplate", default=None)
    if operation != "clear" and (not isinstance(value_template, str) or not value_template.strip()):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} valueTemplate is required and must be a string",
            )
        )

    var_type = env_var_types.get(variable_name)
    if operation == "increment" and var_type not in {"number", "integer"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} increment supports only number/integer variable, "
                    f"but '{variable_name}' is {var_type}"
                ),
            )
        )
    if operation == "append" and var_type not in {"string", "array"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} append supports only string/array variable, "
                    f"but '{variable_name}' is {var_type}"
                ),
            )
        )
