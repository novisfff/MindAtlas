"""Bounded recovery classification for durable Main Agent Runs (Plan 06 Task 5 §8.4).

Recovery never silently continues an old Run with a new credential key slot.
Provider/Gateway I/O is forbidden until classification returns a safe continue
decision and the worker commits ``recovering -> running``.

Classification outcomes:
- continue: Checkpoint/refs validated; commit recovery_complete then execute
- short_circuit: post-result Checkpoint already committed; no re-execution
- reuse_unit: same logical_unit_id; reuse reservation/started; increment attempt only
- needs_reconciliation: codec/build/credential/ref/artifact drift
- fail: deterministic contract/input/output failure
- cancel_only: cancelling takeover — seal/finalize only
- backoff: transient infrastructure error; schedule next_attempt_at
- exhausted: recovery_count exceeded; fail or reconcile depending on certainty
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.durable.codec import (
    NeedsReconciliationError,
    decode_checkpoint,
    checkpoint_state_digest,
)
from app.assistant.durable.contracts import (
    DurableAgentCheckpointV1,
    DurableExecutionUnitV1,
)
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunManifestRevision,
    AssistantRunObligationRevision,
    AssistantRunPolicyRevision,
)
from app.assistant.durable.repository import (
    DurableCommitResult,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
    STATUS_CANCELLING,
    STATUS_FAILED,
    STATUS_NEEDS_RECONCILIATION,
    STATUS_RECOVERING,
    STATUS_RUNNING,
)
from app.assistant.models import AssistantChatRun
from app.config import get_settings

logger = logging.getLogger(__name__)

RecoveryKind = Literal[
    "continue",
    "short_circuit",
    "reuse_unit",
    "needs_reconciliation",
    "fail",
    "cancel_only",
    "backoff",
    "exhausted",
]


@dataclass(frozen=True)
class CredentialSnapshot:
    """Frozen credential identity observed for a Run at recovery time.

    A mismatch against the Manifest's credential_runtime_revision (or key-slot
    rotation during an active Run) must perform no Provider/Gateway call and
    enter ``needs_reconciliation``.
    """

    credential_id: UUID | None
    credential_runtime_revision: int | None
    credential_config_digest: str | None = None


@dataclass(frozen=True)
class RecoveryDecision:
    """Bounded classification result for one claimed recovery/takeover."""

    kind: RecoveryKind
    reason_code: str
    detail: str | None = None
    checkpoint: DurableAgentCheckpointV1 | None = None
    inflight_unit: DurableExecutionUnitV1 | None = None
    # When reuse_unit: the unit with attempt incremented (reservation/started kept).
    recovered_unit: DurableExecutionUnitV1 | None = None
    # Side-effect flags for the worker loop (Task 6 plugs execution here).
    allow_provider_io: bool = False
    allow_capability_io: bool = False
    short_circuit_after_result: bool = False

    @property
    def is_terminal_classification(self) -> bool:
        return self.kind in {
            "needs_reconciliation",
            "fail",
            "cancel_only",
            "exhausted",
        }


class CredentialResolver(Protocol):
    """Port: resolve live credential revision for a frozen credential id.

    Task 6 wires the real AI registry lookup. Tests inject fakes.
    """

    def resolve_credential_snapshot(
        self, *, credential_id: UUID
    ) -> CredentialSnapshot | None: ...


class NoopCredentialResolver:
    """Default resolver that reports no live credential (forces reconcile if needed)."""

    def resolve_credential_snapshot(
        self, *, credential_id: UUID
    ) -> CredentialSnapshot | None:
        return None


@dataclass
class RecoveryClassifier:
    """Classify a claimed Run before any Provider/Capability adapter I/O."""

    db: Session
    max_recovery_attempts: int | None = None
    credential_resolver: CredentialResolver | None = None
    # Optional Artifact object existence probe: (storage_key) -> bool.
    artifact_object_exists: Callable[[str], bool] | None = None

    def __post_init__(self) -> None:
        if self.max_recovery_attempts is None:
            self.max_recovery_attempts = int(
                get_settings().assistant_worker_max_recovery_attempts
            )
        if self.credential_resolver is None:
            self.credential_resolver = NoopCredentialResolver()
        self.repo = DurableRunRepository(self.db)

    def classify(
        self,
        *,
        run: AssistantChatRun,
        claim_kind: str,
        worker_app_build_revision: str,
        worker_supported_codec_versions: Sequence[int] | None = None,
    ) -> RecoveryDecision:
        """Bounded recovery classification (no Provider/Gateway I/O)."""
        # Cancellation-only takeover: never resume model/capability work.
        if claim_kind == "reclaim_cancelling" or str(run.status) == STATUS_CANCELLING:
            return RecoveryDecision(
                kind="cancel_only",
                reason_code="cancelling_takeover",
                detail="expired cancelling lease; cancellation finalization only",
                allow_provider_io=False,
                allow_capability_io=False,
            )

        # Recovery-count exhaustion.
        recovery_count = int(run.recovery_count or 0)
        max_attempts = int(self.max_recovery_attempts or 5)
        if recovery_count > max_attempts:
            # Uncertain state (has checkpoint/inflight) stays needs_reconciliation;
            # otherwise fail safely.
            if run.current_checkpoint_id is not None:
                return RecoveryDecision(
                    kind="exhausted",
                    reason_code="recovery_exhausted_uncertain",
                    detail=(
                        f"recovery_count={recovery_count} exceeds "
                        f"max={max_attempts} with uncertain checkpoint state"
                    ),
                    allow_provider_io=False,
                    allow_capability_io=False,
                )
            return RecoveryDecision(
                kind="exhausted",
                reason_code="recovery_exhausted",
                detail=(
                    f"recovery_count={recovery_count} exceeds max={max_attempts}"
                ),
                allow_provider_io=False,
                allow_capability_io=False,
            )

        # Build compatibility.
        required_build = str(run.required_app_build_revision or "")
        if required_build and required_build != str(worker_app_build_revision):
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="build_revision_mismatch",
                detail=(
                    f"run requires build={required_build!r}, "
                    f"worker has {worker_app_build_revision!r}"
                ),
            )

        supported = {
            int(v)
            for v in (
                worker_supported_codec_versions
                if worker_supported_codec_versions is not None
                else (1,)
            )
        }

        # Load + validate Checkpoint (if any).
        checkpoint_row = self._load_current_checkpoint(run)
        if checkpoint_row is None:
            # Fresh claim with no checkpoint yet — safe to continue from start.
            if claim_kind == "queued" or str(run.status) == STATUS_RUNNING:
                return RecoveryDecision(
                    kind="continue",
                    reason_code="no_checkpoint",
                    detail="no current checkpoint; begin/continue execution",
                    allow_provider_io=True,
                    allow_capability_io=True,
                )
            # recovering without checkpoint is uncertain.
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="missing_checkpoint",
                detail="recovering run has no current checkpoint",
            )

        # Codec / schema version.
        schema_version = int(checkpoint_row.schema_version or 0)
        if schema_version not in supported:
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="unsupported_checkpoint_codec",
                detail=(
                    f"checkpoint schema_version={schema_version} not in "
                    f"supported={sorted(supported)}"
                ),
            )

        try:
            decoded = decode_checkpoint(checkpoint_row.state_payload or {})
        except NeedsReconciliationError as exc:
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="checkpoint_codec_drift",
                detail=str(exc),
            )
        except Exception as exc:
            return RecoveryDecision(
                kind="fail",
                reason_code="checkpoint_decode_failed",
                detail=f"deterministic decode failure: {exc}",
            )

        # Digest verification.
        try:
            expected_digest = checkpoint_state_digest(decoded)
        except Exception as exc:
            return RecoveryDecision(
                kind="fail",
                reason_code="checkpoint_digest_failed",
                detail=str(exc),
            )
        if str(checkpoint_row.state_digest or "") != expected_digest:
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="checkpoint_digest_mismatch",
                detail="stored state_digest does not match decoded checkpoint",
            )

        # Pointer consistency: checkpoint revision ids must match run pointers.
        pointer_err = self._validate_pointers(run, checkpoint_row, decoded)
        if pointer_err is not None:
            return pointer_err

        # Credential revision drift — no Provider/Gateway I/O.
        cred_err = self._validate_credential_revision(run, decoded)
        if cred_err is not None:
            return cred_err

        # Artifact availability for referenced ids.
        art_err = self._validate_artifacts(run, decoded)
        if art_err is not None:
            return art_err

        # Short-circuit: post-result / ready_for_completion|memory|terminal with
        # no inflight unit means the result is already committed.
        phase = str(decoded.phase)
        inflight = decoded.inflight_unit
        if phase in {"ready_for_completion", "ready_for_memory", "terminal"} and (
            inflight is None
        ):
            return RecoveryDecision(
                kind="short_circuit",
                reason_code="post_result_committed",
                detail=(
                    f"checkpoint phase={phase} has no inflight unit; "
                    "short-circuit without re-execution"
                ),
                checkpoint=decoded,
                inflight_unit=None,
                allow_provider_io=False,
                allow_capability_io=False,
                short_circuit_after_result=True,
            )

        # Same logical unit recovery: reuse reservation/started; increment attempt.
        if inflight is not None and str(inflight.state) in {"prepared", "started"}:
            recovered = DurableExecutionUnitV1(
                logical_unit_id=inflight.logical_unit_id,
                kind=inflight.kind,
                state=inflight.state,
                provider_round=inflight.provider_round,
                call_ids=inflight.call_ids,
                attempt=int(inflight.attempt) + 1,
                reserved_budget_revision=inflight.reserved_budget_revision,
                started_budget_revision=inflight.started_budget_revision,
            )
            return RecoveryDecision(
                kind="reuse_unit",
                reason_code="same_logical_unit",
                detail=(
                    f"reuse logical_unit_id={inflight.logical_unit_id} "
                    f"state={inflight.state}; attempt {inflight.attempt}->{recovered.attempt}"
                ),
                checkpoint=decoded,
                inflight_unit=inflight,
                recovered_unit=recovered,
                allow_provider_io=True,
                allow_capability_io=True,
            )

        # Validated — safe to continue after committing recovering -> running.
        return RecoveryDecision(
            kind="continue",
            reason_code="recovery_validated",
            detail=f"checkpoint phase={phase} validated",
            checkpoint=decoded,
            inflight_unit=inflight,
            allow_provider_io=True,
            allow_capability_io=True,
        )

    def apply_decision(
        self,
        *,
        run: AssistantChatRun,
        lease: LeaseToken,
        decision: RecoveryDecision,
        expected_revision: int | None = None,
    ) -> DurableCommitResult | None:
        """Commit the repository transition implied by a classification.

        Returns None when the decision is pure continue/reuse/short_circuit that
        still needs ``complete_recovery`` (recovering -> running) first — callers
        should call :meth:`commit_recovery_complete` for those.

        For terminal classifications, commits failed/needs_reconciliation/cancelled.
        """
        rev = (
            int(expected_revision)
            if expected_revision is not None
            else int(run.state_revision)
        )

        if decision.kind == "cancel_only":
            return self.repo.finalize_cancellation(
                run_id=run.id,
                expected_revision=rev,
                lease=lease,
                require_lease=True,
                events=(
                    EventSpec(
                        event_key=f"recovery.cancel_only:{run.id}:{rev}",
                        event_name="run.cancelled",
                        payload={
                            "reasonCode": decision.reason_code,
                            "via": "recovery_finalizer",
                        },
                        visibility="public",
                    ),
                ),
            )

        if decision.kind in {"needs_reconciliation", "exhausted"} and (
            decision.reason_code.endswith("uncertain")
            or decision.kind == "needs_reconciliation"
            or run.current_checkpoint_id is not None
        ):
            # exhausted with uncertain state, or any needs_reconciliation.
            if str(run.status) == STATUS_RECOVERING:
                return self.repo.commit_recovery_terminal(
                    run_id=run.id,
                    expected_revision=rev,
                    lease=lease,
                    target_status=STATUS_NEEDS_RECONCILIATION,
                    failure_code=decision.reason_code,
                    error_message=(decision.detail or decision.reason_code)[:500],
                    events=(
                        EventSpec(
                            event_key=f"recovery.reconcile:{run.id}:{rev}",
                            event_name="run.needs_reconciliation",
                            payload={
                                "reasonCode": decision.reason_code,
                                "detail": decision.detail,
                            },
                            visibility="public",
                        ),
                    ),
                )
            # running path
            return self.repo.commit_running_result(
                run_id=run.id,
                expected_revision=rev,
                lease=lease,
                target_status=STATUS_NEEDS_RECONCILIATION,
                failure_code=decision.reason_code,
                error_message=(decision.detail or decision.reason_code)[:500],
                events=(
                    EventSpec(
                        event_key=f"recovery.reconcile:{run.id}:{rev}",
                        event_name="run.needs_reconciliation",
                        payload={
                            "reasonCode": decision.reason_code,
                            "detail": decision.detail,
                        },
                        visibility="public",
                    ),
                ),
            )

        if decision.kind in {"fail", "exhausted"}:
            target = STATUS_FAILED
            if str(run.status) == STATUS_RECOVERING:
                return self.repo.commit_recovery_terminal(
                    run_id=run.id,
                    expected_revision=rev,
                    lease=lease,
                    target_status=target,
                    failure_code=decision.reason_code,
                    error_message=(decision.detail or decision.reason_code)[:500],
                    events=(
                        EventSpec(
                            event_key=f"recovery.fail:{run.id}:{rev}",
                            event_name="run.failed",
                            payload={
                                "reasonCode": decision.reason_code,
                                "detail": decision.detail,
                            },
                            visibility="public",
                        ),
                    ),
                )
            return self.repo.commit_running_result(
                run_id=run.id,
                expected_revision=rev,
                lease=lease,
                target_status=target,
                failure_code=decision.reason_code,
                error_message=(decision.detail or decision.reason_code)[:500],
                events=(
                    EventSpec(
                        event_key=f"recovery.fail:{run.id}:{rev}",
                        event_name="run.failed",
                        payload={
                            "reasonCode": decision.reason_code,
                            "detail": decision.detail,
                        },
                        visibility="public",
                    ),
                ),
            )

        # continue / reuse_unit / short_circuit / backoff: no terminal commit here.
        return None

    def commit_recovery_complete(
        self,
        *,
        run: AssistantChatRun,
        lease: LeaseToken,
        decision: RecoveryDecision,
        expected_revision: int | None = None,
    ) -> DurableCommitResult:
        """Commit recovering -> running after successful classification."""
        rev = (
            int(expected_revision)
            if expected_revision is not None
            else int(run.state_revision)
        )
        payload: dict[str, Any] = {
            "reasonCode": decision.reason_code,
            "kind": decision.kind,
        }
        if decision.recovered_unit is not None:
            payload["logicalUnitId"] = decision.recovered_unit.logical_unit_id
            payload["attempt"] = decision.recovered_unit.attempt
            payload["unitState"] = decision.recovered_unit.state
        if decision.short_circuit_after_result:
            payload["shortCircuit"] = True

        return self.repo.complete_recovery(
            run_id=run.id,
            expected_revision=rev,
            lease=lease,
            events=(
                EventSpec(
                    event_key=f"recovery.complete:{run.id}:{rev}:{decision.kind}",
                    event_name="run.recovery.complete",
                    payload=payload,
                    visibility="internal",
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _load_current_checkpoint(
        self, run: AssistantChatRun
    ) -> AssistantRunCheckpoint | None:
        if run.current_checkpoint_id is None:
            return None
        row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        if row is None:
            return None
        if row.run_id != run.id:
            return None
        return row

    def _validate_pointers(
        self,
        run: AssistantChatRun,
        checkpoint_row: AssistantRunCheckpoint,
        decoded: DurableAgentCheckpointV1,
    ) -> RecoveryDecision | None:
        """Ensure checkpoint revision linkage matches run current pointers."""
        checks = (
            ("manifest", run.current_manifest_revision_id, checkpoint_row.manifest_revision_id, decoded.manifest_revision_id),
            ("policy", run.current_policy_revision_id, checkpoint_row.policy_revision_id, decoded.policy_revision_id),
            ("budget", run.current_budget_revision_id, checkpoint_row.budget_revision_id, decoded.budget_revision_id),
            ("obligation", run.current_obligation_revision_id, checkpoint_row.obligation_revision_id, decoded.obligation_revision_id),
        )
        for name, run_ptr, row_ptr, decoded_ptr in checks:
            if run_ptr is not None and row_ptr is not None and run_ptr != row_ptr:
                return RecoveryDecision(
                    kind="needs_reconciliation",
                    reason_code="pointer_mismatch",
                    detail=f"{name} run pointer {run_ptr} != checkpoint row {row_ptr}",
                )
            if row_ptr is not None and decoded_ptr is not None and row_ptr != decoded_ptr:
                return RecoveryDecision(
                    kind="needs_reconciliation",
                    reason_code="pointer_mismatch",
                    detail=(
                        f"{name} checkpoint row {row_ptr} != decoded {decoded_ptr}"
                    ),
                )
            # Ensure referenced revision rows exist when pointers are set.
            if row_ptr is not None:
                model = {
                    "manifest": AssistantRunManifestRevision,
                    "policy": AssistantRunPolicyRevision,
                    "budget": AssistantRunBudgetRevision,
                    "obligation": AssistantRunObligationRevision,
                }[name]
                if self.db.get(model, row_ptr) is None:
                    return RecoveryDecision(
                        kind="needs_reconciliation",
                        reason_code="missing_revision_row",
                        detail=f"{name} revision {row_ptr} missing",
                    )
        return None

    def _validate_credential_revision(
        self,
        run: AssistantChatRun,
        decoded: DurableAgentCheckpointV1,
    ) -> RecoveryDecision | None:
        """Detect credential key-slot / runtime-revision drift.

        Manifest payload is expected to carry frozen model/credential refs under
        standard keys. When a live credential resolver reports a different
        runtime revision, we enter needs_reconciliation without any Provider I/O.
        """
        if run.current_manifest_revision_id is None:
            return None
        manifest = self.db.get(
            AssistantRunManifestRevision, run.current_manifest_revision_id
        )
        if manifest is None:
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="missing_manifest",
                detail="current manifest revision row missing",
            )

        payload = manifest.payload or {}
        frozen = self._extract_frozen_credential(payload)
        if frozen is None:
            # No credential freeze in payload — nothing to compare.
            return None

        cred_id, frozen_rev, frozen_digest = frozen
        if self.credential_resolver is None:
            return None
        live = self.credential_resolver.resolve_credential_snapshot(
            credential_id=cred_id
        )
        if live is None:
            # Cannot resolve live credential — treat as drift (do not use unknown key).
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="credential_unresolvable",
                detail=f"credential {cred_id} cannot be resolved for recovery",
            )
        if live.credential_runtime_revision is not None and frozen_rev is not None:
            if int(live.credential_runtime_revision) != int(frozen_rev):
                return RecoveryDecision(
                    kind="needs_reconciliation",
                    reason_code="credential_revision_drift",
                    detail=(
                        f"frozen credential_runtime_revision={frozen_rev} "
                        f"!= live={live.credential_runtime_revision}; "
                        "no Provider/Gateway I/O"
                    ),
                )
        if (
            frozen_digest
            and live.credential_config_digest
            and str(frozen_digest) != str(live.credential_config_digest)
        ):
            return RecoveryDecision(
                kind="needs_reconciliation",
                reason_code="credential_config_drift",
                detail="credential config digest mismatch; no Provider/Gateway I/O",
            )
        return None

    def _extract_frozen_credential(
        self, payload: Mapping[str, Any]
    ) -> tuple[UUID, int | None, str | None] | None:
        """Pull credential id/revision/digest from a Manifest payload.

        Accepts both camelCase (wire) and snake_case keys, and nested
        model/provider objects used by Plan 04/05 manifests.
        """
        candidates: list[Mapping[str, Any]] = [payload]
        for key in (
            "model",
            "modelRef",
            "model_ref",
            "provider",
            "providerRef",
            "provider_ref",
            "frozenModel",
            "frozen_model",
        ):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                candidates.append(nested)

        for obj in candidates:
            raw_id = (
                obj.get("credentialId")
                or obj.get("credential_id")
                or obj.get("credentialID")
            )
            if raw_id is None:
                continue
            try:
                cred_id = UUID(str(raw_id))
            except (TypeError, ValueError):
                continue
            raw_rev = (
                obj.get("credentialRuntimeRevision")
                or obj.get("credential_runtime_revision")
            )
            rev: int | None
            try:
                rev = int(raw_rev) if raw_rev is not None else None
            except (TypeError, ValueError):
                rev = None
            digest = (
                obj.get("credentialConfigDigest")
                or obj.get("credential_config_digest")
            )
            digest_s = str(digest) if digest else None
            return cred_id, rev, digest_s
        return None

    def _validate_artifacts(
        self,
        run: AssistantChatRun,
        decoded: DurableAgentCheckpointV1,
    ) -> RecoveryDecision | None:
        """Referenced Artifact rows must exist; missing objects => reconcile."""
        for art_id in decoded.artifact_ids or ():
            row = self.db.get(AssistantRunArtifact, art_id)
            if row is None or row.run_id != run.id:
                return RecoveryDecision(
                    kind="needs_reconciliation",
                    reason_code="artifact_row_missing",
                    detail=f"artifact {art_id} missing for run",
                )
            # Object existence probe for object-backed Artifacts only.
            if (
                str(getattr(row, "storage_kind", "") or "") == "object"
                and row.object_key
                and self.artifact_object_exists is not None
            ):
                try:
                    if not self.artifact_object_exists(str(row.object_key)):
                        return RecoveryDecision(
                            kind="needs_reconciliation",
                            reason_code="artifact_object_missing",
                            detail=f"artifact object missing for {art_id}",
                        )
                except Exception as exc:
                    return RecoveryDecision(
                        kind="backoff",
                        reason_code="artifact_probe_transient",
                        detail=str(exc),
                    )
        return None


__all__ = [
    "CredentialResolver",
    "CredentialSnapshot",
    "NoopCredentialResolver",
    "RecoveryClassifier",
    "RecoveryDecision",
    "RecoveryKind",
]
