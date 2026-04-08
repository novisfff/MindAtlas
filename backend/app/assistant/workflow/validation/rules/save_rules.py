from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from app.assistant.workflow.validation.contracts import (
    _IF_ELSE_ALL_OPERATORS,
    _IF_ELSE_HANDLE_RE,
    _OUTPUT_FIELD_NAME_RE,
    _OUTPUT_FIELD_TYPES,
    _SYS_FIELDS,
)
from app.assistant.workflow.validation.helpers import (
    extract_container_body,
    iter_config_template_texts,
    validate_container_subflow,
    validate_output_fields_config,
)
from app.assistant.workflow.validation.models import ValidationError
from app.assistant.workflow.validation.rules.code_executor_rules import (
    validate_code_executor_node_config,
)
from app.assistant.workflow.validation.rules.common import (
    cfg_get,
    cfg_str_list,
)
from app.assistant.workflow.validation.rules.context_rules import ValidationContext
from app.assistant.workflow.validation.rules.human_in_loop_rules import (
    validate_human_in_loop_node_config,
)
from app.assistant.workflow.validation.rules.http_request_rules import (
    validate_http_request_node_config,
)
from app.assistant.workflow.validation.rules.if_else_rules import (
    normalize_if_else_config,
    normalize_if_else_handle,
    normalize_if_else_operator,
)
from app.assistant.workflow.validation.rules.variable_assign_rules import (
    validate_variable_assign_node_config,
)


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")
_AGENT_KNOWLEDGE_MODES = {"naive", "local", "global", "hybrid", "mix"}
_INTERNAL_AGENT_KB_TOOL_NAME = "kb_search"


def _validate_human_in_loop_handles(
    *,
    node_id: str,
    out_handles: list[str],
    errors: list[ValidationError],
    subject: str,
) -> None:
    expected_handles = {"approved", "rejected"}
    for handle in expected_handles:
        count = out_handles.count(handle)
        if count != 1:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} handle '{handle}' must map to exactly one outgoing edge",
                )
            )
    for handle in out_handles:
        if handle not in expected_handles:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} has unknown outgoing handle: {handle}",
                )
            )


