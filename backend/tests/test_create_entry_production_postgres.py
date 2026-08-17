"""Execution-boundary qualification for the Provider create_entry declaration.

These tests intentionally invoke the declaration outside the capability
gateway.  A direct Python call must be incapable of acquiring a Session or
performing a legacy tool write.
"""

from __future__ import annotations

import ast
import hashlib
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_POSTGRES_REQUIRED = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for PostgreSQL write proof",
)


def _postgres_url() -> str:
    if _POSTGRES_URL.startswith("postgresql://"):
        return _POSTGRES_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    return _POSTGRES_URL


@contextmanager
def _current_metadata_database():
    from app.database import Base
    from app.model_registry import load_all_live_models

    load_all_live_models()
    schema = f"task3_create_entry_{uuid.uuid4().hex}"
    admin = create_engine(_postgres_url(), future=True, pool_pre_ping=True)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        _postgres_url(),
        future=True,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _seed_postgres_call(factory):  # noqa: ANN001
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunBudgetRevision,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.policy import (
        create_initial_ledger_state,
        create_initial_obligation_ledger_state,
        normalize_run_budget_limits,
    )
    from app.entry_type.models import EntryType
    from tests.assistant_runtime_support import make_main_agent_run

    db = factory()
    run = make_main_agent_run(
        db,
        status="running",
        state_revision=1,
        lease_owner="worker-1",
        lease_generation=1,
        commit=False,
    )
    entry_type = EntryType(
        code="KNOWLEDGE",
        name="Knowledge",
        color="#1",
        graph_enabled=True,
        ai_enabled=True,
        enabled=True,
    )
    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=1,
        manifest_digest="a" * 64,
        schema_version=1,
        payload={},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run.id,
        revision=1,
        policy_digest="b" * 64,
        payload={},
    )
    started = datetime.now(timezone.utc)
    budget_state = create_initial_ledger_state(
        limits=normalize_run_budget_limits(),
        started_at_utc=started,
        deadline_at_utc=started + timedelta(minutes=2),
    )
    obligation_state = create_initial_obligation_ledger_state()
    budget = AssistantRunBudgetRevision(
        run_id=run.id,
        revision=1,
        budget_digest=budget_state.ledger_digest,
        payload=budget_state.model_dump(mode="json", by_alias=True),
    )
    obligation = AssistantRunObligationRevision(
        run_id=run.id,
        revision=1,
        obligation_digest=obligation_state.ledger_digest,
        payload=obligation_state.model_dump(mode="json", by_alias=True),
    )
    payload = b"{}"
    input_artifact = AssistantRunArtifact(
        run_id=run.id,
        kind="capability_call_input",
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(payload),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        inline_bytes=payload,
        metadata_json={"contractVersion": 1},
    )
    db.add_all([entry_type, manifest, policy, budget, obligation, input_artifact])
    db.flush()
    run.current_manifest_revision_id = manifest.id
    run.current_policy_revision_id = policy.id
    run.current_budget_revision_id = budget.id
    run.current_obligation_revision_id = obligation.id
    run.deadline_at = budget_state.deadline_at_utc
    call = AssistantCapabilityCall(
        run_id=run.id,
        manifest_revision_id=manifest.id,
        provider_tool_call_id="postgres-local-race",
        logical_call_key="provider:postgres-local-race",
        owner_kind="main_agent",
        capability_type="tool",
        domain_key="create_entry",
        descriptor_digest="c" * 64,
        authorization_digest="d" * 64,
        input_artifact_id=input_artifact.id,
        input_digest=hashlib.sha256(payload).hexdigest(),
        side_effect_class="write_local",
        execution_mode="local_transactional",
        idempotency_key="e" * 64,
        status="authorized",
        state_revision=1,
        attempt_count=0,
    )
    db.add(call)
    db.commit()
    return run.id, call.id, entry_type.id


def _declaration_function():
    from app.assistant.tools import create_entry

    return getattr(create_entry, "func", create_entry)


