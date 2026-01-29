from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, model_validator

from app.common.schemas import CamelModel, OrmModel


ToolKind = Literal["local", "remote"]
StepType = Literal["analysis", "tool", "summary"]
ArgsFrom = Literal["context", "previous", "custom", "json"]
AnalysisOutputMode = Literal["text", "json"]
AuthType = Literal["none", "bearer", "basic", "api-key"]
BodyType = Literal["none", "form-data", "x-www-form-urlencoded", "json", "xml", "raw"]
SkillMode = Literal["steps", "agent"]
OutputFieldType = Literal["string", "number", "integer", "boolean", "object", "array"]

# 允许的 URL scheme
ALLOWED_URL_SCHEMES = {"http", "https"}


def validate_endpoint_url(url: str | None) -> None:
    """验证 endpoint_url 的基本安全性"""
    if not url:
        return
    url = url.strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError(f"endpoint_url 只允许 http/https scheme，不允许: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError("endpoint_url 缺少主机名")
    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "localhost.localdomain"):
        raise ValueError("endpoint_url 不允许访问 localhost")


# ==================== Tool Schemas ====================

class InputParamSchema(CamelModel):
    """输入参数定义"""
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    param_type: str = Field(default="string", max_length=32)  # string|number|boolean|array|object
    required: bool = False


class SystemToolDefinitionResponse(CamelModel):
    """系统工具完整定义（代码即真相）。"""
    name: str
    description: str | None = None
    kind: ToolKind = "local"
    is_system: bool = True
    enabled: bool = True
    input_params: list[InputParamSchema] | None = None
    returns: str | None = None
    json_schema: dict | None = None


class SystemToolEnabledUpdateRequest(CamelModel):
    """更新系统工具启用状态（默认启用；禁用才会落库为覆盖配置）。"""
    enabled: bool = True


class AssistantToolCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    kind: ToolKind = "remote"
    enabled: bool = True

    # 输入参数
    input_params: list[InputParamSchema] | None = None

    endpoint_url: str | None = Field(default=None, max_length=2048)
    http_method: str | None = Field(default="POST", max_length=10)
    headers: dict[str, str] | None = None

    # Query params
    query_params: dict[str, str] | None = None

    # Body config
    body_type: BodyType | None = "none"
    body_content: str | None = None

    # Auth config
    auth_type: AuthType | None = "none"
    auth_header_name: str | None = Field(default="Authorization", max_length=128)
    auth_scheme: str | None = Field(default="Bearer", max_length=32)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)

    timeout_seconds: int | None = Field(default=15, ge=1, le=120)
    payload_wrapper: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate(self) -> "AssistantToolCreateRequest":
        if self.kind == "remote":
            if not (self.endpoint_url or "").strip():
                raise ValueError("endpoint_url is required when kind=remote")
            # 验证 URL 安全性
            validate_endpoint_url(self.endpoint_url)
        return self


class AssistantToolUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    enabled: bool | None = None

    # 输入参数
    input_params: list[InputParamSchema] | None = None

    endpoint_url: str | None = Field(default=None, max_length=2048)
    http_method: str | None = Field(default=None, max_length=10)
    headers: dict[str, str] | None = None

    # Query params
    query_params: dict[str, str] | None = None

    # Body config
    body_type: BodyType | None = None
    body_content: str | None = None

    # Auth config
    auth_type: AuthType | None = None
    auth_header_name: str | None = Field(default=None, max_length=128)
    auth_scheme: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)

    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    payload_wrapper: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate(self) -> "AssistantToolUpdateRequest":
        # 验证 URL 安全性（如果提供了 endpoint_url）
        if self.endpoint_url:
            validate_endpoint_url(self.endpoint_url)
        return self


class AssistantToolResponse(OrmModel):
    id: UUID
    name: str
    description: str | None
    kind: str
    is_system: bool
    enabled: bool

    # 输入参数
    input_params: list[dict] | None

    endpoint_url: str | None
    http_method: str | None
    headers: dict | None

    # Query params
    query_params: dict | None

    # Body config
    body_type: str | None
    body_content: str | None

    # Auth config
    auth_type: str | None
    auth_header_name: str | None
    auth_scheme: str | None
    api_key_hint: str | None

    timeout_seconds: int | None
    payload_wrapper: str | None

    created_at: datetime
    updated_at: datetime


