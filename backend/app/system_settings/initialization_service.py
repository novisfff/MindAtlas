from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, encrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException
from app.common.ssrf import validate_url_ssrf
from app.entry_type.models import EntryType
from app.relation.models import RelationType
from app.scheduler import sync_scheduler
from app.system_settings.initialization_defaults_loader import (
    InitializationDefaultRelationType,
    load_initialization_entry_type_defaults,
    load_initialization_relation_type_defaults,
)
from app.system_settings.models import AppSetting
from app.system_settings.runtime_config_service import (
    SystemRuntimeConfigService,
    clear_runtime_config_caches,
)
from app.system_settings.schemas import (
    InitializationCompletionResponse,
    InitializationDefaultEntryTypeResponse,
    InitializationDefaultsResponse,
    InitializationStatusResponse,
    InitializeSystemRequest,
)
from app.system_settings.service import (
    SYSTEM_LOCALE_KEY,
    SystemLocale,
    SystemSettingsService,
    get_default_system_locale,
    normalize_system_locale,
)

SYSTEM_INITIALIZATION_STATE_KEY = "system_initialization_state"
SYSTEM_INITIALIZATION_VERSION = 1
_INITIALIZATION_BINDING_COMPONENTS: tuple[str, str, str] = (
    "assistant",
    "lightrag",
    "workflow_copilot",
)


@dataclass(frozen=True)
class CoreInitializationResult:
    """Staged core product state inside the outer initialization transaction."""

    locale: SystemLocale
    credential_id: UUID
    llm_model_id: UUID


def require_supported_locale(value: Any) -> SystemLocale:
    locale = normalize_system_locale(value)
    if locale is None:
        raise ApiException(status_code=400, code=40040, message="locale must be zh or en")
    return locale


