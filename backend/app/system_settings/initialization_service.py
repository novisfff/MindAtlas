from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, encrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException
from app.common.ssrf import validate_url_ssrf
from app.entry.models import Entry
from app.entry_type.models import EntryType
from app.relation.models import RelationType
from app.system_settings.initialization_defaults_loader import (
    InitializationDefaultEntryType,
    InitializationDefaultRelationType,
    load_initialization_entry_type_defaults,
    load_initialization_relation_type_defaults,
)
from app.system_settings.models import AppSetting
from app.system_settings.runtime_config_service import SystemRuntimeConfigService
from app.system_settings.schemas import (
    InitializeSystemRequest,
    InitializationCompletionResponse,
    InitializationDefaultEntryTypeResponse,
    InitializationDefaultsResponse,
    InitializationStatusResponse,
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
_INITIALIZATION_BINDING_COMPONENTS: tuple[str, str, str] = ("assistant", "lightrag", "workflow_copilot")
_LEGACY_ZH_SEEDED_ENTRY_TYPES: tuple[dict[str, Any], ...] = (
    {
        "code": "KNOWLEDGE",
        "name": "知识",
        "description": "学习的知识点",
        "color": "#3B82F6",
        "icon": "book",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "PROJECT",
        "name": "项目",
        "description": "参与的项目",
        "color": "#10B981",
        "icon": "folder",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "COMPETITION",
        "name": "比赛",
        "description": "参加的比赛",
        "color": "#F59E0B",
        "icon": "trophy",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "EXPERIENCE",
        "name": "经历",
        "description": "个人经历",
        "color": "#8B5CF6",
        "icon": "star",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "ACHIEVEMENT",
        "name": "成果",
        "description": "取得的成果",
        "color": "#EF4444",
        "icon": "award",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "TECHNOLOGY",
        "name": "技术",
        "description": "掌握的技术",
        "color": "#06B6D4",
        "icon": "code",
        "graph_enabled": True,
        "ai_enabled": True,
        "enabled": True,
    },
    {
        "code": "DOCUMENT",
        "name": "资料",
        "description": "收集的资料",
        "color": "#6B7280",
        "icon": "file",
        "graph_enabled": True,
        "ai_enabled": False,
        "enabled": True,
    },
)


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

    def _delete_initialization_state(self) -> None:
        setting = self._get_setting(SYSTEM_INITIALIZATION_STATE_KEY)
        if setting is not None:
            self.db.delete(setting)
            self.db.flush()

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

    def _entry_types_match_defaults_for_locale(self, locale: str) -> bool:
        defaults = load_initialization_entry_type_defaults(locale)
        return self._entry_types_match_defaults_snapshot(defaults)

    def _entry_types_match_defaults_snapshot(
        self,
        defaults: list[InitializationDefaultEntryType] | tuple[dict[str, Any], ...],
    ) -> bool:
        current_rows = self.db.query(EntryType).all()
        if not current_rows:
            return True
        if len(current_rows) != len(defaults):
            return False

        current_by_code = {str(item.code or "").strip(): item for item in current_rows}
        default_by_code = {
            str((item.code if isinstance(item, InitializationDefaultEntryType) else item["code"]) or "").strip(): item
            for item in defaults
        }
        if set(current_by_code) != set(default_by_code):
            return False

        for code, default in default_by_code.items():
            current = current_by_code[code]
            default_name = default.name if isinstance(default, InitializationDefaultEntryType) else default["name"]
            default_description = default.description if isinstance(default, InitializationDefaultEntryType) else default["description"]
            default_color = default.color if isinstance(default, InitializationDefaultEntryType) else default["color"]
            default_icon = default.icon if isinstance(default, InitializationDefaultEntryType) else default["icon"]
            default_graph_enabled = default.graph_enabled if isinstance(default, InitializationDefaultEntryType) else default["graph_enabled"]
            default_ai_enabled = default.ai_enabled if isinstance(default, InitializationDefaultEntryType) else default["ai_enabled"]
            default_enabled = default.enabled if isinstance(default, InitializationDefaultEntryType) else default["enabled"]
            if (
                (current.name or "").strip() != (default_name or "").strip()
                or (current.description or "").strip() != (default_description or "").strip()
                or (current.color or "").strip() != (default_color or "").strip()
                or (current.icon or "").strip() != (default_icon or "").strip()
                or bool(current.graph_enabled) != bool(default_graph_enabled)
                or bool(current.ai_enabled) != bool(default_ai_enabled)
                or bool(current.enabled) != bool(default_enabled)
            ):
                return False
        return True

    def _entry_types_have_customizations(self) -> bool:
        current_rows = self.db.query(EntryType).all()
        if not current_rows:
            return False
        return not (
            self._entry_types_match_defaults_for_locale("zh")
            or self._entry_types_match_defaults_for_locale("en")
            or self._entry_types_match_defaults_snapshot(_LEGACY_ZH_SEEDED_ENTRY_TYPES)
        )

    def _has_existing_entries(self) -> bool:
        return self.db.query(Entry.id).first() is not None

    def _has_existing_ai_configuration(self) -> bool:
        if self.db.query(AiCredential.id).first() is not None:
            return True
        if self.db.query(AiModel.id).first() is not None:
            return True
        return (
            self.db.query(AiComponentBinding.id)
            .filter(AiComponentBinding.llm_model_id.is_not(None))
            .first()
            is not None
        )

    def _should_auto_complete_legacy(self) -> bool:
        return (
            self._has_existing_entries()
            or self._has_existing_ai_configuration()
            or self._entry_types_have_customizations()
        )

    def get_initialization_status(self) -> InitializationStatusResponse:
        payload = self._initialization_payload()
        if payload is not None:
            if str(payload.get("source") or "") == "legacy_auto_completed" and not self._should_auto_complete_legacy():
                self._delete_initialization_state()
                self.db.commit()
                return InitializationStatusResponse(
                    initialized=False,
                    legacy_auto_completed=False,
                    locale=self._current_locale(),
                )
            locale = normalize_system_locale(payload.get("locale")) or self._current_locale()
            return InitializationStatusResponse(
                initialized=True,
                legacy_auto_completed=str(payload.get("source") or "") == "legacy_auto_completed",
                locale=locale,
            )

        if self._should_auto_complete_legacy():
            locale = self._current_locale()
            self._upsert_locale(locale)
            self._upsert_initialization_state(locale=locale, source="legacy_auto_completed")
            self.db.commit()
            return InitializationStatusResponse(
                initialized=True,
                legacy_auto_completed=True,
                locale=locale,
            )

        return InitializationStatusResponse(
            initialized=False,
            legacy_auto_completed=False,
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
            capability_modules=runtime_service.list_capability_module_summaries(locale=normalized_locale),
            runtime_config=runtime_config,
        )

    def _ensure_can_initialize(self) -> SystemLocale:
        status = self.get_initialization_status()
        if status.initialized:
            raise ApiException(status_code=409, code=40970, message="System is already initialized")
        return status.locale

    def _ensure_unique_credential_name(self, name: str) -> None:
        existing = self.db.query(AiCredential.id).filter(AiCredential.name.ilike(name)).first()
        if existing is not None:
            raise ApiException(status_code=409, code=40971, message=f"AI credential name already exists: {name}")

    def _create_ai_credential(self, *, name: str, base_url: str, api_key: str) -> AiCredential:
        validate_url_ssrf(base_url, raise_api_exception=True)
        self._ensure_unique_credential_name(name)
        try:
            encrypted = encrypt_api_key(api_key)
        except Exception as exc:
            raise ApiException(status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured") from exc

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
        model = AiModel(
            credential_id=credential_id,
            name=model_name,
            model_type="llm",
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
            item.code: item
            for item in load_initialization_entry_type_defaults(locale)
        }
        existing_codes = {
            str(code or "").strip()
            for code, in self.db.query(EntryType.code).all()
            if str(code or "").strip()
        }
        seen_codes: set[str] = set()
        prepared: list[dict[str, Any]] = []

        if not request.entry_types:
            raise ApiException(status_code=400, code=40070, message="At least one entry type is required")

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
                code = str(item.code or "").strip() or self._next_custom_code(existing_codes | seen_codes)

            if code in seen_codes:
                raise ApiException(status_code=400, code=40072, message=f"Duplicate entry type code: {code}")
            seen_codes.add(code)

            prepared.append(
                {
                    "code": code,
                    "name": item.name,
                    "description": item.description or "",
                    "color": item.color or "",
                    "icon": item.icon or "",
                    "graph_enabled": item.graph_enabled,
                    "ai_enabled": item.ai_enabled,
                    "enabled": item.enabled,
                }
            )

        return prepared

    def _align_entry_types(self, locale: SystemLocale, request: InitializeSystemRequest) -> None:
        prepared = self._prepare_entry_type_payloads(locale, request)
        submitted_codes = {item["code"] for item in prepared}
        existing_rows = {
            str(item.code or "").strip(): item
            for item in self.db.query(EntryType).all()
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
            str(item.code or "").strip(): item
            for item in self.db.query(RelationType).all()
        }
        for preset in defaults:
            self._apply_relation_type_defaults(existing_rows.get(preset.code), preset)
        self.db.flush()

    def initialize_system(self, request: InitializeSystemRequest) -> InitializationCompletionResponse:
        locale = normalize_system_locale(request.locale)
        if locale is None:
            raise ApiException(status_code=400, code=40040, message="locale must be zh or en")

        self._ensure_can_initialize()

        try:
            self._upsert_locale(locale)
            credential = self._create_ai_credential(
                name=request.ai_credential.name.strip(),
                base_url=request.ai_credential.base_url.strip(),
                api_key=request.ai_credential.api_key.strip(),
            )
            llm_model = self._create_llm_model(
                credential_id=credential.id,
                model_name=request.llm_model.name.strip(),
            )
            self._bind_llm_model(llm_model.id)
            self._align_entry_types(locale, request)
            self._align_relation_types(locale)

            assistant_config_service = AssistantConfigService(self.db)
            assistant_config_service.reset_all_system_skills(confirm=True, commit=False)
            assistant_config_service.reset_all_system_behaviors(confirm=True, commit=False)

            runtime_service = SystemRuntimeConfigService(self.db)
            if request.runtime_config is not None:
                if request.runtime_config.storage is not None:
                    runtime_service.update_storage_config(request.runtime_config.storage, commit=False)

                if request.runtime_config.knowledge_graph is not None:
                    if not request.runtime_config.knowledge_graph.summary_language:
                        request.runtime_config.knowledge_graph.summary_language = "Chinese" if locale == "zh" else "English"
                    runtime_service.update_knowledge_graph_config(
                        request.runtime_config.knowledge_graph,
                        commit=False,
                        default_embedding_name="text-embedding-3-small",
                    )

                if request.runtime_config.document_parsing is not None:
                    runtime_service.update_document_parsing_config(request.runtime_config.document_parsing, commit=False)

                if request.runtime_config.automation is not None:
                    runtime_service.update_automation_config(request.runtime_config.automation, commit=False)

            self._upsert_initialization_state(locale=locale, source="user")
            self.db.commit()
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
            locale=locale,
        )
