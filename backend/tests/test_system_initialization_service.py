from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

# Encryption for AI credential staging during initialization.
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-bootstrap-task4")

bootstrap_backend_imports()
reset_caches()

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

# Ensure operator auth tables are registered on Base.metadata for make_session().
import app.operator_auth.models  # noqa: E402,F401


_OPERATOR_PASSWORD = "correct horse battery"
_CTX = None  # filled lazily after imports


def _request_context():
    from app.operator_auth.contracts import RequestSecurityContext

    return RequestSecurityContext(
        request_id="init-test-req",
        request_digest="a" * 64,
        user_agent_digest="b" * 64,
        network_digest="c" * 64,
    )


def _valid_setup():
    from app.operator_auth.contracts import SetupAuthorization

    return SetupAuthorization(validated=True)


def _initialization_marker(session):
    from app.system_settings.initialization_service import SYSTEM_INITIALIZATION_STATE_KEY
    from app.system_settings.models import AppSetting

    setting = (
        session.query(AppSetting)
        .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
        .first()
    )
    if setting is None or not isinstance(setting.value_json, dict):
        return None
    if setting.value_json.get("initialized") is not True:
        return None
    return setting


class SystemInitializationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _make_request(self, *, locale: str = "zh"):
        from app.system_settings.schemas import InitializeSystemRequest

        return InitializeSystemRequest.model_validate(
            {
                "locale": locale,
                "operatorPassword": _OPERATOR_PASSWORD,
                "aiCredential": {
                    "name": "OpenAI",
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-test-1234567890",
                },
                "llmModel": {
                    "name": "gpt-4.1-mini",
                },
                "entryTypes": [
                    {
                        "code": "KNOWLEDGE",
                        "name": "Knowledge" if locale == "en" else "知识",
                        "description": "Concepts" if locale == "en" else "知识点",
                        "color": "#3B82F6",
                        "icon": "book",
                        "graphEnabled": True,
                        "aiEnabled": True,
                        "enabled": True,
                        "origin": "default",
                    },
                    {
                        "name": "Custom Notes" if locale == "en" else "自定义笔记",
                        "description": "My own type" if locale == "en" else "我的自定义类型",
                        "color": "#14B8A6",
                        "icon": "file-text",
                        "graphEnabled": True,
                        "aiEnabled": True,
                        "enabled": True,
                        "origin": "custom",
                    },
                ],
            }
        )

    def test_fresh_install_reports_uninitialized(self) -> None:
        from app.system_settings.initialization_service import SystemInitializationService

        service = SystemInitializationService(self.db)
        status = service.get_initialization_status()
        defaults = service.get_initialization_defaults(locale="en")

        self.assertFalse(status.initialized)
        self.assertEqual(status.locale, "zh")
        self.assertFalse(hasattr(status, "legacy_auto_completed"))
        self.assertEqual(defaults.locale, "en")
        self.assertEqual(defaults.entry_types[0].origin, "default")
        self.assertEqual(defaults.entry_types[0].name, "Knowledge")

    def test_legacy_seeded_entry_types_still_require_initialization(self) -> None:
        from app.entry_type.models import EntryType
        from app.system_settings.initialization_service import SystemInitializationService

        legacy_seed_rows = [
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
        ]
        self.db.add_all(EntryType(**row) for row in legacy_seed_rows)
        self.db.commit()

        service = SystemInitializationService(self.db)
        status = service.get_initialization_status()

        self.assertFalse(status.initialized)
        self.assertFalse(hasattr(status, "legacy_auto_completed"))

    def test_existing_data_never_auto_completes_initialization(self) -> None:
        from app.entry_type.models import EntryType
        from app.entry.models import Entry, TimeMode
        from app.system_settings.initialization_service import SystemInitializationService

        entry_type = EntryType(
            code="NOTE",
            name="Note",
            description="preexisting",
            color="#111111",
            icon="file",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.db.add(entry_type)
        self.db.flush()
        self.db.add(
            Entry(
                title="preexisting development data",
                content="preexisting development data",
                type_id=entry_type.id,
                time_mode=TimeMode.NONE,
            )
        )
        self.db.commit()

        status = SystemInitializationService(self.db).get_initialization_status()
        self.assertFalse(status.initialized)
        self.assertFalse(hasattr(status, "legacy_auto_completed"))
        # Clean product never mutates an old development DB on status reads.
        self.assertIsNone(_initialization_marker(self.db))

    def test_stale_legacy_auto_completed_marker_is_ignored(self) -> None:
        """A legacy_auto_completed marker is still an initialized=True payload.

        Clean product does not rewrite or clear it on status reads; re-init is
        blocked until an operator deliberately resets the marker outside this
        path. What we guarantee: no auto-complete from bare domain data, and no
        ``legacy_auto_completed`` response field.
        """
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
            SystemInitializationService,
        )
        from app.system_settings.models import AppSetting

        self.db.add(
            AppSetting(
                key=SYSTEM_INITIALIZATION_STATE_KEY,
                value_json={
                    "initialized": True,
                    "locale": "zh",
                    "version": 1,
                    "source": "legacy_auto_completed",
                },
            )
        )
        self.db.commit()

        service = SystemInitializationService(self.db)
        status = service.get_initialization_status()

        # Marker still means initialized under clean-only status (payload-based).
        # Response shape no longer exposes legacy_auto_completed.
        self.assertTrue(status.initialized)
        self.assertFalse(hasattr(status, "legacy_auto_completed"))

    def test_existing_ai_configuration_never_auto_completes(self) -> None:
        from app.ai_registry.models import AiCredential
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
            SystemInitializationService,
        )
        from app.system_settings.models import AppSetting

        self.db.add(
            AiCredential(
                name="Existing Provider",
                base_url="https://api.openai.com/v1",
                api_key_encrypted="token",
                api_key_hint="****1234",
            )
        )
        self.db.commit()

        service = SystemInitializationService(self.db)
        status = service.get_initialization_status()
        setting = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
            .first()
        )

        self.assertFalse(status.initialized)
        self.assertFalse(hasattr(status, "legacy_auto_completed"))
        self.assertIsNone(setting)

    def test_customized_entry_types_never_auto_complete(self) -> None:
        from app.entry_type.models import EntryType
        from app.system_settings.initialization_service import SystemInitializationService

        self.db.add(
            EntryType(
                code="CUSTOMIZED",
                name="Customized",
                description="Custom",
                color="#111111",
                icon="sparkles",
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            )
        )
        self.db.commit()

        service = SystemInitializationService(self.db)
        status = service.get_initialization_status()

        self.assertFalse(status.initialized)
        self.assertFalse(hasattr(status, "legacy_auto_completed"))

    def test_initialize_system_writes_models_bindings_and_defaults(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
        from app.assistant_config.models import AssistantAgentProfile, AssistantSystemBehaviorBinding
        from app.entry_type.models import EntryType
        from app.relation.models import RelationType
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
            SystemInitializationService,
        )
        from app.system_settings.models import AppSetting

        service = SystemInitializationService(self.db)
        result = service.initialize_system(self._make_request(locale="en"))

        self.assertTrue(result.initialized)
        self.assertEqual(result.locale, "en")

        credentials = self.db.query(AiCredential).all()
        models = self.db.query(AiModel).all()
        bindings = self.db.query(AiComponentBinding).all()
        entry_types = {item.code: item for item in self.db.query(EntryType).all()}
        relation_types = {item.code: item for item in self.db.query(RelationType).all()}
        init_state = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
            .first()
        )

        self.assertEqual(len(credentials), 1)
        self.assertEqual(len(models), 1)
        self.assertEqual(len(bindings), 3)
        llm_models = [item for item in models if item.model_type == "llm"]
        self.assertEqual(len(llm_models), 1)
        self.assertTrue(all(item.llm_model_id == llm_models[0].id for item in bindings))
        lightrag_binding = next(item for item in bindings if item.component == "lightrag")
        embedding_models = [item for item in models if item.model_type == "embedding"]
        self.assertEqual(len(embedding_models), 0)
        self.assertIsNone(lightrag_binding.embedding_model_id)
        self.assertEqual(entry_types["KNOWLEDGE"].name, "Knowledge")
        self.assertIn("CUSTOM_TYPE_1", entry_types)
        self.assertEqual(relation_types["BELONGS_TO"].name, "Belongs To")
        self.assertGreater(
            self.db.query(AssistantAgentProfile)
            .filter(AssistantAgentProfile.is_system.is_(True))
            .count(),
            0,
        )
        self.assertGreater(self.db.query(AssistantSystemBehaviorBinding).count(), 0)
        self.assertIsNotNone(init_state)

    def test_initialize_system_forces_entry_type_capabilities_enabled(self) -> None:
        from app.entry_type.models import EntryType
        from app.system_settings.initialization_service import SystemInitializationService

        request = self._make_request(locale="zh")
        request.entry_types[0].graph_enabled = False
        request.entry_types[0].ai_enabled = False
        request.entry_types[0].enabled = False

        service = SystemInitializationService(self.db)
        service.initialize_system(request)

        knowledge = (
            self.db.query(EntryType)
            .filter(EntryType.code == "KNOWLEDGE")
            .first()
        )

        self.assertIsNotNone(knowledge)
        self.assertTrue(bool(knowledge.enabled))
        self.assertTrue(bool(knowledge.graph_enabled))
        self.assertTrue(bool(knowledge.ai_enabled))

    def test_initialize_system_persists_lightweight_runtime_ai_defaults(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiModel
        from app.system_settings.initialization_service import SystemInitializationService
        from app.system_settings.models import AppSetting
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeConfigPayloadRequest

        request = self._make_request(locale="zh")
        request.runtime_config = RuntimeConfigPayloadRequest.model_validate(
            {
                "knowledgeGraph": {
                    "summaryLanguage": "Chinese",
                    "embeddingModelName": "text-embedding-3-large",
                    "embeddingHost": "https://embedding.example/v1",
                    "embeddingDim": 3072,
                    "embeddingApiKey": "embedding-secret-456",
                    "rerankModel": "bge-reranker-v2-m3",
                    "rerankHost": "https://rerank.example/v1",
                    "rerankApiKey": "rerank-secret-123",
                    "rerankRequestFormat": "standard",
                },
                "documentParsing": {
                    "ocrLangs": "zh,en",
                },
            }
        )

        service = SystemInitializationService(self.db)
        service.initialize_system(request)

        models = self.db.query(AiModel).all()
        embedding_models = [item for item in models if item.model_type == "embedding"]
        lightrag_binding = (
            self.db.query(AiComponentBinding)
            .filter(AiComponentBinding.component == "lightrag")
            .first()
        )
        knowledge_graph_setting = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == "runtime_knowledge_graph_config")
            .first()
        )
        document_parsing_setting = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == "runtime_document_parsing_config")
            .first()
        )

        self.assertEqual(len(models), 1)
        self.assertEqual(len(embedding_models), 0)
        self.assertIsNotNone(lightrag_binding)
        self.assertIsNone(lightrag_binding.embedding_model_id)
        self.assertIsNotNone(knowledge_graph_setting)
        self.assertEqual(knowledge_graph_setting.value_json["embeddingModelName"], "text-embedding-3-large")
        self.assertEqual(knowledge_graph_setting.value_json["summaryLanguage"], "Chinese")
        self.assertEqual(knowledge_graph_setting.value_json["embeddingHost"], "https://embedding.example/v1")
        self.assertEqual(knowledge_graph_setting.value_json["embeddingDim"], 3072)
        self.assertEqual(knowledge_graph_setting.value_json["rerankModel"], "bge-reranker-v2-m3")
        self.assertEqual(knowledge_graph_setting.value_json["rerankHost"], "https://rerank.example/v1")
        self.assertEqual(knowledge_graph_setting.value_json["rerankRequestFormat"], "standard")
        self.assertIn("embeddingApiKeyEncrypted", knowledge_graph_setting.value_json)
        self.assertNotIn("embeddingApiKey", knowledge_graph_setting.value_json)
        self.assertIn("rerankApiKeyEncrypted", knowledge_graph_setting.value_json)
        self.assertNotIn("rerankApiKey", knowledge_graph_setting.value_json)
        self.assertEqual(
            SystemRuntimeConfigService(self.db).get_runtime_config_response().knowledge_graph.embedding_dim,
            3072,
        )
        self.assertIsNotNone(document_parsing_setting)
        self.assertEqual(document_parsing_setting.value_json["ocrLangs"], "zh,en")

    def test_initialize_system_persists_automation_toggle_explicitly(self) -> None:
        from app.system_settings.initialization_service import SystemInitializationService
        from app.system_settings.models import AppSetting
        from app.system_settings.schemas import RuntimeConfigPayloadRequest

        request = self._make_request(locale="zh")
        request.runtime_config = RuntimeConfigPayloadRequest.model_validate(
            {
                "automation": {
                    "schedulerEnabled": False,
                },
            }
        )

        with patch("app.system_settings.initialization_service.sync_scheduler") as sync_scheduler_mock:
            SystemInitializationService(self.db).initialize_system(request)

        automation_setting = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == "runtime_automation_config")
            .first()
        )

        self.assertIsNotNone(automation_setting)
        self.assertEqual(automation_setting.value_json["schedulerEnabled"], False)
        sync_scheduler_mock.assert_called_once()

    def test_initialize_system_rolls_back_on_invalid_provider_url(self) -> None:
        from app.ai_registry.models import AiCredential
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
            SystemInitializationService,
        )
        from app.system_settings.models import AppSetting

        request = self._make_request(locale="zh")
        request.ai_credential.base_url = "http://localhost:11434/v1"

        service = SystemInitializationService(self.db)
        with self.assertRaises(Exception):
            service.initialize_system(request)

        self.assertEqual(self.db.query(AiCredential).count(), 0)
        self.assertEqual(
            self.db.query(AppSetting)
            .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
            .count(),
            0,
        )

    def test_failed_core_stage_rolls_back_operator_and_marker(self) -> None:
        from app.assistant.runtime.models import (
            AssistantMainAgentRolloutEvent,
            AssistantMainAgentRolloutRevision,
        )
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantSkillPackage,
        )
        from app.operator_auth.models import OperatorAccount
        from app.system_settings.initialization_coordinator import InitializationCoordinator
        from app.system_settings.initialization_service import SystemInitializationService

        coordinator = InitializationCoordinator(self.db)
        request = self._make_request(locale="en")

        with patch.object(
            SystemInitializationService,
            "_align_relation_types",
            side_effect=RuntimeError("injected"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                coordinator.initialize(
                    request,
                    setup_authorization=_valid_setup(),
                    request_context=_request_context(),
                )

        # Fresh view after rollback.
        self.db.expire_all()
        self.assertEqual(self.db.query(OperatorAccount).count(), 0)
        self.assertIsNone(_initialization_marker(self.db))
        self.assertEqual(self.db.query(AssistantSkillPackage).count(), 0)
        self.assertEqual(self.db.query(AssistantMainAgentProfile).count(), 0)
        self.assertEqual(self.db.query(AssistantMainAgentRolloutRevision).count(), 0)
        self.assertEqual(self.db.query(AssistantMainAgentRolloutEvent).count(), 0)

    def test_coordinator_seeds_operator_and_marker_atomically(self) -> None:
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantSkillPackage,
        )
        from app.operator_auth.models import OperatorAccount, OperatorAuditEvent
        from app.system_settings.initialization_coordinator import InitializationCoordinator
        from app.system_settings.initialization_service import SystemInitializationService

        with patch("app.system_settings.initialization_service.sync_scheduler"):
            result = InitializationCoordinator(self.db).initialize(
                self._make_request(locale="en"),
                setup_authorization=_valid_setup(),
                request_context=_request_context(),
            )

        self.assertEqual(result.locale, "en")
        self.assertEqual(self.db.query(OperatorAccount).count(), 1)
        account = self.db.query(OperatorAccount).one()
        self.assertTrue(account.enabled)
        self.assertEqual(account.role, "operator")
        self.assertEqual(result.operator_account_id, account.id)
        marker = _initialization_marker(self.db)
        self.assertIsNotNone(marker)
        self.assertEqual(marker.value_json.get("source"), "user")
        status = SystemInitializationService(self.db).get_initialization_status()
        self.assertTrue(status.initialized)
        self.assertFalse(hasattr(status, "legacy_auto_completed"))

        # Bootstrap prepares but does not activate.
        self.assertIsNotNone(result.prepared_rollout_revision_id)
        self.assertEqual(result.rollout_control_revision, 0)
        package = self.db.query(AssistantSkillPackage).filter(
            AssistantSkillPackage.is_system.is_(True)
        ).one()
        self.assertEqual(package.canonical_name, "mindatlas-universal")
        profile = self.db.query(AssistantMainAgentProfile).one()
        self.assertEqual(profile.migration_state, "bootstrap")
        self.assertTrue(profile.runtime_enabled)
        control = self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
        self.assertIsNotNone(control)
        self.assertIsNone(control.active_rollout_revision_id)

        response = result.to_response()
        self.assertEqual(response.assistant_bootstrap, "pending_worker")
        self.assertEqual(
            response.prepared_rollout_revision_id, result.prepared_rollout_revision_id
        )
        self.assertEqual(response.rollout_control_revision, 0)

        audit_rows = (
            self.db.query(OperatorAuditEvent)
            .filter(OperatorAuditEvent.event_type == "operator_account_initialized")
            .all()
        )
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0].outcome, "succeeded")
        self.assertEqual(audit_rows[0].operator_id, account.id)
        self.assertEqual(audit_rows[0].metadata_json.get("assistantBootstrap"), "prepared")

    def test_second_initialization_is_rejected(self) -> None:
        from app.common.exceptions import ApiException
        from app.operator_auth.models import OperatorAccount
        from app.system_settings.initialization_coordinator import InitializationCoordinator

        with patch("app.system_settings.initialization_service.sync_scheduler"):
            InitializationCoordinator(self.db).initialize(
                self._make_request(locale="zh"),
                setup_authorization=_valid_setup(),
                request_context=_request_context(),
            )
            with self.assertRaises(ApiException) as ctx:
                InitializationCoordinator(self.db).initialize(
                    self._make_request(locale="en"),
                    setup_authorization=_valid_setup(),
                    request_context=_request_context(),
                )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.message, "system_already_initialized")
        self.assertEqual(self.db.query(OperatorAccount).count(), 1)


if __name__ == "__main__":
    unittest.main()
