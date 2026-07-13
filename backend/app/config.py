from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(_BACKEND_ROOT / ".env"),
            str(_BACKEND_ROOT / ".env.local"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = Field(default="MindAtlas API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    app_default_locale: str = Field(default="zh", alias="APP_DEFAULT_LOCALE")
    # Immutable image/git revision in staging/production. Local/test may use "development".
    app_build_revision: str = Field(default="development", alias="APP_BUILD_REVISION")

    # Plan 02A temporary OpenClaw Capability Runtime mode selector.
    # Process/deployment switch only (get_settings is cached). Requires restart.
    # Accepts exactly "legacy" or "shared"; no aliases (new/auto/bool/empty).
    openclaw_capability_runtime_mode: Literal["legacy", "shared"] = Field(
        default="legacy",
        alias="OPENCLAW_CAPABILITY_RUNTIME_MODE",
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=False, alias="LOG_JSON")

    # API
    api_prefix: str = Field(default="/api", alias="API_PREFIX")

    # Database
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/mindatlas",
        alias="DATABASE_URL",
    )

    # CORS
    # Keep as string to support simple comma-separated values in `.env` without requiring JSON.
    cors_origins: str = Field(default="", alias="CORS_ORIGINS")

    # Uploads
    upload_dir: str = Field(default="../uploads", alias="UPLOAD_DIR")

    # MinIO (S3-compatible object storage)
    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="mindatlas", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    # AI (optional)
    ai_provider: str = Field(default="openai", alias="AI_PROVIDER")
    ai_api_key: str | None = Field(default=None, alias="AI_API_KEY")
    ai_base_url: str = Field(default="https://api.openai.com/v1", alias="AI_BASE_URL")
    ai_model: str = Field(default="gpt-3.5-turbo", alias="AI_MODEL")
    ai_provider_fernet_key: str = Field(default="", alias="AI_PROVIDER_FERNET_KEY")

    # LightRAG (optional, for knowledge graph indexing)
    lightrag_enabled: bool = Field(default=False, alias="LIGHTRAG_ENABLED")
    lightrag_worker_enabled: bool = Field(default=False, alias="LIGHTRAG_WORKER_ENABLED")
    lightrag_worker_poll_interval_ms: int = Field(default=2000, alias="LIGHTRAG_WORKER_POLL_INTERVAL_MS")
    lightrag_worker_batch_size: int = Field(default=50, alias="LIGHTRAG_WORKER_BATCH_SIZE")
    lightrag_worker_max_attempts: int = Field(default=6, alias="LIGHTRAG_WORKER_MAX_ATTEMPTS")
    lightrag_worker_lock_ttl_sec: int = Field(default=300, alias="LIGHTRAG_WORKER_LOCK_TTL_SEC")
    lightrag_working_dir: str = Field(default="./lightrag_storage", alias="LIGHTRAG_WORKING_DIR")
    lightrag_workspace: str = Field(default="", alias="LIGHTRAG_WORKSPACE")
    lightrag_graph_storage: str = Field(default="Neo4JStorage", alias="LIGHTRAG_GRAPH_STORAGE")
    lightrag_llm_model: str = Field(default="", alias="LIGHTRAG_LLM_MODEL")
    lightrag_llm_host: str = Field(default="", alias="LIGHTRAG_LLM_HOST")
    lightrag_llm_key: str | None = Field(default=None, alias="LIGHTRAG_LLM_KEY")
    lightrag_embedding_model: str = Field(default="", alias="LIGHTRAG_EMBEDDING_MODEL")
    lightrag_embedding_host: str = Field(default="", alias="LIGHTRAG_EMBEDDING_HOST")
    lightrag_embedding_key: str | None = Field(default=None, alias="LIGHTRAG_EMBEDDING_KEY")
    lightrag_embedding_dim: int = Field(default=1536, alias="LIGHTRAG_EMBEDDING_DIM")
    lightrag_ai_key_source: str = Field(default="env_or_db", alias="LIGHTRAG_AI_KEY_SOURCE")
    lightrag_init_timeout_sec: float = Field(default=120.0, alias="LIGHTRAG_INIT_TIMEOUT_SEC")
    lightrag_query_timeout_sec: float = Field(default=60.0, alias="LIGHTRAG_QUERY_TIMEOUT_SEC")
    lightrag_query_max_concurrency: int = Field(default=1, alias="LIGHTRAG_QUERY_MAX_CONCURRENCY")
    lightrag_query_cache_ttl_sec: int = Field(default=0, alias="LIGHTRAG_QUERY_CACHE_TTL_SEC")
    lightrag_query_cache_maxsize: int = Field(default=128, alias="LIGHTRAG_QUERY_CACHE_MAXSIZE")

    # LightRAG language (optional)
    # Controls prompt language for summarization/entity extraction inside LightRAG.
    # Example values: "English", "Chinese".
    lightrag_summary_language: str = Field(default="", alias="LIGHTRAG_SUMMARY_LANGUAGE")

    # LightRAG rerank (optional)
    # If configured, graph/vector recall will enable rerank automatically.
    lightrag_rerank_model: str = Field(default="", alias="LIGHTRAG_RERANK_MODEL")
    lightrag_rerank_host: str = Field(default="", alias="LIGHTRAG_RERANK_HOST")
    lightrag_rerank_key: str | None = Field(default=None, alias="LIGHTRAG_RERANK_KEY")
    lightrag_rerank_timeout_sec: float = Field(default=15.0, alias="LIGHTRAG_RERANK_TIMEOUT_SEC")
    # "standard" uses {"model","query","documents","top_n"}.
    # "aliyun" uses DashScope format {"model","input":{"query","documents"},"parameters":{"top_n"}}.
    lightrag_rerank_request_format: str = Field(default="standard", alias="LIGHTRAG_RERANK_REQUEST_FORMAT")
    lightrag_rerank_enable_chunking: bool = Field(default=False, alias="LIGHTRAG_RERANK_ENABLE_CHUNKING")
    lightrag_rerank_max_tokens_per_doc: int = Field(default=480, alias="LIGHTRAG_RERANK_MAX_TOKENS_PER_DOC")
    lightrag_min_rerank_score: float = Field(default=0.0, alias="LIGHTRAG_MIN_RERANK_SCORE")

    # Assistant KB tools (LightRAG-powered retrieval only)
    assistant_kb_graph_recall_mode: str = Field(default="mix", alias="ASSISTANT_KB_GRAPH_RECALL_MODE")
    assistant_kb_graph_recall_top_k: int = Field(default=10, alias="ASSISTANT_KB_GRAPH_RECALL_TOP_K")
    assistant_kb_graph_recall_chunk_top_k: int = Field(default=20, alias="ASSISTANT_KB_GRAPH_RECALL_CHUNK_TOP_K")
    assistant_kb_graph_recall_max_entries: int = Field(default=10, alias="ASSISTANT_KB_GRAPH_RECALL_MAX_ENTRIES")
    assistant_kb_graph_recall_chunks_per_entry: int = Field(default=3, alias="ASSISTANT_KB_GRAPH_RECALL_CHUNKS_PER_ENTRY")
    assistant_kb_graph_recall_max_chunk_chars: int = Field(default=600, alias="ASSISTANT_KB_GRAPH_RECALL_MAX_CHUNK_CHARS")
    assistant_kb_graph_recall_min_score: float = Field(default=0.0, alias="ASSISTANT_KB_GRAPH_RECALL_MIN_SCORE")
    assistant_kb_graph_recall_max_tokens: int = Field(default=8, alias="ASSISTANT_KB_GRAPH_RECALL_MAX_TOKENS")
    assistant_router_history_turns: int = Field(default=3, alias="ASSISTANT_ROUTER_HISTORY_TURNS")
    assistant_router_history_max_chars_per_message: int = Field(
        default=400,
        alias="ASSISTANT_ROUTER_HISTORY_MAX_CHARS_PER_MESSAGE",
    )
    assistant_router_history_max_messages: int = Field(default=6, alias="ASSISTANT_ROUTER_HISTORY_MAX_MESSAGES")
    assistant_router_include_last_skill_hint: bool = Field(
        default=True,
        alias="ASSISTANT_ROUTER_INCLUDE_LAST_SKILL_HINT",
    )
    assistant_memory_l0_turns: int = Field(default=6, alias="ASSISTANT_MEMORY_L0_TURNS")
    assistant_memory_l0_max_chars: int = Field(default=25000, alias="ASSISTANT_MEMORY_L0_MAX_CHARS")
    assistant_memory_l1_max_chars: int = Field(default=2000, alias="ASSISTANT_MEMORY_L1_MAX_CHARS")
    assistant_memory_l2_max_items: int = Field(default=20, alias="ASSISTANT_MEMORY_L2_MAX_ITEMS")
    assistant_memory_mode_default: str = Field(default="auto", alias="ASSISTANT_MEMORY_MODE_DEFAULT")
    assistant_memory_injection_max_chars: int = Field(
        default=30000,
        alias="ASSISTANT_MEMORY_INJECTION_MAX_CHARS",
    )

    # KB prompt injection budget (executor formats kb_search result into prompt)
    kb_context_max_chars: int = Field(default=16000, alias="KB_CONTEXT_MAX_CHARS")

    # Neo4j (required if lightrag_enabled=true)
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    # pgvector (optional, for vector storage in PostgreSQL)
    pgvector_enabled: bool = Field(default=False, alias="PGVECTOR_ENABLED")

    # Docling worker (optional, for attachment parsing)
    docling_worker_enabled: bool = Field(default=False, alias="DOCLING_WORKER_ENABLED")
    docling_worker_poll_interval_ms: int = Field(default=2000, alias="DOCLING_WORKER_POLL_INTERVAL_MS")
    docling_worker_batch_size: int = Field(default=1, alias="DOCLING_WORKER_BATCH_SIZE")
    docling_worker_max_attempts: int = Field(default=3, alias="DOCLING_WORKER_MAX_ATTEMPTS")
    docling_worker_lock_ttl_sec: int = Field(default=600, alias="DOCLING_WORKER_LOCK_TTL_SEC")
    docling_max_file_size_mb: int = Field(default=100, alias="DOCLING_MAX_FILE_SIZE_MB")
    docling_max_pdf_pages: int = Field(default=500, alias="DOCLING_MAX_PDF_PAGES")

    # Docling OCR (RapidOCR, CPU optimized)
    docling_ocr_enabled: bool = Field(default=True, alias="DOCLING_OCR_ENABLED")
    docling_ocr_force_full_page_ocr: bool = Field(default=False, alias="DOCLING_OCR_FORCE_FULL_PAGE_OCR")
    docling_ocr_langs: str = Field(default="auto", alias="DOCLING_OCR_LANGS")
    docling_ocr_det_model_path: str = Field(default="", alias="DOCLING_OCR_DET_MODEL_PATH")
    docling_ocr_rec_model_path: str = Field(default="", alias="DOCLING_OCR_REC_MODEL_PATH")
    docling_ocr_cls_model_path: str = Field(default="", alias="DOCLING_OCR_CLS_MODEL_PATH")
    docling_ocr_modelscope_enabled: bool = Field(default=True, alias="DOCLING_OCR_MODELSCOPE_ENABLED")
    docling_ocr_modelscope_repo_id: str = Field(default="RapidAI/RapidOCR", alias="DOCLING_OCR_MODELSCOPE_REPO_ID")

    # Docling picture description (remote VLM via OpenAI-compatible API)
    docling_picture_description_enabled: bool = Field(default=False, alias="DOCLING_PICTURE_DESCRIPTION_ENABLED")
    docling_picture_description_url: str = Field(default="", alias="DOCLING_PICTURE_DESCRIPTION_URL")
    docling_picture_description_api_key: str | None = Field(default=None, alias="DOCLING_PICTURE_DESCRIPTION_API_KEY")
    docling_picture_description_model: str = Field(default="", alias="DOCLING_PICTURE_DESCRIPTION_MODEL")
    docling_picture_description_prompt: str = Field(
        default="请简要描述这张图片的内容；如果是图表请提取关键数据。",
        alias="DOCLING_PICTURE_DESCRIPTION_PROMPT",
    )
    docling_picture_description_timeout_sec: float = Field(default=60.0, alias="DOCLING_PICTURE_DESCRIPTION_TIMEOUT_SEC")
    docling_picture_description_concurrency: int = Field(default=1, alias="DOCLING_PICTURE_DESCRIPTION_CONCURRENCY")
    docling_picture_description_params_json: str = Field(default="", alias="DOCLING_PICTURE_DESCRIPTION_PARAMS_JSON")

    # Scheduler (optional, for background jobs like weekly report generation)
    scheduler_enabled: bool = Field(default=False, alias="SCHEDULER_ENABLED")

    # Workflow code executor
    workflow_code_executor_timeout_ms: int = Field(default=5000, alias="WORKFLOW_CODE_EXECUTOR_TIMEOUT_MS")
    workflow_code_executor_max_timeout_ms: int = Field(default=5000, alias="WORKFLOW_CODE_EXECUTOR_MAX_TIMEOUT_MS")
    workflow_code_executor_memory_limit_mb: int = Field(default=128, alias="WORKFLOW_CODE_EXECUTOR_MEMORY_LIMIT_MB")
    workflow_code_executor_max_output_chars: int = Field(default=16000, alias="WORKFLOW_CODE_EXECUTOR_MAX_OUTPUT_CHARS")
    workflow_code_executor_python_allowed_modules: str = Field(
        default="json,re,math,datetime,statistics,itertools,functools,decimal,uuid,base64,hashlib,collections",
        alias="WORKFLOW_CODE_EXECUTOR_PYTHON_ALLOWED_MODULES",
    )
    workflow_code_executor_javascript_allowed_modules: str = Field(
        default="path,url,crypto,util",
        alias="WORKFLOW_CODE_EXECUTOR_JAVASCRIPT_ALLOWED_MODULES",
    )
    workflow_http_request_timeout_ms: int = Field(default=15000, alias="WORKFLOW_HTTP_REQUEST_TIMEOUT_MS")
    workflow_http_request_max_timeout_ms: int = Field(default=60000, alias="WORKFLOW_HTTP_REQUEST_MAX_TIMEOUT_MS")
    workflow_http_request_max_retries: int = Field(default=5, alias="WORKFLOW_HTTP_REQUEST_MAX_RETRIES")
    workflow_http_request_max_response_bytes: int = Field(default=524288, alias="WORKFLOW_HTTP_REQUEST_MAX_RESPONSE_BYTES")

    # Server
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        value = (v or "").strip().upper()
        return value or "INFO"

    @field_validator("openclaw_capability_runtime_mode", mode="before")
    @classmethod
    def validate_openclaw_capability_runtime_mode(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError(
                "OPENCLAW_CAPABILITY_RUNTIME_MODE must be exactly 'legacy' or 'shared'"
            )
        value = v.strip()
        if value not in {"legacy", "shared"}:
            raise ValueError(
                "OPENCLAW_CAPABILITY_RUNTIME_MODE must be exactly 'legacy' or 'shared'"
            )
        return value

    def cors_origins_list(self) -> list[str]:
        value = self.cors_origins
        if not value or not value.strip():
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    def sqlalchemy_database_uri(self) -> str:
        # Support the reference default `postgresql://...` while ensuring a stable driver for SQLAlchemy.
        if self.database_url.startswith("postgresql://") and "+psycopg2" not in self.database_url:
            return self.database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
