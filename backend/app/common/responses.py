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
