"""Query cache and concurrency primitives for LightRAG service."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

import anyio

from app.lightrag.schemas import LightRagQueryMode, LightRagQueryResponse


@dataclass(frozen=True)
class CacheEntry:
    value: LightRagQueryResponse
    expires_at: float


class QueryCache:
    def __init__(self) -> None:
        self._data: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, *, now: float) -> LightRagQueryResponse | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return entry.value

    def set(self, key: str, value: LightRagQueryResponse, *, now: float, ttl_sec: int, maxsize: int) -> None:
        if ttl_sec <= 0 or maxsize <= 0:
            return
        with self._lock:
            self._data[key] = CacheEntry(value=value, expires_at=now + float(ttl_sec))
            self._data.move_to_end(key)
            while len(self._data) > maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def invalidate_by_prefix(self, prefix: str) -> None:
        with self._lock:
            if not prefix:
                self._data.clear()
                return
            to_delete = [k for k in self._data if k.startswith(prefix)]
            for key in to_delete:
                self._data.pop(key, None)

    def invalidate_all(self) -> None:
        self.clear()


QUERY_CACHE = QueryCache()

QUERY_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}
QUERY_SEMAPHORES_LOCK = threading.Lock()


def get_query_semaphore(max_concurrency: int) -> threading.BoundedSemaphore:
    n = max(1, int(max_concurrency or 1))
    with QUERY_SEMAPHORES_LOCK:
        sem = QUERY_SEMAPHORES.get(n)
        if sem is None:
            sem = threading.BoundedSemaphore(n)
            QUERY_SEMAPHORES[n] = sem
        return sem


async def acquire_query_semaphore(sem: threading.BoundedSemaphore, *, timeout_sec: float) -> bool:
    t = float(timeout_sec or 0.0)
    if t <= 0:
        return await anyio.to_thread.run_sync(lambda: sem.acquire(blocking=False), abandon_on_cancel=True)
    return await anyio.to_thread.run_sync(lambda: sem.acquire(timeout=t), abandon_on_cancel=True)


def reset_query_state_for_tests() -> None:
    """Best-effort test hook to clear in-process caches."""
    try:
        with QUERY_SEMAPHORES_LOCK:
            QUERY_SEMAPHORES.clear()
    except Exception:
        pass
    try:
        QUERY_CACHE.clear()
    except Exception:
        pass


def hash_for_cache(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_cache_key(*, query: str, mode: LightRagQueryMode, top_k: int) -> str:
    # Keep key stable and safe: do not include secrets.
    q = (query or "").strip()
    return f"m={mode}|k={top_k}|ql={len(q)}|qh={hash_for_cache(q)}"
