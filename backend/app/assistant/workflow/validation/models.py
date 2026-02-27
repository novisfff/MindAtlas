from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationError:
    node_id: str | None
    message: str


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
