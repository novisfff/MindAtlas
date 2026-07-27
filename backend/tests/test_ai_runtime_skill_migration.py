"""Plan 10 Task 2 — migrate legacy skills to native packages / Main Agent Profile."""
from __future__ import annotations


import os
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-plan10-task2")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ASSISTANT_SKILL_PUBLISH_GATE_MODE", "observe")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ai_runtime_migration"


def _load_fixture(name: str) -> dict:
    import json

    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _current_pkg_rev(db, package_id) -> int:
    from app.assistant.skills.models import AssistantSkillPackage

    row = db.get(AssistantSkillPackage, package_id)
    return int(getattr(row, "aggregate_revision", 0) or 0) if row is not None else 0


def _current_profile_rev(db, profile_id) -> int:
    from app.assistant.skills.models import AssistantMainAgentProfile

    row = db.get(AssistantMainAgentProfile, profile_id)
    return int(getattr(row, "aggregate_revision", 0) or 0) if row is not None else 0


class SourceAdapterUnitTests(unittest.TestCase):
    def test_adapter_rejects_credentials_and_mutable_secrets(self) -> None:
        from app.assistant.migration.packages import (
            PackageMigrationError,
            portable_source_from_legacy_record,
        )

        with self.assertRaises(PackageMigrationError) as ctx:
            portable_source_from_legacy_record(
                {
                    "id": str(uuid4()),
                    "name": "custom_note_taker",
                    "enabled": True,
                    "is_system": False,
                    "description": "ok",
                    "system_prompt": "talk",
                    "tools": [{"name": "http", "api_key": "sk-secret-value"}],
                    "workflow_id": str(uuid4()),
                }
            )
        self.assertEqual(ctx.exception.reason_code, "secret_or_credential_rejected")

        with self.assertRaises(PackageMigrationError) as ctx2:
            portable_source_from_legacy_record(
                {
                    "id": str(uuid4()),
                    "name": "custom_note_taker",
                    "enabled": True,
                    "is_system": False,
                    "kb_config": {"password": "hunter2"},
                    "workflow_id": str(uuid4()),
                }
            )
        self.assertEqual(ctx2.exception.reason_code, "secret_or_credential_rejected")

    def test_general_chat_is_profile_provenance_not_skill_package(self) -> None:
        from app.assistant.migration.packages import (
            PackageMigrationError,
            classify_legacy_source,
            portable_source_from_legacy_record,
        )

        record = {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "general_chat",
            "enabled": True,
            "is_system": True,
            "agent_profile_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "description": "default conversation skill",
        }
        classification = classify_legacy_source(record)
        self.assertEqual(classification.kind, "profile")
        self.assertEqual(classification.reason_code, "general_chat_profile_target")
        with self.assertRaises(PackageMigrationError) as ctx:
            portable_source_from_legacy_record(record)
        self.assertEqual(ctx.exception.reason_code, "general_chat_not_a_skill_package")

    def test_system_order_is_locked(self) -> None:
        from app.assistant.migration.packages import SYSTEM_PACKAGE_MIGRATION_ORDER

        self.assertEqual(
            list(SYSTEM_PACKAGE_MIGRATION_ORDER),
            ["general_chat", "quick_stats", "periodic_review", "smart_capture"],
        )

    def test_write_branch_policy_never_silent_drops(self) -> None:
        from app.assistant.migration.packages import decide_write_branch_action

        self.assertEqual(
            decide_write_branch_action(
                skill_name="smart_capture",
                branch="create_entry",
                supported=True,
                plan08_evidence=True,
            ).action,
            "migrate",
        )
        for branch in ("update_entry", "merge_entry", "relation_followup"):
            decision = decide_write_branch_action(
                skill_name="smart_capture",
                branch=branch,
                supported=True,
                plan08_evidence=False,
            )
            self.assertIn(decision.action, {"block", "archive"})
            self.assertIsNotNone(decision.reason_code)
        # Plan 08 evidence does not promote non-create_entry branches.
        for branch in ("update_entry", "merge_entry", "relation_followup"):
            decision = decide_write_branch_action(
                skill_name="smart_capture",
                branch=branch,
                supported=True,
                plan08_evidence=True,
            )
            self.assertIn(decision.action, {"block", "archive"})
            self.assertNotEqual(decision.action, "migrate")
            self.assertEqual(decision.reason_code, "non_create_write_branch")


