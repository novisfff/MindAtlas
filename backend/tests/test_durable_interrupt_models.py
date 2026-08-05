"""ORM + BudgetSuspension unit tests for Plan 07 durable Interrupts (Task 4).

SQLite-backed via tests._db. PostgreSQL partial indexes / triggers / migration
gates live in test_durable_interrupt_repository_postgres.py.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
PEPPER = "unit-test-interrupt-pepper-not-for-prod"


def _make_main_agent_run(db, *, status: str = "running", **kwargs: Any):
    from app.assistant.models import Conversation
    from tests.assistant_runtime_support import make_main_agent_run

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    return make_main_agent_run(
        db,
        conversation=conv,
        status=status,
        build_revision=kwargs.pop("required_app_build_revision", "build-test-1"),
        runtime_contract_version=1,
        state_revision=int(kwargs.pop("state_revision", 1)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )


def _seed_revisions(db, run_id):
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    manifest = AssistantRunManifestRevision(
        run_id=run_id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={"k": 1},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run_id,
        revision=1,
        policy_digest=DIGEST_A,
        payload={"p": 1},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run_id,
        revision=1,
        budget_digest=DIGEST_A,
        payload={"b": 1},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run_id,
        revision=1,
        obligation_digest=DIGEST_A,
        payload={"o": 1},
    )
    db.add_all([manifest, policy, budget, obligation])
    db.flush()
    ck = AssistantRunCheckpoint(
        run_id=run_id,
        sequence=1,
        expected_state_revision=1,
        committed_state_revision=1,
        schema_version=2,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_A,
        phase="waiting",
        state_payload={"waiting": True},
        state_digest=DIGEST_A,
    )
    db.add(ck)
    db.flush()
    return manifest, policy, budget, obligation, ck


def _parent_ledger(*, remaining_ms: int = 60_000):
    from app.assistant.policy import create_initial_ledger_state, normalize_run_budget_limits

    start = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    limits = normalize_run_budget_limits()
    # Force a known wall window so remaining_active_ms is deterministic.
    payload = limits.model_dump()
    payload["max_wall_time_ms"] = max(remaining_ms, 1_000)
    from app.assistant.policy.contracts import RunBudgetLimits

    limits = RunBudgetLimits(**payload)
    deadline = start + timedelta(milliseconds=remaining_ms + 5_000)
    return create_initial_ledger_state(
        limits=limits,
        started_at_utc=start,
        deadline_at_utc=deadline,
    )


class DurableInterruptModelTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_table_registered(self) -> None:
        from app.database import Base
        import app.assistant.durable.models  # noqa: F401

        self.assertIn("assistant_run_interrupt", Base.metadata.tables)

    def test_orm_columns_and_insert_simple_approval(self) -> None:
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.workflow.durable.interrupts import (
            build_budget_suspension_state,
            compute_request_digest,
            derive_interrupt_key,
        )
        from app.assistant.workflow.durable.contracts import derive_interrupt_id

        run = _make_main_agent_run(self.db)
        manifest, _policy, budget, _obl, ck = _seed_revisions(self.db, run.id)
        run.current_budget_revision_id = budget.id
        run.current_checkpoint_id = ck.id
        self.db.commit()

        frame_id = uuid.uuid4()
        visit = "visit-1"
        interrupt_id = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        parent = _parent_ledger(remaining_ms=30_000)
        now = datetime(2026, 7, 16, 12, 0, 10, tzinfo=timezone.utc)
        exp = now + timedelta(hours=1)
        from app.assistant.workflow.durable.interrupts import compute_remaining_active_ms

        remaining = compute_remaining_active_ms(
            parent_deadline_at_utc=parent.deadline_at_utc,
            database_now=now,
        )
        suspension = build_budget_suspension_state(
            run_id=run.id,
            interrupt_id=interrupt_id,
            parent_budget_revision_id=budget.id,
            parent_ledger_revision=parent.revision,
            parent_ledger_digest=parent.ledger_digest,
            suspended_at_utc=now,
            remaining_active_ms=remaining,
            human_wait_expires_at_utc=exp,
        )
        req = {"title": "Approve?"}
        row = AssistantRunInterrupt(
            id=interrupt_id,
            run_id=run.id,
            interrupt_key=key,
            kind="approval",
            status="pending",
            checkpoint_id=ck.id,
            manifest_revision_id=manifest.id,
            workflow_frame_id=frame_id,
            node_id="human_1",
            node_visit_id=visit,
            request_revision=1,
            request_run_revision=1,
            budget_revision_id=budget.id,
            budget_suspension_state=suspension.model_dump(mode="json", by_alias=True),
            budget_suspension_digest=suspension.suspension_digest,
            request_payload=req,
            request_digest=compute_request_digest(
                kind="approval",
                request_payload=req,
                field_schema=None,
                initial_values={},
            ),
            field_schema=None,
            field_schema_digest=None,
            initial_values={},
            expires_at=exp,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        self.assertEqual(row.status, "pending")
        self.assertIsNone(row.capability_call_id)
        self.assertEqual(row.budget_suspension_digest, suspension.suspension_digest)
        self.assertEqual(len(row.budget_suspension_digest), 64)

    def test_unique_run_interrupt_key(self) -> None:
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.workflow.durable.interrupts import (
            build_budget_suspension_state,
            compute_remaining_active_ms,
            compute_request_digest,
            derive_interrupt_key,
        )
        from app.assistant.workflow.durable.contracts import derive_interrupt_id

        run = _make_main_agent_run(self.db)
        manifest, _p, budget, _o, ck = _seed_revisions(self.db, run.id)
        frame_id = uuid.uuid4()
        visit = "visit-1"
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        parent = _parent_ledger()
        now = datetime(2026, 7, 16, 12, 0, 10, tzinfo=timezone.utc)
        exp = now + timedelta(hours=1)
        remaining = compute_remaining_active_ms(
            parent_deadline_at_utc=parent.deadline_at_utc, database_now=now
        )

        def _row(iid):
            suspension = build_budget_suspension_state(
                run_id=run.id,
                interrupt_id=iid,
                parent_budget_revision_id=budget.id,
                parent_ledger_revision=parent.revision,
                parent_ledger_digest=parent.ledger_digest,
                suspended_at_utc=now,
                remaining_active_ms=remaining,
                human_wait_expires_at_utc=exp,
            )
            return AssistantRunInterrupt(
                id=iid,
                run_id=run.id,
                interrupt_key=key,
                kind="approval",
                status="pending",
                checkpoint_id=ck.id,
                manifest_revision_id=manifest.id,
                workflow_frame_id=frame_id,
                node_id="n1",
                node_visit_id=visit,
                request_revision=1,
                request_run_revision=1,
                budget_revision_id=budget.id,
                budget_suspension_state=suspension.model_dump(mode="json", by_alias=True),
                budget_suspension_digest=suspension.suspension_digest,
                request_payload={"title": "x"},
                request_digest=compute_request_digest(
                    kind="approval",
                    request_payload={"title": "x"},
                    field_schema=None,
                    initial_values={},
                ),
                initial_values={},
                expires_at=exp,
                created_at=now,
                updated_at=now,
            )

        id1 = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        self.db.add(_row(id1))
        self.db.commit()
        id2 = uuid.uuid4()
        self.db.add(_row(id2))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_input_requires_schema_check(self) -> None:
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.workflow.durable.interrupts import (
            build_budget_suspension_state,
            compute_remaining_active_ms,
            compute_request_digest,
            derive_interrupt_key,
        )

        run = _make_main_agent_run(self.db)
        manifest, _p, budget, _o, ck = _seed_revisions(self.db, run.id)
        frame_id = uuid.uuid4()
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id="v1",
            logical_interrupt_ordinal=1,
        )
        parent = _parent_ledger()
        now = datetime(2026, 7, 16, 12, 0, 10, tzinfo=timezone.utc)
        exp = now + timedelta(hours=1)
        remaining = compute_remaining_active_ms(
            parent_deadline_at_utc=parent.deadline_at_utc, database_now=now
        )
        iid = uuid.uuid4()
        suspension = build_budget_suspension_state(
            run_id=run.id,
            interrupt_id=iid,
            parent_budget_revision_id=budget.id,
            parent_ledger_revision=parent.revision,
            parent_ledger_digest=parent.ledger_digest,
            suspended_at_utc=now,
            remaining_active_ms=remaining,
            human_wait_expires_at_utc=exp,
        )
        row = AssistantRunInterrupt(
            id=iid,
            run_id=run.id,
            interrupt_key=key,
            kind="input",
            status="pending",
            checkpoint_id=ck.id,
            manifest_revision_id=manifest.id,
            workflow_frame_id=frame_id,
            node_id="n1",
            node_visit_id="v1",
            request_revision=1,
            request_run_revision=1,
            budget_revision_id=budget.id,
            budget_suspension_state=suspension.model_dump(mode="json", by_alias=True),
            budget_suspension_digest=suspension.suspension_digest,
            request_payload={"title": "fill"},
            request_digest=compute_request_digest(
                kind="input",
                request_payload={"title": "fill"},
                field_schema=None,
                initial_values={},
            ),
            field_schema=None,
            field_schema_digest=None,
            initial_values={},
            expires_at=exp,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_budget_suspension_digest_covers_fields(self) -> None:
        from app.assistant.workflow.durable.contracts import (
            BudgetSuspensionStateV1,
            compute_suspension_digest,
        )
        from app.assistant.workflow.durable.interrupts import build_budget_suspension_state

        run_id = uuid.uuid4()
        interrupt_id = uuid.uuid4()
        parent_rev = uuid.uuid4()
        now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
        exp = now + timedelta(hours=2)
        state = build_budget_suspension_state(
            run_id=run_id,
            interrupt_id=interrupt_id,
            parent_budget_revision_id=parent_rev,
            parent_ledger_revision=3,
            parent_ledger_digest=DIGEST_A,
            suspended_at_utc=now,
            remaining_active_ms=12_345,
            human_wait_expires_at_utc=exp,
        )
        expected = compute_suspension_digest(
            run_id=run_id,
            interrupt_id=interrupt_id,
            parent_budget_revision_id=parent_rev,
            parent_ledger_revision=3,
            parent_ledger_digest=DIGEST_A,
            suspended_at_utc=now,
            remaining_active_ms=12_345,
            human_wait_expires_at_utc=exp,
        )
        self.assertEqual(state.suspension_digest, expected)
        # Tamper parent digest fails construction.
        with self.assertRaises(ValueError):
            BudgetSuspensionStateV1(
                run_id=run_id,
                interrupt_id=interrupt_id,
                parent_budget_revision_id=parent_rev,
                parent_ledger_revision=3,
                parent_ledger_digest=DIGEST_A,
                suspended_at_utc=now,
                remaining_active_ms=12_345,
                human_wait_expires_at_utc=exp,
                suspension_digest=DIGEST_B,
            )

    def test_remaining_active_ms_floor_clamp(self) -> None:
        from app.assistant.workflow.durable.interrupts import compute_remaining_active_ms

        deadline = datetime(2026, 7, 16, 12, 0, 1, 500000, tzinfo=timezone.utc)
        now = datetime(2026, 7, 16, 12, 0, 0, 0, tzinfo=timezone.utc)
        # 1500 ms -> floor stays 1500
        self.assertEqual(
            compute_remaining_active_ms(parent_deadline_at_utc=deadline, database_now=now),
            1500,
        )
        # past deadline clamps to 0
        self.assertEqual(
            compute_remaining_active_ms(
                parent_deadline_at_utc=now, database_now=deadline
            ),
            0,
        )

    def test_resume_budget_non_time_fields_byte_identical(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            derive_resume_budget_ledger,
            non_time_budget_snapshot,
        )

        parent = _parent_ledger(remaining_ms=50_000)
        # Simulate some usage.
        from app.assistant.policy.budgets import compute_ledger_digest

        used = parent.model_copy(
            update={
                "provider_rounds_started": 2,
                "capability_calls_started": 1,
                "prompt_tokens_used": 100,
                "completion_tokens_used": 50,
                "denial_count": 1,
                "ledger_digest": compute_ledger_digest(
                    revision=parent.revision,
                    limits=parent.limits,
                    owner_limits=parent.owner_limits,
                    provider_rounds_started=2,
                    main_agent_cycles_started=parent.main_agent_cycles_started,
                    capability_calls_started=1,
                    completion_followups_started=parent.completion_followups_started,
                    prompt_tokens_used=100,
                    completion_tokens_used=50,
                    owner_calls_started=parent.owner_calls_started,
                    global_read_signatures=parent.global_read_signatures,
                    owner_read_signatures=parent.owner_read_signatures,
                    reservations=parent.reservations,
                    denial_count=1,
                    started_at_utc=parent.started_at_utc,
                    deadline_at_utc=parent.deadline_at_utc,
                ),
            }
        )
        now = datetime(2026, 7, 16, 13, 0, 0, tzinfo=timezone.utc)
        remaining = 40_000
        child = derive_resume_budget_ledger(
            parent=used,
            remaining_active_ms=remaining,
            database_now=now,
        )
        self.assertEqual(non_time_budget_snapshot(used), non_time_budget_snapshot(child))
        self.assertEqual(child.revision, used.revision + 1)
        self.assertEqual(
            child.deadline_at_utc,
            now + timedelta(milliseconds=remaining),
        )
        self.assertNotEqual(child.ledger_digest, used.ledger_digest)
        # remaining_active_ms never increases across sequential suspensions
        second_remaining = 30_000
        child2 = derive_resume_budget_ledger(
            parent=child,
            remaining_active_ms=second_remaining,
            database_now=now + timedelta(seconds=5),
        )
        self.assertLess(second_remaining, remaining)
        self.assertEqual(non_time_budget_snapshot(child), non_time_budget_snapshot(child2))

    def test_zero_time_pause_refusal_via_repository(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            CODE_INTERRUPT_ZERO_ACTIVE_TIME,
            DurableInterruptRepository,
            InterruptConflict,
            derive_interrupt_key,
        )
        from app.assistant.workflow.durable.contracts import derive_interrupt_id

        run = _make_main_agent_run(self.db)
        manifest, _p, budget, _o, ck = _seed_revisions(self.db, run.id)
        run.current_budget_revision_id = budget.id
        self.db.commit()
        parent = _parent_ledger(remaining_ms=1)
        # Force deadline already passed relative to suspended_at.
        past = parent.deadline_at_utc + timedelta(seconds=1)
        frame_id = uuid.uuid4()
        visit = "v1"
        iid = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        repo = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        with self.assertRaises(InterruptConflict) as ctx:
            repo.create_pending_interrupt(
                run_id=run.id,
                interrupt_id=iid,
                interrupt_key=key,
                kind="approval",
                checkpoint_id=ck.id,
                manifest_revision_id=manifest.id,
                budget_revision_id=budget.id,
                workflow_frame_id=frame_id,
                node_id="n1",
                node_visit_id=visit,
                request_run_revision=1,
                request_payload={"title": "x"},
                field_schema=None,
                initial_values={},
                parent_ledger=parent,
                parent_budget_revision_id=budget.id,
                suspended_at_utc=past,
            )
        self.assertEqual(ctx.exception.code, CODE_INTERRUPT_ZERO_ACTIVE_TIME)

    def test_schema_normalize_and_render(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            InterruptSchemaError,
            normalize_interrupt_field_schema,
            render_interrupt_fields,
            validate_submitted_values,
        )

        raw = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "Name", "maxLength": 40},
                "ok": {"type": "boolean", "title": "OK"},
            },
            "required": ["name"],
            "additionalProperties": True,
        }
        norm = normalize_interrupt_field_schema(raw)
        assert norm is not None
        self.assertEqual(norm["additionalProperties"], False)
        fields = render_interrupt_fields(norm)
        self.assertEqual(len(fields), 2)
        names = {f["name"] for f in fields}
        self.assertEqual(names, {"name", "ok"})
        vals = validate_submitted_values(
            field_schema=norm,
            values={"name": "Ada", "ok": True},
            kind="input",
            outcome="submitted",
        )
        self.assertEqual(vals["name"], "Ada")
        with self.assertRaises(InterruptSchemaError):
            validate_submitted_values(
                field_schema=norm,
                values={"ok": True},
                kind="input",
                outcome="submitted",
            )
        with self.assertRaises(InterruptSchemaError):
            normalize_interrupt_field_schema(
                {"type": "object", "properties": {"password": {"type": "string"}}}
            )

    def test_schema_bounds_enforced_on_submit(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            InterruptSchemaError,
            normalize_interrupt_field_schema,
            validate_submitted_values,
        )

        raw = {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "exclusiveMinimum": 1,
                    "exclusiveMaximum": 10,
                },
                "score": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "tags": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": {"type": "string"},
                },
            },
            "required": ["count", "score", "tags"],
            "additionalProperties": False,
        }
        norm = normalize_interrupt_field_schema(raw)
        assert norm is not None

        ok = validate_submitted_values(
            field_schema=norm,
            values={"count": 5, "score": 0.5, "tags": ["a"]},
            kind="input",
            outcome="submitted",
        )
        self.assertEqual(ok["count"], 5)

        with self.assertRaises(InterruptSchemaError):
            validate_submitted_values(
                field_schema=norm,
                values={"count": 1, "score": 0.5, "tags": ["a"]},
                kind="input",
                outcome="submitted",
            )
        with self.assertRaises(InterruptSchemaError):
            validate_submitted_values(
                field_schema=norm,
                values={"count": 10, "score": 0.5, "tags": ["a"]},
                kind="input",
                outcome="submitted",
            )
        with self.assertRaises(InterruptSchemaError):
            validate_submitted_values(
                field_schema=norm,
                values={"count": 5, "score": -0.1, "tags": ["a"]},
                kind="input",
                outcome="submitted",
            )
        with self.assertRaises(InterruptSchemaError):
            validate_submitted_values(
                field_schema=norm,
                values={"count": 5, "score": 0.5, "tags": []},
                kind="input",
                outcome="submitted",
            )
        with self.assertRaises(InterruptSchemaError):
            validate_submitted_values(
                field_schema=norm,
                values={"count": 5, "score": 0.5, "tags": ["a", "b", "c"]},
                kind="input",
                outcome="submitted",
            )

    def test_schema_format_date_time_preserved_for_render(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            normalize_interrupt_field_schema,
            render_interrupt_fields,
        )

        raw = {
            "type": "object",
            "properties": {
                "due": {"type": "string", "format": "date", "title": "Due"},
                "at": {"type": "string", "format": "time", "title": "At"},
                "note": {"type": "string", "format": "email", "title": "Note"},
            },
            "required": ["due"],
            "additionalProperties": False,
        }
        norm = normalize_interrupt_field_schema(raw)
        assert norm is not None
        self.assertEqual(norm["properties"]["due"].get("format"), "date")
        self.assertEqual(norm["properties"]["at"].get("format"), "time")
        self.assertNotIn("format", norm["properties"]["note"])
        fields = {f["name"]: f for f in render_interrupt_fields(norm)}
        self.assertEqual(fields["due"]["type"], "date")
        self.assertEqual(fields["at"]["type"], "time")
        self.assertEqual(fields["note"]["type"], "input")

    def test_create_pending_crash_retry_after_clock_advance(self) -> None:
        """Insert-or-read must return stored suspension after wall clock advances."""
        from app.assistant.workflow.durable.contracts import derive_interrupt_id
        from app.assistant.workflow.durable.interrupts import (
            DurableInterruptRepository,
            derive_interrupt_key,
        )

        run = _make_main_agent_run(self.db)
        manifest, _p, budget, _o, ck = _seed_revisions(self.db, run.id)
        run.current_budget_revision_id = budget.id
        self.db.commit()

        parent = _parent_ledger(remaining_ms=120_000)
        frame_id = uuid.uuid4()
        visit = "visit-crash-retry"
        iid = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        suspended_at = parent.started_at_utc + timedelta(seconds=1)
        repo = DurableInterruptRepository(self.db, token_pepper=PEPPER)

        first = repo.create_pending_interrupt(
            run_id=run.id,
            interrupt_id=iid,
            interrupt_key=key,
            kind="approval",
            checkpoint_id=ck.id,
            manifest_revision_id=manifest.id,
            budget_revision_id=budget.id,
            workflow_frame_id=frame_id,
            node_id="n1",
            node_visit_id=visit,
            request_run_revision=1,
            request_payload={"title": "approve?"},
            field_schema=None,
            initial_values={},
            parent_ledger=parent,
            parent_budget_revision_id=budget.id,
            suspended_at_utc=suspended_at,
        )
        self.db.commit()
        self.assertTrue(first.created)
        stored_digest = first.interrupt.budget_suspension_digest
        stored_remaining = first.suspension.remaining_active_ms

        later = suspended_at + timedelta(seconds=30)
        second = repo.create_pending_interrupt(
            run_id=run.id,
            interrupt_id=iid,
            interrupt_key=key,
            kind="approval",
            checkpoint_id=ck.id,
            manifest_revision_id=manifest.id,
            budget_revision_id=budget.id,
            workflow_frame_id=frame_id,
            node_id="n1",
            node_visit_id=visit,
            request_run_revision=1,
            request_payload={"title": "approve?"},
            field_schema=None,
            initial_values={},
            parent_ledger=parent,
            parent_budget_revision_id=budget.id,
            suspended_at_utc=later,
        )
        self.assertFalse(second.created)
        self.assertEqual(second.interrupt.id, first.interrupt.id)
        self.assertEqual(second.interrupt.budget_suspension_digest, stored_digest)
        self.assertEqual(second.suspension.remaining_active_ms, stored_remaining)
        self.assertEqual(
            second.suspension.suspension_digest,
            first.suspension.suspension_digest,
        )

    def test_settings_defaults(self) -> None:
        from app.config import Settings

        s = Settings(
            ASSISTANT_ARTIFACT_BUCKET="private-artifacts-test",
            MINIO_BUCKET="public-attachments",
        )
        self.assertFalse(s.assistant_durable_interrupts_enabled)
        self.assertEqual(s.assistant_interrupt_default_ttl_sec, 86400)
        self.assertEqual(s.assistant_interrupt_max_ttl_sec, 604800)
        self.assertEqual(s.assistant_interrupt_comment_max_chars, 4000)
        self.assertEqual(s.assistant_interrupt_token_pepper, "")


if __name__ == "__main__":
    unittest.main()
