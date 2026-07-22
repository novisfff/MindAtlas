"""Plan 09 Task 4 — snapshot projection policy and secret canaries.

read_snapshot is default-disabled, allowlist-only, immutable, bounded, and
hard-denies credentials/encrypted/auth/cookies/signed URLs/private fields.
Output canaries prove secrets never enter event/Artifact/gate evidence.
"""

from __future__ import annotations

import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class EvalProviderFixtureContractTests(unittest.TestCase):
    """Task 5: provider fixture refs are separate from assertion fields."""

    def test_provider_fixture_ref_rejects_assertion_fields(self) -> None:
        from app.assistant.evaluation.contracts import (
            ProviderFixtureRef,
            normalize_provider_fixture_refs,
        )
        from pydantic import ValidationError

        ref = ProviderFixtureRef(script_key="provider-selects-skill-b")
        self.assertEqual(ref.kind, "provider_script")
        self.assertEqual(ref.script_key, "provider-selects-skill-b")
        payload = ref.model_dump(mode="json")
        self.assertNotIn("acceptable_skill_keys", payload)
        self.assertNotIn("expected_mode", payload)

        with self.assertRaises((ValidationError, ValueError, TypeError)):
            ProviderFixtureRef(
                script_key="x",
                acceptable_skill_keys=["skill-a"],  # type: ignore[call-arg]
            )

        normalized = normalize_provider_fixture_refs(
            [
                {"kind": "provider_script", "script_key": "provider-selects-skill-a"},
                "provider_script:legacy-key",
            ]
        )
        self.assertEqual(
            [item.script_key for item in normalized],
            ["provider-selects-skill-a", "legacy-key"],
        )

    def test_create_run_pins_provider_fixture_in_closure(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository

        db = make_session()
        try:
            repo = EvaluationRepository(db)
            dataset = repo.create_dataset(
                stable_key=f"fix-{uuid.uuid4().hex[:8]}",
                display_name="fixture-pin",
                ownership="custom",
            )
            snapshot = [
                {
                    "case_key": "c1",
                    "ordinal": 0,
                    "locale": "en",
                    "input_messages": [{"role": "user", "content": "hi"}],
                    "fixture_refs": [
                        {
                            "kind": "provider_script",
                            "script_key": "provider-selects-skill-a",
                        }
                    ],
                    "expected_mode": "golden_skill",
                    "case_digest": DIGEST_A,
                }
            ]
            repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
            published = repo.publish_dataset_version(
                dataset_id=dataset.id,
                expected_aggregate_revision=0,
                expected_draft_revision=0,
                version_name="v1",
            )
            run = repo.create_run(
                subject_kind="skill_version",
                subject_aggregate_id=uuid.uuid4(),
                subject_version_id=uuid.uuid4(),
                subject_content_digest=DIGEST_A,
                subject_binding_digest=DIGEST_A,
                dataset_version_ids=[published.version_id],
                threshold_policy_version="t1",
                mode="dataset_scripted",
                isolation_namespace_id=uuid.uuid4(),
                runtime_contract_version=1,
                required_build_revision="development",
                isolation_digest=DIGEST_A,
                evidence_provenance="real_orchestration",
                provider_fixture_revision="plan04-provider-v1",
                provider_fixture_digest=DIGEST_B,
            )
            db.commit()
            self.assertEqual(run.evidence_provenance, "real_orchestration")
            self.assertEqual(run.provider_fixture_revision, "plan04-provider-v1")
            self.assertEqual(run.provider_fixture_digest, DIGEST_B)
            stored = repo.list_cases(published.version_id)[0]
            self.assertEqual(stored.fixture_refs[0]["kind"], "provider_script")
        finally:
            db.close()


class SnapshotPolicyShapeTests(unittest.TestCase):
    def test_read_snapshot_default_disabled(self) -> None:
        from app.assistant.evaluation.snapshots import (
            READ_SNAPSHOT_DEFAULT_ENABLED,
            SnapshotPolicyError,
            build_evaluation_snapshot,
        )

        self.assertFalse(READ_SNAPSHOT_DEFAULT_ENABLED)
        with self.assertRaises(SnapshotPolicyError) as ctx:
            build_evaluation_snapshot(
                source_type="entry_summary",
                policy_version="v1",
                source_rows=[{"id": "1", "title": "t", "type_key": "note", "created_at": "x"}],
                authorized=False,
            )
        self.assertEqual(ctx.exception.code, "read_snapshot_disabled")

    def test_wildcards_forbidden_in_allowlist(self) -> None:
        from app.assistant.evaluation.snapshots import (
            SnapshotPolicyError,
            SnapshotProjectionPolicy,
        )

        with self.assertRaises(SnapshotPolicyError) as ctx:
            SnapshotProjectionPolicy(
                source_type="x",
                policy_version="v1",
                allowed_fields=("*",),
            )
        self.assertEqual(ctx.exception.code, "wildcard_forbidden")

    def test_hard_denied_field_cannot_be_allowlisted(self) -> None:
        from app.assistant.evaluation.snapshots import (
            SnapshotPolicyError,
            SnapshotProjectionPolicy,
        )

        with self.assertRaises(SnapshotPolicyError) as ctx:
            SnapshotProjectionPolicy(
                source_type="x",
                policy_version="v1",
                allowed_fields=("api_key", "title"),
            )
        self.assertEqual(ctx.exception.code, "hard_denied_allowlist")

    def test_project_row_drops_non_allowlisted_and_keeps_safe(self) -> None:
        from app.assistant.evaluation.snapshots import (
            get_snapshot_policy,
            project_row,
        )

        policy = get_snapshot_policy("entry_summary", "v1")
        projected = project_row(
            {
                "id": "1",
                "title": "Hello",
                "type_key": "note",
                "created_at": "2026-01-01",
                "api_key": "CANARY_API_KEY_DO_NOT_LEAK",
                "password": "secret",
                "extra": "drop-me",
            },
            policy=policy,
        )
        self.assertEqual(
            projected,
            {
                "id": "1",
                "title": "Hello",
                "type_key": "note",
                "created_at": "2026-01-01",
            },
        )
        self.assertNotIn("api_key", projected)
        self.assertNotIn("password", projected)
        self.assertNotIn("extra", projected)

    def test_authorized_build_is_immutable_and_bounded(self) -> None:
        from app.assistant.evaluation.snapshots import build_evaluation_snapshot

        snap = build_evaluation_snapshot(
            source_type="entry_summary",
            policy_version="v1",
            source_rows=[
                {
                    "id": "1",
                    "title": "A",
                    "type_key": "note",
                    "created_at": "t0",
                    "authorization": "CANARY_AUTHORIZATION_BEARER_DO_NOT_LEAK",
                }
            ],
            authorized=True,
            snapshot_id=uuid.uuid4(),
        )
        self.assertEqual(len(snap.rows), 1)
        self.assertNotIn("authorization", snap.rows[0])
        self.assertEqual(len(snap.content_digest), 64)
        self.assertGreater(snap.byte_size, 0)
        # Frozen-ish: rows is a tuple of dicts (immutable container).
        self.assertIsInstance(snap.rows, tuple)

    def test_row_ceiling(self) -> None:
        from app.assistant.evaluation.snapshots import (
            SnapshotPolicyError,
            SnapshotProjectionPolicy,
            build_evaluation_snapshot,
            register_snapshot_policy,
        )

        register_snapshot_policy(
            SnapshotProjectionPolicy(
                source_type="tiny",
                policy_version="v1",
                allowed_fields=("id",),
                max_rows=2,
                max_bytes=10_000,
            )
        )
        with self.assertRaises(SnapshotPolicyError) as ctx:
            build_evaluation_snapshot(
                source_type="tiny",
                policy_version="v1",
                source_rows=[{"id": "1"}, {"id": "2"}, {"id": "3"}],
                authorized=True,
            )
        self.assertEqual(ctx.exception.code, "row_ceiling")

    def test_credential_shaped_value_in_allowlisted_field_rejected(self) -> None:
        from app.assistant.evaluation.snapshots import (
            SnapshotPolicyError,
            get_snapshot_policy,
            project_row,
        )

        policy = get_snapshot_policy("entry_summary", "v1")
        with self.assertRaises(SnapshotPolicyError) as ctx:
            project_row(
                {
                    "id": "1",
                    "title": "Bearer CANARY_AUTHORIZATION_BEARER_DO_NOT_LEAK",
                    "type_key": "note",
                    "created_at": "t",
                },
                policy=policy,
            )
        self.assertIn(ctx.exception.code, {"secret_canary", "credential_shaped_value"})


class IsolationSnapshotShapeTests(unittest.TestCase):
    def test_fixture_mode_requires_null_snapshot_fields(self) -> None:
        from app.assistant.evaluation.contracts import RuntimeIsolationContext
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            RuntimeIsolationContext(
                namespace_id=uuid.uuid4(),
                owner_kind="test",
                subject_digest=DIGEST_A,
                dataset_version_ids=(uuid.uuid4(),),
                memory_mode="empty",
                data_mode="fixture",
                data_snapshot_id=uuid.uuid4(),
                snapshot_projection_policy_digest=DIGEST_A,
            )

    def test_read_snapshot_mode_requires_both_fields(self) -> None:
        from app.assistant.evaluation.contracts import RuntimeIsolationContext
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            RuntimeIsolationContext(
                namespace_id=uuid.uuid4(),
                owner_kind="test",
                subject_digest=DIGEST_A,
                dataset_version_ids=(uuid.uuid4(),),
                memory_mode="empty",
                data_mode="read_snapshot",
                data_snapshot_id=None,
                snapshot_projection_policy_digest=None,
            )


class SecretCanaryEvidenceTests(unittest.TestCase):
    def test_assert_payload_safe_rejects_hard_denied_keys(self) -> None:
        from app.assistant.evaluation.snapshots import assert_payload_safe

        with self.assertRaises(ValueError):
            assert_payload_safe({"api_key": "x"}, context="event")

    def test_assert_payload_safe_rejects_canary_values(self) -> None:
        from app.assistant.evaluation.snapshots import (
            SECRET_CANARY_VALUES,
            assert_payload_safe,
        )

        canary = next(iter(SECRET_CANARY_VALUES))
        with self.assertRaises(ValueError):
            assert_payload_safe({"note": canary}, context="artifact")

    def test_runner_events_never_contain_canaries(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )
        from app.assistant.evaluation.snapshots import (
            SECRET_CANARY_VALUES,
            payload_contains_secret_canaries,
        )

        isolation, identity = make_interactive_identity()
        # Inject canary into fixture store; read path must not leak into events.
        canary = next(iter(SECRET_CANARY_VALUES))
        runner = EvaluationRunner(fixture_store={"poison": {"secret": canary}})
        outcome = runner.run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="eval.read",
                        side_effect="read",
                        arguments={"fixture_key": "poison"},
                        logical_call_key="r1",
                    ),
                )
            ),
        )
        for event in outcome.events:
            hits = payload_contains_secret_canaries(event)
            self.assertEqual(hits, [], msg=f"canary leaked into event: {event}")
        for record in outcome.call_records:
            hits = payload_contains_secret_canaries(dict(record.decision))
            self.assertEqual(hits, [], msg=f"canary leaked into call decision: {record}")

    def test_repository_append_event_rejects_hard_denied_payload(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import (
            EvaluationRepository,
            EvaluationRepositoryError,
        )

        db = make_session()
        try:
            repo = EvaluationRepository(db)
            dataset = repo.create_dataset(
                stable_key=f"ds-{uuid.uuid4().hex[:8]}",
                display_name="snap",
                ownership="custom",
            )
            snapshot = [
                {
                    "case_key": "c1",
                    "ordinal": 0,
                    "locale": "en",
                    "input_messages": [{"role": "user", "content": "hi"}],
                    "expected_mode": "golden_skill",
                    "case_digest": DIGEST_A,
                }
            ]
            repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
            published = repo.publish_dataset_version(
                dataset_id=dataset.id,
                expected_aggregate_revision=0,
                expected_draft_revision=0,
                version_name="v1",
            )
            run = repo.create_run(
                subject_kind="skill_version",
                subject_aggregate_id=uuid.uuid4(),
                subject_version_id=uuid.uuid4(),
                subject_content_digest=DIGEST_A,
                subject_binding_digest=DIGEST_A,
                dataset_version_ids=[published.version_id],
                threshold_policy_version="t1",
                mode="interactive_scripted",
                isolation_namespace_id=uuid.uuid4(),
                runtime_contract_version=1,
                required_build_revision="development",
                isolation_digest=DIGEST_A,
            )
            db.commit()
            run = repo.transition_run(
                run_id=run.id, expected_revision=0, to_status="running", lease_owner="w"
            )
            with self.assertRaises((EvaluationRepositoryError, ValueError)):
                repo.append_event(
                    eval_run_id=run.id,
                    expected_run_revision=int(run.state_revision),
                    event_type="eval.bad",
                    payload={"authorization": "Bearer secret"},
                )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
