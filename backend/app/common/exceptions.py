from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from app.common.responses import ApiResponse


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


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiException)
    async def api_exception_handler(_, exc: ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code=exc.code, message=exc.message, data=exc.details).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiResponse.fail(code=42200, message="Validation Error", data=exc.errors()).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_, exc: StarletteHTTPException) -> JSONResponse:
        message = str(exc.detail) if exc.detail is not None else "HTTP Error"
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code=exc.status_code, message=message).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content=ApiResponse.fail(code=50000, message="Internal Server Error").model_dump(),
        )
