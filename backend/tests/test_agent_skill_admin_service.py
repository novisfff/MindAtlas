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


def _passing_gate_metrics() -> dict:
    return {
        "all_cases": 100,
        "recall_at_8": 0.95,
        "false_injection_rate": 0.01,
        "direct_answer_accuracy": 0.95,
        "capability_path_accuracy": 0.90,
        "completion_success": 0.95,
        "legacy_completion_success": 0.95,
        "completion_success_delta_vs_legacy": 0.0,
        "unauthorized_broader_side_effect_count": 0,
        "positive_cases": 50,
        "direct_answer_cases": 20,
        "real_side_effect_in_test": 0,
        "budget_policy_bypass": 0,
        "false_completion_pending_obligation": 0,
        "unresolved_obligation_falsely_completed": 0,
        "schema_escape": 0,
        "secret_exposure": 0,
        "duplicate_write": 0,
    }


def _create_passing_enable_gate(
    db,
    *,
    package_id,
    version_id,
    content_digest,
    binding_digest,
    package_canonical_name: str | None = None,
):
    """Create a completed dataset_scripted run + server-derived passing enable gate."""
    import uuid as _uuid_mod

    from app.assistant.evaluation.assertions import THRESHOLD_POLICY_VERSION
    from app.assistant.evaluation.gates import (
        PublishGateService,
        build_publish_gate_subject,
        current_build_revision,
        current_gate_environment_pins,
        make_create_gate_request,
        skill_catalog_pin_digest,
    )
    from app.assistant.evaluation.repository import EvaluationRepository
    from app.assistant.skills.models import AssistantSkillPackage

    repo = EvaluationRepository(db)
    dataset = repo.create_dataset(
        stable_key=f"ds-admin-{_uuid_mod.uuid4().hex[:8]}",
        display_name="Admin Gate DS",
        ownership="custom",
    )
    digest_e = "e" * 64
    snapshot = [
        {
            "case_key": "c1",
            "ordinal": 0,
            "locale": "en",
            "input_messages": [{"role": "user", "content": "hi"}],
            "expected_mode": "golden_skill",
            "case_digest": digest_e,
        }
    ]
    repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
    published = repo.publish_dataset_version(
        dataset_id=dataset.id,
        expected_aggregate_revision=0,
        expected_draft_revision=0,
        version_name="v1",
        actor="tester",
    )
    db.commit()
    build_rev = current_build_revision()
    run = repo.create_run(
        subject_kind="skill_version",
        subject_aggregate_id=package_id,
        subject_version_id=version_id,
        subject_content_digest=content_digest,
        subject_binding_digest=binding_digest or ("b" * 64),
        dataset_version_ids=[published.version_id],
        threshold_policy_version=THRESHOLD_POLICY_VERSION,
        mode="dataset_scripted",
        isolation_namespace_id=_uuid_mod.uuid4(),
        runtime_contract_version=1,
        required_build_revision=build_rev,
        isolation_digest="c" * 64,
        actor_principal="tester",
        evidence_provenance="real_orchestration",
        provider_fixture_revision="test-provider-v1",
        provider_fixture_digest="d" * 64,
    )
    repo.transition_run(run_id=run.id, expected_revision=0, to_status="running")
    repo.transition_run(
        run_id=run.id,
        expected_revision=1,
        to_status="completed",
        gate_eligible=True,
        aggregate_metrics=_passing_gate_metrics(),
    )
    db.commit()
    package = db.get(AssistantSkillPackage, package_id)
    canonical = package_canonical_name or (
        str(package.canonical_name) if package is not None else "unknown"
    )
    catalog_digest = skill_catalog_pin_digest(
        package_id=package_id,
        canonical_name=canonical,
        published_version_id=version_id,
        content_digest=content_digest,
    )
    pins = current_gate_environment_pins(
        db,
        catalog_digest=catalog_digest,
        dataset_version_ids=(published.version_id,),
        build_revision=build_rev,
    )
    subject = build_publish_gate_subject(
        kind="skill_version",
        aggregate_id=package_id,
        version_id=version_id,
        content_digest=content_digest,
        binding_digest=binding_digest or ("b" * 64),
        profile_digest=pins.profile_digest,
        catalog_digest=pins.catalog_digest,
        dataset_version_ids=pins.dataset_version_ids,
        runtime_contract_version=pins.runtime_contract_version,
        policy_version=pins.policy_version,
        threshold_version=pins.threshold_version,
        build_revision=pins.build_revision,
    )
    svc = PublishGateService(db)
    result = svc.create_gate(
        make_create_gate_request(subject=subject, qualifying_eval_run_ids=(run.id,)),
        actor_principal="op-1",
    )
    db.commit()
    return result.gate


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

        # Publish then enable catalog via admin operator path (requires gate).
        pub = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=self.package.draft_version.id, request_id="pub-req-1", expected_aggregate_revision=0),  # type: ignore[union-attr]
        )
        self.assertEqual(pub.version_source, "publish")
        # refresh package
        detail = self.pkg_svc.get_package(self.package.id)
        from app.assistant.skills.models import AssistantSkillVersion

        version = self.db.get(AssistantSkillVersion, pub.id)
        assert version is not None
        gate = _create_passing_enable_gate(
            self.db,
            package_id=self.package.id,
            version_id=pub.id,
            content_digest=str(version.content_digest),
            binding_digest=str(version.binding_set_digest or ("b" * 64)),
        )
        enabled = self.admin.enable_catalog(
            self.package.id,
            AggregateRevisionCommand(
                request_id="en-1",
                expected_aggregate_revision=detail.aggregate_revision,
                gate_id=gate.id,
            ),
            principal=_operator(),
            expected_published_version_id=pub.id,
            gate_id=gate.id,
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
                PublishSkillVersionCommand(draft_version_id=self.package.draft_version.id, request_id="pub-req-2", expected_aggregate_revision=int(self.pkg_svc.get_package(self.package.id).aggregate_revision)),  # type: ignore[union-attr]
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
            PublishSkillVersionCommand(draft_version_id=self.package.draft_version.id, request_id="pub-req-3", expected_aggregate_revision=int(self.pkg_svc.get_package(self.package.id).aggregate_revision)),  # type: ignore[union-attr]
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

    def test_metadata_vs_archive_stale_revision_conflicts(self) -> None:
        """Sequential dual-mutation CAS: archive after metadata at same expected rev fails.

        True two-session concurrency is covered by the PG-gated tests in
        ``test_agent_skill_admin_postgres_migration.py`` (skipped without URL).
        """
        from app.common.exceptions import ApiException
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            UpdateSkillPackageMetadataCommand,
        )

        detail = self.admin.update_metadata(
            self.package.id,
            UpdateSkillPackageMetadataCommand(
                request_id="meta-race-1",
                expected_aggregate_revision=0,
                display_name="MetaFirst",
            ),
            principal=_operator(),
        )
        self.assertEqual(detail.aggregate_revision, 1)

        with self.assertRaises(ApiException) as ctx:
            self.admin.archive(
                self.package.id,
                AggregateRevisionCommand(
                    request_id="arch-race-1",
                    expected_aggregate_revision=0,  # stale
                ),
                principal=_operator(),
            )
        self.assertEqual(ctx.exception.code, 40994)

        archived = self.admin.archive(
            self.package.id,
            AggregateRevisionCommand(
                request_id="arch-race-2",
                expected_aggregate_revision=1,
            ),
            principal=_operator(),
        )
        self.assertEqual(archived.aggregate_revision, 2)
        self.assertIsNotNone(archived.archived_at)

        # Re-archive under a new requestId intentionally bumps revision (audit).
        re_archived = self.admin.archive(
            self.package.id,
            AggregateRevisionCommand(
                request_id="arch-race-3",
                expected_aggregate_revision=2,
            ),
            principal=_operator(),
        )
        self.assertEqual(re_archived.aggregate_revision, 3)
        self.assertIsNotNone(re_archived.archived_at)


if __name__ == "__main__":
    unittest.main()
