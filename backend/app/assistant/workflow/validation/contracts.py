from __future__ import annotations

import re

_IF_ELSE_HANDLE_RE = re.compile(r"[a-zA-Z0-9_]+")
_IF_ELSE_NEW_OPERATORS = {
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "is",
    "is_not",
    "is_empty",
    "is_not_empty",
}
_IF_ELSE_LEGACY_OPERATORS = {"equals", "not_equals", "gt", "lt", "gte", "lte"}
_IF_ELSE_ALL_OPERATORS = _IF_ELSE_NEW_OPERATORS | _IF_ELSE_LEGACY_OPERATORS
_IF_ELSE_LEGACY_OPERATOR_MAP = {
    "equals": "is",
    "not_equals": "is_not",
}
_SYS_FIELDS = {"date", "datetime", "conversation_id"}
_START_INPUT_MODES = {"text", "structured"}
_START_INPUT_FIELD_TYPES = {"string", "number", "integer", "boolean"}
_START_INPUT_FIELD_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_OUTPUT_FIELD_NAME_RE = re.compile(r"[a-zA-Z0-9_]+")
_OUTPUT_FIELD_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
_CODE_EXECUTOR_LANGUAGES = {"python", "javascript"}
_CODE_EXECUTOR_ENTRYPOINT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_CODE_EXECUTOR_INPUT_KEY_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_HUMAN_FIELD_TYPES = {"string", "number", "integer", "boolean", "array"}
_HUMAN_FIELD_WIDGETS = {"input", "textarea", "switch", "select", "radio", "tag_selector", "date", "time"}
_HUMAN_FIELD_WIDGET_ALLOWED_TYPES: dict[str, set[str]] = {
    "input": {"string", "number", "integer"},
    "textarea": {"string"},
    "switch": {"boolean"},
    "select": {"string", "number", "integer"},
    "radio": {"string", "number", "integer"},
    "tag_selector": {"array"},
    "date": {"string"},
    "time": {"string"},
}
_ENV_VAR_PATH_RE = re.compile(r"env\\.([a-zA-Z_][a-zA-Z0-9_]*)$")
_SUPPORTED_NODE_TYPES = {
    "start",
    "llm",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "iteration",
    "loop",
    "code_executor",
    "variable_assign",
    "human_in_loop",
    "output",
}
_CONTAINER_BODY_ALLOWED_NODE_TYPES = {
    "start",
    "llm",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "code_executor",
    "variable_assign",
    "human_in_loop",
}
_REMOVED_NODE_TYPE_MESSAGES = {
    "answer": "Node type 'answer' is no longer supported. Use the output node instead.",
    "template": "Node type 'template' has been removed. Please refactor with supported nodes.",
    "variable_aggregator": "Node type 'variable_aggregator' has been removed. Please refactor with supported nodes.",
}