def _validate_container_body(
    *,
    node_id: str,
    container_type: str,
    cfg: dict,
    start_allowed_fields: set[str],
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    body_nodes, body_edges = extract_container_body(cfg)
    body_node_map, _, body_topo = validate_container_subflow(
        node_id, container_type, body_nodes, body_edges, errors
    )
    body_topo_index = {body_node_id: index for index, body_node_id in enumerate(body_topo)}
    body_out_handles: dict[str, list[str]] = defaultdict(list)
    for raw_edge in body_edges:
        if not isinstance(raw_edge, dict):
            continue
        source = str(raw_edge.get("source_node_id", raw_edge.get("sourceNodeId", "")) or "").strip()
        if source not in body_node_map:
            continue
        handle = str(raw_edge.get("source_handle", raw_edge.get("sourceHandle", "output")) or "output").strip().lower()
        body_out_handles[source].append(handle)

    for body_node_id, raw_body_node in body_node_map.items():
        body_type = str(raw_body_node.get("node_type", raw_body_node.get("nodeType", "")) or "").strip()
        body_cfg = raw_body_node.get("config")
        if not isinstance(body_cfg, dict):
            continue
        if body_type == "code_executor":
            validate_code_executor_node_config(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}' code_executor",
                validate_timeout=True,
            )
        if body_type == "http_request":
            validate_http_request_node_config(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}' http_request",
            )
        if body_type == "variable_assign":
            validate_variable_assign_node_config(
                node_id=node_id,
                cfg=body_cfg,
                env_var_types=start_env_var_types,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}' variable_assign",
            )
        if body_type == "human_in_loop":
            validate_human_in_loop_node_config(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}' human_in_loop",
            )
            _validate_human_in_loop_handles(
                node_id=node_id,
                out_handles=body_out_handles.get(body_node_id, []),
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}'",
            )
        if body_type == "workflow_call":
            _validate_workflow_call_node(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}' workflow_call",
            )
        if body_type in {"llm", "parameter_extractor", "agent"}:
            _validate_model_source_for_llm_like_nodes(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}'",
            )
        if body_type == "agent":
            _validate_agent_node(
                node_id=node_id,
                cfg=body_cfg,
                errors=errors,
                subject=f"{container_type} body node '{body_node_id}' agent",
            )
        for text in iter_config_template_texts(body_cfg):
            for m in _VAR_RE.finditer(text):
                ref_node = m.group(1)
                ref_field = m.group(2)
                if ref_node == "sys":
                    if ref_field not in _SYS_FIELDS:
                        errors.append(
                            ValidationError(
                                node_id=node_id,
                                message=f"{container_type} body node '{body_node_id}' references unsupported sys variable: sys.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "start":
                    if ref_field not in start_allowed_fields:
                        errors.append(
                            ValidationError(
                                node_id=node_id,
                                message=f"{container_type} body node '{body_node_id}' references unsupported start field: start.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "env":
                    if ref_field not in start_env_var_types:
                        errors.append(
                            ValidationError(
                                node_id=node_id,
                                message=f"{container_type} body node '{body_node_id}' references unknown env variable: env.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "container":
                    continue
                if ref_node not in body_node_map:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"{container_type} body node '{body_node_id}' references unknown node: {ref_node}",
                        )
                    )
                    continue
                if (
                    body_node_id in body_topo_index
                    and ref_node in body_topo_index
                    and body_topo_index[ref_node] >= body_topo_index[body_node_id]
                ):
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"{container_type} body node '{body_node_id}' references non-upstream node: {ref_node}",
                        )
                    )


def _validate_iteration_node(
    *,
    node_id: str,
    cfg: dict,
    start_allowed_fields: set[str],
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    input_source = cfg_get(cfg, "input_source", "inputSource", default=None)
    if not isinstance(input_source, str) or not input_source.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message="iteration inputSource is required and must be a string",
            )
        )
    output_variable = str(cfg_get(cfg, "output_variable", "outputVariable", default="") or "").strip()
    if not output_variable or not _OUTPUT_FIELD_NAME_RE.fullmatch(output_variable):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="iteration outputVariable must match [a-zA-Z0-9_]+",
            )
        )
    output_selector = cfg_get(cfg, "output_selector", "outputSelector", default=None)
    if not isinstance(output_selector, str) or not output_selector.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message="iteration outputSelector is required and must be a string",
            )
        )
    error_strategy = str(cfg_get(cfg, "error_strategy", "errorStrategy", default="fail_fast") or "fail_fast").strip().lower()
    if error_strategy not in {"fail_fast", "skip_item"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"iteration errorStrategy is invalid: {error_strategy}",
            )
        )
    _validate_container_body(
        node_id=node_id,
        container_type="iteration",
        cfg=cfg,
        start_allowed_fields=start_allowed_fields,
        start_env_var_types=start_env_var_types,
        errors=errors,
    )


