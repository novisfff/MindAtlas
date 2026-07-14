"""Sibling scheduling for one assistant multi-tool message (Plan 03 Task 5).

Pure planning groups Provider-order calls into sequential/parallel batches.
Execution never starts unsafe calls in parallel, never leaves unpaired tool
calls, and never reuses one run's surface/auth/session in another worker.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, Sequence

from app.assistant.capabilities.contracts import CapabilityDescriptor
from app.assistant.domain.contracts import ResolvedRunManifestRevision
from app.assistant.provider_loop.aliases import lookup_tool_by_alias
from app.assistant.provider_loop.contracts import (
    CancellationPort,
    ProviderToolSurface,
)
from app.assistant.provider_loop.messages import ProviderToolCall


PARALLEL_SIDE_EFFECTS = frozenset({"none", "compute", "read"})
DEFAULT_MAX_WORKERS = 4


@dataclass(frozen=True)
class DispatcherCapabilities:
    """What the injected dispatcher/executor stack can actually do."""

    supports_isolated_parallel: bool = False
    max_workers: int = DEFAULT_MAX_WORKERS

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("max_workers must be >= 1")


@dataclass(frozen=True)
class SiblingExecutionGroup:
    """One contiguous Provider-order batch with a single execution mode."""

    mode: Literal["sequential", "parallel"]
    calls: tuple[ProviderToolCall, ...]
    call_indexes: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.calls:
            raise ValueError("SiblingExecutionGroup requires at least one call")
        if len(self.calls) != len(self.call_indexes):
            raise ValueError("calls/call_indexes length mismatch")
        if self.mode == "parallel" and len(self.calls) < 1:
            raise ValueError("parallel group requires calls")
        if self.mode == "sequential" and len(self.calls) < 1:
            raise ValueError("sequential group requires calls")
        expected = tuple(call.call_index for call in self.calls)
        if expected != self.call_indexes:
            raise ValueError("call_indexes must match call.call_index order")


def is_parallel_eligible(
    descriptor: CapabilityDescriptor,
    *,
    dispatcher_capabilities: DispatcherCapabilities,
) -> bool:
    """Eligibility is permission only; it never forces threads by itself."""
    behavior = descriptor.behavior
    if not dispatcher_capabilities.supports_isolated_parallel:
        return False
    if descriptor.capability_type == "agent":
        return False
    if behavior.interrupt_mode != "none":
        return False
    if behavior.side_effect not in PARALLEL_SIDE_EFFECTS:
        return False
    if not behavior.parallel_safe:
        return False
    return True


def plan_sibling_execution(
    calls: tuple[ProviderToolCall, ...],
    *,
    surface: ProviderToolSurface,
    dispatcher_capabilities: DispatcherCapabilities,
    descriptors_by_alias: dict[str, CapabilityDescriptor] | None = None,
) -> tuple[SiblingExecutionGroup, ...]:
    """Plan maximal contiguous groups with identical parallel eligibility.

    Must be called only after a complete pre-plan classification verification
    pass. Stale ``parallel_safe=true`` is never used as scheduling permission
    because the caller re-describes every binding before invoking this planner.
    """
    if not isinstance(calls, tuple):
        raise TypeError("calls must be a tuple")
    if not calls:
        return ()

    for index, call in enumerate(calls):
        if call.call_index != index:
            raise ValueError("calls must be contiguous Provider order starting at 0")

    groups: list[SiblingExecutionGroup] = []
    current_mode: Literal["sequential", "parallel"] | None = None
    current_calls: list[ProviderToolCall] = []

    def flush() -> None:
        nonlocal current_mode, current_calls
        if not current_calls or current_mode is None:
            current_calls = []
            current_mode = None
            return
        batch = tuple(current_calls)
        groups.append(
            SiblingExecutionGroup(
                mode=current_mode,
                calls=batch,
                call_indexes=tuple(item.call_index for item in batch),
            )
        )
        current_calls = []
        current_mode = None

    for call in calls:
        if descriptors_by_alias is not None:
            descriptor = descriptors_by_alias.get(call.provider_alias)
            if descriptor is None:
                raise ValueError(f"missing descriptor for alias {call.provider_alias!r}")
        else:
            definition = lookup_tool_by_alias(surface, call.provider_alias)
            descriptor = definition.descriptor
        eligible = is_parallel_eligible(
            descriptor,
            dispatcher_capabilities=dispatcher_capabilities,
        )
        mode: Literal["sequential", "parallel"] = "parallel" if eligible else "sequential"
        if current_mode is None:
            current_mode = mode
            current_calls = [call]
            continue
        if mode == current_mode and mode == "parallel":
            current_calls.append(call)
            continue
        if mode == current_mode and mode == "sequential":
            # Contiguous unsafe calls stay one sequential group for ordered
            # Manifest chaining; they still execute one-at-a-time.
            current_calls.append(call)
            continue
        flush()
        current_mode = mode
        current_calls = [call]

    flush()
    return tuple(groups)


class SiblingSession(Protocol):
    """Minimal worker Session/context identity used by isolation tests."""

    session_id: str

    def close(self) -> None: ...


class IsolatedDispatcherFactory(Protocol):
    """Creates an independent dispatcher/Gateway context per worker call."""

    def open(self, *, call: ProviderToolCall, parent_session_id: str | None) -> tuple[Any, SiblingSession]:
        """Return ``(dispatcher, session)`` for one call. Must not reuse parent Session."""


@dataclass
class SequentialSiblingExecutor:
    """Execute items in Provider order on the calling thread."""

    def map_parallel(
        self,
        items: Sequence[Any],
        worker: Callable[[Any], Any],
        *,
        max_workers: int,
    ) -> list[Any]:
        del max_workers
        return [worker(item) for item in items]


@dataclass
class BoundedIsolatedSiblingExecutor:
    """Bounded thread-pool executor with per-call isolation requirements.

    Production/test runtime only enables this when an isolated dispatcher
    factory is supplied. Eligibility alone never forces threads.
    """

    max_workers: int = DEFAULT_MAX_WORKERS
    parent_session_id: str | None = None
    guard: Any | None = None
    _active_threads: set[int] = field(default_factory=set, init=False, repr=False)

    def map_parallel(
        self,
        items: Sequence[Any],
        worker: Callable[[Any], Any],
        *,
        max_workers: int,
    ) -> list[Any]:
        if not items:
            return []
        bound = max(1, min(max_workers, self.max_workers, len(items)))
        # Preserve input order in the returned list even if workers finish out of order.
        results: list[Any | None] = [None] * len(items)

        def _run(index: int, item: Any) -> tuple[int, Any]:
            import threading

            thread_id = threading.get_ident()
            self._active_threads.add(thread_id)
            try:
                if self.guard is not None:
                    self.guard.enter_worker(
                        parent_session_id=self.parent_session_id,
                        thread_id=thread_id,
                    )
                try:
                    value = worker(item)
                finally:
                    if self.guard is not None:
                        self.guard.exit_worker(thread_id=thread_id)
                return index, value
            finally:
                self._active_threads.discard(thread_id)

        with ThreadPoolExecutor(max_workers=bound) as pool:
            futures = {
                pool.submit(_run, index, item): index for index, item in enumerate(items)
            }
            for future in as_completed(futures):
                index, value = future.result()
                results[index] = value
        return list(results)


def merge_parallel_manifests(
    *,
    parent: ResolvedRunManifestRevision,
    children: Sequence[ResolvedRunManifestRevision],
) -> ResolvedRunManifestRevision:
    """Converge parallel sibling Manifest results.

    Unchanged results + one child, or multiple byte-identical children, converge.
    Conflicting different children are a protocol error (no last-writer-wins).
    """
    distinct: list[ResolvedRunManifestRevision] = []
    seen: set[str] = set()
    for child in children:
        if child.manifest_digest == parent.manifest_digest:
            continue
        if child.manifest_digest in seen:
            continue
        seen.add(child.manifest_digest)
        distinct.append(child)
    if not distinct:
        return parent
    if len(distinct) == 1:
        return distinct[0]
    raise ValueError("conflicting parallel sibling manifest children")


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "BoundedIsolatedSiblingExecutor",
    "DispatcherCapabilities",
    "IsolatedDispatcherFactory",
    "PARALLEL_SIDE_EFFECTS",
    "SequentialSiblingExecutor",
    "SiblingExecutionGroup",
    "SiblingSession",
    "is_parallel_eligible",
    "merge_parallel_manifests",
    "plan_sibling_execution",
]