# ==================== Skill Schemas ====================

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class OutputFieldSpecInput(CamelModel):
    """输出字段规格定义（API 输入）"""
    name: str = Field(..., min_length=1, max_length=64)
    type: OutputFieldType = "string"
    nullable: bool = False
    items_type: OutputFieldType | None = None
    enum: list[str] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "OutputFieldSpecInput":
        if not re.fullmatch(r"[a-zA-Z0-9_]+", self.name):
            raise ValueError(f"Invalid output field name: {self.name}")
        if self.type == "array" and not self.items_type:
            raise ValueError("items_type is required when type=array")
        if self.items_type == "array":
            raise ValueError("items_type cannot be array")
        return self


def _extract_field_names(output_fields: list[OutputFieldSpecInput] | list[str] | None) -> set[str]:
    """从 output_fields 提取字段名集合（兼容新旧格式）"""
    if not output_fields:
        return set()
    names: set[str] = set()
    for f in output_fields:
        if isinstance(f, OutputFieldSpecInput):
            names.add(f.name)
        elif isinstance(f, str) and f.strip():
            names.add(f.strip())
        elif isinstance(f, dict):
            name = f.get("name", "")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return names


def _validate_step_template_refs(steps: list["AssistantSkillStepInput"]) -> None:
    """校验模板变量引用：analysis jsonmode 仅允许引用其 output_fields 白名单。"""
    allowed_fields_by_step: dict[int, set[str]] = {}
    for idx, step in enumerate(steps):
        step_no = idx + 1
        if step.type == "analysis" and step.output_mode == "json":
            # 使用新的提取函数，兼容新旧格式
            allowed_fields_by_step[step_no] = _extract_field_names(step.output_fields)

    base_vars = {"user_input", "history", "last_step_result", "last_step_result_raw"}

    for idx, step in enumerate(steps):
        current_step_no = idx + 1
        template = (step.args_template or "").strip()
        if step.type != "tool":
            template = ""
        if step.type == "tool" and step.args_from not in ("custom", "json"):
            continue
        if not template:
            continue

        for m in _TEMPLATE_VAR_RE.finditer(template):
            var_name = m.group(1)
            if var_name in base_vars:
                continue
            if re.fullmatch(r"step_\d+_result", var_name):
                continue
            if re.fullmatch(r"step_\d+_result_raw", var_name):
                continue

            m2 = re.fullmatch(r"step_(\d+)_([a-zA-Z0-9_]+)", var_name)
            if not m2:
                continue

            ref_step_no = int(m2.group(1))
            field = m2.group(2)

            if ref_step_no >= current_step_no:
                raise ValueError(f"args_template references future step: {var_name}")

            allowed = allowed_fields_by_step.get(ref_step_no)
            if not allowed or field not in allowed:
                raise ValueError(
                    f"args_template references disallowed field: {var_name}; "
                    f"step_{ref_step_no} must be analysis jsonmode and include '{field}' in output_fields"
                )

    # analysis instruction 的变量引用校验（禁止 user_input/history 等用户输入类变量）
    disallowed_in_instruction = {"user_input", "history"}
    for idx, step in enumerate(steps):
        current_step_no = idx + 1
        if step.type != "analysis":
            continue
        instruction = (step.instruction or "").strip()
        if not instruction:
            continue
        for m in _TEMPLATE_VAR_RE.finditer(instruction):
            var_name = m.group(1)
            if var_name in disallowed_in_instruction:
                raise ValueError(f"analysis instruction cannot reference: {var_name}")
            if var_name in ("last_step_result", "last_step_result_raw"):
                continue
            if re.fullmatch(r"step_\d+_result", var_name):
                continue
            if re.fullmatch(r"step_\d+_result_raw", var_name):
                continue
            m2 = re.fullmatch(r"step_(\d+)_([a-zA-Z0-9_]+)", var_name)
            if not m2:
                continue
            ref_step_no = int(m2.group(1))
            field = m2.group(2)
            if ref_step_no >= current_step_no:
                raise ValueError(f"analysis instruction references future step: {var_name}")
            allowed = allowed_fields_by_step.get(ref_step_no)
            if not allowed or field not in allowed:
                raise ValueError(
                    f"analysis instruction references disallowed field: {var_name}; "
                    f"step_{ref_step_no} must be analysis jsonmode and include '{field}' in output_fields"
                )