def _validate_loop_node(
    *,
    node_id: str,
    cfg: dict,
    start_allowed_fields: set[str],
    start_env_var_types: dict[str, str],
    errors: list[ValidationError],
) -> None:
    max_iterations_raw = cfg_get(cfg, "max_iterations", "maxIterations", default=10)
    try:
        max_iterations = int(max_iterations_raw)
    except Exception:
        max_iterations = 0
    if max_iterations < 1 or max_iterations > 1000:
        errors.append(
            ValidationError(
                node_id=node_id,
                message="loop maxIterations must be between 1 and 1000",
            )
        )

    termination_logic = str(cfg_get(cfg, "termination_logic", "terminationLogic", default="and") or "and").strip().lower()
    if termination_logic not in {"and", "or"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"loop terminationLogic is invalid: {termination_logic}",
            )
        )

    initial_vars = cfg_get(cfg, "initial_vars", "initialVars", default=[])
    if initial_vars is not None and not isinstance(initial_vars, list):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="loop initialVars must be a list",
            )
        )
    elif isinstance(initial_vars, list):
        for item in initial_vars:
            if not isinstance(item, dict):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message="loop initialVars items must be objects",
                    )
                )
                continue
            name = str(item.get("name", "") or "").strip()
            if not name or not _OUTPUT_FIELD_NAME_RE.fullmatch(name):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"loop initialVars contains invalid variable name: {name}",
                    )
                )

    update_mappings = cfg_get(cfg, "update_mappings", "updateMappings", default=[])
    if update_mappings is not None and not isinstance(update_mappings, list):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="loop updateMappings must be a list",
            )
        )
    elif isinstance(update_mappings, list):
        for item in update_mappings:
            if not isinstance(item, dict):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message="loop updateMappings items must be objects",
                    )
                )
                continue
            name = str(item.get("name", "") or "").strip()
            value = item.get("value")
            if not name or not _OUTPUT_FIELD_NAME_RE.fullmatch(name):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"loop updateMappings contains invalid variable name: {name}",
                    )
                )
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"loop updateMappings variable '{name or '<empty>'}' requires string value",
                    )
                )

    termination_conditions = cfg_get(cfg, "termination_conditions", "terminationConditions", default=[])
    if termination_conditions is not None and not isinstance(termination_conditions, list):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="loop terminationConditions must be a list",
            )
        )
    elif isinstance(termination_conditions, list):
        for cond in termination_conditions:
            if not isinstance(cond, dict):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message="loop terminationConditions contains invalid condition item",
                    )
                )
                continue
            var = str(cond.get("variable", "") or "").strip()
            if not var:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message="loop terminationConditions requires variable",
                    )
                )
                continue
            if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"Invalid loop condition variable path: {var}",
                    )
                )
                continue
            if var.startswith("sys."):
                sys_field = var.split(".", 1)[1]
                if sys_field not in _SYS_FIELDS:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Unsupported sys variable in loop condition: {var}",
                        )
                    )
            if var.startswith("env."):
                env_name = var.split(".", 1)[1]
                if env_name not in start_env_var_types:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Unknown env variable in loop condition: {var}",
                        )
                    )

    _validate_container_body(
        node_id=node_id,
        container_type="loop",
        cfg=cfg,
        start_allowed_fields=start_allowed_fields,
        start_env_var_types=start_env_var_types,
        errors=errors,
    )


def _validate_tool_node(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
) -> None:
    tool_name = cfg_get(cfg, "tool_name", "toolName", default="")
    if not isinstance(tool_name, str) or not tool_name.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message="Tool node requires toolName",
            )
        )

    input_bindings = cfg_get(cfg, "input_bindings", "inputBindings")
    if input_bindings is None:
        errors.append(
            ValidationError(
                node_id=node_id,
                message="Tool node requires inputBindings; legacy argsFrom/argsTemplate are no longer supported",
            )
        )
        return
    if not isinstance(input_bindings, dict):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="tool.inputBindings must be an object",
            )
        )
        return
    for key, value in input_bindings.items():
        if not isinstance(key, str) or not key.strip():
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="tool.inputBindings contains empty parameter name",
                )
            )
            continue
        if not isinstance(value, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"tool.inputBindings['{key}'] must be a string",
                )
            )


