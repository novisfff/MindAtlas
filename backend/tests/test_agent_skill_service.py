from __future__ import annotations

import inspect
import unittest
import uuid
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "agent_skills" / "valid-weekly-review"
)


def _fixture_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(FIXTURE_ROOT.rglob("*")):
        if path.is_file():
            rel = path.relative_to(FIXTURE_ROOT).as_posix()
            files[rel] = path.read_bytes()
    return files


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
    capabilities: str | None = None,
) -> bytes:
    aliases = legacy_aliases if legacy_aliases is not None else ["weekly_review"]
    alias_block = "\n".join(f"  - {a}" for a in aliases) if aliases else "  []"
    caps = capabilities
    if caps is None:
        caps = (
            "  - type: tool\n"
            "    key: search_entries\n"
            "  - type: workflow\n"
            "    key: periodic_review__workflow\n"
        )
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
        f"{caps}"
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
    ).encode("utf-8")


def _parse(
    *,
    name: str = "weekly-review",
    skill_md: bytes | None = None,
    mindatlas: bytes | None = None,
    resources: dict[str, bytes] | None = None,
    include_mindatlas: bool = True,
):
    from app.assistant.skills.package_io import parse_skill_directory_files

    files: dict[str, bytes] = {
        "SKILL.md": skill_md if skill_md is not None else _minimal_skill_md(name=name),
    }
    if include_mindatlas:
        files["mindatlas.yaml"] = (
            mindatlas if mindatlas is not None else _mindatlas_yaml()
        )
    if resources:
        files.update(resources)
    return parse_skill_directory_files(files, expected_root_name=None)


class AgentSkillServiceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()
        from app.assistant.skills.service import AgentSkillService

        self.svc = AgentSkillService(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_create_native_package_reserves_name_and_aliases_atomically(self) -> None:
        from app.assistant.skills.models import (
            AssistantSkillPackage,
            AssistantSkillPackageAlias,
        )
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        parsed = _parse()
        detail = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="v1")
        )
        self.assertEqual(detail.canonical_name, "weekly-review")
        self.assertEqual(detail.migration_state, "native")
        self.assertFalse(detail.catalog_enabled)
        self.assertIsNotNone(detail.draft_version)
        self.assertEqual(detail.draft_version.sequence_no, 1)
        self.assertEqual(detail.draft_version.version_source, "save")
        self.assertIsNone(detail.published_version)

        pkg = self.db.get(AssistantSkillPackage, detail.id)
        self.assertIsNotNone(pkg)
        assert pkg is not None
        self.assertEqual(pkg.migration_state, "native")
        self.assertFalse(pkg.catalog_enabled)

        aliases = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == pkg.id)
            .all()
        )
        by_type = {a.alias_type: a for a in aliases}
        self.assertIn("canonical", by_type)
        self.assertEqual(by_type["canonical"].alias, "weekly-review")
        self.assertEqual(by_type["canonical"].normalized_alias, "weekly-review")
        legacy = [a for a in aliases if a.alias_type == "legacy"]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0].alias, "weekly_review")
        self.assertEqual(legacy[0].normalized_alias, "weekly_review")

    def test_canonical_and_alias_share_collision_namespace(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="alpha-skill",
                    mindatlas=_mindatlas_yaml(legacy_aliases=["alpha_legacy"]),
                ),
                version_name="v1",
            )
        )
        # Another package whose legacy alias collides with existing canonical.
        with self.assertRaises(ApiException) as ctx:
            self.svc.create_native_package(
                CreateSkillPackageCommand(
                    parsed=_parse(
                        name="beta-skill",
                        mindatlas=_mindatlas_yaml(legacy_aliases=["Alpha-Skill"]),
                    ),
                    version_name="v1",
                )
            )
        self.assertEqual(ctx.exception.code, 40991)

        # Create a package that owns weekly_review as legacy, then collide.
        self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="weekly-review",
                    mindatlas=_mindatlas_yaml(legacy_aliases=["weekly_review"]),
                ),
                version_name="v1",
            )
        )
        with self.assertRaises(ApiException) as ctx2:
            self.svc.create_native_package(
                CreateSkillPackageCommand(
                    parsed=_parse(
                        name="weekly-review-2",
                        mindatlas=_mindatlas_yaml(legacy_aliases=["weekly_review"]),
                    ),
                    version_name="v1",
                )
            )
        self.assertEqual(ctx2.exception.code, 40991)

    def test_canonical_name_cannot_change_on_save(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )

        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=_parse(name="fixed-name"), version_name="v1")
        )
        with self.assertRaises(ApiException) as ctx:
            self.svc.save_draft(
                SaveSkillDraftCommand(
                    package_id=created.id,
                    parsed=_parse(name="other-name"),
                    version_name="v2",
                )
            )
        self.assertEqual(ctx.exception.code, 40990)

    def test_alias_append_only_and_nfkc_casefold_collision(self) -> None:
        from app.assistant.skills.models import AssistantSkillPackageAlias
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )
        from app.common.exceptions import ApiException

        created = self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="alias-pack",
                    mindatlas=_mindatlas_yaml(legacy_aliases=["Alias_One"]),
                ),
                version_name="v1",
            )
        )
        # Saving with same legacy aliases does not rewrite rows.
        before = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == created.id)
            .count()
        )
        self.svc.save_draft(
            SaveSkillDraftCommand(
                package_id=created.id,
                parsed=_parse(
                    name="alias-pack",
                    skill_md=_minimal_skill_md(name="alias-pack", body="# changed\n"),
                    mindatlas=_mindatlas_yaml(legacy_aliases=["Alias_One"]),
                ),
                version_name="v2",
            )
        )
        after = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == created.id)
            .count()
        )
        self.assertEqual(before, after)

        # New legacy alias is append-only.
        self.svc.save_draft(
            SaveSkillDraftCommand(
                package_id=created.id,
                parsed=_parse(
                    name="alias-pack",
                    skill_md=_minimal_skill_md(name="alias-pack", body="# again\n"),
                    mindatlas=_mindatlas_yaml(legacy_aliases=["Alias_One", "Alias_Two"]),
                ),
                version_name="v3",
            )
        )
        aliases = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == created.id)
            .all()
        )
        norms = {a.normalized_alias for a in aliases}
        self.assertIn("alias_two", norms)

        # NFKC/casefold collision with another package's alias.
        with self.assertRaises(ApiException) as ctx:
            self.svc.create_native_package(
                CreateSkillPackageCommand(
                    parsed=_parse(
                        name="other-pack",
                        mindatlas=_mindatlas_yaml(legacy_aliases=["ALIAS_ONE"]),
                    ),
                    version_name="v1",
                )
            )
        self.assertEqual(ctx.exception.code, 40991)

    def test_reserved_main_agent_names_unavailable(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        # Both reserved Main Agent names are unavailable as canonical names.
        with self.assertRaises(Exception):
            _parse(name="general-chat", include_mindatlas=False)
        with self.assertRaises(Exception):
            _parse(name="general_chat", include_mindatlas=False)

        # Parser also rejects them as legacy aliases.
        for reserved in ("general_chat", "general-chat"):
            with self.assertRaises(Exception):
                _parse(
                    name="near-reserved",
                    mindatlas=_mindatlas_yaml(legacy_aliases=[reserved]),
                )

        # Service-layer defense rejects reserved aliases on append-only path.
        created = self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="near-reserved",
                    mindatlas=_mindatlas_yaml(legacy_aliases=[]),
                ),
                version_name="v1",
            )
        )
        for reserved in ("general_chat", "general-chat"):
            with self.assertRaises(ApiException) as ctx:
                self.svc._append_legacy_aliases(
                    package_id=created.id,
                    legacy_aliases=[reserved],
                )
            self.assertEqual(ctx.exception.code, 40991)

    def test_first_save_sequence_and_identical_content_returns_existing(self) -> None:
        from app.assistant.skills.models import AssistantSkillVersion
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )

        parsed = _parse(name="seq-pack")
        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )
        self.assertEqual(created.draft_version.sequence_no, 1)
        first_id = created.draft_version.id

        again = self.svc.save_draft(
            SaveSkillDraftCommand(
                package_id=created.id,
                parsed=parsed,
                version_name="draft-1-again",
            )
        )
        self.assertEqual(again.id, first_id)
        self.assertEqual(again.sequence_no, 1)
        detail = self.svc.get_package(created.id)
        self.assertEqual(detail.draft_version.id, first_id)

        count = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == created.id)
            .count()
        )
        self.assertEqual(count, 1)

    def test_identical_content_points_draft_at_older_owned_save(self) -> None:
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )

        p1 = _parse(
            name="pointer-pack",
            skill_md=_minimal_skill_md(name="pointer-pack", body="# one\n"),
        )
        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=p1, version_name="s1")
        )
        v1_id = created.draft_version.id

        p2 = _parse(
            name="pointer-pack",
            skill_md=_minimal_skill_md(name="pointer-pack", body="# two\n"),
        )
        v2 = self.svc.save_draft(
            SaveSkillDraftCommand(package_id=created.id, parsed=p2, version_name="s2")
        )
        self.assertEqual(v2.sequence_no, 2)
        self.assertNotEqual(v2.id, v1_id)

        # Re-save identical to v1: pointer moves back to v1, no new row.
        restored = self.svc.save_draft(
            SaveSkillDraftCommand(package_id=created.id, parsed=p1, version_name="s1-again")
        )
        self.assertEqual(restored.id, v1_id)
        detail = self.svc.get_package(created.id)
        self.assertEqual(detail.draft_version.id, v1_id)
        self.assertEqual(detail.draft_version.sequence_no, 1)

    def test_changed_content_appends_sequence_without_mutating_history(self) -> None:
        from app.assistant.skills.models import (
            AssistantSkillCapabilityBinding,
            AssistantSkillVersion,
            AssistantSkillVersionResource,
        )
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )

        p1 = _parse(
            name="hist-pack",
            resources={"references/a.md": b"# a\n"},
        )
        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=p1, version_name="s1")
        )
        v1 = self.db.get(AssistantSkillVersion, created.draft_version.id)
        assert v1 is not None
        v1_skill_md = v1.skill_md
        v1_content_digest = v1.content_digest
        v1_res_count = (
            self.db.query(AssistantSkillVersionResource)
            .filter(AssistantSkillVersionResource.skill_version_id == v1.id)
            .count()
        )
        v1_bind_count = (
            self.db.query(AssistantSkillCapabilityBinding)
            .filter(AssistantSkillCapabilityBinding.skill_version_id == v1.id)
            .count()
        )

        p2 = _parse(
            name="hist-pack",
            skill_md=_minimal_skill_md(name="hist-pack", body="# changed body\n"),
            resources={"references/a.md": b"# a\n", "references/b.md": b"# b\n"},
        )
        v2 = self.svc.save_draft(
            SaveSkillDraftCommand(package_id=created.id, parsed=p2, version_name="s2")
        )
        self.assertEqual(v2.sequence_no, 2)

        # Older version immutable.
        v1_reload = self.db.get(AssistantSkillVersion, v1.id)
        assert v1_reload is not None
        self.assertEqual(v1_reload.skill_md, v1_skill_md)
        self.assertEqual(v1_reload.content_digest, v1_content_digest)
        self.assertEqual(
            self.db.query(AssistantSkillVersionResource)
            .filter(AssistantSkillVersionResource.skill_version_id == v1.id)
            .count(),
            v1_res_count,
        )
        self.assertEqual(
            self.db.query(AssistantSkillCapabilityBinding)
            .filter(AssistantSkillCapabilityBinding.skill_version_id == v1.id)
            .count(),
            v1_bind_count,
        )

    def test_list_get_package_and_version_never_expose_resource_bytes(self) -> None:
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        created = self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="bytes-pack",
                    resources={"references/guide.md": b"secret-bytes-xyz"},
                ),
                version_name="s1",
            )
        )
        listed = self.svc.list_packages()
        self.assertTrue(any(p.id == created.id for p in listed))
        for item in listed:
            payload = item.model_dump()
            self.assertNotIn("content", payload)
            dumped = str(payload)
            self.assertNotIn("secret-bytes-xyz", dumped)

        detail = self.svc.get_package(created.id)
        self.assertIsNotNone(detail.draft_version)
        self.assertNotIn("secret-bytes-xyz", str(detail.model_dump()))

        versions = self.svc.list_versions(created.id)
        self.assertEqual(len(versions), 1)
        self.assertNotIn("secret-bytes-xyz", str(versions[0].model_dump()))

        version = self.svc.get_version(created.id, created.draft_version.id)
        self.assertTrue(version.resources)
        for res in version.resources:
            self.assertEqual(res.path, "references/guide.md")
            self.assertFalse(hasattr(res, "content") and getattr(res, "content", None))
            self.assertNotIn("content", res.model_dump())
        self.assertNotIn("secret-bytes-xyz", str(version.model_dump()))

        raw = self.svc.get_resource_bytes(
            created.id, created.draft_version.id, "references/guide.md"
        )
        self.assertEqual(raw, b"secret-bytes-xyz")

    def test_get_resource_bytes_fails_closed_when_blob_content_corrupted(self) -> None:
        from app.assistant.skills.models import (
            AssistantSkillResourceBlob,
            AssistantSkillVersionResource,
        )
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.common.exceptions import ApiException

        original = b"trusted-resource-bytes"
        created = self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="corrupt-blob-pack",
                    resources={"references/guide.md": original},
                ),
                version_name="s1",
            )
        )
        resource = (
            self.db.query(AssistantSkillVersionResource)
            .filter(
                AssistantSkillVersionResource.skill_version_id
                == created.draft_version.id,
                AssistantSkillVersionResource.path == "references/guide.md",
            )
            .one()
        )
        blob = self.db.get(AssistantSkillResourceBlob, resource.blob_id)
        assert blob is not None
        stored_sha = blob.sha256
        stored_size = blob.byte_size
        # Corrupt payload in place while leaving digest/size columns unchanged.
        blob.content = b"x" * stored_size
        self.db.commit()
        reloaded = self.db.get(AssistantSkillResourceBlob, resource.blob_id)
        assert reloaded is not None
        self.assertEqual(reloaded.sha256, stored_sha)
        self.assertEqual(reloaded.byte_size, stored_size)
        self.assertEqual(len(reloaded.content), stored_size)
        self.assertNotEqual(bytes(reloaded.content), original)

        with self.assertRaises(ApiException) as ctx:
            self.svc.get_resource_bytes(
                created.id, created.draft_version.id, "references/guide.md"
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40993)

    def test_native_create_cannot_assign_shadow_and_stays_catalog_disabled(self) -> None:
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.models import AssistantSkillPackage

        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=_parse(name="native-only"), version_name="s1")
        )
        self.assertEqual(created.migration_state, "native")
        self.assertFalse(created.catalog_enabled)
        pkg = self.db.get(AssistantSkillPackage, created.id)
        assert pkg is not None
        self.assertEqual(pkg.migration_state, "native")
        self.assertFalse(pkg.catalog_enabled)

        # create method has no migration_state parameter.
        sig = inspect.signature(self.svc.create_native_package)
        self.assertNotIn("migration_state", sig.parameters)

    def test_resource_blob_dedup_and_quota_and_no_orphan_on_failure(self) -> None:
        from app.assistant.skills.models import AssistantSkillResourceBlob
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            SaveSkillDraftCommand,
        )
        from app.assistant.skills import service as skill_service_mod
        from app.common.exceptions import ApiException

        content = b"shared-resource-body"
        created = self.svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=_parse(
                    name="blob-pack",
                    resources={"references/a.md": content},
                ),
                version_name="s1",
            )
        )
        blob_count_1 = self.db.query(AssistantSkillResourceBlob).count()
        self.assertEqual(blob_count_1, 1)

        # Second draft with same bytes reuses blob.
        self.svc.save_draft(
            SaveSkillDraftCommand(
                package_id=created.id,
                parsed=_parse(
                    name="blob-pack",
                    skill_md=_minimal_skill_md(name="blob-pack", body="# v2\n"),
                    resources={"references/a.md": content, "references/b.md": content},
                ),
                version_name="s2",
            )
        )
        self.assertEqual(self.db.query(AssistantSkillResourceBlob).count(), 1)

        # Force a tiny quota so the next distinct blob exceeds it.
        original = skill_service_mod.MAX_PACKAGE_DISTINCT_BLOB_BYTES
        skill_service_mod.MAX_PACKAGE_DISTINCT_BLOB_BYTES = len(content)
        try:
            blobs_before = self.db.query(AssistantSkillResourceBlob).count()
            with self.assertRaises(ApiException) as ctx:
                self.svc.save_draft(
                    SaveSkillDraftCommand(
                        package_id=created.id,
                        parsed=_parse(
                            name="blob-pack",
                            skill_md=_minimal_skill_md(name="blob-pack", body="# v3\n"),
                            resources={
                                "references/a.md": content,
                                "references/new.md": b"brand-new-distinct-bytes",
                            },
                        ),
                        version_name="s3",
                    )
                )
            self.assertEqual(ctx.exception.code, 41391)
            # Failed save must not leave unreferenced new blob.
            self.assertEqual(
                self.db.query(AssistantSkillResourceBlob).count(), blobs_before
            )
        finally:
            skill_service_mod.MAX_PACKAGE_DISTINCT_BLOB_BYTES = original

    def test_resolve_published_alias_returns_frozen_owned_ref(self) -> None:
        from app.assistant.domain.contracts import ResolvedSkillRef
        from app.assistant.skills.models import (
            AssistantSkillPackage,
            AssistantSkillVersion,
        )
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.common.exceptions import ApiException

        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=_parse(name="resolve-pack"), version_name="s1")
        )
        draft = self.db.get(AssistantSkillVersion, created.draft_version.id)
        assert draft is not None

        # Manually insert a publish row (publish service is Task 5).
        pub = AssistantSkillVersion(
            skill_package_id=created.id,
            sequence_no=2,
            version_name="publish-1",
            version_source="publish",
            source_draft_version_id=draft.id,
            origin="api",
            skill_md=draft.skill_md,
            mindatlas_yaml=draft.mindatlas_yaml,
            frontmatter=draft.frontmatter,
            extension_manifest=draft.extension_manifest,
            resource_index=draft.resource_index,
            skill_md_digest=draft.skill_md_digest,
            manifest_digest=draft.manifest_digest,
            resource_index_digest=draft.resource_index_digest,
            content_digest=draft.content_digest,
            binding_set_digest="b" * 64,
            version_digest="c" * 64,
        )
        self.db.add(pub)
        self.db.flush()
        pkg = self.db.get(AssistantSkillPackage, created.id)
        assert pkg is not None
        pkg.published_version_id = pub.id
        self.db.commit()

        ref = self.svc.resolve_published_alias("resolve-pack")
        self.assertIsInstance(ref, ResolvedSkillRef)
        self.assertEqual(ref.package_id, created.id)
        self.assertEqual(ref.version_id, pub.id)
        self.assertEqual(ref.sequence, 2)
        self.assertEqual(ref.content_digest, draft.content_digest)
        self.assertEqual(ref.version_digest, "c" * 64)
        self.assertEqual(ref.canonical_name, "resolve-pack")
        self.assertEqual(ref.requested_name_normalized, "resolve-pack")
        self.assertIsNotNone(ref.resolved_via_alias_id)

        # Also resolve via legacy alias.
        ref2 = self.svc.resolve_published_alias("weekly_review")
        self.assertEqual(ref2.version_id, pub.id)

        # Move published pointer to a second publish row; previously returned ref stays frozen.
        pub2 = AssistantSkillVersion(
            skill_package_id=created.id,
            sequence_no=3,
            version_name="publish-2",
            version_source="publish",
            source_draft_version_id=draft.id,
            origin="api",
            skill_md=draft.skill_md,
            mindatlas_yaml=draft.mindatlas_yaml,
            frontmatter=draft.frontmatter,
            extension_manifest=draft.extension_manifest,
            resource_index=draft.resource_index,
            skill_md_digest=draft.skill_md_digest,
            manifest_digest=draft.manifest_digest,
            resource_index_digest=draft.resource_index_digest,
            content_digest=draft.content_digest,
            binding_set_digest="d" * 64,
            version_digest="e" * 64,
        )
        self.db.add(pub2)
        self.db.flush()
        pkg.published_version_id = pub2.id
        self.db.commit()

        self.assertEqual(ref.version_id, pub.id)
        self.assertEqual(ref.version_digest, "c" * 64)

        ref_now = self.svc.resolve_published_alias("resolve-pack")
        self.assertEqual(ref_now.version_id, pub2.id)

        with self.assertRaises(ApiException) as ctx:
            self.svc.resolve_published_alias("missing-skill-name")
        self.assertEqual(ctx.exception.code, 40490)

    def test_no_public_update_or_delete_for_immutable_rows(self) -> None:
        public = [
            name
            for name, member in inspect.getmembers(
                type(self.svc), predicate=inspect.isfunction
            )
            if not name.startswith("_")
        ]
        forbidden_substrings = (
            "update_version",
            "delete_version",
            "update_resource",
            "delete_resource",
            "update_binding",
            "delete_binding",
            "delete_package",
            "update_alias",
            "delete_alias",
            "purge_blob",
        )
        for name in public:
            for bad in forbidden_substrings:
                self.assertNotEqual(name, bad)
                self.assertFalse(name.startswith(bad))

        # Ensure expected public surface exists.
        for required in (
            "create_native_package",
            "save_draft",
            "list_packages",
            "get_package",
            "list_versions",
            "get_version",
            "get_resource_bytes",
            "resolve_published_alias",
        ):
            self.assertTrue(hasattr(self.svc, required), msg=required)

    def test_duplicate_canonical_name_conflict(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=_parse(name="dup-name"), version_name="s1")
        )
        with self.assertRaises(ApiException) as ctx:
            self.svc.create_native_package(
                CreateSkillPackageCommand(
                    parsed=_parse(
                        name="dup-name",
                        mindatlas=_mindatlas_yaml(legacy_aliases=["other_alias"]),
                    ),
                    version_name="s1",
                )
            )
        self.assertEqual(ctx.exception.code, 40990)

    def test_shadow_package_advances_to_native_on_save(self) -> None:
        from app.assistant.skills.models import (
            AssistantSkillPackage,
            AssistantSkillPackageAlias,
        )
        from app.assistant.skills.schemas import SaveSkillDraftCommand

        pkg = AssistantSkillPackage(
            canonical_name="shadow-pack",
            display_name="Shadow",
            description="legacy shadow",
            migration_state="shadow",
            catalog_enabled=False,
            is_system=False,
        )
        self.db.add(pkg)
        self.db.flush()
        self.db.add(
            AssistantSkillPackageAlias(
                skill_package_id=pkg.id,
                alias="shadow-pack",
                normalized_alias="shadow-pack",
                alias_type="canonical",
            )
        )
        self.db.commit()

        detail = self.svc.save_draft(
            SaveSkillDraftCommand(
                package_id=pkg.id,
                parsed=_parse(name="shadow-pack", include_mindatlas=False),
                version_name="native-draft",
            )
        )
        self.assertEqual(detail.sequence_no, 1)
        reloaded = self.db.get(AssistantSkillPackage, pkg.id)
        assert reloaded is not None
        self.assertEqual(reloaded.migration_state, "native")

    def test_package_and_version_not_found_codes(self) -> None:
        from app.common.exceptions import ApiException

        missing = uuid.uuid4()
        with self.assertRaises(ApiException) as ctx:
            self.svc.get_package(missing)
        self.assertEqual(ctx.exception.code, 40490)

        created = self.svc.create_native_package(
            __import__(
                "app.assistant.skills.schemas", fromlist=["CreateSkillPackageCommand"]
            ).CreateSkillPackageCommand(
                parsed=_parse(name="found-pack"), version_name="s1"
            )
        )
        with self.assertRaises(ApiException) as ctx2:
            self.svc.get_version(created.id, missing)
        self.assertEqual(ctx2.exception.code, 40491)

        with self.assertRaises(ApiException) as ctx3:
            self.svc.get_resource_bytes(
                created.id, created.draft_version.id, "missing/path.md"
            )
        self.assertEqual(ctx3.exception.code, 40492)

    def test_request_schemas_forbid_extra_and_publish_only_draft_id(self) -> None:
        from pydantic import ValidationError
        from app.assistant.skills.schemas import (
            PublishMainAgentProfileCommand,
            PublishSkillVersionCommand,
            SkillPackageJsonCreateRequest,
            SkillPackageJsonSaveRequest,
        )

        with self.assertRaises(ValidationError):
            SkillPackageJsonCreateRequest.model_validate(
                {
                    "skillMd": "x",
                    "mediaType": "text/markdown",
                }
            )
        with self.assertRaises(ValidationError):
            SkillPackageJsonSaveRequest.model_validate(
                {
                    "skillMd": "x",
                    "contentDigest": "a" * 64,
                }
            )
        with self.assertRaises(ValidationError):
            PublishSkillVersionCommand.model_validate(
                {"draftVersionId": str(uuid.uuid4()), "latest": True}
            )
        cmd = PublishSkillVersionCommand(draft_version_id=uuid.uuid4())
        self.assertIsInstance(cmd.draft_version_id, uuid.UUID)
        cmd2 = PublishMainAgentProfileCommand(draft_version_id=uuid.uuid4())
        self.assertIsInstance(cmd2.draft_version_id, uuid.UUID)

    def test_unresolved_bindings_created_for_manifest_capabilities(self) -> None:
        from app.assistant.skills.models import AssistantSkillCapabilityBinding
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        created = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=_parse(name="bind-pack"), version_name="s1")
        )
        bindings = (
            self.db.query(AssistantSkillCapabilityBinding)
            .filter(
                AssistantSkillCapabilityBinding.skill_version_id
                == created.draft_version.id
            )
            .order_by(AssistantSkillCapabilityBinding.ordinal)
            .all()
        )
        self.assertEqual(len(bindings), 2)
        self.assertEqual(bindings[0].capability_type, "tool")
        self.assertEqual(bindings[0].capability_key, "search_entries")
        self.assertEqual(bindings[0].resolution_status, "unresolved")
        self.assertEqual(bindings[1].capability_type, "workflow")
        self.assertEqual(bindings[1].capability_key, "periodic_review__workflow")
        self.assertIsNone(bindings[0].resolution_snapshot)
        self.assertIsNone(bindings[0].binding_contract_digest)


if __name__ == "__main__":
    unittest.main()