@unittest.skip("assistant_skill table removed (Plan 10 B2) — historical migrate path")
class SkillPackageMigrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-plan10-task2"
        os.environ["APP_ENV"] = "test"
        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from tests.agent_skill_test_support import create_default_model_binding

        self.db = make_session()
        create_default_model_binding(self.db)

    def tearDown(self) -> None:
        self.db.close()
        reset_caches()
        from app.config import get_settings

        get_settings.cache_clear()

    def _create_legacy_skill(
        self,
        *,
        name: str,
        target: str = "workflow",
        enabled: bool = True,
        is_system: bool = False,
        description: str = "Captures knowledge; use for structured intake.",
        intent_examples: list[str] | None = None,
    ):
        from app.assistant_config.models import AssistantSkill
        from tests.agent_skill_test_support import (
            create_published_agent,
            create_published_workflow,
        )

        if target == "workflow":
            workflow, version = create_published_workflow(
                self.db,
                name=f"{name}__workflow",
                model_source="default",
            )
            skill = AssistantSkill(
                name=name,
                description=description,
                intent_examples=intent_examples or [f"run {name}"],
                tools=[],
                mode="langgraph",
                langgraph_pattern="workflow_dag",
                is_system=is_system,
                enabled=enabled,
                workflow_id=workflow.id,
                agent_profile_id=None,
            )
            self.db.add(skill)
            self.db.commit()
            return skill, workflow, version

        agent, version = create_published_agent(
            self.db,
            name=f"{name}__agent",
            tools=["list_tags"],
            model_source="default",
        )
        skill = AssistantSkill(
            name=name,
            description=description,
            intent_examples=intent_examples or ["hello"],
            tools=["list_tags"],
            mode="langgraph",
            langgraph_pattern="agent_loop",
            is_system=is_system,
            enabled=enabled,
            workflow_id=None,
            agent_profile_id=agent.id,
        )
        self.db.add(skill)
        self.db.commit()
        return skill, agent, version

    def test_promote_general_chat_to_profile_not_skill_package(self) -> None:
        from app.assistant.migration.packages import migrate_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantSkillPackage,
        )
        from app.assistant.skills.service import MainAgentProfileService

        skill, _agent, _version = self._create_legacy_skill(
            name="general_chat",
            target="agent",
            is_system=True,
            description="Default conversation skill for MindAtlas.",
        )

        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-gc-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
            verify=False,
        )
        self.assertGreaterEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)

        # No general-chat package may exist.
        pkgs = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name.in_(("general-chat", "general_chat")))
            .all()
        )
        self.assertEqual(pkgs, [])

        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        self.assertIsNotNone(profile.published_version_id)
        self.assertIn(str(profile.migration_state), {"native", "cutover"})
        # Cutover lock should be set so legacy bridge cannot mutate.
        self.assertEqual(profile.migration_state, "cutover")

        repo = RuntimeMigrationRepository(self.db)
        item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(skill.id),
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.state, "migrated")
        self.assertEqual(item.target_type, "main_agent_profile")
        self.assertEqual(item.target_id, str(profile.id))

        # Independent verify advances to verified.
        from app.assistant.migration.packages import verify_packages

        vreport = verify_packages(
            self.db,
            request_id=f"pkg-verify-gc-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
        )
        self.assertGreaterEqual(vreport.succeeded, 1)
        self.db.refresh(item)
        self.assertEqual(item.state, "verified")

        # Legacy adapter must not overwrite cutover profile.
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter

        sync = LegacySkillShadowAdapter().sync_one(self.db, skill.id)
        self.assertEqual(sync.status, "unchanged")
        self.assertTrue(
            any(d.reason_code == "shadow_sync_stopped_native" for d in sync.diagnostics)
        )

    def test_migrate_system_packages_in_order_with_cutover_lock(self) -> None:
        from app.assistant.migration.packages import migrate_packages, verify_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage

        skills = []
        for name in ("quick_stats", "periodic_review", "smart_capture"):
            skill, _wf, _ver = self._create_legacy_skill(
                name=name,
                is_system=True,
                description=f"System skill {name} for migration tests.",
            )
            skills.append(skill)

        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-sys-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[s.id for s in skills],
            verify=False,
        )
        self.assertEqual(report.failed, 0)
        self.assertGreaterEqual(report.succeeded, 3)
        # Order recorded in report.
        migrated_names = [
            i.get("sourceNameNormalized")
            for i in report.items
            if i.get("outcome") in {"migrated", "unchanged"}
        ]
        # Relative order of system packages preserved.
        qs = migrated_names.index("quick_stats")
        pr = migrated_names.index("periodic_review")
        sc = migrated_names.index("smart_capture")
        self.assertLess(qs, pr)
        self.assertLess(pr, sc)

        repo = RuntimeMigrationRepository(self.db)
        for skill in skills:
            item = repo.get_item_by_source(
                subject_kind="skill",
                source_type="legacy_skill",
                source_id=str(skill.id),
            )
            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item.state, "migrated")
            self.assertEqual(item.target_type, "skill_package")
            package = self.db.get(AssistantSkillPackage, UUID(str(item.target_id)))
            self.assertIsNotNone(package)
            assert package is not None
            self.assertEqual(package.migration_state, "cutover")
            self.assertIsNotNone(package.published_version_id)
            self.assertFalse(package.catalog_enabled)  # traffic remains off
            self.assertEqual(package.legacy_skill_id, skill.id)

            # Legacy adapter cannot mutate cutover packages.
            sync = LegacySkillShadowAdapter().sync_one(self.db, skill.id)
            self.assertEqual(sync.status, "unchanged")
            self.assertTrue(
                any(
                    d.reason_code == "shadow_sync_stopped_native"
                    for d in sync.diagnostics
                )
            )

        vreport = verify_packages(
            self.db,
            request_id=f"pkg-verify-sys-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[s.id for s in skills],
        )
        self.assertEqual(vreport.failed, 0)
        for skill in skills:
            item = repo.get_item_by_source(
                subject_kind="skill",
                source_type="legacy_skill",
                source_id=str(skill.id),
            )
            assert item is not None
            self.assertEqual(item.state, "verified")

    def test_custom_enabled_migrates_disabled_archives(self) -> None:
        from app.assistant.migration.packages import migrate_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository

        enabled, _wf, _v = self._create_legacy_skill(
            name="custom_note_taker",
            is_system=False,
            enabled=True,
            description="User custom note taking skill for migration.",
        )
        disabled, _wf2, _v2 = self._create_legacy_skill(
            name="retired_experiment",
            is_system=False,
            enabled=False,
            description="Disabled historical skill that must be archived.",
        )

        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-custom-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[enabled.id, disabled.id],
            verify=False,
        )
        self.assertEqual(report.failed, 0)
        repo = RuntimeMigrationRepository(self.db)
        enabled_item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(enabled.id),
        )
        disabled_item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(disabled.id),
        )
        assert enabled_item is not None
        assert disabled_item is not None
        self.assertEqual(enabled_item.state, "migrated")
        self.assertEqual(disabled_item.state, "archived")
        self.assertEqual(disabled_item.reason_code, "disabled_historical_source")

    def test_smart_capture_write_branches_not_silently_dropped(self) -> None:
        from app.assistant.migration.packages import migrate_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository

        skill, _wf, _v = self._create_legacy_skill(
            name="smart_capture",
            is_system=True,
            description="Capture with write branches for migration tests.",
        )
        write_branches = [
            {
                "id": "wb-create-entry",
                "skill_name": "smart_capture",
                "branch": "create_entry",
                "supported": True,
                "plan08_evidence": True,
            },
            {
                "id": "wb-update-entry",
                "skill_name": "smart_capture",
                "branch": "update_entry",
                "supported": True,
                "plan08_evidence": False,
            },
            {
                "id": "wb-merge-entry",
                "skill_name": "smart_capture",
                "branch": "merge_entry",
                "supported": True,
                "plan08_evidence": False,
            },
            {
                "id": "wb-update-entry-evidenced",
                "skill_name": "smart_capture",
                "branch": "update_entry",
                "supported": True,
                "plan08_evidence": True,
            },
        ]
        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-wb-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
            write_branches=write_branches,
            verify=False,
        )
        self.assertEqual(report.failed, 0)
        repo = RuntimeMigrationRepository(self.db)
        create_item = repo.get_item_by_source(
            subject_kind="write_branch",
            source_type="legacy_write_branch",
            source_id="wb-create-entry",
        )
        update_item = repo.get_item_by_source(
            subject_kind="write_branch",
            source_type="legacy_write_branch",
            source_id="wb-update-entry",
        )
        merge_item = repo.get_item_by_source(
            subject_kind="write_branch",
            source_type="legacy_write_branch",
            source_id="wb-merge-entry",
        )
        evidenced_update = repo.get_item_by_source(
            subject_kind="write_branch",
            source_type="legacy_write_branch",
            source_id="wb-update-entry-evidenced",
        )
        assert create_item is not None
        assert update_item is not None
        assert merge_item is not None
        assert evidenced_update is not None
        self.assertEqual(create_item.state, "migrated")
        self.assertIn(update_item.state, {"blocked", "archived"})
        self.assertIn(merge_item.state, {"blocked", "archived"})
        self.assertIn(evidenced_update.state, {"blocked", "archived"})
        self.assertNotEqual(evidenced_update.state, "migrated")
        self.assertEqual(evidenced_update.reason_code, "non_create_write_branch")
        self.assertNotEqual(update_item.state, "discovered")
        self.assertNotEqual(merge_item.state, "discovered")

    def test_migrate_blocks_secret_bearing_legacy_skill(self) -> None:
        from app.assistant.migration.packages import migrate_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from app.assistant.skills.models import AssistantSkillPackage

        skill, _wf, _v = self._create_legacy_skill(
            name="secret_custom_skill",
            is_system=False,
            enabled=True,
            description="Custom skill with credential-like tools config.",
        )
        skill.tools = [{"name": "http", "api_key": "sk-secret-value"}]
        skill.kb_config = {"password": "hunter2"}
        self.db.commit()

        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-secret-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
            verify=False,
        )
        self.assertGreaterEqual(report.blocked, 1)
        repo = RuntimeMigrationRepository(self.db)
        item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(skill.id),
        )
        assert item is not None
        self.assertEqual(item.state, "blocked")
        self.assertEqual(item.reason_code, "secret_or_credential_rejected")
        packages = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.legacy_skill_id == skill.id)
            .all()
        )
        self.assertEqual(packages, [])
        published = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.published_version_id.isnot(None))
            .all()
        )
        # No package published for this secret-bearing skill (may have unrelated
        # system packages from fixtures — scope by legacy_skill_id already empty).
        self.assertTrue(all(p.legacy_skill_id != skill.id for p in published))

    def test_rerun_is_idempotent(self) -> None:
        from app.assistant.migration.packages import migrate_packages, verify_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from app.assistant.skills.models import AssistantSkillPackage

        skill, _wf, _v = self._create_legacy_skill(
            name="quick_stats",
            is_system=True,
            description="Read-only stats skill for idempotent migration.",
        )
        kwargs = dict(
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
            verify=True,
        )
        first = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-idemp-1-{uuid4()}",
            **kwargs,
        )
        self.assertEqual(first.failed, 0)
        repo = RuntimeMigrationRepository(self.db)
        item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(skill.id),
        )
        assert item is not None
        self.assertEqual(item.state, "verified")
        target_id = item.target_id
        package = self.db.get(AssistantSkillPackage, UUID(str(target_id)))
        assert package is not None
        published_before = package.published_version_id
        rev_before = package.aggregate_revision

        second = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-idemp-2-{uuid4()}",
            **kwargs,
        )
        self.assertEqual(second.failed, 0)
        self.db.refresh(item)
        self.db.refresh(package)
        self.assertEqual(item.state, "verified")
        self.assertEqual(item.target_id, target_id)
        self.assertEqual(package.published_version_id, published_before)
        # Aggregate may stay same or only advance if no mutation; either way package stays cutover.
        self.assertEqual(package.migration_state, "cutover")
        self.assertGreaterEqual(int(package.aggregate_revision or 0), int(rev_before or 0))

        # Explicit verify rerun also idempotent.
        v2 = verify_packages(
            self.db,
            request_id=f"pkg-verify-idemp-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
        )
        self.assertEqual(v2.failed, 0)

    def test_missing_target_blocks_item(self) -> None:
        from app.assistant.migration.packages import migrate_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository

        # Create skill bound to a workflow, then clear the published pointer so
        # migration sees target_unpublished / target_missing.
        skill, workflow, _version = self._create_legacy_skill(
            name="orphan_custom",
            is_system=False,
            enabled=True,
            description="Skill whose target loses its published version.",
        )
        workflow.published_version_id = None
        self.db.commit()

        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-orphan-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
            verify=False,
        )
        self.assertGreaterEqual(report.blocked, 1)
        repo = RuntimeMigrationRepository(self.db)
        item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(skill.id),
        )
        assert item is not None
        self.assertEqual(item.state, "blocked")
        self.assertIn(item.reason_code, {"target_missing", "target_unpublished"})

    def test_alias_collision_blocks_mapping(self) -> None:
        from app.assistant.migration.packages import migrate_packages
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from app.assistant.skills.models import (
            AssistantSkillPackage,
            AssistantSkillPackageAlias,
        )
        from app.assistant.skills.contracts import normalize_skill_lookup_name

        # Pre-occupy the canonical/alias space for "quick-stats".
        occupied = AssistantSkillPackage(
            canonical_name="quick-stats",
            display_name="Occupied",
            description="Already taken package",
            migration_state="native",
            catalog_enabled=False,
            is_system=False,
        )
        self.db.add(occupied)
        self.db.flush()
        self.db.add(
            AssistantSkillPackageAlias(
                skill_package_id=occupied.id,
                alias="quick_stats",
                normalized_alias=normalize_skill_lookup_name("quick_stats"),
                alias_type="legacy",
            )
        )
        self.db.commit()

        skill, _wf, _v = self._create_legacy_skill(
            name="quick_stats",
            is_system=True,
            description="Conflicts with occupied package alias.",
        )
        report = migrate_packages(
            self.db,
            request_id=f"pkg-migrate-collision-{uuid4()}",
            actor_principal="operator:task2",
            build_revision="test-build-plan10-task2",
            environment="test",
            database_fingerprint="sqlite-test",
            schema_head="6417df0243be",
            dry_run=False,
            skill_ids=[skill.id],
            verify=False,
        )
        self.assertGreaterEqual(report.blocked, 1)
        repo = RuntimeMigrationRepository(self.db)
        item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id=str(skill.id),
        )
        assert item is not None
        self.assertEqual(item.state, "blocked")
        self.assertIn(
            item.reason_code,
            {"canonical_name_collision", "alias_collision", "package_legacy_skill_collision"},
        )