def _validate_if_else_node(
    *,
    node_id: str,
    cfg: dict,
    ctx: ValidationContext,
    errors: list[ValidationError],
) -> None:
    normalized = normalize_if_else_config(cfg)
    branches = normalized.get("branches")
    else_handle = str(normalized.get("else_handle") or "else")

    if not isinstance(branches, list) or not branches:
        errors.append(
            ValidationError(
                node_id=node_id,
                message="if_else requires at least one IF/ELIF branch",
            )
        )
        return

    branch_ids: list[str] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        branch_id = normalize_if_else_handle(branch.get("id"))
        if not branch_id or not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"if_else branch has invalid id: {branch.get('id')}",
                )
            )
            continue
        if branch_id in branch_ids:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"if_else branch id duplicated: {branch_id}",
                )
            )
        branch_ids.append(branch_id)

        logic = str(branch.get("logic") or "and").strip().lower()
        if logic not in {"and", "or"}:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"if_else branch '{branch_id}' has invalid logic: {logic}",
                )
            )

        conditions = branch.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"if_else branch '{branch_id}' requires at least one condition",
                )
            )
            continue

        for cond in conditions:
            if not isinstance(cond, dict):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"if_else branch '{branch_id}' contains invalid condition item",
                    )
                )
                continue
            var = str(cond.get("variable") or "").strip()
            if not var:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"if_else branch '{branch_id}' contains empty condition variable",
                    )
                )
                continue
            if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"Invalid condition variable path: {var}",
                    )
                )
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
                if env_name not in ctx.start_env_var_types:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Unknown env variable in condition: {var}",
                        )
                    )

            raw_op = str(cond.get("operator") or "").strip().lower()
            op = normalize_if_else_operator(raw_op)
            if op not in _IF_ELSE_ALL_OPERATORS:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"Unsupported condition operator: {raw_op or cond.get('operator')}",
                    )
                )
                continue

            if op not in {"is_empty", "is_not_empty"}:
                raw_value = cond.get("value")
                value = "" if raw_value is None else str(raw_value)
                if not value.strip():
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"Condition operator '{op}' requires value",
                        )
                    )

    normalized_out_handles = [normalize_if_else_handle(handle) for handle in ctx.out_handles.get(node_id, [])]
    if normalized_out_handles.count(else_handle) != 1:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"if_else requires exactly one '{else_handle}' outgoing edge",
            )
        )

    expected_handles = set(branch_ids)
    expected_handles.add(else_handle)
    for handle in expected_handles:
        count = normalized_out_handles.count(handle)
        if count != 1:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"if_else handle '{handle}' must map to exactly one outgoing edge",
                )
            )

    for handle in normalized_out_handles:
        if handle not in expected_handles:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"if_else has unknown outgoing handle: {handle}",
                )
            )


def _validate_human_in_loop_node(
    *,
    node_id: str,
    cfg: dict,
    ctx: ValidationContext,
    errors: list[ValidationError],
) -> None:
    validate_human_in_loop_node_config(
        node_id=node_id,
        cfg=cfg,
        errors=errors,
        subject="human_in_loop",
    )
    normalized_out_handles = [str(handle or "").strip().lower() for handle in ctx.out_handles.get(node_id, [])]
    _validate_human_in_loop_handles(
        node_id=node_id,
        out_handles=normalized_out_handles,
        errors=errors,
        subject="human_in_loop",
    )


