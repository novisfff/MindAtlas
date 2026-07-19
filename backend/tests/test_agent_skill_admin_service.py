"""Plan 09 Task 1 — SkillAdminService aggregate lifecycle tests."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "agent_skills" / "valid-weekly-review"
)


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
):
    from app.assistant.skills.package_io import parse_skill_directory_files

    files: dict[str, bytes] = {
        "SKILL.md": _minimal_skill_md(name=name, body=body),
        "mindatlas.yaml": mindatlas if mindatlas is not None else _mindatlas_yaml(),
    }
    return parse_skill_directory_files(files, expected_root_name=None)


def _operator(principal_id: str = "op-1") -> "OperatorPrincipal":
    from app.assistant.skills.principal import OperatorPrincipal

    return OperatorPrincipal(principal_id=principal_id, role="operator")


def _viewer(principal_id: str = "viewer-1") -> "OperatorPrincipal":
    from app.assistant.skills.principal import OperatorPrincipal

    return OperatorPrincipal(principal_id=principal_id, role="viewer")


class SkillAdminServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.admin_service import SkillAdminService
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        self.db = make_session()
        self.pkg_svc = AgentSkillService(self.db)
        self.admin = SkillAdminService(self.db)
        self.package = self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(name=f"admin-pack-{uuid.uuid4().hex[:8]}"),
                version_name="draft-1",
            )
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_missing_principal_rejected_on_metadata(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import UpdateSkillPackageMetadataCommand

        with self.assertRaises(ApiException) as ctx:
            self.admin.update_metadata(
                self.package.id,
                UpdateSkillPackageMetadataCommand(
                    request_id="r1",
                    expected_aggregate_revision=0,
                    display_name="New",
                ),
                principal=None,
            )
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.code, 40190)

    def test_fake_principal_type_rejected(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import UpdateSkillPackageMetadataCommand

        with self.assertRaises(ApiException) as ctx:
            self.admin.update_metadata(
                self.package.id,
                UpdateSkillPackageMetadataCommand(
                    request_id="r1",
                    expected_aggregate_revision=0,
                    display_name="New",
                ),
                principal="not-a-principal",  # type: ignore[arg-type]
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_metadata_cas_and_request_id_retry(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import UpdateSkillPackageMetadataCommand

        detail = self.admin.update_metadata(
            self.package.id,
            UpdateSkillPackageMetadataCommand(
                request_id="meta-1",
                expected_aggregate_revision=0,
                display_name="Renamed",
                description="desc",
            ),
            principal=_operator(),
        )
        self.assertEqual(detail.display_name, "Renamed")
        self.assertEqual(detail.aggregate_revision, 1)

        # Identical retry returns persisted outcome.
        again = self.admin.update_metadata(
            self.package.id,
            UpdateSkillPackageMetadataCommand(
                request_id="meta-1",
                expected_aggregate_revision=0,
                display_name="Renamed",
                description="desc",
            ),
            principal=_operator(),
        )
        self.assertEqual(again.aggregate_revision, 1)
        self.assertEqual(again.display_name, "Renamed")

        # Altered reuse of requestId conflicts.
        with self.assertRaises(ApiException) as ctx:
            self.admin.update_metadata(
                self.package.id,
                UpdateSkillPackageMetadataCommand(
                    request_id="meta-1",
                    expected_aggregate_revision=1,
                    display_name="Other",
                ),
                principal=_operator(),
            )
        self.assertEqual(ctx.exception.code, 40997)

        # Stale revision conflicts.
        with self.assertRaises(ApiException) as ctx2:
            self.admin.update_metadata(
                self.package.id,
                UpdateSkillPackageMetadataCommand(
                    request_id="meta-2",
                    expected_aggregate_revision=0,
                    display_name="Stale",
                ),
                principal=_operator(),
            )
        self.assertEqual(ctx2.exception.code, 40994)

    def test_archive_disables_catalog_unarchive_does_not_reenable(self) -> None:
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant.skills.models import AssistantSkillPackage
        from app.common.exceptions import ApiException

        # Publish then enable catalog via admin operator path.
        pub = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(
                draft_version_id=self.package.draft_version.id  # type: ignore[union-attr]
            ),
        )
        self.assertEqual(pub.version_source, "publish")
        # refresh package
        detail = self.pkg_svc.get_package(self.package.id)
        enabled = self.admin.enable_catalog(
            self.package.id,
            AggregateRevisionCommand(
                request_id="en-1",
                expected_aggregate_revision=detail.aggregate_revision,
            ),
            principal=_operator(),
            expected_published_version_id=pub.id,
        )
        self.assertTrue(enabled.catalog_enabled)
        self.assertIsNotNone(enabled.catalog_enabled_at)

        archived = self.admin.archive(
            self.package.id,
            AggregateRevisionCommand(
                request_id="ar-1",
                expected_aggregate_revision=enabled.aggregate_revision,
            ),
            principal=_operator(),
        )
        self.assertIsNotNone(archived.archived_at)
        self.assertFalse(archived.catalog_enabled)
        self.assertIsNone(archived.catalog_enabled_at)

        # Publish blocked while archived.
        with self.assertRaises(ApiException) as ctx:
            self.pkg_svc.publish(
                self.package.id,
                PublishSkillVersionCommand(
                    draft_version_id=self.package.draft_version.id  # type: ignore[union-attr]
                ),
            )
        self.assertEqual(ctx.exception.code, 40996)

        unarchived = self.admin.unarchive(
            self.package.id,
            AggregateRevisionCommand(
                request_id="uar-1",
                expected_aggregate_revision=archived.aggregate_revision,
            ),
            principal=_operator(),
        )
        self.assertIsNone(unarchived.archived_at)
        self.assertFalse(unarchived.catalog_enabled)

        row = self.db.get(AssistantSkillPackage, self.package.id)
        assert row is not None
        self.assertFalse(row.catalog_enabled)

    def test_catalog_enable_requires_operator_role(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            PublishSkillVersionCommand,
        )

        pub = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(
                draft_version_id=self.package.draft_version.id  # type: ignore[union-attr]
            ),
        )
        with self.assertRaises(ApiException) as ctx:
            self.admin.enable_catalog(
                self.package.id,
                AggregateRevisionCommand(
                    request_id="en-viewer",
                    expected_aggregate_revision=0,
                ),
                principal=_viewer(),
                expected_published_version_id=pub.id,
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, 40391)

    def test_custom_alias_add_and_disable_protects_canonical_legacy(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import (
            AddSkillPackageAliasCommand,
            DisableSkillPackageAliasCommand,
        )
        from app.assistant.skills.models import AssistantSkillPackageAlias

        detail = self.admin.add_alias(
            self.package.id,
            AddSkillPackageAliasCommand(
                request_id="alias-1",
                expected_aggregate_revision=0,
                alias="custom-weekly",
            ),
            principal=_operator(),
        )
        custom = next(a for a in detail.aliases if a.alias_type == "custom")
        self.assertIsNone(custom.disabled_at)

        disabled = self.admin.disable_alias(
            self.package.id,
            custom.id,
            DisableSkillPackageAliasCommand(
                request_id="alias-dis-1",
                expected_aggregate_revision=detail.aggregate_revision,
            ),
            principal=_operator(),
        )
        custom2 = next(a for a in disabled.aliases if a.id == custom.id)
        self.assertIsNotNone(custom2.disabled_at)

        # Disabled name remains reserved.
        with self.assertRaises(ApiException) as ctx:
            self.admin.add_alias(
                self.package.id,
                AddSkillPackageAliasCommand(
                    request_id="alias-2",
                    expected_aggregate_revision=disabled.aggregate_revision,
                    alias="custom-weekly",
                ),
                principal=_operator(),
            )
        self.assertEqual(ctx.exception.code, 40991)

        # Canonical cannot be disabled.
        canonical = next(a for a in disabled.aliases if a.alias_type == "canonical")
        with self.assertRaises(ApiException) as ctx2:
            self.admin.disable_alias(
                self.package.id,
                canonical.id,
                DisableSkillPackageAliasCommand(
                    request_id="alias-dis-can",
                    expected_aggregate_revision=disabled.aggregate_revision,
                ),
                principal=_operator(),
            )
        self.assertEqual(ctx2.exception.code, 42296)

        # Legacy cannot be disabled.
        legacy = next(a for a in disabled.aliases if a.alias_type == "legacy")
        with self.assertRaises(ApiException) as ctx3:
            self.admin.disable_alias(
                self.package.id,
                legacy.id,
                DisableSkillPackageAliasCommand(
                    request_id="alias-dis-leg",
                    expected_aggregate_revision=disabled.aggregate_revision,
                ),
                principal=_operator(),
            )
        self.assertEqual(ctx3.exception.code, 42296)

        # No physical DELETE of alias rows.
        count = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == self.package.id)
            .count()
        )
        self.assertGreaterEqual(count, 3)

    def test_no_physical_package_delete_api_on_admin_service(self) -> None:
        # Admin service must not expose delete methods.
        self.assertFalse(hasattr(self.admin, "delete"))
        self.assertFalse(hasattr(self.admin, "delete_package"))
        self.assertFalse(hasattr(self.admin, "hard_delete"))


if __name__ == "__main__":
    unittest.main()
