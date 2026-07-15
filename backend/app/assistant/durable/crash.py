"""Controlled crash / rollback inject points for Plan 06 Task 9 crash matrix.

Production code never arms injectors. Tests arm a process-local
:class:`CrashInjector` for a single kill point, drive the worker through the
boundary, and assert durable invariants on the committed state.

Kill points match Plan 06 Task 9:

1. after_prepare_before_started
2. after_started_before_adapter_io
3. after_provider_response_before_result
4. after_capability_result_before_result
5. after_skill_lineage_before_accept_commit
6. after_lifecycle_accept_commit_before_observe
7. after_manifest_artifact_upload_before_checkpoint
8. after_checkpoint_insert_before_pointer_advance  (transaction rollback inject)
9. after_final_message_before_memory_application
10. during_memory_computation_before_apply
11. during_heartbeat
12. after_stop_request_before_cancellation_seal
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from enum import Enum
from typing import Callable, Iterator


class CrashPoint(str, Enum):
    """Named crash inject points matching Plan 06 Task 9 kill matrix."""

    AFTER_PREPARE_BEFORE_STARTED = "after_prepare_before_started"
    AFTER_STARTED_BEFORE_ADAPTER_IO = "after_started_before_adapter_io"
    AFTER_PROVIDER_RESPONSE_BEFORE_RESULT = "after_provider_response_before_result"
    AFTER_CAPABILITY_RESULT_BEFORE_RESULT = "after_capability_result_before_result"
    AFTER_SKILL_LINEAGE_BEFORE_ACCEPT_COMMIT = "after_skill_lineage_before_accept_commit"
    AFTER_LIFECYCLE_ACCEPT_COMMIT_BEFORE_OBSERVE = (
        "after_lifecycle_accept_commit_before_observe"
    )
    AFTER_MANIFEST_ARTIFACT_UPLOAD_BEFORE_CHECKPOINT = (
        "after_manifest_artifact_upload_before_checkpoint"
    )
    AFTER_CHECKPOINT_INSERT_BEFORE_POINTER_ADVANCE = (
        "after_checkpoint_insert_before_pointer_advance"
    )
    AFTER_FINAL_MESSAGE_BEFORE_MEMORY_APPLICATION = (
        "after_final_message_before_memory_application"
    )
    DURING_MEMORY_COMPUTATION_BEFORE_APPLY = "during_memory_computation_before_apply"
    DURING_HEARTBEAT = "during_heartbeat"
    AFTER_STOP_REQUEST_BEFORE_CANCELLATION_SEAL = (
        "after_stop_request_before_cancellation_seal"
    )


class WorkerCrash(Exception):
    """Simulated process death after a committed boundary (no further work)."""

    def __init__(self, point: CrashPoint | str) -> None:
        self.point = CrashPoint(point) if not isinstance(point, CrashPoint) else point
        super().__init__(f"injected worker crash at {self.point.value}")


class TransactionRollbackInject(Exception):
    """Simulated mid-transaction failure; caller must roll back uncommitted work."""

    def __init__(self, point: CrashPoint | str) -> None:
        self.point = CrashPoint(point) if not isinstance(point, CrashPoint) else point
        super().__init__(f"injected transaction rollback at {self.point.value}")


_injector: contextvars.ContextVar["CrashInjector | None"] = contextvars.ContextVar(
    "durable_crash_injector", default=None
)
# Process-wide fallback for threads that do not inherit the contextvar (workers).
_process_injector_lock = threading.RLock()
_process_injector: "CrashInjector | None" = None


class CrashInjector:
    """Arm a single kill point; first hit raises and disarms."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._armed: CrashPoint | None = None
        self._hits: list[CrashPoint] = []
        self._handlers: dict[CrashPoint, Callable[[], None]] = {}

    def arm(self, point: CrashPoint | str) -> None:
        cp = CrashPoint(point) if not isinstance(point, CrashPoint) else point
        with self._lock:
            self._armed = cp
            self._hits.clear()

    def disarm(self) -> None:
        with self._lock:
            self._armed = None

    @property
    def armed(self) -> CrashPoint | None:
        with self._lock:
            return self._armed

    @property
    def hits(self) -> tuple[CrashPoint, ...]:
        with self._lock:
            return tuple(self._hits)

    def on(self, point: CrashPoint | str, handler: Callable[[], None]) -> None:
        """Optional custom handler; default raises :class:`WorkerCrash`."""
        cp = CrashPoint(point) if not isinstance(point, CrashPoint) else point
        with self._lock:
            self._handlers[cp] = handler

    def maybe_crash(self, point: CrashPoint | str) -> None:
        cp = CrashPoint(point) if not isinstance(point, CrashPoint) else point
        with self._lock:
            if self._armed is not cp:
                return
            self._hits.append(cp)
            self._armed = None  # one-shot
            handler = self._handlers.get(cp)
        if handler is not None:
            handler()
            return
        if cp is CrashPoint.AFTER_CHECKPOINT_INSERT_BEFORE_POINTER_ADVANCE:
            raise TransactionRollbackInject(cp)
        raise WorkerCrash(cp)


def get_injector() -> CrashInjector | None:
    local = _injector.get()
    if local is not None:
        return local
    with _process_injector_lock:
        return _process_injector


def set_injector(injector: CrashInjector | None) -> None:
    _injector.set(injector)
    with _process_injector_lock:
        global _process_injector
        _process_injector = injector


def maybe_crash(point: CrashPoint | str) -> None:
    """No-op when no injector is armed; otherwise fire the armed kill point."""
    inj = get_injector()
    if inj is None:
        return
    inj.maybe_crash(point)


@contextmanager
def armed_crash(point: CrashPoint | str) -> Iterator[CrashInjector]:
    """Context manager: arm one kill point for the duration of the block."""
    inj = CrashInjector()
    inj.arm(point)
    token = _injector.set(inj)
    with _process_injector_lock:
        global _process_injector
        previous = _process_injector
        _process_injector = inj
    try:
        yield inj
    finally:
        inj.disarm()
        _injector.reset(token)
        with _process_injector_lock:
            _process_injector = previous


__all__ = [
    "CrashInjector",
    "CrashPoint",
    "TransactionRollbackInject",
    "WorkerCrash",
    "armed_crash",
    "get_injector",
    "maybe_crash",
    "set_injector",
]