@unittest.skip("assistant_skill table removed (Plan 10 B2) — historical migrate path")
class PackagesCliTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-plan10-task2"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from tests.agent_skill_test_support import create_default_model_binding

        self.db = make_session()
        create_default_model_binding(self.db)

    def tearDown(self) -> None:
        self.db.close()
        reset_caches()

    def test_cli_packages_migrate_and_verify_not_stub(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from app.assistant.migration.cli import main
        from app.assistant_config.models import AssistantSkill
        from tests.agent_skill_test_support import create_published_workflow

        workflow, _version = create_published_workflow(
            self.db,
            name="cli_quick_stats__workflow",
            model_source="default",
        )
        skill = AssistantSkill(
            name="quick_stats",
            description="CLI migration path for quick stats skill package.",
            intent_examples=["stats"],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            is_system=True,
            enabled=True,
            workflow_id=workflow.id,
            agent_profile_id=None,
        )
        self.db.add(skill)
        self.db.commit()
        skill_id = str(skill.id)

        def factory():
            # Return the same bound session for CLI injection.
            return self.db

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "migrate.json"
            code = main(
                [
                    "packages",
                    "migrate",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "sqlite-test",
                    "--source-snapshot-digest",
                    "a" * 64,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "test-build-plan10-task2",
                    "--request-id",
                    f"cli-pkg-migrate-{uuid4()}",
                    "--batch-size",
                    "50",
                    "--apply",
                    "--report-json",
                    str(report_path),
                    "--operator-principal",
                    "operator:cli",
                    "--skill-id",
                    skill_id,
                ],
                session_factory=factory,
            )
            self.assertIn(code, {0, 2})
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("command"), "packages.migrate")
            self.assertTrue(payload.get("ok"))

            vpath = Path(tmp) / "verify.json"
            vcode = main(
                [
                    "packages",
                    "verify",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "sqlite-test",
                    "--source-snapshot-digest",
                    "a" * 64,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "test-build-plan10-task2",
                    "--request-id",
                    f"cli-pkg-verify-{uuid4()}",
                    "--batch-size",
                    "50",
                    "--apply",
                    "--report-json",
                    str(vpath),
                    "--operator-principal",
                    "operator:cli",
                    "--skill-id",
                    skill_id,
                ],
                session_factory=factory,
            )
            self.assertIn(vcode, {0, 2})
            vpayload = json.loads(vpath.read_text(encoding="utf-8"))
            self.assertEqual(vpayload.get("command"), "packages.verify")
            self.assertTrue(vpayload.get("ok"))


if __name__ == "__main__":
    unittest.main()
