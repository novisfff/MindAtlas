from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.common.request_context import get_request_locale
from app.config import get_settings
from app.system_settings.models import AppSetting

SystemLocale = Literal["zh", "en"]

SYSTEM_LOCALE_KEY = "system_locale"
SUPPORTED_SYSTEM_LOCALES: tuple[SystemLocale, SystemLocale] = ("zh", "en")


def normalize_system_locale(value: Any) -> SystemLocale | None:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_SYSTEM_LOCALES:
        return normalized  # type: ignore[return-value]
    return None


def get_default_system_locale() -> SystemLocale:
    settings = get_settings()
    return normalize_system_locale(getattr(settings, "app_default_locale", None)) or "zh"


def get_system_language_name(locale: str | None) -> str:
    return "Chinese" if normalize_system_locale(locale) == "zh" else "English"


class SystemSettingsService:
    def __init__(self, db: Session):
        self.db = db

    def _get_setting(self, key: str) -> AppSetting | None:
        return self.db.query(AppSetting).filter(AppSetting.key == key).first()

    def get_persisted_locale(self) -> SystemLocale | None:
        setting = self._get_setting(SYSTEM_LOCALE_KEY)
        if setting is None:
            return None
        payload = setting.value_json
        if isinstance(payload, dict):
            return normalize_system_locale(payload.get("locale"))
        return normalize_system_locale(payload)

    def resolve_locale_response(self, *, preferred_locale: str | None = None) -> tuple[SystemLocale, bool]:
        persisted_locale = self.get_persisted_locale()
        if persisted_locale is not None:
            return persisted_locale, True

        request_locale = normalize_system_locale(preferred_locale) or normalize_system_locale(get_request_locale())
        return request_locale or get_default_system_locale(), False

    def set_locale(self, locale: str) -> SystemLocale:
        normalized = normalize_system_locale(locale)
        if normalized is None:
            raise ValueError("locale must be zh or en")

        setting = self._get_setting(SYSTEM_LOCALE_KEY)
        if setting is None:
            setting = AppSetting(
                key=SYSTEM_LOCALE_KEY,
                value_json={"locale": normalized},
            )
            self.db.add(setting)
        else:
            setting.value_json = {"locale": normalized}

        self.db.commit()
        self.db.refresh(setting)
        return normalized


def resolve_system_locale(db: Session | None = None, *, preferred_locale: str | None = None) -> SystemLocale:
    normalized_preferred = normalize_system_locale(preferred_locale)
    if normalized_preferred is not None:
        return normalized_preferred

    request_locale = normalize_system_locale(get_request_locale())
    if request_locale is not None:
        return request_locale

    if db is not None:
        persisted_locale = SystemSettingsService(db).get_persisted_locale()
        if persisted_locale is not None:
            return persisted_locale

    return get_default_system_locale()
