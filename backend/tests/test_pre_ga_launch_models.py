from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import CheckConstraint


def test_launch_models_have_exact_table_names_and_frozen_identity_columns() -> None:
    from app.pre_ga_launch.models import (
        PreGaLaunchCandidate,
        PreGaLaunchControl,
        PreGaLaunchGateUse,
    )

    assert PreGaLaunchCandidate.__tablename__ == "pre_ga_launch_candidate"
    assert PreGaLaunchGateUse.__tablename__ == "pre_ga_launch_gate_use"
    assert PreGaLaunchControl.__tablename__ == "pre_ga_launch_control"
    candidate_columns = set(PreGaLaunchCandidate.__table__.columns.keys())
    for name in {
        "creation_request_id",
        "creation_request_digest",
        "qualification_target_json",
        "qualification_target_digest",
        "subject_json",
        "subject_digest",
        "automated_evidence_ref_json",
        "rehearsal_evidence_ref_json",
        "operational_snapshot_json",
        "unknown_call_count",
        "needs_reconciliation_count",
        "active_run_count",
        "passed",
        "safe_failure_codes",
        "issued_at",
        "expires_at",
    }:
        assert name in candidate_columns
    assert set(PreGaLaunchControl.__table__.columns.keys()) == {
        "singleton_key",
        "active_subject_digest",
        "active_candidate_id",
        "active_gate_use_id",
        "revision",
        "launched_at",
        "updated_at",
    }
    assert "expires_at" not in set(PreGaLaunchControl.__table__.columns.keys())


def test_launch_model_constraints_are_named_and_closed() -> None:
    from app.pre_ga_launch.models import PreGaLaunchCandidate, PreGaLaunchControl

    names = {
        constraint.name
        for constraint in PreGaLaunchCandidate.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_pre_ga_launch_candidate_kind" in names
    assert "ck_pre_ga_launch_candidate_expiry" in names
    assert "ck_pre_ga_launch_candidate_passed_shape" in names
    control_names = {
        constraint.name
        for constraint in PreGaLaunchControl.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_pre_ga_launch_control_singleton" in control_names
    assert "ck_pre_ga_launch_control_revision_shape" in control_names


def test_launch_subject_excludes_volatile_state_and_self_digest() -> None:
    from app.pre_ga_launch.contracts import LaunchOperationalSnapshotV1, PreGaLaunchSubjectV1

    values = {
        "qualification_target_digest": "1" * 64,
        "build_revision": "release-1",
        "image_set_digest": "2" * 64,
        "deployed_artifact_set_digest": "3" * 64,
        "schema_family": "pre_ga_v1",
        "schema_revision": "pre_ga_v1_0002",
        "schema_runtime_identity_digest": "4" * 64,
        "deployment_class": "production",
        "operator_auth_contract_version": "operator-auth-v1",
        "rollout_revision_id": UUID("00000000-0000-0000-0000-000000000001"),
        "rollout_revision_digest": "5" * 64,
        "runtime_closure_digest": "6" * 64,
        "profile_version_id": UUID("00000000-0000-0000-0000-000000000002"),
        "profile_content_digest": "7" * 64,
        "model_id": UUID("00000000-0000-0000-0000-000000000003"),
        "model_identity_digest": "8" * 64,
        "package_closure_digest": "9" * 64,
        "capability_closure_digest": "a" * 64,
        "seed_manifest_digest": "b" * 64,
        "worker_runtime_contract_version": 1,
        "worker_checkpoint_codec_version": 3,
        "worker_capability_feature_digest": "c" * 64,
        "create_entry_contract_digest": "d" * 64,
        "write_policy_digest": "e" * 64,
        "write_cohort_digest": "f" * 64,
        "reconciliation_contract_version": 1,
        "dependency_lock_set_digest": "0" * 64,
        "automated_evidence_manifest_digest": "1" * 64,
        "rehearsal_evidence_manifest_digest": "2" * 64,
        "scenario_set_digest": "3" * 64,
        "required_assertion_set_digest": "4" * 64,
        "runner_contract_version": 1,
        "runner_identity_digest": "5" * 64,
        "evidence_trust_set_digest": "6" * 64,
    }
    subject = PreGaLaunchSubjectV1.build(**values)
    payload = subject.model_dump(mode="json", by_alias=True)
    assert "subjectDigest" in payload
    assert subject.subject_digest == PreGaLaunchSubjectV1.build(**values).subject_digest
    assert "activeRunCount" not in payload
    assert "unknownCapabilityCallCount" not in payload
    assert "needsReconciliationCount" not in payload
    snapshot = LaunchOperationalSnapshotV1.build(
        unknown_capability_call_count=0,
        needs_reconciliation_count=0,
        active_run_count=0,
        observed_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )
    assert len(snapshot.snapshot_digest) == 64
