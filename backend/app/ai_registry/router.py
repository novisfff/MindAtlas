from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.ai_registry.schemas import (
    AiCredentialCreateRequest,
    AiCredentialResponse,
    AiCredentialTestConnectionResponse,
    AiCredentialUpdateRequest,
    AiModelCapabilityProbeCapabilityItem,
    AiModelCapabilityProbeResponse,
    AiModelCapabilityProbeRunRequest,
    AiModelCreateRequest,
    AiModelResponse,
    AiModelUpdateRequest,
    ComponentBinding,
    DiscoverModelsByKeyRequest,
    DiscoverModelsResponse,
    ModelBindingsResponse,
    UpdateModelBindingsRequest,
)
from app.ai_registry.service import (
    AiBindingService,
    AiCredentialService,
    AiModelCapabilityProbeService,
    AiModelService,
    LiveProbeResult,
)
from app.ai_registry.models import AiModelCapabilityProbe
from app.common.responses import ApiResponse
from app.database import get_db

credential_router = APIRouter(prefix="/api/ai-credentials", tags=["ai-credentials"])
model_router = APIRouter(prefix="/api/ai-models", tags=["ai-models"])
binding_router = APIRouter(prefix="/api/model-bindings", tags=["model-bindings"])


# ==================== Credentials ====================

