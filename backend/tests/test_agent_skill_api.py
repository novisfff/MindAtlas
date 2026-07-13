"""API contract tests for Agent Skill package routes (Plan 01 Task 9)."""

from __future__ import annotations

import base64
import io
import json
import unittest
import zipfile
from pathlib import Path
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.skills.router import (  # noqa: E402
    MAX_JSON_BODY_BYTES,
    main_agent_profile_router,
    skill_package_router,
)
from app.assistant_config.router import router as assistant_config_router  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from tests._db import make_session  # noqa: E402


FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "agent_skills" / "valid-weekly-review"
)

EXPECTED_PACKAGE_PATHS = {
    ("GET", "/api/assistant-config/skill-packages"),
    ("POST", "/api/assistant-config/skill-packages"),
    ("POST", "/api/assistant-config/skill-packages/import"),
    ("GET", "/api/assistant-config/skill-packages/{package_id}"),
    ("PUT", "/api/assistant-config/skill-packages/{package_id}/draft"),
    ("GET", "/api/assistant-config/skill-packages/{package_id}/versions"),
    ("GET", "/api/assistant-config/skill-packages/{package_id}/versions/{version_id}"),
    (
        "POST",
        "/api/assistant-config/skill-packages/{package_id}/publish",
    ),
    (
        "GET",
        "/api/assistant-config/skill-packages/{package_id}/versions/{version_id}/resources/{path}",
    ),
    (
        "GET",
        "/api/assistant-config/skill-packages/{package_id}/versions/{version_id}/export",
    ),
}


def _minimal_skill_md(
    *,
    name: str = "weekly-review",
    description: str = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    ),
    body: str = "# Weekly review\n\nBody.\n",
) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"


def _mindatlas_yaml(
    *,
    display_name: str = "周度回顾",
    legacy_aliases: list[str] | None = None,
) -> str:
    aliases = legacy_aliases if legacy_aliases is not None else ["weekly_review"]
    alias_block = "\n".join(f"  - {a}" for a in aliases) if aliases else "  []"
    return (
        "version: 1\n"
        f"display_name: {display_name}\n"
        f"legacy_aliases:\n{alias_block}\n"
        "\n"
        "routing:\n"
        "  include_examples: []\n"
        "  exclude_examples: []\n"
        "  conflict_rules: []\n"
        "\n"
        "capabilities:\n"
        "  - type: tool\n"
        "    key: search_entries\n"
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
        "metadata: {}\n"
    )


def _create_body(
    *,
    name: str = "weekly-review",
    version_name: str | None = "initial draft",
    resources: list[dict[str, str]] | None = None,
    extra: dict | None = None,
) -> dict:
    body: dict = {
        "skillMd": _minimal_skill_md(name=name),
        "mindatlasYaml": _mindatlas_yaml(
            legacy_aliases=[name.replace("-", "_")],
        ),
        "resources": resources
        if resources is not None
        else [
            {
                "path": "references/guide.md",
                "contentBase64": base64.b64encode(b"# Guide\n").decode("ascii"),
            }
        ],
    }
    if version_name is not None:
        body["versionName"] = version_name
    if extra:
        body.update(extra)
    return body


def _make_zip(name: str = "import-skill") -> bytes:
    skill_md = _minimal_skill_md(name=name).encode("utf-8")
    mindatlas = _mindatlas_yaml(legacy_aliases=[name.replace("-", "_")]).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(f"{name}/SKILL.md", skill_md)
        zf.writestr(f"{name}/mindatlas.yaml", mindatlas)
        zf.writestr(f"{name}/references/guide.md", b"# Guide\n")
    return buf.getvalue()


