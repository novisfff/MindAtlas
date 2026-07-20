"""Plan 09 P1: draft save CAS + resource preservation on content-only edits."""

from __future__ import annotations

import base64
import json
import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.skills.router import skill_package_router  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from tests._db import make_session  # noqa: E402


def _skill_md(name: str = "cas-skill") -> str:
    return (
        f"---\nname: {name}\n"
        "description: CAS and resource preservation regression skill for admin saves and drafts.\n"
        "---\n\n# Body\n"
    )


def _mindatlas_yaml() -> str:
    return (
        "version: 1\n"
        "display_name: CAS Skill\n"
        "legacy_aliases: []\n"
        "\n"
        "routing:\n"
        "  include_examples: []\n"
        "  exclude_examples: []\n"
        "  conflict_rules: []\n"
        "\n"
        "capabilities: []\n"
        "\n"
        "policy:\n"
        "  allowed_side_effects:\n"
        "    - read\n"
        "    - compute\n"
        "  max_skill_calls: 16\n"
        "  max_same_read_calls: 3\n"
        "  requires_terminal_output: true\n"
        "  terminal_text_allowed: true\n"
        "\n"
        "provider_aliases: {}\n"
    )


def _create_with_resource(client: TestClient, *, name: str) -> dict:
    body = {
        "skillMd": _skill_md(name),
        "mindatlasYaml": _mindatlas_yaml(),
        "resources": [
            {
                "path": "references/guide.md",
                "contentBase64": base64.b64encode(b"# Guide\nkeep-me\n").decode("ascii"),
            },
            {
                "path": "scripts/helper.sh",
                "contentBase64": base64.b64encode(b"#!/bin/sh\necho hi\n").decode(
                    "ascii"
                ),
            },
        ],
        "versionName": "v1",
    }
    r = client.post(
        "/api/assistant-config/skill-packages",
        content=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


class DraftCasAndResourceTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.session = session
        self.client = TestClient(app)

    def test_omitted_resources_preserves_previous_draft_resources(self) -> None:
        name = f"keep-res-{uuid.uuid4().hex[:8]}"
        created = _create_with_resource(self.client, name=name)
        package_id = created["id"]
        rev = created["aggregateRevision"]
        draft_id = created["draftVersion"]["id"]

        # Content-only edit: omit resources field entirely.
        save = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(
                {
                    "skillMd": _skill_md(name).replace("# Body", "# Body edited"),
                    "mindatlasYaml": _mindatlas_yaml(),
                    "versionName": "v2",
                    "expectedAggregateRevision": rev,
                    "requestId": f"save-{uuid.uuid4().hex[:8]}",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(save.status_code, 200, save.text)
        new_draft = save.json()["data"]
        self.assertNotEqual(new_draft["id"], draft_id)

        meta = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{new_draft['id']}"
        )
        self.assertEqual(meta.status_code, 200, meta.text)
        resources = meta.json()["data"]["resources"]
        paths = sorted(r["path"] for r in resources)
        self.assertEqual(paths, ["references/guide.md", "scripts/helper.sh"])

        # Bytes still available via resource endpoint.
        blob = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/"
            f"{new_draft['id']}/resources/references/guide.md"
        )
        self.assertEqual(blob.status_code, 200, blob.text)
        self.assertIn(b"keep-me", blob.content)

    def test_explicit_empty_resources_clears(self) -> None:
        name = f"clear-res-{uuid.uuid4().hex[:8]}"
        created = _create_with_resource(self.client, name=name)
        package_id = created["id"]
        rev = created["aggregateRevision"]
        save = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(
                {
                    "skillMd": _skill_md(name),
                    "mindatlasYaml": _mindatlas_yaml(),
                    "resources": [],
                    "expectedAggregateRevision": rev,
                    "requestId": f"clear-{uuid.uuid4().hex[:8]}",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(save.status_code, 200, save.text)
        new_id = save.json()["data"]["id"]
        meta = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{new_id}"
        )
        self.assertEqual(meta.json()["data"]["resources"], [])

    def test_stale_aggregate_revision_conflicts(self) -> None:
        name = f"cas-{uuid.uuid4().hex[:8]}"
        created = _create_with_resource(self.client, name=name)
        package_id = created["id"]
        rev = created["aggregateRevision"]

        ok = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(
                {
                    "skillMd": _skill_md(name).replace("# Body", "# A"),
                    "expectedAggregateRevision": rev,
                    "requestId": "req-a",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(ok.status_code, 200, ok.text)

        stale = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(
                {
                    "skillMd": _skill_md(name).replace("# Body", "# B"),
                    "expectedAggregateRevision": rev,  # stale
                    "requestId": "req-b",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(stale.status_code, 409, stale.text)

    def test_identical_request_id_retry_is_idempotent(self) -> None:
        name = f"idem-{uuid.uuid4().hex[:8]}"
        created = _create_with_resource(self.client, name=name)
        package_id = created["id"]
        rev = created["aggregateRevision"]
        body = {
            "skillMd": _skill_md(name).replace("# Body", "# idem"),
            "expectedAggregateRevision": rev,
            "requestId": "same-req",
        }
        first = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])


if __name__ == "__main__":
    unittest.main()
