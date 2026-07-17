from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports

bootstrap_backend_imports()


def test_provider_resume_failure_routes_to_reconciliation(monkeypatch) -> None:
    import app.assistant.durable.workflow_executor as module

    checkpoint = SimpleNamespace(
        next_action=SimpleNamespace(kind="resume_child"),
        workflow_state=SimpleNamespace(),
        pending_interrupt_id=None,
    )
    monkeypatch.setattr(
        module,
        "load_current_checkpoint",
        lambda *_args, **_kwargs: checkpoint,
    )

    class _Resolver:
        def __init__(self, _db) -> None:
            pass

        def resolve(self, *, workflow_state):
            return object(), {}

    monkeypatch.setattr(module, "DurableRuntimeMaterialResolver", _Resolver)
    monkeypatch.setattr(
        module,
        "execute_interrupt_resume",
        lambda *_args, **_kwargs: SimpleNamespace(
            kind="root_terminal",
            provider_waiting_resolution=object(),
            state_revision=7,
        ),
    )

    class _Db:
        def close(self) -> None:
            pass

    executor = module.DurableWorkflowUnitExecutor(
        provider_resume=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("resume failed")
        )
    )
    monkeypatch.setattr(
        executor,
        "_load_provider_continuation",
        lambda *_args, **_kwargs: object(),
    )
    reconciled = []
    monkeypatch.setattr(
        executor,
        "_reconcile",
        lambda *_args, **kwargs: reconciled.append(kwargs),
    )

    executor.execute(
        claimed=SimpleNamespace(
            run_id=uuid4(),
            state_revision=6,
            lease=SimpleNamespace(),
        ),
        decision=SimpleNamespace(kind="continue"),
        heartbeat=lambda: True,
        session_factory=_Db,
    )

    assert reconciled[0]["reason_code"] == "provider_resume_failed"
    assert reconciled[0]["expected_revision"] == 7


def test_resume_provider_loop_action_uses_persisted_resolution(monkeypatch) -> None:
    import app.assistant.durable.workflow_executor as module

    checkpoint = SimpleNamespace(
        next_action=SimpleNamespace(kind="resume_provider_loop"),
        artifact_ids=(uuid4(),),
    )
    monkeypatch.setattr(
        module,
        "load_current_checkpoint",
        lambda *_args, **_kwargs: checkpoint,
    )

    class _Db:
        def get(self, *_args):
            return SimpleNamespace(current_checkpoint_id=uuid4())

        def close(self) -> None:
            pass

    executor = module.DurableWorkflowUnitExecutor(provider_resume=lambda **_kwargs: None)
    continuation = object()
    resolution = object()
    monkeypatch.setattr(
        executor,
        "_load_provider_continuation",
        lambda *_args, **_kwargs: continuation,
    )
    monkeypatch.setattr(
        executor,
        "_load_provider_waiting_resolution",
        lambda *_args, **_kwargs: resolution,
    )
    invoked = []
    monkeypatch.setattr(
        executor,
        "_invoke_provider_resume",
        lambda *_args, **kwargs: invoked.append(kwargs),
    )

    executor.execute(
        claimed=SimpleNamespace(
            run_id=uuid4(),
            state_revision=9,
            lease=SimpleNamespace(),
        ),
        decision=SimpleNamespace(kind="continue"),
        heartbeat=lambda: True,
        session_factory=_Db,
    )

    assert invoked[0]["continuation"] is continuation
    assert invoked[0]["resolution"] is resolution
    assert invoked[0]["expected_revision"] == 9