def _database_effect_snapshot(db):  # noqa: ANN001
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.entry.models import Entry

    return {
        "calls": db.query(AssistantCapabilityCall).count(),
        "entries": db.query(Entry).count(),
        "new": tuple(sorted(type(item).__name__ for item in db.new)),
    }


class _CommitForbiddenSession:
    def commit(self) -> None:
        raise AssertionError("the Provider declaration must not commit")


def test_gateway_required_decorated_create_entry_cannot_write_outside_gateway():
    from app.assistant.capability_calls.create_entry_declaration import (
        CapabilityGatewayRequired,
    )
    from app.assistant.tools._context import reset_current_db, set_current_db

    db = make_session()
    token = set_current_db(_CommitForbiddenSession())
    try:
        before = _database_effect_snapshot(db)
        with pytest.raises(CapabilityGatewayRequired) as exc:
            _declaration_function()(title="gateway boundary", content="must not write")
        assert exc.value.safe_code == "capability_gateway_required"
        assert _database_effect_snapshot(db) == before
    finally:
        reset_current_db(token)
        db.close()


def test_verified_gateway_invocation_returns_normalized_nonwriting_proposal():
    from app.assistant.capability_calls.create_entry_declaration import (
        _gateway_invocation_for_capability_adapter,
    )

    proposal = _declaration_function()(
        title="  Gateway title  ",
        content="  Gateway body  ",
        tags=[" alpha ", "", "beta"],
        _gateway_invocation=_gateway_invocation_for_capability_adapter(),
    )

    assert proposal.model_dump(mode="json") == {
        "title": "Gateway title",
        "summary": None,
        "content": "Gateway body",
        "type_code": None,
        "tags": ["alpha", "beta"],
        "time_mode": None,
        "time_at": None,
        "time_from": None,
        "time_to": None,
    }


def test_gateway_injected_marker_survives_tool_argument_validation():
    from app.assistant.capability_calls.create_entry_declaration import (
        _gateway_invocation_for_capability_adapter,
    )
    from app.assistant.tools import create_entry
    from app.assistant.workflow.engine.runtime_helpers import wrap_tool_with_db

    db = make_session()
    try:
        proposal = wrap_tool_with_db(create_entry, db.get_bind())(
            title="adapter title",
            content="adapter body",
            _gateway_invocation=_gateway_invocation_for_capability_adapter(),
        )
    finally:
        db.close()

    assert proposal.title == "adapter title"
    assert proposal.content == "adapter body"


def test_provider_json_cannot_forge_gateway_invocation_or_expose_it_in_schema():
    from app.assistant.capability_calls.create_entry_declaration import (
        CapabilityGatewayInvocation,
        CapabilityGatewayRequired,
        CreateEntryCapabilityInput,
    )
    from app.assistant.tools import create_entry

    schema = CreateEntryCapabilityInput.model_json_schema()
    assert "_gateway_invocation" not in schema["properties"]
    assert "_gateway_invocation" not in create_entry.args_schema.model_json_schema()[
        "properties"
    ]
    with pytest.raises(Exception):
        CreateEntryCapabilityInput.model_validate(
            {
                "title": "forged",
                "content": "payload",
                "_gateway_invocation": {"verified": True},
            }
        )
    from app.assistant.workflow.engine.runtime_helpers import coerce_tool_args

    with pytest.raises(Exception):
        coerce_tool_args(
            create_entry,
            {
                "title": "forged",
                "content": "payload",
                "_gateway_invocation": {"verified": True},
            },
        )
    with pytest.raises(CapabilityGatewayRequired):
        _declaration_function()(
            title="forged",
            content="payload",
            _gateway_invocation=CapabilityGatewayInvocation(object()),
        )


