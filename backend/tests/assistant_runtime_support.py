"""Shared Main-Agent-only Run fixture helpers (Plan 2 Task 9).

Surviving tests that insert ``AssistantChatRun`` or call
``AssistantChatRunService.create_run`` must pass the complete frozen runtime
closure fields produced here — never ``runtime_kind`` selection and never a
Legacy default.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
from app.assistant.durable.worker_registry import (
    RUNTIME_CONTRACT_VERSION,
    default_capability_feature_digest,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64


@dataclass(frozen=True)
class FrozenRuntimeFields:
    main_agent_rollout_revision_id: UUID
    main_agent_profile_version_id: UUID
    resolved_model_id: UUID
    runtime_closure_digest: str
    runtime_contract_version: int
    required_checkpoint_codec_version: int
    required_capability_feature_digest: str
    required_app_build_revision: str


def frozen_runtime_fields(
    *,
    rollout_revision_id: UUID,
    profile_version_id: UUID,
    model_id: UUID,
    build_revision: str = "test-build",
) -> FrozenRuntimeFields:
    feature = default_capability_feature_digest()
    closure_digest = sha256_canonical_json(
        {
            "rolloutRevisionId": str(rollout_revision_id),
            "profileVersionId": str(profile_version_id),
            "modelId": str(model_id),
            "buildRevision": build_revision,
            "featureDigest": feature,
        }
    )
    return FrozenRuntimeFields(
        main_agent_rollout_revision_id=rollout_revision_id,
        main_agent_profile_version_id=profile_version_id,
        resolved_model_id=model_id,
        runtime_closure_digest=closure_digest,
        runtime_contract_version=RUNTIME_CONTRACT_VERSION,
        required_checkpoint_codec_version=int(CURRENT_CHECKPOINT_CODEC_VERSION),
        required_capability_feature_digest=feature,
        required_app_build_revision=build_revision,
    )


def frozen_runtime_kwargs(
    *,
    rollout_revision_id: UUID,
    profile_version_id: UUID,
    model_id: UUID,
    build_revision: str = "test-build",
    capability_ledger_mode: str = "enforced",
    commit: bool = True,
) -> dict[str, Any]:
    """Ready-to-splat kwargs for ``AssistantChatRunService.create_run``."""
    fields = frozen_runtime_fields(
        rollout_revision_id=rollout_revision_id,
        profile_version_id=profile_version_id,
        model_id=model_id,
        build_revision=build_revision,
    )
    payload = asdict(fields)
    payload["capability_ledger_mode"] = capability_ledger_mode
    payload["commit"] = commit
    return payload


@dataclass(frozen=True)
class SeededMainAgentRuntime:
    """IDs + complete frozen field dict for direct ``AssistantChatRun`` inserts."""

    rollout_revision_id: UUID
    profile_version_id: UUID
    model_id: UUID
    fields: FrozenRuntimeFields

    def as_run_kwargs(
        self,
        *,
        capability_ledger_mode: str = "enforced",
    ) -> dict[str, Any]:
        payload = asdict(self.fields)
        payload["capability_ledger_mode"] = capability_ledger_mode
        payload["runtime_kind"] = "main_agent"
        return payload

    def as_create_run_kwargs(
        self,
        *,
        capability_ledger_mode: str = "enforced",
        commit: bool = True,
    ) -> dict[str, Any]:
        return frozen_runtime_kwargs(
            rollout_revision_id=self.rollout_revision_id,
            profile_version_id=self.profile_version_id,
            model_id=self.model_id,
            build_revision=self.fields.required_app_build_revision,
            capability_ledger_mode=capability_ledger_mode,
            commit=commit,
        )


def seed_main_agent_runtime(
    db: Session,
    *,
    build_revision: str = "test-build",
    profile_key: str | None = None,
) -> SeededMainAgentRuntime:
    """Insert profile version + prepared rollout + model binding; return frozen fields.

    Safe for SQLite unit tests. Does not activate the rollout.
    """
    from app.assistant.runtime.contracts import (
        AssistantRuntimeSubject,
        PreparedRolloutRevision,
    )
    from app.assistant.runtime.repository import AssistantRuntimeRepository
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantMainAgentProfileVersion,
    )
    from tests.agent_skill_test_support import create_default_model_binding

    key = profile_key or f"default-{uuid.uuid4().hex[:8]}"
    profile = AssistantMainAgentProfile(
        profile_key=key,
        display_name="Main Agent",
        is_default=False,
        migration_state="native",
        runtime_enabled=False,
    )
    db.add(profile)
    db.flush()
    profile_version = AssistantMainAgentProfileVersion(
        profile_id=profile.id,
        sequence_no=1,
        version_name="v1",
        version_source="save",
        origin="api",
        snapshot={"schemaVersion": 2},
        content_digest=DIGEST_A,
    )
    db.add(profile_version)
    db.flush()
    _cred, model, _binding = create_default_model_binding(db)
    subject = AssistantRuntimeSubject(
        profile_version_id=profile_version.id,
        profile_content_digest=DIGEST_A,
        model_id=model.id,
        model_identity_digest=DIGEST_B,
        package_closure=(),
        package_closure_digest=DIGEST_C,
        capability_closure_digest=DIGEST_D,
        seed_manifest_digest=DIGEST_E,
        build_revision=build_revision,
        runtime_contract_version=RUNTIME_CONTRACT_VERSION,
        checkpoint_codec_version=int(CURRENT_CHECKPOINT_CODEC_VERSION),
        capability_feature_digest=DIGEST_F,
    )
    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(
        PreparedRolloutRevision.from_subject(
            subject=subject,
            revision_id=uuid.uuid4(),
            prepared_by_operator_id=None,
            prepared_reason="assistant-runtime-support",
        )
    )
    db.flush()
    fields = frozen_runtime_fields(
        rollout_revision_id=prepared.id,
        profile_version_id=profile_version.id,
        model_id=model.id,
        build_revision=build_revision,
    )
    # Prefer deterministic digests from the prepared subject for model-row FKs;
    # override feature/codec from the prepared revision when present.
    fields = FrozenRuntimeFields(
        main_agent_rollout_revision_id=prepared.id,
        main_agent_profile_version_id=profile_version.id,
        resolved_model_id=model.id,
        runtime_closure_digest=fields.runtime_closure_digest,
        runtime_contract_version=RUNTIME_CONTRACT_VERSION,
        required_checkpoint_codec_version=int(CURRENT_CHECKPOINT_CODEC_VERSION),
        required_capability_feature_digest=fields.required_capability_feature_digest,
        required_app_build_revision=build_revision,
    )
    return SeededMainAgentRuntime(
        rollout_revision_id=prepared.id,
        profile_version_id=profile_version.id,
        model_id=model.id,
        fields=fields,
    )


def make_main_agent_run(
    db: Session,
    *,
    status: str = "queued",
    build_revision: str = "test-build",
    capability_ledger_mode: str = "enforced",
    conversation: Any | None = None,
    user_message: Any | None = None,
    assistant_message: Any | None = None,
    commit: bool = True,
    **overrides: Any,
) -> Any:
    """Insert one Main-Agent ``AssistantChatRun`` with complete frozen fields."""
    from app.assistant.models import AssistantChatRun, Conversation, Message

    seeded = seed_main_agent_runtime(db, build_revision=build_revision)
    if conversation is None:
        conversation = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
        db.add(conversation)
        db.flush()
    if user_message is None and assistant_message is None:
        user_message = Message(
            conversation_id=conversation.id, role="user", content="hi"
        )
        assistant_message = Message(
            conversation_id=conversation.id, role="assistant", content=""
        )
        db.add_all([user_message, assistant_message])
        db.flush()

    kwargs = seeded.as_run_kwargs(capability_ledger_mode=capability_ledger_mode)
    kwargs.update(
        {
            "conversation_id": conversation.id,
            "status": status,
            "user_message_id": getattr(user_message, "id", None),
            "assistant_message_id": getattr(assistant_message, "id", None),
            "state_revision": int(overrides.pop("state_revision", 0)),
            "last_event_seq": int(overrides.pop("last_event_seq", 0)),
            "memory_commit_status": overrides.pop("memory_commit_status", "pending"),
        }
    )
    kwargs.update(overrides)
    run = AssistantChatRun(**kwargs)
    db.add(run)
    db.flush()
    if commit:
        db.commit()
        db.refresh(run)
    return run


__all__ = [
    "DIGEST_A",
    "DIGEST_B",
    "DIGEST_C",
    "DIGEST_D",
    "DIGEST_E",
    "DIGEST_F",
    "FrozenRuntimeFields",
    "SeededMainAgentRuntime",
    "asdict",
    "frozen_runtime_fields",
    "frozen_runtime_kwargs",
    "make_main_agent_run",
    "seed_main_agent_runtime",
]
