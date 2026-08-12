"""Production qualification contract and fail-closed create-entry guard tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()


def test_create_entry_contract_digest_binds_execution_safety_contracts():
    from app.assistant.capability_calls.write_guard import (
        APPROVAL_BINDING_CONTRACT_VERSION,
        CAPABILITY_LEDGER_CONTRACT_VERSION,
        CREATE_ENTRY_CONTRACT_DIGEST,
        IDEMPOTENCY_CONTRACT_VERSION,
        RECONCILIATION_CONTRACT_VERSION,
        create_entry_contract_payload,
        system_tool_input_schema_digest,
        system_tool_output_schema_digest,
    )
    from app.assistant.domain.digests import sha256_canonical_json

    payload = create_entry_contract_payload()
    assert payload == {
        "schemaVersion": 1,
        "domainKey": "create_entry",
        "inputSchemaDigest": system_tool_input_schema_digest("create_entry"),
        "outputSchemaDigest": system_tool_output_schema_digest("create_entry"),
        "localAdapterContractVersion": 1,
        "capabilityLedgerContractVersion": CAPABILITY_LEDGER_CONTRACT_VERSION,
        "approvalBindingContractVersion": APPROVAL_BINDING_CONTRACT_VERSION,
        "idempotencyContractVersion": IDEMPOTENCY_CONTRACT_VERSION,
        "reconciliationContractVersion": RECONCILIATION_CONTRACT_VERSION,
    }
    assert CREATE_ENTRY_CONTRACT_DIGEST == sha256_canonical_json(payload)


def test_write_policy_and_cohort_are_code_owned():
    from app.assistant.capability_calls.write_guard import (
        WRITE_COHORT_DIGEST,
        WRITE_COHORT_PAYLOAD,
        WRITE_POLICY_DIGEST,
        write_policy_payload,
    )
    from app.assistant.domain.digests import sha256_canonical_json

    assert WRITE_COHORT_PAYLOAD == {
        "schemaVersion": 1,
        "cohort": "single_operator_main_agent",
        "supportedWrites": ["create_entry"],
    }
    assert WRITE_COHORT_DIGEST == sha256_canonical_json(WRITE_COHORT_PAYLOAD)
    assert WRITE_POLICY_DIGEST == sha256_canonical_json(write_policy_payload())
    assert write_policy_payload() == {
        "schemaVersion": 1,
        "ledger": {"mode": "enforced", "contractVersion": 1},
        "approval": {"mode": "call_owned_durable", "contractVersion": 1},
        "execution": {"mode": "local_transactional"},
        "idempotency": {
            "scope": "same_call",
            "replay": "exact_request_only",
            "contractVersion": 1,
        },
        "postApprovalGuard": {"required": True},
        "uncertainOutcome": {
            "disposition": "reconciliation_required",
            "contractVersion": 1,
        },
    }


class _LockPort:
    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, db) -> None:  # noqa: ANN001
        del db
        self.calls += 1


class _SchemaPort:
    def __init__(self) -> None:
        self.compatible = True

    def is_compatible(self, db) -> bool:  # noqa: ANN001
        del db
        return self.compatible


class _LaunchPort:
    def __init__(self) -> None:
        self.allowed = True

    def allows_current_subject(self, db, *, closure, deployment_class) -> bool:  # noqa: ANN001
        del db, closure, deployment_class
        return self.allowed


class _ClosurePort:
    def __init__(self) -> None:
        self.drifted = False

    def revalidate(self, closure):  # noqa: ANN001
        if self.drifted:
            raise RuntimeError("drift")
        return closure


@dataclass
class _WriteState:
    db: object

    def __post_init__(self) -> None:
        from app.assistant.capability_calls.write_guard import (
            CREATE_ENTRY_CONTRACT_DIGEST,
            RECONCILIATION_CONTRACT_VERSION,
            WRITE_COHORT_DIGEST,
            WRITE_POLICY_DIGEST,
            system_tool_input_schema_digest,
            system_tool_output_schema_digest,
        )
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.skills.resolution import (
            build_binding_snapshot,
            compute_system_tool_contract_set_digest,
            system_tool_schemas,
        )
        from app.schema.contracts import DeploymentClass

        self.lock = _LockPort()
        self.schema = _SchemaPort()
        self.launch = _LaunchPort()
        self.closure_port = _ClosurePort()
        self.deployment_class = DeploymentClass.PRODUCTION
        self.unresolved = {"unknown": 0, "needs_reconciliation": 0}
        self.operator_available = True
        self.settings = SimpleNamespace(
            assistant_main_agent_write_mode="create_entry",
            assistant_durable_interrupts_enabled=True,
            assistant_interrupt_token_pepper="pepper-stable",
            assistant_capability_reconciliation_enabled=True,
            assistant_capability_reconciliation_evidence_secret="e" * 32,
            assistant_capability_call_idempotency_secret="i" * 32,
        )
        self.closure = SimpleNamespace(
            rollout_revision_id="rollout-1",
            profile_version_id="profile-1",
            model_id="model-1",
            closure_digest="a" * 64,
            build_revision="build-immutable",
            runtime_contract_version=1,
            checkpoint_codec_version=3,
            capability_feature_digest="b" * 64,
            create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
            write_policy_digest=WRITE_POLICY_DIGEST,
            write_cohort_digest=WRITE_COHORT_DIGEST,
            reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
        )
        self.run = SimpleNamespace(
            main_agent_rollout_revision_id="rollout-1",
            main_agent_profile_version_id="profile-1",
            resolved_model_id="model-1",
            runtime_closure_digest="a" * 64,
            required_app_build_revision="build-immutable",
            runtime_contract_version=1,
            required_checkpoint_codec_version=3,
            required_capability_feature_digest="b" * 64,
            capability_ledger_mode="enforced",
            required_create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
            required_write_policy_digest=WRITE_POLICY_DIGEST,
            required_write_cohort_digest=WRITE_COHORT_DIGEST,
            required_reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
        )
        input_schema, output_schema = system_tool_schemas("create_entry")
        tool_set_digest = compute_system_tool_contract_set_digest()
        resolution_digest = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "capabilityType": "tool",
                "targetIdentity": "system-tool:create_entry",
                "targetId": None,
                "targetVersionId": None,
                "targetRevision": None,
                "inputSchemaDigest": system_tool_input_schema_digest("create_entry"),
                "outputSchemaDigest": system_tool_output_schema_digest("create_entry"),
                "executableRevision": self.closure.build_revision,
                "configDigest": tool_set_digest,
                "systemToolContractSetDigest": tool_set_digest,
            }
        )
        snapshot, dependency_digest, binding_digest = build_binding_snapshot(
            capability_type="tool",
            target_identity="system-tool:create_entry",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            input_schema=input_schema,
            output_schema=output_schema,
            completion=SimpleNamespace(
                terminal_output=False,
                needs_followup=True,
                followup_hint=None,
            ),
            config_digest=tool_set_digest,
            executable_revision=self.closure.build_revision,
            resolution_digest=resolution_digest,
            dependencies=(),
        )
        self.binding = SimpleNamespace(
            ref=SimpleNamespace(
                capability_type="tool",
                capability_key="create_entry",
                target_identity="system-tool:create_entry",
                target_id=None,
                target_version_id=None,
                target_revision=None,
                input_schema_digest=system_tool_input_schema_digest("create_entry"),
                output_schema_digest=system_tool_output_schema_digest("create_entry"),
                resolution_digest=resolution_digest,
                dependency_closure_digest=dependency_digest,
                binding_contract_digest=binding_digest,
            ),
            resolved=SimpleNamespace(
                capability_type="tool",
                capability_key="create_entry",
                target_identity="system-tool:create_entry",
                target_id=None,
                target_version_id=None,
                resolved_revision=None,
                input_schema_digest=system_tool_input_schema_digest("create_entry"),
                output_schema_digest=system_tool_output_schema_digest("create_entry"),
                config_digest=tool_set_digest,
                executable_revision=self.closure.build_revision,
                resolution_digest=resolution_digest,
                resolution_snapshot=snapshot,
                dependency_closure_digest=dependency_digest,
                binding_contract_digest=binding_digest,
            )
        )
        self.approval_mode = "call_owned_durable"

    def arrange(self, arrangement: str) -> None:
        from app.schema.contracts import DeploymentClass

        if arrangement == "write_mode_off":
            self.settings.assistant_main_agent_write_mode = "off"
        elif arrangement == "ledger_not_enforced":
            self.run.capability_ledger_mode = "legacy_read_only"
        elif arrangement == "interrupts_disabled":
            self.settings.assistant_durable_interrupts_enabled = False
        elif arrangement == "idempotency_secret_missing":
            self.settings.assistant_capability_call_idempotency_secret = ""
        elif arrangement == "reconciliation_unavailable":
            self.settings.assistant_capability_reconciliation_enabled = False
        elif arrangement == "schema_incompatible":
            self.schema.compatible = False
        elif arrangement == "production_launch_missing":
            self.launch.allowed = False
            self.deployment_class = DeploymentClass.PRODUCTION
        elif arrangement == "rehearsal_authorization_missing":
            self.launch.allowed = False
            self.deployment_class = DeploymentClass.REHEARSAL
        elif arrangement == "closure_contract_drift":
            self.closure_port.drifted = True
        elif arrangement == "binding_not_create":
            self.binding.ref.capability_key = "search_entries"
        elif arrangement == "approval_not_call_owned":
            self.approval_mode = "workflow_owned"
        elif arrangement == "unknown_call_open":
            self.unresolved["unknown"] = 1
        elif arrangement == "needs_reconciliation_open":
            self.unresolved["needs_reconciliation"] = 1
        else:  # pragma: no cover - fixture typo defense
            raise AssertionError(arrangement)

    def effect_snapshot(self) -> tuple[int, int]:
        return self.capability_call_count(), self.lock.calls

    def capability_call_count(self) -> int:
        from app.assistant.capability_calls.models import AssistantCapabilityCall

        return self.db.query(AssistantCapabilityCall).count()

    def propose_new_create(self):  # noqa: ANN201
        from app.assistant.capability_calls.write_guard import ProductionWriteGuard

        guard = ProductionWriteGuard(
            self.db,
            settings=self.settings,
            schema_compatibility=self.schema,
            launch_authorization=self.launch,
            deployment_class=self.deployment_class,
            closure_revalidator=self.closure_port,
            lock_port=self.lock,
            unresolved_counter=lambda _db: dict(self.unresolved),
            operator_control_available=lambda _db: self.operator_available,
        )
        return guard.evaluate_new_proposal_locked(
            run=self.run,
            closure=self.closure,
            domain_key="create_entry",
            binding=self.binding,
            approval_mode=self.approval_mode,
        )


@pytest.fixture
def write_state():
    db = make_session()
    try:
        yield _WriteState(db)
    finally:
        db.close()


@pytest.mark.parametrize(
    ("arrangement", "reason"),
    [
        ("write_mode_off", "create_entry_not_enabled"),
        ("ledger_not_enforced", "write_safety_blocked"),
        ("interrupts_disabled", "write_safety_blocked"),
        ("idempotency_secret_missing", "write_safety_blocked"),
        ("reconciliation_unavailable", "write_safety_blocked"),
        ("schema_incompatible", "write_safety_blocked"),
        ("production_launch_missing", "pre_ga_launch_unapproved"),
        ("rehearsal_authorization_missing", "write_safety_blocked"),
        ("closure_contract_drift", "write_safety_blocked"),
        ("binding_not_create", "capability_not_supported"),
        ("approval_not_call_owned", "write_safety_blocked"),
        ("unknown_call_open", "reconciliation_required"),
        ("needs_reconciliation_open", "reconciliation_required"),
    ],
)
def test_new_write_guard_fails_before_call(write_state, arrangement, reason):
    write_state.arrange(arrangement)
    before_calls = write_state.capability_call_count()
    result = write_state.propose_new_create()
    assert result.reason_code == reason
    assert write_state.capability_call_count() == before_calls == 0
    assert write_state.lock.calls == 1


def test_allowed_snapshot_binds_all_frozen_write_contracts(write_state):
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
    )

    result = write_state.propose_new_create()
    assert result.allowed is True
    assert result.reason_code is None
    assert result.create_entry_contract_digest == CREATE_ENTRY_CONTRACT_DIGEST
    assert result.write_policy_digest == WRITE_POLICY_DIGEST
    assert result.write_cohort_digest == WRITE_COHORT_DIGEST
    assert result.reconciliation_contract_version == RECONCILIATION_CONTRACT_VERSION
    assert "pepper-stable" not in repr(result)
    assert "i" * 32 not in repr(result)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("resolution_digest", "e" * 64),
        ("binding_contract_digest", "f" * 64),
    ],
)
def test_exact_binding_rejects_ref_to_resolved_digest_drift(
    write_state, field, replacement
):
    setattr(write_state.binding.resolved, field, replacement)
    result = write_state.propose_new_create()
    assert result.allowed is False
    assert result.reason_code == "capability_not_supported"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("resolution_digest", "e" * 64),
        ("binding_contract_digest", "f" * 64),
    ],
)
def test_exact_binding_rejects_consistent_but_wrong_digest_pair(
    write_state, field, replacement
):
    setattr(write_state.binding.ref, field, replacement)
    setattr(write_state.binding.resolved, field, replacement)
    result = write_state.propose_new_create()
    assert result.allowed is False
    assert result.reason_code == "capability_not_supported"


def test_unsupported_domain_raises_before_lock(write_state):
    from app.assistant.capabilities.supported_writes import CapabilityNotSupported
    from app.assistant.capability_calls.write_guard import ProductionWriteGuard

    guard = ProductionWriteGuard(
        write_state.db,
        settings=write_state.settings,
        schema_compatibility=write_state.schema,
        launch_authorization=write_state.launch,
        deployment_class=write_state.deployment_class,
        closure_revalidator=write_state.closure_port,
        lock_port=write_state.lock,
        unresolved_counter=lambda _db: dict(write_state.unresolved),
        operator_control_available=lambda _db: True,
    )
    with pytest.raises(CapabilityNotSupported):
        guard.evaluate_new_proposal_locked(
            run=write_state.run,
            closure=write_state.closure,
            domain_key="update_entry",
            binding=write_state.binding,
            approval_mode="call_owned_durable",
        )
    assert write_state.lock.calls == 0


def test_post_approval_revalidates_exact_interrupt_before_side_effect(write_state):
    from app.assistant.capability_calls.write_guard import ProductionWriteGuard

    call = SimpleNamespace(
        id="call-1",
        status="authorized",
        domain_key="create_entry",
        approval_binding_digest="c" * 64,
        interrupt_id="interrupt-1",
    )
    interrupt = SimpleNamespace(
        id="interrupt-1",
        capability_call_id="call-1",
        interrupt_origin="capability_call",
        status="approved",
        request_payload={
            "approvalBindingDigest": "c" * 64,
            "bindingContractDigest": write_state.binding.ref.binding_contract_digest,
            "targetDigest": write_state.binding.ref.resolution_digest,
        },
    )
    # Production launch can be revoked between proposal and approval.
    write_state.launch.allowed = False
    guard = ProductionWriteGuard(
        write_state.db,
        settings=write_state.settings,
        schema_compatibility=write_state.schema,
        launch_authorization=write_state.launch,
        deployment_class=write_state.deployment_class,
        closure_revalidator=write_state.closure_port,
        lock_port=write_state.lock,
        unresolved_counter=lambda _db: dict(write_state.unresolved),
        operator_control_available=lambda _db: True,
    )
    result = guard.evaluate_post_approval_locked(
        call=call,
        run=write_state.run,
        closure=write_state.closure,
        binding=write_state.binding,
        approved_interrupt=interrupt,
    )
    assert result.allowed is False
    assert result.reason_code == "pre_ga_launch_unapproved"
    assert call.status == "authorized"


def test_post_approval_rejects_binding_drift_from_approved_interrupt(write_state):
    from app.assistant.capability_calls.write_guard import ProductionWriteGuard

    approved_binding_digest = write_state.binding.ref.binding_contract_digest
    approved_target_digest = write_state.binding.ref.resolution_digest
    call = SimpleNamespace(
        id="call-1",
        status="authorized",
        domain_key="create_entry",
        approval_binding_digest="c" * 64,
        interrupt_id="interrupt-1",
    )
    interrupt = SimpleNamespace(
        id="interrupt-1",
        capability_call_id="call-1",
        interrupt_origin="capability_call",
        status="approved",
        request_payload={
            "approvalBindingDigest": "c" * 64,
            "bindingContractDigest": approved_binding_digest,
            "targetDigest": approved_target_digest,
        },
    )
    write_state.binding.ref.binding_contract_digest = "e" * 64
    write_state.binding.resolved.binding_contract_digest = "e" * 64
    guard = ProductionWriteGuard(
        write_state.db,
        settings=write_state.settings,
        schema_compatibility=write_state.schema,
        launch_authorization=write_state.launch,
        deployment_class=write_state.deployment_class,
        closure_revalidator=write_state.closure_port,
        lock_port=write_state.lock,
        unresolved_counter=lambda _db: dict(write_state.unresolved),
        operator_control_available=lambda _db: True,
    )
    result = guard.evaluate_post_approval_locked(
        call=call,
        run=write_state.run,
        closure=write_state.closure,
        binding=write_state.binding,
        approved_interrupt=interrupt,
    )
    assert result.allowed is False
    assert result.reason_code == "write_safety_blocked"


def test_create_entry_settings_are_fail_closed_and_secrets_are_not_repr():
    from app.config import Settings

    common = {
        "_env_file": None,
        "APP_ENV": "test",
        "APP_BUILD_REVISION": "immutable-test-build",
        "MINDATLAS_DEPLOYMENT_CLASS": "rehearsal",
        "ASSISTANT_MAIN_AGENT_WRITE_MODE": "create_entry",
        "ASSISTANT_CAPABILITY_LEDGER_MODE": "enforced",
        "ASSISTANT_DURABLE_INTERRUPTS_ENABLED": True,
        "ASSISTANT_INTERRUPT_TOKEN_PEPPER": "stable-pepper",
        "ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED": True,
        "ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET": "e" * 32,
        "ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET": "i" * 32,
    }
    settings = Settings(**common)
    assert settings.assistant_main_agent_write_mode == "create_entry"
    assert not hasattr(settings, "assistant_main_agent_write_cohort_digest")
    assert not hasattr(settings, "assistant_capability_reconciliation_operator_id")
    rendered = repr(settings)
    assert "stable-pepper" not in rendered
    assert "e" * 32 not in rendered
    assert "i" * 32 not in rendered

    for missing in (
        "ASSISTANT_DURABLE_INTERRUPTS_ENABLED",
        "ASSISTANT_INTERRUPT_TOKEN_PEPPER",
        "ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED",
        "ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET",
        "ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET",
    ):
        values = dict(common)
        values[missing] = False if missing.endswith("ENABLED") else ""
        with pytest.raises(ValidationError):
            Settings(**values)


def test_run_and_rollout_write_identity_columns_are_required_without_defaults():
    from app.assistant.models import AssistantChatRun
    from app.assistant.runtime.models import AssistantMainAgentRolloutRevision

    for table in (
        AssistantMainAgentRolloutRevision.__table__,
        AssistantChatRun.__table__,
    ):
        for name in (
            "required_create_entry_contract_digest",
            "required_write_policy_digest",
            "required_write_cohort_digest",
            "required_reconciliation_contract_version",
        ):
            column = table.c[name]
            assert column.nullable is False
            assert column.default is None
            assert column.server_default is None


def test_durable_aggregate_rejects_missing_write_guard():
    from app.assistant.capability_calls.aggregate import (
        DurableCapabilityLedgerAggregate,
    )

    with pytest.raises(ValueError, match="production write guard is required"):
        DurableCapabilityLedgerAggregate(
            db=SimpleNamespace(),
            authorization_factory=SimpleNamespace(),
            idempotency_secret="i" * 32,
            write_guard=None,
        )
