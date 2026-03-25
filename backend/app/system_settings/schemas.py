from __future__ import annotations

from uuid import UUID
from typing import Literal

from pydantic import Field, field_validator

from app.common.schemas import CamelModel


SystemLocale = Literal["zh", "en"]
InitializationEntryTypeOrigin = Literal["default", "custom"]
RuntimeConfigSource = Literal["app_config", "environment_default", "default"]
RuntimeConfigGroupKey = Literal["storage", "knowledge_graph", "document_parsing", "automation"]


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
    capability_modules: list["CapabilityModuleSummaryResponse"] = Field(default_factory=list, alias="capabilityModules")
    runtime_config: "RuntimeConfigResponse" = Field(alias="runtimeConfig")


class InitializationAiCredentialRequest(CamelModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048, alias="baseUrl")
    api_key: str = Field(min_length=1, max_length=4096, alias="apiKey")


class InitializationLlmModelRequest(CamelModel):
    name: str = Field(min_length=1, max_length=255)


class SecretFieldStateResponse(CamelModel):
    configured: bool = False
    hint: str | None = None


class RuntimeConfigModuleBase(CamelModel):
    group_key: RuntimeConfigGroupKey = Field(alias="groupKey")
    configured: bool
    source: RuntimeConfigSource
    restart_required: bool = Field(alias="restartRequired")
    has_secrets: bool = Field(alias="hasSecrets")
    effective_summary: str = Field(default="", alias="effectiveSummary")


class CapabilityModuleSummaryResponse(RuntimeConfigModuleBase):
    title: str
    description: str
    allow_skip: bool = Field(default=True, alias="allowSkip")


class RuntimeStorageConfigRequest(CamelModel):
    endpoint: str | None = Field(default=None, max_length=512)
    access_key: str | None = Field(default=None, max_length=512, alias="accessKey")
    secret_key: str | None = Field(default=None, max_length=4096, alias="secretKey")
    bucket: str | None = Field(default=None, max_length=255)
    secure: bool | None = None
    max_file_size_mb: int | None = Field(default=None, ge=1, le=1024, alias="maxFileSizeMb")
    max_pdf_pages: int | None = Field(default=None, ge=1, le=5000, alias="maxPdfPages")

    @field_validator("endpoint", "access_key", "secret_key", "bucket", mode="before")
    @classmethod
    def _normalize_optional_storage_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class RuntimeStorageConfigResponse(RuntimeConfigModuleBase):
    endpoint: str = ""
    bucket: str = ""
    secure: bool = False
    max_file_size_mb: int = Field(default=100, alias="maxFileSizeMb")
    max_pdf_pages: int = Field(default=500, alias="maxPdfPages")
    access_key_state: SecretFieldStateResponse = Field(default_factory=SecretFieldStateResponse, alias="accessKeyState")
    secret_key_state: SecretFieldStateResponse = Field(default_factory=SecretFieldStateResponse, alias="secretKeyState")


class RuntimeKnowledgeGraphConfigRequest(CamelModel):
    enabled: bool | None = None
    neo4j_uri: str | None = Field(default=None, max_length=2048, alias="neo4jUri")
    neo4j_user: str | None = Field(default=None, max_length=255, alias="neo4jUser")
    neo4j_password: str | None = Field(default=None, max_length=4096, alias="neo4jPassword")
    neo4j_database: str | None = Field(default=None, max_length=255, alias="neo4jDatabase")
    workspace: str | None = Field(default=None, max_length=255)
    graph_storage: str | None = Field(default=None, max_length=255, alias="graphStorage")
    summary_language: str | None = Field(default=None, max_length=64, alias="summaryLanguage")
    llm_model_id: UUID | None = Field(default=None, alias="llmModelId")
    llm_model_name: str | None = Field(default=None, max_length=255, alias="llmModelName")
    embedding_model_id: UUID | None = Field(default=None, alias="embeddingModelId")
    embedding_model_name: str | None = Field(default=None, max_length=255, alias="embeddingModelName")
    embedding_host: str | None = Field(default=None, max_length=2048, alias="embeddingHost")
    embedding_api_key: str | None = Field(default=None, max_length=4096, alias="embeddingApiKey")
    rerank_model: str | None = Field(default=None, max_length=255, alias="rerankModel")
    rerank_host: str | None = Field(default=None, max_length=2048, alias="rerankHost")
    rerank_api_key: str | None = Field(default=None, max_length=4096, alias="rerankApiKey")
    rerank_request_format: str | None = Field(default=None, max_length=64, alias="rerankRequestFormat")

    @field_validator(
        "neo4j_uri",
        "neo4j_user",
        "neo4j_password",
        "neo4j_database",
        "workspace",
        "graph_storage",
        "summary_language",
        "llm_model_name",
        "embedding_model_name",
        "embedding_host",
        "embedding_api_key",
        "rerank_model",
        "rerank_host",
        "rerank_api_key",
        "rerank_request_format",
        mode="before",
    )
    @classmethod
    def _normalize_optional_kg_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class RuntimeKnowledgeGraphConfigResponse(RuntimeConfigModuleBase):
    enabled: bool = False
    neo4j_uri: str = Field(default="", alias="neo4jUri")
    neo4j_user: str = Field(default="", alias="neo4jUser")
    neo4j_database: str = Field(default="", alias="neo4jDatabase")
    workspace: str = ""
    graph_storage: str = Field(default="Neo4JStorage", alias="graphStorage")
    summary_language: str = Field(default="", alias="summaryLanguage")
    llm_model_id: UUID | None = Field(default=None, alias="llmModelId")
    llm_model_name: str | None = Field(default=None, alias="llmModelName")
    embedding_model_id: UUID | None = Field(default=None, alias="embeddingModelId")
    embedding_model_name: str | None = Field(default=None, alias="embeddingModelName")
    embedding_host: str = Field(default="", alias="embeddingHost")
    rerank_model: str = Field(default="", alias="rerankModel")
    rerank_host: str = Field(default="", alias="rerankHost")
    rerank_request_format: str = Field(default="standard", alias="rerankRequestFormat")
    neo4j_password_state: SecretFieldStateResponse = Field(default_factory=SecretFieldStateResponse, alias="neo4jPasswordState")
    embedding_api_key_state: SecretFieldStateResponse = Field(default_factory=SecretFieldStateResponse, alias="embeddingApiKeyState")
    rerank_api_key_state: SecretFieldStateResponse = Field(default_factory=SecretFieldStateResponse, alias="rerankApiKeyState")


