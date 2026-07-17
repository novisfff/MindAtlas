from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests._bootstrap import bootstrap_backend_imports

bootstrap_backend_imports()


class _Session:
    def __init__(self) -> None:
        self.closed = False
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


def _cfg():
    from app.assistant.durable.worker_registry import WorkerIdentity
    from app.assistant.worker import AssistantWorkerConfig

    return AssistantWorkerConfig(
        identity=WorkerIdentity(
            worker_id="expiry-worker",
            app_build_revision="test-build-expiry",
            runtime_contract_version=1,
            supported_checkpoint_codec_versions=(1, 2),
        ),
        poll_interval_ms=100,
        lease_ttl_sec=30,
        heartbeat_interval_sec=5,
        registration_ttl_sec=20,
        max_recovery_attempts=5,
        retry_base_ms=100,
        retry_max_ms=1000,
        state_path=Path("/tmp/unused-expiry-worker-state.json"),
        expiry_scan_interval_sec=5.0,
        expiry_scan_batch_size=17,
    )


def test_run_once_scans_expiry_even_when_no_run_is_claimed(monkeypatch) -> None:
    from app.assistant.worker import AssistantWorker

    sessions: list[_Session] = []
    scans: list[tuple[_Session, int]] = []

    def session_factory():
        session = _Session()
        sessions.append(session)
        return session

    class _LeaseService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def claim_next(self, *, draining: bool = False):
            assert draining is False
            return None

    monkeypatch.setattr("app.assistant.worker.RunLeaseService", _LeaseService)
    worker = AssistantWorker(
        _cfg(),
        session_factory=session_factory,
        executor=SimpleNamespace(execute=lambda **_kwargs: None),
        expiry_scanner=lambda db, *, limit: scans.append((db, limit)),
        monotonic=lambda: 100.0,
    )

    assert worker.run_once() == 0
    assert len(scans) == 1
    assert scans[0][1] == 17
    assert all(session.closed for session in sessions)


def test_expiry_scan_failure_rolls_back_and_does_not_block_claim(monkeypatch) -> None:
    from app.assistant.worker import AssistantWorker

    sessions: list[_Session] = []

    def session_factory():
        session = _Session()
        sessions.append(session)
        return session

    class _LeaseService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def claim_next(self, *, draining: bool = False):
            return None

    def fail_scan(_db, *, limit: int):
        assert limit == 17
        raise RuntimeError("scan failed")

    monkeypatch.setattr("app.assistant.worker.RunLeaseService", _LeaseService)
    worker = AssistantWorker(
        _cfg(),
        session_factory=session_factory,
        executor=SimpleNamespace(execute=lambda **_kwargs: None),
        expiry_scanner=fail_scan,
        monotonic=lambda: 100.0,
    )

    assert worker.run_once() == 0
    assert sessions[0].rollback_count == 1
    assert len(sessions) == 2


def test_typed_expiry_requires_an_explicit_expired_edge() -> None:
    from app.assistant.workflow.durable.interrupt_api import _has_explicit_expired_edge
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )
    from uuid import uuid4

    target_version_id = uuid4()
    expired_edge = DurableEdgeV1(
        edge_id="expired",
        source_node_id="approve",
        target_node_id="cleanup",
        source_handle="expired",
        target_handle=None,
    )
    default_edge = DurableEdgeV1(
        edge_id="default",
        source_node_id="approve",
        target_node_id="output",
        source_handle=None,
        target_handle=None,
    )

    def plan(edges):
        nodes = (
            DurableNodePlanV1(
                node_id="approve",
                node_type="human_in_loop",
                config_digest="a" * 64,
                outgoing_edges=edges,
                adapter_key="human.v1",
                business_side_effect="none",
                may_interrupt=True,
                dependency_refs=(),
            ),
        )
        digest = compute_plan_digest(
            target_kind="workflow",
            target_version_id=target_version_id,
            target_digest="b" * 64,
            entry_node_id="approve",
            nodes=nodes,
        )
        return DurableExecutionPlanV1(
            target_kind="workflow",
            target_version_id=target_version_id,
            target_digest="b" * 64,
            entry_node_id="approve",
            nodes=nodes,
            plan_digest=digest,
        )

    assert _has_explicit_expired_edge(plan((default_edge, expired_edge)), "approve")
    assert not _has_explicit_expired_edge(plan((default_edge,)), "approve")


def test_expiry_material_drift_is_not_converted_to_terminal_cancel() -> None:
    from app.assistant.workflow.durable.interrupt_api import (
        _interrupt_has_typed_expiry,
    )

    class _Db:
        def get(self, *_args):
            return object()

    import pytest

    with pytest.raises(Exception, match="durable expiry material"):
        _interrupt_has_typed_expiry(
            _Db(), interrupt=type("Interrupt", (), {"checkpoint_id": "missing"})()
        )
