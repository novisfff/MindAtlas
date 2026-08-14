"""Code-owned create-entry contracts and the global production write guard.

The guard is deliberately a transaction-local boundary.  A new proposal and
every transition into or out of an unresolved state use the same PostgreSQL
advisory lock, so the global unresolved-call query cannot race a write.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Final, Mapping, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.assistant.capabilities.supported_writes import (
    CapabilityNotSupported,
    normalize_unsupported_branch,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.schema.contracts import DeploymentClass

CAPABILITY_LEDGER_CONTRACT_VERSION: Final[int] = 1
APPROVAL_BINDING_CONTRACT_VERSION: Final[int] = 1
IDEMPOTENCY_CONTRACT_VERSION: Final[int] = 1
RECONCILIATION_CONTRACT_VERSION: Final[int] = 1
LOCAL_CREATE_ENTRY_ADAPTER_CONTRACT_VERSION: Final[int] = 1

WRITE_SAFETY_ADVISORY_LOCK_KEY: Final[int] = 0x4D41575249544531
UNRESOLVED_WRITE_STATUSES: Final[tuple[str, str]] = (
    "unknown",
    "needs_reconciliation",
)


@lru_cache(maxsize=None)
def _system_tool_schema_digests(domain_key: str) -> tuple[str, str]:
    from app.assistant.domain.json_schema import binding_schema_digest
    from app.assistant.skills.resolution import system_tool_schemas

    input_schema, output_schema = system_tool_schemas(domain_key)
    return binding_schema_digest(input_schema), binding_schema_digest(output_schema)


def system_tool_input_schema_digest(domain_key: str) -> str:
    return _system_tool_schema_digests(str(domain_key))[0]


def system_tool_output_schema_digest(domain_key: str) -> str:
    return _system_tool_schema_digests(str(domain_key))[1]


def create_entry_contract_payload() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "domainKey": "create_entry",
        "inputSchemaDigest": system_tool_input_schema_digest("create_entry"),
        "outputSchemaDigest": system_tool_output_schema_digest("create_entry"),
        "localAdapterContractVersion": LOCAL_CREATE_ENTRY_ADAPTER_CONTRACT_VERSION,
        "capabilityLedgerContractVersion": CAPABILITY_LEDGER_CONTRACT_VERSION,
        "approvalBindingContractVersion": APPROVAL_BINDING_CONTRACT_VERSION,
        "idempotencyContractVersion": IDEMPOTENCY_CONTRACT_VERSION,
        "reconciliationContractVersion": RECONCILIATION_CONTRACT_VERSION,
    }


CREATE_ENTRY_CONTRACT_DIGEST: Final[str] = sha256_canonical_json(
    create_entry_contract_payload()
)

WRITE_COHORT_PAYLOAD: Final[dict[str, Any]] = {
    "schemaVersion": 1,
    "cohort": "single_operator_main_agent",
    "supportedWrites": ["create_entry"],
}
WRITE_COHORT_DIGEST: Final[str] = sha256_canonical_json(WRITE_COHORT_PAYLOAD)


def write_policy_payload() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "ledger": {
            "mode": "enforced",
            "contractVersion": CAPABILITY_LEDGER_CONTRACT_VERSION,
        },
        "approval": {
            "mode": "call_owned_durable",
            "contractVersion": APPROVAL_BINDING_CONTRACT_VERSION,
        },
        "execution": {"mode": "local_transactional"},
        "idempotency": {
            "scope": "same_call",
            "replay": "exact_request_only",
            "contractVersion": IDEMPOTENCY_CONTRACT_VERSION,
        },
        "postApprovalGuard": {"required": True},
        "uncertainOutcome": {
            "disposition": "reconciliation_required",
            "contractVersion": RECONCILIATION_CONTRACT_VERSION,
        },
    }


WRITE_POLICY_DIGEST: Final[str] = sha256_canonical_json(write_policy_payload())


class WriteLaunchAuthorization(Protocol):
    """Transactionally revalidate the durable launch subject.

    Implementations must lock the durable launch/profile row they inspect
    (``SELECT ... FOR UPDATE`` or an equivalent serialization primitive) and
    keep that lock until the caller's transaction commits.  Launch mutation
    uses the same row lock, so authorization cannot be revoked between this
    check and local Entry staging.
    """

    def allows_current_subject(
        self,
        db: Session,
        *,
        closure: Any,
        deployment_class: DeploymentClass,
    ) -> bool: ...


class WriteSafetyLock(Protocol):
    def acquire(self, db: Session) -> None: ...


class WriteSchemaCompatibility(Protocol):
    def is_compatible(self, db: Session) -> bool: ...


class RuntimeClosureRevalidator(Protocol):
    def revalidate(self, closure: Any) -> Any: ...


class _PostgresAdvisoryLock:
    def acquire(self, db: Session) -> None:
        acquire_write_safety_advisory_lock(db)


class _DenyLaunchAuthorization:
    def allows_current_subject(
        self,
        db: Session,
        *,
        closure: Any,
        deployment_class: DeploymentClass,
    ) -> bool:
        del db, closure, deployment_class
        return False


class _DefaultLaunchAuthorization:
    """Use the durable production gate when no test port is injected."""

    def allows_current_subject(
        self,
        db: Session,
        *,
        closure: Any,
        deployment_class: DeploymentClass,
    ) -> bool:
        del closure
        if deployment_class is not DeploymentClass.PRODUCTION:
            # Rehearsal writes are authorized only by the release-profile
            # adapter, which must be injected by that profile. Development is
            # intentionally write-disabled.
            return False
        try:
            from app.pre_ga_launch.factory import default_pre_ga_launch_service

            return bool(
                default_pre_ga_launch_service(db).evaluate_current_launch().launched
            )
        except Exception:
            return False


def acquire_write_safety_advisory_lock(db: Session) -> None:
    """Acquire the one production write-safety transaction lock.

    Production code never treats an unknown/non-PostgreSQL dialect as a safe
    no-op.  SQLite unit tests must inject a lock port explicitly.
    """
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "")
    if dialect != "postgresql":
        raise RuntimeError("write_safety_advisory_lock_requires_postgresql")
    db.execute(
        text("SELECT pg_advisory_xact_lock(:write_safety_key)"),
        {"write_safety_key": WRITE_SAFETY_ADVISORY_LOCK_KEY},
    )


def _default_unresolved_counter(db: Session) -> dict[str, int]:
    from app.assistant.capability_calls.models import AssistantCapabilityCall

    rows = db.execute(
        select(AssistantCapabilityCall.status, func.count())
        .where(AssistantCapabilityCall.status.in_(UNRESOLVED_WRITE_STATUSES))
        .group_by(AssistantCapabilityCall.status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    return {status: int(counts.get(status, 0)) for status in UNRESOLVED_WRITE_STATUSES}


def _default_operator_control_available(db: Session, settings: Any) -> bool:
    try:
        from app.operator_auth.dependencies import load_session_mac_key_ring
        from app.operator_auth.models import OperatorAccount

        account = (
            db.query(OperatorAccount)
            .filter(
                OperatorAccount.singleton_key == "operator",
                OperatorAccount.enabled.is_(True),
            )
            .one_or_none()
        )
        return account is not None and load_session_mac_key_ring(settings) is not None
    except Exception:
        return False


def _deployment_class_from_process(settings: Any) -> DeploymentClass | None:
    raw = str(
        getattr(settings, "mindatlas_deployment_class", "")
        or os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "")
    ).strip()
    try:
        return DeploymentClass(raw)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ProductionWriteGuardSnapshot:
    allowed: bool
    reason_code: str | None
    unresolved_counts: Mapping[str, int] = field(default_factory=dict)
    create_entry_contract_digest: str | None = None
    write_policy_digest: str | None = None
    write_cohort_digest: str | None = None
    reconciliation_contract_version: int | None = None


class ProductionWriteGuard:
    """Fail-closed guard for a new or approved local create-entry mutation."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Any | None = None,
        schema_compatibility: WriteSchemaCompatibility | None = None,
        launch_authorization: WriteLaunchAuthorization | None = None,
        deployment_class: DeploymentClass | None = None,
        closure_revalidator: RuntimeClosureRevalidator | None = None,
        lock_port: WriteSafetyLock | None = None,
        unresolved_counter: Callable[[Session], Mapping[str, int]] | None = None,
        operator_control_available: Callable[[Session], bool] | None = None,
    ) -> None:
        from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
        from app.assistant.runtime.readiness import Plan2AlembicHeadCompatibility
        from app.config import get_settings

        self.db = db
        self.settings = settings if settings is not None else get_settings()
        self.schema_compatibility = (
            schema_compatibility
            if schema_compatibility is not None
            else Plan2AlembicHeadCompatibility()
        )
        self.launch_authorization = launch_authorization or _DefaultLaunchAuthorization()
        self.deployment_class = deployment_class or _deployment_class_from_process(
            self.settings
        )
        self.closure_revalidator = closure_revalidator or AssistantRuntimeClosureBuilder(db)
        self.lock_port = lock_port or _PostgresAdvisoryLock()
        self.unresolved_counter = unresolved_counter or _default_unresolved_counter
        self.operator_control_available = operator_control_available or (
            lambda session: _default_operator_control_available(session, self.settings)
        )

    def evaluate_new_proposal_locked(
        self,
        *,
        run: Any,
        closure: Any,
        domain_key: str,
        binding: Any,
        approval_mode: str,
        lock_already_held: bool = False,
    ) -> ProductionWriteGuardSnapshot:
        normalized = str(domain_key or "").strip()
        if normalized != "create_entry":
            raise CapabilityNotSupported(normalize_unsupported_branch(normalized))
        if not lock_already_held:
            try:
                from app.pre_ga_launch.repository import LaunchRepository

                LaunchRepository(self.db).lock_launch()
                self.lock_port.acquire(self.db)
            except Exception:
                return self._blocked("write_safety_blocked")
        return self._evaluate(
            run=run,
            closure=closure,
            binding=binding,
            approval_mode=approval_mode,
        )

    def evaluate_post_approval_locked(
        self,
        *,
        call: Any,
        run: Any,
        closure: Any,
        binding: Any,
        approved_interrupt: Any,
        lock_already_held: bool = False,
    ) -> ProductionWriteGuardSnapshot:
        if str(getattr(call, "domain_key", "") or "") != "create_entry":
            raw = str(getattr(call, "domain_key", "") or "")
            raise CapabilityNotSupported(normalize_unsupported_branch(raw))
        if not lock_already_held:
            try:
                from app.pre_ga_launch.repository import LaunchRepository

                LaunchRepository(self.db).lock_launch()
                self.lock_port.acquire(self.db)
            except Exception:
                return self._blocked("write_safety_blocked")
        if not self._approved_interrupt_matches(call, approved_interrupt, binding):
            return self._blocked("write_safety_blocked")
        return self._evaluate(
            run=run,
            closure=closure,
            binding=binding,
            approval_mode="call_owned_durable",
        )

    def _evaluate(
        self,
        *,
        run: Any,
        closure: Any,
        binding: Any,
        approval_mode: str,
    ) -> ProductionWriteGuardSnapshot:
        # Exact order is part of the write-policy contract.
        try:
            if not self.schema_compatibility.is_compatible(self.db):
                return self._blocked("write_safety_blocked")
        except Exception:
            return self._blocked("write_safety_blocked")

        if not self._closure_is_current(run, closure):
            return self._blocked("write_safety_blocked")

        deployment = self.deployment_class
        if deployment is None:
            return self._blocked("write_safety_blocked")
        try:
            launch_allowed = self.launch_authorization.allows_current_subject(
                self.db,
                closure=closure,
                deployment_class=deployment,
            )
        except Exception:
            launch_allowed = False
        if not launch_allowed:
            return self._blocked(
                "pre_ga_launch_unapproved"
                if deployment is DeploymentClass.PRODUCTION
                else "write_safety_blocked"
            )

        if str(getattr(self.settings, "assistant_main_agent_write_mode", "off")) != (
            "create_entry"
        ):
            return self._blocked("create_entry_not_enabled")
        if str(getattr(run, "capability_ledger_mode", "")) != "enforced":
            return self._blocked("write_safety_blocked")
        if not self._write_contracts_match(run, closure):
            return self._blocked("write_safety_blocked")
        if not self._binding_is_exact_create_entry(binding, closure):
            return self._blocked("capability_not_supported")
        if str(approval_mode or "") != "call_owned_durable":
            return self._blocked("write_safety_blocked")
        if not self._controls_available():
            return self._blocked("write_safety_blocked")
        secret = str(
            getattr(
                self.settings,
                "assistant_capability_call_idempotency_secret",
                "",
            )
            or ""
        )
        if len(secret.encode("utf-8")) < 32:
            return self._blocked("write_safety_blocked")
        try:
            raw_counts = self.unresolved_counter(self.db)
            counts = {
                status: int(raw_counts.get(status, 0))
                for status in UNRESOLVED_WRITE_STATUSES
            }
        except Exception:
            return self._blocked("write_safety_blocked")
        if any(counts.values()):
            return self._blocked("reconciliation_required", counts=counts)
        return ProductionWriteGuardSnapshot(
            allowed=True,
            reason_code=None,
            unresolved_counts=counts,
            create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
            write_policy_digest=WRITE_POLICY_DIGEST,
            write_cohort_digest=WRITE_COHORT_DIGEST,
            reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
        )

    @staticmethod
    def _blocked(
        reason_code: str,
        *,
        counts: Mapping[str, int] | None = None,
    ) -> ProductionWriteGuardSnapshot:
        from app.assistant.capability_calls.observability import record_capability_metric

        # Phase is supplied by the call site in the full runner; this static
        # boundary defaults to proposal and never accepts request metadata.
        safe_reason = str(reason_code)
        record_capability_metric(
            "mindatlas_create_entry_write_guard_rejection_total",
            {"reason_code": safe_reason, "phase": "proposal"},
        )
        return ProductionWriteGuardSnapshot(
            allowed=False,
            reason_code=str(reason_code),
            unresolved_counts=dict(counts or {}),
        )

    def _closure_is_current(self, run: Any, closure: Any) -> bool:
        try:
            current = self.closure_revalidator.revalidate(closure)
        except Exception:
            return False
        if current != closure:
            return False
        checks = (
            ("main_agent_rollout_revision_id", "rollout_revision_id"),
            ("main_agent_profile_version_id", "profile_version_id"),
            ("resolved_model_id", "model_id"),
            ("runtime_closure_digest", "closure_digest"),
            ("required_app_build_revision", "build_revision"),
            ("runtime_contract_version", "runtime_contract_version"),
            ("required_checkpoint_codec_version", "checkpoint_codec_version"),
            ("required_capability_feature_digest", "capability_feature_digest"),
        )
        return all(
            str(getattr(run, run_name, ""))
            == str(getattr(closure, closure_name, ""))
            for run_name, closure_name in checks
        )

    @staticmethod
    def _write_contracts_match(run: Any, closure: Any) -> bool:
        expected = (
            (
                "required_create_entry_contract_digest",
                "create_entry_contract_digest",
                CREATE_ENTRY_CONTRACT_DIGEST,
            ),
            ("required_write_policy_digest", "write_policy_digest", WRITE_POLICY_DIGEST),
            ("required_write_cohort_digest", "write_cohort_digest", WRITE_COHORT_DIGEST),
            (
                "required_reconciliation_contract_version",
                "reconciliation_contract_version",
                RECONCILIATION_CONTRACT_VERSION,
            ),
        )
        return all(
            getattr(run, run_name, None)
            == getattr(closure, closure_name, None)
            == code_value
            for run_name, closure_name, code_value in expected
        )

    @staticmethod
    def _binding_is_exact_create_entry(binding: Any, closure: Any) -> bool:
        ref = getattr(binding, "ref", None)
        resolved = getattr(binding, "resolved", None)
        if ref is None or resolved is None:
            return False
        from app.assistant.domain.json_schema import binding_schema_digest
        from app.assistant.skills.resolution import (
            compute_system_tool_contract_set_digest,
        )

        input_digest = system_tool_input_schema_digest("create_entry")
        output_digest = system_tool_output_schema_digest("create_entry")
        tool_set_digest = compute_system_tool_contract_set_digest()
        executable_revision = str(getattr(closure, "build_revision", "") or "")
        expected_resolution_digest = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "capabilityType": "tool",
                "targetIdentity": "system-tool:create_entry",
                "targetId": None,
                "targetVersionId": None,
                "targetRevision": None,
                "inputSchemaDigest": input_digest,
                "outputSchemaDigest": output_digest,
                "executableRevision": executable_revision,
                "configDigest": tool_set_digest,
                "systemToolContractSetDigest": tool_set_digest,
            }
        )
        expected = (
            ("capability_type", "tool"),
            ("capability_key", "create_entry"),
            ("target_identity", "system-tool:create_entry"),
            ("target_id", None),
            ("target_version_id", None),
            ("input_schema_digest", input_digest),
            ("output_schema_digest", output_digest),
            ("resolution_digest", expected_resolution_digest),
        )
        if any(
            getattr(ref, field_name, None) != expected_value
            or getattr(resolved, field_name, None) != expected_value
            for field_name, expected_value in expected
        ):
            return False
        if getattr(ref, "target_revision", None) is not None:
            return False
        if getattr(resolved, "resolved_revision", None) is not None:
            return False
        if (
            str(getattr(resolved, "config_digest", "") or "") != tool_set_digest
            or str(getattr(resolved, "executable_revision", "") or "")
            != executable_revision
        ):
            return False

        binding_digest = str(getattr(ref, "binding_contract_digest", "") or "")
        if (
            not binding_digest
            or binding_digest
            != str(getattr(resolved, "binding_contract_digest", "") or "")
        ):
            return False
        snapshot = getattr(resolved, "resolution_snapshot", None)
        if not isinstance(snapshot, Mapping):
            return False
        payload = {
            key: value for key, value in snapshot.items() if key != "bindingContractDigest"
        }
        if (
            str(snapshot.get("bindingContractDigest") or "") != binding_digest
            or sha256_canonical_json(payload) != binding_digest
        ):
            return False
        target = snapshot.get("target")
        execution = snapshot.get("execution")
        if not isinstance(target, Mapping) or not isinstance(execution, Mapping):
            return False
        if dict(target) != {
            "capabilityType": "tool",
            "targetIdentity": "system-tool:create_entry",
            "targetId": None,
            "targetVersionId": None,
            "targetRevision": None,
            "resolutionDigest": expected_resolution_digest,
        }:
            return False
        if dict(execution) != {
            "configDigest": tool_set_digest,
            "executableRevision": executable_revision,
        }:
            return False
        if snapshot.get("inputSchemaDigest") != input_digest or snapshot.get(
            "outputSchemaDigest"
        ) != output_digest:
            return False
        try:
            if binding_schema_digest(snapshot.get("inputSchema")) != input_digest:
                return False
            if binding_schema_digest(snapshot.get("outputSchema")) != output_digest:
                return False
        except Exception:
            return False
        empty_closure_digest = sha256_canonical_json([])
        return (
            snapshot.get("dependencyClosure") == []
            and snapshot.get("dependencyClosureDigest") == empty_closure_digest
            and getattr(ref, "dependency_closure_digest", empty_closure_digest)
            == empty_closure_digest
            and getattr(resolved, "dependency_closure_digest", empty_closure_digest)
            == empty_closure_digest
        )

    def _controls_available(self) -> bool:
        settings = self.settings
        interrupts = bool(
            getattr(settings, "assistant_durable_interrupts_enabled", False)
        ) and bool(
            str(getattr(settings, "assistant_interrupt_token_pepper", "") or "").strip()
        )
        reconciliation = bool(
            getattr(settings, "assistant_capability_reconciliation_enabled", False)
        ) and len(
            str(
                getattr(
                    settings,
                    "assistant_capability_reconciliation_evidence_secret",
                    "",
                )
                or ""
            ).encode("utf-8")
        ) >= 32
        try:
            operator = bool(self.operator_control_available(self.db))
        except Exception:
            operator = False
        return interrupts and reconciliation and operator

    @staticmethod
    def _approved_interrupt_matches(call: Any, interrupt: Any, binding: Any) -> bool:
        if interrupt is None:
            return False
        payload = getattr(interrupt, "request_payload", None) or {}
        digest = str(getattr(call, "approval_binding_digest", "") or "")
        ref = getattr(binding, "ref", None)
        return (
            str(getattr(call, "status", "")) == "authorized"
            and bool(digest)
            and str(getattr(interrupt, "id", ""))
            == str(getattr(call, "interrupt_id", ""))
            and str(getattr(interrupt, "capability_call_id", ""))
            == str(getattr(call, "id", ""))
            and str(getattr(interrupt, "interrupt_origin", ""))
            == "capability_call"
            and str(getattr(interrupt, "status", "")) == "approved"
            and str(payload.get("approvalBindingDigest") or "") == digest
            and ref is not None
            and str(payload.get("bindingContractDigest") or "")
            == str(getattr(ref, "binding_contract_digest", "") or "")
            and str(payload.get("targetDigest") or "")
            == str(getattr(ref, "resolution_digest", "") or "")
        )


__all__ = [
    "APPROVAL_BINDING_CONTRACT_VERSION",
    "CAPABILITY_LEDGER_CONTRACT_VERSION",
    "CREATE_ENTRY_CONTRACT_DIGEST",
    "IDEMPOTENCY_CONTRACT_VERSION",
    "LOCAL_CREATE_ENTRY_ADAPTER_CONTRACT_VERSION",
    "ProductionWriteGuard",
    "ProductionWriteGuardSnapshot",
    "RECONCILIATION_CONTRACT_VERSION",
    "UNRESOLVED_WRITE_STATUSES",
    "WRITE_COHORT_DIGEST",
    "WRITE_COHORT_PAYLOAD",
    "WRITE_POLICY_DIGEST",
    "WRITE_SAFETY_ADVISORY_LOCK_KEY",
    "WriteLaunchAuthorization",
    "acquire_write_safety_advisory_lock",
    "create_entry_contract_payload",
    "system_tool_input_schema_digest",
    "system_tool_output_schema_digest",
    "write_policy_payload",
]
