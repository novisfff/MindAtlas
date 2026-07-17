"""Route claimed durable Runs to the executor that owns the persisted action."""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from app.assistant.durable.reconstruction import load_current_checkpoint


_DURABLE_WORKFLOW_ACTIONS = frozenset(
    {
        "continue_child",
        "resume_child",
        "resume_provider_loop",
        "expire_or_cancel_child",
    }
)


def _load_checkpoint_or_none(db: Any, run_id: UUID) -> Any | None:
    from app.assistant.models import AssistantChatRun

    run = db.get(AssistantChatRun, run_id)
    if run is None or run.current_checkpoint_id is None:
        return None
    return load_current_checkpoint(db, run_id=run_id)


class DurableRunUnitRouter:
    """Dispatch one claimed Run without changing its persistence semantics."""

    def __init__(
        self,
        *,
        provider_executor: Any,
        durable_executor: Any,
        checkpoint_reader: Callable[[Any, UUID], Any | None] | None = None,
    ) -> None:
        self.provider_executor = provider_executor
        self.durable_executor = durable_executor
        self.checkpoint_reader = checkpoint_reader or _load_checkpoint_or_none

    def execute(
        self,
        *,
        claimed: Any,
        decision: Any,
        heartbeat: Callable[[], bool],
        session_factory: Callable[[], Any],
    ) -> None:
        if str(getattr(decision, "kind", "")) in {
            "cancel_only",
            "fail",
            "exhausted",
            "needs_reconciliation",
        }:
            self.provider_executor.execute(
                claimed=claimed,
                decision=decision,
                heartbeat=heartbeat,
                session_factory=session_factory,
            )
            return
        db = session_factory()
        try:
            checkpoint = self.checkpoint_reader(db, claimed.run_id)
        finally:
            db.close()

        action = str(
            getattr(getattr(checkpoint, "next_action", None), "kind", "") or ""
        )
        schema_version = int(getattr(checkpoint, "schema_version", 0) or 0)
        executor = (
            self.durable_executor
            if schema_version == 2 and action in _DURABLE_WORKFLOW_ACTIONS
            else self.provider_executor
        )
        executor.execute(
            claimed=claimed,
            decision=decision,
            heartbeat=heartbeat,
            session_factory=session_factory,
        )


__all__ = ["DurableRunUnitRouter"]
