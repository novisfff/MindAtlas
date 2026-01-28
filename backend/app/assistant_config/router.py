from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.assistant_config.schemas import (
    AssistantSkillCreateRequest,
    AssistantSkillResponse,
    AssistantSkillUpdateRequest,
    AssistantToolCreateRequest,
    AssistantToolResponse,
    AssistantToolUpdateRequest,
    ResetSkillRequest,
    SystemToolDefinitionResponse,
    SystemToolEnabledUpdateRequest,
)
from app.assistant_config.service import AssistantConfigService
from app.common.responses import ApiResponse
from app.database import get_db

router = APIRouter(prefix="/api/assistant-config", tags=["assistant-config"])


# ==================== Tools ====================

@router.get("/system-tools/definitions", response_model=ApiResponse)
def list_system_tool_definitions(
    include_disabled: bool = Query(True, description="是否包含已禁用的系统工具"),
    include_schema: bool = Query(True, description="是否包含 JSON Schema"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """获取系统工具完整定义（从代码获取，非数据库）。"""
    service = AssistantConfigService(db)
    items = service.list_system_tool_definitions(
        include_disabled=include_disabled,
        include_schema=include_schema,
    )
    return ApiResponse.ok([
        SystemToolDefinitionResponse.model_validate(i).model_dump(by_alias=True)
        for i in items
    ])


@router.put("/system-tools/{name}/enabled", response_model=ApiResponse)
def update_system_tool_enabled(
    name: str,
    request: SystemToolEnabledUpdateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """更新系统工具启用状态（仅保存 enabled 覆盖；工具定义信息以代码为准）。"""
    service = AssistantConfigService(db)
    service.set_system_tool_enabled(name, enabled=request.enabled)
    return ApiResponse.ok({"name": name, "enabled": request.enabled})


@router.get("/tools", response_model=ApiResponse)
def list_tools(
    sync_system: bool = Query(True),
    include_disabled: bool = Query(True),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    tools = service.list_tools(sync_system=sync_system, include_disabled=include_disabled)
    return ApiResponse.ok([
        AssistantToolResponse.model_validate(t).model_dump(by_alias=True)
        for t in tools
    ])


@router.get("/tools/{id}", response_model=ApiResponse)
def get_tool(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    tool = service.get_tool(id)
    return ApiResponse.ok(AssistantToolResponse.model_validate(tool).model_dump(by_alias=True))


@router.post("/tools", response_model=ApiResponse)
def create_tool(request: AssistantToolCreateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    tool = service.create_tool(request)
    return ApiResponse.ok(AssistantToolResponse.model_validate(tool).model_dump(by_alias=True))


@router.put("/tools/{id}", response_model=ApiResponse)
def update_tool(id: UUID, request: AssistantToolUpdateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    tool = service.update_tool(id, request)
    return ApiResponse.ok(AssistantToolResponse.model_validate(tool).model_dump(by_alias=True))


@router.delete("/tools/{id}", response_model=ApiResponse)
def delete_tool(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    service.delete_tool(id)
    return ApiResponse.ok(None, "Tool deleted")


# ==================== Skills ====================

@router.get("/skills", response_model=ApiResponse)
def list_skills(
    sync_system: bool = Query(True),
    include_disabled: bool = Query(True),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    skills = service.list_skills(sync_system=sync_system, include_disabled=include_disabled)
    return ApiResponse.ok([
        AssistantSkillResponse.model_validate(s).model_dump(by_alias=True)
        for s in skills
    ])


@router.get("/skills/{id}", response_model=ApiResponse)
def get_skill(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.get_skill(id)
    return ApiResponse.ok(AssistantSkillResponse.model_validate(skill).model_dump(by_alias=True))


@router.post("/skills", response_model=ApiResponse)
def create_skill(request: AssistantSkillCreateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.create_skill(request)
    return ApiResponse.ok(AssistantSkillResponse.model_validate(skill).model_dump(by_alias=True))


@router.put("/skills/{id}", response_model=ApiResponse)
def update_skill(id: UUID, request: AssistantSkillUpdateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.update_skill(id, request)
    return ApiResponse.ok(AssistantSkillResponse.model_validate(skill).model_dump(by_alias=True))


@router.post("/skills/{id}/reset", response_model=ApiResponse)
def reset_skill(id: UUID, request: ResetSkillRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.reset_skill(id, confirm=request.confirm)
    return ApiResponse.ok(AssistantSkillResponse.model_validate(skill).model_dump(by_alias=True))


@router.post("/skills/reset-all", response_model=ApiResponse)
def reset_all_skills(request: ResetSkillRequest, db: Session = Depends(get_db)) -> ApiResponse:
    """重置所有系统技能到默认配置，并清理已下线的系统技能"""
    service = AssistantConfigService(db)
    result = service.reset_all_system_skills(confirm=request.confirm)
    return ApiResponse.ok(result)


@router.delete("/skills/{id}", response_model=ApiResponse)
def delete_skill(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    service.delete_skill(id)
    return ApiResponse.ok(None, "Skill deleted")
