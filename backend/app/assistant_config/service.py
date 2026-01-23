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
        """同步系统工具到数据库"""
        system = ToolRegistry.list_system_tools()
        if not system:
            return

        for item in system:
            existing = (
                self.db.query(AssistantTool)
                .filter(AssistantTool.name == item.name)
                .first()
            )
            if not existing:
                self.db.add(
                    AssistantTool(
                        name=item.name,
                        description=item.description or None,
                        kind="local",
                        is_system=True,
                        enabled=True,
                    )
                )
            else:
                existing.description = item.description or None
                existing.kind = "local"
                existing.is_system = True

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40910, message="Sync system tools failed") from exc

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
                skill = AssistantSkill(
                    name=s.name,
                    description=s.description,
                    intent_examples=s.intent_examples,
                    tools=s.tools,
                    mode=s.mode,
                    system_prompt=s.system_prompt,
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
        q = self.db.query(AssistantTool).order_by(AssistantTool.created_at.desc())
        if not include_disabled:
            q = q.filter(AssistantTool.enabled.is_(True))
        return q.all()

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
