"""LangGraph 执行引擎 - 支持 agent_loop 与 workflow_dag 两种子图模式"""
from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from functools import lru_cache
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, Iterator, Literal, Optional

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session, sessionmaker
from typing_extensions import Annotated, TypedDict

from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id
from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skills.base import SkillDefinition
from app.assistant.tools._context import reset_current_db, set_current_db

logger = logging.getLogger(__name__)

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_OUTPUT_SINGLE_VAR_RE = re.compile(r"^\s*\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}\s*$")


# ==================== State Types (Task 2.1) ====================


class StepOutput(TypedDict, total=False):
    status: str                    # "ok" | "error"
    text: str                      # 文本形式结果
    raw: Any                       # json.loads 解析后的对象
    json_fields: dict[str, Any]    # analysis 步骤的 output_fields 解析结果
    allowed_fields: list[str]      # 允许被模板引用的字段名白名单
    tool_meta: dict | None         # tool 步骤的元信息


class AssistantState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    skill_name: str
    user_input: str
    kb_enabled: bool
    iteration_count: int
    metadata: dict                 # SSE 回调等运行时信息
    current_step: int
    step_outputs: dict[int, StepOutput]
    summary_trace: list[dict]


# ==================== Helpers ====================

_MAX_TEXT_LEN = 8000
KB_CITATION_INSTRUCTIONS = """## 引用标注（知识库问答）
当你使用 `kb_search` 返回的参考资料时，必须在相关句子末尾添加引用标注。

引用格式：
- 使用 `[^n]` 格式标注引用，n 为参考资料的编号
- 例如：根据记录显示[^1]，该项目于2024年启动[^2]。

重要约束：
- 只能引用 kb_search 返回结果中提供的编号，不要编造不存在的编号
- 不需要在回答末尾输出脚注定义，系统会自动处理
- 如果参考了某条资料，务必标注对应编号

工具使用要求：
- 当“知识库开关”启用时，系统会通过 `kb_search` 为你提供参考资料（UNTRUSTED）
- `kb_search` 返回结果里包含 `references`（编号）和召回内容；回答时严格按编号引用
"""


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _truncate(text: Any, max_len: int = _MAX_TEXT_LEN) -> str:
    s = text if isinstance(text, str) else _stringify(text)
    return s[:max_len] if len(s) > max_len else s


