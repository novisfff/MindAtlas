from __future__ import annotations

import re
from typing import Sequence

from app.assistant.workflow.validation.contracts import _SYS_FIELDS
from app.assistant.workflow.validation.helpers import (
    extract_container_body,
    resolve_start_env_var_contract,
)
from app.assistant.workflow.validation.models import ValidationError
from app.assistant.workflow.validation.rules.code_executor_rules import (
    validate_code_executor_node_config,
)
from app.assistant.workflow.validation.rules.common import cfg_get
from app.assistant.workflow.validation.rules.human_in_loop_rules import (
    validate_human_in_loop_node_config,
)
from app.assistant.workflow.validation.rules.http_request_rules import (
    validate_http_request_node_config,
)
from app.assistant.workflow.validation.rules.if_else_rules import (
    normalize_if_else_config,
)
from app.assistant.workflow.validation.rules.variable_assign_rules import (
    validate_variable_assign_node_config,
)


def resolve_compile_start_env_var_types(nodes: Sequence) -> dict[str, str]:
    start_env_var_types: dict[str, str] = {}
    for n in nodes:
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        if ntype != "start":
            continue
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        if not isinstance(cfg, dict):
            cfg = {}
        start_env_var_types, _ = resolve_start_env_var_contract(cfg)
        break
    return start_env_var_types


def _validate_compile_tool_reference(
    *,
    node_id: str | None,
    tool_name: object,
    tool_names: set[str] | None,
    errors: list[ValidationError],
    not_found_message: str,
) -> None:
    if tool_names is None:
        return
    if isinstance(tool_name, str) and tool_name.strip() and tool_name not in tool_names:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=not_found_message.format(tool_name=tool_name),
            )
        )


def _validate_compile_llm_node_config(
    *,
    node_id: str | None,
    cfg: dict,
    errors: list[ValidationError],
) -> None:
    output_mode_raw = cfg_get(cfg, "output_mode", "outputMode", default="text")
    output_mode = str(output_mode_raw or "text").strip().lower()
    if output_mode == "json":
        output_mode = "structured"
    if output_mode not in {"text", "structured"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"Unsupported llm output_mode: {output_mode_raw}",
            )
        )

    output_fields = cfg_get(cfg, "output_fields", "outputFields")
    if output_mode == "structured" and (not isinstance(output_fields, list) or not output_fields):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="LLM structured mode requires output_fields",
            )
        )

    if output_fields is not None and output_fields != []:
        if not isinstance(output_fields, list):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="LLM node output_fields must be a list",
                )
            )
        else:
            for f in output_fields:
                if isinstance(f, dict):
                    name = f.get("name", "")
                    if not name or not re.fullmatch(r"[a-zA-Z0-9_]+", str(name)):
                        errors.append(
                            ValidationError(
                                node_id=node_id,
                                message=f"Invalid output field name: {name}",
                            )
                        )


def _validate_compile_output_node_config(
    *,
    node_id: str | None,
    cfg: dict,
    errors: list[ValidationError],
) -> None:
    output_mode_raw = cfg_get(cfg, "output_mode", "outputMode", default="text")
    output_mode = str(output_mode_raw or "text").strip().lower()
    if output_mode == "json":
        output_mode = "structured"
    if output_mode not in {"text", "structured"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"Unsupported output output_mode: {output_mode_raw}",
            )
        )
        return

    text_template = cfg_get(cfg, "text_template", "textTemplate", default="")
    output_fields = cfg_get(cfg, "output_fields", "outputFields", default=None)
    if output_mode == "text":
        if text_template is not None and not isinstance(text_template, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="output text mode requires textTemplate to be a string",
                )
            )
    else:
        if not isinstance(output_fields, list) or not output_fields:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="output structured mode requires output_fields",
                )
            )
        else:
            for f in output_fields:
                if not isinstance(f, dict):
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message="output output_fields items must be objects",
                        )
                    )
                    continue
                name = str(f.get("name", "") or "").strip()
                if not name or not re.fullmatch(r"[a-zA-Z0-9_]+", name):
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Invalid output field name: {name}",
                        )
                    )
                if not isinstance(f.get("value"), str):
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"output field '{name or '<unknown>'}' requires string value",
                        )
                    )