class RuntimeDocumentParsingConfigRequest(CamelModel):
    worker_enabled: bool | None = Field(default=None, alias="workerEnabled")
    ocr_enabled: bool | None = Field(default=None, alias="ocrEnabled")
    ocr_langs: str | None = Field(default=None, max_length=255, alias="ocrLangs")
    picture_description_enabled: bool | None = Field(default=None, alias="pictureDescriptionEnabled")
    picture_description_url: str | None = Field(default=None, max_length=2048, alias="pictureDescriptionUrl")
    picture_description_api_key: str | None = Field(default=None, max_length=4096, alias="pictureDescriptionApiKey")
    picture_description_model: str | None = Field(default=None, max_length=255, alias="pictureDescriptionModel")
    picture_description_prompt: str | None = Field(default=None, max_length=4000, alias="pictureDescriptionPrompt")
    picture_description_timeout_sec: float | None = Field(default=None, ge=1, le=600, alias="pictureDescriptionTimeoutSec")
    picture_description_params_json: str | None = Field(default=None, max_length=4000, alias="pictureDescriptionParamsJson")
    max_file_size_mb: int | None = Field(default=None, ge=1, le=1024, alias="maxFileSizeMb")
    max_pdf_pages: int | None = Field(default=None, ge=1, le=5000, alias="maxPdfPages")

    @field_validator(
        "ocr_langs",
        "picture_description_url",
        "picture_description_api_key",
        "picture_description_model",
        "picture_description_prompt",
        "picture_description_params_json",
        mode="before",
    )
    @classmethod
    def _normalize_optional_doc_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class RuntimeDocumentParsingConfigResponse(RuntimeConfigModuleBase):
    worker_enabled: bool = Field(default=False, alias="workerEnabled")
    ocr_enabled: bool = Field(default=True, alias="ocrEnabled")
    ocr_langs: str = Field(default="auto", alias="ocrLangs")
    picture_description_enabled: bool = Field(default=False, alias="pictureDescriptionEnabled")
    picture_description_url: str = Field(default="", alias="pictureDescriptionUrl")
    picture_description_model: str = Field(default="", alias="pictureDescriptionModel")
    picture_description_prompt: str = Field(default="", alias="pictureDescriptionPrompt")
    picture_description_timeout_sec: float = Field(default=60.0, alias="pictureDescriptionTimeoutSec")
    picture_description_params_json: str = Field(default="", alias="pictureDescriptionParamsJson")
    max_file_size_mb: int = Field(default=100, alias="maxFileSizeMb")
    max_pdf_pages: int = Field(default=500, alias="maxPdfPages")
    picture_description_api_key_state: SecretFieldStateResponse = Field(default_factory=SecretFieldStateResponse, alias="pictureDescriptionApiKeyState")


class RuntimeAutomationConfigRequest(CamelModel):
    scheduler_enabled: bool | None = Field(default=None, alias="schedulerEnabled")


class RuntimeAutomationConfigResponse(RuntimeConfigModuleBase):
    scheduler_enabled: bool = Field(default=False, alias="schedulerEnabled")


class RuntimeConfigPayloadRequest(CamelModel):
    storage: RuntimeStorageConfigRequest | None = None
    knowledge_graph: RuntimeKnowledgeGraphConfigRequest | None = Field(default=None, alias="knowledgeGraph")
    document_parsing: RuntimeDocumentParsingConfigRequest | None = Field(default=None, alias="documentParsing")
    automation: RuntimeAutomationConfigRequest | None = None


class RuntimeConfigResponse(CamelModel):
    storage: RuntimeStorageConfigResponse
    knowledge_graph: RuntimeKnowledgeGraphConfigResponse = Field(alias="knowledgeGraph")
    document_parsing: RuntimeDocumentParsingConfigResponse = Field(alias="documentParsing")
    automation: RuntimeAutomationConfigResponse


class RuntimeConfigValidationResponse(CamelModel):
    ok: bool
    message: str | None = None
    field_errors: dict[str, str] = Field(default_factory=dict, alias="fieldErrors")


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
    runtime_config: RuntimeConfigPayloadRequest | None = Field(default=None, alias="runtimeConfig")


class InitializationCompletionResponse(CamelModel):
    initialized: bool
    locale: SystemLocale


InitializationDefaultsResponse.model_rebuild()
