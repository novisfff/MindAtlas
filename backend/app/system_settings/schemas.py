from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from app.common.schemas import CamelModel


SystemLocale = Literal["zh", "en"]


class SystemLocaleResponse(CamelModel):
    locale: SystemLocale
    persisted: bool


class SystemLocaleUpdateRequest(CamelModel):
    locale: SystemLocale

    @field_validator("locale")
    @classmethod
    def _validate_locale(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"zh", "en"}:
            raise ValueError("locale must be zh or en")
        return normalized
