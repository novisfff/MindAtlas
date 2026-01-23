"""Daemon runtime for KB prefetch.

KB retrieval (LightRAG/Neo4j/network) may hang in rare cases. If we run it inline
in the request thread, the whole chat SSE can stall and even make shutdown
unresponsive.

This module provides a daemon worker thread that executes KB prefetch jobs.
Callers wait with a hard timeout and fail-open on timeout.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from queue import Queue
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _Job:
    __slots__ = ("fn", "future")

    def __init__(self, fn: Callable[[], T], future: Future):
        self.fn = fn
        self.future = future


class KbPrefetchRuntime:
    def __init__(self) -> None:
        self._jobs: "Queue[_Job | None]" = Queue()
        self._thread = threading.Thread(target=self._run, name="kb-prefetch", daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def call(self, fn: Callable[[], T], *, timeout_sec: float) -> T:
        fut: Future = Future()
        self._jobs.put(_Job(fn, fut))
        return fut.result(timeout=timeout_sec)

    def _run(self) -> None:
        logger.info("kb prefetch runtime ready", extra={"kb_thread": threading.current_thread().name})
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                res = job.fn()
            except BaseException as exc:  # noqa: BLE001
                job.future.set_exception(exc)
            else:
                job.future.set_result(res)


_RUNTIME: KbPrefetchRuntime | None = None
_LOCK = threading.Lock()


def _get_runtime() -> KbPrefetchRuntime:
    global _RUNTIME
    if _RUNTIME is not None and _RUNTIME.is_alive():
        return _RUNTIME
    with _LOCK:
        if _RUNTIME is None or not _RUNTIME.is_alive():
            _RUNTIME = KbPrefetchRuntime()
        return _RUNTIME


def call_kb_prefetch(fn: Callable[[], T], *, timeout_sec: float) -> T:
    """Execute a KB prefetch job with a hard timeout.

    If the current runtime thread is wedged, the timeout protects the caller.
    """
    runtime = _get_runtime()
    try:
        return runtime.call(fn, timeout_sec=timeout_sec)
    except FutureTimeoutError:
        # Best-effort: if the worker got stuck, create a new daemon runtime for next calls.
        global _RUNTIME
        with _LOCK:
            _RUNTIME = None
        raise

