from __future__ import annotations

from fastapi import APIRouter

from app.ai.schemas import AiGenerateRequest, AiGenerateResponse
from app.ai.service import AiService
from app.common.responses import ApiResponse


router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/generate", response_model=ApiResponse)
def generate(request: AiGenerateRequest) -> ApiResponse:
    service = AiService()
    result: AiGenerateResponse = service.generate(request)
    return ApiResponse.ok(result.model_dump(by_alias=True))