def _validate_output_node(
    *,
    node_id: str,
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
                message=f"Unsupported output outputMode: {output_mode_raw}",
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
        return

    if not isinstance(output_fields, list) or not output_fields:
        errors.append(
            ValidationError(
                node_id=node_id,
                message="output structured mode requires outputFields",
            )
        )
        return

    for field in output_fields:
        if not isinstance(field, dict):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="output outputFields items must be objects",
                )
            )
            continue

        field_name = str(field.get("name", "") or "").strip()
        if not field_name or not _OUTPUT_FIELD_NAME_RE.fullmatch(field_name):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"Invalid output field name: {field_name}",
                )
            )

        value = field.get("value", None)
        if not isinstance(value, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"output field '{field_name or '<unknown>'}' requires string value template",
                )
            )

        field_type_raw = field.get("type", "string")
        field_type = str(field_type_raw or "string").strip().lower() or "string"
        if field_type not in _OUTPUT_FIELD_TYPES:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"Invalid output field type: {field_type_raw}",
                )
            )

        nullable_raw = field.get("nullable", None)
        if nullable_raw is not None and not isinstance(nullable_raw, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"output field '{field_name or '<unknown>'}' nullable must be boolean",
                )
            )

        items_type_raw = field.get("items_type", field.get("itemsType"))
        items_type = str(items_type_raw or "").strip().lower()
        if field_type == "array":
            if not items_type:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"output field '{field_name}' type=array requires itemsType",
                    )
                )
            elif items_type not in _OUTPUT_FIELD_TYPES or items_type == "array":
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"output field '{field_name}' has invalid itemsType: {items_type_raw}",
                    )
                )

        enum_value = field.get("enum")
        if enum_value is not None:
            if not isinstance(enum_value, list) or any(
                not isinstance(item, str) or not item.strip() for item in enum_value
            ):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"output field '{field_name}' enum must be non-empty string list",
                    )
                )


def _validate_llm_node(
    *,
    node_id: str,
    cfg: dict,
    ctx: ValidationContext,
    errors: list[ValidationError],
) -> None:
    knowledge_source_ids = cfg_str_list(cfg, "knowledge_source_node_ids", "knowledgeSourceNodeIds")
    raw_inject_mode = str(
        cfg_get(cfg, "knowledge_inject_mode", "knowledgeInjectMode", default="references_only")
        or "references_only"
    ).strip().lower()
    if raw_inject_mode not in {"references_only", "full_payload"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"Unsupported llm knowledgeInjectMode: {raw_inject_mode}",
            )
        )
    raw_max_refs = cfg_get(cfg, "knowledge_max_refs", "knowledgeMaxRefs", default=None)
    if raw_max_refs is not None and str(raw_max_refs).strip() != "":
        try:
            max_refs_val = int(raw_max_refs)
        except Exception:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="llm knowledgeMaxRefs must be an integer",
                )
            )
        else:
            if max_refs_val < 1 or max_refs_val > 100:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message="llm knowledgeMaxRefs must be between 1 and 100",
                    )
                )

    for source_id in knowledge_source_ids:
        if source_id not in ctx.node_ids:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"llm knowledge source node not found: {source_id}",
                )
            )
            continue
        if ctx.type_map.get(source_id) != "knowledge_retrieval":
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"llm knowledge source must be knowledge_retrieval node: {source_id}",
                )
            )
        if (
            node_id in ctx.topo_index
            and source_id in ctx.topo_index
            and ctx.topo_index[source_id] >= ctx.topo_index[node_id]
        ):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"llm knowledge source must be upstream node: {source_id}",
                )
            )


def _validate_model_source_for_llm_like_nodes(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str = "node",
) -> None:
    raw_model_source = cfg_get(cfg, "model_source", "modelSource", default=None)
    model_source = str(raw_model_source or "default").strip().lower() or "default"
    if model_source not in {"default", "custom"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"Unsupported {subject} modelSource: {raw_model_source}",
            )
        )

    raw_model_id = cfg_get(cfg, "model_id", "modelId", default=None)
    model_id = str(raw_model_id).strip() if raw_model_id is not None else ""
    if model_source == "custom" and not model_id:
        message = "custom modelSource requires modelId" if subject == "node" else f"{subject} custom modelSource requires modelId"
        errors.append(
            ValidationError(
                node_id=node_id,
                message=message,
            )
        )
    if model_source == "default" and model_id:
        message = "default modelSource must not provide modelId" if subject == "node" else f"{subject} default modelSource must not provide modelId"
        errors.append(
            ValidationError(
                node_id=node_id,
                message=message,
            )
        )
    if model_id:
        try:
            UUID(model_id)
        except Exception:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"Invalid {subject} modelId (must be UUID): {model_id}",
                )
            )


