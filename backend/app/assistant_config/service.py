from __future__ import annotations

from app.assistant.skills.base import DEFAULT_SKILL_NAME
from app.assistant.skills.converters import db_skill_to_definition_light

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, encrypt_api_key
from app.assistant_config.models import AssistantSkill, AssistantSkillStep, AssistantTool
from app.assistant_config.registry import SkillRegistry, ToolRegistry
from app.assistant_config.schemas import (
    AssistantSkillCreateRequest,
    AssistantSkillUpdateRequest,
    AssistantToolCreateRequest,
    AssistantToolUpdateRequest,
)
from app.common.exceptions import ApiException


class AssistantConfigService:
    def __init__(self, db: Session):
        self.db = db

    # -------------------------
    # System seed / sync
    # -------------------------
    def sync_system_tools(self) -> None:
        """同步系统工具（仅清理 DB overlay，不写入系统工具定义）。

        设计说明：
        - 系统工具的“定义信息”（描述、参数、Schema）以代码为准，不落库。
        - 数据库仅保存系统工具的 enabled 覆盖（通常只有禁用项）。
        - 该方法负责清理历史遗留的 system/local 工具记录与已删除工具引用，避免 UI/技能配置出现陈旧工具。
        """
        system = ToolRegistry.list_system_tools()
        if not system:
            return

        system_names = {t.name for t in system if getattr(t, "name", None)}
        internal_names = set(ToolRegistry.INTERNAL_TOOL_NAMES or [])

        # 清理内部工具：不应出现在 DB 可配置工具列表/覆盖表里
        if internal_names:
            (
                self.db.query(AssistantTool)
                .filter(
                    (AssistantTool.is_system.is_(True)) | (AssistantTool.kind == "local"),
                    AssistantTool.name.in_(tuple(internal_names)),
                )
                .delete(synchronize_session=False)
            )

        # 清理：系统工具从代码定义获取，DB 中已移除的系统工具需要删除，避免在 UI/配置中继续出现
        stale_names: list[str] = []
        if system_names:
            stale_names_query = (
                self.db.query(AssistantTool.name)
                .filter(
                    (AssistantTool.is_system.is_(True))
                    | (AssistantTool.kind == "local")  # 历史遗留：kind=local 但未标记 is_system
                )
                .filter(~AssistantTool.name.in_(tuple(system_names)))
            )
            if internal_names:
                stale_names_query = stale_names_query.filter(~AssistantTool.name.in_(tuple(internal_names)))

            stale_names = [str(n) for (n,) in stale_names_query.all() if n]
            if stale_names:
                (
                    self.db.query(AssistantTool)
                    .filter(
                        (AssistantTool.is_system.is_(True)) | (AssistantTool.kind == "local"),
                        AssistantTool.name.in_(tuple(stale_names)),
                    )
                    .delete(synchronize_session=False)
                )

            # 清理历史遗留：enabled=True 的系统工具落库记录不再需要（默认启用），仅保留 enabled=False 覆盖
            (
                self.db.query(AssistantTool)
                .filter(
                    (AssistantTool.is_system.is_(True)) | (AssistantTool.kind == "local"),
                    AssistantTool.name.in_(tuple(system_names)),
                    AssistantTool.enabled.is_(True),
                )
                .delete(synchronize_session=False)
            )

        # 同时清理 skills.tools 里引用的已删除工具名，避免“技能配置里仍存在已删除工具”
        removed_names = internal_names.union(stale_names)
        if removed_names:
            skills = self.db.query(AssistantSkill).all()
            for skill in skills:
                tools = getattr(skill, "tools", None)
                if not isinstance(tools, list):
                    continue
                cleaned = [t for t in tools if not (isinstance(t, str) and t in removed_names)]
                if cleaned != tools:
                    skill.tools = cleaned

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40910, message="Sync system tools failed") from exc

    def list_system_tool_definitions(
        self,
        *,
        include_disabled: bool = True,
        include_schema: bool = True,
    ) -> list[dict]:
        """返回系统工具完整定义：从代码提取，DB 仅用于 overlay enabled 状态。"""
        from app.assistant_config.schemas import InputParamSchema

        definitions = ToolRegistry.list_system_tool_definitions()
        names = [d.name for d in definitions]

        enabled_by_name: dict[str, bool] = {n: True for n in names}
        if names:
            rows = (
                self.db.query(AssistantTool.name, AssistantTool.enabled)
                .filter(
                    AssistantTool.name.in_(tuple(names)),
                    AssistantTool.kind == "local",  # 仅使用 DB 里的 enabled 覆盖，不落库定义信息
                )
                .all()
            )
            for name, enabled in rows:
                enabled_by_name[str(name)] = bool(enabled)

        result: list[dict] = []
        for d in definitions:
            enabled = enabled_by_name.get(d.name, True)
            if not include_disabled and not enabled:
                continue

            result.append({
                "name": d.name,
                "description": d.description or None,
                "kind": "local",
                "is_system": True,
                "enabled": enabled,
                "input_params": [
                    InputParamSchema(
                        name=p.name,
                        description=p.description,
                        param_type=p.param_type,
                        required=p.required,
                    )
                    for p in (d.input_params or [])
                ],
                "returns": d.returns,
                "json_schema": d.json_schema if include_schema else None,
            })
        return result

    def sync_system_skills(self) -> None:
        """同步系统技能到数据库"""
        system_skills = SkillRegistry.list_system_skills()
        if not system_skills:
            return

        for s in system_skills:
            existing = (
                self.db.query(AssistantSkill)
                .filter(AssistantSkill.name == s.name)
                .first()
            )
            if not existing:
                # 构建 kb_config
                kb_config_data = None
                if getattr(s, "kb", None) is not None:
                    kb_config_data = {"enabled": bool(s.kb.enabled)}

                skill = AssistantSkill(
                    name=s.name,
                    description=s.description,
                    intent_examples=s.intent_examples,
                    tools=s.tools,
                    mode=s.mode,
                    system_prompt=s.system_prompt,
                    kb_config=kb_config_data,
                    is_system=True,
                    enabled=True,
                )
                skill.steps = [
                    AssistantSkillStep(
                        step_order=i,
                        type=step.type,
                        instruction=step.instruction,
                        tool_name=step.tool_name,
                        args_from=step.args_from,
                        args_template=getattr(step, "args_template", None),
                        output_mode=getattr(step, "output_mode", None),
                        output_fields=getattr(step, "output_fields", None),
                        include_in_summary=getattr(step, "include_in_summary", True),
                    )
                    for i, step in enumerate(s.steps)
                ]
                self.db.add(skill)
            else:
                # 不覆盖已存在记录的配置，只确保 is_system 标记正确
                existing.is_system = True

                # 强制启用默认 Skill
                if existing.name == DEFAULT_SKILL_NAME and not existing.enabled:
                    existing.enabled = True

                # 温和回填：仅当 existing.mode=="steps" 且系统定义是 agent 且 existing.system_prompt 为空时修复
                if (
                    existing.mode == "steps"
                    and s.mode == "agent"
                    and not existing.system_prompt
                ):
                    existing.mode = s.mode
                    existing.system_prompt = s.system_prompt

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40911, message="Sync system skills failed") from exc

    # -------------------------
    # Tools CRUD
    # -------------------------
    def list_tools(self, sync_system: bool = True, include_disabled: bool = False) -> list[AssistantTool]:
        if sync_system:
            self.sync_system_tools()
        # 仅返回自定义（remote）工具；系统工具定义不落库
        q = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.kind == "remote")
            .order_by(AssistantTool.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantTool.enabled.is_(True))
        return q.all()

    def set_system_tool_enabled(self, name: str, enabled: bool) -> None:
        """设置系统工具 enabled 覆盖（默认启用；禁用才落库）。"""
        system = ToolRegistry.list_system_tools()
        system_names = {t.name for t in system if getattr(t, "name", None)}
        if name not in system_names:
            raise ApiException(status_code=404, code=40413, message=f"System tool not found: {name}")

        record = self.db.query(AssistantTool).filter(AssistantTool.name == name).first()

        # 启用：删除覆盖（恢复默认 True）
        if enabled:
            if record is not None:
                # 避免误删同名 remote 工具（理论上不应发生）
                if (record.kind or "").lower() == "remote" and not record.is_system:
                    raise ApiException(status_code=409, code=40914, message=f"Tool name conflict: {name}")
                self.db.delete(record)
            self.db.commit()
            return

        # 禁用：写入/更新覆盖记录
        if record is None:
            self.db.add(
                AssistantTool(
                    name=name,
                    description=None,
                    kind="local",
                    is_system=True,
                    enabled=False,
                )
            )
        else:
            if (record.kind or "").lower() == "remote" and not record.is_system:
                raise ApiException(status_code=409, code=40914, message=f"Tool name conflict: {name}")
            record.kind = "local"
            record.is_system = True
            record.enabled = False
            # 不落库定义信息
            record.description = None
            record.input_params = None
            record.endpoint_url = None
            record.http_method = None
            record.headers = None
            record.query_params = None
            record.body_type = None
            record.body_content = None
            record.auth_type = None
            record.auth_header_name = None
            record.auth_scheme = None
            record.api_key_encrypted = None
            record.api_key_hint = None
            record.timeout_seconds = None
            record.payload_wrapper = None

        self.db.commit()

    def get_tool(self, id: UUID) -> AssistantTool:
        tool = self.db.query(AssistantTool).filter(AssistantTool.id == id).first()
        if not tool:
            raise ApiException(status_code=404, code=40410, message=f"Tool not found: {id}")
        return tool

    def create_tool(self, request: AssistantToolCreateRequest) -> AssistantTool:
        if request.kind == "local":
            raise ApiException(status_code=400, code=40010, message="kind=local is reserved for system tools")

        existing = self.db.query(AssistantTool).filter(AssistantTool.name.ilike(request.name)).first()
        if existing:
            raise ApiException(status_code=400, code=40011, message=f"Tool name already exists: {request.name}")

        encrypted = None
        hint = None
        if request.api_key:
            try:
                encrypted = encrypt_api_key(request.api_key)
                hint = api_key_hint(request.api_key)
            except Exception as exc:
                raise ApiException(status_code=500, code=50010, message="Encryption key not configured") from exc

        tool = AssistantTool(
            name=request.name,
            description=request.description,
            kind="remote",
            is_system=False,
            enabled=request.enabled,
            input_params=[p.model_dump() for p in request.input_params] if request.input_params else None,
            endpoint_url=request.endpoint_url,
            http_method=request.http_method,
            headers=request.headers,
            query_params=request.query_params,
            body_type=request.body_type,
            body_content=request.body_content,
            auth_type=request.auth_type,
            auth_header_name=request.auth_header_name,
            auth_scheme=request.auth_scheme,
            api_key_encrypted=encrypted,
            api_key_hint=hint,
            timeout_seconds=request.timeout_seconds,
            payload_wrapper=request.payload_wrapper,
        )
        self.db.add(tool)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40912, message="Create tool failed") from exc
        self.db.refresh(tool)
        return tool

    def update_tool(self, id: UUID, request: AssistantToolUpdateRequest) -> AssistantTool:
        tool = self.get_tool(id)

        if tool.is_system:
            # 系统工具只允许修改 enabled
            if request.enabled is not None:
                tool.enabled = request.enabled
            else:
                raise ApiException(status_code=400, code=40012, message="System tool can only update enabled")
        else:
            if request.name is not None:
                tool.name = request.name
            if request.description is not None:
                tool.description = request.description
            if request.enabled is not None:
                tool.enabled = request.enabled
            if request.input_params is not None:
                tool.input_params = [p.model_dump() for p in request.input_params]
            if request.endpoint_url is not None:
                tool.endpoint_url = request.endpoint_url
            if request.http_method is not None:
                tool.http_method = request.http_method
            if request.headers is not None:
                tool.headers = request.headers
            if request.query_params is not None:
                tool.query_params = request.query_params
            if request.body_type is not None:
                tool.body_type = request.body_type
            if request.body_content is not None:
                tool.body_content = request.body_content
            if request.auth_type is not None:
                tool.auth_type = request.auth_type
            if request.auth_header_name is not None:
                tool.auth_header_name = request.auth_header_name
            if request.auth_scheme is not None:
                tool.auth_scheme = request.auth_scheme
            if request.timeout_seconds is not None:
                tool.timeout_seconds = request.timeout_seconds
            if request.payload_wrapper is not None:
                tool.payload_wrapper = request.payload_wrapper
            if request.api_key is not None:
                tool.api_key_encrypted = encrypt_api_key(request.api_key)
                tool.api_key_hint = api_key_hint(request.api_key)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40913, message="Update tool failed") from exc
        self.db.refresh(tool)
        return tool

    def delete_tool(self, id: UUID) -> None:
        tool = self.get_tool(id)
        if tool.is_system:
            raise ApiException(status_code=400, code=40013, message="System tool cannot be deleted")
        self.db.delete(tool)
        self.db.commit()

    # -------------------------
    # Skills CRUD
    # -------------------------
    def list_skills(self, sync_system: bool = True, include_disabled: bool = False) -> list[AssistantSkill]:
        if sync_system:
            self.sync_system_skills()
        q = self.db.query(AssistantSkill).order_by(AssistantSkill.created_at.desc())
        if not include_disabled:
            q = q.filter(AssistantSkill.enabled.is_(True))
        return q.all()

    def get_skill(self, id: UUID) -> AssistantSkill:
        skill = self.db.query(AssistantSkill).filter(AssistantSkill.id == id).first()
        if not skill:
            raise ApiException(status_code=404, code=40411, message=f"Skill not found: {id}")
        return skill

    def create_skill(self, request: AssistantSkillCreateRequest) -> AssistantSkill:
        existing = self.db.query(AssistantSkill).filter(
            AssistantSkill.name.ilike(request.name)
        ).first()
        if existing:
            raise ApiException(status_code=400, code=40020, message=f"Skill name exists: {request.name}")

        skill = AssistantSkill(
            name=request.name,
            description=request.description,
            intent_examples=request.intent_examples,
            tools=request.tools,
            mode=request.mode,
            system_prompt=request.system_prompt,
            kb_config=request.kb_config,
            is_system=False,
            enabled=request.enabled,
        )
        skill.steps = [
            AssistantSkillStep(
                step_order=i,
                type=step.type,
                instruction=step.instruction,
                tool_name=step.tool_name,
                args_from=step.args_from,
                args_template=step.args_template,
                output_mode=getattr(step, "output_mode", None),
                output_fields=getattr(step, "output_fields", None),
                include_in_summary=step.include_in_summary if step.include_in_summary is not None else True,
                kb_config=getattr(step, "kb_config", None),
            )
            for i, step in enumerate(request.steps)
        ]
        self.db.add(skill)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40920, message="Create skill failed") from exc
        self.db.refresh(skill)
        return skill

    def update_skill(self, id: UUID, request: AssistantSkillUpdateRequest) -> AssistantSkill:
        skill = self.get_skill(id)

        if skill.is_system:
            # 系统技能允许编辑内容，但禁止改名
            if request.name is not None and request.name != skill.name:
                raise ApiException(status_code=400, code=40021, message="System skill cannot be renamed")
            if request.description is not None:
                skill.description = request.description
            if request.intent_examples is not None:
                skill.intent_examples = request.intent_examples
            if request.tools is not None:
                skill.tools = request.tools
            if request.mode is not None:
                skill.mode = request.mode
            if request.system_prompt is not None:
                skill.system_prompt = request.system_prompt
            if request.kb_config is not None:
                skill.kb_config = request.kb_config
            if request.steps is not None:
                # 先删除旧 steps，避免唯一约束冲突
                for old_step in skill.steps:
                    self.db.delete(old_step)
                self.db.flush()
                skill.steps = [
                    AssistantSkillStep(
                        step_order=i,
                        type=step.type,
                        instruction=step.instruction,
                        tool_name=step.tool_name,
                        args_from=step.args_from,
                        args_template=step.args_template,
                        output_mode=getattr(step, "output_mode", None),
                        output_fields=getattr(step, "output_fields", None),
                        include_in_summary=step.include_in_summary if step.include_in_summary is not None else True,
                        kb_config=getattr(step, "kb_config", None),
                    )
                    for i, step in enumerate(request.steps)
                ]
            if request.enabled is not None:
                # 阻止禁用默认 Skill
                if skill.name == DEFAULT_SKILL_NAME and request.enabled is False:
                    raise ApiException(status_code=400, code=40025, message="General chat skill cannot be disabled")
                skill.enabled = request.enabled
        else:
            if request.name is not None:
                skill.name = request.name
            if request.description is not None:
                skill.description = request.description
            if request.intent_examples is not None:
                skill.intent_examples = request.intent_examples
            if request.tools is not None:
                skill.tools = request.tools
            if request.mode is not None:
                skill.mode = request.mode
            if request.system_prompt is not None:
                skill.system_prompt = request.system_prompt
            if request.kb_config is not None:
                skill.kb_config = request.kb_config
            if request.enabled is not None:
                skill.enabled = request.enabled
            if request.steps is not None:
                # 先删除旧 steps，避免唯一约束冲突
                for old_step in skill.steps:
                    self.db.delete(old_step)
                self.db.flush()
                skill.steps = [
                    AssistantSkillStep(
                        step_order=i,
                        type=step.type,
                        instruction=step.instruction,
                        tool_name=step.tool_name,
                        args_from=step.args_from,
                        args_template=step.args_template,
                        output_mode=getattr(step, "output_mode", None),
                        output_fields=getattr(step, "output_fields", None),
                        include_in_summary=step.include_in_summary if step.include_in_summary is not None else True,
                        kb_config=getattr(step, "kb_config", None),
                    )
                    for i, step in enumerate(request.steps)
                ]

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40921, message="Update skill failed") from exc
        self.db.refresh(skill)
        return skill

    def reset_skill(self, id: UUID, confirm: bool) -> AssistantSkill:
        """复位系统技能到默认配置"""
        if not confirm:
            raise ApiException(status_code=400, code=40023, message="confirm=true required")

        skill = self.get_skill(id)
        if not skill.is_system:
            raise ApiException(status_code=400, code=40024, message="Only system skill can be reset")

        from app.assistant.skills.definitions import get_skill_by_name

        default = get_skill_by_name(skill.name)
        if not default:
            raise ApiException(status_code=404, code=40412, message=f"Default not found: {skill.name}")

        # 保留 enabled 状态，只复位内容
        enabled = skill.enabled
        skill.description = default.description
        skill.intent_examples = default.intent_examples
        skill.tools = default.tools
        skill.mode = default.mode
        skill.system_prompt = default.system_prompt
        # 重置 kb_config
        if getattr(default, "kb", None) is not None:
            skill.kb_config = {"enabled": bool(default.kb.enabled)}
        else:
            skill.kb_config = {"enabled": False}
        # 先删除旧 steps，避免唯一约束冲突
        for old_step in skill.steps:
            self.db.delete(old_step)
        self.db.flush()
        skill.steps = [
            AssistantSkillStep(
                step_order=i,
                type=step.type,
                instruction=step.instruction,
                tool_name=step.tool_name,
                args_from=step.args_from,
                args_template=getattr(step, "args_template", None),
                output_mode=getattr(step, "output_mode", None),
                output_fields=getattr(step, "output_fields", None),
                include_in_summary=getattr(step, "include_in_summary", True),
            )
            for i, step in enumerate(default.steps)
        ]
        skill.enabled = enabled

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40922, message="Reset skill failed") from exc
        self.db.refresh(skill)
        return skill

    def delete_skill(self, id: UUID) -> None:
        skill = self.get_skill(id)
        if skill.is_system:
            raise ApiException(status_code=400, code=40022, message="System skill cannot be deleted")
        self.db.delete(skill)
        self.db.commit()

    def reset_all_system_skills(self, confirm: bool) -> dict:
        """重置所有系统技能到默认配置，并清理已下线的系统技能"""
        if not confirm:
            raise ApiException(status_code=400, code=40023, message="confirm=true required")

        from app.assistant.skills.definitions import SKILLS, get_skill_by_name

        # 获取代码侧系统技能名称集合
        default_names = {s.name for s in SKILLS}

        # 获取 DB 中所有系统技能
        db_system_skills = (
            self.db.query(AssistantSkill)
            .filter(AssistantSkill.is_system.is_(True))
            .all()
        )

        reset_count = 0
        deleted_count = 0
        created_count = 0
        affected: list[dict] = []

        # 处理 DB 中已存在的系统技能
        for skill in db_system_skills:
            if skill.name in default_names:
                # 重置到默认配置
                default = get_skill_by_name(skill.name)
                if default:
                    self._reset_skill_to_default(skill, default)
                    reset_count += 1
                    affected.append({"name": skill.name, "id": str(skill.id), "action": "reset"})
            else:
                # 已下线的系统技能，删除
                for old_step in skill.steps:
                    self.db.delete(old_step)
                self.db.delete(skill)
                deleted_count += 1
                affected.append({"name": skill.name, "id": str(skill.id), "action": "deleted"})

        # 创建缺失的系统技能
        existing_names = {s.name for s in db_system_skills}
        for s in SKILLS:
            if s.name not in existing_names:
                kb_config_data = None
                if getattr(s, "kb", None) is not None:
                    kb_config_data = {"enabled": bool(s.kb.enabled)}

                skill = AssistantSkill(
                    name=s.name,
                    description=s.description,
                    intent_examples=s.intent_examples,
                    tools=s.tools,
                    mode=s.mode,
                    system_prompt=s.system_prompt,
                    kb_config=kb_config_data,
                    is_system=True,
                    enabled=True,
                )
                skill.steps = [
                    AssistantSkillStep(
                        step_order=i,
                        type=step.type,
                        instruction=step.instruction,
                        tool_name=step.tool_name,
                        args_from=step.args_from,
                        args_template=getattr(step, "args_template", None),
                        output_mode=getattr(step, "output_mode", None),
                        output_fields=getattr(step, "output_fields", None),
                        include_in_summary=getattr(step, "include_in_summary", True),
                    )
                    for i, step in enumerate(s.steps)
                ]
                self.db.add(skill)
                created_count += 1
                affected.append({"name": s.name, "id": None, "action": "created"})

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40923, message="Reset all skills failed") from exc

        return {
            "resetCount": reset_count,
            "deletedCount": deleted_count,
            "createdCount": created_count,
            "affected": affected,
        }

    def _reset_skill_to_default(self, skill: AssistantSkill, default) -> None:
        """内部方法：将技能重置到默认配置"""
        # 保留 enabled 状态（general_chat 强制启用）
        enabled = skill.enabled
        if skill.name == DEFAULT_SKILL_NAME:
            enabled = True

        skill.description = default.description
        skill.intent_examples = default.intent_examples
        skill.tools = default.tools
        skill.mode = default.mode
        skill.system_prompt = default.system_prompt

        # 重置 kb_config
        if getattr(default, "kb", None) is not None:
            skill.kb_config = {"enabled": bool(default.kb.enabled)}
        else:
            skill.kb_config = {"enabled": False}

        # 删除旧 steps
        for old_step in skill.steps:
            self.db.delete(old_step)
        self.db.flush()

        # 创建新 steps
        skill.steps = [
            AssistantSkillStep(
                step_order=i,
                type=step.type,
                instruction=step.instruction,
                tool_name=step.tool_name,
                args_from=step.args_from,
                args_template=getattr(step, "args_template", None),
                output_mode=getattr(step, "output_mode", None),
                output_fields=getattr(step, "output_fields", None),
                include_in_summary=getattr(step, "include_in_summary", True),
            )
            for i, step in enumerate(default.steps)
        ]
        skill.enabled = enabled
