"""Plan 09 Task 1 — bounded diff and restore-as-new-draft tests."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _minimal_skill_md(
    *,
    name: str = "weekly-review",
    description: str = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    ),
    body: str = "# Weekly review\n\nBody.\n",
) -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    ).encode("utf-8")


def _mindatlas_yaml(
    *,
    display_name: str = "周度回顾",
    legacy_aliases: list[str] | None = None,
) -> bytes:
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
    ).encode("utf-8")


def _parse(
    *,
    name: str = "weekly-review",
    mindatlas: bytes | None = None,
    body: str = "# Weekly review\n\nBody.\n",
    resources: dict[str, bytes] | None = None,
):
    from app.assistant.skills.package_io import parse_skill_directory_files

    files: dict[str, bytes] = {
        "SKILL.md": _minimal_skill_md(name=name, body=body),
        "mindatlas.yaml": mindatlas if mindatlas is not None else _mindatlas_yaml(),
    }
    if resources:
        files.update(resources)
    return parse_skill_directory_files(files, expected_root_name=None)


def _operator(principal_id: str = "op-1"):
    from app.assistant.skills.principal import OperatorPrincipal

    return OperatorPrincipal(principal_id=principal_id, role="operator")


class SkillDiffRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.admin_service import SkillAdminService
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )

        self.db = make_session()
        self.pkg_svc = AgentSkillService(self.db)
        self.admin = SkillAdminService(self.db)
        name = f"diff-pack-{uuid.uuid4().hex[:8]}"
        self.package = self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name=name,
                    body="# V1\n\nOriginal body.\n",
                    resources={
                        "references/notes.md": b"# notes v1\n",
                        "assets/secret-token.txt": b"super-secret-value",
                    },
                ),
                version_name="draft-1",
            )
        )
        self.v1 = self.package.draft_version
        assert self.v1 is not None
        # Append a second divergent draft.
        self.v2 = self.pkg_svc.save_draft(
            SaveSkillDraftCommand(
                package_id=self.package.id,
                parsed=_parse(
                    name=name,
                    body="# V2\n\nChanged body for diff.\n",
                    resources={
                        "references/notes.md": b"# notes v2\n",
                        "references/extra.md": b"new file\n",
                        "assets/secret-token.txt": b"super-secret-value-changed",
                    },
                ),
                version_name="draft-2",
            )
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_bounded_diff_excludes_resource_bytes_and_secrets(self) -> None:
        from app.assistant.skills.diff import diff_skill_versions

        result = diff_skill_versions(
            self.db,
            package_id=self.package.id,
            left_version_id=self.v1.id,
            right_version_id=self.v2.id,
        )
        self.assertTrue(result.resource_bytes_excluded)
        self.assertTrue(result.secrets_excluded)
        self.assertTrue(result.unbounded_bodies_excluded)
        self.assertEqual(result.left_version_id, self.v1.id)
        self.assertEqual(result.right_version_id, self.v2.id)
        self.assertNotEqual(result.left_content_digest, result.right_content_digest)

        paths = {h.path for h in result.hunks}
        self.assertIn("SKILL.md", paths)
        self.assertIn("references/notes.md", paths)
        self.assertIn("references/extra.md", paths)
        self.assertIn("assets/secret-token.txt", paths)

        # Resource bytes never appear as previews for resources.
        for h in result.hunks:
            if h.path.startswith("references/") or h.path.startswith("assets/"):
                self.assertIsNone(h.left_preview)
                self.assertIsNone(h.right_preview)

        # Secret path has digests only (no preview of secret content).
        secret = next(h for h in result.hunks if "secret" in h.path.lower())
        self.assertIsNone(secret.left_preview)
        self.assertIsNone(secret.right_preview)
        self.assertIn(secret.kind, {"changed", "added", "removed", "unchanged_meta"})

        # Metadata only — no raw skill_md body dump unbounded.
        self.assertIn("contentDigest", result.left_metadata)
        self.assertNotIn("skillMd", result.left_metadata)
        self.assertNotIn("skill_md", result.left_metadata)

    def test_restore_as_new_draft_copies_content_with_provenance(self) -> None:
        from app.assistant.skills.schemas import (
            PublishSkillVersionCommand,
            RestoreSkillVersionAsDraftCommand,
        )
        from app.assistant.skills.models import AssistantSkillVersion

        # Publish v2 so published pointer exists and must remain stable.
        published = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=self.v2.id),
        )
        detail_before = self.pkg_svc.get_package(self.package.id)
        self.assertEqual(detail_before.published_version.id, published.id)  # type: ignore[union-attr]

        restored = self.admin.restore_as_new_draft(
            self.package.id,
            self.v1.id,
            RestoreSkillVersionAsDraftCommand(
                request_id="restore-1",
                expected_aggregate_revision=detail_before.aggregate_revision,
            ),
            principal=_operator(),
        )
        self.assertEqual(restored.version_source, "save")
        # Plan 01 content-digest uniqueness may re-point draft to the original
        # save row; either way content must match the restored source.
        self.assertEqual(restored.content_digest, self.v1.content_digest)

        detail_after = self.pkg_svc.get_package(self.package.id)
        # Published pointer untouched.
        self.assertEqual(
            detail_after.published_version.id, published.id  # type: ignore[union-attr]
        )
        self.assertEqual(detail_after.draft_version.id, restored.id)  # type: ignore[union-attr]
        self.assertEqual(
            detail_after.aggregate_revision, detail_before.aggregate_revision + 1
        )

        row = self.db.get(AssistantSkillVersion, restored.id)
        assert row is not None
        manifest = dict(row.extension_manifest or {})
        self.assertEqual(manifest.get("restoredFromVersionId"), str(self.v1.id))

        # History still contains original versions — no pointer rewind.
        versions = self.pkg_svc.list_versions(self.package.id)
        ids = {v.id for v in versions}
        self.assertIn(self.v1.id, ids)
        self.assertIn(self.v2.id, ids)
        self.assertIn(published.id, ids)
        self.assertIn(restored.id, ids)

        # Restore from the published version (different content than v1 save)
        # appends a fresh draft when content is not already a save row.
        restored_pub = self.admin.restore_as_new_draft(
            self.package.id,
            published.id,
            RestoreSkillVersionAsDraftCommand(
                request_id="restore-2",
                expected_aggregate_revision=detail_after.aggregate_revision,
            ),
            principal=_operator(),
        )
        self.assertEqual(restored_pub.version_source, "save")
        self.assertEqual(restored_pub.content_digest, published.content_digest)
        # v2 was the draft behind publish — same content_digest as publish source.
        detail_final = self.pkg_svc.get_package(self.package.id)
        self.assertEqual(
            detail_final.published_version.id, published.id  # type: ignore[union-attr]
        )
        self.assertEqual(detail_final.draft_version.id, restored_pub.id)  # type: ignore[union-attr]

    def test_restore_requires_principal(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import RestoreSkillVersionAsDraftCommand

        with self.assertRaises(ApiException) as ctx:
            self.admin.restore_as_new_draft(
                self.package.id,
                self.v1.id,
                RestoreSkillVersionAsDraftCommand(
                    request_id="restore-no-auth",
                    expected_aggregate_revision=0,
                ),
                principal=None,
            )
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.code, 40190)

    def test_restore_request_id_retry_is_idempotent(self) -> None:
        from app.assistant.skills.schemas import RestoreSkillVersionAsDraftCommand

        detail = self.pkg_svc.get_package(self.package.id)
        first = self.admin.restore_as_new_draft(
            self.package.id,
            self.v1.id,
            RestoreSkillVersionAsDraftCommand(
                request_id="restore-idem",
                expected_aggregate_revision=detail.aggregate_revision,
            ),
            principal=_operator(),
        )
        second = self.admin.restore_as_new_draft(
            self.package.id,
            self.v1.id,
            RestoreSkillVersionAsDraftCommand(
                request_id="restore-idem",
                expected_aggregate_revision=detail.aggregate_revision,
            ),
            principal=_operator(),
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.content_digest, second.content_digest)


if __name__ == "__main__":
    unittest.main()
