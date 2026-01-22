from __future__ import annotations

from functools import lru_cache
from typing import Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = Field(default="MindAtlas API", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")

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
    lightrag_query_timeout_sec: float = Field(default=30.0, alias="LIGHTRAG_QUERY_TIMEOUT_SEC")
    lightrag_query_max_concurrency: int = Field(default=1, alias="LIGHTRAG_QUERY_MAX_CONCURRENCY")
    lightrag_query_cache_ttl_sec: int = Field(default=0, alias="LIGHTRAG_QUERY_CACHE_TTL_SEC")
    lightrag_query_cache_maxsize: int = Field(default=128, alias="LIGHTRAG_QUERY_CACHE_MAXSIZE")

    # Neo4j (required if lightrag_enabled=true)
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    # pgvector (optional, for vector storage in PostgreSQL)
    pgvector_enabled: bool = Field(default=False, alias="PGVECTOR_ENABLED")

    # Server
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

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
