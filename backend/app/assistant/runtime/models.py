"""Immutable Main-Agent rollout revision / control / event ORM models (Plan 2)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapper

from app.assistant.runtime.contracts import (
    CONTROL_KEY_MAIN_AGENT,
    ROLLOUT_EVENT_ACTIONS,
)
from app.common.time import utcnow
from app.database import Base


def _sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=name)


_ACTION_SQL = ", ".join(f"'{a}'" for a in ROLLOUT_EVENT_ACTIONS)


class AssistantMainAgentRolloutRevision(Base):
    """Immutable prepared Main-Agent rollout revision (content never changes)."""

    __tablename__ = "assistant_main_agent_rollout_revision"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_label = Column(String(128), nullable=False)
    profile_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_profile_version.id"),
        nullable=False,
    )
    profile_content_digest = Column(String(64), nullable=False)
    model_id = Column(UUID(as_uuid=True), ForeignKey("ai_model.id"), nullable=False)
    model_identity_digest = Column(String(64), nullable=False)
    package_closure_json = Column(JSONB, nullable=False)
    package_closure_digest = Column(String(64), nullable=False)
    capability_closure_digest = Column(String(64), nullable=False)
    seed_manifest_digest = Column(String(64), nullable=False)
    build_revision = Column(String(128), nullable=False)
    runtime_contract_version = Column(Integer, nullable=False)
    checkpoint_codec_version = Column(Integer, nullable=False)
    capability_feature_digest = Column(String(64), nullable=False)
    required_create_entry_contract_digest = Column(String(64), nullable=False)
    required_write_policy_digest = Column(String(64), nullable=False)
    required_write_cohort_digest = Column(String(64), nullable=False)
    required_reconciliation_contract_version = Column(Integer, nullable=False)
    revision_digest = Column(String(64), nullable=False)
    prepared_by_operator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("operator_account.id"),
        nullable=True,
    )
    prepared_reason = Column(String(500), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "revision_digest",
            name="uq_ma_rollout_revision_digest",
        ),
        UniqueConstraint(
            "revision_label",
            name="uq_ma_rollout_revision_label",
        ),
        CheckConstraint(
            "runtime_contract_version > 0 AND checkpoint_codec_version > 0 "
            "AND required_reconciliation_contract_version > 0",
            name="ck_ma_rollout_revision_positive_contract",
        ),
        CheckConstraint(
            "length(prepared_reason) >= 1 AND length(prepared_reason) <= 500",
            name="ck_ma_rollout_revision_reason_len",
        ),
        CheckConstraint(
            "length(build_revision) >= 1 AND length(build_revision) <= 128",
            name="ck_ma_rollout_revision_build_len",
        ),
        _sha256_check(
            "profile_content_digest",
            name="ck_ma_rollout_revision_profile_content_digest",
        ),
        _sha256_check(
            "model_identity_digest",
            name="ck_ma_rollout_revision_model_identity_digest",
        ),
        _sha256_check(
            "package_closure_digest",
            name="ck_ma_rollout_revision_package_closure_digest",
        ),
        _sha256_check(
            "capability_closure_digest",
            name="ck_ma_rollout_revision_capability_closure_digest",
        ),
        _sha256_check(
            "seed_manifest_digest",
            name="ck_ma_rollout_revision_seed_manifest_digest",
        ),
        _sha256_check(
            "capability_feature_digest",
            name="ck_ma_rollout_revision_capability_feature_digest",
        ),
        _sha256_check(
            "required_create_entry_contract_digest",
            name="ck_ma_rollout_revision_create_entry_contract_digest",
        ),
        _sha256_check(
            "required_write_policy_digest",
            name="ck_ma_rollout_revision_write_policy_digest",
        ),
        _sha256_check(
            "required_write_cohort_digest",
            name="ck_ma_rollout_revision_write_cohort_digest",
        ),
        _sha256_check(
            "revision_digest",
            name="ck_ma_rollout_revision_revision_digest",
        ),
    )


class AssistantRuntimeBootstrapGateUse(Base):
    """Immutable authorization provenance for the trusted system bootstrap.

    This is intentionally separate from the evaluation publish-gate tables:
    system bootstrap is build-owned and does not claim to be an evaluated user
    publication.  It binds the trusted seed's exact Profile, Skill, Model, and
    runtime closure inputs in the same initialization transaction as the
    prepared rollout.
    """

    __tablename__ = "assistant_runtime_bootstrap_gate_use"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    action = Column(String(32), nullable=False, default="system_bootstrap")
    rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=False,
    )
    rollout_revision_digest = Column(String(64), nullable=False)
    profile_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_profile_version.id"),
        nullable=False,
    )
    profile_content_digest = Column(String(64), nullable=False)
    skill_package_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_package.id"),
        nullable=False,
    )
    skill_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_version.id"),
        nullable=False,
    )
    skill_version_digest = Column(String(64), nullable=False)
    model_id = Column(UUID(as_uuid=True), ForeignKey("ai_model.id"), nullable=False)
    model_identity_digest = Column(String(64), nullable=False)
    seed_manifest_digest = Column(String(64), nullable=False)
    seed_contract_digest = Column(String(64), nullable=False)
    package_closure_digest = Column(String(64), nullable=False)
    capability_closure_digest = Column(String(64), nullable=False)
    build_revision = Column(String(128), nullable=False)
    runtime_contract_version = Column(Integer, nullable=False)
    checkpoint_codec_version = Column(Integer, nullable=False)
    capability_feature_digest = Column(String(64), nullable=False)
    closure_digest = Column(String(64), nullable=False)
    bootstrap_request_id = Column(UUID(as_uuid=True), nullable=False)
    operator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("operator_account.id"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "bootstrap_request_id",
            name="uq_runtime_bootstrap_gate_use_request_id",
        ),
        UniqueConstraint(
            "rollout_revision_id",
            name="uq_runtime_bootstrap_gate_use_rollout_revision_id",
        ),
        CheckConstraint(
            "action = 'system_bootstrap'",
            name="ck_runtime_bootstrap_gate_use_action",
        ),
        CheckConstraint(
            "runtime_contract_version > 0 AND checkpoint_codec_version > 0",
            name="ck_runtime_bootstrap_gate_use_positive_contract",
        ),
        CheckConstraint(
            "length(build_revision) >= 1 AND length(build_revision) <= 128",
            name="ck_runtime_bootstrap_gate_use_build_len",
        ),
        _sha256_check(
            "rollout_revision_digest",
            name="ck_runtime_bootstrap_gate_use_rollout_revision_digest",
        ),
        _sha256_check(
            "profile_content_digest",
            name="ck_runtime_bootstrap_gate_use_profile_content_digest",
        ),
        _sha256_check(
            "skill_version_digest",
            name="ck_runtime_bootstrap_gate_use_skill_version_digest",
        ),
        _sha256_check(
            "model_identity_digest",
            name="ck_runtime_bootstrap_gate_use_model_identity_digest",
        ),
        _sha256_check(
            "seed_manifest_digest",
            name="ck_runtime_bootstrap_gate_use_seed_manifest_digest",
        ),
        _sha256_check(
            "seed_contract_digest",
            name="ck_runtime_bootstrap_gate_use_seed_contract_digest",
        ),
        _sha256_check(
            "package_closure_digest",
            name="ck_runtime_bootstrap_gate_use_package_closure_digest",
        ),
        _sha256_check(
            "capability_closure_digest",
            name="ck_runtime_bootstrap_gate_use_capability_closure_digest",
        ),
        _sha256_check(
            "capability_feature_digest",
            name="ck_runtime_bootstrap_gate_use_capability_feature_digest",
        ),
        _sha256_check(
            "closure_digest",
            name="ck_runtime_bootstrap_gate_use_closure_digest",
        ),
    )


class AssistantMainAgentRolloutControl(Base):
    """Singleton durable control pointer for the active Main-Agent rollout."""

    __tablename__ = "assistant_main_agent_rollout_control"

    control_key = Column(String(32), primary_key=True)
    active_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=True,
    )
    state_revision = Column(Integer, nullable=False, server_default=text("0"), default=0)
    new_runs_enabled = Column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=text("now()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            f"control_key = '{CONTROL_KEY_MAIN_AGENT}'",
            name="ck_ma_rollout_control_key",
        ),
        CheckConstraint(
            "state_revision >= 0",
            name="ck_ma_rollout_control_state_revision",
        ),
    )


class AssistantMainAgentRolloutEvent(Base):
    """Append-only rollout control event (prepare/activate/switch)."""

    __tablename__ = "assistant_main_agent_rollout_event"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    from_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=True,
    )
    to_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=True,
    )
    action = Column(String(32), nullable=False)
    control_revision = Column(Integer, nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    request_digest = Column(String(64), nullable=False)
    operator_id = Column(
        UUID(as_uuid=True), ForeignKey("operator_account.id"), nullable=True
    )
    operator_session_id = Column(
        UUID(as_uuid=True), ForeignKey("operator_session.id"), nullable=True
    )
    reason = Column(String(500), nullable=False)
    evidence_digest = Column(String(64), nullable=False)
    result_json = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "request_id",
            name="uq_ma_rollout_event_request_id",
        ),
        CheckConstraint(
            f"action IN ({_ACTION_SQL})",
            name="ck_ma_rollout_event_action",
        ),
        CheckConstraint(
            "control_revision >= 0",
            name="ck_ma_rollout_event_control_revision",
        ),
        CheckConstraint(
            "length(reason) >= 1 AND length(reason) <= 500",
            name="ck_ma_rollout_event_reason_len",
        ),
        _sha256_check(
            "request_digest",
            name="ck_ma_rollout_event_request_digest",
        ),
        _sha256_check(
            "evidence_digest",
            name="ck_ma_rollout_event_evidence_digest",
        ),
    )


def _reject_rollout_revision_mutation(
    mapper: Mapper, connection, target  # noqa: ANN001
) -> None:
    # SQLite unit tests lack PL/pgSQL triggers; enforce immutability via ORM.
    raise IntegrityError(
        "assistant rollout revision is immutable",
        params=None,
        orig=Exception("assistant rollout revision is immutable"),
    )


def _reject_rollout_event_mutation(
    mapper: Mapper, connection, target  # noqa: ANN001
) -> None:
    raise IntegrityError(
        "assistant rollout event is append-only",
        params=None,
        orig=Exception("assistant rollout event is append-only"),
    )


def _reject_bootstrap_gate_use_mutation(
    mapper: Mapper, connection, target  # noqa: ANN001
) -> None:
    raise IntegrityError(
        "assistant runtime bootstrap gate use is immutable",
        params=None,
        orig=Exception("assistant runtime bootstrap gate use is immutable"),
    )


# Late import keeps sqlalchemy.exc out of module import cycle noise.
from sqlalchemy.exc import IntegrityError  # noqa: E402

event.listen(AssistantMainAgentRolloutRevision, "before_update", _reject_rollout_revision_mutation)
event.listen(AssistantMainAgentRolloutRevision, "before_delete", _reject_rollout_revision_mutation)
event.listen(AssistantMainAgentRolloutEvent, "before_update", _reject_rollout_event_mutation)
event.listen(AssistantMainAgentRolloutEvent, "before_delete", _reject_rollout_event_mutation)
event.listen(AssistantRuntimeBootstrapGateUse, "before_update", _reject_bootstrap_gate_use_mutation)
event.listen(AssistantRuntimeBootstrapGateUse, "before_delete", _reject_bootstrap_gate_use_mutation)
