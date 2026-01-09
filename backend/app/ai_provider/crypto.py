from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import get_settings


def _get_fernet() -> Fernet:
    key = (get_settings().ai_provider_fernet_key or "").strip()
    if not key:
        raise ValueError("AI_PROVIDER_FERNET_KEY is not set")
    return Fernet(key.encode("utf-8"))


def encrypt_api_key(api_key: str) -> str:
    token = _get_fernet().encrypt(api_key.strip().encode("utf-8"))
    return token.decode("utf-8")


def decrypt_api_key(token: str) -> str:
    raw = _get_fernet().decrypt(token.encode("utf-8"))
    return raw.decode("utf-8")


def api_key_hint(api_key: str) -> str:
    value = (api_key or "").strip()
    if not value:
        return "****"
    suffix = value[-4:] if len(value) >= 4 else value
    return f"****{suffix}"
