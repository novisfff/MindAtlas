"""API contract tests for Main Agent Profile routes (Plan 01 Task 9)."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.skills.router import (  # noqa: E402
    main_agent_profile_router,
    skill_package_router,
)
from app.assistant.skills.schemas import default_main_agent_profile_snapshot  # noqa: E402
from app.assistant_config.router import router as assistant_config_router  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from tests._db import make_session  # noqa: E402


EXPECTED_MAIN_AGENT_PATHS = {
    ("GET", "/api/assistant-config/main-agent-profiles/default"),
    ("PUT", "/api/assistant-config/main-agent-profiles/default/draft"),
    ("GET", "/api/assistant-config/main-agent-profiles/default/versions"),
    ("POST", "/api/assistant-config/main-agent-profiles/default/publish"),
}


def _snapshot_payload(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(default_main_agent_profile_snapshot().normalized_payload())
    payload.update(overrides)
    return payload


class MainAgentProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(assistant_config_router)
        app.include_router(skill_package_router)
        app.include_router(main_agent_profile_router)

        def _override_get_db():  # noqa: ANN001
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.db.close()

    def test_openapi_exposes_exact_main_agent_paths_once(self) -> None:
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        found: set[tuple[str, str]] = set()
        for path, methods in paths.items():
            for method in methods:
                if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    found.add((method.upper(), path))
        for expected in EXPECTED_MAIN_AGENT_PATHS:
            self.assertIn(expected, found, msg=f"missing OpenAPI path {expected}")
        self.assertNotIn(
            "/api/assistant-config/main-agent-profiles/main-agent-profiles/default",
            paths,
        )
        # No update/delete for immutable versions.
        for path in paths:
            if "main-agent-profiles" not in path:
                continue
            methods = {m.upper() for m in paths[path]}
            self.assertNotIn("DELETE", methods)
            if path.endswith("/versions") or "/versions/" in path:
                self.assertNotIn("PUT", methods)
                self.assertNotIn("PATCH", methods)

    def test_get_default_bootstrap_and_runtime_disabled(self) -> None:
        resp = self.client.get("/api/assistant-config/main-agent-profiles/default")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload["success"])
        data = payload["data"]
        self.assertEqual(data["profileKey"], "default")
        self.assertTrue(data["isDefault"])
        self.assertFalse(data["runtimeEnabled"])
        self.assertIsNotNone(data["draftVersion"])
        self.assertIsNone(data["publishedVersion"])
        # No endpoint activates the Main Agent loop.
        self.assertNotIn("activate", json.dumps(data).lower())
        self.assertNotIn("runtime_enabled", json.dumps(data))  # camelCase only

    def test_save_draft_list_versions_publish(self) -> None:
        # Ensure default exists.
        profile = self.client.get(
            "/api/assistant-config/main-agent-profiles/default"
        ).json()["data"]
        self.assertFalse(profile["runtimeEnabled"])

        snap = _snapshot_payload(basePrompt="Updated main agent prompt for draft.")
        saved = self.client.put(
            "/api/assistant-config/main-agent-profiles/default/draft",
            content=json.dumps({"snapshot": snap, "versionName": "admin-draft"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        draft = saved.json()["data"]
        self.assertEqual(draft["versionSource"], "save")
        self.assertEqual(draft["origin"], "api")
        draft_id = draft["id"]

        versions = self.client.get(
            "/api/assistant-config/main-agent-profiles/default/versions",
            params={"versionSource": "save", "limit": 50, "offset": 0},
        )
        self.assertEqual(versions.status_code, 200)
        page = versions.json()["data"]
        self.assertIn("items", page)
        self.assertIn("total", page)
        self.assertIn("limit", page)
        self.assertIn("offset", page)
        self.assertGreaterEqual(page["total"], 1)
        self.assertTrue(any(item["id"] == draft_id for item in page["items"]))

        published = self.client.post(
            "/api/assistant-config/main-agent-profiles/default/publish",
            json={"draftVersionId": draft_id},
        )
        self.assertEqual(published.status_code, 200, published.text)
        pub = published.json()["data"]
        self.assertEqual(pub["versionSource"], "publish")
        self.assertEqual(pub["sourceDraftVersionId"], draft_id)

        after = self.client.get(
            "/api/assistant-config/main-agent-profiles/default"
        ).json()["data"]
        self.assertIsNotNone(after["publishedVersion"])
        self.assertFalse(after["runtimeEnabled"])

    def test_invalid_snapshot_fields_rejected(self) -> None:
        bad = _snapshot_payload(schemaVersion=2)
        resp = self.client.put(
            "/api/assistant-config/main-agent-profiles/default/draft",
            content=json.dumps({"snapshot": bad}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], 42294)

        unknown = _snapshot_payload()
        unknown["unknownField"] = "nope"
        resp2 = self.client.put(
            "/api/assistant-config/main-agent-profiles/default/draft",
            content=json.dumps({"snapshot": unknown}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp2.status_code, 422)
        self.assertEqual(resp2.json()["code"], 42294)

    def test_publish_unknown_draft_not_found(self) -> None:
        self.client.get("/api/assistant-config/main-agent-profiles/default")
        resp = self.client.post(
            "/api/assistant-config/main-agent-profiles/default/publish",
            json={"draftVersionId": str(uuid4())},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], 40493)

    def test_no_activate_or_version_mutation_routes(self) -> None:
        schema = self.client.get("/openapi.json").json()["paths"]
        main_paths = [p for p in schema if "main-agent-profiles" in p]
        joined = " ".join(main_paths).lower()
        self.assertNotIn("activate", joined)
        self.assertNotIn("enable", joined)
        # Only the four locked relative routes under /default.
        self.assertEqual(len(main_paths), 4)

    def test_legacy_skills_still_present(self) -> None:
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/api/assistant-config/skills", schema["paths"])
        resp = self.client.get("/api/assistant-config/skills")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], 0)
        self.assertIsInstance(payload["data"], list)


if __name__ == "__main__":
    unittest.main()
