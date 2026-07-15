"""Atomic base materialization for durable Main Agent Runs (Plan 06 Task 6).

Materializes Manifest/policy/budget/obligation/Provider transcript and the first
Checkpoint in one CAS transaction after claim (status already running).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.durable.codec import (
    checkpoint_state_digest,
    encode_checkpoint_v1,
    encode_provider_message,
)
from app.assistant.durable.contracts import (
    DurableAgentCheckpointV1,
    DurableNextActionV1,
)
from app.assistant.durable.models import (
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunManifestRevision,
    AssistantRunObligationRevision,
    AssistantRunPolicyRevision,
    AssistantRunProviderMessage,
)
from app.assistant.durable.repository import (
    DurableChildBundle,
    DurableCommitResult,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
)
from app.assistant.provider_loop.messages import (
    ProviderMessage,
    digest_provider_message,
    digest_provider_transcript,
)

logger = logging.getLogger(__name__)

_PROTECTED_ROLES = frozenset(
    {"runtime_instruction", "runtime_context", "runtime_completion"}
)


def materialize_base_run_state(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    manifest_payload: Mapping[str, Any],
    manifest_digest: str,
    policy_payload: Mapping[str, Any],
    policy_digest: str,
    budget_payload: Mapping[str, Any],
    budget_digest: str,
    obligation_payload: Mapping[str, Any],
    obligation_digest: str,
    provider_messages: Sequence[ProviderMessage] = (),
    protection_kinds: Sequence[str] | None = None,
    policy_revision_for_messages: bool = False,
    phase: str = "ready_for_provider",
    next_action_kind: str = "continue_provider",
) -> DurableCommitResult:
    """Atomically append base revisions + transcript + first Checkpoint.

    Caller must already hold a running lease (post-claim). No adapter I/O.
    """
    from app.assistant.models import AssistantChatRun

    run = db.get(AssistantChatRun, run_id)
    if run is None:
        raise DurableRunConflict("run_not_found", f"run not found: {run_id}")
    if str(run.runtime_kind) != "main_agent":
        raise DurableRunConflict(
            "not_main_agent",
            f"runtime_kind={run.runtime_kind} is not main_agent",
            run=run,
        )
    if run.current_checkpoint_id is not None:
        raise DurableRunConflict(
            "protocol_error",
            "base state already materialized",
            run=run,
        )

    manifest = AssistantRunManifestRevision(
        id=uuid4(),
        run_id=run_id,
        revision=1,
        parent_revision_id=None,
        parent_digest=None,
        manifest_digest=manifest_digest,
        schema_version=1,
        payload=dict(manifest_payload),
    )
    policy = AssistantRunPolicyRevision(
        id=uuid4(),
        run_id=run_id,
        revision=1,
        parent_revision_id=None,
        parent_digest=None,
        policy_digest=policy_digest,
        payload=dict(policy_payload),
    )
    budget = AssistantRunBudgetRevision(
        id=uuid4(),
        run_id=run_id,
        revision=1,
        parent_revision_id=None,
        parent_digest=None,
        budget_digest=budget_digest,
        payload=dict(budget_payload),
    )
    obligation = AssistantRunObligationRevision(
        id=uuid4(),
        run_id=run_id,
        revision=1,
        parent_revision_id=None,
        parent_digest=None,
        obligation_digest=obligation_digest,
        payload=dict(obligation_payload),
    )
    db.add_all([manifest, policy, budget, obligation])
    db.flush()

    msg_rows: list[AssistantRunProviderMessage] = []
    messages = tuple(provider_messages or ())
    for idx, msg in enumerate(messages):
        role = str(getattr(msg, "role", "") or "")
        if protection_kinds is not None and idx < len(protection_kinds):
            protection = protection_kinds[idx]
        else:
            protection = "protected" if role in _PROTECTED_ROLES else "public"
        body = encode_provider_message(msg)
        content_digest = digest_provider_message(msg)
        policy_id = None
        obl_id = None
        if protection == "protected" or role in _PROTECTED_ROLES:
            policy_id = policy.id
        if role == "runtime_completion":
            obl_id = obligation.id
        # When policy_revision_for_messages, attach policy to protected rows.
        if policy_revision_for_messages and role in _PROTECTED_ROLES:
            policy_id = policy.id
        msg_rows.append(
            AssistantRunProviderMessage(
                id=uuid4(),
                run_id=run_id,
                ordinal=idx + 1,
                provider_round=0,
                role=role,
                payload_version=1,
                payload_discriminator=role if role in _PROTECTED_ROLES else None,
                payload_body=body,
                protection_kind=protection,
                content_digest=content_digest,
                manifest_revision_id=manifest.id,
                policy_revision_id=policy_id,
                obligation_revision_id=obl_id,
            )
        )
    for row in msg_rows:
        db.add(row)
    if msg_rows:
        db.flush()

    ordinal = len(messages)
    transcript_digest = digest_provider_transcript(messages)
    checkpoint = DurableAgentCheckpointV1(
        run_id=run_id,
        phase=phase,  # type: ignore[arg-type]
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        provider_loop_continuation=None,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV1(kind=next_action_kind),  # type: ignore[arg-type]
    )
    state_payload = encode_checkpoint_v1(checkpoint)
    state_digest = checkpoint_state_digest(checkpoint)
    ck = AssistantRunCheckpoint(
        id=uuid4(),
        run_id=run_id,
        sequence=1,
        expected_state_revision=int(expected_revision),
        committed_state_revision=int(expected_revision) + 1,
        schema_version=1,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        phase=phase,
        logical_unit_id=None,
        reason="base_materialized",
        state_payload=state_payload,
        state_digest=state_digest,
    )

    bundle = DurableChildBundle(
        rows=[ck],
        current_manifest_revision_id=manifest.id,
        current_policy_revision_id=policy.id,
        current_budget_revision_id=budget.id,
        current_obligation_revision_id=obligation.id,
        current_checkpoint_id=ck.id,
    )
    repo = DurableRunRepository(db)
    return repo.commit_semantic(
        run_id=run_id,
        expected_revision=expected_revision,
        lease=lease,
        events=[
            EventSpec(
                event_key=f"run.base_materialized:rev{expected_revision}",
                event_name="run.base_materialized",
                payload={
                    "manifestDigest": manifest_digest,
                    "policyDigest": policy_digest,
                    "providerMessageCount": ordinal,
                },
                visibility="internal",
            )
        ],
        children=bundle,
    )


__all__ = ["materialize_base_run_state"]
