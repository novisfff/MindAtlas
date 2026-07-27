from __future__ import annotations

from typing import Any, Callable
from uuid import UUID


def attach_human_loop_runtime(
    *,
    db: Any,
    db_bind: Any,
    metadata: dict[str, Any],
    run_id: str,
    channel_type: str,
    conversation_id_uuid: UUID | None,
    workflow_id_uuid: UUID | None,
    skill_id_uuid: UUID | None,
    message_id_uuid: UUID | None,
    emit: Callable[..., None],
    cancel_checker: Callable[[], bool] | None = None,
) -> None:
    """No-op: legacy blocking HumanLoop is removed (Plan 10 B2).

    Durable Main Agent / Plan 07 interrupts own production HITL. Capability
    scopes and workflow-test paths must not attach a blocking runtime.
    """
    _ = (
        db,
        db_bind,
        run_id,
        channel_type,
        conversation_id_uuid,
        workflow_id_uuid,
        skill_id_uuid,
        message_id_uuid,
        emit,
        cancel_checker,
    )
    # Explicitly clear any prior attachment.
    if isinstance(metadata, dict):
        metadata.pop("human_loop_runtime", None)
    return None
