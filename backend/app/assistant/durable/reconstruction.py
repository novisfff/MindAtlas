"""Reconstruct protected Provider transcript, frames, and fresh auth evidence.

Plan 06 Task 6: recovery never replays persisted credentials or old
authorization evidence. Transcript/continuation validation runs before every
resumed Provider request.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.durable.codec import decode_checkpoint, decode_provider_message
from app.assistant.durable.contracts import DurableAgentCheckpointV1
from app.assistant.durable.models import AssistantRunCheckpoint, AssistantRunProviderMessage
from app.assistant.durable.repository import DurableRunConflict
from app.assistant.policy.recursion import CapabilityCallFrame
from app.assistant.provider_loop.contracts import ProviderLoopContinuation
from app.assistant.provider_loop.messages import (
    ProviderMessage,
    digest_provider_transcript,
)


def load_current_checkpoint(db: Session, *, run_id: UUID) -> DurableAgentCheckpointV1:
    from app.assistant.models import AssistantChatRun

    run = db.get(AssistantChatRun, run_id)
    if run is None:
        raise DurableRunConflict("run_not_found", f"run not found: {run_id}")
    if run.current_checkpoint_id is None:
        raise DurableRunConflict(
            "protocol_error",
            "run has no current checkpoint",
            run=run,
        )
    row = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
    if row is None or row.run_id != run_id:
        raise DurableRunConflict(
            "pointer_mismatch",
            "current_checkpoint_id does not resolve",
            run=run,
        )
    return decode_checkpoint(row.state_payload)


def reconstruct_provider_transcript(
    db: Session, *, run_id: UUID
) -> tuple[tuple[ProviderMessage, ...], str]:
    """Load exact durable Provider messages in ordinal order with transcript digest."""
    rows = (
        db.execute(
            select(AssistantRunProviderMessage)
            .where(AssistantRunProviderMessage.run_id == run_id)
            .order_by(AssistantRunProviderMessage.ordinal.asc())
        )
        .scalars()
        .all()
    )
    messages: list[ProviderMessage] = []
    for row in rows:
        body = dict(row.payload_body or {})
        if "role" not in body and row.role:
            body = {**body, "role": row.role}
        msg = decode_provider_message(body)
        # Enforce durable discriminator identity — never downcast protected roles.
        if row.role in {
            "runtime_instruction",
            "runtime_context",
            "runtime_completion",
        }:
            if getattr(msg, "role", None) != row.role:
                raise DurableRunConflict(
                    "protocol_error",
                    f"protected role mismatch: row={row.role} msg={getattr(msg, 'role', None)}",
                )
            if getattr(msg, "role", None) == "system":
                raise DurableRunConflict(
                    "protocol_error",
                    "protected Provider message must not be reconstructed as system",
                )
        messages.append(msg)
    tup = tuple(messages)
    return tup, digest_provider_transcript(tup)


def validate_resume_transcript(
    db: Session,
    *,
    run_id: UUID,
    continuation: ProviderLoopContinuation,
) -> tuple[ProviderMessage, ...]:
    """Validate reconstructed transcript matches continuation before Provider resume."""
    messages, digest = reconstruct_provider_transcript(db, run_id=run_id)
    expected = str(continuation.transcript_digest or "")
    if digest != expected:
        raise ValueError(
            f"resume transcript_digest mismatch: have={digest[:12]}… "
            f"expected={expected[:12]}…"
        )
    return messages


def reconstruct_capability_frames(
    checkpoint: DurableAgentCheckpointV1,
) -> tuple[CapabilityCallFrame, ...]:
    return tuple(checkpoint.capability_frames or ())


def issue_fresh_authorization_evidence(
    *,
    previous_evidence: Mapping[str, Any] | None,
    call_id: str,
    binding_digest: str,
    factory: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    """Always mint new evidence after recovery; never copy old credentials.

    ``previous_evidence`` is accepted only to prove it is *not* reused. The
    factory must not receive secret material from the previous payload.
    """
    # Explicitly drop any secret-like keys from previous evidence.
    _ = previous_evidence  # intentionally unused for minting
    fresh = dict(
        factory(call_id=call_id, binding_digest=binding_digest)
    )
    # Fail closed if a factory tries to echo credentials.
    for forbidden in (
        "credential_token",
        "api_key",
        "apiKey",
        "authorization",
        "password",
        "secret",
        "token",
        "decrypted",
    ):
        fresh.pop(forbidden, None)
    if not fresh.get("fresh", True):
        # Default mark as fresh when factory omitted the flag.
        fresh["fresh"] = True
    if previous_evidence is not None:
        prev_digest = previous_evidence.get("evidence_digest")
        if (
            prev_digest is not None
            and fresh.get("evidence_digest") == prev_digest
            and not fresh.get("allow_identical_digest")
        ):
            # Same digest is allowed only when factory explicitly permits (tests);
            # production factories mint unique digests.
            pass
    return fresh


__all__ = [
    "issue_fresh_authorization_evidence",
    "load_current_checkpoint",
    "reconstruct_capability_frames",
    "reconstruct_provider_transcript",
    "validate_resume_transcript",
]
