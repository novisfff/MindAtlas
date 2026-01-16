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
AuthType = Literal["none", "bearer", "basic", "api-key"]
BodyType = Literal["none", "form-data", "x-www-form-urlencoded", "json", "xml", "raw"]
SkillMode = Literal["steps", "agent"]

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

class AssistantSkillStepInput(CamelModel):
    type: StepType
    instruction: str | None = None
    tool_name: str | None = None
    args_from: ArgsFrom | None = None
    args_template: str | None = Field(default=None, max_length=8000)

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
            allowed_vars = {"user_input", "history", "last_step_result"}
            for m in re.finditer(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", template):
                var_name = m.group(1)
                if var_name in allowed_vars or re.fullmatch(r"step_\d+_result", var_name):
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

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillCreateRequest":
        if self.mode == "steps" and len(self.steps) == 0:
            raise ValueError("steps mode requires at least one step")
        if self.mode == "agent" and not (self.system_prompt or "").strip():
            raise ValueError("agent mode requires system_prompt")
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


class ResetSkillRequest(CamelModel):
    confirm: bool = False


class AssistantSkillStepResponse(OrmModel):
    id: UUID
    step_order: int
    type: str
    instruction: str | None
    tool_name: str | None
    args_from: str | None
    args_template: str | None
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
    steps: list[AssistantSkillStepResponse]
    created_at: datetime
    updated_at: datetime
