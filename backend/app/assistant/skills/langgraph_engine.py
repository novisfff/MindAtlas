"""LangGraph 执行引擎 - 支持 agent_loop 与 workflow_dag 两种子图模式"""
from __future__ import annotations

import json
import logging
import re
import uuid
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

from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skills.base import SkillDefinition
from app.assistant.tools._context import reset_current_db, set_current_db

logger = logging.getLogger(__name__)

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


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


def _resolve_node_template_vars(
    template: str,
    node_outputs: dict[str, NodeOutput],
    start_inputs: dict[str, Any],
    sys_vars: dict[str, str] | None = None,
) -> str:
    """Resolve {{node_id.field}} template variables from DAG node outputs."""
    if not template:
        return ""
    sys_ctx = sys_vars or {}

    def _repl(match: re.Match) -> str:
        node_id = match.group(1)
        field = match.group(2)

        if node_id == "start":
            return _truncate(start_inputs.get(field, ""))
        if node_id == "sys":
            return _truncate(sys_ctx.get(field, ""))

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
) -> Callable[[WorkflowState], dict]:
    def llm_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}

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

        raw_is_output = node_cfg.get("is_output", False)
        if isinstance(raw_is_output, str):
            is_output = raw_is_output.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            is_output = bool(raw_is_output)
        output_fields = node_cfg.get("output_fields") or []
        field_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in output_fields]

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

        context_data = {}
        for nid, out in node_outputs.items():
            context_data[nid] = _truncate(out.get("text", ""), 2000)
        context_snapshot = json.dumps(context_data, ensure_ascii=False, default=str)[:4000]

        msgs = [
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": f"上下文数据：\n{context_snapshot}"},
            {"role": "user", "content": user_input_rendered},
        ]

        _emit(metadata, "on_node_start", node_id=node_id, node_type="llm")

        def _run_once(allow_content_stream: bool) -> str:
            chunks: list[str] = []
            for chunk in llm.stream(msgs):
                if not chunk.content:
                    continue
                chunks.append(chunk.content)
                _emit(metadata, "on_node_output_delta", node_id=node_id, delta=chunk.content)
                if allow_content_stream and is_output:
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
            if is_output:
                _emit(metadata, "on_content_delta", chunk=json_text)

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return llm_node


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


def _build_template_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def template_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        template = node_cfg.get("template", "")

        _emit(metadata, "on_node_start", node_id=node_id, node_type="template")

        rendered = _resolve_node_template_vars(template, node_outputs, start_inputs, sys_vars)
        node_out = NodeOutput(status="ok", text=rendered, raw=rendered, json_fields={})

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return template_node


def _build_param_extractor_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
) -> Callable[[WorkflowState], dict]:
    def param_extractor_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = _get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}

        instruction = _resolve_node_template_vars(
            node_cfg.get("instruction", ""), node_outputs, start_inputs, sys_vars,
        )
        output_fields = node_cfg.get("output_fields") or []
        field_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in output_fields]

        from app.assistant.skills.base import OutputFieldSpec, build_json_output_constraint
        specs = []
        for f in output_fields:
            if isinstance(f, dict):
                try:
                    specs.append(OutputFieldSpec(**f))
                except Exception:
                    specs.append(OutputFieldSpec(name=f.get("name", "field")))
        constraint = build_json_output_constraint(specs) if specs else ""

        prompt = f"从以下文本中提取结构化参数。\n\n{instruction}\n\n{constraint}"
        msgs = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": state.get("user_input", "")},
        ]

        _emit(metadata, "on_node_start", node_id=node_id, node_type="parameter_extractor")

        chunks: list[str] = []
        for chunk in llm.stream(msgs):
            if chunk.content:
                chunks.append(chunk.content)

        text = "".join(chunks).strip()
        node_out: NodeOutput = {"status": "ok", "text": text, "raw": None, "json_fields": {}}

        if text:
            parsed = _extract_json_object(text)
            if parsed:
                filtered = {k: parsed.get(k) for k in field_names} if field_names else parsed
                node_out["raw"] = filtered
                node_out["json_fields"] = filtered

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

        query = _resolve_node_template_vars(
            node_cfg.get("query", "{{start.user_input}}"), node_outputs, start_inputs, sys_vars,
        )

        _emit(metadata, "on_node_start", node_id=node_id, node_type="knowledge_retrieval")

        kb_tool = tool_map.get("kb_search")
        result_text = ""
        if kb_tool:
            wrapped = _wrap_tool_with_db(kb_tool, db_bind)
            try:
                result_text = _stringify(wrapped(query=query))
            except Exception as e:
                logger.warning("KR node %s failed: %s", node_id, e)
                result_text = f"知识库检索失败: {e}"
        else:
            result_text = "知识库工具不可用"

        node_out = NodeOutput(status="ok", text=result_text, raw=result_text, json_fields={})
        _emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return kr_node


def _build_aggregator_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def aggregator_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))

        _emit(metadata, "on_node_start", node_id=node_id, node_type="variable_aggregator")

        # Collect outputs from specified source nodes or all available
        source_nodes = node_cfg.get("source_nodes", [])
        merged: dict[str, Any] = {}
        for src in source_nodes:
            out = node_outputs.get(src)
            if out:
                merged[src] = out.get("raw") or out.get("text", "")

        if not source_nodes:
            # Auto-collect from all predecessors (will be populated by edges)
            merged = {k: (v.get("raw") or v.get("text", "")) for k, v in node_outputs.items()}

        merged_text = json.dumps(merged, ensure_ascii=False, default=str)
        node_out = NodeOutput(status="ok", text=merged_text, raw=merged, json_fields=merged)

        _emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return aggregator_node


def _get_start_inputs(node_outputs: dict[str, NodeOutput]) -> dict[str, Any]:
    start_out = node_outputs.get("start")
    if start_out:
        return start_out.get("json_fields", {})
    return {}


# Node builder registry
NODE_BUILDERS: dict[str, str] = {
    "start": "_build_start_node",
    "llm": "_build_dag_llm_node",
    "tool": "_build_dag_tool_node",
    "if_else": "_build_if_else_node",
    "template": "_build_template_node",
    "parameter_extractor": "_build_param_extractor_node",
    "knowledge_retrieval": "_build_kr_node",
    "variable_aggregator": "_build_aggregator_node",
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
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        node_map[nid] = _normalize_config(cfg) if isinstance(cfg, dict) else {}
        type_map[nid] = ntype or ""
        nodes_raw.append({"node_id": nid, "node_type": ntype, "config": node_map[nid]})

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
            node_fn = _build_dag_llm_node(nid, cfg, llm)
        elif ntype == "tool":
            node_fn = _build_dag_tool_node(nid, cfg, tool_map, args_llm, db_bind)
        elif ntype == "if_else":
            node_fn = _build_if_else_node(nid, cfg)
        elif ntype == "template":
            node_fn = _build_template_node(nid, cfg)
        elif ntype == "parameter_extractor":
            node_fn = _build_param_extractor_node(nid, cfg, llm)
        elif ntype == "knowledge_retrieval":
            node_fn = _build_kr_node(nid, cfg, tool_map, db_bind)
        elif ntype == "variable_aggregator":
            node_fn = _build_aggregator_node(nid, cfg)
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
            compiled = _get_or_compile_graph(
                cache_key,
                lambda: build_workflow_dag_subgraph(
                    skill, wf_nodes, wf_edges,
                    self.llm, self.args_llm, tool_map, db_bind,
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
                        yield str(chunk)
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
        except Exception as e:
            logger.error("LangGraph execution failed: skill=%s error=%s",
                         skill.name, e, exc_info=True)
            raise
