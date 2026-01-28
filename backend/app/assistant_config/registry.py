from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, get_args, get_origin

from sqlalchemy.orm import Session, joinedload

from app.assistant_config.models import AssistantSkill, AssistantTool
from app.assistant_config.remote_tool import RemoteTool


@dataclass(frozen=True)
class SystemToolDefinition:
    name: str
    description: str


@dataclass(frozen=True)
class SystemToolParamDefinition:
    name: str
    description: str | None
    param_type: str
    required: bool


@dataclass(frozen=True)
class SystemToolFullDefinition:
    name: str
    description: str
    input_params: list[SystemToolParamDefinition]
    returns: str | None
    json_schema: dict | None


class ToolRegistry:
    """工具注册表 - 解析系统本地工具和数据库自定义工具"""

    # 内部系统工具：不对外展示，但仍可在运行时被内部逻辑调用
    INTERNAL_TOOL_NAMES: frozenset[str] = frozenset({"kb_search"})

    @staticmethod
    def list_system_tools() -> list[SystemToolDefinition]:
        from app.assistant import tools as assistant_tools

        results: list[SystemToolDefinition] = []
        for tool_name in getattr(assistant_tools, "__all__", []):
            if tool_name in ToolRegistry.INTERNAL_TOOL_NAMES:
                continue
            tool_obj = getattr(assistant_tools, tool_name, None)
            description = ""
            if tool_obj is not None:
                description = (
                    getattr(tool_obj, "description", None)
                    or getattr(tool_obj, "__doc__", "")
                    or ""
                ).strip()
            results.append(SystemToolDefinition(name=tool_name, description=description))
        return results

    @staticmethod
    def list_system_tool_definitions() -> list[SystemToolFullDefinition]:
        """从代码定义获取系统工具完整信息（名称/描述/参数签名/JSON Schema）。"""
        from app.assistant import tools as assistant_tools

        results: list[SystemToolFullDefinition] = []
        for tool_name in getattr(assistant_tools, "__all__", []):
            if tool_name in ToolRegistry.INTERNAL_TOOL_NAMES:
                continue
            tool_obj = getattr(assistant_tools, tool_name, None)
            if tool_obj is None:
                continue

            description = (
                getattr(tool_obj, "description", None)
                or getattr(tool_obj, "__doc__", "")
                or ""
            ).strip()

            input_params, returns, json_schema = ToolRegistry._extract_tool_params(tool_obj)
            results.append(SystemToolFullDefinition(
                name=tool_name,
                description=description,
                input_params=input_params,
                returns=returns,
                json_schema=json_schema,
            ))
        return results

    @staticmethod
    def resolve_system_tool(tool_name: str) -> Any | None:
        from app.assistant import tools as assistant_tools
        return getattr(assistant_tools, tool_name, None)

    def __init__(self, db: Session):
        self.db = db

    def resolve(self, tool_name: str) -> Any | None:
        """解析工具 - 优先从数据库查找，正确处理禁用状态

        逻辑:
        1. 内部工具直接返回系统工具（绕过 DB 禁用）
        2. 先查询数据库中是否存在该工具（不管启用状态）
        3. 如果存在且被禁用，返回 None（不回退到系统工具）
        4. 如果存在且启用，返回对应工具
        5. 如果不存在，回退到系统工具
        """
        # 内部工具：绕过 DB 禁用逻辑
        if tool_name in self.INTERNAL_TOOL_NAMES:
            return self.resolve_system_tool(tool_name)

        # 先查询是否存在该工具（不过滤 enabled）
        record = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.name == tool_name)
            .first()
        )

        if record:
            # 工具存在于数据库
            if not record.enabled:
                # 工具被禁用，返回 None（不回退到系统工具）
                return None
            # 工具启用
            if (record.kind or "").lower() == "remote":
                return RemoteTool.from_model(record)
            return self.resolve_system_tool(tool_name)

        # 工具不在数据库中，回退到系统工具
        return self.resolve_system_tool(tool_name)

    # -------------------------
    # Tool signature extraction
    # -------------------------
    @staticmethod
    def _tool_schema_json(tool: Any) -> dict | None:
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is None:
            return None
        if hasattr(args_schema, "model_json_schema"):
            try:
                return args_schema.model_json_schema()
            except Exception:
                return None
        if hasattr(args_schema, "schema"):
            try:
                return args_schema.schema()
            except Exception:
                return None
        return None

    @staticmethod
    def _parse_docstring_args(doc: str) -> dict[str, str]:
        """解析 Google-style docstring 中的 Args 段。"""
        args: dict[str, str] = {}
        lines = (doc or "").splitlines()
        in_args = False
        current: str | None = None
        for raw in lines:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not in_args:
                if stripped == "Args:":
                    in_args = True
                continue
            if stripped in {"Returns:", "Raises:", "Note:", "Notes:", "注意:"}:
                break
            if not stripped:
                continue
            if ":" in stripped:
                name, desc = stripped.split(":", 1)
                name = name.strip()
                desc = desc.strip()
                if name and " " not in name:
                    args[name] = desc
                    current = name
                    continue
            if current:
                args[current] = (args[current] + " " + stripped).strip()
        return args

    @staticmethod
    def _parse_docstring_returns(doc: str) -> str | None:
        """解析 Google-style docstring 中的 Returns 段。"""
        lines = (doc or "").splitlines()
        in_returns = False
        result_lines: list[str] = []
        for raw in lines:
            stripped = raw.strip()
            if not in_returns:
                if stripped in {"Returns:", "返回:"}:
                    in_returns = True
                continue
            if stripped in {"Raises:", "Note:", "Notes:", "注意:", "Args:"}:
                break
            if stripped:
                result_lines.append(stripped)
        return "\n".join(result_lines) if result_lines else None

    @staticmethod
    def _param_type_from_json_schema(prop: dict) -> str:
        if not isinstance(prop, dict):
            return "string"
        t = prop.get("type")
        if isinstance(t, str):
            if t == "integer":
                return "number"
            if t in {"string", "number", "boolean", "array", "object"}:
                return t
        for key in ("anyOf", "oneOf"):
            items = prop.get(key)
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    if it.get("type") == "null":
                        continue
                    return ToolRegistry._param_type_from_json_schema(it)
        return "string"

    @staticmethod
    def _param_type_from_annotation(annotation: Any) -> str:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is list or origin is tuple or origin is set:
            return "array"
        if origin is dict:
            return "object"
        if args:
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                return ToolRegistry._param_type_from_annotation(non_none[0])
        if annotation in {int, float}:
            return "number"
        if annotation is bool:
            return "boolean"
        if annotation is dict:
            return "object"
        if annotation is list:
            return "array"
        return "string"

    @staticmethod
    def _extract_tool_params(tool: Any) -> tuple[list[SystemToolParamDefinition], str | None, dict | None]:
        """从 LangChain tool 对象中提取参数列表、返回值描述与 JSON Schema。"""
        schema_json = ToolRegistry._tool_schema_json(tool)
        args_schema = getattr(tool, "args_schema", None)

        doc = ""
        tool_func = getattr(tool, "func", None)
        if callable(tool_func):
            doc = inspect.getdoc(tool_func) or ""
        if not doc:
            doc = inspect.getdoc(tool) or ""
        doc_args = ToolRegistry._parse_docstring_args(doc)
        doc_returns = ToolRegistry._parse_docstring_returns(doc)

        # 优先从 JSON Schema 解析
        if isinstance(schema_json, dict):
            props = schema_json.get("properties") or {}
            required = set(schema_json.get("required") or [])
            ordered_names: list[str] = []
            if args_schema is not None:
                mf = getattr(args_schema, "model_fields", None)
                if isinstance(mf, dict):
                    ordered_names = list(mf.keys())
                else:
                    f = getattr(args_schema, "__fields__", None)
                    if isinstance(f, dict):
                        ordered_names = list(f.keys())
            if not ordered_names:
                ordered_names = list(props.keys())

            params: list[SystemToolParamDefinition] = []
            for name in ordered_names:
                prop = props.get(name) or {}
                desc = (prop.get("description") or "").strip() or doc_args.get(name) or None
                ptype = ToolRegistry._param_type_from_json_schema(prop)
                params.append(SystemToolParamDefinition(
                    name=name,
                    description=desc,
                    param_type=ptype,
                    required=name in required,
                ))
            return params, doc_returns, schema_json

        # fallback：函数签名
        if callable(tool_func):
            sig = inspect.signature(tool_func)
            params = []
            for p in sig.parameters.values():
                if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                if p.name in {"self", "cls"}:
                    continue
                req = (p.default is inspect.Parameter.empty)
                ptype = ToolRegistry._param_type_from_annotation(p.annotation)
                desc = doc_args.get(p.name) or None
                params.append(SystemToolParamDefinition(
                    name=p.name,
                    description=desc,
                    param_type=ptype,
                    required=req,
                ))
            return params, doc_returns, None

        return [], doc_returns, None


class SkillRegistry:
    """技能注册表 - 解析系统技能和数据库自定义技能"""

    @staticmethod
    def list_system_skills() -> list[Any]:
        from app.assistant.skills.definitions import SKILLS
        return list(SKILLS)

    def __init__(self, db: Session):
        self.db = db

    def list_enabled_db_skills(self, include_steps: bool = False) -> list[AssistantSkill]:
        """获取启用的数据库 Skills

        Args:
            include_steps: 是否预加载 steps（路由阶段不需要，执行阶段需要）
        """
        query = (
            self.db.query(AssistantSkill)
            .filter(AssistantSkill.enabled.is_(True))
        )
        if include_steps:
            query = query.options(joinedload(AssistantSkill.steps))
        return query.order_by(AssistantSkill.created_at.desc()).all()
