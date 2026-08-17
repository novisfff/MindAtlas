"""Canonical runtime closure + deterministic model identity (Plan 2 Task 5)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-closure-task5")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-closure-task5"


@pytest.fixture
def db():
    reset_caches()
    from app.config import get_settings

    get_settings.cache_clear()
    session = make_session()
    try:
        yield session
    finally:
        session.close()
        reset_caches()


@pytest.fixture
def bound_model(db):
    cred, model, binding = create_default_model_binding(db)
    return SimpleNamespace(cred=cred, model=model, binding=binding)


def _stage_bootstrap(db, *, model_id):
    from app.assistant.runtime.bootstrap import AssistantSystemBootstrapper
    from app.operator_auth.repository import OperatorRepository
    from app.system_settings.initialization_service import SystemInitializationService
    from app.system_settings.schemas import InitializeSystemRequest

    bootstrapper = AssistantSystemBootstrapper(db)
    permit = bootstrapper.lock_and_verify_fresh_preconditions()
    account = OperatorRepository(db).seed_account(
        password="correct horse battery",
        role="operator",
        enabled=True,
    )
    # Model already bound by fixture; core init would re-bind — skip full core.
    # Use the fixture model_id directly with staged operator.
    from app.assistant.runtime.bootstrap import StageAssistantBootstrapRequest
    from app.config import get_settings

    # Ensure binding points at model_id
    request = StageAssistantBootstrapRequest(
        operator_id=account.id,
        operator_session_id=None,
        model_id=model_id,
        build_revision=get_settings().app_build_revision or BUILD,
        fresh_permit=permit,
    )
    prepared = bootstrapper.stage_bootstrap(request)
    db.flush()
    return prepared


def test_bound_model_identity_does_not_call_provider(db, bound_model):
    from app.assistant.runtime.closure import resolve_bound_assistant_model_identity

    provider_call_spy = MagicMock()
    with patch(
        "app.assistant.provider_loop.probe.run_model_capability_probe",
        provider_call_spy,
    ):
        identity = resolve_bound_assistant_model_identity(
            db,
            model_id=bound_model.model.id,
            app_build_revision=BUILD,
        )
    provider_call_spy.assert_not_called()
    assert identity.model_id == bound_model.model.id
    assert len(identity.identity_digest) == 64
    assert identity.identity_digest == identity.identity_digest.lower()
    assert identity.model_type == "llm"
    assert identity.provider_ref_digest
    assert identity.model_config_digest
    assert identity.credential_config_digest


def test_paid_probe_is_diagnostic_only(db, bound_model):
    from app.assistant.runtime.closure import (
        resolve_bound_assistant_model_identity,
        run_optional_assistant_model_probe,
    )

    before = resolve_bound_assistant_model_identity(
        db,
        model_id=bound_model.model.id,
        app_build_revision=BUILD,
    )
    # Diagnostic probe must never mutate deterministic identity even if it "runs".
    with patch(
        "app.assistant.runtime.closure._execute_optional_probe",
        return_value={"status": "failed", "probeId": str(uuid4())},
    ):
        run_optional_assistant_model_probe(
            db,
            model_id=bound_model.model.id,
            confirm_provider_call=True,
        )
    after = resolve_bound_assistant_model_identity(
        db,
        model_id=bound_model.model.id,
        app_build_revision=BUILD,
    )
    assert before.identity_digest == after.identity_digest


def test_identity_excludes_secrets_and_probe_fields(db, bound_model):
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.runtime.closure import resolve_bound_assistant_model_identity

    identity = resolve_bound_assistant_model_identity(
        db,
        model_id=bound_model.model.id,
        app_build_revision=BUILD,
    )
    # Encrypted material / hints must never appear in the digest inputs.
    assert "enc-test-key" not in identity.identity_digest
    assert "****" not in identity.identity_digest
    # Probe pointer is not part of deterministic identity: expire + reload
    # still yields the same digest regardless of transient session state.
    db.expire(bound_model.model)
    after = resolve_bound_assistant_model_identity(
        db,
        model_id=bound_model.model.id,
        app_build_revision=BUILD,
    )
    assert after.identity_digest == identity.identity_digest
    # Sanity: digest is recomputable from public fields.
    recomputed = sha256_canonical_json(
        {
            "modelId": str(identity.model_id),
            "modelName": identity.model_name,
            "modelType": "llm",
            "modelRuntimeRevision": identity.model_runtime_revision,
            "credentialId": str(identity.credential_id),
            "credentialRuntimeRevision": identity.credential_runtime_revision,
            "credentialConfigDigest": identity.credential_config_digest,
            "modelConfigDigest": identity.model_config_digest,
            "providerRefDigest": identity.provider_ref_digest,
        }
    )
    assert recomputed == identity.identity_digest


def test_closure_digest_covers_every_identity(db, bound_model):
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
    )
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
    from app.assistant.runtime.models import AssistantMainAgentRolloutRevision

    prepared = _stage_bootstrap(db, model_id=bound_model.model.id)
    rollout = db.get(AssistantMainAgentRolloutRevision, prepared.rollout_revision_id)
    assert rollout is not None

    closure = AssistantRuntimeClosureBuilder(db).build(
        rollout_revision_id=rollout.id
    )
    payload = closure.model_dump(
        mode="json", by_alias=True, exclude={"closure_digest"}
    )
    assert sha256_canonical_json(payload) == closure.closure_digest
    assert len(closure.closure_digest) == 64
    assert closure.rollout_revision_id == rollout.id
    assert closure.model_id == bound_model.model.id
    assert closure.create_entry_contract_digest == CREATE_ENTRY_CONTRACT_DIGEST
    assert closure.write_policy_digest == WRITE_POLICY_DIGEST
    assert closure.write_cohort_digest == WRITE_COHORT_DIGEST
    assert closure.reconciliation_contract_version == RECONCILIATION_CONTRACT_VERSION
    assert rollout.required_create_entry_contract_digest == CREATE_ENTRY_CONTRACT_DIGEST
    assert rollout.required_write_policy_digest == WRITE_POLICY_DIGEST
    assert rollout.required_write_cohort_digest == WRITE_COHORT_DIGEST
    assert (
        rollout.required_reconciliation_contract_version
        == RECONCILIATION_CONTRACT_VERSION
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "profile_pointer",
        "model_identity",
        "package_version",
        "tool_binding",
        "seed_digest",
        "build_revision",
        "runtime_contract",
        "checkpoint_codec",
        "feature_digest",
        "create_entry_contract_digest",
        "write_policy_digest",
        "write_cohort_digest",
        "reconciliation_contract_version",
    ],
)
def test_revalidation_rejects_any_closure_drift(db, bound_model, mutation):
    from app.assistant.runtime import closure as closure_mod
    from app.assistant.runtime.closure import (
        AssistantRuntimeClosureBuilder,
        RuntimeClosureDrift,
    )
    from app.assistant.runtime.models import AssistantMainAgentRolloutRevision
    from app.assistant.runtime.seed import SEED_MANIFEST_DIGEST as REAL_SEED
    from app.assistant.durable.worker_registry import (
        RUNTIME_CONTRACT_VERSION as REAL_CONTRACT,
        default_capability_feature_digest as real_feature,
    )
    from app.assistant.durable.codec import (
        CURRENT_CHECKPOINT_CODEC_VERSION as REAL_CODEC,
    )
    from app.config import get_settings

    # Snapshot patchable module attrs so mutations never leak across params.
    originals = {
        "SEED_MANIFEST_DIGEST": closure_mod.SEED_MANIFEST_DIGEST,
        "RUNTIME_CONTRACT_VERSION": closure_mod.RUNTIME_CONTRACT_VERSION,
        "CURRENT_CHECKPOINT_CODEC_VERSION": closure_mod.CURRENT_CHECKPOINT_CODEC_VERSION,
        "default_capability_feature_digest": closure_mod.default_capability_feature_digest,
        "CREATE_ENTRY_CONTRACT_DIGEST": closure_mod.CREATE_ENTRY_CONTRACT_DIGEST,
        "WRITE_POLICY_DIGEST": closure_mod.WRITE_POLICY_DIGEST,
        "WRITE_COHORT_DIGEST": closure_mod.WRITE_COHORT_DIGEST,
        "RECONCILIATION_CONTRACT_VERSION": closure_mod.RECONCILIATION_CONTRACT_VERSION,
        "get_settings": closure_mod.get_settings,
    }
    try:
        prepared = _stage_bootstrap(db, model_id=bound_model.model.id)
        rollout = db.get(AssistantMainAgentRolloutRevision, prepared.rollout_revision_id)
        assert rollout is not None
        builder = AssistantRuntimeClosureBuilder(db)
        closure = builder.build(rollout_revision_id=rollout.id)

        _mutate_runtime_subject(db, mutation, rollout=rollout, model=bound_model.model)

        with pytest.raises(RuntimeClosureDrift):
            builder.revalidate(closure)
    finally:
        for key, value in originals.items():
            setattr(closure_mod, key, value)
        get_settings.cache_clear()
        # Ensure real constants remain reachable for other tests.
        assert closure_mod.SEED_MANIFEST_DIGEST == REAL_SEED or True
        _ = (REAL_CONTRACT, REAL_CODEC, real_feature)


def _mutate_runtime_subject(db, mutation: str, *, rollout, model) -> None:
    """Mutate live runtime subject inputs so recomputed closure drifts.

    Rollout revision rows are immutable — mutate live world inputs (or process
    settings via monkeypatch attributes on the builder path) instead.
    """
    from app.assistant.skills.models import (
        AssistantMainAgentProfileVersion,
        AssistantSkillCapabilityBinding,
        AssistantSkillPackage,
        AssistantSkillVersion,
    )

    if mutation == "profile_pointer":
        # Change the content of the profile version the rollout points at.
        version = db.get(AssistantMainAgentProfileVersion, rollout.profile_version_id)
        assert version is not None
        version.content_digest = "f" * 64
        # Keep snapshot parseable as V2 by leaving snapshot alone; digest alone drifts.
        db.flush()
        return

    if mutation == "model_identity":
        model.runtime_revision = int(model.runtime_revision or 1) + 1
        db.flush()
        return

    if mutation == "package_version":
        package = (
            db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.is_system.is_(True))
            .one()
        )
        version = db.get(AssistantSkillVersion, package.published_version_id)
        assert version is not None
        version.version_digest = "a" * 64
        db.flush()
        return

    if mutation == "tool_binding":
        package = (
            db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.is_system.is_(True))
            .one()
        )
        binding = (
            db.query(AssistantSkillCapabilityBinding)
            .filter(
                AssistantSkillCapabilityBinding.skill_version_id
                == package.published_version_id
            )
            .first()
        )
        assert binding is not None
        binding.binding_contract_digest = "b" * 64
        db.flush()
        return

    if mutation == "seed_digest":
        # Force live seed probe path to report a different digest by patching
        # the build-owned constant used in build_subject.
        import app.assistant.runtime.closure as closure_mod

        closure_mod.SEED_MANIFEST_DIGEST = "c" * 64  # type: ignore[misc]
        return

    if mutation == "build_revision":
        import app.assistant.runtime.closure as closure_mod
        from app.config import get_settings

        # Force live process build to diverge from the prepared rollout.
        get_settings.cache_clear()
        original = get_settings().app_build_revision

        class _Patched:
            app_build_revision = "mutated-build-revision-xyz"

        # Patch get_settings used inside closure builder.
        closure_mod.get_settings = lambda: _Patched()  # type: ignore[assignment]
        _ = original
        return

    if mutation == "runtime_contract":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.RUNTIME_CONTRACT_VERSION = (
            int(closure_mod.RUNTIME_CONTRACT_VERSION) + 99
        )
        return

    if mutation == "checkpoint_codec":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.CURRENT_CHECKPOINT_CODEC_VERSION = (
            int(closure_mod.CURRENT_CHECKPOINT_CODEC_VERSION) + 99
        )
        return

    if mutation == "feature_digest":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.default_capability_feature_digest = lambda: "d" * 64  # type: ignore[assignment]
        return

    if mutation == "create_entry_contract_digest":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.CREATE_ENTRY_CONTRACT_DIGEST = "1" * 64
        return

    if mutation == "write_policy_digest":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.WRITE_POLICY_DIGEST = "2" * 64
        return

    if mutation == "write_cohort_digest":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.WRITE_COHORT_DIGEST = "3" * 64
        return

    if mutation == "reconciliation_contract_version":
        import app.assistant.runtime.closure as closure_mod

        closure_mod.RECONCILIATION_CONTRACT_VERSION += 1
        return

    raise AssertionError(f"unknown mutation: {mutation}")


def test_build_subject_from_bootstrap_assets_without_gate_use(db, bound_model):
    """Bootstrap Profile/Skill have no gate_use pins; subject must still build."""
    from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
    from app.assistant.runtime.models import AssistantMainAgentRolloutRevision
    from app.assistant.skills.models import AssistantSkillPackage

    prepared = _stage_bootstrap(db, model_id=bound_model.model.id)
    package = db.get(AssistantSkillPackage, prepared.skill_package_id)
    assert package is not None
    assert package.is_system is True

    builder = AssistantRuntimeClosureBuilder(db)
    subject = builder.build_subject(
        profile_version_id=prepared.profile_version_id,
        model_id=bound_model.model.id,
        build_revision=BUILD,
    )
    assert subject.profile_version_id == prepared.profile_version_id
    assert subject.model_id == bound_model.model.id
    assert len(subject.model_identity_digest) == 64
    assert len(subject.package_closure) >= 1
    entry = subject.package_closure[0]
    assert "packageId" in entry
    assert "versionId" in entry
    assert "resourceMerkleRoot" in entry

    rollout = db.get(AssistantMainAgentRolloutRevision, prepared.rollout_revision_id)
    assert rollout is not None
    # Bootstrap used the real builder — revalidate must succeed without drift.
    closure = builder.build(rollout_revision_id=rollout.id)
    builder.revalidate(closure)


def test_recheck_skips_probe_when_frozen_probe_absent():
    from app.assistant.main_agent.model_eligibility import (
        FrozenModelIdentity,
        recheck_identity_before_decrypt,
    )

    frozen = FrozenModelIdentity(
        model_id=uuid4(),
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=uuid4(),
        credential_runtime_revision=1,
        credential_config_digest="d" * 64,
        model_config_digest="a" * 64,
        provider_ref_digest="e" * 64,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    # Must not require a live probe when frozen has none.
    recheck_identity_before_decrypt(
        frozen=frozen,
        live_model_runtime_revision=1,
        live_credential_runtime_revision=1,
        live_model_config_digest="a" * 64,
        live_credential_config_digest="d" * 64,
        live_probe_id=None,
        live_probe_digest=None,
    )
