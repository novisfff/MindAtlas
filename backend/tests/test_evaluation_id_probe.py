"""Eval ID membership probes must not poison the outer DB transaction.

When Plan 09 eval tables are absent (e.g. capability PG suite parked at Plan 08
ledger), ``session.get(EvalRun, …)`` raises ProgrammingError and PostgreSQL
aborts the transaction. Probes must use a SAVEPOINT so callers can continue.
"""

from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock

from tests._bootstrap import bootstrap_backend_imports

bootstrap_backend_imports()

from app.assistant.evaluation.contracts import (  # noqa: E402
    is_evaluation_capability_call_id,
    is_evaluation_event_id,
    is_evaluation_run_id,
    reject_if_evaluation_id,
)


class _PoisoningSession:
    """Session whose ``get`` aborts the outer txn unless run under begin_nested."""

    def __init__(self) -> None:
        self.poisoned = False
        self.nested_depth = 0
        self.get_calls = 0
        self.outer_ops = 0

    @contextmanager
    def begin_nested(self) -> Iterator[None]:
        self.nested_depth += 1
        try:
            yield
        except Exception:
            # SAVEPOINT rollback — outer remains usable.
            self.nested_depth -= 1
            raise
        else:
            self.nested_depth -= 1

    def get(self, model: Any, row_id: Any) -> Any:
        self.get_calls += 1
        if self.nested_depth == 0:
            self.poisoned = True
            raise RuntimeError("relation does not exist (no savepoint)")
        # Inside savepoint: still raise missing-table style error.
        raise RuntimeError("relation \"assistant_skill_eval_run\" does not exist")

    def execute_ok(self) -> str:
        if self.poisoned:
            raise RuntimeError("InFailedSqlTransaction")
        self.outer_ops += 1
        return "ok"


class _HitSession:
    """Session that finds an eval row under a savepoint."""

    def __init__(self, hit: bool = True) -> None:
        self.hit = hit
        self.nested = 0

    @contextmanager
    def begin_nested(self) -> Iterator[None]:
        self.nested += 1
        try:
            yield
        finally:
            self.nested -= 1

    def get(self, model: Any, row_id: Any) -> Any:
        assert self.nested == 1, "probe must run inside begin_nested"
        return object() if self.hit else None


class EvaluationIdProbeTests(unittest.TestCase):
    def test_missing_table_under_savepoint_does_not_poison_outer(self) -> None:
        session = _PoisoningSession()
        rid = uuid.uuid4()
        self.assertFalse(is_evaluation_run_id(session, rid))
        self.assertFalse(is_evaluation_capability_call_id(session, rid))
        self.assertFalse(is_evaluation_event_id(session, rid))
        self.assertFalse(session.poisoned)
        self.assertEqual(session.execute_ok(), "ok")
        self.assertEqual(session.get_calls, 3)

    def test_reject_if_evaluation_id_safe_when_tables_missing(self) -> None:
        session = _PoisoningSession()
        # Must not raise and must leave session usable.
        reject_if_evaluation_id(session, entity="run", value=uuid.uuid4())
        reject_if_evaluation_id(session, entity="capability_call", value=uuid.uuid4())
        reject_if_evaluation_id(session, entity="event", value=uuid.uuid4())
        self.assertEqual(session.execute_ok(), "ok")

    def test_hit_uses_begin_nested_and_rejects(self) -> None:
        session = _HitSession(hit=True)
        with self.assertRaises(ValueError) as ctx:
            reject_if_evaluation_id(session, entity="run", value=uuid.uuid4())
        self.assertIn("evaluation identifiers", str(ctx.exception))

    def test_miss_uses_begin_nested_and_allows(self) -> None:
        session = _HitSession(hit=False)
        reject_if_evaluation_id(session, entity="run", value=uuid.uuid4())

    def test_invalid_uuid_false(self) -> None:
        session = MagicMock()
        self.assertFalse(is_evaluation_run_id(session, "not-a-uuid"))
        session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