class AssistantSkillStepInput(CamelModel):
    type: StepType
    instruction: str | None = None
    tool_name: str | None = None
    args_from: ArgsFrom | None = None
    args_template: str | None = Field(default=None, max_length=8000)
    output_mode: AnalysisOutputMode | None = None
    output_fields: list[OutputFieldSpecInput] | list[str] | None = None  # 支持新旧格式
    include_in_summary: bool | None = True
    kb_config: dict | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillStepInput":
        if self.type == "tool" and not (self.tool_name or "").strip():
            raise ValueError("tool_name is required when type=tool")
        if self.type == "tool" and self.args_from == "custom":
            if not (self.args_template or "").strip():
                raise ValueError("args_template is required when args_from=custom")
        if self.type == "tool" and self.args_from == "json":
            template = (self.args_template or "").strip()
            if not template:
                raise ValueError("args_template is required when args_from=json")
            # 校验变量名
            allowed_vars = {"user_input", "history", "last_step_result", "last_step_result_raw"}
            for m in re.finditer(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", template):
                var_name = m.group(1)
                if var_name in allowed_vars:
                    continue
                if re.fullmatch(r"step_\d+_result", var_name):
                    continue
                if re.fullmatch(r"step_\d+_result_raw", var_name):
                    continue
                # step_N_<field> 由 Skill 级校验（结合 analysis jsonmode 输出字段白名单）进一步限制
                if re.fullmatch(r"step_\d+_[a-zA-Z0-9_]+", var_name):
                    continue
                raise ValueError(f"Unknown variable in args_template: {var_name}")
            # 校验 JSON 格式（用占位符替换变量后解析）
            probe = re.sub(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}", '"__placeholder__"', template)
            try:
                obj = json.loads(probe)
            except Exception:
                raise ValueError("args_template must be valid JSON when args_from=json")
            if not isinstance(obj, dict):
                raise ValueError("args_template must be a JSON object when args_from=json")

        # analysis 输出 JSON 模式校验
        if self.type == "analysis" and self.output_mode == "json":
            fields = self.output_fields or []
            if not isinstance(fields, list) or len(fields) == 0:
                raise ValueError("output_fields is required when output_mode=json for analysis step")

            # 兼容新旧格式：提取并校验字段名
            field_names = _extract_field_names(fields)
            if not field_names:
                raise ValueError("output_fields is required when output_mode=json for analysis step")

            # 校验字段名格式
            for name in field_names:
                if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
                    raise ValueError(f"Invalid output field name: {name}")
        return self


class AssistantSkillCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=512)
    intent_examples: list[str] = []
    tools: list[str] = []
    mode: SkillMode = "steps"
    system_prompt: str | None = Field(default=None, max_length=4096)
    steps: list[AssistantSkillStepInput] = []
    enabled: bool = True
    kb_config: dict | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillCreateRequest":
        if self.mode == "steps" and len(self.steps) == 0:
            raise ValueError("steps mode requires at least one step")
        if self.mode == "agent" and not (self.system_prompt or "").strip():
            raise ValueError("agent mode requires system_prompt")
        if self.mode == "steps":
            _validate_step_template_refs(self.steps or [])
        return self


class AssistantSkillUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=512)
    intent_examples: list[str] | None = None
    tools: list[str] | None = None
    mode: SkillMode | None = None
    system_prompt: str | None = Field(default=None, max_length=4096)
    steps: list[AssistantSkillStepInput] | None = None
    enabled: bool | None = None
    kb_config: dict | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillUpdateRequest":
        if self.steps is not None:
            _validate_step_template_refs(self.steps or [])
        return self


class ResetSkillRequest(CamelModel):
    confirm: bool = False


class ResetAllSkillsResponse(CamelModel):
    """重置所有系统技能的响应"""
    reset_count: int
    deleted_count: int
    created_count: int
    affected: list[dict]


class AssistantSkillStepResponse(OrmModel):
    id: UUID
    step_order: int
    type: str
    instruction: str | None
    tool_name: str | None
    args_from: str | None
    args_template: str | None
    output_mode: str | None
    output_fields: list[dict] | list[str] | None  # 支持新旧格式
    include_in_summary: bool | None
    kb_config: dict | None
    created_at: datetime
    updated_at: datetime


class AssistantSkillResponse(OrmModel):
    id: UUID
    name: str
    description: str
    intent_examples: list[str] | None
    tools: list[str] | None
    mode: str
    system_prompt: str | None
    is_system: bool
    enabled: bool
    kb_config: dict | None
    steps: list[AssistantSkillStepResponse]
    created_at: datetime
    updated_at: datetime
