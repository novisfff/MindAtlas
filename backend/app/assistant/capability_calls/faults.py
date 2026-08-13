"""Explicit test-only fault injection for the local create_entry boundary.

The port is never populated from settings, HTTP input, Provider arguments, or
dynamic imports. Production construction leaves it as ``None``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Literal

CreateEntryFaultPoint = Literal[
    "before_proposal",
    "after_proposal",
    "before_approval_decision",
    "after_approval_decision",
    "after_entry_stage_before_commit",
    "after_commit_before_ack",
    "after_commit_before_checkpoint_observation",
]

CREATE_ENTRY_FAULT_POINTS = frozenset(
    {
        "before_proposal",
        "after_proposal",
        "before_approval_decision",
        "after_approval_decision",
        "after_entry_stage_before_commit",
        "after_commit_before_ack",
        "after_commit_before_checkpoint_observation",
    }
)


class CapabilityInjectedFault(RuntimeError):
    """A deterministic test failure at one named ledger boundary."""

    def __init__(self, point: str) -> None:
        if point not in CREATE_ENTRY_FAULT_POINTS:
            raise ValueError(f"unsupported create_entry fault point: {point!r}")
        self.point = point
        super().__init__(f"injected capability fault at {point}")


@dataclass
class CapabilityFaultPort:
    """One-shot named fault port supplied directly by test factories."""

    points: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        invalid = set(self.points) - set(CREATE_ENTRY_FAULT_POINTS)
        if invalid:
            raise ValueError(f"unsupported create_entry fault point: {sorted(invalid)!r}")

    @classmethod
    def once(cls, point: CreateEntryFaultPoint) -> "CapabilityFaultPort":
        return cls(Counter({str(point): 1}))

    @classmethod
    def many(cls, points: Iterable[CreateEntryFaultPoint]) -> "CapabilityFaultPort":
        return cls(Counter(str(point) for point in points))

    def hit(self, point: CreateEntryFaultPoint) -> None:
        if point not in CREATE_ENTRY_FAULT_POINTS:
            raise ValueError(f"unsupported create_entry fault point: {point!r}")
        remaining = int(self.points.get(point, 0))
        if remaining <= 0:
            return
        if remaining == 1:
            self.points.pop(point, None)
        else:
            self.points[point] = remaining - 1
        raise CapabilityInjectedFault(point)


__all__ = [
    "CapabilityFaultPort",
    "CapabilityInjectedFault",
    "CREATE_ENTRY_FAULT_POINTS",
    "CreateEntryFaultPoint",
]
