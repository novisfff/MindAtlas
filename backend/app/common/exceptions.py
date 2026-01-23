from __future__ import annotations

import logging
import traceback
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from app.common.responses import ApiResponse
from app.common.request_context import get_request_id


class ApiException(StarletteHTTPException):
    def __init__(
        self,
        status_code: int = 400,
        code: int = 40000,
        message: str = "Bad Request",
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.details = details


def register_exception_handlers(app: FastAPI, *, debug: bool = False) -> None:
    logger = logging.getLogger(__name__)

    @app.exception_handler(ApiException)
    async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        logger.warning(
            "api_exception request_id=%s method=%s path=%s status=%s code=%s message=%s",
            request_id,
            request.method,
            request.url.path,
            exc.status_code,
            exc.code,
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code=exc.code, message=exc.message, data=exc.details).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        logger.warning(
            "validation_error request_id=%s method=%s path=%s errors=%s",
            request_id,
            request.method,
            request.url.path,
            exc.errors(),
        )
        return JSONResponse(
            status_code=422,
            content=ApiResponse.fail(code=42200, message="Validation Error", data=exc.errors()).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = str(exc.detail) if exc.detail is not None else "HTTP Error"
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        logger.warning(
            "http_exception request_id=%s method=%s path=%s status=%s message=%s",
            request_id,
            request.method,
            request.url.path,
            exc.status_code,
            message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code=exc.status_code, message=message).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        logger.exception(
            "unhandled_exception request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        details: Any | None = None
        if debug:
            details = {
                "requestId": request_id,
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            }
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content=ApiResponse.fail(code=50000, message="Internal Server Error", data=details).model_dump(),
        )
