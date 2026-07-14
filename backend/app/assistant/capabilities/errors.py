"""Safe capability domain errors and unexpected-exception sanitization."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.assistant.capabilities.contracts import (
    MAX_SAFE_MESSAGE_LEN,
    CapabilityError,
    CapabilityValidationIssue,
)

logger = logging.getLogger(__name__)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def _bounded_safe_text(value: str, *, fallback: str) -> str:
    text = value if isinstance(value, str) else fallback
    text = _CONTROL_RE.sub("", text).strip()
    if not text:
        text = fallback
    if len(text) > MAX_SAFE_MESSAGE_LEN:
        text = text[:MAX_SAFE_MESSAGE_LEN]
    return text


class CapabilityDomainError(Exception):
    """Process-local exception carrying a frozen CapabilityError payload."""

    def __init__(self, error: CapabilityError) -> None:
        self.error = error
        super().__init__(error.safe_code)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"CapabilityDomainError({self.error.safe_code})"

    def __repr__(self) -> str:
        return (
            f"CapabilityDomainError(safe_code={self.error.safe_code!r}, "
            f"error_type={self.error.error_type!r})"
        )


class CapabilitySchemaValidationError(CapabilityDomainError):
    """Raised when input/output JSON fails the compiled binding schema."""

    def __init__(
        self,
        *,
        label: str,
        issues: tuple[CapabilityValidationIssue, ...],
    ) -> None:
        error_type = "invalid_input" if label == "input" else "invalid_output"
        safe_message = _bounded_safe_text(
            f"{label} failed schema validation",
            fallback="schema validation failed",
        )
        error = CapabilityError(
            error_type=error_type,  # type: ignore[arg-type]
            safe_code=error_type,
            safe_message=safe_message,
            retry_disposition="never",
            validation_issues=issues,
        )
        self.label = label
        self.issues = issues
        super().__init__(error)

    def __repr__(self) -> str:
        return (
            f"CapabilitySchemaValidationError(label={self.label!r}, "
            f"issue_count={len(self.issues)})"
        )


def sanitize_unexpected_exception(
    exc: BaseException,
    *,
    call_id: str | None,
    target_identity: str | None,
    stage: str,
) -> CapabilityError:
    """Map an untrusted exception to a safe CapabilityError without leaking secrets.

    Logs only stable identifiers and the exception class name. Never logs
    ``str(exc)``, ``repr(exc)``, args, or traceback/exc_info.
    """
    exc_class = type(exc).__name__
    safe_stage = _bounded_safe_text(stage, fallback="unknown")
    logger.error(
        "capability_unexpected_error call_id=%s target_identity=%s stage=%s exc_class=%s",
        call_id or "-",
        target_identity or "-",
        safe_stage,
        exc_class,
    )
    return CapabilityError(
        error_type="execution_failed",
        safe_code="execution_failed",
        safe_message="capability execution failed",
        retry_disposition="never",
        target_identity=target_identity,
        call_id=call_id,
        validation_issues=(),
    )


__all__ = [
    "CapabilityDomainError",
    "CapabilitySchemaValidationError",
    "sanitize_unexpected_exception",
]
