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
_SYS_FIELDS = {
    "date",
    "datetime",
    "conversation_id",
    "locale",
    "language",
    "openclaw_source",
    "openclaw_channel",
    "openclaw_session",
    "openclaw_tool",
}
_START_INPUT_MODES = {"text", "structured"}
_START_INPUT_FIELD_TYPES = {"string", "number", "integer", "boolean", "array"}
_START_INPUT_FIELD_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_START_MEMORY_MODES = {"auto", "off", "structured"}
_START_MEMORY_STRUCTURED_FIELDS = {
    "memory_recent_dialogue",
    "memory_conversation_summary",
    "memory_skill_facts",
}
_START_MEMORY_LEGACY_FIELDS = {
    "memory_l0",
    "memory_l1",
    "memory_l2",
}
_START_MEMORY_RESERVED_FIELDS = _START_MEMORY_STRUCTURED_FIELDS | _START_MEMORY_LEGACY_FIELDS
_OUTPUT_FIELD_NAME_RE = re.compile(r"[a-zA-Z0-9_]+")
_OUTPUT_FIELD_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
_CODE_EXECUTOR_LANGUAGES = {"python", "javascript"}
_CODE_EXECUTOR_ENTRYPOINT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_CODE_EXECUTOR_INPUT_KEY_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_HTTP_REQUEST_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_HTTP_REQUEST_BODY_TYPES = {"none", "json", "raw", "x-www-form-urlencoded", "form-data"}
_HTTP_REQUEST_AUTH_TYPES = {"none", "bearer", "api_key"}
_HTTP_REQUEST_API_KEY_IN = {"header", "query"}
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
    "agent",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "iteration",
    "loop",
    "code_executor",
    "http_request",
    "variable_assign",
    "human_in_loop",
    "workflow_call",
    "output",
}
_CONTAINER_BODY_ALLOWED_NODE_TYPES = {
    "start",
    "llm",
    "agent",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "code_executor",
    "http_request",
    "variable_assign",
    "human_in_loop",
    "workflow_call",
}
_REMOVED_NODE_TYPE_MESSAGES = {
    "answer": "Node type 'answer' is no longer supported. Use the output node instead.",
    "template": "Node type 'template' has been removed. Please refactor with supported nodes.",
    "variable_aggregator": "Node type 'variable_aggregator' has been removed. Please refactor with supported nodes.",
}