def _validate_agent_node(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str = "agent",
) -> None:
    system_prompt = cfg_get(cfg, "system_prompt", "systemPrompt", default=None)
    if system_prompt is not None and not isinstance(system_prompt, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} systemPrompt must be a string",
            )
        )

    user_input = cfg_get(cfg, "user_input", "userInput", default=None)
    if user_input is not None and not isinstance(user_input, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} userInput must be a string",
            )
        )

    tool_names_raw = cfg_get(cfg, "tool_names", "toolNames", default=None)
    tool_names: list[str] = []
    if tool_names_raw is None:
        tool_names = []
    elif not isinstance(tool_names_raw, list):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} toolNames must be a string array",
            )
        )
    else:
        for idx, item in enumerate(tool_names_raw, start=1):
            if not isinstance(item, str) or not item.strip():
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} toolNames[{idx}] must be a non-empty string",
                    )
                )
                continue
            tool_name = item.strip()
            tool_names.append(tool_name)
            if tool_name == _INTERNAL_AGENT_KB_TOOL_NAME:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} toolNames must not include kb_search; use knowledgeEnabled instead",
                    )
                )

    knowledge_enabled_raw = cfg_get(cfg, "knowledge_enabled", "knowledgeEnabled", default=None)
    knowledge_enabled = False
    if knowledge_enabled_raw is None:
        knowledge_enabled = False
    elif isinstance(knowledge_enabled_raw, bool):
        knowledge_enabled = knowledge_enabled_raw
    else:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} knowledgeEnabled must be a boolean",
            )
        )

    knowledge_mode_raw = cfg_get(cfg, "knowledge_mode", "knowledgeMode", default=None)
    if knowledge_mode_raw is not None and str(knowledge_mode_raw).strip():
        knowledge_mode = str(knowledge_mode_raw).strip().lower()
        if knowledge_mode not in _AGENT_KNOWLEDGE_MODES:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} knowledgeMode is invalid: {knowledge_mode_raw}",
                )
            )

    knowledge_top_k_raw = cfg_get(cfg, "knowledge_top_k", "knowledgeTopK", default=None)
    if knowledge_top_k_raw is not None and str(knowledge_top_k_raw).strip():
        try:
            knowledge_top_k = int(knowledge_top_k_raw)
        except Exception:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} knowledgeTopK must be an integer between 1 and 50",
                )
            )
        else:
            if knowledge_top_k < 1 or knowledge_top_k > 50:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} knowledgeTopK must be between 1 and 50",
                    )
                )

    if not tool_names and not knowledge_enabled:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} requires at least one toolNames entry or knowledgeEnabled=true",
            )
        )

    max_iterations_raw = cfg_get(cfg, "max_iterations", "maxIterations", default=12)
    try:
        max_iterations = int(max_iterations_raw)
    except Exception:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} maxIterations must be an integer between 1 and 20",
            )
        )
    else:
        if max_iterations < 1 or max_iterations > 20:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} maxIterations must be between 1 and 20",
                )
            )


def _validate_parameter_extractor_node(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
) -> None:
    input_content = cfg_get(cfg, "input_content", "inputContent", default=None)
    if input_content is not None and not isinstance(input_content, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message="parameter_extractor inputContent must be a string",
            )
        )

    validate_output_fields_config(
        node_id=node_id,
        node_type="parameter_extractor",
        output_fields=cfg_get(cfg, "output_fields", "outputFields", default=None),
        errors=errors,
        required=True,
    )


