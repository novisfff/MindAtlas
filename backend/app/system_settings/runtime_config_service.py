from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.common.exceptions import ApiException
from app.config import get_settings
from app.database import SessionLocal
from app.system_settings.models import AppSetting
from app.system_settings.schemas import (
    CapabilityModuleSummaryResponse,
    RuntimeAutomationConfigRequest,
    RuntimeAutomationConfigResponse,
    RuntimeConfigResponse,
    RuntimeConfigValidationResponse,
    RuntimeDocumentParsingConfigRequest,
    RuntimeDocumentParsingConfigResponse,
    RuntimeKnowledgeGraphConfigRequest,
    RuntimeKnowledgeGraphConfigResponse,
    RuntimeStorageConfigRequest,
    RuntimeStorageConfigResponse,
    SecretFieldStateResponse,
)
from app.system_settings.service import (
    get_default_system_locale,
    get_system_language_name,
    normalize_system_locale,
    resolve_system_locale,
)

RUNTIME_STORAGE_CONFIG_KEY = "runtime_storage_config"
RUNTIME_KNOWLEDGE_GRAPH_CONFIG_KEY = "runtime_knowledge_graph_config"
RUNTIME_DOCUMENT_PARSING_CONFIG_KEY = "runtime_document_parsing_config"
RUNTIME_AUTOMATION_CONFIG_KEY = "runtime_automation_config"
SYSTEM_INITIALIZATION_STATE_KEY = "system_initialization_state"

RUNTIME_SOURCE_APP = "app_config"
RUNTIME_SOURCE_ENV = "environment_default"
RUNTIME_SOURCE_DEFAULT = "default"

RUNTIME_CAPABILITY_ERROR_STORAGE_NOT_CONFIGURED = 40981
RUNTIME_CAPABILITY_ERROR_DOCUMENT_PARSING_NOT_CONFIGURED = 40982
RUNTIME_CAPABILITY_ERROR_KNOWLEDGE_GRAPH_NOT_ENABLED = 40983
RUNTIME_CAPABILITY_ERROR_KNOWLEDGE_GRAPH_NOT_CONFIGURED = 40984


@dataclass(frozen=True)
class ResolvedStorageRuntimeConfig:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    secure: bool
    max_file_size_mb: int
    max_pdf_pages: int
    configured: bool
    source: str


@dataclass(frozen=True)
class ResolvedKnowledgeGraphRuntimeConfig:
    enabled: bool
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    workspace: str
    graph_storage: str
    summary_language: str
    llm_model_id: Any | None
    llm_model_name: str | None
    embedding_model_id: Any | None
    embedding_model_name: str | None
    embedding_host: str
    embedding_dim: int
    embedding_api_key: str
    rerank_model: str
    rerank_host: str
    rerank_api_key: str
    rerank_request_format: str
    configured: bool
    source: str


@dataclass(frozen=True)
class ResolvedDocumentParsingRuntimeConfig:
    worker_enabled: bool
    ocr_enabled: bool
    ocr_langs: str
    picture_description_enabled: bool
    picture_description_url: str
    picture_description_api_key: str
    picture_description_model: str
    picture_description_prompt: str
    picture_description_timeout_sec: float
    picture_description_params_json: str
    max_file_size_mb: int
    max_pdf_pages: int
    configured: bool
    source: str


@dataclass(frozen=True)
class ResolvedAutomationRuntimeConfig:
    scheduler_enabled: bool
    configured: bool
    source: str


def clear_runtime_config_caches() -> None:
    resolve_runtime_storage_config.cache_clear()
    resolve_runtime_knowledge_graph_config.cache_clear()
    resolve_runtime_document_parsing_config.cache_clear()
    resolve_runtime_automation_config.cache_clear()

    try:
        from app.common.storage import get_minio_client

        get_minio_client.cache_clear()
    except Exception:
        pass

    try:
        from app.lightrag.clients.neo4j import get_neo4j_driver

        get_neo4j_driver.cache_clear()
    except Exception:
        pass

    try:
        from app.lightrag.manager import reset_lightrag_singletons_for_tests

        reset_lightrag_singletons_for_tests()
    except Exception:
        pass

    try:
        from app.lightrag.service import reset_lightrag_query_state_for_tests

        reset_lightrag_query_state_for_tests()
    except Exception:
        pass


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def normalize_runtime_group_key(group_key: str) -> str:
    normalized = (group_key or "").strip().replace("-", "_")
    if normalized in {"knowledgeGraph", "knowledgegraph"}:
        return "knowledge_graph"
    if normalized in {"documentParsing", "documentparsing"}:
        return "document_parsing"
    if normalized in {"storage", "knowledge_graph", "document_parsing", "automation"}:
        return normalized
    return normalized


def _module_titles(locale: str) -> dict[str, dict[str, str]]:
    if normalize_system_locale(locale) == "en":
        return {
            "storage": {
                "title": "Storage & Attachments",
                "description": "Configure MinIO-backed attachment storage and upload limits.",
            },
            "knowledge_graph": {
                "title": "Knowledge Graph",
                "description": "Connect Neo4j and LightRAG so graph retrieval and graph visualization can run.",
            },
            "document_parsing": {
                "title": "Document Parsing",
                "description": "Enable Docling parsing, OCR, and optional image description for attachments.",
            },
            "automation": {
                "title": "Automation",
                "description": "Enable the background scheduler used by weekly and monthly AI jobs.",
            },
        }
    return {
        "storage": {
            "title": "存储与附件",
            "description": "配置 MinIO 附件存储与上传限制。",
        },
        "knowledge_graph": {
            "title": "知识图谱",
            "description": "配置 Neo4j 与 LightRAG，让图谱检索和图谱视图可以运行。",
        },
        "document_parsing": {
            "title": "文档解析",
            "description": "配置 Docling、OCR 与可选的图片描述能力。",
        },
        "automation": {
            "title": "自动化任务",
            "description": "配置后台调度器，支撑周报、月报等系统 AI 任务。",
        },
    }