@credential_router.get("", response_model=ApiResponse)
def list_credentials(db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    items = svc.find_all()
    return ApiResponse.ok([AiCredentialResponse.model_validate(x).model_dump(by_alias=True) for x in items])


@credential_router.get("/{id}", response_model=ApiResponse)
def get_credential(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    item = svc.find_by_id(id)
    return ApiResponse.ok(AiCredentialResponse.model_validate(item).model_dump(by_alias=True))


@credential_router.post("", response_model=ApiResponse)
def create_credential(request: AiCredentialCreateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    item = svc.create(request.name, request.base_url, request.api_key)
    return ApiResponse.ok(AiCredentialResponse.model_validate(item).model_dump(by_alias=True))


@credential_router.put("/{id}", response_model=ApiResponse)
def update_credential(id: UUID, request: AiCredentialUpdateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    item = svc.update(id, name=request.name, base_url=request.base_url, api_key=request.api_key)
    return ApiResponse.ok(AiCredentialResponse.model_validate(item).model_dump(by_alias=True))


@credential_router.delete("/{id}", response_model=ApiResponse)
def delete_credential(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    svc.delete(id)
    return ApiResponse.ok(None)


@credential_router.post("/{id}/test-connection", response_model=ApiResponse)
def test_credential(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    ok, status_code, message = svc.test_connection(id)
    return ApiResponse.ok(AiCredentialTestConnectionResponse(ok=ok, status_code=status_code, message=message).model_dump(by_alias=True))


@credential_router.post("/discover-models", response_model=ApiResponse)
def discover_models_by_key(request: DiscoverModelsByKeyRequest) -> ApiResponse:
    ok, models, message = AiCredentialService.discover_models_by_key(base_url=request.base_url, api_key=request.api_key)
    return ApiResponse.ok(DiscoverModelsResponse(ok=ok, models=models, message=message).model_dump(by_alias=True))


@credential_router.post("/{id}/discover-models", response_model=ApiResponse)
def discover_models_by_id(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiCredentialService(db)
    ok, models, message = svc.discover_models_by_id(id)
    return ApiResponse.ok(DiscoverModelsResponse(ok=ok, models=models, message=message).model_dump(by_alias=True))


# ==================== Models ====================

@model_router.get("", response_model=ApiResponse)
def list_models(
    credential_id: UUID | None = Query(default=None, alias="credentialId"),
    model_type: str | None = Query(default=None, alias="modelType"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    svc = AiModelService(db)
    items = svc.find_all(credential_id=credential_id, model_type=model_type)  # type: ignore[arg-type]
    return ApiResponse.ok([AiModelResponse.model_validate(x).model_dump(by_alias=True) for x in items])


@model_router.get("/{id}", response_model=ApiResponse)
def get_model(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiModelService(db)
    item = svc.find_by_id(id)
    return ApiResponse.ok(AiModelResponse.model_validate(item).model_dump(by_alias=True))


@model_router.post("", response_model=ApiResponse)
def create_model(request: AiModelCreateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiModelService(db)
    item = svc.create(credential_id=request.credential_id, name=request.name, model_type=request.model_type)
    return ApiResponse.ok(AiModelResponse.model_validate(item).model_dump(by_alias=True))


@model_router.put("/{id}", response_model=ApiResponse)
def update_model(id: UUID, request: AiModelUpdateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiModelService(db)
    item = svc.update(id, name=request.name, model_type=request.model_type)
    return ApiResponse.ok(AiModelResponse.model_validate(item).model_dump(by_alias=True))


@model_router.delete("/{id}", response_model=ApiResponse)
def delete_model(
    id: UUID,
    confirm_bound_bindings: bool = Query(False, alias="confirmBoundBindings"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    svc = AiModelService(db)
    svc.delete(id, confirm_bound_bindings=confirm_bound_bindings)
    return ApiResponse.ok(None)


def _probe_capabilities_payload(raw: object) -> dict[str, dict[str, object | None]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, object | None]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        out[str(key)] = {
            "observation": value.get("observation"),
            "safeReasonCode": value.get("safeReasonCode") or value.get("safe_reason_code"),
        }
    return out


def _probe_to_response(
    probe: AiModelCapabilityProbe,
    *,
    is_current: bool,
    is_stale: bool,
    promotion_outcome: str | None = None,
) -> dict:
    caps_raw = _probe_capabilities_payload(probe.capabilities)
    caps = {
        key: AiModelCapabilityProbeCapabilityItem(
            observation=item["observation"],  # type: ignore[arg-type]
            safe_reason_code=item.get("safeReasonCode"),  # type: ignore[arg-type]
        )
        for key, item in caps_raw.items()
        if item.get("observation") in {"passed", "failed", "not_observed"}
    }
    payload = AiModelCapabilityProbeResponse(
        id=probe.id,
        model_id=probe.model_id,
        probe_contract_version=int(probe.probe_contract_version),
        adapter_key=probe.adapter_key,
        adapter_revision=probe.adapter_revision,
        model_config_digest=probe.model_config_digest,
        status=probe.status,  # type: ignore[arg-type]
        capabilities=caps,
        probe_digest=probe.probe_digest,
        safe_error_code=probe.safe_error_code,
        safe_error_summary=probe.safe_error_summary,
        created_at=probe.created_at,
        is_current=is_current,
        is_stale_for_current_config=is_stale,
        promotion_outcome=promotion_outcome,  # type: ignore[arg-type]
    )
    return payload.model_dump(by_alias=True)


@model_router.post("/{model_id}/capability-probe", response_model=ApiResponse)
def run_capability_probe(
    model_id: UUID,
    request: AiModelCapabilityProbeRunRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    svc = AiModelCapabilityProbeService(db)
    result: LiveProbeResult = svc.run_live_probe(
        model_id,
        adapter_key=request.adapter_key,
        confirm_provider_call=request.confirm_provider_call,
        promote=request.promote,
    )
    return ApiResponse.ok(
        _probe_to_response(
            result.probe,
            is_current=result.is_current,
            is_stale=result.is_stale_for_current_config,
            promotion_outcome=result.promotion_outcome,
        )
    )


@model_router.get("/{model_id}/capability-probes", response_model=ApiResponse)
def list_capability_probes(
    model_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ApiResponse:
    svc = AiModelCapabilityProbeService(db)
    items = svc.list_for_model(model_id, limit=limit, offset=offset)
    return ApiResponse.ok(
        [
            _probe_to_response(probe, is_current=is_current, is_stale=is_stale)
            for probe, is_current, is_stale in items
        ]
    )


# ==================== Bindings ====================

@binding_router.get("", response_model=ApiResponse)
def get_bindings(db: Session = Depends(get_db)) -> ApiResponse:
    bsvc = AiBindingService(db)
    bindings = bsvc.get_bindings()

    msvc = AiModelService(db)

    def _model_or_none(mid: UUID | None) -> AiModelResponse | None:
        if not mid:
            return None
        try:
            m = msvc.find_by_id(mid)
            return AiModelResponse.model_validate(m)
        except Exception:
            return None

    assistant = bindings["assistant"]
    lightrag = bindings["lightrag"]
    workflow_copilot = bindings["workflow_copilot"]
    payload = ModelBindingsResponse(
        assistant=ComponentBinding(
            llm_model_id=assistant.llm_model_id,
            embedding_model_id=assistant.embedding_model_id,
            llm_model=_model_or_none(assistant.llm_model_id),
            embedding_model=_model_or_none(assistant.embedding_model_id),
        ),
        lightrag=ComponentBinding(
            llm_model_id=lightrag.llm_model_id,
            embedding_model_id=lightrag.embedding_model_id,
            llm_model=_model_or_none(lightrag.llm_model_id),
            embedding_model=_model_or_none(lightrag.embedding_model_id),
        ),
        workflow_copilot=ComponentBinding(
            llm_model_id=workflow_copilot.llm_model_id,
            embedding_model_id=workflow_copilot.embedding_model_id,
            llm_model=_model_or_none(workflow_copilot.llm_model_id),
            embedding_model=_model_or_none(workflow_copilot.embedding_model_id),
        ),
    )
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@binding_router.put("", response_model=ApiResponse)
def update_bindings(request: UpdateModelBindingsRequest, db: Session = Depends(get_db)) -> ApiResponse:
    svc = AiBindingService(db)
    if request.assistant is not None:
        svc.update_component(
            "assistant",
            llm_model_id=request.assistant.llm_model_id,
            embedding_model_id=request.assistant.embedding_model_id,
        )
    if request.lightrag is not None:
        svc.update_component(
            "lightrag",
            llm_model_id=request.lightrag.llm_model_id,
            embedding_model_id=request.lightrag.embedding_model_id,
        )
    if request.workflow_copilot is not None:
        svc.update_component(
            "workflow_copilot",
            llm_model_id=request.workflow_copilot.llm_model_id,
            embedding_model_id=request.workflow_copilot.embedding_model_id,
        )
    return get_bindings(db=db)
