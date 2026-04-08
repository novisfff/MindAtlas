from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "build_agent_node": "agent_node",
    "build_tool_node": "agent_tool_node",
    "build_code_executor_node": "code_executor_node",
    "build_dag_agent_node": "dag_agent_node",
    "build_http_request_node": "http_request_node",
    "build_human_in_loop_node": "human_in_loop_node",
    "build_if_else_node": "if_else_node",
    "build_iteration_node": "iteration_node",
    "build_kr_node": "knowledge_node",
    "build_dag_llm_node": "llm_node",
    "build_loop_node": "loop_node",
    "build_output_node": "output_node",
    "build_param_extractor_node": "param_extractor_node",
    "build_start_node": "start_node",
    "build_dag_tool_node": "tool_node",
    "build_variable_assign_node": "variable_assign_node",
    "build_workflow_call_node": "workflow_call_node",
}


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(f"app.assistant.workflow.engine.node_builders.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
