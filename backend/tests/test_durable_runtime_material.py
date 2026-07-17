from __future__ import annotations

import os
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-plan07-material")
os.environ.setdefault("APP_ENV", "test")


def _make_session():
    from tests._db import make_session

    return make_session()


def _workflow_state(golden, *, plan_digest: str | None = None):
    from app.assistant.workflow.durable.contracts import (
        DurableCallFrameV1,
        DurableWorkflowStateV1,
    )

    run_id = uuid4()
    frame_id = uuid4()
    return DurableWorkflowStateV1(
        run_id=run_id,
        root_frame_id=frame_id,
        root_invocation_digest="a" * 64,
        frame_stack=(
            DurableCallFrameV1(
                frame_id=frame_id,
                parent_frame_id=None,
                invocation_call_id="call-root",
                owner_skill_package_id=golden.skill_package_id,
                owner_skill_version_id=golden.skill_version_id,
                target_kind="workflow",
                target_id=golden.workflow_id,
                target_version_id=golden.workflow_version_id,
                target_digest=golden.target_digest,
                execution_plan_digest=plan_digest or golden.plan_digest,
                current_node_id="approve",
                node_visit_id="visit-approve-1",
                node_visit_ordinal=1,
                execution_attempt=1,
                phase="waiting",
            ),
        ),
    )


def test_reconstructs_root_material_from_exact_published_rows() -> None:
    from app.assistant.workflow.durable.golden_path import (
        publish_durable_proposal_review,
    )
    from app.assistant.workflow.durable.material import DurableRuntimeMaterialResolver

    db = _make_session()
    try:
        golden = publish_durable_proposal_review(db)
        state = _workflow_state(golden)

        root, children = DurableRuntimeMaterialResolver(db).resolve(
            workflow_state=state
        )

        assert root.plan.target_version_id == golden.workflow_version_id
        assert root.plan.plan_digest == golden.plan_digest
        assert root.node_configs == golden.node_configs
        assert children[str(golden.workflow_version_id)].plan == root.plan
    finally:
        db.close()


def test_rejects_checkpoint_plan_digest_drift() -> None:
    from app.assistant.workflow.durable.golden_path import (
        publish_durable_proposal_review,
    )
    from app.assistant.workflow.durable.material import (
        DurableMaterialResolutionError,
        DurableRuntimeMaterialResolver,
    )

    db = _make_session()
    try:
        golden = publish_durable_proposal_review(db)
        state = _workflow_state(golden, plan_digest="f" * 64)

        with pytest.raises(DurableMaterialResolutionError, match="plan digest"):
            DurableRuntimeMaterialResolver(db).resolve(workflow_state=state)
    finally:
        db.close()


def test_never_falls_back_when_frozen_owner_version_is_missing() -> None:
    from app.assistant.workflow.durable.golden_path import (
        publish_durable_proposal_review,
    )
    from app.assistant.workflow.durable.material import (
        DurableMaterialResolutionError,
        DurableRuntimeMaterialResolver,
    )

    db = _make_session()
    try:
        golden = publish_durable_proposal_review(db)
        state = _workflow_state(golden)
        payload = state.model_dump(mode="python")
        payload["frame_stack"][0]["owner_skill_version_id"] = uuid4()
        from app.assistant.workflow.durable.contracts import DurableWorkflowStateV1

        missing_owner = DurableWorkflowStateV1.model_validate(payload)

        with pytest.raises(DurableMaterialResolutionError, match="owner Skill version"):
            DurableRuntimeMaterialResolver(db).resolve(workflow_state=missing_owner)
    finally:
        db.close()
