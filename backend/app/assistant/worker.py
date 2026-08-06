"""Durable Main Agent assistant worker (Plan 06 Task 5+6).

Usage:
    python -m app.assistant.worker
    python -m app.assistant.worker --healthcheck

Task 5 owns registration, claim/lease/heartbeat, recovery classification, and
the process skeleton. Task 6 plugs MainAgentRunExecutor for Provider/Capability
execution at Checkpoint boundaries. SkeletonRunExecutor remains available for
tests that inject it explicitly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any, Callable, Protocol

from app.assistant.durable.leases import ClaimedLease, RunLeaseService
from app.assistant.durable.recovery import (
    CredentialResolver,
    NoopCredentialResolver,
    RecoveryClassifier,
    RecoveryDecision,
)
from app.assistant.durable.repository import (
    DurableRunConflict,
    DurableRunRepository,
    LeaseToken,
    STATUS_RECOVERING,
)
from app.assistant.durable.worker_registry import (
    WorkerIdentity,
    WorkerRegistry,
    default_capability_feature_digest,
)
from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Path written after successful registration so Docker healthcheck can validate
# a *fresh compatible registration* rather than a mere Python PID.
DEFAULT_WORKER_STATE_PATH = Path(
    os.environ.get(
        "ASSISTANT_WORKER_STATE_PATH",
        "/tmp/mindatlas-assistant-worker-state.json",
    )
)

# Stable process exit when the live schema is incompatible with this build.
# Plan 3 will swap the schema identity port; reason remains schema_incompatible.
WORKER_SCHEMA_INCOMPATIBLE_EXIT = 78


class RunExecutor(Protocol):
    """Task 6 port: execute a claimed Run under a live lease.

    Implementations must:
    - perform no Provider/Capability I/O when ``decision.allow_*`` is False;
    - honor short-circuit / reuse_unit decisions;
    - stop immediately when heartbeat returns False (lease lost).
    """

    def execute(
        self,
        *,
        claimed: ClaimedLease,
        decision: RecoveryDecision,
        heartbeat: Callable[[], bool],
        session_factory: Callable[[], Any],
    ) -> None: ...


class SkeletonRunExecutor:
    """Task 5 placeholder: classification + recovery commit only, no loop I/O.

    Logs the decision and applies terminal/recovery-complete transitions so the
    worker process is exercisable without the full Main Agent loop.
    """

    def execute(
        self,
        *,
        claimed: ClaimedLease,
        decision: RecoveryDecision,
        heartbeat: Callable[[], bool],
        session_factory: Callable[[], Any],
    ) -> None:
        logger.info(
            "skeleton execute run_id=%s claim=%s decision=%s reason=%s",
            claimed.run_id,
            claimed.kind,
            decision.kind,
            decision.reason_code,
        )
        if not heartbeat():
            logger.warning(
                "lease lost before skeleton apply run_id=%s", claimed.run_id
            )
            return

        db = session_factory()
        try:
            classifier = RecoveryClassifier(db)
            repo = DurableRunRepository(db)
            run = repo.get_run(claimed.run_id)
            if run is None:
                return

            # Terminal / cancel-only classifications.
            applied = classifier.apply_decision(
                run=run,
                lease=claimed.lease,
                decision=decision,
                expected_revision=claimed.state_revision,
            )
            if applied is not None:
                logger.info(
                    "skeleton applied terminal run_id=%s status=%s rev=%s",
                    claimed.run_id,
                    applied.status,
                    applied.state_revision,
                )
                return

            # recovering -> running after successful classification.
            if str(run.status) == STATUS_RECOVERING and decision.kind in {
                "continue",
                "reuse_unit",
                "short_circuit",
            }:
                if not heartbeat():
                    logger.warning(
                        "lease lost before recovery_complete run_id=%s",
                        claimed.run_id,
                    )
                    return
                result = classifier.commit_recovery_complete(
                    run=run,
                    lease=claimed.lease,
                    decision=decision,
                    expected_revision=claimed.state_revision,
                )
                logger.info(
                    "skeleton recovery_complete run_id=%s status=%s rev=%s "
                    "short_circuit=%s",
                    claimed.run_id,
                    result.status,
                    result.state_revision,
                    decision.short_circuit_after_result,
                )
                # Task 6 plugs full execution after this point.
                return

            # Queued/running with continue — Task 6 executes the loop.
            logger.info(
                "skeleton defer_execution run_id=%s status=%s decision=%s "
                "(Task 6 owns Provider/Capability loop)",
                claimed.run_id,
                run.status,
                decision.kind,
            )
        except DurableRunConflict as exc:
            logger.info(
                "skeleton apply conflict run_id=%s code=%s",
                claimed.run_id,
                exc.code,
            )
        finally:
            db.close()


@dataclass
class AssistantWorkerConfig:
    """Runtime configuration for one assistant-worker process."""

    identity: WorkerIdentity
    poll_interval_ms: int
    lease_ttl_sec: int
    heartbeat_interval_sec: int
    registration_ttl_sec: int
    max_recovery_attempts: int
    retry_base_ms: int
    retry_max_ms: int
    state_path: Path = DEFAULT_WORKER_STATE_PATH
    expiry_scan_interval_sec: float = 5.0
    expiry_scan_batch_size: int = 50

    @classmethod
    def from_settings(
        cls,
        *,
        identity: WorkerIdentity | None = None,
        settings: Any | None = None,
        state_path: Path | None = None,
    ) -> AssistantWorkerConfig:
        s = settings or get_settings()
        # Validate heartbeat < lease_ttl / 3 at startup (settings also enforce).
        hb = int(s.assistant_worker_heartbeat_interval_sec)
        lease = int(s.assistant_worker_lease_ttl_sec)
        if hb * 3 >= lease:
            raise RuntimeError(
                "assistant_worker_heartbeat_interval_sec must be < "
                "assistant_worker_lease_ttl_sec / 3"
            )
        return cls(
            identity=identity or WorkerIdentity.from_settings(settings=s),
            poll_interval_ms=int(s.assistant_worker_poll_interval_ms),
            lease_ttl_sec=lease,
            heartbeat_interval_sec=hb,
            registration_ttl_sec=int(s.assistant_worker_registration_ttl_sec),
            max_recovery_attempts=int(s.assistant_worker_max_recovery_attempts),
            retry_base_ms=int(s.assistant_worker_retry_base_ms),
            retry_max_ms=int(s.assistant_worker_retry_max_ms),
            state_path=state_path or DEFAULT_WORKER_STATE_PATH,
            expiry_scan_interval_sec=float(
                s.assistant_interrupt_expiry_scan_interval_sec
            ),
            expiry_scan_batch_size=int(
                s.assistant_interrupt_expiry_scan_batch_size
            ),
        )


class AssistantWorker:
    """Poll/claim/recover loop for durable Main Agent Runs."""

    def __init__(
        self,
        cfg: AssistantWorkerConfig,
        *,
        session_factory: Callable[[], Any] | None = None,
        executor: RunExecutor | None = None,
        credential_resolver: CredentialResolver | None = None,
        artifact_object_exists: Callable[[str], bool] | None = None,
        expiry_scanner: Callable[..., Any] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.session_factory = session_factory or SessionLocal
        if executor is None:
            # Route Checkpoint v2 Workflow units without changing the Plan 06
            # provider executor's persistence ownership.
            from app.assistant.durable.runner import MainAgentRunExecutor
            from app.assistant.durable.unit_router import DurableRunUnitRouter
            from app.assistant.durable.workflow_executor import (
                DurableWorkflowUnitExecutor,
            )

            provider_executor = MainAgentRunExecutor(
                heartbeat_interval_sec=float(cfg.heartbeat_interval_sec),
                lease_ttl_sec=float(cfg.lease_ttl_sec),
            )
            executor = DurableRunUnitRouter(
                provider_executor=provider_executor,
                durable_executor=DurableWorkflowUnitExecutor(
                    provider_resume=provider_executor.resume_waiting,
                ),
            )
        self.executor = executor
        self.credential_resolver = credential_resolver or NoopCredentialResolver()
        self.artifact_object_exists = artifact_object_exists
        if expiry_scanner is None:
            from app.assistant.workflow.durable.interrupt_api import (
                scan_expired_interrupts,
            )

            expiry_scanner = scan_expired_interrupts
        self.expiry_scanner = expiry_scanner
        self._monotonic = monotonic or time.monotonic
        self._draining = False
        self._stop = Event()
        self._last_reg_heartbeat = 0.0
        self._last_lease_heartbeat: dict[str, float] = {}
        self._last_expiry_scan = float("-inf")

    @property
    def worker_id(self) -> str:
        return self.cfg.identity.worker_id

    @property
    def draining(self) -> bool:
        return self._draining

    def request_drain(self) -> None:
        """SIGTERM path: mark draining, stop claims, keep heartbeats."""
        self._draining = True
        logger.info("worker drain requested worker_id=%s", self.worker_id)
        db = self.session_factory()
        try:
            WorkerRegistry(db).mark_draining(self.worker_id)
            self._write_state(ok=True, draining=True)
        except Exception:
            logger.exception("mark_draining failed worker_id=%s", self.worker_id)
        finally:
            db.close()

    def request_stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> int:
        """Main loop until stop. Returns process exit code."""
        logger.info(
            "assistant worker starting worker_id=%s build=%s",
            self.worker_id,
            self.cfg.identity.app_build_revision,
        )
        registered = self._register()
        if registered != 0:
            return registered
        while not self._stop.is_set():
            try:
                self._maybe_registration_heartbeat()
                processed = self.run_once()
                if processed == 0:
                    self._stop.wait(self.cfg.poll_interval_ms / 1000.0)
            except Exception:
                logger.exception("worker run_once error")
                self._stop.wait(self.cfg.poll_interval_ms / 1000.0)
        logger.info("assistant worker stopped worker_id=%s", self.worker_id)
        return 0

    def run_once(self) -> int:
        """One claim/classify/execute cycle. Returns 1 if work was claimed."""
        self._maybe_scan_expired_interrupts()
        db = self.session_factory()
        try:
            if not self._schema_is_compatible(db):
                # Recheck before each claim loop; refuse claims on drift.
                logger.error("assistant_worker_schema_incompatible")
                self._write_state(
                    ok=False,
                    draining=self._draining,
                    reason="schema_incompatible",
                )
                return 0
            leases = RunLeaseService(
                db,
                identity=self.cfg.identity,
                lease_ttl=timedelta(seconds=self.cfg.lease_ttl_sec),
                retry_base_ms=self.cfg.retry_base_ms,
                retry_max_ms=self.cfg.retry_max_ms,
            )
            claimed = leases.claim_next(draining=self._draining)
            if claimed is None:
                return 0
        finally:
            db.close()

        return self._handle_claimed(claimed)

    def _maybe_scan_expired_interrupts(self) -> None:
        """Run bounded expiry housekeeping without gating existing Runs."""
        now = self._monotonic()
        if now - self._last_expiry_scan < self.cfg.expiry_scan_interval_sec:
            return
        self._last_expiry_scan = now
        db = self.session_factory()
        try:
            result = self.expiry_scanner(
                db,
                limit=self.cfg.expiry_scan_batch_size,
            )
            logger.debug("durable interrupt expiry scan result=%s", result)
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            logger.exception("durable interrupt expiry scan failed")
        finally:
            db.close()

    def _handle_claimed(self, claimed: ClaimedLease) -> int:
        """Classify + execute one claimed lease (fresh DB sessions)."""
        logger.info(
            "claimed run_id=%s kind=%s status=%s rev=%s gen=%s",
            claimed.run_id,
            claimed.kind,
            claimed.status,
            claimed.state_revision,
            claimed.lease.lease_generation,
        )

        db = self.session_factory()
        try:
            classifier = RecoveryClassifier(
                db,
                max_recovery_attempts=self.cfg.max_recovery_attempts,
                credential_resolver=self.credential_resolver,
                artifact_object_exists=self.artifact_object_exists,
            )
            repo = DurableRunRepository(db)
            run = repo.get_run(claimed.run_id)
            if run is None:
                return 1
            decision = classifier.classify(
                run=run,
                claim_kind=claimed.kind,
                worker_app_build_revision=self.cfg.identity.app_build_revision,
                worker_supported_codec_versions=(
                    self.cfg.identity.supported_checkpoint_codec_versions
                ),
            )
        finally:
            db.close()

        logger.info(
            "classified run_id=%s decision=%s reason=%s allow_provider=%s",
            claimed.run_id,
            decision.kind,
            decision.reason_code,
            decision.allow_provider_io,
        )

        def heartbeat() -> bool:
            return self._heartbeat_lease(claimed.lease)

        # Backoff: release lease + schedule next_attempt_at.
        if decision.kind == "backoff":
            self._apply_backoff(claimed, decision)
            return 1

        try:
            self.executor.execute(
                claimed=claimed,
                decision=decision,
                heartbeat=heartbeat,
                session_factory=self.session_factory,
            )
        except (TimeoutError, ConnectionError):
            # Release the claim through the existing bounded backoff protocol;
            # durable waiting resumes remain retryable on transient Provider I/O.
            from types import SimpleNamespace

            self._apply_backoff(
                claimed,
                SimpleNamespace(reason_code="provider_resume_transient"),
            )
        return 1

    def _apply_backoff(
        self, claimed: ClaimedLease, decision: RecoveryDecision
    ) -> None:
        db = self.session_factory()
        try:
            leases = RunLeaseService(
                db,
                identity=self.cfg.identity,
                lease_ttl=timedelta(seconds=self.cfg.lease_ttl_sec),
                retry_base_ms=self.cfg.retry_base_ms,
                retry_max_ms=self.cfg.retry_max_ms,
            )
            attempt = int(getattr(claimed.run, "recovery_count", 0) or 0)
            leases.schedule_backoff(
                lease=claimed.lease,
                expected_revision=claimed.state_revision,
                attempt=attempt,
                reason_code=decision.reason_code,
            )
        except DurableRunConflict as exc:
            logger.info(
                "backoff conflict run_id=%s code=%s", claimed.run_id, exc.code
            )
        finally:
            db.close()

    def _heartbeat_lease(self, lease: LeaseToken) -> bool:
        """Extend Run lease; False means lost lease (stop I/O)."""
        db = self.session_factory()
        try:
            leases = RunLeaseService(
                db,
                identity=self.cfg.identity,
                lease_ttl=timedelta(seconds=self.cfg.lease_ttl_sec),
            )
            ok = leases.heartbeat(lease)
            if not ok:
                logger.warning(
                    "lease lost run_id=%s gen=%s",
                    lease.run_id,
                    lease.lease_generation,
                )
            return ok
        finally:
            db.close()

    def _schema_is_compatible(self, db: Any) -> bool:
        """True when the live schema matches this build's interim Plan 2 head.

        Always returns a boolean — never raises raw SQL/Alembic errors to
        callers. Worker health reports only the stable reason
        ``schema_incompatible``.
        """
        try:
            from app.schema.compatibility import runtime_schema_compatibility

            return bool(runtime_schema_compatibility().is_compatible(db))
        except Exception:
            logger.exception("assistant_worker_schema_probe_failed")
            return False

    def _register(self) -> int:
        """Register this boot identity. Returns 0 on success, non-zero exit."""
        db = self.session_factory()
        try:
            if not self._schema_is_compatible(db):
                logger.error("assistant_worker_schema_incompatible")
                self._write_state(
                    ok=False, draining=False, reason="schema_incompatible"
                )
                return WORKER_SCHEMA_INCOMPATIBLE_EXIT
            reg = WorkerRegistry(db)
            row = reg.register(self.cfg.identity)
            self._last_reg_heartbeat = time.monotonic()
            self._write_state(ok=True, draining=False)
            logger.info(
                "worker registered worker_id=%s build=%s heartbeat_at=%s",
                row.worker_id,
                row.app_build_revision,
                row.heartbeat_at,
            )
            return 0
        finally:
            db.close()

    def _maybe_registration_heartbeat(self) -> None:
        interval = max(1, self.cfg.heartbeat_interval_sec)
        now = time.monotonic()
        if now - self._last_reg_heartbeat < interval:
            return
        db = self.session_factory()
        try:
            if not self._schema_is_compatible(db):
                logger.error("assistant_worker_schema_incompatible")
                self._write_state(
                    ok=False,
                    draining=self._draining,
                    reason="schema_incompatible",
                )
                return
            ok = WorkerRegistry(db).heartbeat(self.worker_id)
            self._last_reg_heartbeat = now
            if ok:
                self._write_state(ok=True, draining=self._draining)
            else:
                # Registration disappeared — re-register.
                logger.warning(
                    "registration missing; re-registering worker_id=%s",
                    self.worker_id,
                )
                WorkerRegistry(db).register(self.cfg.identity)
                self._write_state(ok=True, draining=self._draining)
        finally:
            db.close()

    def _write_state(
        self, *, ok: bool, draining: bool, reason: str | None = None
    ) -> None:
        """Persist worker state for Docker healthcheck validation."""
        payload: dict[str, Any] = {
            "ok": ok,
            "worker_id": self.worker_id,
            "app_build_revision": self.cfg.identity.app_build_revision,
            "runtime_contract_version": self.cfg.identity.runtime_contract_version,
            "supported_checkpoint_codec_versions": list(
                self.cfg.identity.supported_checkpoint_codec_versions
            ),
            "draining": draining,
            "written_at": utcnow().isoformat(),
        }
        if reason is not None:
            # Stable reason only — never a raw SQL/Alembic error string.
            payload["reason"] = str(reason)
        path = self.cfg.state_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.exception("failed to write worker state path=%s", path)


def run_healthcheck(*, state_path: Path | None = None) -> int:
    """Validate a fresh compatible registration (Docker HEALTHCHECK).

    Exit 0 only when:
    1. worker state file exists with worker_id + build;
    2. DB registration is fresh, non-draining, and build/codec compatible.
    """
    path = state_path or DEFAULT_WORKER_STATE_PATH
    if not path.is_file():
        print(json.dumps({"ok": False, "reason": "state_file_missing"}), flush=True)
        return 1
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            json.dumps({"ok": False, "reason": "state_file_invalid", "error": str(exc)}),
            flush=True,
        )
        return 1

    if state.get("ok") is not True:
        print(
            json.dumps({"ok": False, "reason": "state_file_unhealthy"}),
            flush=True,
        )
        return 1

    worker_id = str(state.get("worker_id") or "").strip()
    build = str(state.get("app_build_revision") or "").strip()
    if not worker_id or not build:
        print(
            json.dumps({"ok": False, "reason": "state_file_incomplete"}),
            flush=True,
        )
        return 1

    from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION

    settings = get_settings()
    ttl = timedelta(seconds=int(settings.assistant_worker_registration_ttl_sec))
    db = SessionLocal()
    try:
        result = WorkerRegistry(db).healthcheck(
            worker_id=worker_id,
            app_build_revision=build,
            runtime_contract_version=int(
                state.get("runtime_contract_version") or 1
            ),
            # Align process health with readiness/admission: current release codec.
            required_checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
            # Health must prove this worker matches the release capability
            # contract, never merely the digest it registered for itself.
            required_capability_feature_digest=default_capability_feature_digest(),
            registration_ttl=ttl,
        )
    finally:
        db.close()

    print(json.dumps(result), flush=True)
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MindAtlas durable assistant worker")
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Validate fresh compatible registration and exit",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help="Path to worker state JSON (default ASSISTANT_WORKER_STATE_PATH)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single claim cycle and exit (tests/debug)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.healthcheck:
        return run_healthcheck(state_path=args.state_path)

    cfg = AssistantWorkerConfig.from_settings(
        state_path=args.state_path or DEFAULT_WORKER_STATE_PATH
    )
    worker = AssistantWorker(cfg)

    def _handle_sigterm(signum: int, frame: Any) -> None:  # noqa: ARG001
        logger.info("received signal %s; draining", signum)
        worker.request_drain()
        # Give current unit a grace period via lease TTL; then stop loop.
        worker.request_stop()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    if args.once:
        registered = worker._register()
        if registered != 0:
            return registered
        worker.run_once()
        return 0
    return worker.run_forever()


if __name__ == "__main__":
    sys.exit(main())
