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
    WorkflowInput,
    WorkflowValidationResponse,
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


# ==================== Workflow ====================

@router.get("/workflow/node-types", response_model=ApiResponse)
def list_node_types() -> ApiResponse:
    """获取支持的工作流节点类型目录"""
    from app.assistant.skills.base import NodeType
    from typing import get_args

    node_types = []
    for nt in get_args(NodeType):
        node_types.append({
            "type": nt,
            "label": _NODE_TYPE_LABELS.get(nt, nt),
            "description": _NODE_TYPE_DESCRIPTIONS.get(nt, ""),
        })
    return ApiResponse.ok(node_types)


@router.put("/skills/{id}/workflow", response_model=ApiResponse)
def update_workflow(
    id: UUID,
    request: WorkflowInput,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """仅更新 Skill 的工作流 DAG（nodes + edges + viewport）"""
    service = AssistantConfigService(db)
    skill = service.update_workflow(id, request)
    return ApiResponse.ok(
        AssistantSkillResponse.model_validate(skill).model_dump(by_alias=True)
    )


@router.post("/skills/{id}/validate-workflow", response_model=ApiResponse)
def validate_workflow(
    id: UUID,
    request: WorkflowInput,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """验证工作流 DAG 拓扑"""
    from app.assistant.skills.workflow_validator import (
        validate_parallel_branches,
        validate_workflow as _validate_workflow,
    )

    result = _validate_workflow(request.nodes, request.edges)
    # Also run parallel branch validation
    parallel_result = validate_parallel_branches(request.nodes, request.edges)
    all_errors = result.errors + parallel_result.errors

    resp = WorkflowValidationResponse(
        valid=len(all_errors) == 0,
        errors=[{"node_id": e.node_id, "message": e.message} for e in all_errors],
    )
    return ApiResponse.ok(resp.model_dump(by_alias=True))


# Node type metadata
_NODE_TYPE_LABELS = {
    "start": "Start",
    "llm": "LLM",
    "tool": "Tool",
    "if_else": "IF/ELSE",
    "template": "Template",
    "parameter_extractor": "Parameter Extractor",
    "knowledge_retrieval": "Knowledge Retrieval",
    "variable_aggregator": "Variable Aggregator",
}

_NODE_TYPE_DESCRIPTIONS = {
    "start": "Workflow entry point, defines input variables",
    "llm": "Call LLM for analysis or optional final output",
    "tool": "Execute a registered tool",
    "if_else": "IF/ELIF/ELSE branching with configurable conditions and logic",
    "template": "Transform data using template strings",
    "parameter_extractor": "Extract structured parameters from text using LLM",
    "knowledge_retrieval": "Search knowledge base for relevant context",
    "variable_aggregator": "Merge outputs from parallel branches",
}
