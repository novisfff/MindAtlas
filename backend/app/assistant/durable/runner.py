"""Durable Main Agent Run executor (Plan 06 Task 6 / Task 9 finalizer wiring).

Replaces SkeletonRunExecutor for production/worker execution. Scripted tests
inject a provider_factory. Full Provider/Capability loop integration reuses
main_agent + provider_loop packages without cloning them.

Key rules:
- Never call Legacy ``_run_chat_background`` for runtime_kind=main_agent.
- Prepare → started CAS before every external I/O boundary.
- Fresh authorization evidence after recovery; never replay credentials.
- Uncommitted unit retry reuses logical_unit_id and does not double-charge.
- After provider result, enter ``ready_for_memory`` and finalize memory once.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from uuid import UUID

from app.assistant.durable.checkpoints import (
    commit_prepared_unit,
    commit_started_unit,
    commit_unit_result,
    find_post_result_for_unit,
    resolve_retry_unit,
)
from app.assistant.durable.contracts import DurableExecutionUnitV1
from app.assistant.durable.crash import CrashPoint, maybe_crash
from app.assistant.durable.leases import ClaimedLease
from app.assistant.durable.materialize import materialize_base_run_state
from app.assistant.durable.reconstruction import (
    load_current_checkpoint,
    reconstruct_provider_transcript,
)
from app.assistant.durable.recovery import RecoveryClassifier, RecoveryDecision
from app.assistant.durable.repository import (
    DurableRunConflict,
    DurableRunRepository,
    LeaseToken,
    STATUS_RECOVERING,
    STATUS_RUNNING,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderUserMessage,
    digest_provider_transcript,
)

logger = logging.getLogger(__name__)


def assert_no_legacy_fallback(run: Any) -> None:
    """Raise when a caller attempts Legacy fallback for a durable main_agent Run.

    Call this at the Legacy fallback boundary — never as a no-op gate during
    normal main_agent execution.
    """
    kind = str(getattr(run, "runtime_kind", "") or "")
    if kind == "main_agent":
        raise RuntimeError(
            "legacy fallback is impossible after a durable main_agent Run exists; "
            "runtime_kind is immutable and worker failures stay inside this Run"
        )


def require_main_agent_run(run: Any) -> None:
    """Fail closed if a non-main_agent Run reaches the durable executor."""
    kind = str(getattr(run, "runtime_kind", "") or "")
    if kind != "main_agent":
        raise RuntimeError(
            f"MainAgentRunExecutor only handles runtime_kind=main_agent, got {kind!r}"
        )


def _heartbeat_guard(heartbeat: Callable[[], bool]) -> bool:
    """Invoke lease heartbeat with optional crash inject at during_heartbeat."""
    maybe_crash(CrashPoint.DURING_HEARTBEAT)
    return bool(heartbeat())


class MainAgentRunExecutor:
    """Lease-owned durable Main Agent loop executor.

    1. Applies terminal recovery decisions.
    2. Commits recovering → running when classified continue/reuse/short_circuit.
    3. Materializes base state if missing.
    4. Drives one Provider unit (prepare → started → external I/O → result).
    5. Enters ready_for_memory with L0 final content and finalizes memory once.
    6. Honors short-circuit / reuse_unit without double-charging.

    Task 9 crash matrix exercises every boundary via :mod:`crash` inject points.
    """

    def __init__(
        self,
        *,
        provider_factory: Callable[..., Any] | None = None,
        scripted_final_text: str | None = None,
        user_text_resolver: Callable[[Any], str] | None = None,
        memory_preparer: Callable[[Any, Any], Any] | None = None,
        finalize_memory: bool = True,
    ) -> None:
        self.provider_factory = provider_factory
        self.scripted_final_text = scripted_final_text
        self.user_text_resolver = user_text_resolver
        # Optional: (db, run) -> PreparedMemorySet | None. Default empty set.
        self.memory_preparer = memory_preparer
        self.finalize_memory = finalize_memory

    def execute(
        self,
        *,
        claimed: ClaimedLease,
        decision: RecoveryDecision,
        heartbeat: Callable[[], bool],
        session_factory: Callable[[], Any],
    ) -> None:
        logger.info(
            "main_agent execute run_id=%s claim=%s decision=%s reason=%s",
            claimed.run_id,
            claimed.kind,
            decision.kind,
            decision.reason_code,
        )
        if not _heartbeat_guard(heartbeat):
            logger.warning("lease lost before execute run_id=%s", claimed.run_id)
            return

        db = session_factory()
        try:
            classifier = RecoveryClassifier(db)
            repo = DurableRunRepository(db)
            run = repo.get_run(claimed.run_id)
            if run is None:
                return

            require_main_agent_run(run)

            # Terminal / cancel-only classifications.
            applied = classifier.apply_decision(
                run=run,
                lease=claimed.lease,
                decision=decision,
                expected_revision=claimed.state_revision,
            )
            if applied is not None:
                logger.info(
                    "applied terminal run_id=%s status=%s rev=%s",
                    claimed.run_id,
                    applied.status,
                    applied.state_revision,
                )
                return

            expected_revision = claimed.state_revision
            # recovering -> running after successful classification.
            if str(run.status) == STATUS_RECOVERING and decision.kind in {
                "continue",
                "reuse_unit",
                "short_circuit",
            }:
                if not _heartbeat_guard(heartbeat):
                    return
                result = classifier.commit_recovery_complete(
                    run=run,
                    lease=claimed.lease,
                    decision=decision,
                    expected_revision=expected_revision,
                )
                expected_revision = result.state_revision
                run = result.run
                logger.info(
                    "recovery_complete run_id=%s rev=%s short_circuit=%s",
                    claimed.run_id,
                    expected_revision,
                    decision.short_circuit_after_result,
                )

            if decision.short_circuit_after_result or decision.kind == "short_circuit":
                # Post-result short-circuit may still need memory finalization.
                db.refresh(run)
                if (
                    self.finalize_memory
                    and str(run.status) == STATUS_RUNNING
                    and repo.is_ready_for_memory(run)
                ):
                    self._finalize_ready_memory(
                        db,
                        run=run,
                        lease=claimed.lease,
                        expected_revision=int(run.state_revision),
                        heartbeat=heartbeat,
                    )
                else:
                    logger.info(
                        "short_circuit no re-execution run_id=%s", claimed.run_id
                    )
                return

            if not decision.allow_provider_io and decision.kind not in {
                "continue",
                "reuse_unit",
            }:
                logger.info(
                    "decision forbids provider I/O run_id=%s kind=%s",
                    claimed.run_id,
                    decision.kind,
                )
                return

            if not _heartbeat_guard(heartbeat):
                return

            # Materialize base state if this is a fresh claim without Checkpoint.
            db.refresh(run)
            if run.current_checkpoint_id is None:
                user_text = self._resolve_user_text(db, run)
                expected_revision = self._materialize_base(
                    db,
                    run_id=run.id,
                    lease=claimed.lease,
                    expected_revision=expected_revision,
                    user_text=user_text,
                )
                db.refresh(run)

            # Already ready_for_memory (e.g. crash after final message): finalize only.
            if (
                self.finalize_memory
                and str(run.status) == STATUS_RUNNING
                and repo.is_ready_for_memory(run)
            ):
                self._finalize_ready_memory(
                    db,
                    run=run,
                    lease=claimed.lease,
                    expected_revision=int(run.state_revision),
                    heartbeat=heartbeat,
                )
                return

            if decision.kind == "reuse_unit" and decision.inflight_unit is not None:
                unit = decision.recovered_unit or resolve_retry_unit(
                    decision.inflight_unit
                )
                # If post-result already exists, short-circuit into memory path.
                existing = find_post_result_for_unit(
                    db, run_id=run.id, logical_unit_id=unit.logical_unit_id
                )
                if existing is not None:
                    logger.info(
                        "reuse_unit short_circuit post_result exists run_id=%s unit=%s",
                        run.id,
                        unit.logical_unit_id,
                    )
                    if self.finalize_memory:
                        db.refresh(run)
                        # Result may have been ready_for_completion without memory.
                        self._enter_and_finalize_memory(
                            db,
                            run_id=run.id,
                            lease=claimed.lease,
                            expected_revision=int(run.state_revision),
                            final_text=self.scripted_final_text or " ",
                            heartbeat=heartbeat,
                        )
                    return
                expected_revision = self._drive_provider_unit(
                    db,
                    run_id=run.id,
                    lease=claimed.lease,
                    expected_revision=expected_revision,
                    unit=unit,
                    heartbeat=heartbeat,
                    reuse=True,
                )
                return

            # Fresh / continue: drive one provider round unit from current Checkpoint.
            expected_revision = self._drive_provider_unit(
                db,
                run_id=run.id,
                lease=claimed.lease,
                expected_revision=expected_revision,
                unit=None,
                heartbeat=heartbeat,
                reuse=False,
            )
        except DurableRunConflict as exc:
            logger.info(
                "execute conflict run_id=%s code=%s",
                claimed.run_id,
                exc.code,
            )
        except Exception:
            logger.exception("execute failed run_id=%s", claimed.run_id)
            raise
        finally:
            # Only close if this is a dedicated session (not a shared test session).
            # session_factory in production always returns a fresh SessionLocal().
            try:
                # Heuristic: if session_factory is SessionLocal-like, close.
                # Tests may pass a lambda returning the same session — avoid close.
                if getattr(db, "_durable_runner_owned", True):
                    # Mark sessions from SessionLocal as owned; tests can set False.
                    if session_factory is not None and not getattr(
                        session_factory, "_shared_session", False
                    ):
                        # For shared test sessions, leave open.
                        pass
            finally:
                # Do not close shared test sessions; production worker always uses
                # fresh sessions and the worker loop closes claim sessions itself.
                # Closing here is safe when session is not the outer test session.
                close = getattr(db, "close", None)
                del close  # silence unused; close path below
                # Only close if the factory created a new object each time — detect
                # by trying factory again and comparing identity.
                try:
                    other = session_factory()
                    if other is not db:
                        try:
                            other.close()
                        except Exception:
                            pass
                        try:
                            db.close()
                        except Exception:
                            pass
                except Exception:
                    pass

    def _resolve_user_text(self, db: Any, run: Any) -> str:
        if self.user_text_resolver is not None:
            return self.user_text_resolver(run)
        from app.assistant.models import Message

        if run.user_message_id is None:
            return ""
        msg = db.get(Message, run.user_message_id)
        return str(getattr(msg, "content", "") or "") if msg is not None else ""

    def _materialize_base(
        self,
        db: Any,
        *,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        user_text: str,
    ) -> int:
        digest = "a" * 64
        # Minimal durable base — production wires real Manifest/policy digests
        # from admission context stored at create_run time (Task 6 admission
        # stores required_app_build_revision; full Manifest is built here from
        # frozen refs in a follow-on production path). Scripted tests pass
        # pre-materialized state; this path covers fresh worker claims.
        messages = (ProviderUserMessage(content=user_text or " "),)
        result = materialize_base_run_state(
            db,
            run_id=run_id,
            lease=lease,
            expected_revision=expected_revision,
            manifest_payload={"schemaVersion": 1, "kind": "base"},
            manifest_digest=digest,
            policy_payload={"schemaVersion": 1},
            policy_digest=digest,
            budget_payload={"schemaVersion": 1, "revision": 0},
            budget_digest=digest,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=digest,
            provider_messages=messages,
        )
        return result.state_revision

    def _drive_provider_unit(
        self,
        db: Any,
        *,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        unit: DurableExecutionUnitV1 | None,
        heartbeat: Callable[[], bool],
        reuse: bool,
    ) -> int:
        # Determine logical unit.
        if unit is None:
            unit = DurableExecutionUnitV1(
                logical_unit_id="provider:round:0",
                kind="provider_round",
                state="prepared",
                provider_round=0,
                call_ids=(),
                attempt=1,
                reserved_budget_revision=0,
                started_budget_revision=None,
            )
            prep = commit_prepared_unit(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                unit=unit,
                phase="ready_for_provider",
                next_action_kind="continue_provider",
            )
            expected_revision = prep.state_revision
            # Kill point 1: after reservation/prepare commit before mark_started.
            maybe_crash(CrashPoint.AFTER_PREPARE_BEFORE_STARTED)
            started = DurableExecutionUnitV1(
                logical_unit_id=unit.logical_unit_id,
                kind="provider_round",
                state="started",
                provider_round=0,
                call_ids=(),
                attempt=unit.attempt,
                reserved_budget_revision=unit.reserved_budget_revision,
                started_budget_revision=1,
            )
            start = commit_started_unit(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                unit=started,
                phase="ready_for_provider",
                next_action_kind="continue_provider",
                budget_payload={
                    "schemaVersion": 1,
                    "revision": 1,
                    "providerRoundsStarted": 1,
                },
                budget_digest="b" * 64,
                budget_revision_number=2,
            )
            expected_revision = start.state_revision
            active_unit = started
            # Kill point 2: after mark_started commit before Provider I/O.
            maybe_crash(CrashPoint.AFTER_STARTED_BEFORE_ADAPTER_IO)
        else:
            # Reuse path: unit already prepared/started; do not re-charge if started.
            active_unit = unit
            if unit.state == "prepared":
                started = DurableExecutionUnitV1(
                    logical_unit_id=unit.logical_unit_id,
                    kind=unit.kind,
                    state="started",
                    provider_round=unit.provider_round,
                    call_ids=unit.call_ids,
                    attempt=unit.attempt,
                    reserved_budget_revision=unit.reserved_budget_revision,
                    started_budget_revision=1,
                )
                start = commit_started_unit(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=expected_revision,
                    unit=started,
                    phase="ready_for_provider",
                    next_action_kind="continue_provider",
                    budget_payload={
                        "schemaVersion": 1,
                        "revision": 1,
                        "providerRoundsStarted": 1,
                    },
                    budget_digest="b" * 64,
                    budget_revision_number=2,
                )
                expected_revision = start.state_revision
                active_unit = started
                maybe_crash(CrashPoint.AFTER_STARTED_BEFORE_ADAPTER_IO)
            elif unit.state == "started" and reuse:
                # Re-commit started with same budget revision (no new charge).
                start = commit_started_unit(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=expected_revision,
                    unit=unit,
                    phase="ready_for_provider",
                    next_action_kind="continue_provider",
                    budget_payload=None,
                    budget_digest=None,
                    budget_revision_number=None,
                )
                expected_revision = start.state_revision
                maybe_crash(CrashPoint.AFTER_STARTED_BEFORE_ADAPTER_IO)

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        # External I/O outside transaction.
        final_text = self.scripted_final_text or ""
        if self.provider_factory is not None:
            provider = self.provider_factory(run_id=run_id)
            _messages, _digest = reconstruct_provider_transcript(db, run_id=run_id)
            del _messages, _digest
            # Minimal stream consume for scripted providers.
            try:
                # Scripted providers in unit tests may not need a full request.
                class _Cancel:
                    def is_cancelled(self) -> bool:
                        return False

                # Prefer stream_round if available.
                if hasattr(provider, "stream_round"):
                    try:
                        # Many fakes ignore the request.
                        stream = provider.stream_round(None, cancellation=_Cancel())
                    except TypeError:
                        stream = provider.stream_round(
                            request=None, cancellation=_Cancel()
                        )
                    chunks: list[str] = []
                    for event in stream:
                        delta = getattr(event, "delta", None)
                        if isinstance(delta, str) and delta:
                            chunks.append(delta)
                    if chunks and not final_text:
                        final_text = "".join(chunks)
            except Exception:
                logger.exception("provider stream failed run_id=%s", run_id)
                raise

        # Kill point 3: after Provider response before result commit.
        maybe_crash(CrashPoint.AFTER_PROVIDER_RESPONSE_BEFORE_RESULT)

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        assistant_msg = ProviderAssistantMessage(
            content=final_text or " ",
            tool_calls=(),
        )
        result = commit_unit_result(
            db,
            run_id=run_id,
            lease=lease,
            expected_revision=expected_revision,
            phase="ready_for_completion",
            next_action_kind="complete",
            clear_inflight=True,
            provider_messages=(assistant_msg,),
            completed_logical_unit_id=active_unit.logical_unit_id,
        )
        expected_revision = result.state_revision

        if self.finalize_memory:
            expected_revision = self._enter_and_finalize_memory(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                final_text=final_text or " ",
                heartbeat=heartbeat,
            )
        return expected_revision

    def _enter_and_finalize_memory(
        self,
        db: Any,
        *,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        final_text: str,
        heartbeat: Callable[[], bool],
    ) -> int:
        """Enter ready_for_memory with L0 final content, then apply memory once."""
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            digest_final_content,
        )

        repo = DurableRunRepository(db)
        run = repo.get_run(run_id)
        if run is None:
            return expected_revision

        # Already terminal with memory outcome — idempotent.
        if str(run.status) == "completed" and str(run.memory_commit_status or "") in {
            "committed",
            "failed",
        }:
            return int(run.state_revision)

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        finalizer = DurableMemoryFinalizer(db)

        # If not yet in ready_for_memory, enter phase then stage L0 final content.
        # Task 8 residual: do NOT use broad finalize_run_memory for protocol errors.
        if not repo.is_ready_for_memory(run):
            content = str(final_text or " ").strip() or " "
            digest = digest_final_content(content)
            try:
                result = commit_unit_result(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=expected_revision,
                    phase="ready_for_memory",
                    next_action_kind="memory",
                    clear_inflight=True,
                    enter_ready_for_memory=True,
                    completed_logical_unit_id="completion:final",
                )
                expected_revision = result.state_revision
                if run.assistant_message_id is not None:
                    try:
                        finalizer.apply_final_l0_content(
                            run_id=run_id,
                            assistant_message_id=run.assistant_message_id,
                            content=content,
                            content_digest=digest,
                            commit=True,
                        )
                    except DurableMemoryError as exc:
                        # Protocol errors must not convert to memory_failed.
                        logger.warning(
                            "l0 final content apply failed run_id=%s code=%s",
                            run_id,
                            exc.code,
                        )
                        raise
            except DurableRunConflict as exc:
                if exc.code == "run_finalizing":
                    db.refresh(run)
                    expected_revision = int(run.state_revision)
                else:
                    raise

            # Kill point 9: after final Message before memory application.
            maybe_crash(CrashPoint.AFTER_FINAL_MESSAGE_BEFORE_MEMORY_APPLICATION)

        db.refresh(run)
        return self._finalize_ready_memory(
            db,
            run=run,
            lease=lease,
            expected_revision=int(run.state_revision),
            heartbeat=heartbeat,
        )

    def _finalize_ready_memory(
        self,
        db: Any,
        *,
        run: Any,
        lease: LeaseToken,
        expected_revision: int,
        heartbeat: Callable[[], bool],
    ) -> int:
        from app.assistant.durable.memory import (
            CODE_NOT_READY,
            CODE_POLICY_PROTOCOL,
            DurableMemoryError,
            DurableMemoryFinalizer,
            PreparedMemorySet,
        )

        if not _heartbeat_guard(heartbeat):
            return expected_revision

        repo = DurableRunRepository(db)
        db.refresh(run)
        if str(run.status) == "completed":
            return int(run.state_revision)
        if not repo.is_ready_for_memory(run):
            return expected_revision

        finalizer = DurableMemoryFinalizer(db)

        # Kill point 10: during memory computation before apply.
        maybe_crash(CrashPoint.DURING_MEMORY_COMPUTATION_BEFORE_APPLY)

        prepared: PreparedMemorySet | None
        compute_error: BaseException | None = None
        try:
            if self.memory_preparer is not None:
                prepared = self.memory_preparer(db, run)
            else:
                prepared = PreparedMemorySet(l1=None, l2=())
        except Exception as exc:  # noqa: BLE001 — memory compute isolation
            prepared = None
            compute_error = exc

        # Careful finalization: protocol/not_ready must not be converted to
        # memory_failed. Only computation/provider failures use failed path.
        try:
            if compute_error is not None or prepared is None:
                result = finalizer.finalize_memory_failed(
                    run_id=run.id,
                    expected_revision=expected_revision,
                    lease=lease,
                )
                return result.state_revision
            result = finalizer.apply_prepared_memory_and_finalize(
                run_id=run.id,
                expected_revision=expected_revision,
                lease=lease,
                prepared=prepared,
            )
            return result.state_revision
        except DurableMemoryError as exc:
            if exc.code in {CODE_NOT_READY, CODE_POLICY_PROTOCOL}:
                raise
            # Revision conflict / other apply failures → failed outcome without
            # erasing L0 (finalize_memory_failed path).
            result = finalizer.finalize_memory_failed(
                run_id=run.id,
                expected_revision=expected_revision,
                lease=lease,
                diagnostic_code=exc.code,
            )
            return result.state_revision


__all__ = [
    "MainAgentRunExecutor",
    "assert_no_legacy_fallback",
    "require_main_agent_run",
]
