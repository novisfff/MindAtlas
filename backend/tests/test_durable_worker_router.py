from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports

bootstrap_backend_imports()


class _ExecutorSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _execute_with_checkpoint(checkpoint):
    from app.assistant.durable.unit_router import DurableRunUnitRouter

    provider = _ExecutorSpy()
    durable = _ExecutorSpy()
    session = _Session()
    claimed = SimpleNamespace(run_id=uuid4())
    decision = SimpleNamespace(kind="continue")
    router = DurableRunUnitRouter(
        provider_executor=provider,
        durable_executor=durable,
        checkpoint_reader=lambda _db, _run_id: checkpoint,
    )

    router.execute(
        claimed=claimed,
        decision=decision,
        heartbeat=lambda: True,
        session_factory=lambda: session,
    )
    return provider, durable, session


def test_checkpoint_v1_keeps_existing_provider_executor() -> None:
    provider, durable, session = _execute_with_checkpoint(
        SimpleNamespace(
            schema_version=1,
            next_action=SimpleNamespace(kind="continue_provider"),
        )
    )

    assert len(provider.calls) == 1
    assert durable.calls == []
    assert session.closed is True


def test_checkpoint_v2_durable_action_routes_even_when_new_admissions_are_off(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ASSISTANT_DURABLE_INTERRUPTS_ENABLED", "false")
    provider, durable, session = _execute_with_checkpoint(
        SimpleNamespace(
            schema_version=2,
            next_action=SimpleNamespace(kind="resume_child"),
        )
    )

    assert provider.calls == []
    assert len(durable.calls) == 1
    assert session.closed is True


def test_default_worker_installs_durable_unit_router(tmp_path) -> None:
    from app.assistant.durable.unit_router import DurableRunUnitRouter
    from app.assistant.worker import AssistantWorker, AssistantWorkerConfig
    from app.assistant.durable.worker_registry import WorkerIdentity

    cfg = AssistantWorkerConfig(
        identity=WorkerIdentity(
            worker_id="router-default-worker",
            app_build_revision="test-router-build",
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
        state_path=tmp_path / "worker.json",
    )

    worker = AssistantWorker(cfg, session_factory=lambda: _Session())

    assert isinstance(worker.executor, DurableRunUnitRouter)
    assert worker.executor.durable_executor.__class__.__name__ == (
        "DurableWorkflowUnitExecutor"
    )
    assert worker.executor.durable_executor.provider_resume is not None


def test_recovery_terminal_decision_does_not_require_checkpoint_decode() -> None:
    from app.assistant.durable.unit_router import DurableRunUnitRouter

    provider = _ExecutorSpy()
    durable = _ExecutorSpy()
    router = DurableRunUnitRouter(
        provider_executor=provider,
        durable_executor=durable,
        checkpoint_reader=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("codec drift")
        ),
    )
    router.execute(
        claimed=SimpleNamespace(run_id=uuid4()),
        decision=SimpleNamespace(kind="needs_reconciliation"),
        heartbeat=lambda: True,
        session_factory=lambda: _Session(),
    )

    assert len(provider.calls) == 1
    assert durable.calls == []
