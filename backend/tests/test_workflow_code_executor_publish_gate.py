from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class WorkflowCodeExecutorPublishGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    @staticmethod
    def _build_workflow_input(
        *,
        code: str,
        language: str = "python",
        input_bindings: dict[str, str] | None = None,
    ):
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                WorkflowNodeInput(
                    node_id="code_1",
                    node_type="code_executor",
                    label="Code",
                    config={
                        "language": language,
                        "code": code,
                        "entrypoint": "main",
                        "inputBindings": input_bindings if input_bindings is not None else {"arg1": "{{start.user_input}}", "arg2": ""},
                        "outputFields": [{"name": "result", "type": "string", "nullable": False}],
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_final",
                    node_type="output",
                    label="Output",
                    config={"outputMode": "text", "textTemplate": "{{code_1.response}}"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(
                    edge_id="e_start_code",
                    source_node_id="start",
                    target_node_id="code_1",
                    source_handle="output",
                    target_handle="input",
                ),
                WorkflowEdgeInput(
                    edge_id="e_code_output",
                    source_node_id="code_1",
                    target_node_id="output_final",
                    source_handle="output",
                    target_handle="input",
                ),
            ],
        )

    def test_publish_blocks_disallowed_import_and_keeps_published_pointer(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest, WorkflowPublishRequest
        from app.assistant_config.service import AssistantConfigService
        from app.common.exceptions import ApiException

        service = AssistantConfigService(self.db)
        workflow = service.create_workflow(
            AssistantWorkflowCreateRequest(
                name="code_gate_case",
                description="workflow for code publish gate",
                enabled=True,
            )
        )
        before_published_id = workflow.published_version_id
        before_versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .count()
        )

        request = WorkflowPublishRequest(
            workflow=self._build_workflow_input(
                code=(
                    "import os\n\n"
                    "def main(arg1: str, arg2: str):\n"
                    "    return {'result': f'{arg1}{arg2}'}\n"
                ),
                language="python",
            ),
            version_name="bad publish",
        )

        with self.assertRaises(ApiException) as ctx:
            service.publish_workflow(workflow.id, request)

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("publish blocked by validation", ctx.exception.message.lower())

        refreshed = service.get_workflow(workflow.id)
        self.assertEqual(refreshed.published_version_id, before_published_id)
        after_versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .count()
        )
        self.assertEqual(after_versions, before_versions)

    def test_publish_blocks_legacy_signature(self) -> None:
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest, WorkflowPublishRequest
        from app.assistant_config.service import AssistantConfigService
        from app.common.exceptions import ApiException

        service = AssistantConfigService(self.db)
        workflow = service.create_workflow(
            AssistantWorkflowCreateRequest(
                name="code_legacy_sig",
                description="workflow for legacy signature gate",
                enabled=True,
            )
        )

        with self.assertRaises(ApiException) as ctx:
            service.publish_workflow(
                workflow.id,
                WorkflowPublishRequest(
                    workflow=self._build_workflow_input(
                        code=(
                            "def main(inputs, context):\n"
                            "    return {'result': str(inputs)}\n"
                        ),
                        language="python",
                    ),
                    version_name="legacy signature",
                ),
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("signature must match inputbindings keys", ctx.exception.message.lower())

    def test_publish_blocks_signature_and_binding_mismatch(self) -> None:
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest, WorkflowPublishRequest
        from app.assistant_config.service import AssistantConfigService
        from app.common.exceptions import ApiException

        service = AssistantConfigService(self.db)
        workflow = service.create_workflow(
            AssistantWorkflowCreateRequest(
                name="code_binding_mismatch",
                description="workflow for signature and binding mismatch",
                enabled=True,
            )
        )

        with self.assertRaises(ApiException) as ctx:
            service.publish_workflow(
                workflow.id,
                WorkflowPublishRequest(
                    workflow=self._build_workflow_input(
                        code=(
                            "def main(arg1: str):\n"
                            "    return {'result': arg1}\n"
                        ),
                        language="python",
                        input_bindings={"text": "{{start.user_input}}"},
                    ),
                    version_name="binding mismatch",
                ),
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("signature must match inputbindings keys", ctx.exception.message.lower())

    def test_publish_succeeds_for_valid_code_executor_workflow(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest, WorkflowPublishRequest
        from app.assistant_config.service import AssistantConfigService

        service = AssistantConfigService(self.db)
        workflow = service.create_workflow(
            AssistantWorkflowCreateRequest(
                name="code_publish_ok",
                description="workflow for successful code publish",
                enabled=True,
            )
        )
        before_published_id = workflow.published_version_id

        published = service.publish_workflow(
            workflow.id,
            WorkflowPublishRequest(
                workflow=self._build_workflow_input(
                    code=(
                        "def main(text: str):\n"
                        "    return {'result': text}\n"
                    ),
                    language="python",
                    input_bindings={"text": "{{start.user_input}}"},
                ),
                version_name="good publish",
            ),
        )

        self.assertIsNotNone(published.published_version_id)
        self.assertNotEqual(published.published_version_id, before_published_id)
        versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .count()
        )
        self.assertGreaterEqual(versions, 2)
