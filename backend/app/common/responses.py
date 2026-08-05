from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    success: bool
    code: int
    message: str
    data: Optional[Any] = None

    @classmethod
    def ok(cls, data: Any = None, message: str = "OK") -> "ApiResponse":
        return cls(success=True, code=0, message=message, data=data)

    @classmethod
    def fail(
        cls,
        code: int,
        message: str,
        data: Any = None,
    ) -> "ApiResponse":
        return cls(success=False, code=code, message=message, data=data)

    def as_json_dict(self) -> dict[str, Any]:
        """Serialize the envelope for JSONResponse content (JSON-safe)."""
        return self.model_dump(mode="json")


def ok_json_content(data: Any = None, message: str = "OK") -> dict[str, Any]:
    """JSON-safe success envelope for status-bearing public routes (e.g. /ready)."""
    return ApiResponse.ok(data, message=message).as_json_dict()