@contextmanager
def _runtime_db_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SystemRuntimeConfigService:
    def __init__(self, db: Session):
        self.db = db

    def _get_setting(self, key: str) -> AppSetting | None:
        try:
            return self.db.query(AppSetting).filter(AppSetting.key == key).first()
        except SQLAlchemyError:
            return None

    def _get_setting_payload(self, key: str) -> tuple[dict[str, Any], bool]:
        setting = self._get_setting(key)
        if setting is None or not isinstance(setting.value_json, dict):
            return {}, False
        return dict(setting.value_json), True

    def _initialization_state_payload(self) -> dict[str, Any] | None:
        setting = self._get_setting(SYSTEM_INITIALIZATION_STATE_KEY)
        if setting is None or not isinstance(setting.value_json, dict):
            return None
        return dict(setting.value_json)

    def _is_system_initialized(self) -> bool:
        payload = self._initialization_state_payload()
        return bool(payload and payload.get("initialized") is True)

    def _upsert_setting_payload(self, key: str, payload: dict[str, Any] | None) -> None:
        existing = self._get_setting(key)
        normalized_payload = payload or {}
        if not normalized_payload:
            if existing is not None:
                self.db.delete(existing)
                self.db.flush()
            return

        if existing is None:
            existing = AppSetting(key=key, value_json=normalized_payload)
            self.db.add(existing)
        else:
            existing.value_json = normalized_payload
        self.db.flush()

    def _finalize_runtime_update(self, *, commit: bool, sync_scheduler: bool = False) -> None:
        if not commit:
            return
        self.db.commit()
        clear_runtime_config_caches()
        if sync_scheduler:
            from app.scheduler import sync_scheduler as _sync_scheduler

            _sync_scheduler()

    def _apply_text_payload_updates(
        self,
        payload: dict[str, Any],
        request: Any,
        mapping: dict[str, str],
    ) -> None:
        for payload_key, request_field in mapping.items():
            if request_field not in request.model_fields_set:
                continue
            normalized = _normalize_optional_text(getattr(request, request_field))
            if normalized is None:
                payload.pop(payload_key, None)
            else:
                payload[payload_key] = normalized

    def _apply_secret_payload_update(
        self,
        payload: dict[str, Any],
        request: Any,
        *,
        request_field: str,
        encrypted_key: str,
        hint_key: str,
    ) -> None:
        if request_field not in request.model_fields_set:
            return

        normalized = _normalize_optional_text(getattr(request, request_field))
        if normalized is None:
            payload.pop(encrypted_key, None)
            payload.pop(hint_key, None)
            return

        payload[encrypted_key] = encrypt_api_key(normalized)
        payload[hint_key] = api_key_hint(normalized)

    def _assert_locked_field_change_allowed(
        self,
        *,
        field_name: str,
        current_value: str | None,
        requested_value: str | None,
    ) -> None:
        normalized_current = _normalize_optional_text(current_value)
        normalized_requested = _normalize_optional_text(requested_value)
        if normalized_current is None:
            return
        if normalized_requested == normalized_current:
            return
        raise ApiException(
            status_code=409,
            code=40985,
            message=f"{field_name} is locked after initialization and cannot be changed",
        )

    def _assert_fully_locked_text_field(
        self,
        *,
        field_name: str,
        current_value: str | None,
        requested_value: str | None,
    ) -> None:
        normalized_current = _normalize_optional_text(current_value)
        normalized_requested = _normalize_optional_text(requested_value)
        if normalized_requested == normalized_current:
            return
        raise ApiException(
            status_code=409,
            code=40985,
            message=f"{field_name} is locked after initialization and cannot be changed",
        )

    def _assert_fully_locked_boolean_field(
        self,
        *,
        field_name: str,
        current_value: bool,
        requested_value: bool | None,
    ) -> None:
        if requested_value is None:
            return
        if bool(requested_value) == bool(current_value):
            return
        raise ApiException(
            status_code=409,
            code=40985,
            message=f"{field_name} is locked after initialization and cannot be changed",
        )

    def _assert_fully_locked_int_field(
        self,
        *,
        field_name: str,
        current_value: int,
        requested_value: int | None,
    ) -> None:
        if requested_value is None:
            return
        if int(requested_value) == int(current_value):
            return
        raise ApiException(
            status_code=409,
            code=40985,
            message=f"{field_name} is locked after initialization and cannot be changed",
        )

    def _assert_locked_embedding_selection_allowed(
        self,
        request: RuntimeKnowledgeGraphConfigRequest,
        current: RuntimeKnowledgeGraphConfigResponse,
    ) -> None:
        current_model_id = str(current.embedding_model_id) if current.embedding_model_id else None
        current_model_name = _normalize_optional_text(current.embedding_model_name)

        if "embedding_model_id" in request.model_fields_set:
            requested_model_id = str(request.embedding_model_id) if request.embedding_model_id else None
            if requested_model_id != current_model_id:
                raise ApiException(
                    status_code=409,
                    code=40986,
                    message="embeddingModelId is locked after initialization and cannot be changed",
                )

        if "embedding_model_name" in request.model_fields_set:
            requested_model_name = _normalize_optional_text(request.embedding_model_name)
            if requested_model_name != current_model_name:
                raise ApiException(
                    status_code=409,
                    code=40987,
                    message="embeddingModelName is locked after initialization and cannot be changed",
                )

    def _assert_initialized_knowledge_graph_lock(
        self,
        request: RuntimeKnowledgeGraphConfigRequest,
    ) -> None:
        if not self._is_system_initialized():
            return

        resolved_current, current = self._resolve_knowledge_graph_internal()

        self._assert_fully_locked_boolean_field(
            field_name="enabled",
            current_value=current.enabled,
            requested_value=request.enabled,
        )

        if "neo4j_uri" in request.model_fields_set:
            self._assert_fully_locked_text_field(
                field_name="neo4jUri",
                current_value=current.neo4j_uri,
                requested_value=request.neo4j_uri,
            )

        if "neo4j_user" in request.model_fields_set:
            self._assert_fully_locked_text_field(
                field_name="neo4jUser",
                current_value=current.neo4j_user,
                requested_value=request.neo4j_user,
            )

        if "neo4j_password" in request.model_fields_set:
            self._assert_fully_locked_text_field(
                field_name="neo4jPassword",
                current_value=resolved_current.neo4j_password,
                requested_value=request.neo4j_password,
            )

        if "neo4j_database" in request.model_fields_set:
            self._assert_fully_locked_text_field(
                field_name="neo4jDatabase",
                current_value=current.neo4j_database,
                requested_value=request.neo4j_database,
            )

        if "graph_storage" in request.model_fields_set:
            self._assert_fully_locked_text_field(
                field_name="graphStorage",
                current_value=current.graph_storage,
                requested_value=request.graph_storage,
            )

        if "summary_language" in request.model_fields_set:
            self._assert_locked_field_change_allowed(
                field_name="summaryLanguage",
                current_value=current.summary_language,
                requested_value=request.summary_language,
            )

        if "embedding_host" in request.model_fields_set:
            self._assert_locked_field_change_allowed(
                field_name="embeddingHost",
                current_value=resolved_current.embedding_host,
                requested_value=request.embedding_host,
            )

        if "embedding_dim" in request.model_fields_set:
            self._assert_fully_locked_int_field(
                field_name="embeddingDim",
                current_value=resolved_current.embedding_dim,
                requested_value=request.embedding_dim,
            )

        self._assert_locked_embedding_selection_allowed(request, current)

    def _assert_initialized_document_parsing_lock(
        self,
        request: RuntimeDocumentParsingConfigRequest,
    ) -> None:
        if not self._is_system_initialized():
            return

        _resolved_current, current = self._resolve_document_parsing_internal()
        self._assert_fully_locked_boolean_field(
            field_name="workerEnabled",
            current_value=current.worker_enabled,
            requested_value=request.worker_enabled,
        )

    def _secret_state(
        self,
        *,
        payload: dict[str, Any],
        encrypted_key: str,
        hint_key: str,
        env_value: str | None,
    ) -> tuple[str, SecretFieldStateResponse]:
        encrypted = _normalize_optional_text(payload.get(encrypted_key))
        if encrypted:
            try:
                return decrypt_api_key(encrypted), SecretFieldStateResponse(
                    configured=True,
                    hint=_normalize_optional_text(payload.get(hint_key)) or None,
                )
            except Exception:
                pass

        env_secret = _normalize_optional_text(env_value) or ""
        if env_secret:
            return env_secret, SecretFieldStateResponse(configured=True, hint=api_key_hint(env_secret))
        return "", SecretFieldStateResponse(configured=False, hint=None)

    def _resolve_storage_internal(self) -> tuple[ResolvedStorageRuntimeConfig, RuntimeStorageConfigResponse]:
        settings = get_settings()
        payload, has_app_payload = self._get_setting_payload(RUNTIME_STORAGE_CONFIG_KEY)

        endpoint = _normalize_optional_text(payload.get("endpoint")) or _normalize_optional_text(settings.minio_endpoint) or ""
        access_key, access_key_state = self._secret_state(
            payload=payload,
            encrypted_key="accessKeyEncrypted",
            hint_key="accessKeyHint",
            env_value=settings.minio_access_key,
        )
        secret_key, secret_key_state = self._secret_state(
            payload=payload,
            encrypted_key="secretKeyEncrypted",
            hint_key="secretKeyHint",
            env_value=settings.minio_secret_key,
        )
        bucket = _normalize_optional_text(payload.get("bucket")) or _normalize_optional_text(settings.minio_bucket) or ""
        secure = bool(payload["secure"]) if "secure" in payload else bool(settings.minio_secure)
        max_file_size_mb = int(payload.get("maxFileSizeMb") or settings.docling_max_file_size_mb or 100)
        max_pdf_pages = int(payload.get("maxPdfPages") or settings.docling_max_pdf_pages or 500)

        configured = bool(endpoint and access_key and secret_key and bucket)
        source = RUNTIME_SOURCE_APP if has_app_payload else (RUNTIME_SOURCE_ENV if configured else RUNTIME_SOURCE_DEFAULT)
        summary = f"MinIO · {bucket}" if configured else ("MinIO · incomplete" if source == RUNTIME_SOURCE_APP else "Not configured")

        resolved = ResolvedStorageRuntimeConfig(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            secure=secure,
            max_file_size_mb=max_file_size_mb,
            max_pdf_pages=max_pdf_pages,
            configured=configured,
            source=source,
        )
        response = RuntimeStorageConfigResponse(
            group_key="storage",
            configured=configured,
            source=source,
            restart_required=False,
            has_secrets=access_key_state.configured or secret_key_state.configured,
            effective_summary=summary,
            endpoint=endpoint,
            bucket=bucket,
            secure=secure,
            max_file_size_mb=max_file_size_mb,
            max_pdf_pages=max_pdf_pages,
            access_key_state=access_key_state,
            secret_key_state=secret_key_state,
        )
        return resolved, response

    def _empty_binding(self, component: str) -> AiComponentBinding:
        return AiComponentBinding(component=component, llm_model_id=None, embedding_model_id=None)

    def _get_binding(
        self,
        component: str,
        *,
        persist_if_missing: bool = True,
        allow_missing_table: bool = False,
    ) -> AiComponentBinding:
        try:
            row = (
                self.db.query(AiComponentBinding)
                .filter(AiComponentBinding.component == component)
                .first()
            )
        except SQLAlchemyError:
            if allow_missing_table:
                self.db.rollback()
                return self._empty_binding(component)
            raise

        if row is None:
            if not persist_if_missing:
                return self._empty_binding(component)
            row = AiComponentBinding(component=component, llm_model_id=None, embedding_model_id=None)
            self.db.add(row)
            self.db.flush()
        return row

    def _find_model(self, model_id: Any | None, *, allow_missing_table: bool = False) -> AiModel | None:
        if not model_id:
            return None
        try:
            return self.db.query(AiModel).filter(AiModel.id == model_id).first()
        except SQLAlchemyError:
            if allow_missing_table:
                self.db.rollback()
                return None
            raise

    def _resolve_or_create_model(self, *, model_type: str, model_name: str) -> AiModel:
        normalized_name = _normalize_optional_text(model_name)
        if not normalized_name:
            raise ApiException(status_code=400, code=40081, message=f"{model_type} model name is required")

        current_binding = self._get_binding("lightrag")
        current_model = self._find_model(
            current_binding.embedding_model_id if model_type == "embedding" else current_binding.llm_model_id
        )
        assistant_binding = self._get_binding("assistant")
        assistant_model = self._find_model(assistant_binding.llm_model_id)

        credential_id = None
        if current_model is not None:
            credential_id = current_model.credential_id
        elif assistant_model is not None:
            credential_id = assistant_model.credential_id
        else:
            latest_credential = (
                self.db.query(AiCredential)
                .order_by(AiCredential.created_at.desc())
                .first()
            )
            credential_id = latest_credential.id if latest_credential is not None else None

        if credential_id is None:
            raise ApiException(status_code=400, code=40082, message="No AI credential is available for knowledge graph model binding")

        existing = (
            self.db.query(AiModel)
            .filter(
                AiModel.credential_id == credential_id,
                AiModel.name == normalized_name,
                AiModel.model_type == model_type,
            )
            .first()
        )
        if existing is not None:
            return existing

        model = AiModel(
            credential_id=credential_id,
            name=normalized_name,
            model_type=model_type,
        )
        self.db.add(model)
        self.db.flush()
        return model

    def _apply_knowledge_graph_model_selection(
        self,
        request: RuntimeKnowledgeGraphConfigRequest,
        *,
        commit: bool,
    ) -> None:
        binding = self._get_binding("lightrag")

        if request.llm_model_id is not None:
            binding.llm_model_id = request.llm_model_id
        elif request.llm_model_name is not None:
            binding.llm_model_id = self._resolve_or_create_model(
                model_type="llm",
                model_name=request.llm_model_name,
            ).id

        if "embedding_model_id" in request.model_fields_set:
            binding.embedding_model_id = request.embedding_model_id
        elif "embedding_model_name" in request.model_fields_set:
            # Embedding model selection is runtime-config owned so it can point to
            # a provider different from the main AI credential.
            binding.embedding_model_id = None

        self.db.flush()
        if commit:
            self.db.commit()

    def _resolve_knowledge_graph_internal(self) -> tuple[ResolvedKnowledgeGraphRuntimeConfig, RuntimeKnowledgeGraphConfigResponse]:
        settings = get_settings()
        payload, has_app_payload = self._get_setting_payload(RUNTIME_KNOWLEDGE_GRAPH_CONFIG_KEY)
        binding = self._get_binding(
            "lightrag",
            persist_if_missing=False,
            allow_missing_table=True,
        )
        llm_model = self._find_model(binding.llm_model_id, allow_missing_table=True)
        embedding_model = self._find_model(binding.embedding_model_id, allow_missing_table=True)

        enabled = bool(payload["enabled"]) if "enabled" in payload else bool(settings.lightrag_enabled)
        neo4j_uri = _normalize_optional_text(payload.get("neo4jUri")) or _normalize_optional_text(settings.neo4j_uri) or ""
        neo4j_user = _normalize_optional_text(payload.get("neo4jUser")) or _normalize_optional_text(settings.neo4j_user) or ""
        neo4j_password, neo4j_password_state = self._secret_state(
            payload=payload,
            encrypted_key="neo4jPasswordEncrypted",
            hint_key="neo4jPasswordHint",
            env_value=settings.neo4j_password,
        )
        neo4j_database = _normalize_optional_text(payload.get("neo4jDatabase")) or _normalize_optional_text(settings.neo4j_database) or ""
        workspace = _normalize_optional_text(payload.get("workspace")) or _normalize_optional_text(settings.lightrag_workspace) or ""
        graph_storage = _normalize_optional_text(payload.get("graphStorage")) or _normalize_optional_text(settings.lightrag_graph_storage) or "Neo4JStorage"
        summary_language = (
            _normalize_optional_text(payload.get("summaryLanguage"))
            or _normalize_optional_text(settings.lightrag_summary_language)
            or get_system_language_name(resolve_system_locale(self.db))
        )
        embedding_host = _normalize_optional_text(payload.get("embeddingHost")) or _normalize_optional_text(settings.lightrag_embedding_host) or ""
        payload_embedding_model_name = _normalize_optional_text(payload.get("embeddingModelName"))
        resolved_embedding_model_name = payload_embedding_model_name or (embedding_model.name if embedding_model is not None else None) or _normalize_optional_text(settings.lightrag_embedding_model)
        resolved_embedding_model_id = None if payload_embedding_model_name else (embedding_model.id if embedding_model is not None else None)
        embedding_dim = int(payload.get("embeddingDim") or settings.lightrag_embedding_dim or 1536)
        embedding_api_key, embedding_api_key_state = self._secret_state(
            payload=payload,
            encrypted_key="embeddingApiKeyEncrypted",
            hint_key="embeddingApiKeyHint",
            env_value=settings.lightrag_embedding_key,
        )
        rerank_model = _normalize_optional_text(payload.get("rerankModel")) or _normalize_optional_text(settings.lightrag_rerank_model) or ""
        rerank_host = _normalize_optional_text(payload.get("rerankHost")) or _normalize_optional_text(settings.lightrag_rerank_host) or ""
        rerank_api_key, rerank_api_key_state = self._secret_state(
            payload=payload,
            encrypted_key="rerankApiKeyEncrypted",
            hint_key="rerankApiKeyHint",
            env_value=settings.lightrag_rerank_key,
        )
        rerank_request_format = (
            _normalize_optional_text(payload.get("rerankRequestFormat"))
            or _normalize_optional_text(settings.lightrag_rerank_request_format)
            or "standard"
        )

        configured = bool(
            enabled
            and neo4j_uri
            and neo4j_user
            and neo4j_password
            and neo4j_database
            and llm_model is not None
            and resolved_embedding_model_name
        )
        source = RUNTIME_SOURCE_APP if has_app_payload else (RUNTIME_SOURCE_ENV if bool(settings.lightrag_enabled) else RUNTIME_SOURCE_DEFAULT)
        if not enabled:
            summary = "Disabled"
        elif configured:
            summary = f"{graph_storage} · {neo4j_database}"
        else:
            summary = "Configuration incomplete"

        resolved = ResolvedKnowledgeGraphRuntimeConfig(
            enabled=enabled,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_password,
            neo4j_database=neo4j_database,
            workspace=workspace,
            graph_storage=graph_storage,
            summary_language=summary_language,
            llm_model_id=llm_model.id if llm_model is not None else None,
            llm_model_name=llm_model.name if llm_model is not None else None,
            embedding_model_id=resolved_embedding_model_id,
            embedding_model_name=resolved_embedding_model_name,
            embedding_host=embedding_host,
            embedding_dim=embedding_dim,
            embedding_api_key=embedding_api_key,
            rerank_model=rerank_model,
            rerank_host=rerank_host,
            rerank_api_key=rerank_api_key,
            rerank_request_format=rerank_request_format,
            configured=configured,
            source=source,
        )
        response = RuntimeKnowledgeGraphConfigResponse(
            group_key="knowledge_graph",
            configured=configured,
            source=source,
            restart_required=False,
            has_secrets=neo4j_password_state.configured or embedding_api_key_state.configured or rerank_api_key_state.configured,
            effective_summary=summary,
            enabled=enabled,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_database=neo4j_database,
            workspace=workspace,
            graph_storage=graph_storage,
            summary_language=summary_language,
            llm_model_id=resolved.llm_model_id,
            llm_model_name=resolved.llm_model_name,
            embedding_model_id=resolved.embedding_model_id,
            embedding_model_name=resolved.embedding_model_name,
            embedding_host=embedding_host,
            embedding_dim=embedding_dim,
            rerank_model=rerank_model,
            rerank_host=rerank_host,
            rerank_request_format=rerank_request_format,
            neo4j_password_state=neo4j_password_state,
            embedding_api_key_state=embedding_api_key_state,
            rerank_api_key_state=rerank_api_key_state,
        )
        return resolved, response

    def _resolve_document_parsing_internal(self) -> tuple[ResolvedDocumentParsingRuntimeConfig, RuntimeDocumentParsingConfigResponse]:
        settings = get_settings()
        payload, has_app_payload = self._get_setting_payload(RUNTIME_DOCUMENT_PARSING_CONFIG_KEY)

        worker_enabled = bool(payload["workerEnabled"]) if "workerEnabled" in payload else bool(settings.docling_worker_enabled)
        ocr_enabled = bool(payload["ocrEnabled"]) if "ocrEnabled" in payload else bool(settings.docling_ocr_enabled)
        ocr_langs = _normalize_optional_text(payload.get("ocrLangs")) or _normalize_optional_text(settings.docling_ocr_langs) or "auto"
        picture_description_enabled = (
            bool(payload["pictureDescriptionEnabled"])
            if "pictureDescriptionEnabled" in payload
            else bool(settings.docling_picture_description_enabled)
        )
        picture_description_url = (
            _normalize_optional_text(payload.get("pictureDescriptionUrl"))
            or _normalize_optional_text(settings.docling_picture_description_url)
            or ""
        )
        picture_description_api_key, picture_description_api_key_state = self._secret_state(
            payload=payload,
            encrypted_key="pictureDescriptionApiKeyEncrypted",
            hint_key="pictureDescriptionApiKeyHint",
            env_value=settings.docling_picture_description_api_key,
        )
        picture_description_model = (
            _normalize_optional_text(payload.get("pictureDescriptionModel"))
            or _normalize_optional_text(settings.docling_picture_description_model)
            or ""
        )
        picture_description_prompt = (
            _normalize_optional_text(payload.get("pictureDescriptionPrompt"))
            or _normalize_optional_text(settings.docling_picture_description_prompt)
            or ""
        )
        picture_description_timeout_sec = float(
            payload.get("pictureDescriptionTimeoutSec")
            or settings.docling_picture_description_timeout_sec
            or 60.0
        )
        picture_description_params_json = (
            _normalize_optional_text(payload.get("pictureDescriptionParamsJson"))
            or _normalize_optional_text(settings.docling_picture_description_params_json)
            or ""
        )
        max_file_size_mb = int(payload.get("maxFileSizeMb") or settings.docling_max_file_size_mb or 100)
        max_pdf_pages = int(payload.get("maxPdfPages") or settings.docling_max_pdf_pages or 500)

        configured = bool(worker_enabled)
        source = RUNTIME_SOURCE_APP if has_app_payload else (RUNTIME_SOURCE_ENV if worker_enabled or picture_description_enabled else RUNTIME_SOURCE_DEFAULT)
        if worker_enabled:
            summary = "Docling worker enabled"
        elif picture_description_enabled:
            summary = "Picture description only"
        else:
            summary = "Disabled"

        resolved = ResolvedDocumentParsingRuntimeConfig(
            worker_enabled=worker_enabled,
            ocr_enabled=ocr_enabled,
            ocr_langs=ocr_langs,
            picture_description_enabled=picture_description_enabled,
            picture_description_url=picture_description_url,
            picture_description_api_key=picture_description_api_key,
            picture_description_model=picture_description_model,
            picture_description_prompt=picture_description_prompt,
            picture_description_timeout_sec=picture_description_timeout_sec,
            picture_description_params_json=picture_description_params_json,
            max_file_size_mb=max_file_size_mb,
            max_pdf_pages=max_pdf_pages,
            configured=configured,
            source=source,
        )
        response = RuntimeDocumentParsingConfigResponse(
            group_key="document_parsing",
            configured=configured,
            source=source,
            restart_required=True,
            has_secrets=picture_description_api_key_state.configured,
            effective_summary=summary,
            worker_enabled=worker_enabled,
            ocr_enabled=ocr_enabled,
            ocr_langs=ocr_langs,
            picture_description_enabled=picture_description_enabled,
            picture_description_url=picture_description_url,
            picture_description_model=picture_description_model,
            picture_description_prompt=picture_description_prompt,
            picture_description_timeout_sec=picture_description_timeout_sec,
            picture_description_params_json=picture_description_params_json,
            max_file_size_mb=max_file_size_mb,
            max_pdf_pages=max_pdf_pages,
            picture_description_api_key_state=picture_description_api_key_state,
        )
        return resolved, response

    def _resolve_automation_internal(
        self,
        locale: str | None = None,
    ) -> tuple[ResolvedAutomationRuntimeConfig, RuntimeAutomationConfigResponse]:
        settings = get_settings()
        payload, has_app_payload = self._get_setting_payload(RUNTIME_AUTOMATION_CONFIG_KEY)
        scheduler_enabled = bool(payload["schedulerEnabled"]) if "schedulerEnabled" in payload else bool(settings.scheduler_enabled)
        configured = bool(has_app_payload or scheduler_enabled)
        source = RUNTIME_SOURCE_APP if has_app_payload else (RUNTIME_SOURCE_ENV if scheduler_enabled else RUNTIME_SOURCE_DEFAULT)
        if locale is not None:
            resolved_locale = normalize_system_locale(locale) or get_default_system_locale()
        else:
            try:
                resolved_locale = resolve_system_locale(self.db)
            except Exception:
                resolved_locale = get_default_system_locale()
        if resolved_locale == "en":
            summary = "Scheduler enabled" if scheduler_enabled else "Disabled"
        else:
            summary = "已启用后台调度器" if scheduler_enabled else "未启用"
        resolved = ResolvedAutomationRuntimeConfig(
            scheduler_enabled=scheduler_enabled,
            configured=configured,
            source=source,
        )
        response = RuntimeAutomationConfigResponse(
            group_key="automation",
            configured=configured,
            source=source,
            restart_required=False,
            has_secrets=False,
            effective_summary=summary,
            scheduler_enabled=scheduler_enabled,
        )
        return resolved, response

    def get_runtime_config_response(self, *, locale: str | None = None) -> RuntimeConfigResponse:
        _resolved_storage, storage = self._resolve_storage_internal()
        _resolved_kg, knowledge_graph = self._resolve_knowledge_graph_internal()
        _resolved_doc, document_parsing = self._resolve_document_parsing_internal()
        _resolved_automation, automation = self._resolve_automation_internal(locale=locale)
        return RuntimeConfigResponse(
            storage=storage,
            knowledge_graph=knowledge_graph,
            document_parsing=document_parsing,
            automation=automation,
        )

    def list_capability_module_summaries(self, *, locale: str | None = None) -> list[CapabilityModuleSummaryResponse]:
        localized = _module_titles(locale or "zh")
        runtime_config = self.get_runtime_config_response(locale=locale)
        modules = [
            runtime_config.storage,
            runtime_config.knowledge_graph,
            runtime_config.document_parsing,
            runtime_config.automation,
        ]
        return [
            CapabilityModuleSummaryResponse(
                group_key=module.group_key,
                configured=module.configured,
                source=module.source,
                restart_required=module.restart_required,
                has_secrets=module.has_secrets,
                effective_summary=module.effective_summary,
                title=localized[module.group_key]["title"],
                description=localized[module.group_key]["description"],
                allow_skip=True,
            )
            for module in modules
        ]

    def update_storage_config(self, request: RuntimeStorageConfigRequest, *, commit: bool = True) -> RuntimeStorageConfigResponse:
        payload, _ = self._get_setting_payload(RUNTIME_STORAGE_CONFIG_KEY)

        self._apply_text_payload_updates(
            payload,
            request,
            {
                "endpoint": "endpoint",
                "bucket": "bucket",
            },
        )
        self._apply_secret_payload_update(
            payload,
            request,
            request_field="access_key",
            encrypted_key="accessKeyEncrypted",
            hint_key="accessKeyHint",
        )
        self._apply_secret_payload_update(
            payload,
            request,
            request_field="secret_key",
            encrypted_key="secretKeyEncrypted",
            hint_key="secretKeyHint",
        )

        if request.secure is not None:
            payload["secure"] = bool(request.secure)
        if request.max_file_size_mb is not None:
            payload["maxFileSizeMb"] = int(request.max_file_size_mb)
        if request.max_pdf_pages is not None:
            payload["maxPdfPages"] = int(request.max_pdf_pages)

        self._upsert_setting_payload(RUNTIME_STORAGE_CONFIG_KEY, payload)
        self._finalize_runtime_update(commit=commit)
        response = self.get_runtime_config_response().storage
        return response

    def update_knowledge_graph_config(
        self,
        request: RuntimeKnowledgeGraphConfigRequest,
        *,
        commit: bool = True,
    ) -> RuntimeKnowledgeGraphConfigResponse:
        payload, _ = self._get_setting_payload(RUNTIME_KNOWLEDGE_GRAPH_CONFIG_KEY)
        self._assert_initialized_knowledge_graph_lock(request)

        self._apply_text_payload_updates(
            payload,
            request,
            {
                "neo4jUri": "neo4j_uri",
                "neo4jUser": "neo4j_user",
                "neo4jDatabase": "neo4j_database",
                "workspace": "workspace",
                "graphStorage": "graph_storage",
                "summaryLanguage": "summary_language",
                "embeddingModelName": "embedding_model_name",
                "embeddingHost": "embedding_host",
                "rerankModel": "rerank_model",
                "rerankHost": "rerank_host",
                "rerankRequestFormat": "rerank_request_format",
            },
        )

        if request.enabled is not None:
            payload["enabled"] = bool(request.enabled)
        if request.embedding_dim is not None:
            payload["embeddingDim"] = int(request.embedding_dim)

        self._apply_secret_payload_update(
            payload,
            request,
            request_field="neo4j_password",
            encrypted_key="neo4jPasswordEncrypted",
            hint_key="neo4jPasswordHint",
        )
        self._apply_secret_payload_update(
            payload,
            request,
            request_field="embedding_api_key",
            encrypted_key="embeddingApiKeyEncrypted",
            hint_key="embeddingApiKeyHint",
        )
        self._apply_secret_payload_update(
            payload,
            request,
            request_field="rerank_api_key",
            encrypted_key="rerankApiKeyEncrypted",
            hint_key="rerankApiKeyHint",
        )

        self._upsert_setting_payload(RUNTIME_KNOWLEDGE_GRAPH_CONFIG_KEY, payload)
        self._apply_knowledge_graph_model_selection(
            request,
            commit=False,
        )
        self._finalize_runtime_update(commit=commit)
        return self.get_runtime_config_response().knowledge_graph

    def update_document_parsing_config(self, request: RuntimeDocumentParsingConfigRequest, *, commit: bool = True) -> RuntimeDocumentParsingConfigResponse:
        payload, _ = self._get_setting_payload(RUNTIME_DOCUMENT_PARSING_CONFIG_KEY)
        self._assert_initialized_document_parsing_lock(request)

        self._apply_text_payload_updates(
            payload,
            request,
            {
                "ocrLangs": "ocr_langs",
                "pictureDescriptionUrl": "picture_description_url",
                "pictureDescriptionModel": "picture_description_model",
                "pictureDescriptionPrompt": "picture_description_prompt",
                "pictureDescriptionParamsJson": "picture_description_params_json",
            },
        )

        bool_mapping = {
            "workerEnabled": request.worker_enabled,
            "ocrEnabled": request.ocr_enabled,
            "pictureDescriptionEnabled": request.picture_description_enabled,
        }
        for key, value in bool_mapping.items():
            if value is not None:
                payload[key] = bool(value)

        if request.picture_description_timeout_sec is not None:
            payload["pictureDescriptionTimeoutSec"] = float(request.picture_description_timeout_sec)
        if request.max_file_size_mb is not None:
            payload["maxFileSizeMb"] = int(request.max_file_size_mb)
        if request.max_pdf_pages is not None:
            payload["maxPdfPages"] = int(request.max_pdf_pages)
        self._apply_secret_payload_update(
            payload,
            request,
            request_field="picture_description_api_key",
            encrypted_key="pictureDescriptionApiKeyEncrypted",
            hint_key="pictureDescriptionApiKeyHint",
        )

        self._upsert_setting_payload(RUNTIME_DOCUMENT_PARSING_CONFIG_KEY, payload)
        self._finalize_runtime_update(commit=commit)
        return self.get_runtime_config_response().document_parsing

    def update_automation_config(self, request: RuntimeAutomationConfigRequest, *, commit: bool = True) -> RuntimeAutomationConfigResponse:
        payload, _ = self._get_setting_payload(RUNTIME_AUTOMATION_CONFIG_KEY)
        if request.scheduler_enabled is not None:
            payload["schedulerEnabled"] = bool(request.scheduler_enabled)
        self._upsert_setting_payload(RUNTIME_AUTOMATION_CONFIG_KEY, payload)
        self._finalize_runtime_update(commit=commit, sync_scheduler=True)
        return self.get_runtime_config_response().automation

    def update_group(self, group_key: str, request_data: dict[str, Any]) -> RuntimeConfigResponse:
        normalized_group_key = normalize_runtime_group_key(group_key)
        if normalized_group_key == "storage":
            self.update_storage_config(RuntimeStorageConfigRequest.model_validate(request_data))
        elif normalized_group_key == "knowledge_graph":
            self.update_knowledge_graph_config(RuntimeKnowledgeGraphConfigRequest.model_validate(request_data))
        elif normalized_group_key == "document_parsing":
            self.update_document_parsing_config(RuntimeDocumentParsingConfigRequest.model_validate(request_data))
        elif normalized_group_key == "automation":
            self.update_automation_config(RuntimeAutomationConfigRequest.model_validate(request_data))
        else:
            raise ApiException(status_code=404, code=40471, message=f"Unknown runtime config group: {group_key}")
        return self.get_runtime_config_response()

    def validate_group(self, group_key: str, request_data: dict[str, Any]) -> RuntimeConfigValidationResponse:
        normalized_group_key = normalize_runtime_group_key(group_key)
        if normalized_group_key == "storage":
            return self.validate_storage_config(RuntimeStorageConfigRequest.model_validate(request_data))
        if normalized_group_key == "knowledge_graph":
            return self.validate_knowledge_graph_config(RuntimeKnowledgeGraphConfigRequest.model_validate(request_data))
        raise ApiException(status_code=404, code=40471, message=f"Unknown runtime config group: {group_key}")

    def validate_storage_config(self, request: RuntimeStorageConfigRequest) -> RuntimeConfigValidationResponse:
        self.update_storage_config(request, commit=False)
        resolved, merged = self._resolve_storage_internal()
        self.db.rollback()
        field_errors: dict[str, str] = {}
        if not merged.endpoint:
            field_errors["endpoint"] = "Endpoint is required"
        if not merged.bucket:
            field_errors["bucket"] = "Bucket is required"
        if not merged.access_key_state.configured:
            field_errors["accessKey"] = "Access key is required"
        if not merged.secret_key_state.configured:
            field_errors["secretKey"] = "Secret key is required"
        if field_errors:
            return RuntimeConfigValidationResponse(ok=False, message="Storage configuration is incomplete", field_errors=field_errors)

        try:
            from minio import Minio
            from urllib.parse import urlparse

            endpoint = merged.endpoint.strip()
            secure = merged.secure
            if endpoint.startswith("http://") or endpoint.startswith("https://"):
                parsed = urlparse(endpoint)
                secure = parsed.scheme == "https"
                endpoint = (parsed.netloc or parsed.path).rstrip("/")

            client = Minio(
                endpoint,
                access_key=resolved.access_key,
                secret_key=resolved.secret_key,
                secure=secure,
            )
            client.bucket_exists(merged.bucket)
        except Exception as exc:
            return RuntimeConfigValidationResponse(ok=False, message=f"Failed to connect to storage: {exc}")

        return RuntimeConfigValidationResponse(ok=True, message="Storage configuration looks good")

    def validate_knowledge_graph_config(self, request: RuntimeKnowledgeGraphConfigRequest) -> RuntimeConfigValidationResponse:
        self.update_knowledge_graph_config(request, commit=False)
        resolved, merged = self._resolve_knowledge_graph_internal()
        self.db.rollback()
        if not merged.enabled:
            return RuntimeConfigValidationResponse(ok=True, message="Knowledge graph is currently disabled")

        field_errors: dict[str, str] = {}
        if not merged.neo4j_uri:
            field_errors["neo4jUri"] = "Neo4j URI is required"
        if not merged.neo4j_user:
            field_errors["neo4jUser"] = "Neo4j user is required"
        if not merged.neo4j_password_state.configured:
            field_errors["neo4jPassword"] = "Neo4j password is required"
        if not merged.neo4j_database:
            field_errors["neo4jDatabase"] = "Neo4j database is required"
        if not merged.llm_model_id and not merged.llm_model_name:
            field_errors["llmModelId"] = "LightRAG LLM binding is required"
        if not merged.embedding_model_id and not merged.embedding_model_name:
            field_errors["embeddingModelId"] = "LightRAG embedding binding is required"

        if field_errors:
            return RuntimeConfigValidationResponse(ok=False, message="Knowledge graph configuration is incomplete", field_errors=field_errors)

        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                merged.neo4j_uri,
                auth=(merged.neo4j_user, resolved.neo4j_password),
            )
            try:
                driver.verify_connectivity()
            finally:
                driver.close()
        except Exception as exc:
            return RuntimeConfigValidationResponse(ok=False, message=f"Failed to connect to Neo4j: {exc}")

        return RuntimeConfigValidationResponse(ok=True, message="Knowledge graph configuration looks good")