def test_local_adapter_has_no_provider_or_committing_service_dependency():
    from app.assistant.capability_calls import local_write

    tree = ast.parse(Path(local_write.__file__).read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "app.assistant.tools.entry_tools" not in imports
    assert "create" not in calls
    assert "commit" not in calls
    assert "create_in_uow" in calls


@_POSTGRES_REQUIRED
def test_postgres_two_sessions_converge_to_one_entry_and_human_create_stays_unlinked():
    from app.entry.models import Entry, TimeMode
    from app.entry.schemas import EntryRequest
    from app.entry.service import EntryService

    with _current_metadata_database() as engine:
        factory = sessionmaker(
            bind=engine,
            autoflush=True,
            expire_on_commit=False,
            future=True,
        )
        _run_id, call_id, entry_type_id = _seed_postgres_call(factory)
        first = factory()
        second = factory()
        request = EntryRequest(
            title="postgres race",
            summary=None,
            content="one body",
            type_id=entry_type_id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        winner = EntryService(first).create_in_uow(
            request,
            source_capability_call_id=call_id,
        )
        first.flush()

        loser_started = threading.Event()
        loser_result = {}

        def _loser() -> None:
            try:
                loser_started.set()
                replay = EntryService(second).create_in_uow(
                    request,
                    source_capability_call_id=call_id,
                )
                second.commit()
                loser_result["entry_id"] = replay.id
            except BaseException as exc:  # pragma: no cover - surfaced below
                loser_result["error"] = exc

        thread = threading.Thread(target=_loser)
        thread.start()
        assert loser_started.wait(5)
        first.commit()
        thread.join(15)
        assert not thread.is_alive()
        assert "error" not in loser_result, loser_result.get("error")
        assert loser_result["entry_id"] == winner.id

        check = factory()
        try:
            assert (
                check.query(Entry)
                .filter(Entry.source_capability_call_id == call_id)
                .count()
                == 1
            )
            human = EntryService(check).create_in_uow(
                request.model_copy(update={"title": "human entry"}),
                source_capability_call_id=None,
            )
            check.commit()
            assert human.source_capability_call_id is None
            assert check.get(Entry, human.id).source_capability_call_id is None
        finally:
            check.close()
            first.close()
            second.close()


@_POSTGRES_REQUIRED
def test_postgres_real_aggregate_settlement_bundle_and_recovery():
    """Run the production aggregate against PostgreSQL across its commit faults."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from sqlalchemy.orm import sessionmaker

    from tests.test_capability_call_local_transaction import (
        LocalTransactionalGoldenPathTests,
    )

    with _current_metadata_database() as engine:
        factory = sessionmaker(
            bind=engine,
            autoflush=True,
            expire_on_commit=False,
            future=True,
        )
        db = factory()
        class _PostgresTestLock:
            def acquire(self, _db):
                return None

        class _PostgresTestGuard:
            lock_port = _PostgresTestLock()
            runtime_closure_provider = staticmethod(lambda _run: object())

            @staticmethod
            def _allowed():
                return SimpleNamespace(allowed=True, reason_code=None)

            def evaluate_new_proposal_locked(self, **_kwargs):
                return self._allowed()

            def evaluate_post_approval_locked(self, **_kwargs):
                return self._allowed()

        guard = _PostgresTestGuard()
        case = LocalTransactionalGoldenPathTests(
            "test_aggregate_local_dispatch_commits_entry_attempt_and_result_together"
        )
        with (
            patch("tests._db.make_session", return_value=db),
            patch("tests._db.allowing_test_write_guard", return_value=guard),
        ):
            case.setUp()
            try:
                case.test_aggregate_local_dispatch_commits_entry_attempt_and_result_together()
            finally:
                case.tearDown()

    # Exercise the post-commit checkpoint-observation boundary in a fresh
    # isolated schema so the fixture's deterministic EntryType identity cannot
    # collide with the first aggregate run.
    with _current_metadata_database() as second_engine:
        second_factory = sessionmaker(
            bind=second_engine,
            autoflush=True,
            expire_on_commit=False,
            future=True,
        )
        db = second_factory()
        case = LocalTransactionalGoldenPathTests(
            "test_after_checkpoint_observation_fault_recovers_committed_local_bundle"
        )
        with (
            patch("tests._db.make_session", return_value=db),
            patch("tests._db.allowing_test_write_guard", return_value=guard),
        ):
            case.setUp()
            try:
                case.test_after_checkpoint_observation_fault_recovers_committed_local_bundle()
            finally:
                case.tearDown()
