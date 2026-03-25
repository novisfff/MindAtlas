from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


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
        self.assertFalse(status.legacy_auto_completed)

    def test_stale_legacy_auto_completed_state_is_recovered_for_empty_system(self) -> None:
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
        persisted = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
            .first()
        )

        self.assertFalse(status.initialized)
        self.assertFalse(status.legacy_auto_completed)
        self.assertIsNone(persisted)

    def test_existing_ai_configuration_auto_completes_legacy_state(self) -> None:
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

        self.assertTrue(status.initialized)
        self.assertTrue(status.legacy_auto_completed)
        self.assertIsNotNone(setting)

    def test_customized_entry_types_auto_complete_legacy_state(self) -> None:
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

        self.assertTrue(status.initialized)
        self.assertTrue(status.legacy_auto_completed)

    def test_initialize_system_writes_models_bindings_and_defaults(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
        from app.assistant_config.models import AssistantSkill, AssistantSystemBehaviorBinding
        from app.config import get_settings
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
        self.assertEqual(len(models), 2)
        self.assertEqual(len(bindings), 3)
        llm_models = [item for item in models if item.model_type == "llm"]
        self.assertEqual(len(llm_models), 1)
        self.assertTrue(all(item.llm_model_id == llm_models[0].id for item in bindings))
        lightrag_binding = next(item for item in bindings if item.component == "lightrag")
        embedding_models = [item for item in models if item.model_type == "embedding"]
        self.assertEqual(len(embedding_models), 1)
        expected_embedding_name = (
            str(get_settings().lightrag_embedding_model or "").strip() or "text-embedding-3-small"
        )
        self.assertEqual(embedding_models[0].name, expected_embedding_name)
        self.assertEqual(lightrag_binding.embedding_model_id, embedding_models[0].id)
        self.assertEqual(entry_types["KNOWLEDGE"].name, "Knowledge")
        self.assertIn("CUSTOM_TYPE_1", entry_types)
        self.assertEqual(relation_types["BELONGS_TO"].name, "Belongs To")
        self.assertGreater(self.db.query(AssistantSkill).filter(AssistantSkill.is_system.is_(True)).count(), 0)
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
        from app.system_settings.schemas import RuntimeConfigPayloadRequest

        request = self._make_request(locale="zh")
        request.runtime_config = RuntimeConfigPayloadRequest.model_validate(
            {
                "knowledgeGraph": {
                    "summaryLanguage": "Chinese",
                    "embeddingModelName": "text-embedding-3-large",
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

        self.assertEqual(len(models), 2)
        self.assertEqual(len(embedding_models), 1)
        self.assertEqual(embedding_models[0].name, "text-embedding-3-large")
        self.assertIsNotNone(lightrag_binding)
        self.assertEqual(lightrag_binding.embedding_model_id, embedding_models[0].id)
        self.assertIsNotNone(knowledge_graph_setting)
        self.assertEqual(knowledge_graph_setting.value_json["summaryLanguage"], "Chinese")
        self.assertEqual(knowledge_graph_setting.value_json["rerankModel"], "bge-reranker-v2-m3")
        self.assertEqual(knowledge_graph_setting.value_json["rerankHost"], "https://rerank.example/v1")
        self.assertEqual(knowledge_graph_setting.value_json["rerankRequestFormat"], "standard")
        self.assertIn("rerankApiKeyEncrypted", knowledge_graph_setting.value_json)
        self.assertNotIn("rerankApiKey", knowledge_graph_setting.value_json)
        self.assertIsNotNone(document_parsing_setting)
        self.assertEqual(document_parsing_setting.value_json["ocrLangs"], "zh,en")

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


if __name__ == "__main__":
    unittest.main()