def _validate_compile_if_else_node_conditions(
    *,
    node_id: str | None,
    cfg: dict,
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    normalized = normalize_if_else_config(cfg)
    branches = normalized.get("branches", [])
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        for cond in (branch.get("conditions") or []):
            if not isinstance(cond, dict):
                continue
            var = str(cond.get("variable") or "").strip()
            if not var:
                continue
            if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"Invalid condition variable path: {var}",
                    )
                )
                continue
            if var.startswith("sys."):
                sys_field = var.split(".", 1)[1]
                if sys_field not in _SYS_FIELDS:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Unsupported sys variable in condition: {var}",
                        )
                    )
            if var.startswith("env."):
                env_name = var.split(".", 1)[1]
                if env_name not in start_env_var_types:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Unknown env variable in condition: {var}",
                        )
                    )


def _validate_compile_body_llm_node(
    *,
    parent_node_id: str | None,
    parent_node_type: str,
    body_node_id: str,
    body_cfg: dict,
    errors: list[ValidationError],
) -> None:
    body_output_mode_raw = cfg_get(body_cfg, "output_mode", "outputMode", default="text")
    body_output_mode = str(body_output_mode_raw or "text").strip().lower()
    if body_output_mode == "json":
        body_output_mode = "structured"
    if body_output_mode not in {"text", "structured"}:
        errors.append(
            ValidationError(
                node_id=parent_node_id,
                message=(
                    f"{parent_node_type} body node '{body_node_id}' has unsupported llm output_mode: "
                    f"{body_output_mode_raw}"
                ),
            )
        )

    body_output_fields = cfg_get(body_cfg, "output_fields", "outputFields")
    if body_output_mode == "structured" and (
        not isinstance(body_output_fields, list) or not body_output_fields
    ):
        errors.append(
            ValidationError(
                node_id=parent_node_id,
                message=(
                    f"{parent_node_type} body node '{body_node_id}' structured mode requires output_fields"
                ),
            )
        )


