from __future__ import annotations

import logging
import traceback
from typing import TYPE_CHECKING, Any, Optional

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from app.common.responses import ApiResponse
from app.common.request_context import get_request_id

if TYPE_CHECKING:
    from fastapi import FastAPI


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


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _make_json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)


# Keys that commonly carry submitted request values (incl. secrets) in Pydantic v2
# validation error dicts. Loc/msg/type remain for clients; inputs are redacted.
_VALIDATION_ERROR_REDACT_KEYS = frozenset({"input", "url"})


def sanitize_validation_errors(errors: Any) -> list[Any]:
    """Return client/log-safe validation errors without submitted input values.

    Pydantic v2 ``exc.errors()`` includes an ``input`` field with the raw
    submitted value (e.g. passwords on change-password). Strip that and other
    high-risk keys while preserving loc/msg/type for debugging. Nested ``ctx``
    is retained only as JSON-safe constraint metadata (not raw inputs).
    """
    if not isinstance(errors, list):
        safe = _make_json_safe(errors)
        return safe if isinstance(safe, list) else [safe]

    sanitized: list[Any] = []
    for error in errors:
        if not isinstance(error, dict):
            sanitized.append(_make_json_safe(error))
            continue
        cleaned: dict[str, Any] = {}
        for key, value in error.items():
            key_str = str(key)
            if key_str in _VALIDATION_ERROR_REDACT_KEYS:
                continue
            if key_str == "ctx" and isinstance(value, dict):
                # Keep schema constraint metadata; drop any nested input-like keys.
                cleaned[key_str] = {
                    str(ck): _make_json_safe(cv)
                    for ck, cv in value.items()
                    if str(ck) not in _VALIDATION_ERROR_REDACT_KEYS
                }
                continue
            cleaned[key_str] = _make_json_safe(value)
        sanitized.append(cleaned)
    return sanitized


def register_exception_handlers(app: "FastAPI", *, debug: bool = False) -> None:
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

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
        response = JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code=exc.code, message=exc.message, data=exc.details).model_dump(),
        )
        if getattr(request.state, "operator_auth_clear_cookies", False):
            # Lazy imports avoid circular deps with operator_auth.dependencies.
            from app.config import get_settings
            from app.operator_auth.dependencies import clear_session_cookies

            clear_session_cookies(response, settings=get_settings())
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        safe_errors = sanitize_validation_errors(exc.errors())
        logger.warning(
            "validation_error request_id=%s method=%s path=%s errors=%s",
            request_id,
            request.method,
            request.url.path,
            safe_errors,
        )
        return JSONResponse(
            status_code=422,
            content=ApiResponse.fail(
                code=42200,
                message="Validation Error",
                data=safe_errors,
            ).model_dump(),
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
        response = JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code=exc.status_code, message=message).model_dump(),
        )
        if getattr(request.state, "operator_auth_clear_cookies", False):
            from app.config import get_settings
            from app.operator_auth.dependencies import clear_session_cookies

            clear_session_cookies(response, settings=get_settings())
            response.headers["Cache-Control"] = "no-store"
        return response

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
