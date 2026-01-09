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
