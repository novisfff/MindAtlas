from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.assistant_config.schemas import (
    AgentPublishRequest,
    AgentVersionListResponse,
    AgentTestRunRequest,
    AssistantAgentProfileCreateRequest,
    AssistantAgentProfileResponse,
    AssistantAgentProfileUpdateRequest,
    AssistantSkillCreateRequest,
    AssistantSkillResponse,
    AssistantSkillUpdateRequest,
    AssistantToolCreateRequest,
    AssistantToolResponse,
    AssistantToolUpdateRequest,
    AssistantWorkflowCreateRequest,
    AssistantWorkflowResponse,
    AssistantWorkflowUpdateRequest,
    ClearVersionsResponse,
    DeleteVersionResponse,
    RollbackVersionResponse,
    ResetSkillRequest,
    SystemToolDefinitionResponse,
    SystemToolEnabledUpdateRequest,
    WorkflowInput,
    WorkflowPublishRequest,
    WorkflowTestRunRequest,
    WorkflowVersionListResponse,
    WorkflowValidationResponse,
    HumanApprovalDecisionRequest,
)
from app.assistant_config.workflow_test_service import WorkflowTestRunService
from app.assistant_config.agent_test_service import AgentTestRunService
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision
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
        AssistantSkillResponse.model_validate(service.serialize_skill(s)).model_dump(by_alias=True)
        for s in skills
    ])


