"""Plan 09 Task 8 — evaluation HTTP mount, auth, and client-authored gate rejection."""

from __future__ import annotations

import os
import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.evaluation.router import (  # noqa: E402
    PLAN09_EVAL_PREFIX,
    mount_skill_eval_router,
    skill_eval_router,
)
from app.assistant.skills.admin_router import TRUSTED_MOUNT_ENV  # noqa: E402
from app.assistant.skills.router import skill_package_router  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from tests._db import make_session  # noqa: E402


def _digest(ch: str = "a") -> str:
    return (ch * 64)[:64]


class SkillEvalApiMountTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(TRUSTED_MOUNT_ENV, None)
        os.environ.pop("APP_ENV", None)

    def _client(self, *, mount: bool) -> tuple[TestClient, object]:
        app = FastAPI()
        register_exception_handlers(app)
        session = make_session()

        def _override_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        app.include_router(skill_package_router)
        if mount:
            os.environ[TRUSTED_MOUNT_ENV] = "1"
            mounted = mount_skill_eval_router(app, app_env="development")
            self.assertTrue(mounted)
        else:
            os.environ.pop(TRUSTED_MOUNT_ENV, None)
            mounted = mount_skill_eval_router(app, app_env="production")
            self.assertFalse(mounted)
        return TestClient(app), session

    def test_openapi_absent_when_unmounted(self) -> None:
        client, _session = self._client(mount=False)
        paths = (client.get("/openapi.json").json().get("paths") or {})
        for path in paths:
            self.assertNotIn("/skill-eval", path)
            self.assertNotIn(PLAN09_EVAL_PREFIX, path)

    def test_openapi_present_when_trusted_mount(self) -> None:
        client, _session = self._client(mount=True)
        paths = client.get("/openapi.json").json().get("paths") or {}
        eval_paths = [p for p in paths if PLAN09_EVAL_PREFIX in p or "/skill-eval" in p]
        self.assertGreaterEqual(len(eval_paths), 4)
        # No DELETE on eval routes.
        for path, methods in paths.items():
            if "/skill-eval" not in path:
                continue
            self.assertNotIn("delete", {m.lower() for m in methods.keys()})

    def test_eval_routes_require_principal_when_mounted(self) -> None:
        client, _session = self._client(mount=True)
        r = client.get(f"{PLAN09_EVAL_PREFIX}/datasets")
        self.assertIn(r.status_code, {401, 403}, r.text)

        r2 = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            json={
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "subjectContentDigest": _digest("1"),
                "subjectBindingDigest": _digest("2"),
                "mode": "interactive_scripted",
            },
        )
        self.assertIn(r2.status_code, {401, 403}, r2.text)

    def test_client_authored_gate_decision_fields_rejected(self) -> None:
        client, _session = self._client(mount=True)
        headers = {
            "X-MindAtlas-Operator-Id": "operator-task8",
            "X-MindAtlas-Operator-Role": "operator",
        }
        body = {
            "requestId": str(uuid.uuid4()),
            "action": "skill_publish",
            "subjectAggregateId": str(uuid.uuid4()),
            "subjectVersionId": str(uuid.uuid4()),
            "qualifyingEvalRunIds": [str(uuid.uuid4())],
            # Forbidden client-authored decision / closure fields:
            "passed": True,
            "decision": "passed",
            "metrics": {"score": 1.0},
            "assertions": [{"code": "x", "passed": True}],
            "subject": {
                "catalogDigest": _digest("k"),
            },
        }
        r = client.post(f"{PLAN09_EVAL_PREFIX}/gates", json=body, headers=headers)
        self.assertIn(r.status_code, {422, 400}, r.text)
        # Must not create a gate with a client-supplied pass.
        self.assertNotIn('"decision":"passed"', r.text.replace(" ", ""))

    def test_gate_api_rejects_client_authored_subject(self) -> None:
        client, _session = self._client(mount=True)
        headers = {
            "X-MindAtlas-Operator-Id": "operator-task8",
            "X-MindAtlas-Operator-Role": "operator",
        }
        body = {
            "requestId": str(uuid.uuid4()),
            "action": "skill_publish",
            "subjectAggregateId": str(uuid.uuid4()),
            "subjectVersionId": str(uuid.uuid4()),
            "qualifyingEvalRunIds": [str(uuid.uuid4())],
            "subject": {"catalogDigest": "0" * 64},
        }
        r = client.post(f"{PLAN09_EVAL_PREFIX}/gates", json=body, headers=headers)
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_run_with_operator_headers(self) -> None:
        import base64
        import json

        client, session = self._client(mount=True)
        headers = {
            "X-MindAtlas-Operator-Id": "operator-task8",
            "X-MindAtlas-Operator-Role": "operator",
        }
        # Create a real package so server can resolve digests from the version row.
        name = f"eval-run-{uuid.uuid4().hex[:8]}"
        skill_md = (
            f"---\nname: {name}\n"
            "description: Evaluation run admission regression package for Plan 09.\n"
            "---\n\n# Body\n"
        )
        mindatlas = (
            "version: 1\n"
            "display_name: Eval\n"
            "legacy_aliases: []\n\n"
            "routing:\n  include_examples: []\n  exclude_examples: []\n  conflict_rules: []\n\n"
            "capabilities: []\n\n"
            "policy:\n  allowed_side_effects:\n    - read\n    - compute\n"
            "  max_skill_calls: 16\n  max_same_read_calls: 3\n"
            "  requires_terminal_output: true\n  terminal_text_allowed: true\n\n"
            "provider_aliases: {}\n"
        )
        created = client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(
                {
                    "skillMd": skill_md,
                    "mindatlasYaml": mindatlas,
                    "resources": [
                        {
                            "path": "references/a.md",
                            "contentBase64": base64.b64encode(b"a\n").decode("ascii"),
                        }
                    ],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        pkg = created.json()["data"]
        draft = pkg["draftVersion"]
        # Client may send garbage digests — server must recompute from version row.
        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": pkg["id"],
                "subjectVersionId": draft["id"],
                "subjectContentDigest": _digest("3"),
                "subjectBindingDigest": _digest("4"),
                "mode": "interactive_scripted",
                "datasetVersionIds": [],
                "isolationDigest": _digest("5"),
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["mode"], "interactive_scripted")
        run_id = data["id"]

        # Server must ignore client-authored digests and admit the shared
        # candidate-closure resolver's content/binding digests.
        from app.assistant.evaluation.models import AssistantSkillEvalRun
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure

        # Expire identity map so we re-read committed admission digests.
        session.expire_all()
        closure = resolve_skill_candidate_closure(
            session,
            package_id=uuid.UUID(pkg["id"]),
            version_id=uuid.UUID(draft["id"]),
            subject_kind="skill_draft",
        )
        run_row = session.get(AssistantSkillEvalRun, uuid.UUID(run_id))
        self.assertIsNotNone(run_row)
        assert run_row is not None
        self.assertEqual(run_row.subject_content_digest, closure.content_digest)
        self.assertEqual(run_row.subject_binding_digest, closure.binding_set_digest)
        self.assertNotEqual(run_row.subject_content_digest, _digest("3"))
        self.assertNotEqual(run_row.subject_binding_digest, _digest("4"))

        events = client.get(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/events",
            headers=headers,
            params={"afterSequence": 0},
        )
        self.assertEqual(events.status_code, 200, events.text)
        self.assertIn("items", events.json()["data"])

    def test_create_run_rejects_unknown_skill_version(self) -> None:
        client, _session = self._client(mount=True)
        headers = {
            "X-MindAtlas-Operator-Id": "operator-task8",
            "X-MindAtlas-Operator-Role": "operator",
        }
        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "subjectContentDigest": _digest("3"),
                "subjectBindingDigest": _digest("4"),
                "mode": "interactive_scripted",
                "isolationDigest": _digest("5"),
            },
        )
        self.assertEqual(r.status_code, 404, r.text)


class SkillEvalRouterContractTests(unittest.TestCase):
    def test_router_prefix_and_route_count(self) -> None:
        self.assertEqual(PLAN09_EVAL_PREFIX, "/api/assistant-config/skill-eval")
        self.assertGreaterEqual(len(skill_eval_router.routes), 6)


if __name__ == "__main__":
    unittest.main()
