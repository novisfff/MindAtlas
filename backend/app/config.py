from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Plan 04 main-agent hard ceilings. Settings may only lower these, never raise.
ASSISTANT_MAIN_AGENT_CATALOG_TOP_K_HARD_MAX = 32
ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS_HARD_MAX = 8
ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES_HARD_MAX = 65536
ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL_HARD_MAX = 262144
ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES_HARD_MAX = 1 << 20  # 1048576
ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX = 10 << 20  # 10485760
ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES_HARD_MAX = 65536

# Plan 06 durable Artifact storage hard ceilings. Configured values may only lower these.
ASSISTANT_ARTIFACT_INLINE_MAX_BYTES_HARD_MAX = 262144  # 256 KiB
ASSISTANT_ARTIFACT_MAX_BYTES_HARD_MAX = 26214400  # 25 MiB
ASSISTANT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX = 104857600  # 100 MiB

# Plan 07 durable Interrupt hard ceilings. Settings may only lower these, never raise.
ASSISTANT_INTERRUPT_MAX_TTL_SEC_HARD_MAX = 604800  # 7 days
ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS_HARD_MAX = 4000

AssistantCapabilityLedgerMode = Literal["legacy_read_only", "enforced"]
AssistantMainAgentWriteMode = Literal["off", "golden"]
# Plan 10 native runtime selection config.
AssistantRuntimeMode = Literal["legacy", "main_agent"]


