from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.schemas import AiGenerateRequest, AiGenerateResponse
from app.ai.service import AiService
from app.common.responses import ApiResponse
from app.database import get_db


router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/generate", response_model=ApiResponse)
def generate(request: AiGenerateRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = AiService(db)
    result: AiGenerateResponse = service.generate(request)
    return ApiResponse.ok(result.model_dump(by_alias=True))

