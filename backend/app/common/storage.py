"""MinIO storage client singleton for attachment storage."""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from app.config import get_settings


class StorageError(Exception):
    """Storage operation error."""
    pass


@lru_cache(maxsize=1)
def get_minio_client() -> tuple[Minio, str]:
    """Get MinIO client singleton and bucket name.

    Returns:
        Tuple of (Minio client, bucket name)

    Raises:
        StorageError: If MinIO is not configured or initialization fails
    """
    settings = get_settings()
    endpoint = (settings.minio_endpoint or "").strip()
    if not endpoint:
        raise StorageError("MinIO endpoint is not configured")

    secure = settings.minio_secure
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        parsed = urlparse(endpoint)
        secure = parsed.scheme == "https"
        endpoint = (parsed.netloc or parsed.path).rstrip("/")

    access_key = (settings.minio_access_key or "").strip()
    secret_key = (settings.minio_secret_key or "").strip()
    bucket = (settings.minio_bucket or "").strip()
    if not access_key or not secret_key or not bucket:
        raise StorageError("MinIO credentials/bucket are not configured")

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    # Check/create bucket once at startup
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except S3Error as exc:
        raise StorageError(f"Failed to initialize MinIO bucket: {exc.code}") from exc
    except Exception as exc:
        # Network issues (connection refused/timeouts) may raise non-S3 exceptions.
        raise StorageError(f"Failed to initialize MinIO bucket: {exc}") from exc

    return client, bucket


def remove_object_safe(client: Minio, bucket: str, object_key: str) -> bool:
    """Remove object from MinIO, ignoring not-found errors.

    Returns:
        True if deleted or not found, False on other errors
    """
    try:
        client.remove_object(bucket, object_key)
        return True
    except S3Error as exc:
        if getattr(exc, "code", "") in ("NoSuchKey", "NoSuchObject"):
            return True
        return False
    except Exception:
        # Treat network/transport failures as non-fatal for best-effort cleanup.
        return False
