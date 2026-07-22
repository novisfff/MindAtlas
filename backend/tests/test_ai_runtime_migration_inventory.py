"""Plan 10 Task 0 — read-only runtime migration inventory tooling."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ai_runtime_migration"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class InventoryScanFixtureTests(unittest.TestCase):
    def test_scan_classifies_system_custom_disabled_and_aliases(self) -> None:
        from app.assistant.migration.inventory import scan_inventory_from_records

        records = _load_fixture("sanitized_skill_records.json")
        snapshot = scan_inventory_from_records(records)

        kinds = {item.subject_kind for item in snapshot.items}
        self.assertIn("skill", kinds)
        self.assertIn("alias", kinds)
        self.assertIn("package", kinds)
        self.assertIn("l2_memory", kinds)
        self.assertIn("approval", kinds)
        self.assertIn("entrypoint", kinds)

        skill_names = {
            item.source_name_normalized
            for item in snapshot.items
            if item.subject_kind == "skill"
        }
        self.assertIn("general_chat", skill_names)
        self.assertIn("quick_stats", skill_names)
        self.assertIn("smart_capture", skill_names)
        self.assertIn("periodic_review", skill_names)
        self.assertIn("custom_note_taker", skill_names)
        self.assertIn("retired_experiment", skill_names)

        disabled = [
            item
            for item in snapshot.items
            if item.subject_kind == "skill"
            and item.source_name_normalized == "retired_experiment"
        ]
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0].enabled, False)
        self.assertIn(disabled[0].state, {"discovered", "blocked", "archived"})

        self.assertGreaterEqual(snapshot.counts["skill"], 5)
        self.assertGreaterEqual(snapshot.counts["alias"], 1)
        self.assertEqual(snapshot.environment, records["environment"])
        self.assertTrue(snapshot.snapshot_digest)
        self.assertEqual(len(snapshot.snapshot_digest), 64)

    def test_unknown_skill_becomes_blocker(self) -> None:
        from app.assistant.migration.inventory import scan_inventory_from_records

        records = _load_fixture("sanitized_skill_records.json")
        snapshot = scan_inventory_from_records(records)
        blockers = [item for item in snapshot.items if item.state == "blocked"]
        blocker_names = {item.source_name_normalized for item in blockers}
        self.assertIn("brand_new_unknown_skill", blocker_names)
        reasons = {
            item.reason_code
            for item in blockers
            if item.source_name_normalized == "brand_new_unknown_skill"
        }
        self.assertIn("unknown_skill_source", reasons)
        self.assertGreaterEqual(snapshot.blocker_count, 1)

        # Ordinary custom skills (no unknown flag) remain discoverable.
        custom = [
            item
            for item in snapshot.items
            if item.subject_kind == "skill"
            and item.source_name_normalized == "custom_note_taker"
        ]
        self.assertEqual(len(custom), 1)
        self.assertEqual(custom[0].state, "discovered")
        self.assertNotIn("custom_note_taker", blocker_names)

    def test_generic_unknown_flag_blocks_without_name_hardcode(self) -> None:
        from app.assistant.migration.inventory import scan_inventory_from_records

        records = _load_fixture("sanitized_skill_records.json")
        records = json.loads(json.dumps(records))
        records["skills"].append(
            {
                "id": "88888888-8888-4888-8888-888888888888",
                "name": "some_other_custom_unknown",
                "enabled": True,
                "is_system": False,
                "unknown": True,
                "workflow_id": str(uuid4()),
                "agent_profile_id": None,
                "system_prompt": "should not leak",
                "description": "generic unknown custom skill",
            }
        )
        snapshot = scan_inventory_from_records(records)
        blocked = [
            item
            for item in snapshot.items
            if item.subject_kind == "skill"
            and item.source_name_normalized == "some_other_custom_unknown"
        ]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].state, "blocked")
        self.assertEqual(blocked[0].reason_code, "unknown_skill_source")

        # Same name without unknown remains discoverable.
        records_ok = json.loads(json.dumps(records))
        for skill in records_ok["skills"]:
            if skill.get("name") == "some_other_custom_unknown":
                skill.pop("unknown", None)
        ok_snapshot = scan_inventory_from_records(records_ok)
        discovered = [
            item
            for item in ok_snapshot.items
            if item.subject_kind == "skill"
            and item.source_name_normalized == "some_other_custom_unknown"
        ]
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].state, "discovered")

    def test_null_vs_default_namespace_classification(self) -> None:
        from app.assistant.migration.inventory import scan_inventory_from_records

        records = _load_fixture("sanitized_skill_records.json")
        snapshot = scan_inventory_from_records(records)
        l2_items = [item for item in snapshot.items if item.subject_kind == "l2_memory"]
        classifications = {item.namespace_class for item in l2_items}
        self.assertIn("legacy_null_package", classifications)
        self.assertIn("native_default_namespace", classifications)
        self.assertIn("native_custom_namespace", classifications)
        # NULL package rows must not be silently treated as default namespace.
        null_rows = [i for i in l2_items if i.namespace_class == "legacy_null_package"]
        self.assertTrue(null_rows)
        for row in null_rows:
            self.assertIsNone(row.memory_namespace)
            self.assertIsNone(row.skill_package_id)


class SafeReportTests(unittest.TestCase):
    def test_report_contains_only_digests_ids_counts(self) -> None:
        from app.assistant.migration.inventory import (
            build_safe_inventory_report,
            scan_inventory_from_records,
        )

        records = _load_fixture("sanitized_skill_records.json")
        snapshot = scan_inventory_from_records(records)
        report = build_safe_inventory_report(snapshot)
        payload = report.model_dump(mode="json", by_alias=True)

        serialized = json.dumps(payload, sort_keys=True)
        # Must never leak raw prompts / facts / approval payloads from fixtures.
        self.assertNotIn("SYSTEM PROMPT SECRET", serialized)
        self.assertNotIn("user remembered their SSN", serialized)
        self.assertNotIn("approve_payload_secret", serialized)
        self.assertNotIn("raw_prompt", serialized)
        self.assertNotIn("123-45-6789", serialized)

        # Allowed surfaces: digests, IDs, counts, reason codes, states.
        self.assertIn("snapshotDigest", payload)
        self.assertIn("counts", payload)
        self.assertIn("items", payload)
        for item in payload["items"]:
            self.assertIn("sourceId", item)
            self.assertIn("sourceDigest", item)
            self.assertIn("state", item)
            self.assertNotIn("systemPrompt", item)
            self.assertNotIn("requestPayload", item)
            self.assertNotIn("facts", item)
            self.assertNotIn("system_prompt", item)
            self.assertNotIn("request_payload", item)


class VerificationDigestTests(unittest.TestCase):
    def test_snapshot_digest_stable_and_compare_detects_drift(self) -> None:
        from app.assistant.migration.inventory import scan_inventory_from_records
        from app.assistant.migration.verification import (
            compare_inventory_snapshots,
            digest_inventory_snapshot,
        )

        records = _load_fixture("sanitized_skill_records.json")
        a = scan_inventory_from_records(records)
        b = scan_inventory_from_records(records)
        self.assertEqual(digest_inventory_snapshot(a), digest_inventory_snapshot(b))
        self.assertEqual(a.snapshot_digest, digest_inventory_snapshot(a))

        # Mutate one item state via rebuild with extra unknown skill.
        mutated_records = json.loads(json.dumps(records))
        mutated_records["skills"].append(
            {
                "id": str(uuid4()),
                "name": "another_drift_skill",
                "enabled": True,
                "is_system": False,
                "workflow_id": str(uuid4()),
                "agent_profile_id": None,
                "system_prompt": "x",
                "description": "drift",
            }
        )
        c = scan_inventory_from_records(mutated_records)
        comparison = compare_inventory_snapshots(a, c)
        self.assertFalse(comparison.equal)
        self.assertGreater(comparison.added_count, 0)


class OwnershipAuditTests(unittest.TestCase):
    def test_ownership_audit_classifies_known_legacy_modules(self) -> None:
        from app.assistant.migration.ownership import (
            classify_module_ownership,
            audit_module_paths,
        )

        legacy = classify_module_ownership(
            "backend/app/assistant/orchestration/intent_router.py"
        )
        self.assertEqual(legacy.owner_class, "legacy")
        self.assertEqual(legacy.subject_area, "routing")

        supervisor = classify_module_ownership(
            "backend/app/assistant/orchestration/supervisor_graph.py"
        )
        self.assertEqual(supervisor.owner_class, "legacy")

        catalog = classify_module_ownership(
            "backend/app/assistant/skill_catalog/definitions.py"
        )
        self.assertEqual(catalog.owner_class, "legacy")

        hitl = classify_module_ownership(
            "backend/app/assistant/workflow/human_approval_runtime.py"
        )
        self.assertEqual(hitl.owner_class, "legacy")
        self.assertEqual(hitl.subject_area, "approval")

        native = classify_module_ownership(
            "backend/app/assistant/main_agent/catalog.py"
        )
        self.assertEqual(native.owner_class, "native_runtime")

        migration = classify_module_ownership(
            "backend/app/assistant/migration/inventory.py"
        )
        self.assertEqual(migration.owner_class, "migration_tooling")

        # Dynamic import markers from fixture must be classified.
        dynamic_fixture = _load_fixture("dynamic_import_markers.json")
        audit = audit_module_paths(dynamic_fixture["module_paths"])
        classes = {row.owner_class for row in audit}
        self.assertIn("legacy", classes)
        self.assertIn("native_runtime", classes)
        self.assertIn("dynamic_composition", classes)


class MetricDictionaryTests(unittest.TestCase):
    def test_metric_dictionary_completeness(self) -> None:
        from app.assistant.migration.metrics import METRIC_DICTIONARY, required_metric_ids

        ids = {m.metric_id for m in METRIC_DICTIONARY}
        required = required_metric_ids()
        missing = required - ids
        self.assertEqual(missing, set(), f"missing metrics: {sorted(missing)}")
        for metric in METRIC_DICTIONARY:
            self.assertTrue(metric.numerator)
            self.assertTrue(metric.denominator)
            self.assertTrue(metric.eligibility)
            self.assertTrue(metric.window)
            self.assertIn(metric.confidence_model, {"wilson", "bootstrap", "exact_zero", "none"})


class GateMatrixTests(unittest.TestCase):
    def test_inherited_gate_matrix_records_plan09_auth_blocker(self) -> None:
        from app.assistant.migration.gates import GATE_MATRIX, production_cutover_blocked

        self.assertTrue(GATE_MATRIX)
        plan09 = [g for g in GATE_MATRIX if g.plan == "09"]
        self.assertTrue(plan09)
        auth_gates = [g for g in plan09 if "auth" in g.gate_id or "principal" in g.gate_id]
        self.assertTrue(auth_gates)
        for g in auth_gates:
            self.assertFalse(g.satisfied)
            self.assertIn("shadow", g.blocks_stages)
            self.assertIn("write", g.blocks_stages)
            self.assertIn("cleanup", g.blocks_stages)

        status = production_cutover_blocked()
        self.assertTrue(status.blocked)
        self.assertTrue(status.local_tooling_allowed)
        self.assertTrue(
            any("plan09_operator_principal" in code for code in status.reason_codes),
            status.reason_codes,
        )


class CliInventoryScanTests(unittest.TestCase):
    def test_cli_inventory_scan_dry_run_exit_0(self) -> None:
        from app.assistant.migration import cli as migration_cli

        records_path = FIXTURE_DIR / "sanitized_skill_records.json"
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            code = migration_cli.main(
                [
                    "inventory",
                    "scan",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fixture-db-fp",
                    "--source-snapshot-digest",
                    "0" * 64,
                    "--expected-schema-head",
                    "027869a00a47",
                    "--expected-build-revision",
                    "development",
                    "--request-id",
                    str(uuid4()),
                    "--batch-size",
                    "100",
                    "--dry-run",
                    "--report-json",
                    str(report_path),
                    "--fixture-json",
                    str(records_path),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report.get("ok"))
            self.assertIn("snapshotDigest", report)
            self.assertNotIn("SYSTEM PROMPT SECRET", json.dumps(report))

    def test_cli_mutation_stub_exits_precondition_failed(self) -> None:
        from app.assistant.migration import cli as migration_cli

        # packages migrate/verify are implemented in Task 2; remaining groups stay stubs.
        code = migration_cli.main(
            [
                "l2",
                "backfill",
                "--environment",
                "test",
                "--database-fingerprint",
                "fp",
                "--source-snapshot-digest",
                "0" * 64,
                "--expected-schema-head",
                "027869a00a47",
                "--expected-build-revision",
                "development",
                "--request-id",
                str(UUID(int=1)),
                "--batch-size",
                "10",
                "--dry-run",
                "--report-json",
                "/tmp/unused.json",
            ]
        )
        self.assertEqual(code, 3)


class SourceSnapshotDigestTests(unittest.TestCase):
    def test_task0_source_snapshot_digest_is_deterministic(self) -> None:
        from app.assistant.migration.verification import compute_task0_source_snapshot_digest

        d1 = compute_task0_source_snapshot_digest()
        d2 = compute_task0_source_snapshot_digest()
        self.assertEqual(d1, d2)
        self.assertEqual(len(d1), 64)


class BackupExportDigestTests(unittest.TestCase):
    def test_export_digest_helper_on_sanitized_fixture(self) -> None:
        from app.assistant.migration.verification import digest_backup_export_manifest

        manifest = _load_fixture("backup_export_manifest.json")
        digest = digest_backup_export_manifest(manifest)
        self.assertEqual(len(digest), 64)
        # Recompute stability.
        self.assertEqual(digest, digest_backup_export_manifest(manifest))


if __name__ == "__main__":
    unittest.main()
