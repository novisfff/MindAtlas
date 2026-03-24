from __future__ import annotations

import unittest
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant_config.router import router as assistant_config_router  # noqa: E402
from app.common.exceptions import ApiException, register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402


class _StubWorkflowCopilotService:
    response = None
    error: Exception | None = None
    last_db = None
    last_workflow_id = None
    last_request = None

    def __init__(self, db) -> None:  # noqa: ANN001
        type(self).last_db = db

    @classmethod
    def reset(cls) -> None:
        cls.response = None
        cls.error = None
        cls.last_db = None
        cls.last_workflow_id = None
        cls.last_request = None

    def respond(self, *, workflow_id, request):  # noqa: ANN001, ANN201
        type(self).last_workflow_id = workflow_id
        type(self).last_request = request
        if type(self).error is not None:
            raise type(self).error
        return type(self).response


class WorkflowCopilotRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        _StubWorkflowCopilotService.reset()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(assistant_config_router)
        app.dependency_overrides[get_db] = lambda: object()
        return app

    @staticmethod
    def _build_draft_payload() -> dict:
        return {
            "nodes": [
                {
                    "nodeId": "start",
                    "nodeType": "start",
                    "label": "Start",
                    "positionX": 120,
                    "positionY": 220,
                    "config": {
                        "inputMode": "text",
                        "memoryMode": "auto",
                        "structuredFields": [],
                        "sessionVars": [],
                    },
                }
            ],
            "edges": [],
            "viewport": None,
        }

    def test_copilot_route_accepts_camel_case_payload_and_returns_alias_response(self) -> None:
        from unittest.mock import patch

        workflow_id = str(uuid4())
        _StubWorkflowCopilotService.response = {
            "status": "proposal",
            "message": "我整理了一份修复建议。",
            "proposal": {
                "title": "Add output node",
                "summary": "补一个 output 节点，并连到 start。",
                "operations": [
                    {
                        "type": "add_node",
                        "node_type": "output",
                        "node_id": "output_1",
                        "label": "Output",
                        "config": {
                            "outputMode": "text",
                            "textTemplate": "{{start.user_input}}",
                        },
                    }
                ],
                "proposed_workflow": {
                    "nodes": [
                        *self._build_draft_payload()["nodes"],
                        {
                            "node_id": "output_1",
                            "node_type": "output",
                            "label": "Output",
                            "position_x": 420,
                            "position_y": 220,
                            "config": {
                                "output_mode": "text",
                                "text_template": "{{start.user_input}}",
                            },
                        },
                    ],
                    "edges": [],
                    "viewport": None,
                },
                "base_draft_hash": "base123",
                "proposed_draft_hash": "next456",
                "layout_recommendation": "autolayout",
                "validation": {
                    "valid": False,
                    "errors": [
                        {
                            "node_id": "output_1",
                            "message": "Need one more upstream node",
                        }
                    ],
                },
                "affected_node_ids": ["output_1"],
                "warnings": ["建议应用自动布局"],
            },
            "suggestions": ["接下来补一个 llm 节点"],
        }

        app = self._make_app()
        client = TestClient(app)
        body = {
            "mode": "fix_validation",
            "instruction": "修复当前输出节点问题",
            "draft": self._build_draft_payload(),
            "selection": {
                "scope": "selection",
                "nodeIds": ["start"],
                "edgeIds": [],
            },
            "conversation": [
                {"role": "user", "content": "帮我看看当前流程哪里不合理"}
            ],
            "validationContext": {
                "errors": [
                    {
                        "severity": "error",
                        "nodeId": "start",
                        "message": "缺少 output 节点",
                        "source": "backend",
                    }
                ],
                "warnings": [],
            },
            "testRunContext": {
                "selectedRunId": "run_001",
                "result": {"status": "failed"},
                "trace": [{"event": "node_end", "nodeId": "start"}],
                "raw": [{"event": "run_end", "status": "failed"}],
            },
        }

        with patch("app.assistant_config.router.WorkflowCopilotService", _StubWorkflowCopilotService):
            response = client.post(f"/api/assistant-config/workflows/{workflow_id}/copilot/respond", json=body)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["status"], "proposal")
        self.assertEqual(payload["data"]["proposal"]["layoutRecommendation"], "autolayout")
        self.assertEqual(payload["data"]["proposal"]["baseDraftHash"], "base123")
        self.assertEqual(payload["data"]["proposal"]["proposedDraftHash"], "next456")
        self.assertEqual(payload["data"]["proposal"]["affectedNodeIds"], ["output_1"])
        self.assertEqual(payload["data"]["proposal"]["validation"]["errors"][0]["nodeId"], "output_1")
        self.assertEqual(payload["data"]["proposal"]["proposedWorkflow"]["nodes"][1]["nodeId"], "output_1")

        request = _StubWorkflowCopilotService.last_request
        self.assertIsNotNone(request)
        self.assertEqual(str(_StubWorkflowCopilotService.last_workflow_id), workflow_id)
        self.assertEqual(request.selection.scope, "selection")
        self.assertEqual(request.selection.node_ids, ["start"])
        self.assertEqual(request.validation_context.errors[0].node_id, "start")
        self.assertEqual(request.test_run_context.selected_run_id, "run_001")
        self.assertEqual(request.conversation[0].content, "帮我看看当前流程哪里不合理")

    def test_copilot_route_returns_api_exception_payload(self) -> None:
        from unittest.mock import patch

        _StubWorkflowCopilotService.error = ApiException(
            status_code=422,
            code=42263,
            message="Top-level copilot operations cannot target containerId",
        )

        app = self._make_app()
        client = TestClient(app)
        workflow_id = str(uuid4())

        with patch("app.assistant_config.router.WorkflowCopilotService", _StubWorkflowCopilotService):
            response = client.post(
                f"/api/assistant-config/workflows/{workflow_id}/copilot/respond",
                json={
                    "mode": "generate",
                    "instruction": "帮我生成一个流程",
                    "draft": self._build_draft_payload(),
                },
            )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 42263)
        self.assertEqual(payload["message"], "Top-level copilot operations cannot target containerId")

    def test_copilot_route_rejects_invalid_container_selection_payload(self) -> None:
        from unittest.mock import patch

        app = self._make_app()
        client = TestClient(app)
        workflow_id = str(uuid4())

        with patch("app.assistant_config.router.WorkflowCopilotService", _StubWorkflowCopilotService):
            response = client.post(
                f"/api/assistant-config/workflows/{workflow_id}/copilot/respond",
                json={
                    "mode": "edit_selection",
                    "instruction": "修改容器内部流程",
                    "draft": self._build_draft_payload(),
                    "selection": {
                        "scope": "container",
                        "nodeIds": ["start"],
                        "edgeIds": [],
                    },
                },
            )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 42200)
        self.assertIsNone(_StubWorkflowCopilotService.last_request)
        details = payload.get("data") or []
        self.assertTrue(any("container_id" in str(item) or "containerId" in str(item) for item in details))


if __name__ == "__main__":
    unittest.main()
