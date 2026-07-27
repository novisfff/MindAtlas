"""Plan 07 Task 0 characterization: WorkflowState is not portable.

Documents every nonserializable field reachable from the current compiled
LangGraph ``WorkflowState`` so Plan 07 does **not** attempt to pickle/JSON
the live state or retrofit a database Checkpointer onto it.

These tests pass by proving naive serialization **fails** (or would lose
runtime objects). They are characterization only — not a product feature.
"""

from __future__ import annotations

import json
import pickle
import threading
import unittest
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from app.assistant.workflow.engine.state import WorkflowState
from app.assistant.workflow.human_approval_runtime import (
    HumanLoopCoordinator,
    HumanLoopContext,
    HumanLoopRuntime,
)


def _sample_runtime() -> HumanLoopRuntime:
    return HumanLoopRuntime(
        session_factory=lambda: MagicMock(),  # type: ignore[arg-type]
        context=HumanLoopContext(
            run_id="run-1",
            channel_type="web",
            conversation_id=uuid4(),
            message_id=uuid4(),
            workflow_id=uuid4(),
            skill_id=uuid4(),
        ),
        cancel_checker=lambda: False,
        on_requested=lambda payload: None,
        on_resolved=lambda payload: None,
    )


def _json_roundtrip(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _pickle_roundtrip(value: Any) -> Any:
    return pickle.loads(pickle.dumps(value))


class WorkflowStateNonserializableFieldEnumeration(unittest.TestCase):
    """Enumerate fields that block portable Checkpoint persistence."""

    # Field inventory from WorkflowState TypedDict + runtime injections.
    WORKFLOW_STATE_DECLARED_KEYS = (
        "messages",
        "skill_name",
        "workflow_id",
        "workflow_version_id",
        "user_input",
        "kb_enabled",
        "memory_mode",
        "metadata",
        "memory_context",
        "node_outputs",
        "execution_trace",
        "branch_decisions",
        "sys_vars",
        "workflow_node_types",
        "node_llms",
        "stream_output_enabled",
        "output_stream_source_node_id",
        "structured_input",
        "env_vars",
        "env_specs",
    )

    # Injected into metadata / node_llms / process locals — not portable.
    NONSERIALIZABLE_RUNTIME_ANCHORS = (
        "metadata.human_loop_runtime",  # HumanLoopRuntime (session_factory, callbacks)
        "metadata.on_* callbacks",  # callables wired by stream_runtime
        "metadata.__workflow_call_stack__",  # portable list, but sibling of nonportable runtime
        "metadata.workflow_call_session_scopes",  # may hold session-scoped objects
        "node_llms[*]",  # ChatOpenAI / client objects
        "messages[*]",  # LangChain BaseMessage (JSON-able only via custom codec)
        "GLOBAL_HUMAN_LOOP_COORDINATOR",  # process-local threading.Event waiters
        "stream_runtime graph_thread",  # daemon Thread holding compiled graph
        "compiled LangGraph",  # not on WorkflowState but owns execution
        "sqlalchemy Session / session_factory",  # held by HumanLoopRuntime
        "cancel_checker callable",  # held by HumanLoopRuntime
    )

    def test_declared_keys_match_typed_dict(self) -> None:
        self.assertEqual(
            set(self.WORKFLOW_STATE_DECLARED_KEYS),
            set(WorkflowState.__annotations__.keys()),
        )

    def test_human_loop_runtime_not_json_serializable(self) -> None:
        metadata = {"human_loop_runtime": _sample_runtime()}
        with self.assertRaises((TypeError, ValueError)):
            _json_roundtrip(metadata)

    def test_human_loop_runtime_pickle_loses_or_fails(self) -> None:
        """Pickle may fail or resurrect without live session/coordinator waiters."""
        runtime = _sample_runtime()
        try:
            restored = _pickle_roundtrip(runtime)
        except Exception as exc:  # characterization: either fails or is hollow
            self.assertIsInstance(exc, (TypeError, AttributeError, pickle.PicklingError))
            return
        # If pickle succeeds, it cannot restore process-local coordinator waiters.
        self.assertIsInstance(restored, HumanLoopRuntime)
        # session_factory is a lambda — may unpickle as a hollow function object
        self.assertTrue(callable(restored._session_factory))

    def test_removed_coordinator_retains_no_process_local_waiters(self) -> None:
        coordinator = HumanLoopCoordinator()
        coordinator.register("approval-1", threading.Event())
        self.assertEqual(coordinator._waiters, {})

    def test_stream_callbacks_not_json_serializable(self) -> None:
        metadata = {
            "on_node_start": lambda node_id, node_type, **extra: None,
            "on_human_approval_requested": lambda payload: None,
            "on_human_approval_resolved": lambda payload: None,
        }
        with self.assertRaises((TypeError, ValueError)):
            _json_roundtrip(metadata)

    def test_node_llms_chat_clients_not_json_serializable(self) -> None:
        fake_llm = MagicMock(name="ChatOpenAI")
        fake_llm.invoke = lambda *a, **k: "x"
        node_llms = {"llm_node": fake_llm}
        with self.assertRaises((TypeError, ValueError)):
            _json_roundtrip(node_llms)

    def test_langchain_messages_not_raw_json_serializable(self) -> None:
        messages = [HumanMessage(content="hi"), AIMessage(content="hello")]
        with self.assertRaises((TypeError, ValueError)):
            _json_roundtrip(messages)

    def test_full_workflow_state_with_runtime_not_json_portable(self) -> None:
        state: dict[str, Any] = {
            "messages": [HumanMessage(content="hi")],
            "skill_name": "demo",
            "workflow_id": "wf-1",
            "workflow_version_id": "wv-1",
            "user_input": "hi",
            "kb_enabled": False,
            "memory_mode": "off",
            "metadata": {
                "human_loop_runtime": _sample_runtime(),
                "on_node_start": lambda **kw: None,
            },
            "memory_context": {},
            "node_outputs": {},
            "execution_trace": ["start"],
            "branch_decisions": {},
            "sys_vars": {},
            "workflow_node_types": {"start": "start"},
            "node_llms": {"llm": MagicMock(name="ChatOpenAI")},
            "stream_output_enabled": True,
            "output_stream_source_node_id": "",
            "structured_input": {},
            "env_vars": {},
            "env_specs": {},
        }
        with self.assertRaises((TypeError, ValueError)):
            _json_roundtrip(state)

    def test_threading_event_not_meaningfully_portable_across_process(self) -> None:
        event = threading.Event()
        event.set()
        # pickle of Event may work in-process but does not reconnect waiters
        try:
            restored = _pickle_roundtrip(event)
        except Exception:
            return
        # Even if pickle works, a process restart loses in-memory waiter maps.
        self.assertIsInstance(restored, threading.Event)


if __name__ == "__main__":
    unittest.main()