def compute_artifact_orphan_grace_floor_sec(
    *,
    lease_ttl_sec: int,
    retry_base_ms: int,
    retry_max_ms: int,
    max_recovery_attempts: int,
    orphan_scan_interval_sec: int,
    clock_skew_sec: int,
) -> int:
    """Minimum orphan grace: lease + recovery backoff sum + scan interval + clock skew.

    Plan 06 §8: settings may only raise the grace above this derived recovery window.
    """
    backoff_ms = 0
    for attempt in range(max(0, int(max_recovery_attempts))):
        step = int(retry_base_ms) * (2**attempt)
        backoff_ms += min(step, int(retry_max_ms))
    total = (
        int(lease_ttl_sec)
        + (backoff_ms + 999) // 1000  # ceil ms -> sec
        + int(orphan_scan_interval_sec)
        + int(clock_skew_sec)
    )
    return max(1, total)


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

    # Plan 03 live model capability probe (paid Provider call). Default-disabled.
    # confirmProviderCall=true is cost acknowledgement only, not authentication.
    ai_model_capability_probe_enabled: bool = Field(
        default=False,
        alias="AI_MODEL_CAPABILITY_PROBE_ENABLED",
    )

    # Plan 10 runtime mode (default legacy) and durable rollout revision label.
    assistant_runtime_mode: AssistantRuntimeMode = Field(
        default="legacy",
        alias="ASSISTANT_RUNTIME_MODE",
    )
    # Optional active durable rollout revision label (empty = none/default legacy).
    assistant_runtime_rollout_revision: str = Field(
        default="",
        alias="ASSISTANT_RUNTIME_ROLLOUT_REVISION",
    )
    # Reject the removed Plan 04 switch if it remains in process env or dotenv.
    removed_assistant_main_agent_mode: str | None = Field(
        default=None,
        alias="ASSISTANT_MAIN_AGENT_MODE",
        exclude=True,
        repr=False,
    )
    # Main Agent bounded resource ceilings.
    assistant_main_agent_catalog_top_k: int = Field(
        default=8,
        ge=1,
        le=ASSISTANT_MAIN_AGENT_CATALOG_TOP_K_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_CATALOG_TOP_K",
    )
    assistant_main_agent_max_active_skills: int = Field(
        default=4,
        ge=1,
        le=ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS",
    )
    assistant_main_agent_resource_chunk_bytes: int = Field(
        default=16384,
        ge=1024,
        le=ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES",
    )
    assistant_main_agent_resource_max_bytes_per_call: int = Field(
        default=65536,
        ge=1024,
        le=ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL",
    )
    assistant_main_agent_artifact_max_bytes: int = Field(
        default=1048576,
        ge=1024,
        le=ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES",
    )
    assistant_main_agent_artifact_run_max_bytes: int = Field(
        default=5242880,
        ge=1024,
        le=ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES",
    )
    assistant_main_agent_inline_result_bytes: int = Field(
        default=16384,
        ge=256,
        le=ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES_HARD_MAX,
        alias="ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES",
    )

    # Plan 06 durable worker / Artifact storage (private MinIO bucket; never use attachment bucket).
    assistant_worker_poll_interval_ms: int = Field(
        default=500,
        ge=50,
        le=60_000,
        alias="ASSISTANT_WORKER_POLL_INTERVAL_MS",
    )
    assistant_worker_lease_ttl_sec: int = Field(
        default=30,
        ge=5,
        le=3600,
        alias="ASSISTANT_WORKER_LEASE_TTL_SEC",
    )
    assistant_worker_heartbeat_interval_sec: int = Field(
        default=5,
        ge=1,
        le=600,
        alias="ASSISTANT_WORKER_HEARTBEAT_INTERVAL_SEC",
    )
    assistant_worker_registration_ttl_sec: int = Field(
        default=20,
        ge=5,
        le=3600,
        alias="ASSISTANT_WORKER_REGISTRATION_TTL_SEC",
    )
    assistant_worker_max_recovery_attempts: int = Field(
        default=5,
        ge=1,
        le=50,
        alias="ASSISTANT_WORKER_MAX_RECOVERY_ATTEMPTS",
    )
    assistant_worker_retry_base_ms: int = Field(
        default=500,
        ge=50,
        le=60_000,
        alias="ASSISTANT_WORKER_RETRY_BASE_MS",
    )
    assistant_worker_retry_max_ms: int = Field(
        default=30_000,
        ge=100,
        le=600_000,
        alias="ASSISTANT_WORKER_RETRY_MAX_MS",
    )
    assistant_interrupt_expiry_scan_interval_sec: float = Field(
        default=5.0,
        ge=0.1,
        le=3600.0,
        alias="ASSISTANT_INTERRUPT_EXPIRY_SCAN_INTERVAL_SEC",
    )
    assistant_interrupt_expiry_scan_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
        alias="ASSISTANT_INTERRUPT_EXPIRY_SCAN_BATCH_SIZE",
    )
    assistant_artifact_bucket: str = Field(
        default="mindatlas-assistant-artifacts",
        min_length=3,
        max_length=63,
        alias="ASSISTANT_ARTIFACT_BUCKET",
    )
    assistant_artifact_inline_max_bytes: int = Field(
        default=ASSISTANT_ARTIFACT_INLINE_MAX_BYTES_HARD_MAX,
        ge=1,
        le=ASSISTANT_ARTIFACT_INLINE_MAX_BYTES_HARD_MAX,
        alias="ASSISTANT_ARTIFACT_INLINE_MAX_BYTES",
    )
    assistant_artifact_max_bytes: int = Field(
        default=ASSISTANT_ARTIFACT_MAX_BYTES_HARD_MAX,
        ge=1,
        le=ASSISTANT_ARTIFACT_MAX_BYTES_HARD_MAX,
        alias="ASSISTANT_ARTIFACT_MAX_BYTES",
    )
    assistant_artifact_run_max_bytes: int = Field(
        default=ASSISTANT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX,
        ge=1,
        le=ASSISTANT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX,
        alias="ASSISTANT_ARTIFACT_RUN_MAX_BYTES",
    )
    assistant_artifact_orphan_scan_interval_sec: int = Field(
        default=60,
        ge=5,
        le=3600,
        alias="ASSISTANT_ARTIFACT_ORPHAN_SCAN_INTERVAL_SEC",
    )
    assistant_artifact_orphan_grace_sec: int = Field(
        default=900,
        ge=1,
        le=86_400,
        alias="ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC",
    )
    assistant_durable_clock_skew_sec: int = Field(
        default=30,
        ge=0,
        le=600,
        alias="ASSISTANT_DURABLE_CLOCK_SKEW_SEC",
    )

    # Plan 07 durable human Interrupts. Default disabled; pepper blank in examples.
    # Enabling requires a nonempty stable pepper and a compatible v2 worker.
    assistant_durable_interrupts_enabled: bool = Field(
        default=False,
        alias="ASSISTANT_DURABLE_INTERRUPTS_ENABLED",
    )
    assistant_interrupt_default_ttl_sec: int = Field(
        default=86400,
        ge=1,
        le=ASSISTANT_INTERRUPT_MAX_TTL_SEC_HARD_MAX,
        alias="ASSISTANT_INTERRUPT_DEFAULT_TTL_SEC",
    )
    assistant_interrupt_max_ttl_sec: int = Field(
        default=ASSISTANT_INTERRUPT_MAX_TTL_SEC_HARD_MAX,
        ge=1,
        le=ASSISTANT_INTERRUPT_MAX_TTL_SEC_HARD_MAX,
        alias="ASSISTANT_INTERRUPT_MAX_TTL_SEC",
    )
    assistant_interrupt_comment_max_chars: int = Field(
        default=ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS_HARD_MAX,
        ge=1,
        le=ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS_HARD_MAX,
        alias="ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS",
    )
    # Never provide a real default. Required nonempty when interrupts are enabled.
    assistant_interrupt_token_pepper: str = Field(
        default="",
        alias="ASSISTANT_INTERRUPT_TOKEN_PEPPER",
    )

    # Plan 08: HMAC secret for server-generated capability call idempotency keys.
    # Required (min 32 bytes) when capability ledger mode is enforced.
    assistant_capability_call_idempotency_secret: str = Field(
        default="",
        alias="ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET",
    )
    # Plan 08 ledger admission default for new Main Agent Runs (frozen per Run).
    # Default legacy_read_only; enforced only for explicit test/admin cohorts.
    assistant_capability_ledger_mode: AssistantCapabilityLedgerMode = Field(
        default="legacy_read_only",
        alias="ASSISTANT_CAPABILITY_LEDGER_MODE",
    )
    # Plan 08 golden write release gate. Default off; golden requires enforced ledger.
    assistant_main_agent_write_mode: AssistantMainAgentWriteMode = Field(
        default="off",
        alias="ASSISTANT_MAIN_AGENT_WRITE_MODE",
    )
    # Optional cohort digest for golden write eligibility (empty = no cohort).
    assistant_main_agent_write_cohort_digest: str = Field(
        default="",
        alias="ASSISTANT_MAIN_AGENT_WRITE_COHORT_DIGEST",
    )
    # Guarded local reconciliation mutation path. Default-disabled; the actor
    # identity is server-owned configuration, never request/CLI input.
    assistant_capability_reconciliation_enabled: bool = Field(
        default=False,
        alias="ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED",
    )
    assistant_capability_reconciliation_operator_id: UUID | None = Field(
        default=None,
        alias="ASSISTANT_CAPABILITY_RECONCILIATION_OPERATOR_ID",
    )
    assistant_capability_reconciliation_evidence_secret: str = Field(
        default="",
        alias="ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET",
    )
    # Plan 09 evaluation: failed/unused gate evidence retention grace after expiry.
    assistant_skill_gate_evidence_grace_days: int = Field(
        default=30,
        alias="ASSISTANT_SKILL_GATE_EVIDENCE_GRACE_DAYS",
    )
    # Plan 09 publish/catalog gate mode. observe at introduce; enforce before plan exit.
    # observe: ungated publish only for live-disabled bootstrap; never ungated enable
    # or ungated pointer advance on already-enabled aggregates.
    # enforce: native packages/Profiles require gateId for every publish/promotion.
    assistant_skill_publish_gate_mode: str = Field(
        default="observe",
        alias="ASSISTANT_SKILL_PUBLISH_GATE_MODE",
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

    # Single-operator control plane (Plan 1). Secrets have no repository defaults;
    # Settings remains constructible when they are absent so /health stays up.
    # Missing secrets never generate ephemeral replacements.
    initial_setup_token: SecretStr | None = Field(
        default=None, alias="MINDATLAS_INITIAL_SETUP_TOKEN"
    )
    canonical_origin: str = Field(default="", alias="MINDATLAS_CANONICAL_ORIGIN")
    session_hmac_active_key_id: str = Field(
        default="", alias="MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID"
    )
    session_hmac_keys: SecretStr | None = Field(
        default=None, alias="MINDATLAS_SESSION_HMAC_KEYS"
    )

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

    @field_validator("removed_assistant_main_agent_mode", mode="before")
    @classmethod
    def reject_removed_main_agent_mode(cls, _v: object) -> object:
        if _v is None:
            return None
        raise ValueError(
            "ASSISTANT_MAIN_AGENT_MODE has been removed; use "
            "ASSISTANT_RUNTIME_MODE and ASSISTANT_RUNTIME_ROLLOUT_REVISION"
        )

    @model_validator(mode="after")
    def validate_main_agent_cross_field_bounds(self) -> Settings:
        # Resource max per call must cover one chunk; may only lower ceilings.
        if (
            self.assistant_main_agent_resource_max_bytes_per_call
            < self.assistant_main_agent_resource_chunk_bytes
        ):
            raise ValueError(
                "assistant_main_agent_resource_max_bytes_per_call must be >= "
                "assistant_main_agent_resource_chunk_bytes"
            )
        if (
            self.assistant_main_agent_artifact_run_max_bytes
            < self.assistant_main_agent_artifact_max_bytes
        ):
            raise ValueError(
                "assistant_main_agent_artifact_run_max_bytes must be >= "
                "assistant_main_agent_artifact_max_bytes"
            )
        if self.assistant_artifact_run_max_bytes < self.assistant_artifact_max_bytes:
            raise ValueError(
                "assistant_artifact_run_max_bytes must be >= assistant_artifact_max_bytes"
            )
        if self.assistant_artifact_inline_max_bytes > self.assistant_artifact_max_bytes:
            raise ValueError(
                "assistant_artifact_inline_max_bytes must be <= assistant_artifact_max_bytes"
            )
        if self.assistant_worker_retry_max_ms < self.assistant_worker_retry_base_ms:
            raise ValueError(
                "assistant_worker_retry_max_ms must be >= assistant_worker_retry_base_ms"
            )
        # Heartbeat must be strictly less than lease_ttl / 3.
        if self.assistant_worker_heartbeat_interval_sec * 3 >= self.assistant_worker_lease_ttl_sec:
            raise ValueError(
                "assistant_worker_heartbeat_interval_sec must be < "
                "assistant_worker_lease_ttl_sec / 3"
            )
        min_grace = compute_artifact_orphan_grace_floor_sec(
            lease_ttl_sec=self.assistant_worker_lease_ttl_sec,
            retry_base_ms=self.assistant_worker_retry_base_ms,
            retry_max_ms=self.assistant_worker_retry_max_ms,
            max_recovery_attempts=self.assistant_worker_max_recovery_attempts,
            orphan_scan_interval_sec=self.assistant_artifact_orphan_scan_interval_sec,
            clock_skew_sec=self.assistant_durable_clock_skew_sec,
        )
        if self.assistant_artifact_orphan_grace_sec < min_grace:
            raise ValueError(
                "assistant_artifact_orphan_grace_sec must be >= derived recovery window "
                f"({min_grace}s); settings may only raise the grace floor"
            )
        bucket = (self.assistant_artifact_bucket or "").strip()
        if not bucket or bucket == (self.minio_bucket or "").strip():
            raise ValueError(
                "assistant_artifact_bucket must be a nonempty private bucket distinct "
                "from MINIO_BUCKET (attachment public-download bucket)"
            )
        # Plan 07 interrupt TTL / comment ceilings.
        if self.assistant_interrupt_default_ttl_sec > self.assistant_interrupt_max_ttl_sec:
            raise ValueError(
                "assistant_interrupt_default_ttl_sec must be <= assistant_interrupt_max_ttl_sec"
            )
        if self.assistant_interrupt_max_ttl_sec > ASSISTANT_INTERRUPT_MAX_TTL_SEC_HARD_MAX:
            raise ValueError(
                "assistant_interrupt_max_ttl_sec must be <= "
                f"{ASSISTANT_INTERRUPT_MAX_TTL_SEC_HARD_MAX}"
            )
        if (
            self.assistant_interrupt_comment_max_chars
            > ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS_HARD_MAX
        ):
            raise ValueError(
                "assistant_interrupt_comment_max_chars must be <= "
                f"{ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS_HARD_MAX}"
            )
        if self.assistant_durable_interrupts_enabled and not (
            self.assistant_interrupt_token_pepper or ""
        ).strip():
            raise ValueError(
                "assistant_interrupt_token_pepper is required when "
                "assistant_durable_interrupts_enabled is true"
            )
        # Plan 08 ledger / golden write release gates.
        write_mode = str(self.assistant_main_agent_write_mode or "off")
        ledger_mode = str(self.assistant_capability_ledger_mode or "legacy_read_only")
        if write_mode not in {"off", "golden"}:
            raise ValueError(
                "assistant_main_agent_write_mode must be one of: off, golden"
            )
        if ledger_mode not in {"legacy_read_only", "enforced"}:
            raise ValueError(
                "assistant_capability_ledger_mode must be one of: "
                "legacy_read_only, enforced"
            )
        if write_mode == "golden" and ledger_mode != "enforced":
            raise ValueError(
                "assistant_main_agent_write_mode=golden requires "
                "assistant_capability_ledger_mode=enforced"
            )
        if (
            write_mode == "golden"
            and not self.assistant_capability_reconciliation_enabled
        ):
            raise ValueError(
                "assistant_main_agent_write_mode=golden requires the approved "
                "capability reconciliation operator path to be enabled"
            )
        if ledger_mode == "enforced" or write_mode == "golden":
            secret = (self.assistant_capability_call_idempotency_secret or "").strip()
            if len(secret.encode("utf-8")) < 32:
                raise ValueError(
                    "assistant_capability_call_idempotency_secret must be at least "
                    "32 bytes when ledger mode is enforced or write mode is golden"
                )
        if (
            self.assistant_capability_reconciliation_enabled
            and self.assistant_capability_reconciliation_operator_id is None
        ):
            raise ValueError(
                "assistant_capability_reconciliation_operator_id is required when "
                "assistant_capability_reconciliation_enabled is true"
            )
        if self.assistant_capability_reconciliation_enabled and len(
            self.assistant_capability_reconciliation_evidence_secret.encode("utf-8")
        ) < 32:
            raise ValueError(
                "assistant_capability_reconciliation_evidence_secret must be at least "
                "32 bytes when reconciliation is enabled"
            )
        gate_mode = (self.assistant_skill_publish_gate_mode or "").strip().lower()
        if gate_mode not in {"observe", "enforce"}:
            raise ValueError(
                "assistant_skill_publish_gate_mode must be 'observe' or 'enforce'"
            )
        self.assistant_skill_publish_gate_mode = gate_mode

        # Single-operator auth: production/staging origin + credentialed CORS.
        # Setup/session secrets may be absent (health stays up); never mint replacements.
        origin = self.canonical_origin.strip()
        cors = self.cors_origins_list()
        if self.app_env in {"production", "staging"}:
            if not origin.startswith("https://"):
                raise ValueError("MINDATLAS_CANONICAL_ORIGIN must be HTTPS")
            if "*" in cors or origin not in cors:
                raise ValueError(
                    "credentialed CORS must contain the exact canonical origin"
                )
        token = (
            self.initial_setup_token.get_secret_value()
            if self.initial_setup_token
            else ""
        )
        if token and len(token.encode("utf-8")) < 32:
            raise ValueError(
                "MINDATLAS_INITIAL_SETUP_TOKEN must be at least 32 UTF-8 bytes"
            )
        return self

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
