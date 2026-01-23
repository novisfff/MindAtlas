"""LightRAG runtime execution context.

LightRAG (and its Neo4j storage) uses asyncio internally. If the application
creates a new event loop per request/thread (e.g. via asyncio.run/new_event_loop),
async resources (Futures, tasks, drivers) may get bound to different loops and
cause hangs or errors like:
  "got Future attached to a different loop".

This module provides a process-wide dedicated thread that owns a single asyncio
event loop. All LightRAG operations that might touch asyncio should be executed
inside that thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from queue import Queue
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _Job:
    __slots__ = ("fn", "future")

    def __init__(self, fn: Callable[[], Any], future: Future):
        self.fn = fn
        self.future = future


class LightRagRuntime:
    def __init__(self) -> None:
        self._jobs: "Queue[_Job | None]" = Queue()
        self._thread = threading.Thread(target=self._run, name="lightrag-runtime", daemon=True)
        self._loop_ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread.start()
        self._loop_ready.wait(timeout=10.0)

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("LightRagRuntime loop is not initialized")
        return self._loop

    def in_runtime_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def call(self, fn: Callable[[], T], *, timeout_sec: float | None = None) -> T:
        """Run a callable inside the LightRAG runtime thread.

        If called from within the runtime thread, executes inline.
        """
        if self.in_runtime_thread():
            return fn()

        fut: Future = Future()
        self._jobs.put(_Job(fn, fut))
        return fut.result(timeout=timeout_sec)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            # "thread" is a reserved LogRecord attribute; don't overwrite it via extra.
            logger.info("lightrag runtime loop ready", extra={"lightrag_thread": threading.current_thread().name})
        except Exception:
            logger.info("lightrag runtime loop ready")

        while True:
            job = self._jobs.get()
            if job is None:
                break
            try:
                result = job.fn()
            except BaseException as exc:  # noqa: BLE001
                job.future.set_exception(exc)
            else:
                job.future.set_result(result)


_RUNTIME: LightRagRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def get_lightrag_runtime() -> LightRagRuntime:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = LightRagRuntime()
        return _RUNTIME