def _load_runtime_snapshot(loader_name: str):
    with _runtime_db_session() as db:
        service = SystemRuntimeConfigService(db)
        return getattr(service, loader_name)()[0]

@lru_cache(maxsize=1)
def resolve_runtime_storage_config() -> ResolvedStorageRuntimeConfig:
    return _load_runtime_snapshot("_resolve_storage_internal")


@lru_cache(maxsize=1)
def resolve_runtime_knowledge_graph_config() -> ResolvedKnowledgeGraphRuntimeConfig:
    return _load_runtime_snapshot("_resolve_knowledge_graph_internal")


@lru_cache(maxsize=1)
def resolve_runtime_document_parsing_config() -> ResolvedDocumentParsingRuntimeConfig:
    return _load_runtime_snapshot("_resolve_document_parsing_internal")


@lru_cache(maxsize=1)
def resolve_runtime_automation_config() -> ResolvedAutomationRuntimeConfig:
    return _load_runtime_snapshot("_resolve_automation_internal")


def ensure_runtime_storage_configured() -> ResolvedStorageRuntimeConfig:
    config = resolve_runtime_storage_config()
    if config.configured:
        return config
    raise ApiException(
        status_code=409,
        code=RUNTIME_CAPABILITY_ERROR_STORAGE_NOT_CONFIGURED,
        message="Object storage is not configured",
        details={"groupKey": "storage"},
    )


def ensure_runtime_document_parsing_configured() -> ResolvedDocumentParsingRuntimeConfig:
    config = resolve_runtime_document_parsing_config()
    if config.worker_enabled:
        return config
    raise ApiException(
        status_code=409,
        code=RUNTIME_CAPABILITY_ERROR_DOCUMENT_PARSING_NOT_CONFIGURED,
        message="Document parsing is not configured",
        details={"groupKey": "document_parsing"},
    )


def ensure_runtime_knowledge_graph_enabled(*, require_configured: bool = False) -> ResolvedKnowledgeGraphRuntimeConfig:
    config = resolve_runtime_knowledge_graph_config()
    if not config.enabled:
        raise ApiException(
            status_code=409,
            code=RUNTIME_CAPABILITY_ERROR_KNOWLEDGE_GRAPH_NOT_ENABLED,
            message="Knowledge graph is not enabled",
            details={"groupKey": "knowledge_graph"},
        )
    if require_configured and not config.configured:
        raise ApiException(
            status_code=409,
            code=RUNTIME_CAPABILITY_ERROR_KNOWLEDGE_GRAPH_NOT_CONFIGURED,
            message="Knowledge graph is not fully configured",
            details={"groupKey": "knowledge_graph"},
        )
    return config
