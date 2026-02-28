"""Workflow DAG topology validator."""
from __future__ import annotations

from typing import Sequence

from app.assistant.workflow.validation.models import ValidationError, ValidationResult
from app.assistant.workflow.validation.rules.compile_rules import (
    resolve_compile_start_env_var_types as _resolve_compile_start_env_var_types,
    validate_compile_node as _validate_compile_node,
)
from app.assistant.workflow.validation.rules.context_rules import (
    build_validation_context as _build_validation_context,
    validate_template_refs as _validate_template_refs,
)
from app.assistant.workflow.validation.rules.parallel_rules import (
    validate_parallel_branches as _validate_parallel_branches_rule,
)
from app.assistant.workflow.validation.rules.save_rules import (
    validate_node_configs as _validate_node_configs,
)


def validate_workflow(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    """Validate workflow DAG topology."""
    errors: list[ValidationError] = []
    ctx = _build_validation_context(nodes=nodes, edges=edges, errors=errors)
    _validate_template_refs(ctx=ctx, errors=errors)
    _validate_node_configs(ctx=ctx, errors=errors)
    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_workflow_compile(
    nodes: Sequence,
    edges: Sequence,
    tool_names: set[str] | None = None,
    require_output_node: bool = True,
) -> ValidationResult:
    """Extended validation for compilation (Task 13.2).

    Checks tool_name existence, output_fields format, condition expressions.
    """
    result = validate_workflow(nodes, edges)
    errors = list(result.errors)
    if not require_output_node:
        errors = [
            err
            for err in errors
            if err.message != "Must have at least one output node"
            and err.message != "Must have exactly one output node"
            and err.message != "Only one output node is allowed"
        ]
    start_env_var_types = _resolve_compile_start_env_var_types(nodes)

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        if not isinstance(cfg, dict):
            cfg = {}
        _validate_compile_node(
            node_id=nid,
            node_type=ntype,
            cfg=cfg,
            tool_names=tool_names,
            start_env_var_types=start_env_var_types,
            errors=errors,
        )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_parallel_branches(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    return _validate_parallel_branches_rule(nodes=nodes, edges=edges)
