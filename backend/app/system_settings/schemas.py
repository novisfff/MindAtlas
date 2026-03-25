from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.common.schemas import CamelModel


SystemLocale = Literal["zh", "en"]
InitializationEntryTypeOrigin = Literal["default", "custom"]


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


class InitializationStatusResponse(CamelModel):
    initialized: bool
    legacy_auto_completed: bool = Field(alias="legacyAutoCompleted")
    locale: SystemLocale


class InitializationDefaultEntryTypeResponse(CamelModel):
    code: str
    name: str
    description: str = ""
    color: str = ""
    icon: str = ""
    graph_enabled: bool = Field(default=True, alias="graphEnabled")
    ai_enabled: bool = Field(default=True, alias="aiEnabled")
    enabled: bool = True
    origin: InitializationEntryTypeOrigin = "default"


class InitializationDefaultsResponse(CamelModel):
    locale: SystemLocale
    entry_types: list[InitializationDefaultEntryTypeResponse] = Field(default_factory=list, alias="entryTypes")


class InitializationAiCredentialRequest(CamelModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048, alias="baseUrl")
    api_key: str = Field(min_length=1, max_length=4096, alias="apiKey")


class InitializationLlmModelRequest(CamelModel):
    name: str = Field(min_length=1, max_length=255)


class InitializationEntryTypeRequest(CamelModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    color: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, max_length=64)
    graph_enabled: bool = Field(default=True, alias="graphEnabled")
    ai_enabled: bool = Field(default=True, alias="aiEnabled")
    enabled: bool = True
    origin: InitializationEntryTypeOrigin

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("name", "description", "color", "icon", mode="before")
    @classmethod
    def _normalize_string_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class InitializeSystemRequest(CamelModel):
    locale: SystemLocale
    ai_credential: InitializationAiCredentialRequest = Field(alias="aiCredential")
    llm_model: InitializationLlmModelRequest = Field(alias="llmModel")
    entry_types: list[InitializationEntryTypeRequest] = Field(default_factory=list, alias="entryTypes")


class InitializationCompletionResponse(CamelModel):
    initialized: bool
    locale: SystemLocale