class SystemInitializationService:
    def __init__(self, db: Session):
        self.db = db

    def _get_setting(self, key: str) -> AppSetting | None:
        return self.db.query(AppSetting).filter(AppSetting.key == key).first()

    def _upsert_setting(self, key: str, value_json: dict[str, Any]) -> AppSetting:
        setting = self._get_setting(key)
        if setting is None:
            setting = AppSetting(key=key, value_json=value_json)
            self.db.add(setting)
        else:
            setting.value_json = value_json
        self.db.flush()
        return setting

    def _current_locale(self) -> SystemLocale:
        locale, _persisted = SystemSettingsService(self.db).resolve_locale_response()
        return locale

    def _initialization_payload(self) -> dict[str, Any] | None:
        setting = self._get_setting(SYSTEM_INITIALIZATION_STATE_KEY)
        if setting is None or not isinstance(setting.value_json, dict):
            return None
        payload = dict(setting.value_json)
        return payload if payload.get("initialized") is True else None

    def is_initialized(self) -> bool:
        """True only when a clean ``initialized`` marker is present.

        Legacy-looking domain data never auto-initializes the clean product.
        """
        return self._initialization_payload() is not None

    def _upsert_initialization_state(self, *, locale: SystemLocale, source: str) -> None:
        payload = {
            "initialized": True,
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "locale": locale,
            "version": SYSTEM_INITIALIZATION_VERSION,
            "source": source,
        }
        self._upsert_setting(SYSTEM_INITIALIZATION_STATE_KEY, payload)

    def _upsert_locale(self, locale: SystemLocale) -> None:
        self._upsert_setting(SYSTEM_LOCALE_KEY, {"locale": locale})

    def get_initialization_status(self) -> InitializationStatusResponse:
        """Report clean marker only — never mutate or auto-complete."""
        payload = self._initialization_payload()
        if payload is not None:
            locale = normalize_system_locale(payload.get("locale")) or self._current_locale()
            return InitializationStatusResponse(
                initialized=True,
                locale=locale,
            )
        return InitializationStatusResponse(
            initialized=False,
            locale=self._current_locale(),
        )

    def get_initialization_defaults(self, locale: str | None = None) -> InitializationDefaultsResponse:
        normalized_locale = normalize_system_locale(locale) or get_default_system_locale()
        defaults = load_initialization_entry_type_defaults(normalized_locale)
        runtime_service = SystemRuntimeConfigService(self.db)
        runtime_config = runtime_service.get_runtime_config_response(locale=normalized_locale)
        return InitializationDefaultsResponse(
            locale=normalized_locale,
            entry_types=[
                InitializationDefaultEntryTypeResponse(
                    code=item.code,
                    name=item.name,
                    description=item.description,
                    color=item.color,
                    icon=item.icon,
                    graph_enabled=item.graph_enabled,
                    ai_enabled=item.ai_enabled,
                    enabled=item.enabled,
                    origin="default",
                )
                for item in defaults
            ],
            capability_modules=runtime_service.list_capability_module_summaries(
                locale=normalized_locale
            ),
            runtime_config=runtime_config,
        )

    def _ensure_can_initialize(self) -> SystemLocale:
        status = self.get_initialization_status()
        if status.initialized:
            raise ApiException(
                status_code=409,
                code=40970,
                message="system_already_initialized",
            )
        return status.locale

    def _ensure_unique_credential_name(self, name: str) -> None:
        existing = self.db.query(AiCredential.id).filter(AiCredential.name.ilike(name)).first()
        if existing is not None:
            raise ApiException(
                status_code=409,
                code=40971,
                message=f"AI credential name already exists: {name}",
            )

    def _create_ai_credential(self, *, name: str, base_url: str, api_key: str) -> AiCredential:
        validate_url_ssrf(base_url, raise_api_exception=True)
        self._ensure_unique_credential_name(name)
        try:
            encrypted = encrypt_api_key(api_key)
        except Exception as exc:
            raise ApiException(
                status_code=500,
                code=50001,
                message="AI_PROVIDER_FERNET_KEY not configured",
            ) from exc

        credential = AiCredential(
            name=name,
            base_url=base_url,
            api_key_encrypted=encrypted,
            api_key_hint=api_key_hint(api_key),
        )
        self.db.add(credential)
        self.db.flush()
        return credential

    def _create_llm_model(self, *, credential_id: Any, model_name: str) -> AiModel:
        return self._create_ai_model(
            credential_id=credential_id,
            model_name=model_name,
            model_type="llm",
        )

    def _create_ai_model(self, *, credential_id: Any, model_name: str, model_type: str) -> AiModel:
        model = AiModel(
            credential_id=credential_id,
            name=model_name,
            model_type=model_type,
        )
        self.db.add(model)
        self.db.flush()
        return model

    def _bind_llm_model(self, model_id: Any) -> None:
        for component in _INITIALIZATION_BINDING_COMPONENTS:
            binding = (
                self.db.query(AiComponentBinding)
                .filter(AiComponentBinding.component == component)
                .first()
            )
            if binding is None:
                binding = AiComponentBinding(component=component)
                self.db.add(binding)
                self.db.flush()
            binding.llm_model_id = model_id

    def _next_custom_code(self, used_codes: set[str]) -> str:
        index = 1
        while True:
            candidate = f"CUSTOM_TYPE_{index}"
            if candidate not in used_codes:
                used_codes.add(candidate)
                return candidate
            index += 1

    def _prepare_entry_type_payloads(
        self,
        locale: SystemLocale,
        request: InitializeSystemRequest,
    ) -> list[dict[str, Any]]:
        defaults_by_code = {
            item.code: item for item in load_initialization_entry_type_defaults(locale)
        }
        existing_codes = {
            str(code or "").strip()
            for code, in self.db.query(EntryType.code).all()
            if str(code or "").strip()
        }
        seen_codes: set[str] = set()
        prepared: list[dict[str, Any]] = []

        if not request.entry_types:
            raise ApiException(
                status_code=400,
                code=40070,
                message="At least one entry type is required",
            )

        for item in request.entry_types:
            if item.origin == "default":
                code = str(item.code or "").strip()
                if not code or code not in defaults_by_code:
                    raise ApiException(
                        status_code=400,
                        code=40071,
                        message=f"Invalid default entry type code: {item.code or ''}",
                    )
            else:
                code = str(item.code or "").strip() or self._next_custom_code(
                    existing_codes | seen_codes
                )

            if code in seen_codes:
                raise ApiException(
                    status_code=400,
                    code=40072,
                    message=f"Duplicate entry type code: {code}",
                )
            seen_codes.add(code)

            prepared.append(
                {
                    "code": code,
                    "name": item.name,
                    "description": item.description or "",
                    "color": item.color or "",
                    "icon": item.icon or "",
                    # Initialization keeps entry-type capabilities on by default.
                    "graph_enabled": True,
                    "ai_enabled": True,
                    "enabled": True,
                }
            )

        return prepared

    def _align_entry_types(self, locale: SystemLocale, request: InitializeSystemRequest) -> None:
        prepared = self._prepare_entry_type_payloads(locale, request)
        submitted_codes = {item["code"] for item in prepared}
        existing_rows = {
            str(item.code or "").strip(): item for item in self.db.query(EntryType).all()
        }

        for code, row in existing_rows.items():
            if code not in submitted_codes:
                self.db.delete(row)

        for item in prepared:
            row = existing_rows.get(item["code"])
            if row is None:
                row = EntryType(code=item["code"])
                self.db.add(row)

            row.name = item["name"]
            row.description = item["description"]
            row.color = item["color"]
            row.icon = item["icon"]
            row.graph_enabled = bool(item["graph_enabled"])
            row.ai_enabled = bool(item["ai_enabled"])
            row.enabled = bool(item["enabled"])

        self.db.flush()

    def _apply_relation_type_defaults(
        self,
        existing: RelationType | None,
        preset: InitializationDefaultRelationType,
    ) -> RelationType:
        row = existing or RelationType(code=preset.code)
        row.name = preset.name
        row.inverse_name = preset.inverse_name
        row.description = preset.description
        row.color = preset.color
        row.directed = bool(preset.directed)
        row.enabled = bool(preset.enabled)
        if existing is None:
            self.db.add(row)
        return row

    def _align_relation_types(self, locale: SystemLocale) -> None:
        defaults = load_initialization_relation_type_defaults(locale)
        existing_rows = {
            str(item.code or "").strip(): item for item in self.db.query(RelationType).all()
        }
        for preset in defaults:
            self._apply_relation_type_defaults(existing_rows.get(preset.code), preset)
        self.db.flush()

    def _stage_assistant_catalog_and_runtime_config(
        self,
        locale: SystemLocale,
        request: InitializeSystemRequest,
    ) -> None:
        assistant_config_service = AssistantConfigService(self.db)
        # Plan 10: legacy skill rows are gone; re-seed workflows/agents via catalog sync.
        assistant_config_service.ensure_system_catalog_synced()
        assistant_config_service.reset_all_system_behaviors(confirm=True, commit=False)

        runtime_service = SystemRuntimeConfigService(self.db)
        if request.runtime_config is not None:
            if request.runtime_config.storage is not None:
                runtime_service.update_storage_config(
                    request.runtime_config.storage, commit=False
                )

            if request.runtime_config.knowledge_graph is not None:
                if not request.runtime_config.knowledge_graph.summary_language:
                    request.runtime_config.knowledge_graph.summary_language = (
                        "Chinese" if locale == "zh" else "English"
                    )
                runtime_service.update_knowledge_graph_config(
                    request.runtime_config.knowledge_graph,
                    commit=False,
                )

            if request.runtime_config.document_parsing is not None:
                runtime_service.update_document_parsing_config(
                    request.runtime_config.document_parsing, commit=False
                )

            if request.runtime_config.automation is not None:
                runtime_service.update_automation_config(
                    request.runtime_config.automation, commit=False
                )

    def stage_core_initialization(
        self, request: InitializeSystemRequest
    ) -> CoreInitializationResult:
        """Stage locale, AI, types, assistant catalog, and runtime config (no commit)."""
        locale = require_supported_locale(request.locale)
        self._ensure_can_initialize()
        self._upsert_locale(locale)
        credential = self._create_ai_credential(
            name=request.ai_credential.name.strip(),
            base_url=request.ai_credential.base_url.strip(),
            api_key=request.ai_credential.api_key.strip(),
        )
        model = self._create_llm_model(
            credential_id=credential.id,
            model_name=request.llm_model.name.strip(),
        )
        self._bind_llm_model(model.id)
        self._align_entry_types(locale, request)
        self._align_relation_types(locale)
        self._stage_assistant_catalog_and_runtime_config(locale, request)
        return CoreInitializationResult(
            locale=locale,
            credential_id=credential.id,
            llm_model_id=model.id,
        )

    def stage_initialization_marker(
        self,
        *,
        locale: SystemLocale,
        source: Literal["user"] = "user",
    ) -> None:
        """Stage the clean initialization marker (no commit)."""
        if source != "user":
            raise ValueError("clean product marker source must be 'user'")
        self._upsert_initialization_state(locale=locale, source=source)

    def after_commit(self) -> None:
        """Clear runtime caches and resync scheduler after a successful commit."""
        clear_runtime_config_caches()
        sync_scheduler()

    def initialize_system(
        self, request: InitializeSystemRequest
    ) -> InitializationCompletionResponse:
        """Compatibility path for tests that stage+commit core without operator account.

        Production HTTP setup uses ``InitializationCoordinator`` which owns the
        outer transaction and also seeds the Operator account.
        """
        try:
            core = self.stage_core_initialization(request)
            self.stage_initialization_marker(locale=core.locale, source="user")
            self.db.commit()
            self.after_commit()
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40972,
                message="System initialization failed due to a constraint conflict",
            ) from exc
        except Exception:
            self.db.rollback()
            raise

        return InitializationCompletionResponse(
            initialized=True,
            locale=core.locale,
        )
