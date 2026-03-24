from __future__ import annotations

from typing import Callable


class AssistantRunCancelled(RuntimeError):
    """Raised when assistant run is cancelled by user."""


def ensure_not_cancelled(
    cancel_checker: Callable[[], bool] | None,
    *,
    message: str = "assistant run cancelled",
) -> None:
    if callable(cancel_checker) and bool(cancel_checker()):
        raise AssistantRunCancelled(message)
