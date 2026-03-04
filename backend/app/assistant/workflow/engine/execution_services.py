from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from app.assistant.workflow.human_approval_runtime import HumanLoopContext, HumanLoopRuntime


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
    emit: Callable[[dict[str, Any], str], None] | Callable[..., None],
    cancel_checker: Callable[[], bool] | None = None,
) -> HumanLoopRuntime | None:
    if db is None:
        return None

    session_factory = sessionmaker(bind=db_bind)
    human_loop_runtime = HumanLoopRuntime(
        session_factory,
        context=HumanLoopContext(
            run_id=run_id,
            channel_type=channel_type,
            conversation_id=conversation_id_uuid,
            workflow_id=workflow_id_uuid,
            skill_id=skill_id_uuid,
            message_id=message_id_uuid,
        ),
        on_requested=(lambda payload: emit(metadata, "on_human_approval_requested", payload=payload)),
        on_resolved=(lambda payload: emit(metadata, "on_human_approval_resolved", payload=payload)),
        cancel_checker=cancel_checker,
    )
    metadata["human_loop_runtime"] = human_loop_runtime
    return human_loop_runtime
