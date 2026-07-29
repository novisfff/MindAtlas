from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


# Clean-only init encrypts the AI credential; tests must supply a Fernet key.
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

bootstrap_backend_imports()
reset_caches()


class RuntimeConfigServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from app.scheduler import shutdown_scheduler

        shutdown_scheduler()
        self.db = make_session()

    def tearDown(self) -> None:
        from app.scheduler import shutdown_scheduler

        shutdown_scheduler()
        self.db.close()

    def _make_initialization_request(self, *, locale: str = "zh"):
        from app.system_settings.schemas import InitializeSystemRequest

        return InitializeSystemRequest.model_validate(
            {
                "locale": locale,
                # Exact Operator password (Task 6 clean-only init); never log/echo.
                "operatorPassword": "correct horse battery",
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
                        "name": "知识" if locale == "zh" else "Knowledge",
                        "description": "知识点" if locale == "zh" else "Concepts",
                        "color": "#3B82F6",
                        "icon": "book",
                        "graphEnabled": True,
                        "aiEnabled": True,
                        "enabled": True,
                        "origin": "default",
                    }
                ],
            }
        )

    def test_initialized_system_rejects_locked_lightrag_field_changes(self) -> None:
        from app.common.exceptions import ApiException
        from app.system_settings.initialization_service import SystemInitializationService
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeKnowledgeGraphConfigRequest

        SystemInitializationService(self.db).initialize_system(
            self._make_initialization_request(locale="zh")
        )

        service = SystemRuntimeConfigService(self.db)
        current = service.get_runtime_config_response().knowledge_graph

        with self.assertRaises(ApiException) as summary_error:
            service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {"summaryLanguage": "English"}
                )
            )
        self.assertEqual(summary_error.exception.code, 40985)

        with self.assertRaises(ApiException) as embedding_error:
            service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {"embeddingModelName": "text-embedding-3-large"}
                )
            )
        self.assertEqual(embedding_error.exception.code, 40987)

        with self.assertRaises(ApiException) as enabled_error:
            service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {"enabled": not current.enabled}
                )
            )
        self.assertEqual(enabled_error.exception.code, 40985)

        with self.assertRaises(ApiException) as embedding_dim_error:
            service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {"embeddingDim": current.embedding_dim + 1}
                )
            )
        self.assertEqual(embedding_dim_error.exception.code, 40985)

        with self.assertRaises(ApiException) as neo4j_error:
            service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {
                        "neo4jUri": "bolt://127.0.0.1:8765"
                        if current.neo4j_uri != "bolt://127.0.0.1:8765"
                        else "bolt://localhost:7687"
                    }
                )
            )
        self.assertEqual(neo4j_error.exception.code, 40985)

    def test_legacy_initialized_system_rejects_embedding_model_changes(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
        from app.system_settings.models import AppSetting
        from app.common.exceptions import ApiException
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeKnowledgeGraphConfigRequest

        credential = AiCredential(
            name="Existing Provider",
            base_url="https://api.openai.com/v1",
            api_key_encrypted="token",
            api_key_hint="****1234",
        )
        self.db.add(credential)
        self.db.flush()

        llm_model = AiModel(
            credential_id=credential.id,
            name="gpt-4.1-mini",
            model_type="llm",
        )
        self.db.add(llm_model)
        self.db.flush()

        self.db.add(
            AiComponentBinding(
                component="assistant",
                llm_model_id=llm_model.id,
            )
        )
        self.db.add(
            AppSetting(
                key="system_initialization_state",
                value_json={
                    "initialized": True,
                    "locale": "zh",
                    "version": 1,
                    "source": "legacy_auto_completed",
                },
            )
        )
        self.db.commit()

        service = SystemRuntimeConfigService(self.db)
        with self.assertRaises(ApiException) as locked_error:
            service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {"embeddingModelName": "text-embedding-3-small"}
                )
            )
        self.assertEqual(locked_error.exception.code, 40987)

    def test_initialized_system_can_fill_empty_embedding_host_once(self) -> None:
        from app.common.exceptions import ApiException
        from app.config import get_settings
        from app.system_settings.initialization_service import SystemInitializationService
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeKnowledgeGraphConfigRequest
        from unittest.mock import patch

        patched_settings = get_settings().model_copy(update={"lightrag_embedding_host": ""})

        with patch(
            "app.system_settings.runtime_config_service.get_settings",
            return_value=patched_settings,
        ):
            SystemInitializationService(self.db).initialize_system(
                self._make_initialization_request(locale="zh")
            )

            service = SystemRuntimeConfigService(self.db)
            first_response = service.update_knowledge_graph_config(
                RuntimeKnowledgeGraphConfigRequest.model_validate(
                    {"embeddingHost": "https://api.openai.com/v1"}
                )
            )

            self.assertEqual(first_response.embedding_host, "https://api.openai.com/v1")

            with self.assertRaises(ApiException) as locked_error:
                service.update_knowledge_graph_config(
                    RuntimeKnowledgeGraphConfigRequest.model_validate(
                        {"embeddingHost": "https://example.com/v1"}
                    )
                )
            self.assertEqual(locked_error.exception.code, 40985)

    def test_initialized_system_rejects_docling_worker_toggle_changes(self) -> None:
        from app.common.exceptions import ApiException
        from app.system_settings.initialization_service import SystemInitializationService
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeDocumentParsingConfigRequest

        SystemInitializationService(self.db).initialize_system(
            self._make_initialization_request(locale="zh")
        )

        service = SystemRuntimeConfigService(self.db)
        current = service.get_runtime_config_response().document_parsing

        with self.assertRaises(ApiException) as worker_error:
            service.update_document_parsing_config(
                RuntimeDocumentParsingConfigRequest.model_validate(
                    {"workerEnabled": not current.worker_enabled}
                )
            )
        self.assertEqual(worker_error.exception.code, 40985)

    def test_update_knowledge_graph_config_explicit_blank_clears_rerank_fields_and_secret(self) -> None:
        from app.system_settings.models import AppSetting
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeKnowledgeGraphConfigRequest

        service = SystemRuntimeConfigService(self.db)
        service.update_knowledge_graph_config(
            RuntimeKnowledgeGraphConfigRequest.model_validate(
                {
                    "rerankModel": "bge-reranker-v2-m3",
                    "rerankHost": "https://rerank.example/v1",
                    "rerankApiKey": "rerank-secret-123",
                    "rerankRequestFormat": "standard",
                }
            )
        )

        response = service.update_knowledge_graph_config(
            RuntimeKnowledgeGraphConfigRequest.model_validate(
                {
                    "rerankModel": "",
                    "rerankHost": "",
                    "rerankApiKey": "",
                    "rerankRequestFormat": "",
                }
            )
        )

        persisted = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == "runtime_knowledge_graph_config")
            .first()
        )

        self.assertIsNone(persisted)
        self.assertNotEqual(response.source, "app_config")

    def test_sync_scheduler_respects_resolved_toggle(self) -> None:
        from app.scheduler import get_scheduler_job_ids, is_scheduler_running, shutdown_scheduler, sync_scheduler

        shutdown_scheduler()
        with patch(
            "app.scheduler.resolve_runtime_automation_config",
            return_value=SimpleNamespace(scheduler_enabled=False),
        ):
            sync_scheduler()
            self.assertFalse(is_scheduler_running())
            self.assertEqual(get_scheduler_job_ids(), [])

        with patch(
            "app.scheduler.resolve_runtime_automation_config",
            return_value=SimpleNamespace(scheduler_enabled=True),
        ):
            sync_scheduler()
            self.assertTrue(is_scheduler_running())
            self.assertEqual(get_scheduler_job_ids(), ["monthly_report", "weekly_report"])
            sync_scheduler()
            self.assertEqual(get_scheduler_job_ids(), ["monthly_report", "weekly_report"])

    def test_update_automation_config_hot_applies_scheduler_and_sets_restart_free_response(self) -> None:
        from app.scheduler import get_scheduler_job_ids, is_scheduler_running
        from app.system_settings.runtime_config_service import SystemRuntimeConfigService
        from app.system_settings.schemas import RuntimeAutomationConfigRequest

        service = SystemRuntimeConfigService(self.db)

        with patch(
            "app.scheduler.resolve_runtime_automation_config",
            return_value=SimpleNamespace(scheduler_enabled=True),
        ):
            response = service.update_automation_config(
                RuntimeAutomationConfigRequest.model_validate({"schedulerEnabled": True})
            )

        self.assertTrue(response.scheduler_enabled)
        self.assertTrue(response.configured)
        self.assertFalse(response.restart_required)
        self.assertTrue(is_scheduler_running())
        self.assertEqual(get_scheduler_job_ids(), ["monthly_report", "weekly_report"])

        with patch(
            "app.scheduler.resolve_runtime_automation_config",
            return_value=SimpleNamespace(scheduler_enabled=False),
        ):
            response = service.update_automation_config(
                RuntimeAutomationConfigRequest.model_validate({"schedulerEnabled": False})
            )

        self.assertFalse(response.scheduler_enabled)
        self.assertTrue(response.configured)
        self.assertFalse(response.restart_required)
        self.assertFalse(is_scheduler_running())
        self.assertEqual(get_scheduler_job_ids(), [])


if __name__ == "__main__":
    unittest.main()
