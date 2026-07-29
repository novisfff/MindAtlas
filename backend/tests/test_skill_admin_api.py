"""Plan 09 skill admin API — session principal + OpenAPI proofs."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.skills.admin_router import (  # noqa: E402
    PLAN09_ADMIN_PREFIX,
    skill_admin_parent_router,
)
from app.assistant.skills.router import skill_package_router  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from app.operator_auth.route_policy import protected_browser_router  # noqa: E402
from tests._db import make_session  # noqa: E402
from tests.operator_session_helpers import (  # noqa: E402
    build_authenticated_skill_client,
    csrf_headers,
    origin_headers,
    restore_operator_settings,
)


PLAN09_PATH_MARKERS = (
    "/archive",
    "/unarchive",
    "/catalog/enable",
    "/catalog/disable",
    "/restore-draft",
    "/skill-admin",
    "/aliases/{alias_id}/disable",
    "/metadata",
)


def _minimal_skill_md(
    *,
    name: str = "weekly-review",
    description: str = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    ),
    body: str = "# Weekly review\n\nBody.\n",
) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"


def _mindatlas_yaml() -> str:
    return (
        "version: 1\n"
        "display_name: 周度回顾\n"
        "legacy_aliases:\n"
        "  - weekly_review\n"
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
    )


class SkillAdminOpenApiMountTests(unittest.TestCase):
    def test_openapi_includes_admin_when_mounted_under_protected_browser(self) -> None:
        session = make_session()
        try:
            client, _headers, _settings = build_authenticated_skill_client(
                db=session,
                include_routers=[skill_package_router, skill_admin_parent_router],
            )
            schema = client.get("/openapi.json").json()
            paths = schema.get("paths") or {}
            admin_paths = [p for p in paths if PLAN09_ADMIN_PREFIX in p]
            self.assertGreaterEqual(len(admin_paths), 5)
            self.assertIn(
                f"{PLAN09_ADMIN_PREFIX}/main-agent-profiles/default/versions/{{version_id}}",
                paths,
            )
            for path, methods in paths.items():
                if PLAN09_ADMIN_PREFIX not in path:
                    continue
                self.assertNotIn("delete", {m.lower() for m in methods.keys()})
            self.assertIn("/api/assistant-config/skill-packages", paths)
        finally:
            restore_operator_settings()
            session.close()

    def test_admin_mutation_requires_session_and_csrf(self) -> None:
        session = make_session()
        try:
            client, headers, _settings = build_authenticated_skill_client(
                db=session,
                include_routers=[skill_package_router, skill_admin_parent_router],
            )
            name = f"api-pack-{uuid.uuid4().hex[:8]}"
            create = client.post(
                "/api/assistant-config/skill-packages",
                headers=headers,
                json={
                    "skillMd": _minimal_skill_md(name=name),
                    "mindatlasYaml": _mindatlas_yaml(),
                    "resources": [],
                },
            )
            self.assertEqual(create.status_code, 200, create.text)
            package_id = create.json()["data"]["id"]

            # No session cookies → 401 from protected browser / principal dep.
            bare = TestClient(client.app)
            r = bare.patch(
                f"{PLAN09_ADMIN_PREFIX}/skill-packages/{package_id}/metadata",
                headers=origin_headers(),
                json={
                    "requestId": "m1",
                    "expectedAggregateRevision": 0,
                    "displayName": "X",
                },
            )
            self.assertEqual(r.status_code, 401, r.text)

            # Forged operator identity headers never authenticate.
            r_forged = bare.patch(
                f"{PLAN09_ADMIN_PREFIX}/skill-packages/{package_id}/metadata",
                headers=origin_headers(
                    **{
                        "X-MindAtlas-Operator-Id": "forged",
                        "X-MindAtlas-Operator-Role": "operator",
                    }
                ),
                json={
                    "requestId": "m1b",
                    "expectedAggregateRevision": 0,
                    "displayName": "X",
                },
            )
            self.assertEqual(r_forged.status_code, 401, r_forged.text)

            # Authenticated operator metadata CAS works.
            r3 = client.patch(
                f"{PLAN09_ADMIN_PREFIX}/skill-packages/{package_id}/metadata",
                headers=headers,
                json={
                    "requestId": "m2",
                    "expectedAggregateRevision": 0,
                    "displayName": "Admin Name",
                },
            )
            self.assertEqual(r3.status_code, 200, r3.text)
            data = r3.json()["data"]
            self.assertEqual(data["displayName"], "Admin Name")
            self.assertEqual(data["aggregateRevision"], 1)

            # Archive path present and works.
            r4 = client.post(
                f"{PLAN09_ADMIN_PREFIX}/skill-packages/{package_id}/archive",
                headers=csrf_headers(client),
                json={"requestId": "ar1", "expectedAggregateRevision": 1},
            )
            self.assertEqual(r4.status_code, 200, r4.text)
            self.assertIsNotNone(r4.json()["data"]["archivedAt"])
            self.assertFalse(r4.json()["data"]["catalogEnabled"])
        finally:
            restore_operator_settings()
            session.close()


class SkillAdminMigrationModelTests(unittest.TestCase):
    """ORM/create_all coverage for new lifecycle columns (SQLite unit path)."""

    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session as _make

        self.db = _make()

    def tearDown(self) -> None:
        self.db.close()

    def test_package_columns_default_and_alias_disabled_shape(self) -> None:
        from app.assistant.skills.models import (
            AssistantSkillPackage,
            AssistantSkillPackageAlias,
        )
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files

        files = {
            "SKILL.md": _minimal_skill_md(name="col-pack").encode("utf-8"),
            "mindatlas.yaml": _mindatlas_yaml().encode("utf-8"),
        }
        parsed = parse_skill_directory_files(files, expected_root_name=None)
        svc = AgentSkillService(self.db)
        detail = svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="d1")
        )
        pkg = self.db.get(AssistantSkillPackage, detail.id)
        assert pkg is not None
        self.assertEqual(int(pkg.aggregate_revision or 0), 0)
        self.assertIsNone(pkg.archived_at)
        self.assertIsNone(pkg.archived_by)
        self.assertIsNone(pkg.catalog_enabled_at)
        self.assertIsNone(pkg.catalog_enabled_by)

        aliases = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == pkg.id)
            .all()
        )
        for a in aliases:
            self.assertIsNone(a.disabled_at)
            self.assertIsNone(a.disabled_by)

    def test_migration_revision_parent_and_unique_id(self) -> None:
        import re

        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        lifecycle = list(versions.glob("*_add_skill_package_admin_lifecycle.py"))
        self.assertEqual(len(lifecycle), 1)
        text = lifecycle[0].read_text(encoding="utf-8")
        self.assertIn('down_revision = "d7e8f9a0b1c3"', text)
        self.assertNotIn("b4c5d6e7f8a9", text)
        rev_match = re.search(r'revision\s*=\s*["\']([0-9a-f]+)["\']', text)
        self.assertIsNotNone(rev_match)
        rev = rev_match.group(1)  # type: ignore[union-attr]
        self.assertNotEqual(rev, "b4c5d6e7f8a9")
        self.assertNotEqual(rev, "d7e8f9a0b1c3")
        children = []
        for path in versions.glob("*.py"):
            if path.name.startswith("__"):
                continue
            content = path.read_text(encoding="utf-8")
            m = re.search(r"down_revision\s*=\s*[\"']([^\"']+)[\"']", content)
            if m and m.group(1) == rev:
                children.append(path.name)
        self.assertTrue(
            any("add_skill_evaluation_workbench" in name for name in children),
            f"expected evaluation migration to descend from Task 1 head; got {children}",
        )


if __name__ == "__main__":
    unittest.main()