class AgentSkillApiTests(unittest.TestCase):
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

    # ------------------------------------------------------------------
    # OpenAPI / path ownership
    # ------------------------------------------------------------------

    def test_openapi_exposes_exact_package_paths_once(self) -> None:
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        found: set[tuple[str, str]] = set()
        for path, methods in paths.items():
            for method in methods:
                if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    found.add((method.upper(), path))

        for expected in EXPECTED_PACKAGE_PATHS:
            self.assertIn(expected, found, msg=f"missing OpenAPI path {expected}")

        # No duplicated prefix registration.
        self.assertNotIn("/api/assistant-config/skill-packages/skill-packages", paths)
        # Legacy skills surface remains present.
        self.assertIn("/api/assistant-config/skills", paths)

    def test_legacy_skills_openapi_snapshot_unchanged_shape(self) -> None:
        """Snapshot pre-existing legacy skills routes for regression safety."""
        schema = self.client.get("/openapi.json").json()
        legacy = schema["paths"]["/api/assistant-config/skills"]
        self.assertIn("get", legacy)
        self.assertIn("post", legacy)
        # Response still uses shared ApiResponse envelope (no v2 fields required).
        get_resp = legacy["get"]["responses"]["200"]
        self.assertTrue(get_resp)

        # Live list still succeeds with the historical envelope.
        resp = self.client.get("/api/assistant-config/skills")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("success", payload)
        self.assertIn("code", payload)
        self.assertIn("message", payload)
        self.assertIn("data", payload)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], 0)
        self.assertIsInstance(payload["data"], list)

    # ------------------------------------------------------------------
    # Create / get / list
    # ------------------------------------------------------------------

    def test_create_list_get_package_happy_path(self) -> None:
        create = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(_create_body(name="weekly-review")),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(create.status_code, 200, create.text)
        body = create.json()
        self.assertTrue(body["success"])
        data = body["data"]
        self.assertEqual(data["canonicalName"], "weekly-review")
        self.assertEqual(data["migrationState"], "native")
        self.assertFalse(data["catalogEnabled"])
        self.assertIsNotNone(data["draftVersion"])
        self.assertIsNone(data["publishedVersion"])
        # Resource bytes must never appear on detail (digest field names are fine).
        self.assertNotIn("contentBase64", json.dumps(data))
        self.assertNotIn('"content":', json.dumps(data))

        package_id = data["id"]
        listed = self.client.get("/api/assistant-config/skill-packages")
        self.assertEqual(listed.status_code, 200)
        page = listed.json()["data"]
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["limit"], 50)
        self.assertEqual(page["offset"], 0)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["id"], package_id)
        self.assertEqual(page["items"][0]["migrationState"], "native")
        self.assertIsNone(page["items"][0].get("publishedVersion"))

        detail = self.client.get(f"/api/assistant-config/skill-packages/{package_id}")
        self.assertEqual(detail.status_code, 200)
        detail_data = detail.json()["data"]
        self.assertEqual(detail_data["id"], package_id)
        self.assertTrue(detail_data["aliases"])

    def test_list_pagination_filters_and_ordering(self) -> None:
        for name in ("pkg-alpha", "pkg-beta", "pkg-gamma"):
            resp = self.client.post(
                "/api/assistant-config/skill-packages",
                content=json.dumps(_create_body(name=name)),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)

        # publicationState=unpublished keeps all (none published yet).
        page = self.client.get(
            "/api/assistant-config/skill-packages",
            params={
                "publicationState": "unpublished",
                "migrationState": "native",
                "catalogEnabled": "false",
                "limit": 2,
                "offset": 0,
            },
        ).json()["data"]
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["limit"], 2)
        self.assertEqual(len(page["items"]), 2)

        page2 = self.client.get(
            "/api/assistant-config/skill-packages",
            params={"limit": 2, "offset": 2},
        ).json()["data"]
        self.assertEqual(len(page2["items"]), 1)

        # limit capped at 200 by query validation.
        capped = self.client.get(
            "/api/assistant-config/skill-packages",
            params={"limit": 500},
        )
        self.assertEqual(capped.status_code, 422)

        published_only = self.client.get(
            "/api/assistant-config/skill-packages",
            params={"publicationState": "published"},
        ).json()["data"]
        self.assertEqual(published_only["total"], 0)

    def test_client_media_type_rejected_on_create(self) -> None:
        body = _create_body(
            name="media-type-reject",
            resources=[
                {
                    "path": "references/guide.md",
                    "contentBase64": base64.b64encode(b"x").decode("ascii"),
                    "mediaType": "text/html",
                }
            ],
        )
        resp = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 422)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 42292)

    # ------------------------------------------------------------------
    # Draft / versions / publish
    # ------------------------------------------------------------------

    def test_save_draft_versions_and_publish(self) -> None:
        created = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(_create_body(name="draftable")),
            headers={"Content-Type": "application/json"},
        ).json()["data"]
        package_id = created["id"]
        draft_id = created["draftVersion"]["id"]

        # Save changed draft.
        save_body = _create_body(
            name="draftable",
            version_name="draft-2",
            resources=[
                {
                    "path": "references/guide.md",
                    "contentBase64": base64.b64encode(b"# Updated guide\n").decode(
                        "ascii"
                    ),
                }
            ],
        )
        saved = self.client.put(
            f"/api/assistant-config/skill-packages/{package_id}/draft",
            content=json.dumps(save_body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        new_draft = saved.json()["data"]
        self.assertEqual(new_draft["versionSource"], "save")
        self.assertNotEqual(new_draft["id"], draft_id)

        versions = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions",
            params={"versionSource": "save", "origin": "api"},
        )
        self.assertEqual(versions.status_code, 200)
        vpage = versions.json()["data"]
        self.assertGreaterEqual(vpage["total"], 2)
        self.assertIn("items", vpage)
        self.assertIn("limit", vpage)
        self.assertIn("offset", vpage)

        version_id = new_draft["id"]
        meta = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{version_id}"
        )
        self.assertEqual(meta.status_code, 200)
        meta_data = meta.json()["data"]
        self.assertEqual(meta_data["id"], version_id)
        self.assertIn("skillMd", meta_data)
        self.assertIn("resources", meta_data)
        # No resource bytes.
        for resource in meta_data["resources"]:
            self.assertNotIn("content", resource)
            self.assertNotIn("contentBase64", resource)
            self.assertIn("mediaType", resource)
            self.assertIn("path", resource)

        # Publish owned draft (instruction-only packages can publish).
        # Use package without capabilities to avoid resolution requirements? draftable
        # has a tool capability; publish will try to resolve. Create instruction-only.
        only = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(
                {
                    "skillMd": _minimal_skill_md(name="instruction-only"),
                    "versionName": "d1",
                    "resources": [],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(only.status_code, 200, only.text)
        only_data = only.json()["data"]
        only_id = only_data["id"]
        only_draft = only_data["draftVersion"]["id"]
        published = self.client.post(
            f"/api/assistant-config/skill-packages/{only_id}/publish",
            json={"draftVersionId": only_draft},
        )
        self.assertEqual(published.status_code, 200, published.text)
        pub = published.json()["data"]
        self.assertEqual(pub["versionSource"], "publish")
        self.assertEqual(pub["sourceDraftVersionId"], only_draft)

        # Aggregate now has published pointer.
        detail = self.client.get(
            f"/api/assistant-config/skill-packages/{only_id}"
        ).json()["data"]
        self.assertIsNotNone(detail["publishedVersion"])
        self.assertFalse(detail["catalogEnabled"])

    # ------------------------------------------------------------------
    # Resource / export
    # ------------------------------------------------------------------

    def test_resource_retrieval_headers_and_ownership(self) -> None:
        created = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(_create_body(name="resource-pkg")),
            headers={"Content-Type": "application/json"},
        ).json()["data"]
        package_id = created["id"]
        version_id = created["draftVersion"]["id"]

        ok = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{version_id}/resources/references/guide.md"
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.content, b"# Guide\n")
        self.assertEqual(ok.headers.get("x-content-type-options"), "nosniff")
        disposition = ok.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("guide.md", disposition)
        # Server-detected media type for .md
        self.assertIn("text/markdown", ok.headers.get("content-type", ""))

        # Wrong package ownership.
        other = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(_create_body(name="other-pkg")),
            headers={"Content-Type": "application/json"},
        ).json()["data"]
        bad = self.client.get(
            f"/api/assistant-config/skill-packages/{other['id']}/versions/{version_id}/resources/references/guide.md"
        )
        self.assertEqual(bad.status_code, 404)
        self.assertIn(bad.json()["code"], {40491, 40492})

        # Path traversal rejected.
        trav = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{version_id}/resources/../SKILL.md"
        )
        self.assertIn(trav.status_code, {404, 422})
        if trav.status_code == 422:
            self.assertEqual(trav.json()["code"], 42292)

        missing = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{version_id}/resources/references/nope.md"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], 40492)

    def test_export_stable_filename_and_content_type(self) -> None:
        created = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(_create_body(name="exportable")),
            headers={"Content-Type": "application/json"},
        ).json()["data"]
        package_id = created["id"]
        version_id = created["draftVersion"]["id"]
        seq = created["draftVersion"]["sequenceNo"]

        exported = self.client.get(
            f"/api/assistant-config/skill-packages/{package_id}/versions/{version_id}/export"
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn("application/zip", exported.headers.get("content-type", ""))
        disposition = exported.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn(f"exportable-{seq}.zip", disposition)
        self.assertEqual(exported.headers.get("x-content-type-options"), "nosniff")
        # Valid ZIP.
        with zipfile.ZipFile(io.BytesIO(exported.content), "r") as zf:
            names = zf.namelist()
            self.assertTrue(any(n.endswith("SKILL.md") for n in names))

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def test_import_zip_create_only_and_conflict(self) -> None:
        raw = _make_zip("import-skill")
        first = self.client.post(
            "/api/assistant-config/skill-packages/import",
            files={"file": ("import-skill.zip", raw, "application/zip")},
        )
        self.assertEqual(first.status_code, 200, first.text)
        data = first.json()["data"]
        self.assertEqual(data["canonicalName"], "import-skill")
        self.assertEqual(data["migrationState"], "native")
        self.assertFalse(data["catalogEnabled"])
        self.assertIsNone(data["publishedVersion"])

        second = self.client.post(
            "/api/assistant-config/skill-packages/import",
            files={"file": ("import-skill.zip", raw, "application/zip")},
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["code"], 40995)

    # ------------------------------------------------------------------
    # Errors: 404 / 409 / 413 / 422
    # ------------------------------------------------------------------

    def test_not_found_codes(self) -> None:
        missing = uuid4()
        resp = self.client.get(f"/api/assistant-config/skill-packages/{missing}")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], 40490)
        for key in ("success", "code", "message", "data"):
            self.assertIn(key, resp.json())
        self.assertFalse(resp.json()["success"])

    def test_duplicate_canonical_name_conflict(self) -> None:
        body = _create_body(name="dup-name")
        first = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(second.status_code, 409)
        self.assertIn(second.json()["code"], {40990, 40991})

    def test_invalid_publish_reference(self) -> None:
        created = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(
                {
                    "skillMd": _minimal_skill_md(name="pub-ref"),
                    "resources": [],
                }
            ),
            headers={"Content-Type": "application/json"},
        ).json()["data"]
        package_id = created["id"]
        resp = self.client.post(
            f"/api/assistant-config/skill-packages/{package_id}/publish",
            json={"draftVersionId": str(uuid4())},
        )
        self.assertIn(resp.status_code, {404, 422})
        self.assertIn(resp.json()["code"], {40491, 42293, 40993})

    def test_oversize_json_body_rejected_before_parse(self) -> None:
        # Declared Content-Length above 36 MiB is an early rejection.
        huge = MAX_JSON_BODY_BYTES + 1
        resp = self.client.post(
            "/api/assistant-config/skill-packages",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(huge),
            },
        )
        # Starlette/TestClient may reject Content-Length mismatch; either way
        # we must not 200. Prefer our 41390 when the stream path is exercised.
        self.assertNotEqual(resp.status_code, 200)
        if resp.headers.get("content-type", "").startswith("application/json"):
            payload = resp.json()
            if "code" in payload:
                self.assertIn(payload["code"], {41390, 42292, 400})

        # Streamed body just over the bound.
        over = b"{" + (b"a" * (MAX_JSON_BODY_BYTES + 10))
        # invalid JSON but size bound should fire first when stream is counted.
        # Build a body that is valid-ish size-wise: large string field.
        large_payload = json.dumps({"skillMd": "x" * (MAX_JSON_BODY_BYTES)}).encode(
            "utf-8"
        )
        self.assertGreater(len(large_payload), MAX_JSON_BODY_BYTES)
        resp2 = self.client.post(
            "/api/assistant-config/skill-packages",
            content=large_payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp2.status_code, 413)
        self.assertEqual(resp2.json()["code"], 41390)

    def test_oversize_zip_import_rejected(self) -> None:
        from app.assistant.skills.package_io import MAX_ZIP_UPLOAD_BYTES

        # Declared content-length hint.
        resp = self.client.post(
            "/api/assistant-config/skill-packages/import",
            content=b"PK\x03\x04",
            headers={
                "Content-Type": "application/zip",
                "Content-Length": str(MAX_ZIP_UPLOAD_BYTES + 1),
            },
        )
        self.assertNotEqual(resp.status_code, 200)
        if resp.headers.get("content-type", "").startswith("application/json"):
            code = resp.json().get("code")
            if code is not None:
                self.assertEqual(code, 41390)

    def test_invalid_package_body_returns_4229x(self) -> None:
        resp = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(
                {
                    "skillMd": "not-valid-frontmatter",
                    "resources": [],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn(resp.json()["code"], {42290, 42292})

    def test_responses_exclude_credentials_and_orm_internals(self) -> None:
        created = self.client.post(
            "/api/assistant-config/skill-packages",
            content=json.dumps(_create_body(name="safe-dto")),
            headers={"Content-Type": "application/json"},
        ).json()["data"]
        blob = json.dumps(created)
        for forbidden in (
            "api_key",
            "apiKey",
            "password",
            "_sa_instance_state",
            "encrypted",
        ):
            self.assertNotIn(forbidden, blob)

    def test_map_package_io_error_size_vs_validation(self) -> None:
        """Bare 'file ' must not force 41392; only size/count phrases do."""
        from app.assistant.skills.router import _map_package_io_error

        size_msgs = [
            "file 'refs/a.md' exceeds size limit of 100 bytes",
            "SKILL.md exceeds size limit",
            "package entry count exceeds limit",
            "ZIP entry count exceeds limit",
            "ZIP entry 'x' declared size exceeds limit of 10",
            "ZIP entry 'x' streamed size exceeds limit of 10",
        ]
        for msg in size_msgs:
            mapped = _map_package_io_error(ValueError(msg))
            self.assertEqual(mapped.status_code, 413, msg)
            self.assertEqual(mapped.code, 41392, msg)

        validation_msgs = [
            "package file map must be a non-empty mapping",
            "ZIP archive has no file entries",
            "file path must be a non-empty string",
            "file path must not contain backslashes",
            "file path must be relative (no absolute paths)",
        ]
        for msg in validation_msgs:
            mapped = _map_package_io_error(ValueError(msg))
            self.assertEqual(mapped.status_code, 422, msg)
            self.assertEqual(mapped.code, 42292, msg)

    def test_sort_key_created_desc_secondary_id_desc(self) -> None:
        """Equal timestamps must sort by id DESC for stable list order."""
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from uuid import UUID

        from app.assistant.skills.router import _sort_key_created_desc

        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        low = SimpleNamespace(
            created_at=ts, id=UUID("00000000-0000-0000-0000-000000000001")
        )
        mid = SimpleNamespace(
            created_at=ts, id=UUID("00000000-0000-0000-0000-000000000002")
        )
        high = SimpleNamespace(
            created_at=ts, id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        )
        items = [low, mid, high]
        items.sort(key=_sort_key_created_desc)
        self.assertEqual(
            [str(item.id) for item in items],
            [
                "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000001",
            ],
        )


if __name__ == "__main__":
    unittest.main()
