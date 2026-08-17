"""Pure, code-owned binding schema for the gateway-owned ``create_entry`` tool.

This module intentionally has no imports from the tool registry or capability
package.  The create-entry contract is needed while those packages are still
being imported (the worker compatibility constants are evaluated at import
time), so the frozen schema must have a dependency-free source of truth.
"""

from __future__ import annotations

from app.assistant.domain.contracts import ToolParamContract
from app.assistant.domain.digests import JsonValue
from app.assistant.domain.json_schema import tool_params_to_binding_schema


_CREATE_ENTRY_INPUT_PARAMS: tuple[ToolParamContract, ...] = (
    ToolParamContract(name="title", param_type="string"),
    ToolParamContract(name="summary", param_type="string"),
    ToolParamContract(name="content", param_type="string"),
    ToolParamContract(name="type_code", param_type="string"),
    ToolParamContract(name="tags", param_type="array"),
    ToolParamContract(name="time_mode", param_type="string"),
    ToolParamContract(name="time_at", param_type="string"),
    ToolParamContract(name="time_from", param_type="string"),
    ToolParamContract(name="time_to", param_type="string"),
)

_CREATE_ENTRY_OUTPUT_PARAMS: tuple[ToolParamContract, ...] = (
    ToolParamContract(
        name="id", param_type="string", description="新建记录 UUID。"
    ),
    ToolParamContract(
        name="title", param_type="string", description="最终写入的标题。"
    ),
    ToolParamContract(
        name="summary", param_type="string", description="最终写入的摘要。"
    ),
    ToolParamContract(
        name="type", param_type="string", description="记录类型名称。"
    ),
    ToolParamContract(
        name="type_code", param_type="string", description="记录类型编码。"
    ),
    ToolParamContract(
        name="tags", param_type="array", description="标签名称数组。"
    ),
    ToolParamContract(
        name="time_mode",
        param_type="string",
        description="时间模式（POINT/RANGE）。",
    ),
    ToolParamContract(
        name="time_at",
        param_type="string",
        description="POINT 模式日期（YYYY-MM-DD 或 null）。",
    ),
    ToolParamContract(
        name="time_from",
        param_type="string",
        description="RANGE 起始日期（YYYY-MM-DD 或 null）。",
    ),
    ToolParamContract(
        name="time_to",
        param_type="string",
        description="RANGE 结束日期（YYYY-MM-DD 或 null）。",
    ),
    ToolParamContract(
        name="created_at", param_type="string", description="创建时间（ISO8601）。"
    ),
    ToolParamContract(
        name="updated_at", param_type="string", description="更新时间（ISO8601）。"
    ),
)


def create_entry_binding_schemas() -> tuple[
    dict[str, JsonValue], dict[str, JsonValue]
]:
    """Return the canonical input and output binding schemas."""
    return (
        tool_params_to_binding_schema(
            _CREATE_ENTRY_INPUT_PARAMS, require_object_root=True
        ),
        tool_params_to_binding_schema(
            _CREATE_ENTRY_OUTPUT_PARAMS, require_object_root=True
        ),
    )


__all__ = ["create_entry_binding_schemas"]
