"""Plan 06 Task 8: ordered idempotent terminal memory finalizer.

Failures-first coverage for:
- Run/message/content-digest-idempotent L0 final output
- conflicting final digest
- L1 apply with last_applied_run_id
- multiple active Skill namespaces (native package+namespace)
- fact provenance
- crash before/after memory apply
- duplicate finalizer
- memory-provider failure (explicit failed, L0 preserved)
- ready_for_memory nonterminal public view + active unique lock
- terminal completion only with committed|failed memory outcome
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
BUILD = "build-test-1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_session():
    from tests._db import make_session

    return make_session()


def _seed_run_with_messages(
    db,
    *,
    status: str = "running",
    state_revision: int = 1,
    assistant_content: str = "",
    memory_commit_status: str = "pending",
    lease_owner: str = "w1",
    lease_generation: int = 1,
) -> tuple[Any, Any, Any, Any]:
    from app.assistant.models import Conversation, Message
    from tests.assistant_runtime_support import make_main_agent_run

    conv = Conversation(title=f"mem-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    user = Message(conversation_id=conv.id, role="user", content="hello user")
    assistant = Message(
        conversation_id=conv.id, role="assistant", content=assistant_content
    )
    db.add_all([user, assistant])
    db.flush()
    run = make_main_agent_run(
        db,
        conversation=conv,
        user_message=user,
        assistant_message=assistant,
        status=status,
        build_revision=BUILD,
        runtime_contract_version=1,
        state_revision=state_revision,
        memory_commit_status=memory_commit_status,
        lease_owner=lease_owner,
        lease_generation=lease_generation,
        lease_expires_at=_utcnow() + timedelta(hours=1),
        heartbeat_at=_utcnow(),
    )
    db.refresh(assistant)
    db.refresh(user)
    db.refresh(conv)
    return run, conv, user, assistant


def _lease(run, worker_id: str = "w1"):
    from app.assistant.durable.repository import LeaseToken

    return LeaseToken(
        run_id=run.id,
        worker_id=worker_id,
        lease_generation=int(run.lease_generation or 1),
    )


def _seed_ready_for_memory(db, run, *, expected_revision: int | None = None):
    """Advance run into internal ready_for_memory phase (status stays running)."""
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.repository import (
        DurableChildBundle,
        DurableRunRepository,
        EventSpec,
    )

    repo = DurableRunRepository(db)
    lease = _lease(run)
    rev = int(expected_revision if expected_revision is not None else run.state_revision)

    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run.id,
        revision=1,
        policy_digest=DIGEST_A,
        payload={},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run.id,
        revision=1,
        budget_digest=DIGEST_A,
        payload={},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run.id,
        revision=1,
        obligation_digest=DIGEST_A,
        payload={},
    )
    r0 = repo.commit_semantic(
        run_id=run.id,
        expected_revision=rev,
        lease=lease,
        children=DurableChildBundle(rows=[manifest, policy, budget, obligation]),
    )
    db.refresh(manifest)
    db.refresh(policy)
    db.refresh(budget)
    db.refresh(obligation)

    ck = AssistantRunCheckpoint(
        run_id=run.id,
        sequence=1,
        expected_state_revision=r0.state_revision,
        committed_state_revision=r0.state_revision + 1,
        schema_version=1,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_A,
        phase="ready_for_memory",
        state_payload={"phase": "ready_for_memory", "nextAction": {"kind": "memory"}},
        state_digest=DIGEST_C,
    )
    result = repo.enter_ready_for_memory(
        run_id=run.id,
        expected_revision=r0.state_revision,
        lease=lease,
        events=[
            EventSpec(
                event_key=f"memory.ready:{run.id}",
                event_name="memory.ready",
                payload={},
                visibility="internal",
            )
        ],
        children=DurableChildBundle(rows=[ck]),
    )
    db.refresh(run)
    return result


def _make_skill_package(db, *, name: str = "native-skill"):
    from app.assistant.skills.models import AssistantSkillPackage

    pkg = AssistantSkillPackage(
        canonical_name=name,
        display_name=name.title(),
        description="test package",
        migration_state="native",
        catalog_enabled=False,
        is_system=False,
    )
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    # Provenance version id is evidence only (not a required FK on facts_v2 JSON).
    version_id = uuid.uuid4()
    return pkg, version_id


class DurableMemoryL0FinalContentTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_digest_is_stable_for_same_content(self) -> None:
        from app.assistant.durable.memory import digest_final_content

        a = digest_final_content("hello final")
        b = digest_final_content("hello final")
        c = digest_final_content("hello final!")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 64)

    def test_apply_final_l0_writes_once_and_is_digest_idempotent(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryFinalizer,
            digest_final_content,
        )
        from app.assistant.models import Message

        run, _conv, _user, assistant = _seed_run_with_messages(self.db)
        finalizer = DurableMemoryFinalizer(self.db)
        content = "accepted final answer"
        digest = digest_final_content(content)

        msg1 = finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content=content,
            content_digest=digest,
        )
        self.assertEqual(msg1.content, content)

        # Same digest re-apply is a no-op (no second message).
        msg2 = finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content=content,
            content_digest=digest,
        )
        self.assertEqual(msg2.id, assistant.id)
        self.assertEqual(msg2.content, content)
        count = (
            self.db.query(Message)
            .filter(
                Message.conversation_id == run.conversation_id,
                Message.role == "assistant",
            )
            .count()
        )
        self.assertEqual(count, 1)

    def test_conflicting_final_digest_is_protocol_error(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            digest_final_content,
        )

        run, _conv, _user, assistant = _seed_run_with_messages(self.db)
        finalizer = DurableMemoryFinalizer(self.db)
        finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content="first final",
            content_digest=digest_final_content("first final"),
        )
        with self.assertRaises(DurableMemoryError) as ctx:
            finalizer.apply_final_l0_content(
                run_id=run.id,
                assistant_message_id=assistant.id,
                content="different final",
                content_digest=digest_final_content("different final"),
            )
        self.assertEqual(ctx.exception.code, "policy_state_protocol_error")

    def test_whitespace_only_existing_content_still_digest_protected(self) -> None:
        """Whitespace-only existing L0 must not be silently overwritten.

        ``existing.strip()`` used to skip digest checks for whitespace placeholders.
        Even whitespace content must conflict when digests differ.
        """
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            digest_final_content,
        )

        run, _conv, _user, assistant = _seed_run_with_messages(self.db)
        # Seed whitespace-only content outside the finalizer (legacy/buggy path).
        assistant.content = " "
        self.db.commit()
        self.db.refresh(assistant)

        finalizer = DurableMemoryFinalizer(self.db)
        with self.assertRaises(DurableMemoryError) as ctx:
            finalizer.apply_final_l0_content(
                run_id=run.id,
                assistant_message_id=assistant.id,
                content="real final answer",
                content_digest=digest_final_content("real final answer"),
            )
        self.assertEqual(ctx.exception.code, "policy_state_protocol_error")
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, " ")

    def test_wrong_message_id_is_protocol_error(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            digest_final_content,
        )

        run, _conv, _user, assistant = _seed_run_with_messages(self.db)
        finalizer = DurableMemoryFinalizer(self.db)
        with self.assertRaises(DurableMemoryError) as ctx:
            finalizer.apply_final_l0_content(
                run_id=run.id,
                assistant_message_id=uuid.uuid4(),
                content="x",
                content_digest=digest_final_content("x"),
            )
        self.assertEqual(ctx.exception.code, "policy_state_protocol_error")
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, "")

    def test_never_writes_provisional_or_cancelled_markers_as_final(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            digest_final_content,
        )

        run, _conv, _user, assistant = _seed_run_with_messages(self.db)
        finalizer = DurableMemoryFinalizer(self.db)
        for banned in (
            "",
            "   ",
            "[provisional]",
            "[waiting]",
            "[cancelled]",
            "[failed]",
            "[fallback-discarded]",
        ):
            with self.assertRaises(DurableMemoryError) as ctx:
                finalizer.apply_final_l0_content(
                    run_id=run.id,
                    assistant_message_id=assistant.id,
                    content=banned,
                    content_digest=digest_final_content(banned),
                )
            self.assertIn(ctx.exception.code, {
                "policy_state_protocol_error",
                "invalid_final_content",
            })
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, "")


class DurableMemoryNativeL2ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()
        self.pkg, self.version_id = _make_skill_package(self.db, name="pkg-a")
        from app.assistant.models import Conversation

        self.conv = Conversation(title="l2-native")
        self.db.add(self.conv)
        self.db.commit()
        self.db.refresh(self.conv)

    def tearDown(self) -> None:
        self.db.close()

    def test_legacy_name_apis_are_retired(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService

        legacy = AssistantMemoryService(self.db)
        legacy.upsert_l2_facts(self.conv.id, "legacy-skill", ["A", "B"])
        self.assertEqual(legacy.get_l2_facts(self.conv.id, "legacy-skill"), [])

    def test_native_package_namespace_apis(self) -> None:
        from app.assistant.durable.memory import DurableMemoryFinalizer
        from app.assistant.models import AssistantConversationSkillL2Memory

        finalizer = DurableMemoryFinalizer(self.db)
        observed = _utcnow()
        facts = [
            {
                "text": "prefers dark mode",
                "sourceSkillVersionId": str(self.version_id),
                "sourceRunId": str(uuid.uuid4()),
                "sourceCapabilityCallId": None,
                "observedAt": observed.isoformat(),
            }
        ]
        finalizer.upsert_l2_facts_v2(
            conversation_id=self.conv.id,
            skill_package_id=self.pkg.id,
            memory_namespace="default",
            skill_name=self.pkg.canonical_name,
            facts_v2=facts,
            last_applied_run_id=None,
        )
        loaded = finalizer.get_l2_facts_v2(
            conversation_id=self.conv.id,
            skill_package_id=self.pkg.id,
            memory_namespace="default",
        )
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["text"], "prefers dark mode")

        row = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == self.conv.id,
                AssistantConversationSkillL2Memory.skill_package_id == self.pkg.id,
                AssistantConversationSkillL2Memory.memory_namespace == "default",
            )
            .one()
        )
        # Legacy facts mirror text list for old readers.
        self.assertEqual(row.facts, ["prefers dark mode"])

    def test_multiple_namespaces_for_same_package_are_isolated(self) -> None:
        from app.assistant.durable.memory import DurableMemoryFinalizer

        finalizer = DurableMemoryFinalizer(self.db)
        observed = _utcnow().isoformat()
        run_id = str(uuid.uuid4())
        finalizer.upsert_l2_facts_v2(
            conversation_id=self.conv.id,
            skill_package_id=self.pkg.id,
            memory_namespace="default",
            skill_name=self.pkg.canonical_name,
            facts_v2=[
                {
                    "text": "ns-default",
                    "sourceSkillVersionId": str(self.version_id),
                    "sourceRunId": run_id,
                    "sourceCapabilityCallId": None,
                    "observedAt": observed,
                }
            ],
        )
        finalizer.upsert_l2_facts_v2(
            conversation_id=self.conv.id,
            skill_package_id=self.pkg.id,
            memory_namespace="project",
            skill_name=self.pkg.canonical_name,
            facts_v2=[
                {
                    "text": "ns-project",
                    "sourceSkillVersionId": str(self.version_id),
                    "sourceRunId": run_id,
                    "sourceCapabilityCallId": None,
                    "observedAt": observed,
                }
            ],
        )
        self.assertEqual(
            [f["text"] for f in finalizer.get_l2_facts_v2(
                self.conv.id, self.pkg.id, "default"
            )],
            ["ns-default"],
        )
        self.assertEqual(
            [f["text"] for f in finalizer.get_l2_facts_v2(
                self.conv.id, self.pkg.id, "project"
            )],
            ["ns-project"],
        )

    def test_empty_namespace_rejected(self) -> None:
        from app.assistant.durable.memory import DurableMemoryError, DurableMemoryFinalizer

        finalizer = DurableMemoryFinalizer(self.db)
        with self.assertRaises(DurableMemoryError):
            finalizer.upsert_l2_facts_v2(
                conversation_id=self.conv.id,
                skill_package_id=self.pkg.id,
                memory_namespace="  ",
                skill_name=self.pkg.canonical_name,
                facts_v2=[],
            )

    def test_fact_provenance_required(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            normalize_facts_v2,
        )

        with self.assertRaises(DurableMemoryError):
            normalize_facts_v2(
                [{"text": "missing provenance"}],
                default_run_id=uuid.uuid4(),
            )

        ok = normalize_facts_v2(
            [
                {
                    "text": "ok",
                    "sourceSkillVersionId": str(uuid.uuid4()),
                    "sourceRunId": str(uuid.uuid4()),
                    "sourceCapabilityCallId": None,
                    "observedAt": _utcnow().isoformat(),
                }
            ]
        )
        self.assertEqual(len(ok), 1)
        self.assertEqual(ok[0]["text"], "ok")


class DurableMemoryReadyAndFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_enter_ready_for_memory_persists_l0_and_stays_nonterminal(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryFinalizer,
            digest_final_content,
        )
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.durable.repository import (
            CODE_RUN_FINALIZING,
            DurableChildBundle,
            DurableRunConflict,
            DurableRunRepository,
            EventSpec,
        )
        from app.assistant.models import Message
        from app.assistant.service import AssistantService

        run, conv, _user, assistant = _seed_run_with_messages(self.db)
        repo = DurableRunRepository(self.db)
        lease = _lease(run)
        finalizer = DurableMemoryFinalizer(self.db)

        manifest = AssistantRunManifestRevision(
            run_id=run.id, revision=1, manifest_digest=DIGEST_A, schema_version=1, payload={}
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id, revision=1, policy_digest=DIGEST_A, payload={}
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id, revision=1, budget_digest=DIGEST_A, payload={}
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id, revision=1, obligation_digest=DIGEST_A, payload={}
        )
        r0 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=run.state_revision,
            lease=lease,
            children=DurableChildBundle(rows=[manifest, policy, budget, obligation]),
        )
        self.db.refresh(manifest)
        self.db.refresh(policy)
        self.db.refresh(budget)
        self.db.refresh(obligation)
        ck = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=r0.state_revision,
            committed_state_revision=r0.state_revision + 1,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_memory",
            state_payload={"phase": "ready_for_memory"},
            state_digest=DIGEST_C,
        )
        content = "user-visible final answer"
        result = finalizer.enter_ready_for_memory_with_final_content(
            run_id=run.id,
            expected_revision=r0.state_revision,
            lease=lease,
            final_content=content,
            content_digest=digest_final_content(content),
            events=[
                EventSpec(
                    event_key=f"memory.ready:{run.id}",
                    event_name="memory.ready",
                    payload={},
                    visibility="internal",
                )
            ],
            children=DurableChildBundle(rows=[ck]),
        )
        self.assertEqual(result.status, "running")
        self.db.refresh(run)
        self.db.refresh(assistant)
        self.assertEqual(run.status, "running")
        self.assertEqual(run.memory_commit_status, "pending")
        self.assertTrue(repo.is_ready_for_memory(run))
        self.assertEqual(assistant.content, content)

        # Public serialize exposes status only — never phase.
        public = AssistantService._serialize_run(run)
        self.assertEqual(public["status"], "running")
        self.assertNotIn("phase", public)
        self.assertNotIn("ready_for_memory", str(public.values()))

        # Stop blocked (finalizing fence).
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.request_stop(run_id=run.id, expected_revision=result.state_revision)
        self.assertEqual(ctx.exception.code, CODE_RUN_FINALIZING)

        # Active unique still blocks a later Run for the conversation.
        from app.assistant.models import AssistantChatRun
        from tests.assistant_runtime_support import seed_main_agent_runtime

        # Keep the second Run unflushed so this test observes the active-Run
        # uniqueness boundary at commit time, while still carrying a real
        # frozen Main-Agent runtime identity.
        frozen = seed_main_agent_runtime(self.db, build_revision=BUILD)
        later = AssistantChatRun(
            conversation_id=conv.id,
            status="queued",
            memory_commit_status="pending",
            **frozen.as_run_kwargs(),
        )
        self.db.add(later)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # No terminal SSE-style public event yet.
        from app.assistant.models import AssistantChatRunEvent

        terminal = (
            self.db.query(AssistantChatRunEvent)
            .filter(
                AssistantChatRunEvent.run_id == run.id,
                AssistantChatRunEvent.event_name == "run_status",
                AssistantChatRunEvent.visibility == "public",
            )
            .all()
        )
        for ev in terminal:
            payload = ev.payload or {}
            self.assertNotEqual(payload.get("status"), "completed")

        # Message still the single assistant row.
        self.assertEqual(
            self.db.query(Message)
            .filter(Message.conversation_id == conv.id, Message.role == "assistant")
            .count(),
            1,
        )

    def test_apply_prepared_l1_l2_in_one_transaction(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryFinalizer,
            PreparedL1Update,
            PreparedL2Update,
            PreparedMemorySet,
            digest_final_content,
        )
        from app.assistant.models import (
            AssistantConversationL1Memory,
            AssistantConversationSkillL2Memory,
        )
        from app.assistant.durable.repository import EventSpec

        run, conv, _user, assistant = _seed_run_with_messages(self.db)
        pkg, version_id = _make_skill_package(self.db, name=f"skill-{uuid.uuid4().hex[:6]}")
        rfm = _seed_ready_for_memory(self.db, run)
        finalizer = DurableMemoryFinalizer(self.db)
        content = "final for memory"
        finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content=content,
            content_digest=digest_final_content(content),
        )
        self.db.commit()

        observed = _utcnow()
        prepared = PreparedMemorySet(
            l1=PreparedL1Update(
                summary_text="summary after run",
                expected_last_applied_run_id=None,
            ),
            l2=(
                PreparedL2Update(
                    skill_package_id=pkg.id,
                    memory_namespace="default",
                    skill_name=pkg.canonical_name,
                    facts_v2=(
                        {
                            "text": "user likes terse answers",
                            "sourceSkillVersionId": str(version_id),
                            "sourceRunId": str(run.id),
                            "sourceCapabilityCallId": None,
                            "observedAt": observed.isoformat(),
                        },
                    ),
                    expected_version=None,
                ),
            ),
        )
        result = finalizer.apply_prepared_memory_and_finalize(
            run_id=run.id,
            expected_revision=rfm.state_revision,
            lease=_lease(run),
            prepared=prepared,
            events=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={
                        "status": "completed",
                        "memoryCommitStatus": "committed",
                    },
                    visibility="public",
                )
            ],
        )
        self.assertEqual(result.status, "completed")
        self.db.refresh(run)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.memory_commit_status, "committed")
        self.assertIsNotNone(run.memory_committed_at)
        self.assertIsNotNone(run.ended_at)

        l1 = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conv.id)
            .one()
        )
        self.assertEqual(l1.summary_text, "summary after run")
        self.assertEqual(l1.last_applied_run_id, run.id)

        l2 = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == conv.id,
                AssistantConversationSkillL2Memory.skill_package_id == pkg.id,
            )
            .one()
        )
        self.assertEqual(l2.last_applied_run_id, run.id)
        self.assertEqual(l2.facts, ["user likes terse answers"])
        self.assertEqual(l2.facts_v2[0]["text"], "user likes terse answers")
        self.assertEqual(int(l2.version), 1)

        # L0 preserved.
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, content)

    def test_crash_before_memory_apply_leaves_pending_and_l0(self) -> None:
        """Simulate crash after ready_for_memory before finalizer: no L1/L2 write."""
        from app.assistant.durable.memory import (
            DurableMemoryFinalizer,
            digest_final_content,
        )
        from app.assistant.models import (
            AssistantConversationL1Memory,
            AssistantConversationSkillL2Memory,
        )

        run, conv, _user, assistant = _seed_run_with_messages(self.db)
        _seed_ready_for_memory(self.db, run)
        finalizer = DurableMemoryFinalizer(self.db)
        content = "crash-before content"
        finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content=content,
            content_digest=digest_final_content(content),
        )
        self.db.commit()

        self.db.refresh(run)
        self.assertEqual(run.status, "running")
        self.assertEqual(run.memory_commit_status, "pending")
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, content)
        self.assertIsNone(
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conv.id)
            .first()
        )
        self.assertEqual(
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(AssistantConversationSkillL2Memory.conversation_id == conv.id)
            .count(),
            0,
        )

    def test_crash_after_memory_apply_is_idempotent_on_replay(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryFinalizer,
            PreparedL1Update,
            PreparedMemorySet,
            digest_final_content,
        )
        from app.assistant.durable.repository import EventSpec
        from app.assistant.models import AssistantConversationL1Memory

        run, conv, _user, assistant = _seed_run_with_messages(self.db)
        rfm = _seed_ready_for_memory(self.db, run)
        finalizer = DurableMemoryFinalizer(self.db)
        content = "crash-after content"
        finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content=content,
            content_digest=digest_final_content(content),
        )
        self.db.commit()

        prepared = PreparedMemorySet(
            l1=PreparedL1Update(
                summary_text="once only",
                expected_last_applied_run_id=None,
            ),
            l2=(),
        )
        first = finalizer.apply_prepared_memory_and_finalize(
            run_id=run.id,
            expected_revision=rfm.state_revision,
            lease=_lease(run),
            prepared=prepared,
            events=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={"status": "completed", "memoryCommitStatus": "committed"},
                    visibility="public",
                )
            ],
        )
        self.assertEqual(first.status, "completed")

        # Duplicate finalizer (crash after apply, worker retries).
        second = finalizer.apply_prepared_memory_and_finalize(
            run_id=run.id,
            expected_revision=first.state_revision,
            lease=_lease(run),
            prepared=prepared,
            events=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={"status": "completed", "memoryCommitStatus": "committed"},
                    visibility="public",
                )
            ],
        )
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.run.memory_commit_status, "committed")
        rows = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conv.id)
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].summary_text, "once only")
        self.assertEqual(rows[0].last_applied_run_id, run.id)
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, content)

    def test_duplicate_finalizer_when_already_failed_is_noop(self) -> None:
        from app.assistant.durable.memory import DurableMemoryFinalizer
        from app.assistant.durable.repository import EventSpec

        run, _conv, _user, assistant = _seed_run_with_messages(self.db)
        rfm = _seed_ready_for_memory(self.db, run)
        # Simulate L0 already accepted.
        assistant.content = "kept user response"
        self.db.commit()

        finalizer = DurableMemoryFinalizer(self.db)
        first = finalizer.finalize_memory_failed(
            run_id=run.id,
            expected_revision=rfm.state_revision,
            lease=_lease(run),
            diagnostic_code="memory_provider_error",
            events=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={"status": "completed", "memoryCommitStatus": "failed"},
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"memory.failed:{run.id}",
                    event_name="memory.failed",
                    payload={"code": "memory_provider_error"},
                    visibility="internal",
                ),
            ],
        )
        self.assertEqual(first.status, "completed")
        self.assertEqual(first.run.memory_commit_status, "failed")

        second = finalizer.finalize_memory_failed(
            run_id=run.id,
            expected_revision=first.state_revision,
            lease=_lease(run),
            diagnostic_code="memory_provider_error",
            events=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={"status": "completed", "memoryCommitStatus": "failed"},
                    visibility="public",
                )
            ],
        )
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.run.memory_commit_status, "failed")
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, "kept user response")

    def test_memory_provider_failure_does_not_erase_user_response(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryFinalizer,
            PreparedMemorySet,
            digest_final_content,
        )
        from app.assistant.models import (
            AssistantConversationL1Memory,
            AssistantConversationSkillL2Memory,
        )
        from app.assistant.durable.repository import EventSpec

        run, conv, _user, assistant = _seed_run_with_messages(self.db)
        rfm = _seed_ready_for_memory(self.db, run)
        finalizer = DurableMemoryFinalizer(self.db)
        content = "successful user-visible response"
        finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content=content,
            content_digest=digest_final_content(content),
        )
        self.db.commit()

        # High-level path: compute/provider failure → failed memory, L0 intact.
        result = finalizer.finalize_run_memory(
            run_id=run.id,
            expected_revision=rfm.state_revision,
            lease=_lease(run),
            prepared=None,
            compute_error=RuntimeError("provider down"),
            events_on_success=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={"status": "completed", "memoryCommitStatus": "committed"},
                    visibility="public",
                )
            ],
            events_on_failure=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={"status": "completed", "memoryCommitStatus": "failed"},
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"memory.failed:{run.id}",
                    event_name="memory.failed",
                    payload={"code": "memory_provider_error"},
                    visibility="internal",
                ),
            ],
        )
        self.assertEqual(result.status, "completed")
        self.db.refresh(run)
        self.assertEqual(run.memory_commit_status, "failed")
        self.db.refresh(assistant)
        self.assertEqual(assistant.content, content)
        self.assertIsNone(
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conv.id)
            .first()
        )
        self.assertEqual(
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(AssistantConversationSkillL2Memory.conversation_id == conv.id)
            .count(),
            0,
        )

    def test_stale_l1_expected_revision_rejects_partial_apply(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryError,
            DurableMemoryFinalizer,
            PreparedL1Update,
            PreparedMemorySet,
            digest_final_content,
        )
        from app.assistant.models import AssistantConversationL1Memory

        run, conv, _user, assistant = _seed_run_with_messages(self.db)
        rfm = _seed_ready_for_memory(self.db, run)
        # Pre-existing L1 from an earlier run.
        prior_run_id = uuid.uuid4()
        # Need a real run FK? last_applied_run_id FK to assistant_chat_run — use current run
        # with a different expected token by writing a row first.
        self.db.add(
            AssistantConversationL1Memory(
                conversation_id=conv.id,
                summary_text="old",
                last_applied_run_id=None,
            )
        )
        self.db.commit()

        finalizer = DurableMemoryFinalizer(self.db)
        finalizer.apply_final_l0_content(
            run_id=run.id,
            assistant_message_id=assistant.id,
            content="final",
            content_digest=digest_final_content("final"),
        )
        self.db.commit()

        # Wrong expected_last_applied_run_id should fail without partial L2.
        prepared = PreparedMemorySet(
            l1=PreparedL1Update(
                summary_text="new",
                expected_last_applied_run_id=prior_run_id,  # mismatch (row has None)
            ),
            l2=(),
        )
        with self.assertRaises(DurableMemoryError) as ctx:
            finalizer.apply_prepared_memory_and_finalize(
                run_id=run.id,
                expected_revision=rfm.state_revision,
                lease=_lease(run),
                prepared=prepared,
            )
        self.assertEqual(ctx.exception.code, "memory_revision_conflict")
        self.db.refresh(run)
        # Run still nonterminal pending — no partial finalize.
        self.assertEqual(run.status, "running")
        self.assertEqual(run.memory_commit_status, "pending")
        l1 = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conv.id)
            .one()
        )
        self.assertEqual(l1.summary_text, "old")

    def test_duplicate_package_namespace_in_prepared_set_rejected(self) -> None:
        from app.assistant.durable.memory import (
            DurableMemoryError,
            PreparedL2Update,
            PreparedMemorySet,
            validate_prepared_memory_set,
        )

        pkg_id = uuid.uuid4()
        prepared = PreparedMemorySet(
            l1=None,
            l2=(
                PreparedL2Update(
                    skill_package_id=pkg_id,
                    memory_namespace="default",
                    skill_name="x",
                    facts_v2=(),
                    expected_version=None,
                ),
                PreparedL2Update(
                    skill_package_id=pkg_id,
                    memory_namespace="default",
                    skill_name="x",
                    facts_v2=(),
                    expected_version=None,
                ),
            ),
        )
        with self.assertRaises(DurableMemoryError) as ctx:
            validate_prepared_memory_set(prepared)
        self.assertEqual(ctx.exception.code, "duplicate_memory_namespace")

    def test_terminal_completion_requires_memory_outcome(self) -> None:
        """Public completed event only appears with committed|failed memory status."""
        from app.assistant.durable.memory import DurableMemoryFinalizer
        from app.assistant.durable.repository import EventSpec
        from app.assistant.models import AssistantChatRunEvent

        run, _conv, _user, _assistant = _seed_run_with_messages(self.db)
        rfm = _seed_ready_for_memory(self.db, run)
        finalizer = DurableMemoryFinalizer(self.db)

        # Before finalize: no public completed.
        public_before = (
            self.db.query(AssistantChatRunEvent)
            .filter(
                AssistantChatRunEvent.run_id == run.id,
                AssistantChatRunEvent.visibility == "public",
            )
            .all()
        )
        for ev in public_before:
            self.assertNotEqual((ev.payload or {}).get("status"), "completed")

        result = finalizer.finalize_memory_failed(
            run_id=run.id,
            expected_revision=rfm.state_revision,
            lease=_lease(run),
            diagnostic_code="test",
            events=[
                EventSpec(
                    event_key=f"run.completed:{run.id}",
                    event_name="run_status",
                    payload={
                        "status": "completed",
                        "memoryCommitStatus": "failed",
                    },
                    visibility="public",
                )
            ],
        )
        self.assertEqual(result.status, "completed")
        self.assertIn(result.run.memory_commit_status, {"committed", "failed"})

        public_after = (
            self.db.query(AssistantChatRunEvent)
            .filter(
                AssistantChatRunEvent.run_id == run.id,
                AssistantChatRunEvent.event_name == "run_status",
                AssistantChatRunEvent.visibility == "public",
            )
            .all()
        )
        self.assertTrue(public_after)
        for ev in public_after:
            payload = ev.payload or {}
            if payload.get("status") == "completed":
                self.assertIn(payload.get("memoryCommitStatus"), {"committed", "failed"})


class DurableMemoryLastAppliedGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_last_applied_run_id_skips_reapply_for_same_run(self) -> None:
        from app.assistant.durable.memory import DurableMemoryFinalizer
        from app.assistant.models import AssistantConversationL1Memory

        run, conv, _user, _assistant = _seed_run_with_messages(
            self.db, status="completed", memory_commit_status="committed"
        )
        row = AssistantConversationL1Memory(
            conversation_id=conv.id,
            summary_text="already applied",
            last_applied_run_id=run.id,
        )
        self.db.add(row)
        self.db.commit()

        finalizer = DurableMemoryFinalizer(self.db)
        applied = finalizer.apply_l1_if_needed(
            conversation_id=conv.id,
            run_id=run.id,
            summary_text="should not overwrite",
            expected_last_applied_run_id=run.id,
        )
        self.assertFalse(applied)
        self.db.refresh(row)
        self.assertEqual(row.summary_text, "already applied")


if __name__ == "__main__":
    unittest.main()