def _validate_workflow_call_node(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str = "workflow_call",
) -> None:
    target_workflow_id = cfg_get(cfg, "target_workflow_id", "targetWorkflowId", default=None)
    if target_workflow_id is None or not str(target_workflow_id).strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} targetWorkflowId is required",
            )
        )
    else:
        try:
            UUID(str(target_workflow_id).strip())
        except Exception:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} targetWorkflowId must be a UUID",
                )
            )

    binding_mode_raw = cfg_get(cfg, "binding_mode", "bindingMode", default="pinned")
    binding_mode = str(binding_mode_raw or "pinned").strip().lower()
    if binding_mode not in {"pinned", "latest"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} bindingMode is invalid: {binding_mode_raw}",
            )
        )

    target_version_id = cfg_get(cfg, "target_published_version_id", "targetPublishedVersionId", default=None)
    if binding_mode == "pinned":
        if target_version_id is None or not str(target_version_id).strip():
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} targetPublishedVersionId is required when bindingMode=pinned",
                )
            )
        else:
            try:
                UUID(str(target_version_id).strip())
            except Exception:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} targetPublishedVersionId must be a UUID",
                    )
                )
    elif target_version_id is not None and str(target_version_id).strip():
        try:
            UUID(str(target_version_id).strip())
        except Exception:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} targetPublishedVersionId must be a UUID",
                )
            )

    input_bindings = cfg_get(cfg, "input_bindings", "inputBindings", default=None)
    if input_bindings is None:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} requires inputBindings",
            )
        )
    elif not isinstance(input_bindings, dict):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} inputBindings must be an object",
            )
        )
    else:
        for key, value in input_bindings.items():
            binding_key = str(key or "").strip()
            if not binding_key:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} inputBindings keys must be non-empty strings",
                    )
                )
                continue
            if value is not None and not isinstance(value, str):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} inputBindings['{binding_key}'] must be a string",
                    )
                )


def validate_node_configs(
    ctx: ValidationContext,
    errors: list[ValidationError],
) -> None:
    for nid in ctx.node_ids:
        cfg = ctx.config_map.get(nid, {})
        node_type = ctx.type_map.get(nid)

        if node_type == "tool":
            _validate_tool_node(node_id=nid, cfg=cfg, errors=errors)
        elif node_type == "if_else":
            _validate_if_else_node(node_id=nid, cfg=cfg, ctx=ctx, errors=errors)
        elif node_type == "human_in_loop":
            _validate_human_in_loop_node(node_id=nid, cfg=cfg, ctx=ctx, errors=errors)
        elif node_type == "workflow_call":
            _validate_workflow_call_node(node_id=nid, cfg=cfg, errors=errors)
        elif node_type == "output":
            _validate_output_node(node_id=nid, cfg=cfg, errors=errors)
        elif node_type == "llm":
            _validate_llm_node(node_id=nid, cfg=cfg, ctx=ctx, errors=errors)
            _validate_model_source_for_llm_like_nodes(node_id=nid, cfg=cfg, errors=errors)
        elif node_type == "parameter_extractor":
            _validate_model_source_for_llm_like_nodes(node_id=nid, cfg=cfg, errors=errors)
            _validate_parameter_extractor_node(node_id=nid, cfg=cfg, errors=errors)
        elif node_type == "agent":
            _validate_model_source_for_llm_like_nodes(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="agent",
            )
            _validate_agent_node(node_id=nid, cfg=cfg, errors=errors)
        elif node_type == "code_executor":
            validate_code_executor_node_config(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="code_executor",
                validate_timeout=True,
            )
        elif node_type == "http_request":
            validate_http_request_node_config(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="http_request",
            )
        elif node_type == "variable_assign":
            validate_variable_assign_node_config(
                node_id=nid,
                cfg=cfg,
                env_var_types=ctx.start_env_var_types,
                errors=errors,
                subject="variable_assign",
            )
        elif node_type == "iteration":
            _validate_iteration_node(
                node_id=nid,
                cfg=cfg,
                start_allowed_fields=ctx.start_allowed_fields,
                start_env_var_types=ctx.start_env_var_types,
                errors=errors,
            )
        elif node_type == "loop":
            _validate_loop_node(
                node_id=nid,
                cfg=cfg,
                start_allowed_fields=ctx.start_allowed_fields,
                start_env_var_types=ctx.start_env_var_types,
                errors=errors,
            )