@router.get("/skills/{id}", response_model=ApiResponse)
def get_skill(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.get_skill(id)
    return ApiResponse.ok(
        AssistantSkillResponse.model_validate(service.serialize_skill(skill)).model_dump(by_alias=True)
    )


@router.post("/skills", response_model=ApiResponse)
def create_skill(request: AssistantSkillCreateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.create_skill(request)
    return ApiResponse.ok(
        AssistantSkillResponse.model_validate(service.serialize_skill(skill)).model_dump(by_alias=True)
    )


@router.put("/skills/{id}", response_model=ApiResponse)
def update_skill(id: UUID, request: AssistantSkillUpdateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.update_skill(id, request)
    return ApiResponse.ok(
        AssistantSkillResponse.model_validate(service.serialize_skill(skill)).model_dump(by_alias=True)
    )


@router.post("/skills/{id}/reset", response_model=ApiResponse)
def reset_skill(id: UUID, request: ResetSkillRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    skill = service.reset_skill(id, confirm=request.confirm)
    return ApiResponse.ok(
        AssistantSkillResponse.model_validate(service.serialize_skill(skill)).model_dump(by_alias=True)
    )


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


# ==================== Agents ====================

@router.get("/agents", response_model=ApiResponse)
def list_agent_profiles(
    include_disabled: bool = Query(True),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    items = service.list_agent_profiles(include_disabled=include_disabled)
    return ApiResponse.ok([
        AssistantAgentProfileResponse.model_validate(service.serialize_agent_profile(item)).model_dump(by_alias=True)
        for item in items
    ])


@router.get("/agents/{id}", response_model=ApiResponse)
def get_agent_profile(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.get_agent_profile(id)
    return ApiResponse.ok(
        AssistantAgentProfileResponse.model_validate(service.serialize_agent_profile(item)).model_dump(by_alias=True)
    )


@router.post("/agents", response_model=ApiResponse)
def create_agent_profile(
    request: AssistantAgentProfileCreateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.create_agent_profile(request)
    return ApiResponse.ok(
        AssistantAgentProfileResponse.model_validate(service.serialize_agent_profile(item)).model_dump(by_alias=True)
    )


@router.put("/agents/{id}", response_model=ApiResponse)
def update_agent_profile(
    id: UUID,
    request: AssistantAgentProfileUpdateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.update_agent_profile(id, request)
    return ApiResponse.ok(
        AssistantAgentProfileResponse.model_validate(service.serialize_agent_profile(item)).model_dump(by_alias=True)
    )


@router.get("/agents/{id}/versions", response_model=ApiResponse)
def list_agent_profile_versions(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.list_agent_profile_versions(id)
    return ApiResponse.ok(AgentVersionListResponse.model_validate(payload).model_dump(by_alias=True))


@router.post("/agents/{id}/publish", response_model=ApiResponse)
def publish_agent_profile(
    id: UUID,
    request: AgentPublishRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.publish_agent_profile(id, request)
    return ApiResponse.ok(
        AssistantAgentProfileResponse.model_validate(service.serialize_agent_profile(item)).model_dump(by_alias=True)
    )


@router.post("/agents/{id}/versions/{version_id}/rollback", response_model=ApiResponse)
def rollback_agent_profile_version(
    id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.rollback_agent_profile_version(id, version_id)
    return ApiResponse.ok(RollbackVersionResponse.model_validate(payload).model_dump(by_alias=True))


@router.delete("/agents/{id}/versions/{version_id}", response_model=ApiResponse)
def delete_agent_profile_version(
    id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.delete_agent_profile_version(id, version_id)
    return ApiResponse.ok(DeleteVersionResponse.model_validate(payload).model_dump(by_alias=True))


@router.post("/agents/{id}/versions/clear", response_model=ApiResponse)
def clear_agent_profile_versions(
    id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.clear_agent_profile_versions(id)
    return ApiResponse.ok(ClearVersionsResponse.model_validate(payload).model_dump(by_alias=True))


@router.delete("/agents/{id}", response_model=ApiResponse)
def delete_agent_profile(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    service.delete_agent_profile(id)
    return ApiResponse.ok(None, "Agent profile deleted")


@router.post("/agents/{id}/test-run")
def test_run_agent_profile(
    id: UUID,
    request: AgentTestRunRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """在 Agent 编辑器草稿上执行测试运行（不持久化）。"""
    service = AgentTestRunService(db)
    prepared = service.prepare(id, request)
    return StreamingResponse(
        service.stream(prepared),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== Workflow ====================

@router.get("/workflow/node-types", response_model=ApiResponse)
def list_node_types() -> ApiResponse:
    """获取支持的工作流节点类型目录"""
    from app.assistant.skill_catalog.base import NodeType
    from typing import get_args

    node_types = []
    for nt in get_args(NodeType):
        node_types.append({
            "type": nt,
            "label": _NODE_TYPE_LABELS.get(nt, nt),
            "description": _NODE_TYPE_DESCRIPTIONS.get(nt, ""),
        })
    return ApiResponse.ok(node_types)


@router.get("/workflows", response_model=ApiResponse)
def list_workflows(
    include_disabled: bool = Query(True),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    items = service.list_workflows(include_disabled=include_disabled)
    return ApiResponse.ok([
        AssistantWorkflowResponse.model_validate(service.serialize_workflow(item)).model_dump(by_alias=True)
        for item in items
    ])


@router.get("/workflows/{id}", response_model=ApiResponse)
def get_workflow(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.get_workflow(id)
    return ApiResponse.ok(
        AssistantWorkflowResponse.model_validate(service.serialize_workflow(item)).model_dump(by_alias=True)
    )


@router.post("/workflows", response_model=ApiResponse)
def create_workflow(
    request: AssistantWorkflowCreateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.create_workflow(request)
    return ApiResponse.ok(
        AssistantWorkflowResponse.model_validate(service.serialize_workflow(item)).model_dump(by_alias=True)
    )


@router.put("/workflows/{id}", response_model=ApiResponse)
def update_workflow_entity(
    id: UUID,
    request: AssistantWorkflowUpdateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.update_workflow_entity(id, request)
    return ApiResponse.ok(
        AssistantWorkflowResponse.model_validate(service.serialize_workflow(item)).model_dump(by_alias=True)
    )


@router.get("/workflows/{id}/versions", response_model=ApiResponse)
def list_workflow_versions(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.list_workflow_versions(id)
    return ApiResponse.ok(WorkflowVersionListResponse.model_validate(payload).model_dump(by_alias=True))


@router.post("/workflows/{id}/publish", response_model=ApiResponse)
def publish_workflow(
    id: UUID,
    request: WorkflowPublishRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    item = service.publish_workflow(id, request)
    return ApiResponse.ok(
        AssistantWorkflowResponse.model_validate(service.serialize_workflow(item)).model_dump(by_alias=True)
    )


@router.post("/workflows/{id}/versions/{version_id}/rollback", response_model=ApiResponse)
def rollback_workflow_version(
    id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.rollback_workflow_version(id, version_id)
    return ApiResponse.ok(RollbackVersionResponse.model_validate(payload).model_dump(by_alias=True))


@router.delete("/workflows/{id}/versions/{version_id}", response_model=ApiResponse)
def delete_workflow_version(
    id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.delete_workflow_version(id, version_id)
    return ApiResponse.ok(DeleteVersionResponse.model_validate(payload).model_dump(by_alias=True))


@router.post("/workflows/{id}/versions/clear", response_model=ApiResponse)
def clear_workflow_versions(
    id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    payload = service.clear_workflow_versions(id)
    return ApiResponse.ok(ClearVersionsResponse.model_validate(payload).model_dump(by_alias=True))


@router.delete("/workflows/{id}", response_model=ApiResponse)
def delete_workflow(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantConfigService(db)
    service.delete_workflow(id)
    return ApiResponse.ok(None, "Workflow deleted")


def _validate_workflow_payload(
    db: Session,
    request: WorkflowInput,
    *,
    workflow=None,
) -> WorkflowValidationResponse:
    from app.assistant.workflow.validation.validator import (
        validate_parallel_branches,
        validate_workflow as _validate_workflow,
    )

    result = _validate_workflow(request.nodes, request.edges)
    parallel_result = validate_parallel_branches(request.nodes, request.edges)
    all_errors: list[dict] = [
        {"node_id": e.node_id, "message": e.message}
        for e in (result.errors + parallel_result.errors)
    ]

    service = AssistantConfigService(db)
    if len(all_errors) == 0:
        try:
            service.validate_workflow_dependencies(request)
        except ApiException as exc:
            all_errors.append({"node_id": None, "message": exc.message})

    for message in service.collect_workflow_extra_validation_errors(
        workflow=workflow,
        workflow_input=request,
    ):
        all_errors.append({"node_id": None, "message": message})

    return WorkflowValidationResponse(
        valid=len(all_errors) == 0,
        errors=all_errors,
    )


@router.post("/workflows/{id}/validate", response_model=ApiResponse)
def validate_workflow_by_id(
    id: UUID,
    request: WorkflowInput,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantConfigService(db)
    workflow = service.get_workflow(id)
    resp = _validate_workflow_payload(db, request, workflow=workflow)
    return ApiResponse.ok(resp.model_dump(by_alias=True))


@router.post("/workflows/{id}/test-run")
def test_run_workflow_by_id(
    id: UUID,
    request: WorkflowTestRunRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    service = WorkflowTestRunService(db)
    prepared = service.prepare_for_workflow(id, request)
    return StreamingResponse(
        service.stream(prepared),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/approvals/{approval_id}/decision", response_model=ApiResponse)
def submit_run_approval_decision(
    run_id: str,
    approval_id: UUID,
    request: HumanApprovalDecisionRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        payload = submit_human_approval_decision(
            db,
            approval_id=approval_id,
            decision=request.decision,
            values=request.values,
            comment=request.comment,
            expected_run_id=run_id,
        )
    except ValueError as exc:
        raise ApiException(
            status_code=400,
            code=42252,
            message=str(exc),
        ) from exc
    return ApiResponse.ok(payload)


# ==================== Compatibility Skill Workflow Routes ====================

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
        AssistantSkillResponse.model_validate(service.serialize_skill(skill)).model_dump(by_alias=True)
    )


@router.post("/skills/{id}/validate-workflow", response_model=ApiResponse)
def validate_workflow(
    id: UUID,
    request: WorkflowInput,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """兼容路由：按 skill 绑定的 workflow 做验证。"""
    service = AssistantConfigService(db)
    workflow = service.get_skill_workflow(id)
    resp = _validate_workflow_payload(db, request, workflow=workflow)
    return ApiResponse.ok(resp.model_dump(by_alias=True))


@router.post("/skills/{id}/workflow/test-run")
def test_run_workflow(
    id: UUID,
    request: WorkflowTestRunRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """在编辑器草稿上执行工作流测试运行（不持久化）。"""
    service = WorkflowTestRunService(db)
    prepared = service.prepare(id, request)
    return StreamingResponse(
        service.stream(prepared),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Node type metadata
_NODE_TYPE_LABELS = {
    "start": "Start",
    "llm": "LLM",
    "tool": "Tool",
    "if_else": "IF/ELSE",
    "parameter_extractor": "Parameter Extractor",
    "knowledge_retrieval": "Knowledge Retrieval",
    "iteration": "Iteration",
    "loop": "Loop",
    "code_executor": "Code Executor",
    "http_request": "HTTP Request",
    "variable_assign": "Variable Assign",
    "human_in_loop": "Human In Loop",
    "output": "Output",
}

_NODE_TYPE_DESCRIPTIONS = {
    "start": "Workflow entry point, defines input variables",
    "llm": "Call LLM for analysis and intermediate generation",
    "tool": "Execute a registered tool",
    "if_else": "IF/ELIF/ELSE branching with configurable conditions and logic",
    "parameter_extractor": "Extract structured parameters from text using LLM",
    "knowledge_retrieval": "Search knowledge base with optional mode/topK overrides",
    "iteration": "Iterate over an array and execute inner subflow per item",
    "loop": "Repeat inner subflow until termination conditions are met",
    "code_executor": "Run sandboxed Python or JavaScript code with structured inputs and outputs",
    "http_request": "Send HTTP requests with templated URL/headers/body and structured response fields",
    "variable_assign": "Assign or update workflow env variable values",
    "human_in_loop": "Pause workflow and wait for human approval with editable fields",
    "output": "Workflow terminal node that formats and emits final response",
}
