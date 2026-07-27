"""Plan 10 Task 9 — Deploy B1 legacy runtime/UI cleanup architecture guards.

Fail closed: production chat admission must not compose IntentRouter/Supervisor
for new traffic, and legacy Skill admin mutations must not remain writable.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
FRONTEND_APP = BACKEND_ROOT.parent / "frontend" / "src" / "app" / "App.tsx"


def _module_source(module: object) -> str:
    path = inspect.getsourcefile(module)
    assert path is not None, f"no source for {module!r}"
    return Path(path).read_text(encoding="utf-8")


def _top_level_import_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                names.add(alias.name)
                if module:
                    names.add(f"{module}.{alias.name}")
    return names


class LegacyCleanupArchitectureTests(unittest.TestCase):
    def test_assistant_service_module_does_not_import_intent_router_or_supervisor(self) -> None:
        from app.assistant import service as service_mod

        source = _module_source(service_mod)
        imports = _top_level_import_names(source)
        forbidden = {
            "IntentRouter",
            "SkillRouter",
            "SupervisorGraph",
            "build_supervisor_graph",
            "SupervisorState",
            "app.assistant.orchestration.intent_router",
            "app.assistant.orchestration.supervisor_graph",
            "app.assistant.orchestration.supervisor_state",
            "app.assistant.orchestration.agent_runtime",
        }
        hit = forbidden.intersection(imports)
        self.assertFalse(hit, f"assistant.service top-level imports forbidden legacy symbols: {hit}")
        # chat_stream must fail closed rather than daemon-spawn legacy Supervisor.
        chat_src = inspect.getsource(service_mod.AssistantService.chat_stream)
        self.assertIn("Main Agent runtime is required", chat_src)
        self.assertNotIn("_start_background_run", chat_src)

    def test_admission_module_does_not_import_supervisor_or_intent_router(self) -> None:
        from app.assistant.durable import admission as admission_mod

        source = _module_source(admission_mod)
        imports = _top_level_import_names(source)
        forbidden = {
            "IntentRouter",
            "SkillRouter",
            "SupervisorGraph",
            "build_supervisor_graph",
            "app.assistant.orchestration.intent_router",
            "app.assistant.orchestration.supervisor_graph",
            "app.assistant.orchestration.agent_runtime",
        }
        hit = forbidden.intersection(imports)
        self.assertFalse(hit, f"admission imports forbidden legacy symbols: {hit}")

    def test_legacy_skill_admin_routes_are_absent(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.assistant_config.router import router as assistant_config_router
        from app.common.exceptions import register_exception_handlers
        from app.database import get_db
        from tests._db import make_session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(assistant_config_router)

        db = make_session()

        def _override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        client = TestClient(app)

        schema = client.get("/openapi.json").json()
        self.assertNotIn("/api/assistant-config/skills", schema["paths"])
        self.assertFalse(
            any(path.startswith("/api/assistant-config/skills") for path in schema["paths"]),
            schema["paths"].keys(),
        )

        for method, path, body in (
            ("get", "/api/assistant-config/skills", None),
            ("get", "/api/assistant-config/skills/00000000-0000-0000-0000-000000000001", None),
            ("post", "/api/assistant-config/skills", {"name": "x", "description": "y"}),
            ("put", "/api/assistant-config/skills/00000000-0000-0000-0000-000000000001", {"name": "x"}),
            ("delete", "/api/assistant-config/skills/00000000-0000-0000-0000-000000000001", None),
            ("post", "/api/assistant-config/skills/00000000-0000-0000-0000-000000000001/reset", {"confirm": True}),
            ("post", "/api/assistant-config/skills/reset-all", {"confirm": True}),
            (
                "put",
                "/api/assistant-config/skills/00000000-0000-0000-0000-000000000001/workflow",
                {"nodes": [], "edges": []},
            ),
        ):
            kwargs = {"json": body} if body is not None else {}
            resp = getattr(client, method)(path, **kwargs)
            self.assertEqual(
                resp.status_code,
                404,
                f"{method.upper()} {path} expected 404, got {resp.status_code}: {resp.text}",
            )

    def test_frontend_app_does_not_mount_skill_settings_page(self) -> None:
        self.assertTrue(FRONTEND_APP.is_file(), f"missing {FRONTEND_APP}")
        text = FRONTEND_APP.read_text(encoding="utf-8")
        self.assertNotIn("pages/SkillSettings", text)
        self.assertNotIn("'SkillSettings'", text)
        self.assertNotIn('"SkillSettings"', text)
        # Old bookmarks may redirect, but must not render the legacy page.
        self.assertIn('path="/settings/assistant-skills"', text)
        self.assertIn('Navigate to="/settings/universal-skills"', text)
        self.assertIn("/settings/universal-skills", text)

    def test_legacy_orchestration_modules_removed(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app" / "assistant" / "orchestration"
        for name in (
            "intent_router.py",
            "supervisor_graph.py",
            "supervisor_state.py",
            "agent_runtime.py",
        ):
            self.assertFalse((root / name).exists(), f"{name} should be removed")
        # Package must not export AssistantAgent/SkillRouter
        import app.assistant.orchestration as orch

        for attr in ("AssistantAgent", "SkillRouter"):
            with self.assertRaises(AttributeError):
                getattr(orch, attr)

    def test_human_loop_runtime_is_fail_closed(self) -> None:
        from uuid import uuid4

        from app.assistant.workflow.human_approval_runtime import (
            HumanLoopRuntime,
            LegacyHitlRemoved,
            cancel_pending_human_approvals_for_run,
            list_pending_approvals_for_conversation,
            submit_human_approval_decision,
        )
        from app.common.exceptions import ApiException

        rt = HumanLoopRuntime()
        with self.assertRaises(LegacyHitlRemoved):
            rt.create_and_wait(node_id="n1", node_label="n")
        self.assertEqual(list_pending_approvals_for_conversation(None, uuid4()), [])
        self.assertEqual(cancel_pending_human_approvals_for_run(None, run_id="r"), [])
        with self.assertRaises(ApiException) as ctx:
            submit_human_approval_decision(None, approval_id=uuid4(), decision="approve")
        self.assertEqual(ctx.exception.status_code, 410)

    def test_start_background_run_is_fail_closed(self) -> None:
        from uuid import uuid4

        from app.assistant.service import AssistantService

        svc = AssistantService(db=None)  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError) as ctx:
            svc._start_background_run(run_id=uuid4(), stream_output=False, locale="en")
        self.assertIn("Legacy chat daemon is removed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
