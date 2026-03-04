from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow.engine.runtime_helpers import (
    cfg_bool_value,
    cfg_int_value,
    cfg_string_list,
    emit,
    extract_json_object,
    get_start_inputs,
    render_memory_injection_block,
    resolve_node_template_vars,
    resolve_start_memory_mode,
    truncate,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.config import get_settings

def build_dag_llm_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def llm_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = state.get("env_vars", {}) or {}
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
        system_prompt = resolve_node_template_vars(
            system_prompt_raw, node_outputs, start_inputs, sys_vars, env_vars=env_vars
        )
        output_mode = str(node_cfg.get("output_mode", "text") or "text").strip().lower()
        if output_mode == "json":
            output_mode = "structured"
        if output_mode not in {"text", "structured"}:
            raise RuntimeError(f"DAG LLM node {node_id}: unsupported output_mode={output_mode}")

        user_input_template = node_cfg.get("user_input", "{{start.user_input}}")
        if not isinstance(user_input_template, str):
            user_input_template = "{{start.user_input}}"
        user_input_rendered = resolve_node_template_vars(
            user_input_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
        )
        if not user_input_rendered.strip():
            user_input_rendered = start_inputs.get("user_input", "") or state.get("user_input", "")

        knowledge_enabled = cfg_bool_value(
            node_cfg, "knowledge_enabled", "knowledgeEnabled", default=False
        )
        knowledge_source_node_ids = cfg_string_list(
            node_cfg, "knowledge_source_node_ids", "knowledgeSourceNodeIds"
        )
        raw_inject_mode = str(
            node_cfg.get("knowledge_inject_mode", node_cfg.get("knowledgeInjectMode", "references_only"))
            or "references_only"
        ).strip().lower()
        knowledge_inject_mode = raw_inject_mode if raw_inject_mode in {"references_only", "full_payload"} else "references_only"
        knowledge_max_refs = cfg_int_value(
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
        memory_mode = resolve_start_memory_mode(
            {"memory_mode": state.get("memory_mode")},
            default_mode="auto",
        )
        memory_context = state.get("memory_context") if isinstance(state.get("memory_context"), dict) else {}
        l0_messages_raw = memory_context.get("l0_messages")
        l0_messages: list[dict[str, str]] = []
        if isinstance(l0_messages_raw, list):
            for item in l0_messages_raw:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "") or "").strip().lower()
                content = str(item.get("content", "") or "").strip()
                if role not in {"user", "assistant"} or not content:
                    continue
                l0_messages.append({"role": role, "content": content})

        structured_mode = output_mode == "structured"
        if structured_mode and field_names:
            from app.assistant.skill_catalog.base import OutputFieldSpec, build_json_output_constraint
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
        if memory_mode == "auto":
            settings = get_settings()
            memory_block = render_memory_injection_block(
                memory_context=memory_context,
                max_chars=max(
                    1,
                    int(getattr(settings, "assistant_memory_injection_max_chars", 30000) or 30000),
                ),
            )
            if memory_block:
                full_prompt += f"{memory_block}\n\n"
        if constraint:
            full_prompt += f"## {constraint}\n\n"

        msgs = [
            {"role": "system", "content": full_prompt},
        ]
        if memory_mode == "auto" and l0_messages:
            msgs.extend(l0_messages)

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
                    "content": truncate(
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

        emit(metadata, "on_node_start", node_id=node_id, node_type="llm")

        def _run_once(allow_content_stream: bool) -> str:
            chunks: list[str] = []
            for chunk in llm_for_node.stream(msgs):
                if not chunk.content:
                    continue
                chunks.append(chunk.content)
                emit(metadata, "on_node_output_delta", node_id=node_id, delta=chunk.content)
                if (
                    allow_content_stream
                    and stream_output_enabled
                    and output_stream_source_node_id == node_id
                ):
                    emit(metadata, "on_content_delta", chunk=chunk.content)
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

            parsed = extract_json_object(text)
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
            if stream_output_enabled:
                has_stream_source = "output_stream_source_node_id" in state
                if (not has_stream_source) or output_stream_source_node_id == node_id:
                    emit(metadata, "on_content_delta", chunk=json_text)

        emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return llm_node