def _extract_json_object(content: str) -> dict[str, Any] | None:
    """Best-effort parse of a single JSON object from model output."""
    raw = (content or "").strip()
    if not raw:
        return None

    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1].strip()
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(raw[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
        return None


def _extract_single_template_reference(template: str) -> tuple[str, str] | None:
    matched = _OUTPUT_SINGLE_VAR_RE.fullmatch(template or "")
    if not matched:
        return None
    return matched.group(1), matched.group(2)


def _parse_output_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"invalid boolean value: {value}")


def _coerce_output_field_value(field_name: str, rendered_value: str, field_spec: dict[str, Any]) -> Any:
    raw_type = field_spec.get("type", "string")
    field_type = str(raw_type or "string").strip().lower() or "string"
    nullable = bool(field_spec.get("nullable", False))
    trimmed = rendered_value.strip()
    if nullable and trimmed == "":
        return None

    if field_type == "string":
        out_value: Any = rendered_value
    elif field_type == "number":
        out_value = float(trimmed)
    elif field_type == "integer":
        out_value = int(trimmed)
    elif field_type == "boolean":
        out_value = _parse_output_boolean(trimmed if trimmed else rendered_value)
    elif field_type == "object":
        parsed = json.loads(trimmed)
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        out_value = parsed
    elif field_type == "array":
        parsed = json.loads(trimmed)
        if not isinstance(parsed, list):
            raise ValueError("must be a JSON array")
        items_type_raw = field_spec.get("items_type", field_spec.get("itemsType", ""))
        items_type = str(items_type_raw or "").strip().lower()
        if items_type and items_type != "array":
            for idx, item in enumerate(parsed):
                if items_type == "string" and not isinstance(item, str):
                    raise ValueError(f"array item {idx} must be string")
                if items_type == "number" and (
                    not isinstance(item, (int, float)) or isinstance(item, bool)
                ):
                    raise ValueError(f"array item {idx} must be number")
                if items_type == "integer" and (
                    not isinstance(item, int) or isinstance(item, bool)
                ):
                    raise ValueError(f"array item {idx} must be integer")
                if items_type == "boolean" and not isinstance(item, bool):
                    raise ValueError(f"array item {idx} must be boolean")
                if items_type == "object" and not isinstance(item, dict):
                    raise ValueError(f"array item {idx} must be object")
        out_value = parsed
    else:
        raise ValueError(f"unsupported type: {field_type}")

    if out_value is None:
        return None

    enum_values = field_spec.get("enum")
    if isinstance(enum_values, list) and enum_values:
        if str(out_value) not in {str(item) for item in enum_values}:
            raise ValueError(f"value '{out_value}' not in enum")

    return out_value


def _emit(metadata: dict, event: str, **kwargs: Any) -> None:
    """通过 metadata 中的回调发射 SSE 事件。"""
    cb = metadata.get(event)
    if callable(cb):
        cb(**kwargs)


# ==================== DB Session Wrapper (Task 2.2) ====================


def _wrap_tool_with_db(tool: Any, db_bind: Any) -> Callable:
    """每次调用创建独立 session，执行后关闭。"""

    def wrapped(**args: Any) -> Any:
        session = sessionmaker(bind=db_bind)()
        token = set_current_db(session)
        try:
            tool_func = getattr(tool, "func", None)
            if callable(tool_func):
                return tool_func(**args)
            return tool.invoke(args)
        finally:
            session.close()
            reset_current_db(token)

    wrapped.__name__ = getattr(tool, "name", "tool")
    wrapped.__doc__ = getattr(tool, "description", "")
    return wrapped


# ==================== Template Variable Resolver (Task 2.3) ====================


def _resolve_template_vars(
    template: str,
    step_outputs: dict[int, StepOutput],
    user_input: str,
    current_step: int | None = None,
) -> str:
    """解析模板变量，从 step_outputs 生成变量值。

    支持的变量:
    - user_input
    - last_step_result / last_step_result_raw
    - step_N_result / step_N_result_raw
    - step_N_<field> (仅 allowed_fields 中的字段)
    """
    if not template:
        return ""

    # 找到最近的步骤输出
    last_step_no = max(step_outputs.keys()) if step_outputs else 0

    def _repl(match: re.Match) -> str:
        var = match.group(1)

        if var == "user_input":
            return _truncate(user_input)

        if var == "last_step_result":
            if last_step_no and last_step_no in step_outputs:
                return _truncate(step_outputs[last_step_no].get("text", ""))
            return ""

        if var == "last_step_result_raw":
            if last_step_no and last_step_no in step_outputs:
                raw = step_outputs[last_step_no].get("raw")
                return _truncate(_stringify(raw) if raw is not None else "")
            return ""

        # step_N_result
        m = re.fullmatch(r"step_(\d+)_result", var)
        if m:
            n = int(m.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            return _truncate(out.get("text", "")) if out else ""

        # step_N_result_raw
        m = re.fullmatch(r"step_(\d+)_result_raw", var)
        if m:
            n = int(m.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if out:
                raw = out.get("raw")
                return _truncate(_stringify(raw) if raw is not None else "")
            return ""

        # step_N_<field>
        m = re.fullmatch(r"step_(\d+)_([a-zA-Z0-9_]+)", var)
        if m:
            n = int(m.group(1))
            field = m.group(2)
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if not out:
                return ""
            allowed = out.get("allowed_fields", [])
            if field not in allowed:
                raise ValueError(f"Disallowed template field: {var}")
            val = out.get("json_fields", {}).get(field, "")
            return _truncate(_stringify(val) if not isinstance(val, str) else val)

        logger.warning("Unknown template variable: %s", var)
        return ""

    return _TEMPLATE_VAR_RE.sub(_repl, template)


def _resolve_json_template(
    template: str,
    step_outputs: dict[int, StepOutput],
    user_input: str,
    current_step: int | None = None,
    allowed_keys: set[str] | None = None,
) -> dict:
    """解析 JSON 模板，变量替换时保持类型。"""
    if not (template or "").strip():
        return {}

    last_step_no = max(step_outputs.keys()) if step_outputs else 0

    def _repl(match: re.Match) -> str:
        var = match.group(1)

        if var == "user_input":
            return json.dumps(user_input, ensure_ascii=False)

        if var == "last_step_result":
            if last_step_no and last_step_no in step_outputs:
                return json.dumps(step_outputs[last_step_no].get("text", ""), ensure_ascii=False)
            return json.dumps("", ensure_ascii=False)

        if var == "last_step_result_raw":
            if last_step_no and last_step_no in step_outputs:
                raw = step_outputs[last_step_no].get("raw")
                if raw is not None:
                    return json.dumps(raw, ensure_ascii=False, default=str)
            return json.dumps("", ensure_ascii=False)

        # step_N_result
        m2 = re.fullmatch(r"step_(\d+)_result", var)
        if m2:
            n = int(m2.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            return json.dumps(out.get("text", "") if out else "", ensure_ascii=False)

        # step_N_result_raw
        m2 = re.fullmatch(r"step_(\d+)_result_raw", var)
        if m2:
            n = int(m2.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if out:
                raw = out.get("raw")
                if raw is not None:
                    return json.dumps(raw, ensure_ascii=False, default=str)
            return json.dumps("", ensure_ascii=False)

        # step_N_<field>
        m2 = re.fullmatch(r"step_(\d+)_([a-zA-Z0-9_]+)", var)
        if m2:
            n = int(m2.group(1))
            field = m2.group(2)
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if not out:
                return json.dumps("", ensure_ascii=False)
            allowed = out.get("allowed_fields", [])
            if field not in allowed:
                raise ValueError(f"Disallowed template field: {var}")
            val = out.get("json_fields", {}).get(field, "")
            return json.dumps(val, ensure_ascii=False, default=str)

        logger.warning("Unknown json template variable: %s", var)
        return json.dumps("", ensure_ascii=False)

    rendered = _TEMPLATE_VAR_RE.sub(_repl, template)
    try:
        obj = json.loads(rendered)
        if not isinstance(obj, dict):
            return {}
        if allowed_keys is not None:
            return {k: v for k, v in obj.items() if k in allowed_keys}
        return obj
    except Exception as e:
        logger.warning("Failed to parse json template: %s", e)
        return {}


# ==================== Workflow DAG State & Helpers (Phase 2 - Task 14) ====================

_NODE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")
_IF_ELSE_HANDLE_RE = re.compile(r"[a-zA-Z0-9_]+")
_IF_ELSE_LEGACY_OPERATOR_MAP = {
    "equals": "is",
    "not_equals": "is_not",
}


def _normalize_config(cfg: dict | None) -> dict:
    """Normalize camelCase config keys to snake_case for engine consumption.

    System skill definitions and the workflow editor persist camelCase keys
    (e.g. toolName, systemPrompt), but node builders expect snake_case.
    """
    if not cfg:
        return {}
    import re as _re
    _camel_re = _re.compile(r"([a-z0-9])([A-Z])")
    out: dict = {}
    for k, v in cfg.items():
        snake = _camel_re.sub(r"\1_\2", k).lower()
        out[snake] = v
    return out


class NodeOutput(TypedDict, total=False):
    status: str
    text: str
    raw: Any
    json_fields: dict[str, Any]


def _merge_node_outputs(left: dict[str, NodeOutput], right: dict[str, NodeOutput]) -> dict[str, NodeOutput]:
    """Merge reducer for node_outputs: parallel branches each contribute their own keys."""
    merged = dict(left)
    merged.update(right)
    return merged


def _merge_trace(left: list[str], right: list[str]) -> list[str]:
    """Merge reducer for execution_trace: append new entries, deduplicate."""
    seen = set(left)
    merged = list(left)
    for item in right:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _merge_branch_decisions(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Merge reducer for branch_decisions: union of all decisions."""
    merged = dict(left)
    merged.update(right)
    return merged


class WorkflowState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    skill_name: str
    user_input: str
    kb_enabled: bool
    metadata: dict
    node_outputs: Annotated[dict[str, NodeOutput], _merge_node_outputs]
    execution_trace: Annotated[list[str], _merge_trace]
    branch_decisions: Annotated[dict[str, str], _merge_branch_decisions]
    sys_vars: dict[str, str]
    workflow_node_types: dict[str, str]
    node_llms: dict[str, Any]
    stream_output_enabled: bool
    output_stream_source_node_id: str


def _cfg_bool_value(cfg: dict[str, Any], *keys: str, default: bool = False) -> bool:
    raw = default
    for key in keys:
        if key in cfg:
            raw = cfg.get(key)
            break
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(raw)


def _cfg_int_value(
    cfg: dict[str, Any],
    *keys: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw: Any = default
    for key in keys:
        if key in cfg:
            raw = cfg.get(key)
            break
    try:
        val = int(raw)
    except Exception:
        val = default
    return max(min_value, min(max_value, val))


def _cfg_string_list(cfg: dict[str, Any], *keys: str) -> list[str]:
    raw: Any = None
    for key in keys:
        if key in cfg:
            raw = cfg.get(key)
            break
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            result.append(text)
    return result


def _resolve_node_template_vars(
    template: str,
    node_outputs: dict[str, NodeOutput],
    start_inputs: dict[str, Any],
    sys_vars: dict[str, str] | None = None,
    container_fields: dict[str, Any] | None = None,
) -> str:
    """Resolve {{node_id.field}} template variables from DAG node outputs."""
    if not template:
        return ""
    sys_ctx = sys_vars or {}
    container_ctx = container_fields or {}

    def _repl(match: re.Match) -> str:
        node_id = match.group(1)
        field = match.group(2)

        if node_id == "start":
            return _truncate(start_inputs.get(field, ""))
        if node_id == "sys":
            return _truncate(sys_ctx.get(field, ""))
        if node_id == "container":
            if field in container_ctx:
                return _truncate(container_ctx.get(field, ""))
            container_out = node_outputs.get("container", {})
            container_json = container_out.get("json_fields", {}) if isinstance(container_out, dict) else {}
            return _truncate(container_json.get(field, ""))

        out = node_outputs.get(node_id)
        if not out:
            logger.warning("Template references node with no output: %s.%s", node_id, field)
            return ""

        if field == "text":
            return _truncate(out.get("text", ""))
        if field == "raw":
            raw = out.get("raw")
            return _truncate(_stringify(raw) if raw is not None else "")

        json_fields = out.get("json_fields", {})
        if field in json_fields:
            val = json_fields[field]
            return _truncate(_stringify(val) if not isinstance(val, str) else val)

        return _truncate(out.get("text", ""))

    return _NODE_VAR_RE.sub(_repl, template)


def _normalize_if_else_operator(raw: Any) -> str:
    op = str(raw or "is").strip().lower()
    if not op:
        return "is"
    return _IF_ELSE_LEGACY_OPERATOR_MAP.get(op, op)


def _default_if_else_condition(idx: int = 1) -> dict[str, Any]:
    return {
        "id": f"cond_{idx}",
        "variable": "",
        "operator": "is",
        "value": "",
    }


def _normalize_if_else_config(node_cfg: dict[str, Any]) -> dict[str, Any]:
    """Normalize if_else config to branches/else_handle shape.

    Supports both new config:
      branches: [{id,label,logic,conditions:[...]}], else_handle
    and legacy config:
      conditions: [{id,variable,operator,value,handle}]
    """
    else_handle = str(node_cfg.get("else_handle") or "else").strip() or "else"
    if not _IF_ELSE_HANDLE_RE.fullmatch(else_handle):
        else_handle = "else"

    branches_raw = node_cfg.get("branches")
    normalized_branches: list[dict[str, Any]] = []

    if isinstance(branches_raw, list) and branches_raw:
        for branch_idx, branch in enumerate(branches_raw, start=1):
            if not isinstance(branch, dict):
                continue
            branch_id = str(branch.get("id") or f"if_{branch_idx}").strip()
            if not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                branch_id = f"if_{branch_idx}"

            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"

            label = str(branch.get("label") or ("IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}")).strip()
            if not label:
                label = "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}"

            conds_raw = branch.get("conditions")
            conds: list[dict[str, Any]] = []
            if isinstance(conds_raw, list):
                for cond_idx, cond in enumerate(conds_raw, start=1):
                    if not isinstance(cond, dict):
                        continue
                    cond_id = str(cond.get("id") or f"{branch_id}_cond_{cond_idx}").strip() or f"{branch_id}_cond_{cond_idx}"
                    conds.append({
                        "id": cond_id,
                        "variable": str(cond.get("variable") or "").strip(),
                        "operator": _normalize_if_else_operator(cond.get("operator")),
                        "value": None if cond.get("value") is None else str(cond.get("value")),
                    })
            if not conds:
                conds = [_default_if_else_condition()]

            normalized_branches.append({
                "id": branch_id,
                "label": label,
                "logic": logic,
                "conditions": conds,
            })

    # legacy fallback: conditions[] with handle per condition
    if not normalized_branches:
        legacy_conds = node_cfg.get("conditions")
        handle_order: list[str] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        if isinstance(legacy_conds, list):
            for cond_idx, cond in enumerate(legacy_conds, start=1):
                if not isinstance(cond, dict):
                    continue
                handle = str(cond.get("handle") or "").strip()
                if not handle:
                    continue
                if handle in {"default", "else"}:
                    continue
                if not _IF_ELSE_HANDLE_RE.fullmatch(handle):
                    continue
                if handle not in grouped:
                    grouped[handle] = []
                    handle_order.append(handle)
                grouped[handle].append({
                    "id": str(cond.get("id") or f"{handle}_cond_{cond_idx}").strip() or f"{handle}_cond_{cond_idx}",
                    "variable": str(cond.get("variable") or "").strip(),
                    "operator": _normalize_if_else_operator(cond.get("operator")),
                    "value": None if cond.get("value") is None else str(cond.get("value")),
                })

        for branch_idx, handle in enumerate(handle_order, start=1):
            normalized_branches.append({
                "id": handle,
                "label": "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}",
                "logic": "and",
                "conditions": grouped.get(handle) or [_default_if_else_condition()],
            })

    return {
        "branches": normalized_branches,
        "else_handle": else_handle,
    }


def _extract_output_param_names(output_params: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(output_params, list):
        return names
    for item in output_params:
        name = ""
        if isinstance(item, dict):
            name = str(item.get("name", "") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
        if name:
            names.append(name)
    return names


@lru_cache(maxsize=1)
def _get_system_tool_output_param_map() -> dict[str, list[str]]:
    """Load system tool output field names from registry definitions."""
    try:
        from app.assistant_config.registry import ToolRegistry

        mapping: dict[str, list[str]] = {}
        for definition in ToolRegistry.list_system_tool_definitions():
            name = getattr(definition, "name", "")
            if not name:
                continue
            mapping[name] = _extract_output_param_names(
                getattr(definition, "output_params", None)
            )
        return mapping
    except Exception as e:
        logger.debug("Failed to load system tool output param map: %s", e)
        return {}


def _resolve_tool_output_param_names(tool_name: str, tool: Any) -> list[str]:
    # RemoteTool may carry output_params in future DB schema expansion.
    from_tool = _extract_output_param_names(getattr(tool, "output_params", None))
    if from_tool:
        return from_tool
    return _get_system_tool_output_param_map().get(tool_name, [])


def _cfg_list_value(cfg: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        if key in cfg and isinstance(cfg.get(key), list):
            return cfg.get(key) or []
    return []


def _normalize_container_body_nodes(node_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_nodes = _cfg_list_value(node_cfg, "body_nodes", "bodyNodes")
    nodes: list[dict[str, Any]] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("node_id", raw.get("nodeId", "")) or "").strip()
        node_type = str(raw.get("node_type", raw.get("nodeType", "")) or "").strip()
        if not node_id or not node_type:
            continue
        cfg = raw.get("config")
        nodes.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "label": str(raw.get("label", "") or node_id),
                "config": _normalize_config(cfg) if isinstance(cfg, dict) else {},
            }
        )

    if not nodes:
        nodes = [
            {
                "node_id": "start",
                "node_type": "start",
                "label": "start",
                "config": {},
            }
        ]

    if not any(node.get("node_type") == "start" for node in nodes):
        nodes.insert(
            0,
            {
                "node_id": "start",
                "node_type": "start",
                "label": "start",
                "config": {},
            },
        )

    return nodes


def _normalize_container_body_edges(node_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_edges = _cfg_list_value(node_cfg, "body_edges", "bodyEdges")
    edges: list[dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source_node_id", raw.get("sourceNodeId", "")) or "").strip()
        target = str(raw.get("target_node_id", raw.get("targetNodeId", "")) or "").strip()
        if not source or not target:
            continue
        edges.append(
            {
                "source_node_id": source,
                "target_node_id": target,
                "source_handle": str(raw.get("source_handle", raw.get("sourceHandle", "output")) or "output"),
                "target_handle": str(raw.get("target_handle", raw.get("targetHandle", "input")) or "input"),
                "condition_expr": raw.get("condition_expr", raw.get("conditionExpr")),
            }
        )
    return edges


def _coerce_array_input(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except Exception:
            return [text]
    return [value]


def _parse_loose_json_value(value: str) -> Any:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("[") or text.startswith('"'):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def _build_container_start_node(container_input: Any, container_fields: dict[str, Any]) -> Callable[[WorkflowState], dict]:
    def start_node(_state: WorkflowState) -> dict:
        text = _stringify(container_input)
        return {
            "node_outputs": {
                "start": NodeOutput(
                    status="ok",
                    text=text,
                    raw=container_input,
                    json_fields={
                        "user_input": container_input,
                        **container_fields,
                    },
                )
            },
            "execution_trace": ["start"],
        }

    return start_node


def _execute_container_body(
    *,
    container_node_id: str,
    container_node_type: str,
    node_cfg: dict[str, Any],
    parent_state: WorkflowState,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
    container_input: Any = "",
    container_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body_nodes = _normalize_container_body_nodes(node_cfg)
    body_edges = _normalize_container_body_edges(node_cfg)
    body_node_map = {str(node["node_id"]): node for node in body_nodes}
    body_type_map = {str(node["node_id"]): str(node["node_type"]) for node in body_nodes}

    if "start" not in body_node_map:
        raise RuntimeError(f"{container_node_type} node {container_node_id} body has no start node")

    out_edges: dict[str, list[tuple[str, str]]] = {}
    in_degree: dict[str, int] = {node_id: 0 for node_id in body_node_map}
    for edge in body_edges:
        src = str(edge["source_node_id"])
        tgt = str(edge["target_node_id"])
        if src not in body_node_map or tgt not in body_node_map:
            continue
        out_edges.setdefault(src, []).append((tgt, str(edge.get("source_handle", "output") or "output")))
        in_degree[tgt] = in_degree.get(tgt, 0) + 1

    metadata = parent_state.get("metadata", {}) or {}
    sys_vars = parent_state.get("sys_vars", {}) or {}
    runtime_node_llms_raw = parent_state.get("node_llms", {}) or {}
    runtime_node_llms: dict[str, Any]
    if isinstance(runtime_node_llms_raw, dict):
        runtime_node_llms = dict(runtime_node_llms_raw)
    else:
        runtime_node_llms = {}
    scoped_node_llms: dict[str, Any] = dict(node_llms or {})

    container_ctx = dict(container_fields or {})
    start_result = _build_container_start_node(container_input, container_ctx)({})
    node_outputs_local: dict[str, NodeOutput] = dict(parent_state.get("node_outputs", {}))
    node_outputs_local.update(start_result.get("node_outputs", {}))
    node_outputs_local["container"] = NodeOutput(
        status="ok",
        text=_stringify(container_ctx),
        raw=container_ctx,
        json_fields=container_ctx,
    )

    execution_trace: list[str] = ["start"]
    branch_decisions: dict[str, str] = {}

    queue: list[str] = []
    for target, _ in out_edges.get("start", []):
        in_degree[target] = max(0, in_degree.get(target, 0) - 1)
        if in_degree[target] == 0:
            queue.append(target)

    executed_nodes = {"start"}

    while queue:
        current = queue.pop(0)
        if current in executed_nodes:
            continue
        node_meta = body_node_map.get(current)
        if not node_meta:
            continue
        node_type = body_type_map.get(current, "")
        cfg = node_meta.get("config") if isinstance(node_meta.get("config"), dict) else {}
        scoped_model_key = f"{container_node_id}::{current}"
        if scoped_model_key in runtime_node_llms:
            runtime_node_llms[current] = runtime_node_llms[scoped_model_key]
        if scoped_model_key in scoped_node_llms:
            scoped_node_llms[current] = scoped_node_llms[scoped_model_key]

        state_for_node: WorkflowState = {
            "metadata": metadata,
            "node_outputs": node_outputs_local,
            "user_input": _stringify(container_input),
            "sys_vars": sys_vars,
            "workflow_node_types": body_type_map,
            "node_llms": runtime_node_llms,
        }

        if node_type == "llm":
            node_fn = _build_dag_llm_node(current, cfg, llm, node_llms=scoped_node_llms)
        elif node_type == "tool":
            node_fn = _build_dag_tool_node(current, cfg, tool_map, args_llm, db_bind)
        elif node_type == "if_else":
            node_fn = _build_if_else_node(current, cfg)
        elif node_type == "parameter_extractor":
            node_fn = _build_param_extractor_node(current, cfg, llm, node_llms=scoped_node_llms)
        elif node_type == "knowledge_retrieval":
            node_fn = _build_kr_node(current, cfg, tool_map, db_bind)
        elif node_type == "start":
            node_fn = _build_container_start_node(container_input, container_ctx)
        else:
            raise RuntimeError(
                f"{container_node_type} node {container_node_id} body node {current} has unsupported type: {node_type}"
            )

        result = node_fn(state_for_node)
        if isinstance(result.get("node_outputs"), dict):
            node_outputs_local.update(result["node_outputs"])
        if isinstance(result.get("execution_trace"), list):
            execution_trace.extend([str(item) for item in result["execution_trace"]])
        if isinstance(result.get("branch_decisions"), dict):
            branch_decisions.update({str(k): str(v) for k, v in result["branch_decisions"].items()})

        executed_nodes.add(current)

        outgoing = out_edges.get(current, [])
        if not outgoing:
            continue
        if node_type == "if_else":
            chosen = branch_decisions.get(current)
            for target, handle in outgoing:
                normalized_handle = "else" if handle == "default" else handle
                if normalized_handle != chosen:
                    continue
                in_degree[target] = max(0, in_degree.get(target, 0) - 1)
                if in_degree[target] == 0:
                    queue.append(target)
            continue

        for target, _ in outgoing:
            in_degree[target] = max(0, in_degree.get(target, 0) - 1)
            if in_degree[target] == 0:
                queue.append(target)

    produced = {
        node_id: out
        for node_id, out in node_outputs_local.items()
        if node_id in body_node_map and node_id != "start"
    }
    terminal_nodes = [
        node_id
        for node_id in produced.keys()
        if len(out_edges.get(node_id, [])) == 0
    ]
    last_terminal = terminal_nodes[-1] if terminal_nodes else (list(produced.keys())[-1] if produced else "start")
    return {
        "node_outputs": produced,
        "all_node_outputs": node_outputs_local,
        "last_node_id": last_terminal,
        "execution_trace": execution_trace,
    }


# ==================== DAG Node Builders (Task 14.3) ====================


def _build_start_node(
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def start_node(state: WorkflowState) -> dict:
        user_input = state.get("user_input", "")
        sys_vars = state.get("sys_vars", {}) or {}
        return {
            "node_outputs": {
                "start": NodeOutput(
                    status="ok",
                    text=user_input,
                    raw=user_input,
                    json_fields={
                        "user_input": user_input,
                        "sys_date": sys_vars.get("date", ""),
                        "sys_datetime": sys_vars.get("datetime", ""),
                        "sys_conversation_id": sys_vars.get("conversation_id", ""),
                    },
                ),
            },
            "execution_trace": ["start"],
        }
    return start_node


def _build_dag_llm_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def llm_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        workflow_node_types = state.get("workflow_node_types", {}) or {}
        runtime_node_llms = state.get("node_llms", {}) or {}
        if not isinstance(runtime_node_llms, dict):
            runtime_node_llms = {}
        llm_for_node = runtime_node_llms.get(node_id)
        if llm_for_node is None and node_llms is not None:
            llm_for_node = node_llms.get(node_id)
        if llm_for_node is None:
            llm_for_node = llm

        system_prompt_raw = node_cfg.get("system_prompt", "")
        if not isinstance(system_prompt_raw, str):
            system_prompt_raw = ""
        system_prompt = _resolve_node_template_vars(
            system_prompt_raw, node_outputs, start_inputs, sys_vars,
        )
        output_mode = str(node_cfg.get("output_mode", "text") or "text").strip().lower()
        if output_mode == "json":
            output_mode = "structured"
        if output_mode not in {"text", "structured"}:
            raise RuntimeError(f"DAG LLM node {node_id}: unsupported output_mode={output_mode}")

        user_input_template = node_cfg.get("user_input", "{{start.user_input}}")
        if not isinstance(user_input_template, str):
            user_input_template = "{{start.user_input}}"
        user_input_rendered = _resolve_node_template_vars(
            user_input_template, node_outputs, start_inputs, sys_vars,
        )
        if not user_input_rendered.strip():
            user_input_rendered = start_inputs.get("user_input", "") or state.get("user_input", "")

        knowledge_enabled = _cfg_bool_value(
            node_cfg, "knowledge_enabled", "knowledgeEnabled", default=False
        )
        knowledge_source_node_ids = _cfg_string_list(
            node_cfg, "knowledge_source_node_ids", "knowledgeSourceNodeIds"
        )
        raw_inject_mode = str(
            node_cfg.get("knowledge_inject_mode", node_cfg.get("knowledgeInjectMode", "references_only"))
            or "references_only"
        ).strip().lower()
        knowledge_inject_mode = raw_inject_mode if raw_inject_mode in {"references_only", "full_payload"} else "references_only"
        knowledge_max_refs = _cfg_int_value(
            node_cfg,
            "knowledge_max_refs",
            "knowledgeMaxRefs",
            default=20,
            min_value=1,
            max_value=100,
        )
        output_fields = node_cfg.get("output_fields") or []
        field_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in output_fields]
        stream_output_enabled = bool(state.get("stream_output_enabled", True))
        output_stream_source_node_id = str(state.get("output_stream_source_node_id", "") or "")

        structured_mode = output_mode == "structured"
        if structured_mode and field_names:
            from app.assistant.skills.base import OutputFieldSpec, build_json_output_constraint
            specs = []
            for f in output_fields:
                if isinstance(f, dict):
                    try:
                        specs.append(OutputFieldSpec(**f))
                    except Exception:
                        specs.append(OutputFieldSpec(name=f.get("name", "field")))
            constraint = build_json_output_constraint(specs)
        elif structured_mode:
            constraint = "输出要求：只输出一个 JSON 对象；禁止输出额外描述、Markdown、代码块围栏。"
        else:
            constraint = ""

        today = date.today()
        full_prompt = (
            f"你是 MindAtlas AI 助手的分析模块。\n\n"
            f"## 当前日期\n{today.isoformat()}（{today.strftime('%A')}）\n\n"
            f"## 任务\n{system_prompt}\n\n"
        )
        if constraint:
            full_prompt += f"## {constraint}\n\n"

        context_data: dict[str, str] = {}
        for nid, out in node_outputs.items():
            if workflow_node_types.get(nid) == "knowledge_retrieval":
                # KR 输出只允许通过显式 knowledge binding 注入，避免隐式混入。
                continue
            context_data[nid] = _truncate(out.get("text", ""), 2000)
        context_snapshot = json.dumps(context_data, ensure_ascii=False, default=str)[:4000]

        msgs = [
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": f"上下文数据：\n{context_snapshot}"},
        ]

        if knowledge_enabled and knowledge_source_node_ids:
            remaining_refs = knowledge_max_refs
            selected_payloads: list[dict[str, Any]] = []
            for source_id in knowledge_source_node_ids:
                if remaining_refs <= 0:
                    break
                if workflow_node_types.get(source_id) != "knowledge_retrieval":
                    continue
                source_out = node_outputs.get(source_id) or {}
                source_fields = source_out.get("json_fields") if isinstance(source_out.get("json_fields"), dict) else {}
                references = source_fields.get("references") if isinstance(source_fields, dict) else None
                if not isinstance(references, list):
                    raw_payload = source_out.get("raw")
                    if isinstance(raw_payload, dict):
                        references = raw_payload.get("references")
                if not isinstance(references, list):
                    references = []
                clipped_refs = references[:remaining_refs]
                remaining_refs -= len(clipped_refs)

                if knowledge_inject_mode == "references_only":
                    selected_payloads.append({
                        "node_id": source_id,
                        "query": source_fields.get("query", ""),
                        "mode": source_fields.get("mode", ""),
                        "references": clipped_refs,
                        "references_count": len(clipped_refs),
                    })
                    continue

                raw_payload = source_out.get("raw")
                full_payload = dict(raw_payload) if isinstance(raw_payload, dict) else {"payload": raw_payload}
                full_payload["node_id"] = source_id
                full_payload["query"] = full_payload.get("query", source_fields.get("query", ""))
                full_payload["mode"] = full_payload.get("mode", source_fields.get("mode", ""))
                full_payload["result"] = full_payload.get("result", source_fields.get("result", source_out.get("text", "")))
                full_payload["references"] = clipped_refs
                full_payload["references_count"] = len(clipped_refs)
                selected_payloads.append(full_payload)

            if selected_payloads:
                msgs.append({
                    "role": "system",
                    "content": (
                        "以下是你显式绑定的知识检索结果(JSON)。"
                        "你只能把这些内容作为知识依据，不要杜撰引用。"
                    ),
                })
                msgs.append({
                    "role": "user",
                    "content": _truncate(
                        json.dumps(
                            {
                                "inject_mode": knowledge_inject_mode,
                                "sources": selected_payloads,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                        8000,
                    ),
                })

        msgs.append({"role": "user", "content": user_input_rendered})

        _emit(metadata, "on_node_start", node_id=node_id, node_type="llm")

        def _run_once(allow_content_stream: bool) -> str:
            chunks: list[str] = []
            for chunk in llm_for_node.stream(msgs):
                if not chunk.content:
                    continue
                chunks.append(chunk.content)
                _emit(metadata, "on_node_output_delta", node_id=node_id, delta=chunk.content)
                if (
                    allow_content_stream
                    and stream_output_enabled
                    and output_stream_source_node_id == node_id
                ):
                    _emit(metadata, "on_content_delta", chunk=chunk.content)
            return "".join(chunks).strip()

        text = ""
        parsed_structured: dict[str, Any] | None = None
        attempts = 2 if structured_mode else 1
        for attempt in range(attempts):
            try:
                # structured 模式需要先验证后输出，避免失败重试时前端收到脏数据
                text = _run_once(allow_content_stream=not structured_mode)
            except Exception as e:
                raise RuntimeError(f"DAG LLM node {node_id} failed: {e}") from e

            if not structured_mode:
                break

            if not text:
                continue

            parsed = _extract_json_object(text)
            if parsed is not None:
                parsed_structured = {k: parsed.get(k) for k in field_names} if field_names else parsed
                break

            if attempt == attempts - 1:
                raise RuntimeError(
                    f"DAG LLM node {node_id} failed to parse structured output after retry"
                )

        node_out: NodeOutput = {"status": "ok", "text": text, "raw": text, "json_fields": {"response": text}}
        if structured_mode:
            if parsed_structured is None:
                raise RuntimeError(f"DAG LLM node {node_id}: structured output is empty or invalid")
            json_text = json.dumps(parsed_structured, ensure_ascii=False)
            json_fields = dict(parsed_structured)
            json_fields["response"] = json_text
            node_out = {
                "status": "ok",
                "text": json_text,
                "raw": parsed_structured,
                "json_fields": json_fields,
            }

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return llm_node


def _build_output_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def output_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        workflow_node_types = state.get("workflow_node_types", {}) or {}
        stream_output_enabled = bool(state.get("stream_output_enabled", True))
        output_stream_source_node_id = str(state.get("output_stream_source_node_id", "") or "")

        output_mode = str(node_cfg.get("output_mode", "text") or "text").strip().lower()
        if output_mode == "json":
            output_mode = "structured"
        if output_mode not in {"text", "structured"}:
            raise RuntimeError(f"DAG output node {node_id}: unsupported output_mode={output_mode}")

        _emit(metadata, "on_node_start", node_id=node_id, node_type="output")

        if output_mode == "text":
            text_template = node_cfg.get("text_template", "{{start.user_input}}")
            if not isinstance(text_template, str):
                raise RuntimeError(
                    f"DAG output node {node_id}: textTemplate must be string in text mode"
                )

            rendered_text = _resolve_node_template_vars(
                text_template, node_outputs, start_inputs, sys_vars,
            )
            node_out: NodeOutput = {
                "status": "ok",
                "text": rendered_text,
                "raw": rendered_text,
                "json_fields": {"response": rendered_text},
            }

            # When output is a direct single-ref passthrough from an LLM source, LLM node streams tokens.
            single_ref = _extract_single_template_reference(text_template)
            should_skip_final_emit = (
                stream_output_enabled
                and single_ref is not None
                and single_ref[0] == output_stream_source_node_id
                and workflow_node_types.get(single_ref[0]) == "llm"
                and single_ref[1] in {"response", "text"}
            )
            if not should_skip_final_emit and rendered_text:
                _emit(metadata, "on_content_delta", chunk=rendered_text)

            _emit(metadata, "on_node_end", node_id=node_id, status="ok")
            return {
                "node_outputs": {node_id: node_out},
                "execution_trace": [node_id],
            }

        output_fields = node_cfg.get("output_fields")
        if not isinstance(output_fields, list) or not output_fields:
            raise RuntimeError(
                f"DAG output node {node_id}: structured mode requires output_fields"
            )

        structured_payload: dict[str, Any] = {}
        for raw_field in output_fields:
            if not isinstance(raw_field, dict):
                raise RuntimeError(
                    f"DAG output node {node_id}: output_fields items must be objects"
                )
            field_name = str(raw_field.get("name", "") or "").strip()
            if not field_name:
                raise RuntimeError(f"DAG output node {node_id}: output field name is required")
            value_template = raw_field.get("value", "")
            if not isinstance(value_template, str):
                raise RuntimeError(
                    f"DAG output node {node_id}: output field '{field_name}' requires string value"
                )
            rendered_value = _resolve_node_template_vars(
                value_template, node_outputs, start_inputs, sys_vars,
            )
            try:
                coerced = _coerce_output_field_value(field_name, rendered_value, raw_field)
            except Exception as exc:
                raise RuntimeError(
                    f"DAG output node {node_id}: output field '{field_name}' invalid value: {exc}"
                ) from exc
            structured_payload[field_name] = coerced

        json_text = json.dumps(structured_payload, ensure_ascii=False)
        json_fields = dict(structured_payload)
        json_fields["response"] = json_text
        node_out = NodeOutput(
            status="ok",
            text=json_text,
            raw=structured_payload,
            json_fields=json_fields,
        )
        if json_text:
            _emit(metadata, "on_content_delta", chunk=json_text)

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return output_node


def _build_dag_tool_node(
    node_id: str,
    node_cfg: dict,
    tool_map: dict[str, Any],
    args_llm: ChatOpenAI,
    db_bind: Any,
) -> Callable[[WorkflowState], dict]:
    def dag_tool_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        tool_name = node_cfg.get("tool_name", "")
        tool_call_id = f"tool_{uuid.uuid4().hex[:8]}"

        tool = tool_map.get(tool_name)
        if not tool:
            raise RuntimeError(f"DAG tool node {node_id}: tool not found: {tool_name}")

        input_bindings = node_cfg.get("input_bindings")
        if not isinstance(input_bindings, dict):
            raise RuntimeError(
                f"DAG tool node {node_id} requires inputBindings object; legacy argsFrom/argsTemplate are no longer supported"
            )
        args: dict[str, Any] = {}
        for k, raw_tpl in input_bindings.items():
            key = str(k).strip() if isinstance(k, str) else ""
            if not key:
                continue
            if isinstance(raw_tpl, str):
                args[key] = _resolve_node_template_vars(raw_tpl, node_outputs, start_inputs, sys_vars)
            elif raw_tpl is None:
                args[key] = ""
            else:
                args[key] = str(raw_tpl)

        _emit(metadata, "on_node_start", node_id=node_id, node_type="tool")
        _emit(metadata, "on_tool_call_start", tool_call_id=tool_call_id, tool_name=tool_name, args=args)

        wrapped = _wrap_tool_with_db(tool, db_bind)
        status = "ok"
        try:
            result = wrapped(**args)
        except Exception as e:
            logger.error("DAG tool %s failed: %s", tool_name, e)
            status = "error"
            result = f"工具执行失败: {e}"

        result_str = _stringify(result)
        raw: Any = result
        if isinstance(result, str):
            s = result.strip()
            if s.startswith("{") or s.startswith("["):
                try:
                    raw = json.loads(s)
                except Exception:
                    pass

        _emit(metadata, "on_tool_call_end", tool_call_id=tool_call_id,
              status="completed" if status == "ok" else "error", result=result_str)

        json_fields: dict[str, Any] = {
            "result": raw if not isinstance(raw, str) else result_str,
        }
        output_param_names = _resolve_tool_output_param_names(tool_name, tool)
        if isinstance(raw, dict):
            for field_name in output_param_names:
                json_fields[field_name] = raw.get(field_name)
        elif isinstance(raw, list) and "items" in output_param_names:
            json_fields["items"] = raw

        node_out: NodeOutput = {"status": status, "text": result_str, "raw": raw, "json_fields": json_fields}
        _emit(metadata, "on_node_end", node_id=node_id, status=status)

        if status == "error":
            raise RuntimeError(f"DAG tool node {node_id} failed: {result_str}")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return dag_tool_node


def _build_if_else_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def if_else_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        normalized_cfg = _normalize_if_else_config(node_cfg)
        branches = normalized_cfg.get("branches", [])
        else_handle = str(normalized_cfg.get("else_handle") or "else")

        _emit(metadata, "on_node_start", node_id=node_id, node_type="if_else")

        chosen_handle = else_handle
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            branch_handle = str(branch.get("id") or "").strip()
            if not branch_handle:
                continue
            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"
            branch_conditions = branch.get("conditions")
            if not isinstance(branch_conditions, list) or not branch_conditions:
                continue

            results: list[bool] = []
            for cond in branch_conditions:
                if not isinstance(cond, dict):
                    continue
                variable = str(cond.get("variable") or "").strip()
                operator = _normalize_if_else_operator(cond.get("operator"))
                value_template = cond.get("value")
                rhs_template = "" if value_template is None else str(value_template)
                rhs_value = _resolve_node_template_vars(rhs_template, node_outputs, start_inputs, sys_vars)

                actual_value = ""
                if variable.startswith("sys."):
                    sys_key = variable.split(".", 1)[1] if "." in variable else ""
                    actual_value = str(sys_vars.get(sys_key, "") or "")
                else:
                    parts = variable.split(".", 1)
                    ref_node = parts[0]
                    ref_field = parts[1] if len(parts) > 1 else "text"

                    out = node_outputs.get(ref_node, {})
                    actual = out.get("json_fields", {}).get(ref_field, out.get("text", ""))
                    actual_value = str(actual) if actual is not None else ""

                results.append(_eval_condition(actual_value, operator, rhs_value))

            if not results:
                continue
            matched = all(results) if logic == "and" else any(results)
            if matched:
                chosen_handle = branch_handle
                break

        _emit(metadata, "on_branch_decision", node_id=node_id, handle=chosen_handle)
        _emit(metadata, "on_node_end", node_id=node_id, status="ok")

        node_out = NodeOutput(status="ok", text=chosen_handle, raw=chosen_handle, json_fields={"handle": chosen_handle})

        return {
            "node_outputs": {node_id: node_out},
            "branch_decisions": {node_id: chosen_handle},
            "execution_trace": [node_id],
        }
    return if_else_node


def _eval_condition(actual: str, operator: str, value: str) -> bool:
    op = _normalize_if_else_operator(operator)
    actual_str = "" if actual is None else str(actual)
    value_str = "" if value is None else str(value)
    actual_ci = actual_str.casefold()
    value_ci = value_str.casefold()

    if op == "is":
        return actual_ci == value_ci
    if op == "is_not":
        return actual_ci != value_ci
    if op == "contains":
        return value_ci in actual_ci
    if op == "not_contains":
        return value_ci not in actual_ci
    if op == "starts_with":
        return actual_ci.startswith(value_ci)
    if op == "ends_with":
        return actual_ci.endswith(value_ci)
    if op == "is_empty":
        return not actual_str.strip()
    if op == "is_not_empty":
        return bool(actual_str.strip())
    try:
        a, v = float(actual_str), float(value_str)
        if op == "gt":
            return a > v
        if op == "lt":
            return a < v
        if op == "gte":
            return a >= v
        if op == "lte":
            return a <= v
    except (ValueError, TypeError):
        pass
    return False


def _build_param_extractor_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def param_extractor_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        runtime_node_llms = state.get("node_llms", {}) or {}
        if not isinstance(runtime_node_llms, dict):
            runtime_node_llms = {}
        llm_for_node = runtime_node_llms.get(node_id)
        if llm_for_node is None and node_llms is not None:
            llm_for_node = node_llms.get(node_id)
        if llm_for_node is None:
            llm_for_node = llm

        input_content_template = node_cfg.get("input_content")
        if input_content_template is None:
            input_content_template = node_cfg.get("inputContent", "")
        if not isinstance(input_content_template, str):
            input_content_template = ""
        input_content = _resolve_node_template_vars(
            input_content_template, node_outputs, start_inputs, sys_vars,
        )

        instruction_template = node_cfg.get("instruction", "")
        if not isinstance(instruction_template, str):
            instruction_template = ""
        instruction = _resolve_node_template_vars(
            instruction_template, node_outputs, start_inputs, sys_vars,
        )

        output_fields = node_cfg.get("output_fields")
        if output_fields is None:
            output_fields = node_cfg.get("outputFields")
        if not isinstance(output_fields, list) or not output_fields:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: output_fields must be non-empty"
            )

        from app.assistant.skills.base import OutputFieldSpec, build_json_output_constraint
        specs: list[OutputFieldSpec] = []
        for field in output_fields:
            if isinstance(field, str):
                name = field.strip()
                if name:
                    specs.append(OutputFieldSpec(name=name))
                continue
            if not isinstance(field, dict):
                continue

            payload = dict(field)
            if "itemsType" in payload and "items_type" not in payload:
                payload["items_type"] = payload.get("itemsType")
            try:
                specs.append(OutputFieldSpec(**payload))
            except Exception:
                name = str(payload.get("name", "") or "").strip()
                if name:
                    specs.append(OutputFieldSpec(name=name))

        if not specs:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: output_fields are invalid"
            )

        field_names = [spec.name for spec in specs]
        constraint = build_json_output_constraint(specs)

        system_prompt = (
            "你是结构化参数提取器。"
            "你的任务是根据输入内容，提取目标字段并严格返回一个 JSON 对象。"
        )
        if instruction.strip():
            system_prompt += f"\n\n额外提取说明：\n{instruction.strip()}"
        if constraint:
            system_prompt += f"\n\n{constraint}"

        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"输入内容：\n{input_content}"},
        ]

        _emit(metadata, "on_node_start", node_id=node_id, node_type="parameter_extractor")

        chunks: list[str] = []
        for chunk in llm_for_node.stream(msgs):
            if chunk.content:
                chunks.append(chunk.content)
                _emit(metadata, "on_node_output_delta", node_id=node_id, delta=chunk.content)

        text = "".join(chunks).strip()
        parsed = _extract_json_object(text)
        if parsed is None:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: model output must be a valid JSON object"
            )

        missing_fields = [name for name in field_names if name not in parsed]
        if missing_fields:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: missing output fields: {', '.join(missing_fields)}"
            )

        filtered = {name: parsed.get(name) for name in field_names}
        json_text = json.dumps(filtered, ensure_ascii=False)
        node_out: NodeOutput = {
            "status": "ok",
            "text": json_text,
            "raw": parsed,
            "json_fields": filtered,
        }

        node_outputs[node_id] = node_out
        _emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return param_extractor_node


def _build_kr_node(
    node_id: str,
    node_cfg: dict,
    tool_map: dict[str, Any],
    db_bind: Any,
) -> Callable[[WorkflowState], dict]:
    def kr_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}

        query_template = node_cfg.get("query", "{{start.user_input}}")
        if not isinstance(query_template, str):
            query_template = "{{start.user_input}}"
        query = _resolve_node_template_vars(
            query_template, node_outputs, start_inputs, sys_vars,
        )
        raw_mode = node_cfg.get("mode")
        mode = raw_mode.strip() if isinstance(raw_mode, str) and raw_mode.strip() else None
        raw_top_k = node_cfg.get("top_k")
        if raw_top_k is None:
            raw_top_k = node_cfg.get("topK")
        top_k: int | None = None
        if raw_top_k is not None and str(raw_top_k).strip() != "":
            try:
                top_k = max(1, min(50, int(raw_top_k)))
            except Exception:
                top_k = None

        _emit(metadata, "on_node_start", node_id=node_id, node_type="knowledge_retrieval")

        kb_tool = tool_map.get("kb_search")
        result_text = ""
        raw_payload: Any = ""
        if kb_tool:
            wrapped = _wrap_tool_with_db(kb_tool, db_bind)
            invoke_args: dict[str, Any] = {"query": query}
            if mode is not None:
                invoke_args["mode"] = mode
            if top_k is not None:
                invoke_args["top_k"] = top_k
            try:
                raw_result = wrapped(**invoke_args)
                result_text = _stringify(raw_result)
                raw_payload = raw_result
                if isinstance(raw_result, str):
                    parsed = _extract_json_object(raw_result)
                    if parsed is not None:
                        raw_payload = parsed
            except Exception as e:
                logger.warning("KR node %s failed: %s", node_id, e)
                result_text = f"知识库检索失败: {e}"
                raw_payload = {"error": str(e)}
        else:
            result_text = "知识库工具不可用"
            raw_payload = {"error": "kb_search not available"}

        payload_obj = raw_payload if isinstance(raw_payload, dict) else {}
        references = payload_obj.get("references") if isinstance(payload_obj, dict) else None
        if not isinstance(references, list):
            references = []
        references_count = len(references)
        payload_mode = payload_obj.get("mode") if isinstance(payload_obj, dict) else None
        mode_value = payload_mode if isinstance(payload_mode, str) and payload_mode.strip() else (mode or "system_default")
        result_value = payload_obj.get("result") if isinstance(payload_obj, dict) else None
        if result_value is None:
            result_value = result_text or f"检索到 {references_count} 条参考资料"
        if not result_text:
            result_text = _stringify(result_value)

        node_out = NodeOutput(
            status="ok",
            text=result_text,
            raw=raw_payload,
            json_fields={
                "result": result_value,
                "query": query,
                "mode": mode_value,
                "references": references,
                "references_count": references_count,
            },
        )
        _emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return kr_node


def _build_iteration_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def iteration_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}

        input_source_tpl = str(node_cfg.get("input_source", node_cfg.get("inputSource", "")) or "").strip()
        if not input_source_tpl:
            raise RuntimeError(f"DAG iteration node {node_id}: inputSource is required")
        rendered_input = _resolve_node_template_vars(
            input_source_tpl,
            node_outputs,
            start_inputs,
            sys_vars,
        )
        items = _coerce_array_input(_parse_loose_json_value(rendered_input))
        output_variable = str(node_cfg.get("output_variable", node_cfg.get("outputVariable", "results")) or "results").strip() or "results"
        output_selector_tpl = str(node_cfg.get("output_selector", node_cfg.get("outputSelector", "{{container.item}}")) or "{{container.item}}")
        parallel_mode = _cfg_bool_value(node_cfg, "parallel_mode", "parallelMode", default=False)
        error_strategy = str(node_cfg.get("error_strategy", node_cfg.get("errorStrategy", "fail_fast")) or "fail_fast").strip().lower()
        flatten_output = _cfg_bool_value(node_cfg, "flatten_output", "flattenOutput", default=True)

        _emit(metadata, "on_node_start", node_id=node_id, node_type="iteration")

        aggregated: list[Any] = []
        errors_payload: list[dict[str, Any]] = []

        def _run_single(index: int, item: Any) -> tuple[int, Any, dict[str, Any] | None]:
            container_fields = {"item": item, "index": index}
            body_result = _execute_container_body(
                container_node_id=node_id,
                container_node_type="iteration",
                node_cfg=node_cfg,
                parent_state=state,
                llm=llm,
                args_llm=args_llm,
                tool_map=tool_map,
                db_bind=db_bind,
                node_llms=node_llms,
                container_input=item,
                container_fields=container_fields,
            )
            selected_text = _resolve_node_template_vars(
                output_selector_tpl,
                body_result.get("all_node_outputs", {}),
                {"user_input": item, "item": item, "index": index},
                sys_vars,
                container_fields=container_fields,
            )
            selected_value = _parse_loose_json_value(selected_text)
            return index, selected_value, None

        if parallel_mode and len(items) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
                futures = {
                    executor.submit(_run_single, index, item): (index, item)
                    for index, item in enumerate(items)
                }
                results_by_index: dict[int, Any] = {}
                for future in as_completed(futures):
                    index, item = futures[future]
                    try:
                        _, value, _ = future.result()
                        results_by_index[index] = value
                    except Exception as exc:
                        err_item = {"index": index, "item": item, "error": str(exc)}
                        if error_strategy == "skip_item":
                            errors_payload.append(err_item)
                            continue
                        raise RuntimeError(f"DAG iteration node {node_id} failed at index {index}: {exc}") from exc
                for index in range(len(items)):
                    if index in results_by_index:
                        aggregated.append(results_by_index[index])
        else:
            for index, item in enumerate(items):
                try:
                    _, value, _ = _run_single(index, item)
                    aggregated.append(value)
                except Exception as exc:
                    err_item = {"index": index, "item": item, "error": str(exc)}
                    if error_strategy == "skip_item":
                        errors_payload.append(err_item)
                        continue
                    raise RuntimeError(f"DAG iteration node {node_id} failed at index {index}: {exc}") from exc

        if flatten_output:
            flattened: list[Any] = []
            for item in aggregated:
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            aggregated = flattened

        raw_payload = {
            "items": aggregated,
            "count": len(aggregated),
            "errors": errors_payload,
        }
        node_out = NodeOutput(
            status="ok",
            text=json.dumps(raw_payload, ensure_ascii=False),
            raw=raw_payload,
            json_fields={
                output_variable: aggregated,
                "count": len(aggregated),
                "errors": errors_payload,
            },
        )

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return iteration_node


def _build_loop_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def loop_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}

        initial_vars = _cfg_list_value(node_cfg, "initial_vars", "initialVars")
        update_mappings = _cfg_list_value(node_cfg, "update_mappings", "updateMappings")
        termination_conditions = _cfg_list_value(node_cfg, "termination_conditions", "terminationConditions")
        termination_logic = str(node_cfg.get("termination_logic", node_cfg.get("terminationLogic", "and")) or "and").strip().lower()
        if termination_logic not in {"and", "or"}:
            termination_logic = "and"
        max_iterations = _cfg_int_value(
            node_cfg,
            "max_iterations",
            "maxIterations",
            default=10,
            min_value=1,
            max_value=1000,
        )

        loop_vars: dict[str, Any] = {}
        for raw in initial_vars:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "") or "").strip()
            if not name:
                continue
            value_tpl = str(raw.get("value", "") or "")
            rendered = _resolve_node_template_vars(
                value_tpl,
                node_outputs,
                start_inputs,
                sys_vars,
                container_fields=loop_vars,
            )
            loop_vars[name] = _parse_loose_json_value(rendered)

        _emit(metadata, "on_node_start", node_id=node_id, node_type="loop")

        iteration_count = 0
        terminated = False
        last_item: Any = None
        iteration_outputs: list[Any] = []

        while iteration_count < max_iterations:
            container_fields = {"index": iteration_count, **loop_vars}
            body_result = _execute_container_body(
                container_node_id=node_id,
                container_node_type="loop",
                node_cfg=node_cfg,
                parent_state=state,
                llm=llm,
                args_llm=args_llm,
                tool_map=tool_map,
                db_bind=db_bind,
                node_llms=node_llms,
                container_input=start_inputs.get("user_input", state.get("user_input", "")),
                container_fields=container_fields,
            )
            iteration_outputs.append(body_result.get("node_outputs", {}))
            last_node_id = str(body_result.get("last_node_id", "") or "")
            last_out = (body_result.get("all_node_outputs", {}) or {}).get(last_node_id, {})
            last_item = last_out.get("raw", last_out.get("text"))

            for raw in update_mappings:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name", "") or "").strip()
                if not name:
                    continue
                value_tpl = str(raw.get("value", "") or "")
                rendered = _resolve_node_template_vars(
                    value_tpl,
                    body_result.get("all_node_outputs", {}),
                    {"user_input": start_inputs.get("user_input", state.get("user_input", ""))},
                    sys_vars,
                    container_fields={"index": iteration_count, **loop_vars},
                )
                loop_vars[name] = _parse_loose_json_value(rendered)

            if termination_conditions:
                evaluated: list[bool] = []
                for cond in termination_conditions:
                    if not isinstance(cond, dict):
                        continue
                    variable = str(cond.get("variable", "") or "").strip()
                    operator = _normalize_if_else_operator(cond.get("operator"))
                    value_tpl = "" if cond.get("value") is None else str(cond.get("value"))
                    rhs_value = _resolve_node_template_vars(
                        value_tpl,
                        body_result.get("all_node_outputs", {}),
                        {"user_input": start_inputs.get("user_input", state.get("user_input", ""))},
                        sys_vars,
                        container_fields={"index": iteration_count, **loop_vars},
                    )

                    actual_value = ""
                    if variable.startswith("sys."):
                        sys_key = variable.split(".", 1)[1] if "." in variable else ""
                        actual_value = str(sys_vars.get(sys_key, "") or "")
                    elif variable.startswith("container."):
                        var_key = variable.split(".", 1)[1] if "." in variable else ""
                        actual_value = str(loop_vars.get(var_key, ""))
                    else:
                        parts = variable.split(".", 1)
                        ref_node = parts[0]
                        ref_field = parts[1] if len(parts) > 1 else "text"
                        out = (body_result.get("all_node_outputs", {}) or {}).get(ref_node, {})
                        actual = out.get("json_fields", {}).get(ref_field, out.get("text", ""))
                        actual_value = str(actual) if actual is not None else ""

                    evaluated.append(_eval_condition(actual_value, operator, rhs_value))

                if evaluated:
                    matched = all(evaluated) if termination_logic == "and" else any(evaluated)
                    if matched:
                        terminated = True
                        iteration_count += 1
                        break

            iteration_count += 1

        raw_payload = {
            "iterations": iteration_count,
            "terminated": terminated,
            "last_item": last_item,
            "vars": loop_vars,
        }
        json_fields = {
            "iterations": iteration_count,
            "terminated": terminated,
            "last_item": last_item,
            **loop_vars,
        }
        node_out = NodeOutput(
            status="ok",
            text=json.dumps(raw_payload, ensure_ascii=False),
            raw=raw_payload,
            json_fields=json_fields,
        )

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return loop_node


def _get_start_inputs(node_outputs: dict[str, NodeOutput]) -> dict[str, Any]:
    start_out = node_outputs.get("start")
    if start_out:
        return start_out.get("json_fields", {})
    return {}


# Node builder registry
NODE_BUILDERS: dict[str, str] = {
    "start": "_build_start_node",
    "llm": "_build_dag_llm_node",
    "output": "_build_output_node",
    "tool": "_build_dag_tool_node",
    "if_else": "_build_if_else_node",
    "parameter_extractor": "_build_param_extractor_node",
    "knowledge_retrieval": "_build_kr_node",
    "iteration": "_build_iteration_node",
    "loop": "_build_loop_node",
}


# ==================== DAG Compiler (Task 14.4) ====================


def build_workflow_dag_subgraph(
    skill: SkillDefinition,
    nodes: list,
    edges: list,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Any:
    """Compile a workflow DAG into a LangGraph StateGraph."""
    from collections import defaultdict, deque
    from langgraph.graph import END, StateGraph
    from app.assistant.skills.workflow_validator import validate_workflow_compile

    # Build node/edge maps
    node_map: dict[str, dict] = {}
    type_map: dict[str, str] = {}
    nodes_raw: list[dict[str, Any]] = []
    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        label = getattr(n, "label", None) or (n.get("label") if isinstance(n, dict) else None) or nid
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        node_map[nid] = _normalize_config(cfg) if isinstance(cfg, dict) else {}
        type_map[nid] = ntype or ""
        nodes_raw.append({"node_id": nid, "node_type": ntype, "label": label, "config": node_map[nid]})

    out_edges: dict[str, list[tuple[str, str, dict | None]]] = defaultdict(list)
    edges_raw: list[dict[str, Any]] = []
    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        src_handle = getattr(e, "source_handle", "output") or (e.get("source_handle", "output") if isinstance(e, dict) else "output")
        cond_expr = getattr(e, "condition_expr", None) or (e.get("condition_expr") if isinstance(e, dict) else None)
        out_edges[src].append((tgt, src_handle, cond_expr))
        edges_raw.append({"source_node_id": src, "target_node_id": tgt, "source_handle": src_handle})

    validation = validate_workflow_compile(nodes_raw, edges_raw, tool_names=set(tool_map.keys()))
    if not validation.valid:
        msg = "; ".join(err.message for err in validation.errors[:5])
        raise ValueError(f"Invalid workflow DAG: {msg}")

    # Topological sort
    in_degree: dict[str, int] = {nid: 0 for nid in node_map}
    adj: dict[str, list[str]] = defaultdict(list)
    for src, targets in out_edges.items():
        for tgt, _, _ in targets:
            adj[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    topo_order: list[str] = []
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for tgt in adj[nid]:
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                queue.append(tgt)

    # Build graph
    graph = StateGraph(WorkflowState)

    for nid in topo_order:
        ntype = type_map[nid]
        cfg = node_map[nid]

        if ntype == "start":
            node_fn = _build_start_node(cfg)
        elif ntype == "llm":
            node_fn = _build_dag_llm_node(nid, cfg, llm, node_llms=node_llms)
        elif ntype == "output":
            node_fn = _build_output_node(nid, cfg)
        elif ntype == "tool":
            node_fn = _build_dag_tool_node(nid, cfg, tool_map, args_llm, db_bind)
        elif ntype == "if_else":
            node_fn = _build_if_else_node(nid, cfg)
        elif ntype == "parameter_extractor":
            node_fn = _build_param_extractor_node(nid, cfg, llm, node_llms=node_llms)
        elif ntype == "knowledge_retrieval":
            node_fn = _build_kr_node(nid, cfg, tool_map, db_bind)
        elif ntype == "iteration":
            node_fn = _build_iteration_node(
                nid,
                cfg,
                llm,
                args_llm,
                tool_map,
                db_bind,
                node_llms=node_llms,
            )
        elif ntype == "loop":
            node_fn = _build_loop_node(
                nid,
                cfg,
                llm,
                args_llm,
                tool_map,
                db_bind,
                node_llms=node_llms,
            )
        else:
            raise ValueError(f"Unknown node type: {ntype}")

        graph.add_node(nid, node_fn)

    # Find start node
    start_nid = None
    for nid, ntype in type_map.items():
        if ntype == "start":
            start_nid = nid
            break
    if not start_nid:
        raise ValueError("Workflow DAG has no start node")

    graph.set_entry_point(start_nid)

    # Add edges
    for src_nid in topo_order:
        targets = out_edges.get(src_nid, [])
        if not targets:
            # Terminal node -> END
            graph.add_edge(src_nid, END)
            continue

        if type_map[src_nid] == "if_else":
            # Conditional edges: route based on branch_decisions
            handle_to_target: dict[str, str] = {}
            for tgt, handle, _ in targets:
                normalized_handle = "else" if handle == "default" else handle
                handle_to_target[normalized_handle] = tgt

            def _make_if_else_router(nid: str, h2t: dict[str, str]):
                def router(state: WorkflowState) -> str:
                    decisions = state.get("branch_decisions", {})
                    chosen = decisions.get(nid, "else")
                    if chosen == "default":
                        chosen = "else"
                    return h2t.get(chosen, h2t.get("else", h2t.get("default", END)))
                return router

            graph.add_conditional_edges(
                src_nid,
                _make_if_else_router(src_nid, handle_to_target),
                {tgt: tgt for tgt, _, _ in targets},
            )
        elif len(targets) == 1:
            graph.add_edge(src_nid, targets[0][0])
        else:
            # Fan-out: multiple edges from same source (parallel branches)
            for tgt, _, _ in targets:
                graph.add_edge(src_nid, tgt)

    return graph.compile()


# ==================== Agent Loop Subgraph (Phase 3) ====================

_AGENT_MAX_ITERATIONS = 10


def _build_agent_node(
    skill: SkillDefinition,
    llm: ChatOpenAI,
    tools: list,
) -> Callable[[AssistantState], dict]:
    """Task 3.1: 构建 agent_loop 的 agent 节点。"""
    llm_with_tools = llm.bind_tools(tools, parallel_tool_calls=False) if tools else llm

    def agent_node(state: AssistantState) -> dict:
        metadata = state.get("metadata", {})
        iteration = state.get("iteration_count", 0)

        if iteration >= _AGENT_MAX_ITERATIONS:
            return {
                "messages": [AIMessage(content="工具调用次数过多，未能完成任务。请尝试缩小问题范围或换一种问法。")],
                "iteration_count": iteration,
            }

        merged: AIMessageChunk | None = None
        final_chunks: list[str] = []
        for chunk in llm_with_tools.stream(state["messages"]):
            if merged is None:
                merged = chunk
            else:
                merged = merged + chunk
            if chunk.content:
                final_chunks.append(chunk.content)
                _emit(metadata, "on_content_delta", chunk=chunk.content)

        if merged is None:
            return {
                "messages": [AIMessage(content="")],
                "iteration_count": iteration + 1,
            }

        if getattr(merged, "tool_calls", None):
            return {
                "messages": [
                    AIMessage(
                        content=merged.content or "",
                        tool_calls=merged.tool_calls,
                        additional_kwargs=merged.additional_kwargs,
                        response_metadata=merged.response_metadata,
                    )
                ],
                "iteration_count": iteration + 1,
            }

        final_text = "".join(final_chunks)
        return {
            "messages": [AIMessage(content=final_text)],
            "iteration_count": iteration + 1,
        }

    return agent_node


def _build_tool_node(
    tools: list,
    db_bind: Any,
) -> Callable[[AssistantState], dict]:
    """Task 3.2: 构建 agent_loop 的 tool 节点。"""
    tool_map = {getattr(t, "name", ""): t for t in tools}

    def tool_node(state: AssistantState) -> dict:
        metadata = state.get("metadata", {})
        messages = state.get("messages", [])
        last_msg = messages[-1] if messages else None
        tool_calls = getattr(last_msg, "tool_calls", []) if last_msg else []

        new_messages: list[ToolMessage] = []
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_call_id = tc.get("id", f"tool_{uuid.uuid4().hex[:8]}")

            _emit(metadata, "on_tool_call_start",
                  tool_call_id=tool_call_id, tool_name=tool_name, args=tool_args)

            tool = tool_map.get(tool_name)
            status = "completed"
            result = ""
            if not tool:
                status = "error"
                result = f"工具 {tool_name} 不存在"
            else:
                wrapped = _wrap_tool_with_db(tool, db_bind)
                try:
                    result = wrapped(**tool_args)
                except Exception as e:
                    logger.error("Tool %s failed: %s", tool_name, e)
                    status = "error"
                    result = f"工具执行失败: {e}"

            result_str = _stringify(result)
            _emit(metadata, "on_tool_call_end",
                  tool_call_id=tool_call_id, status=status, result=result_str)
            new_messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))

        return {"messages": new_messages}

    return tool_node


def build_agent_subgraph(
    skill: SkillDefinition,
    llm: ChatOpenAI,
    tools: list,
    db_bind: Any,
) -> Any:
    """Task 3.3: 构建 agent_loop 子图。"""
    from langgraph.graph import END, StateGraph

    agent_node = _build_agent_node(skill, llm, tools)
    tool_node = _build_tool_node(tools, db_bind)

    graph = StateGraph(AssistantState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")

    def should_continue(state: AssistantState) -> str:
        msgs = state.get("messages", [])
        last = msgs[-1] if msgs else None
        if last and getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# ==================== LRU Graph Cache (Task 5.3) ====================

import threading

_graph_cache: dict[tuple, Any] = {}
_graph_cache_order: list[tuple] = []
_GRAPH_CACHE_MAX = 32
_graph_cache_lock = threading.Lock()


def _make_cache_key(skill: SkillDefinition, kb_enabled: bool, model: str) -> tuple:
    """构建图缓存的复合 key。"""
    import hashlib

    parts = [skill.name, skill.langgraph_pattern or ""]

    if skill.langgraph_pattern == "agent_loop":
        parts.append(hashlib.md5((skill.system_prompt or "").encode()).hexdigest())
    parts.append(hashlib.md5(json.dumps(sorted(skill.tools or []), ensure_ascii=False).encode()).hexdigest())

    if skill.langgraph_pattern == "workflow_dag":
        wf_nodes = getattr(skill, "workflow_nodes", None) or []
        wf_edges = getattr(skill, "workflow_edges", None) or []
        nodes_data = []
        for n in wf_nodes:
            nodes_data.append({
                "node_id": getattr(n, "node_id", ""),
                "node_type": getattr(n, "node_type", ""),
                "config": getattr(n, "config", None),
            })
        edges_data = []
        for e in wf_edges:
            edges_data.append({
                "source_node_id": getattr(e, "source_node_id", ""),
                "target_node_id": getattr(e, "target_node_id", ""),
                "source_handle": getattr(e, "source_handle", ""),
            })
        dag_str = json.dumps({"n": nodes_data, "e": edges_data}, ensure_ascii=False, sort_keys=True)
        parts.append(hashlib.md5(dag_str.encode()).hexdigest())

    parts.append(str(kb_enabled))
    parts.append(model)
    return tuple(parts)


def _get_or_compile_graph(
    key: tuple,
    compile_fn: Callable[[], Any],
) -> Any:
    """LRU 缓存：命中返回，未命中则编译并缓存。线程安全。"""
    with _graph_cache_lock:
        if key in _graph_cache:
            _graph_cache_order.remove(key)
            _graph_cache_order.append(key)
            return _graph_cache[key]

    compiled = compile_fn()

    with _graph_cache_lock:
        _graph_cache[key] = compiled
        _graph_cache_order.append(key)

        while len(_graph_cache_order) > _GRAPH_CACHE_MAX:
            evict = _graph_cache_order.pop(0)
            _graph_cache.pop(evict, None)

    return compiled


# ==================== LangGraph Engine (Tasks 5.1-5.2, 6.1-6.2, 7.1) ====================


class LangGraphEngine:
    """LangGraph 执行引擎入口。"""

    def __init__(self, api_key: str, base_url: str, model: str, db: Session | None = None):
        default_headers = build_openai_compat_client_headers()
        self.model = model
        self.db = db
        self.llm = ChatOpenAI(
            api_key=(api_key or "").strip(),
            base_url=(base_url or "").strip(),
            model=model,
            streaming=True,
            default_headers=default_headers,
        )
        self.args_llm = ChatOpenAI(
            api_key=(api_key or "").strip(),
            base_url=(base_url or "").strip(),
            model=model,
            streaming=False,
            temperature=0,
            default_headers=default_headers,
        )
        self._tool_cache: dict[str, Any] = {}
        self._node_llm_cache: dict[str, tuple[tuple[str, str, str], ChatOpenAI]] = {}

    def _get_tool(self, tool_name: str) -> Any:
        if tool_name in self._tool_cache:
            return self._tool_cache[tool_name]
        from app.assistant_config.registry import ToolRegistry
        if self.db is not None:
            registry = ToolRegistry(self.db)
            tool = registry.resolve(tool_name)
        else:
            tool = ToolRegistry.resolve_system_tool(tool_name)
        if tool:
            self._tool_cache[tool_name] = tool
        return tool

    def _get_db_bind(self) -> Any:
        if self.db is not None:
            return self.db.get_bind()
        from app.database import engine
        return engine

    def _build_tools(self, skill: SkillDefinition) -> list:
        """构建工具列表，Task 6.1: agent_loop 模式下 kb_search 作为普通工具。"""
        tool_names = list(skill.tools or [])
        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))

        # agent_loop + kb_enabled: 将 kb_search 加入工具列表
        if skill.langgraph_pattern == "agent_loop" and kb_enabled:
            if "kb_search" not in tool_names:
                tool_names.append("kb_search")

        tools = []
        for name in tool_names:
            tool = self._get_tool(name)
            if tool:
                tools.append(tool)
            else:
                logger.warning("LangGraph tool not found: %s", name)
        return tools

    def _build_agent_system_prompt(self, skill: SkillDefinition, tool_names: list[str]) -> str:
        """Task 6.2: 构建 agent_loop 系统提示词，含 KB 引导。"""
        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))
        today = date.today()

        prompt = (
            f"你是 MindAtlas 的 AI 助手，正在执行 Skill: {skill.name}\n\n"
            f"## Skill 描述\n{skill.description}\n\n"
            f"## 当前日期\n{today.isoformat()}（{today.strftime('%A')}）\n\n"
            f"## 可用工具\n你可以使用以下工具来完成任务：{', '.join(tool_names)}\n\n"
            f"## 执行原则\n"
            f"1. 根据用户需求，自主决定是否调用工具以及调用顺序\n"
            f"2. 可以多次调用工具来收集信息\n"
            f"3. 完成任务后，给出清晰友好的回复\n"
        )

        if skill.system_prompt:
            prompt += f"\n## 额外指令\n{skill.system_prompt}\n"

        if kb_enabled:
            prompt += f"\n{KB_CITATION_INSTRUCTIONS}\n"
            prompt += (
                "\n## 知识库使用要求\n"
                "当用户提问可能涉及已有知识/记录时，你必须先调用 kb_search 检索相关资料。\n"
            )

        return prompt

    def _resolve_node_custom_llm(self, model_id: str, *, node_id: str) -> ChatOpenAI:
        if self.db is None:
            raise RuntimeError(
                f"Workflow node {node_id} requires custom model {model_id}, but DB session is unavailable"
            )

        cfg = resolve_openai_compat_config_by_model_id(
            self.db,
            model_id=model_id,
            model_type="llm",
        )
        if cfg is None:
            raise RuntimeError(
                f"Workflow node {node_id} references unavailable llm model: {model_id}"
            )

        cache_key = str(cfg.model_id)
        fingerprint = (cfg.base_url, cfg.model, cfg.api_key)
        cached = self._node_llm_cache.get(cache_key)
        if cached and cached[0] == fingerprint:
            return cached[1]

        default_headers = build_openai_compat_client_headers()
        node_llm = ChatOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            streaming=True,
            default_headers=default_headers,
        )
        self._node_llm_cache[cache_key] = (fingerprint, node_llm)
        return node_llm

    def _resolve_workflow_node_llms(self, skill: SkillDefinition) -> dict[str, ChatOpenAI]:
        node_llms: dict[str, ChatOpenAI] = {}
        if skill.langgraph_pattern != "workflow_dag":
            return node_llms

        def _bind_model_for_node(*, runtime_key: str, cfg: dict[str, Any]) -> None:
            model_source = str(cfg.get("model_source", "default") or "default").strip().lower()
            if model_source in {"", "default"}:
                return
            if model_source != "custom":
                raise RuntimeError(
                    f"Workflow node {runtime_key} has unsupported modelSource: {model_source}"
                )

            model_id = str(cfg.get("model_id", "") or "").strip()
            if not model_id:
                raise RuntimeError(
                    f"Workflow node {runtime_key} requires modelId when modelSource=custom"
                )
            node_llms[runtime_key] = self._resolve_node_custom_llm(model_id, node_id=runtime_key)

        for node in getattr(skill, "workflow_nodes", None) or []:
            node_id = str(getattr(node, "node_id", "") or "").strip()
            node_type = str(getattr(node, "node_type", "") or "").strip()
            if not node_id:
                continue

            raw_cfg = getattr(node, "config", None)
            cfg = _normalize_config(raw_cfg) if isinstance(raw_cfg, dict) else {}
            if node_type in {"llm", "parameter_extractor"}:
                _bind_model_for_node(runtime_key=node_id, cfg=cfg)
                continue
            if node_type not in {"iteration", "loop"}:
                continue

            body_nodes = _normalize_container_body_nodes(cfg)
            for body in body_nodes:
                body_id = str(body.get("node_id", "") or "").strip()
                body_type = str(body.get("node_type", "") or "").strip()
                if not body_id or body_type not in {"llm", "parameter_extractor"}:
                    continue
                body_cfg = body.get("config") if isinstance(body.get("config"), dict) else {}
                runtime_key = f"{node_id}::{body_id}"
                _bind_model_for_node(runtime_key=runtime_key, cfg=body_cfg)

        return node_llms

    def execute(
        self,
        skill: SkillDefinition,
        user_input: str,
        history: list[dict],
        runtime_context: dict[str, Any] | None = None,
        on_tool_call_start: Callable | None = None,
        on_tool_call_end: Callable | None = None,
        on_analysis_start: Callable | None = None,
        on_analysis_delta: Callable | None = None,
        on_analysis_end: Callable | None = None,
        on_node_start: Callable | None = None,
        on_node_output_delta: Callable | None = None,
        on_node_end: Callable | None = None,
        on_branch_decision: Callable | None = None,
    ) -> Iterator[str]:
        """执行 LangGraph skill，yield 流式内容。"""
        logger.info("LangGraphEngine.execute: skill=%s pattern=%s",
                     skill.name, skill.langgraph_pattern)

        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))
        db_bind = self._get_db_bind()
        tools = self._build_tools(skill)
        tool_map = {getattr(t, "name", ""): t for t in tools}
        now = datetime.utcnow()
        context = runtime_context or {}
        raw_stream_output = context.get("stream_output", context.get("streamOutput", True))
        try:
            stream_output_enabled = _parse_output_boolean(raw_stream_output)
        except Exception:
            stream_output_enabled = True
        conversation_id = str(context.get("conversation_id") or "")
        sys_vars = {
            "date": now.date().isoformat(),
            "datetime": now.replace(microsecond=0).isoformat(),
            "conversation_id": conversation_id,
        }

        # 构建回调 metadata：通过线程安全队列实时转发事件，避免节点内流式内容被整段缓存
        runtime_events: Queue[tuple[str, dict[str, Any]]] = Queue()

        def _push_runtime_event(event_name: str, **payload: Any) -> None:
            runtime_events.put((event_name, payload))

        metadata: dict[str, Any] = {
            "on_content_delta": lambda chunk: _push_runtime_event("content_delta", chunk=chunk),
        }
        if on_tool_call_start:
            metadata["on_tool_call_start"] = lambda tool_call_id, tool_name, args: (
                _push_runtime_event(
                    "tool_call_start",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=args,
                )
            )
        if on_tool_call_end:
            metadata["on_tool_call_end"] = lambda tool_call_id, status, result: (
                _push_runtime_event(
                    "tool_call_end",
                    tool_call_id=tool_call_id,
                    status=status,
                    result=result,
                )
            )
        if on_analysis_start:
            metadata["on_analysis_start"] = lambda analysis_id: (
                _push_runtime_event("analysis_start", analysis_id=analysis_id)
            )
        if on_analysis_delta:
            metadata["on_analysis_delta"] = lambda analysis_id, chunk: (
                _push_runtime_event("analysis_delta", analysis_id=analysis_id, chunk=chunk)
            )
        if on_analysis_end:
            metadata["on_analysis_end"] = lambda analysis_id: (
                _push_runtime_event("analysis_end", analysis_id=analysis_id)
            )
        if on_node_start:
            metadata["on_node_start"] = lambda node_id, node_type: (
                _push_runtime_event("node_start", node_id=node_id, node_type=node_type)
            )
        if on_node_output_delta:
            metadata["on_node_output_delta"] = lambda node_id, delta: (
                _push_runtime_event("node_output_delta", node_id=node_id, delta=delta)
            )
        if on_node_end:
            metadata["on_node_end"] = lambda node_id, status: (
                _push_runtime_event("node_end", node_id=node_id, status=status)
            )
        if on_branch_decision:
            metadata["on_branch_decision"] = lambda node_id, handle: (
                _push_runtime_event("branch_decision", node_id=node_id, handle=handle)
            )

        # 构建初始消息
        messages: list[BaseMessage] = [SystemMessage(content="")]
        for h in (history or [])[-10:]:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role == "system":
                continue
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
        messages.append(HumanMessage(content=user_input))

        # 根据 pattern 编译/获取图
        cache_key = _make_cache_key(skill, kb_enabled, self.model)

        pattern = skill.langgraph_pattern
        if pattern not in {"agent_loop", "workflow_dag"}:
            raise ValueError(
                f"Unsupported langgraph_pattern '{pattern}' for skill '{skill.name}'. "
                "Supported patterns: agent_loop, workflow_dag."
            )

        workflow_node_types: dict[str, str] = {}
        node_llms: dict[str, ChatOpenAI] = {}
        output_stream_source_node_id = ""

        if pattern == "agent_loop":
            # 设置 system prompt
            tool_names = [getattr(t, "name", "") for t in tools]
            sys_prompt = self._build_agent_system_prompt(skill, tool_names)
            messages[0] = SystemMessage(content=sys_prompt)

            compiled = _get_or_compile_graph(
                cache_key,
                lambda: build_agent_subgraph(skill, self.llm, tools, db_bind),
            )
        elif pattern == "workflow_dag":
            wf_nodes = getattr(skill, "workflow_nodes", None) or []
            wf_edges = getattr(skill, "workflow_edges", None) or []
            if not wf_nodes:
                raise ValueError(f"workflow_dag skill {skill.name} has no workflow nodes")
            node_llms = self._resolve_workflow_node_llms(skill)
            workflow_node_configs: dict[str, dict[str, Any]] = {}
            workflow_node_types = {
                str(getattr(n, "node_id", "") or ""): str(getattr(n, "node_type", "") or "")
                for n in wf_nodes
                if getattr(n, "node_id", None)
            }
            for node in wf_nodes:
                node_id = str(getattr(node, "node_id", "") or "").strip()
                if not node_id:
                    continue
                raw_cfg = getattr(node, "config", None)
                workflow_node_configs[node_id] = _normalize_config(raw_cfg) if isinstance(raw_cfg, dict) else {}

            output_node_ids = [nid for nid, ntype in workflow_node_types.items() if ntype == "output"]
            if len(output_node_ids) == 1:
                output_node_cfg = workflow_node_configs.get(output_node_ids[0], {})
                output_mode = str(output_node_cfg.get("output_mode", "text") or "text").strip().lower()
                if output_mode == "json":
                    output_mode = "structured"
                if output_mode == "text":
                    text_template = output_node_cfg.get("text_template", "")
                    if isinstance(text_template, str):
                        single_ref = _extract_single_template_reference(text_template)
                        if single_ref is not None:
                            ref_node_id, ref_field = single_ref
                            ref_node_cfg = workflow_node_configs.get(ref_node_id, {})
                            ref_output_mode = str(
                                ref_node_cfg.get("output_mode", "text") or "text"
                            ).strip().lower()
                            if ref_output_mode == "json":
                                ref_output_mode = "structured"
                            if (
                                workflow_node_types.get(ref_node_id) == "llm"
                                and ref_output_mode == "text"
                                and ref_field in {"response", "text"}
                            ):
                                output_stream_source_node_id = ref_node_id

            compiled = _get_or_compile_graph(
                cache_key,
                lambda: build_workflow_dag_subgraph(
                    skill, wf_nodes, wf_edges,
                    self.llm, self.args_llm, tool_map, db_bind,
                    node_llms=node_llms,
                ),
            )
        # 构建初始 state
        if pattern == "workflow_dag":
            initial_state: dict = {
                "messages": messages,
                "skill_name": skill.name,
                "user_input": user_input,
                "kb_enabled": kb_enabled,
                "metadata": metadata,
                "node_outputs": {},
                "execution_trace": [],
                "branch_decisions": {},
                "sys_vars": sys_vars,
                "workflow_node_types": workflow_node_types,
                "node_llms": node_llms,
                "stream_output_enabled": stream_output_enabled,
                "output_stream_source_node_id": output_stream_source_node_id,
            }
        else:
            initial_state: dict = {
                "messages": messages,
                "skill_name": skill.name,
                "user_input": user_input,
                "kb_enabled": kb_enabled,
                "iteration_count": 0,
                "metadata": metadata,
                "current_step": 1,
                "step_outputs": {},
                "summary_trace": [],
            }

        # 执行图
        try:
            graph_errors: list[Exception] = []

            def _run_graph() -> None:
                try:
                    for _ in compiled.stream(initial_state):
                        _push_runtime_event("graph_tick")
                except Exception as exc:  # pragma: no cover - 由主线程统一抛出
                    graph_errors.append(exc)
                finally:
                    _push_runtime_event("graph_done")

            graph_thread = Thread(target=_run_graph, daemon=True)
            graph_thread.start()

            graph_done = False
            buffered_content_chunks: list[str] = []
            while not graph_done or not runtime_events.empty():
                try:
                    event_name, payload = runtime_events.get(timeout=0.1)
                except Empty:
                    if graph_done:
                        break
                    continue

                if event_name == "content_delta":
                    chunk = payload.get("chunk", "")
                    if chunk:
                        if stream_output_enabled:
                            yield str(chunk)
                        else:
                            buffered_content_chunks.append(str(chunk))
                    continue

                if event_name == "tool_call_start" and on_tool_call_start:
                    on_tool_call_start(
                        payload.get("tool_call_id", ""),
                        payload.get("tool_name", ""),
                        payload.get("args", {}),
                    )
                    yield ""
                    continue

                if event_name == "tool_call_end" and on_tool_call_end:
                    on_tool_call_end(
                        payload.get("tool_call_id", ""),
                        payload.get("status", ""),
                        payload.get("result", ""),
                    )
                    yield ""
                    continue

                if event_name == "analysis_start" and on_analysis_start:
                    on_analysis_start(payload.get("analysis_id", ""))
                    yield ""
                    continue

                if event_name == "analysis_delta" and on_analysis_delta:
                    on_analysis_delta(
                        payload.get("analysis_id", ""),
                        payload.get("chunk", ""),
                    )
                    yield ""
                    continue

                if event_name == "analysis_end" and on_analysis_end:
                    on_analysis_end(payload.get("analysis_id", ""))
                    yield ""
                    continue

                if event_name == "node_start" and on_node_start:
                    on_node_start(payload.get("node_id", ""), payload.get("node_type", ""))
                    yield ""
                    continue

                if event_name == "node_output_delta" and on_node_output_delta:
                    on_node_output_delta(payload.get("node_id", ""), payload.get("delta", ""))
                    yield ""
                    continue

                if event_name == "node_end" and on_node_end:
                    on_node_end(payload.get("node_id", ""), payload.get("status", ""))
                    yield ""
                    continue

                if event_name == "branch_decision" and on_branch_decision:
                    on_branch_decision(payload.get("node_id", ""), payload.get("handle", ""))
                    yield ""
                    continue

                if event_name == "graph_tick":
                    # 心跳：让上层有机会冲刷 SSE 队列
                    yield ""
                    continue

                if event_name == "graph_done":
                    graph_done = True
                    continue

            graph_thread.join()
            if graph_errors:
                raise graph_errors[0]
            if not stream_output_enabled and buffered_content_chunks:
                yield "".join(buffered_content_chunks)
        except Exception as e:
            logger.error("LangGraph execution failed: skill=%s error=%s",
                         skill.name, e, exc_info=True)
            raise
