"""Durable Main-Agent rollout repository (locks, CAS, append-only events).

All methods flush but never commit. Callers own the surrounding transaction.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.assistant.runtime.contracts import (
    CONTROL_KEY_MAIN_AGENT,
    NewRolloutEvent,
    PreparedRolloutRevision,
    RuntimeControlConflict,
    RuntimeRequestReuseConflict,
    require_sha256,
)
from app.assistant.runtime.models import (
    AssistantMainAgentRolloutControl,
    AssistantMainAgentRolloutEvent,
    AssistantMainAgentRolloutRevision,
)
from app.common.time import utcnow


class AssistantRuntimeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create_control_for_update(self) -> AssistantMainAgentRolloutControl:
        stmt = (
            select(AssistantMainAgentRolloutControl)
            .where(AssistantMainAgentRolloutControl.control_key == CONTROL_KEY_MAIN_AGENT)
            .with_for_update()
        )
        control = self.db.execute(stmt).scalar_one_or_none()
        if control is not None:
            return control

        control = AssistantMainAgentRolloutControl(
            control_key=CONTROL_KEY_MAIN_AGENT,
            active_rollout_revision_id=None,
            state_revision=0,
            new_runs_enabled=True,
        )
        self.db.add(control)
        self.db.flush()

        # Re-select under FOR UPDATE so concurrent creators serialize.
        control = self.db.execute(stmt).scalar_one()
        return control

    def get_control(self) -> AssistantMainAgentRolloutControl | None:
        """Read-only control lookup; never creates the singleton."""
        return self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)

    def get_active_revision_for_update(
        self,
    ) -> AssistantMainAgentRolloutRevision | None:
        control = self.get_or_create_control_for_update()
        if control.active_rollout_revision_id is None:
            return None
        return self.get_revision_for_update(control.active_rollout_revision_id)

    def get_revision_for_update(
        self, revision_id: UUID
    ) -> AssistantMainAgentRolloutRevision | None:
        stmt = (
            select(AssistantMainAgentRolloutRevision)
            .where(AssistantMainAgentRolloutRevision.id == revision_id)
            .with_for_update()
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def create_prepared_revision(
        self, data: PreparedRolloutRevision
    ) -> AssistantMainAgentRolloutRevision:
        row = AssistantMainAgentRolloutRevision(
            id=data.id,
            revision_label=data.revision_label,
            profile_version_id=data.profile_version_id,
            profile_content_digest=require_sha256(
                data.profile_content_digest, field_name="profile_content_digest"
            ),
            model_id=data.model_id,
            model_identity_digest=require_sha256(
                data.model_identity_digest, field_name="model_identity_digest"
            ),
            package_closure_json=list(data.package_closure_json),
            package_closure_digest=require_sha256(
                data.package_closure_digest, field_name="package_closure_digest"
            ),
            capability_closure_digest=require_sha256(
                data.capability_closure_digest, field_name="capability_closure_digest"
            ),
            seed_manifest_digest=require_sha256(
                data.seed_manifest_digest, field_name="seed_manifest_digest"
            ),
            build_revision=data.build_revision,
            runtime_contract_version=int(data.runtime_contract_version),
            checkpoint_codec_version=int(data.checkpoint_codec_version),
            capability_feature_digest=require_sha256(
                data.capability_feature_digest, field_name="capability_feature_digest"
            ),
            revision_digest=require_sha256(
                data.revision_digest, field_name="revision_digest"
            ),
            prepared_by_operator_id=data.prepared_by_operator_id,
            prepared_reason=data.prepared_reason,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def find_request_event(
        self, request_id: UUID
    ) -> AssistantMainAgentRolloutEvent | None:
        return (
            self.db.query(AssistantMainAgentRolloutEvent)
            .filter(AssistantMainAgentRolloutEvent.request_id == request_id)
            .one_or_none()
        )

    def assert_request_replay(
        self,
        *,
        request_id: UUID,
        request_digest: str,
    ) -> AssistantMainAgentRolloutEvent | None:
        digest = require_sha256(request_digest, field_name="request_digest")
        existing = self.find_request_event(request_id)
        if existing is None:
            return None
        if str(existing.request_digest) != digest:
            raise RuntimeRequestReuseConflict(
                "request_id already used with a different request_digest"
            )
        return existing

    def append_control_event(
        self, event: NewRolloutEvent
    ) -> AssistantMainAgentRolloutEvent:
        row = AssistantMainAgentRolloutEvent(
            from_rollout_revision_id=event.from_rollout_revision_id,
            to_rollout_revision_id=event.to_rollout_revision_id,
            action=event.action,
            control_revision=int(event.control_revision),
            request_id=event.request_id,
            request_digest=event.request_digest,
            operator_id=event.operator_id,
            operator_session_id=event.operator_session_id,
            reason=event.reason,
            evidence_digest=event.evidence_digest,
            result_json=dict(event.result_json),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def compare_and_set_control(
        self,
        *,
        expected_state_revision: int,
        active_rollout_revision_id: UUID | None,
        new_runs_enabled: bool,
    ) -> AssistantMainAgentRolloutControl:
        now = utcnow()
        result = self.db.execute(
            update(AssistantMainAgentRolloutControl)
            .where(
                AssistantMainAgentRolloutControl.control_key == CONTROL_KEY_MAIN_AGENT,
                AssistantMainAgentRolloutControl.state_revision
                == int(expected_state_revision),
            )
            .values(
                active_rollout_revision_id=active_rollout_revision_id,
                new_runs_enabled=bool(new_runs_enabled),
                state_revision=int(expected_state_revision) + 1,
                updated_at=now,
            )
        )
        if int(result.rowcount or 0) != 1:
            raise RuntimeControlConflict(
                f"expected control state_revision={expected_state_revision}"
            )
        self.db.flush()
        control = self.get_or_create_control_for_update()
        return control
