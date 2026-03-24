from __future__ import annotations

from typing import Any, Callable

from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine import engine as engine_runtime
from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    extract_json_object,
    get_start_inputs,
    logger,
    resolve_node_template_vars,
    stringify,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_kr_node(
    node_id: str,
    node_cfg: dict,
    tool_map: dict[str, Any],
    db_bind: Any,
) -> Callable[[WorkflowState], dict]:
    def kr_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        locale = sys_vars.get("locale")
        env_vars = state.get("env_vars", {}) or {}

        query_template = node_cfg.get("query", "{{start.user_input}}")
        if not isinstance(query_template, str):
            query_template = "{{start.user_input}}"
        query = resolve_node_template_vars(
            query_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
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

        emit(metadata, "on_node_start", node_id=node_id, node_type="knowledge_retrieval")

        kb_tool = tool_map.get("kb_search")
        result_text = ""
        raw_payload: Any = ""
        if kb_tool:
            wrapped = engine_runtime._wrap_tool_with_db(kb_tool, db_bind)
            invoke_args: dict[str, Any] = {"query": query}
            if mode is not None:
                invoke_args["mode"] = mode
            if top_k is not None:
                invoke_args["top_k"] = top_k
            try:
                raw_result = wrapped(**invoke_args)
                result_text = stringify(raw_result)
                raw_payload = raw_result
                if isinstance(raw_result, str):
                    parsed = extract_json_object(raw_result)
                    if parsed is not None:
                        raw_payload = parsed
            except Exception as e:
                logger.warning("KR node %s failed: %s", node_id, e)
                result_text = _copy.build_knowledge_failure_message(locale, e)
                raw_payload = {"error": str(e)}
        else:
            result_text = _copy.build_knowledge_unavailable_message(locale)
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
            result_value = result_text or _copy.build_knowledge_result_fallback(locale, references_count)
        if not result_text:
            result_text = stringify(result_value)

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
        emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return kr_node
