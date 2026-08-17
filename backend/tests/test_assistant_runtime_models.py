"""ORM/contract tests for Plan 2 immutable Main-Agent rollout state (Task 2)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_0 = "0" * 64
DIGEST_1 = "1" * 64
DIGEST_2 = "2" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64
DIGEST_5 = "5" * 64
DIGEST_6 = "6" * 64
DIGEST_7 = "7" * 64
DIGEST_8 = "8" * 64
DIGEST_9 = "9" * 64


@pytest.fixture
def db():
    reset_caches()
    session = make_session()
    try:
        yield session
    finally:
        session.close()


def _seed_profile_version(db, *, content_digest: str = DIGEST_A):
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantMainAgentProfileVersion,
    )

    profile = AssistantMainAgentProfile(
        profile_key="default",
        display_name="Main Agent",
        is_default=True,
        migration_state="native",
        runtime_enabled=False,
    )
    db.add(profile)
    db.flush()
    version = AssistantMainAgentProfileVersion(
        profile_id=profile.id,
        sequence_no=1,
        version_name="v1",
        version_source="save",
        origin="api",
        snapshot={"schemaVersion": 2, "content": "test"},
        content_digest=content_digest,
    )
    db.add(version)
    db.flush()
    return profile, version


def _seed_model(db):
    _cred, model, _binding = create_default_model_binding(db)
    return model


def prepared_revision_fixture(db, **overrides):
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
    )
    from app.assistant.runtime.contracts import (
        AssistantRuntimeSubject,
        PreparedRolloutRevision,
    )

    _, profile_version = _seed_profile_version(db)
    model = _seed_model(db)
    subject = AssistantRuntimeSubject(
        profile_version_id=overrides.get("profile_version_id", profile_version.id),
        profile_content_digest=overrides.get(
            "profile_content_digest", profile_version.content_digest
        ),
        model_id=overrides.get("model_id", model.id),
        model_identity_digest=overrides.get("model_identity_digest", DIGEST_B),
        package_closure=overrides.get(
            "package_closure", ({"packageId": str(uuid.uuid4()), "digest": DIGEST_C},)
        ),
        package_closure_digest=overrides.get("package_closure_digest", DIGEST_C),
        capability_closure_digest=overrides.get("capability_closure_digest", DIGEST_D),
        seed_manifest_digest=overrides.get("seed_manifest_digest", DIGEST_E),
        build_revision=overrides.get("build_revision", "build-test-1"),
        runtime_contract_version=overrides.get("runtime_contract_version", 1),
        checkpoint_codec_version=overrides.get("checkpoint_codec_version", 3),
        capability_feature_digest=overrides.get("capability_feature_digest", DIGEST_F),
        create_entry_contract_digest=overrides.get(
            "create_entry_contract_digest", CREATE_ENTRY_CONTRACT_DIGEST
        ),
        write_policy_digest=overrides.get("write_policy_digest", WRITE_POLICY_DIGEST),
        write_cohort_digest=overrides.get("write_cohort_digest", WRITE_COHORT_DIGEST),
        reconciliation_contract_version=overrides.get(
            "reconciliation_contract_version", RECONCILIATION_CONTRACT_VERSION
        ),
    )
    revision_id = overrides.get("revision_id", uuid.uuid4())
    return PreparedRolloutRevision.from_subject(
        subject=subject,
        revision_id=revision_id,
        prepared_by_operator_id=overrides.get("prepared_by_operator_id"),
        prepared_reason=overrides.get("prepared_reason", "unit-test-prepare"),
    )


def test_control_is_singleton_and_revision_is_immutable(db):
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(prepared_revision_fixture(db))
    control = repo.get_or_create_control_for_update()
    assert control.control_key == "main_agent"
    assert control.active_rollout_revision_id is None
    assert control.state_revision == 0
    assert control.new_runs_enabled is True

    prepared.revision_digest = DIGEST_0
    with pytest.raises(IntegrityError):
        db.commit()


def test_require_sha256_rejects_non_lowercase_hex():
    from app.assistant.runtime.contracts import require_sha256

    assert require_sha256(DIGEST_A, field_name="x") == DIGEST_A
    with pytest.raises(ValueError, match="64 lowercase hex"):
        require_sha256("A" * 64, field_name="x")
    with pytest.raises(ValueError, match="64 lowercase hex"):
        require_sha256("not-a-digest", field_name="x")


def test_runtime_subject_and_closure_validate_digests():
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
    )
    from app.assistant.runtime.contracts import (
        AssistantReadinessSnapshot,
        AssistantRuntimeClosure,
        AssistantRuntimeSubject,
        RUNTIME_READINESS_REASON_CODES,
    )

    subject = AssistantRuntimeSubject(
        profile_version_id=uuid.uuid4(),
        profile_content_digest=DIGEST_1,
        model_id=uuid.uuid4(),
        model_identity_digest=DIGEST_2,
        package_closure=({"k": "v"},),
        package_closure_digest=DIGEST_3,
        capability_closure_digest=DIGEST_4,
        seed_manifest_digest=DIGEST_5,
        build_revision="build-1",
        runtime_contract_version=1,
        checkpoint_codec_version=3,
        capability_feature_digest=DIGEST_6,
        create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
        write_policy_digest=WRITE_POLICY_DIGEST,
        write_cohort_digest=WRITE_COHORT_DIGEST,
        reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
    )
    assert subject.runtime_contract_version == 1

    with pytest.raises(ValidationError):
        AssistantRuntimeSubject(
            profile_version_id=uuid.uuid4(),
            profile_content_digest="bad",
            model_id=uuid.uuid4(),
            model_identity_digest=DIGEST_2,
            package_closure=(),
            package_closure_digest=DIGEST_3,
            capability_closure_digest=DIGEST_4,
            seed_manifest_digest=DIGEST_5,
            build_revision="build-1",
            runtime_contract_version=1,
            checkpoint_codec_version=3,
            capability_feature_digest=DIGEST_6,
            create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
            write_policy_digest=WRITE_POLICY_DIGEST,
            write_cohort_digest=WRITE_COHORT_DIGEST,
            reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
        )

    closure = AssistantRuntimeClosure(
        rollout_revision_id=uuid.uuid4(),
        rollout_revision_digest=DIGEST_7,
        profile_version_id=subject.profile_version_id,
        profile_content_digest=subject.profile_content_digest,
        model_id=subject.model_id,
        model_identity_digest=subject.model_identity_digest,
        package_closure_digest=subject.package_closure_digest,
        capability_closure_digest=subject.capability_closure_digest,
        seed_manifest_digest=subject.seed_manifest_digest,
        build_revision=subject.build_revision,
        runtime_contract_version=subject.runtime_contract_version,
        checkpoint_codec_version=subject.checkpoint_codec_version,
        capability_feature_digest=subject.capability_feature_digest,
        create_entry_contract_digest=subject.create_entry_contract_digest,
        write_policy_digest=subject.write_policy_digest,
        write_cohort_digest=subject.write_cohort_digest,
        reconciliation_contract_version=subject.reconciliation_contract_version,
        closure_digest=DIGEST_8,
    )
    assert closure.schema_version == 1

    snapshot = AssistantReadinessSnapshot(
        ready=False,
        reason_codes=("system_not_initialized",),
        active_rollout_revision_id=None,
        profile_version_id=None,
        model_id=None,
        compatible_worker_ids=(),
        build_revision="build-1",
    )
    assert snapshot.ready is False
    assert RUNTIME_READINESS_REASON_CODES[0] == "system_not_initialized"
    assert "new_runs_disabled" in RUNTIME_READINESS_REASON_CODES


def test_rollout_tables_registered():
    from app.database import Base
    import app.assistant.runtime.models  # noqa: F401

    for name in (
        "assistant_main_agent_rollout_revision",
        "assistant_main_agent_rollout_control",
        "assistant_main_agent_rollout_event",
    ):
        assert name in Base.metadata.tables


def test_chat_run_requires_main_agent_closure_fields(db):
    from app.assistant.models import AssistantChatRun, Conversation
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(prepared_revision_fixture(db))
    conv = Conversation(title="t")
    db.add(conv)
    db.flush()

    run = AssistantChatRun(
        conversation_id=conv.id,
        status="queued",
        runtime_kind="main_agent",
        main_agent_rollout_revision_id=prepared.id,
        main_agent_profile_version_id=prepared.profile_version_id,
        resolved_model_id=prepared.model_id,
        runtime_closure_digest=DIGEST_9,
        runtime_contract_version=1,
        required_checkpoint_codec_version=3,
        required_capability_feature_digest=DIGEST_F,
        required_create_entry_contract_digest=(
            prepared.required_create_entry_contract_digest
        ),
        required_write_policy_digest=prepared.required_write_policy_digest,
        required_write_cohort_digest=prepared.required_write_cohort_digest,
        required_reconciliation_contract_version=(
            prepared.required_reconciliation_contract_version
        ),
        required_app_build_revision="build-test-1",
        capability_ledger_mode="enforced",
        memory_commit_status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    assert run.runtime_kind == "main_agent"
    assert run.main_agent_rollout_revision_id == prepared.id