def _validate_compile_body_if_else_node_conditions(
    *,
    parent_node_id: str | None,
    parent_node_type: str,
    body_node_id: str,
    body_cfg: dict,
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    normalized = normalize_if_else_config(body_cfg)
    branches = normalized.get("branches", [])
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        for cond in (branch.get("conditions") or []):
            if not isinstance(cond, dict):
                continue
            var = str(cond.get("variable") or "").strip()
            if not var:
                continue
            if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                errors.append(
                    ValidationError(
                        node_id=parent_node_id,
                        message=(
                            f"{parent_node_type} body node '{body_node_id}' has invalid "
                            f"condition variable path: {var}"
                        ),
                    )
                )
                continue
            if var.startswith("sys."):
                sys_field = var.split(".", 1)[1]
                if sys_field not in _SYS_FIELDS:
                    errors.append(
                        ValidationError(
                            node_id=parent_node_id,
                            message=(
                                f"{parent_node_type} body node '{body_node_id}' uses unsupported "
                                f"sys variable in condition: {var}"
                            ),
                        )
                    )
            if var.startswith("env."):
                env_name = var.split(".", 1)[1]
                if env_name not in start_env_var_types:
                    errors.append(
                        ValidationError(
                            node_id=parent_node_id,
                            message=(
                                f"{parent_node_type} body node '{body_node_id}' uses unknown "
                                f"env variable in condition: {var}"
                            ),
                        )
                    )


def _validate_compile_container_body_nodes(
    *,
    node_id: str | None,
    node_type: str,
    cfg: dict,
    tool_names: set[str] | None,
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    body_nodes, _ = extract_container_body(cfg)
    for raw_body_node in body_nodes:
        if not isinstance(raw_body_node, dict):
            continue
        body_node_id = str(raw_body_node.get("node_id", raw_body_node.get("nodeId", "")) or "").strip() or "<unknown>"
        body_type = str(raw_body_node.get("node_type", raw_body_node.get("nodeType", "")) or "").strip()
        body_cfg = raw_body_node.get("config")
        if not isinstance(body_cfg, dict):
            body_cfg = {}

        if body_type == "tool":
            _validate_compile_tool_reference(
                node_id=node_id,
                tool_name=cfg_get(body_cfg, "tool_name", "toolName", default=""),
                tool_names=tool_names,
                errors=errors,
                not_found_message=f"{node_type} body node '{body_node_id}' references unknown tool: {{tool_name}}",
            )

        if body_type == "llm":
            _validate_compile_body_llm_node(
                parent_node_id=node_id,
                parent_node_type=node_type,
                body_node_id=body_node_id,
                body_cfg=body_cfg,
                errors=errors,
            )

        if body_type == "if_else":
            _validate_compile_body_if_else_node_conditions(
                parent_node_id=node_id,
                parent_node_type=node_type,
                body_node_id=body_node_id,
                body_cfg=body_cfg,
                start_env_var_types=start_env_var_types,
                errors=errors,
            )

        if body_type == "code_executor":
            validate_code_executor_node_config(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{node_type} body node '{body_node_id}' code_executor",
                validate_timeout=True,
            )
        if body_type == "http_request":
            validate_http_request_node_config(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{node_type} body node '{body_node_id}' http_request",
            )
        if body_type == "variable_assign":
            validate_variable_assign_node_config(
                node_id=node_id,
                cfg=body_cfg,
                env_var_types=start_env_var_types,
                errors=errors,
                subject=f"{node_type} body node '{body_node_id}' variable_assign",
            )
        if body_type == "human_in_loop":
            validate_human_in_loop_node_config(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{node_type} body node '{body_node_id}' human_in_loop",
            )


def validate_compile_node(
    *,
    node_id: str | None,
    node_type: str | None,
    cfg: dict,
    tool_names: set[str] | None,
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    if node_type == "tool":
        _validate_compile_tool_reference(
            node_id=node_id,
            tool_name=cfg_get(cfg, "tool_name", "toolName", default=""),
            tool_names=tool_names,
            errors=errors,
            not_found_message="Tool node references unknown tool: {tool_name}",
        )

    if node_type == "llm":
        _validate_compile_llm_node_config(
            node_id=node_id,
            cfg=cfg,
            errors=errors,
        )

    if node_type == "output":
        _validate_compile_output_node_config(
            node_id=node_id,
            cfg=cfg,
            errors=errors,
        )

    if node_type == "if_else":
        _validate_compile_if_else_node_conditions(
            node_id=node_id,
            cfg=cfg,
            start_env_var_types=start_env_var_types,
            errors=errors,
        )

    if node_type == "code_executor":
        validate_code_executor_node_config(
            node_id=node_id,
            cfg=cfg,
            errors=errors,
            subject="code_executor",
            validate_timeout=True,
        )
    if node_type == "http_request":
        validate_http_request_node_config(
            node_id=node_id,
            cfg=cfg,
            errors=errors,
            subject="http_request",
        )
    if node_type == "variable_assign":
        validate_variable_assign_node_config(
            node_id=node_id,
            cfg=cfg,
            env_var_types=start_env_var_types,
            errors=errors,
            subject="variable_assign",
        )
    if node_type == "human_in_loop":
        validate_human_in_loop_node_config(
            node_id=node_id,
            cfg=cfg,
            errors=errors,
            subject="human_in_loop",
        )

    if node_type in {"iteration", "loop"}:
        _validate_compile_container_body_nodes(
            node_id=node_id,
            node_type=node_type,
            cfg=cfg,
            tool_names=tool_names,
            start_env_var_types=start_env_var_types,
            errors=errors,
        )
