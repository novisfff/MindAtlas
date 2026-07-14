"""Plan 05 Task 5: Provider Completion Guard + Main Agent adapter."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.policy.completion import (
    ObligationLedgerCompletionGuard,
    build_completion_decision,
    build_completion_instruction,
    evaluate_completion,
)
from app.assistant.policy.obligations import (
    REASON_ALL_SATISFIED,
    REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS,
    REASON_COMPLETION_FOLLOWUP_LIMIT,
    REASON_OBLIGATIONS_PENDING_AT_FINALIZATION,
    REASON_SKILL_TERMINAL_PENDING,
    REASON_WAITING_WITHOUT_OBLIGATION,
    ObligationLedger,
    build_reserved_obligation,
    create_initial_obligation_ledger_state,
    pure_create_obligation,
)
from app.assistant.provider_loop.adapters.openai_chat import encode_openai_chat_messages
from app.assistant.provider_loop.contracts import (
    NoOpProviderCompletionGuard,
    ProviderCompletionDisposition,
    ProviderCompletionRequest,
    ProviderLoopPorts,
)
from app.assistant.provider_loop.messages import (
    ProviderCompletionInstructionMessage,
    ProviderContextUpdateMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderUserMessage,
    digest_provider_message,
    provider_message_payload,
    validate_provider_transcript,
)

RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SKILL_A = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_MANIFEST_DIGEST = "a" * 64
_GUARD_DIGEST = "b" * 64
_PROMPT_DIGEST = "c" * 64


def _req(
    *,
    text: str | None,
    finalization: bool = False,
    revision: int = 0,
) -> ProviderCompletionRequest:
    return ProviderCompletionRequest(
        manifest_revision=revision,
        manifest_digest=_MANIFEST_DIGEST,
        candidate_text=text,
        finalization_round=finalization,
    )


# ---------------------------------------------------------------------------
# Default-permissive guard (Plan 03 byte/behavior)
# ---------------------------------------------------------------------------


def test_default_guard_nonempty_completes() -> None:
    guard = NoOpProviderCompletionGuard()
    d = guard.evaluate(_req(text="Hello world"))
    assert d.action == "complete"
    assert d.reason_code == "natural_completion"
    assert d.instruction is None
    assert len(d.decision_digest) == 64


def test_default_guard_empty_fails_terminal_text_missing() -> None:
    guard = NoOpProviderCompletionGuard()
    d = guard.evaluate(_req(text="   "))
    assert d.action == "fail"
    assert d.reason_code == "terminal_text_missing"
    d2 = guard.evaluate(_req(text=None))
    assert d2.action == "fail"


def test_default_guard_finalization_same_rules() -> None:
    guard = NoOpProviderCompletionGuard()
    assert guard.evaluate(_req(text="ok", finalization=True)).action == "complete"
    assert guard.evaluate(_req(text="", finalization=True)).action == "fail"


# ---------------------------------------------------------------------------
# Import boundary
# ---------------------------------------------------------------------------


def test_provider_loop_modules_do_not_import_policy() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "assistant" / "provider_loop"
    forbidden_substrings = (
        "app.assistant.policy",
        "BudgetLedgerState",
        "ObligationLedgerState",
    )
    py_files = list(root.rglob("*.py"))
    assert py_files
    for path in py_files:
        if path.name == "__pycache__":
            continue
        source = path.read_text(encoding="utf-8")
        # AST-level import check
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.assistant.policy"), path
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("app.assistant.policy"), path
        # Protocol signatures must not name ledger state types.
        if path.name in {"contracts.py", "loop.py", "messages.py", "runtime.py"}:
            for needle in ("BudgetLedgerState", "ObligationLedgerState"):
                # Allow only in comments? Forbid entirely in these modules.
                # contracts/loop must not name them at all.
                if path.name in {"contracts.py", "loop.py"}:
                    assert needle not in source, f"{path} names {needle}"


def test_provider_completion_request_has_no_ledger_fields() -> None:
    fields = set(ProviderCompletionRequest.model_fields)
    assert "budget_ledger" not in fields
    assert "obligation_ledger" not in fields
    assert fields == {
        "manifest_revision",
        "manifest_digest",
        "candidate_text",
        "finalization_round",
    }


# ---------------------------------------------------------------------------
# Protected completion message
# ---------------------------------------------------------------------------


def test_runtime_completion_distinct_from_instruction_and_context() -> None:
    completion = ProviderCompletionInstructionMessage(
        locale="en",
        manifest_revision=1,
        manifest_digest=_MANIFEST_DIGEST,
        guard_state_digest=_GUARD_DIGEST,
        content="Complete pending obligations.",
    )
    instruction = ProviderRuntimeInstructionMessage(
        instruction_type="soft_finalization",
        locale="en",
        content="Tool budget is exhausted. Summarize.",
    )
    context = ProviderContextUpdateMessage(
        locale="en",
        manifest_revision=1,
        manifest_digest=_MANIFEST_DIGEST,
        prompt_build_digest=_PROMPT_DIGEST,
        content="Main agent manifest context.",
    )
    assert completion.role == "runtime_completion"
    assert instruction.role == "runtime_instruction"
    assert context.role == "runtime_context"
    assert completion.role != instruction.role != context.role

    p_c = provider_message_payload(completion)
    p_i = provider_message_payload(instruction)
    p_x = provider_message_payload(context)
    assert p_c["role"] == "runtime_completion"
    assert p_i["role"] == "runtime_instruction"
    assert p_x["role"] == "runtime_context"
    assert "guardStateDigest" in p_c
    assert "instructionType" in p_i
    assert "promptBuildDigest" in p_x

    # Digests differ.
    assert digest_provider_message(completion) != digest_provider_message(instruction)
    assert digest_provider_message(completion) != digest_provider_message(context)


def test_openai_encodes_runtime_completion_as_system() -> None:
    msg = ProviderCompletionInstructionMessage(
        locale="en",
        manifest_revision=0,
        manifest_digest=_MANIFEST_DIGEST,
        guard_state_digest=_GUARD_DIGEST,
        content="Pending: terminal_output",
    )
    encoded = encode_openai_chat_messages((msg,))
    assert encoded == [{"role": "system", "content": "Pending: terminal_output"}]


def test_pre_plan05_message_vectors_unchanged() -> None:
    """Fixed vectors for system/user/soft-finalization/context stay byte-identical."""
    system = ProviderSystemMessage(content="You are helpful.")
    user = ProviderUserMessage(content="Hi")
    soft = ProviderRuntimeInstructionMessage(
        instruction_type="soft_finalization",
        locale="en",
        content=(
            "Tool budget is exhausted. Summarize completed and incomplete work "
            "as a final answer. Do not call any tools."
        ),
    )
    ctx = ProviderContextUpdateMessage(
        locale="en",
        manifest_revision=2,
        manifest_digest=_MANIFEST_DIGEST,
        prompt_build_digest=_PROMPT_DIGEST,
        content="Skill context body",
    )
    # Payload shapes (plan 03/04 fixed fields).
    assert provider_message_payload(system) == {
        "role": "system",
        "content": "You are helpful.",
    }
    assert provider_message_payload(user) == {"role": "user", "content": "Hi"}
    assert provider_message_payload(soft) == {
        "role": "runtime_instruction",
        "instructionType": "soft_finalization",
        "locale": "en",
        "content": soft.content,
    }
    assert provider_message_payload(ctx) == {
        "role": "runtime_context",
        "contextType": "main_agent_manifest",
        "locale": "en",
        "manifestRevision": 2,
        "manifestDigest": _MANIFEST_DIGEST,
        "promptBuildDigest": _PROMPT_DIGEST,
        "content": "Skill context body",
    }
    # OpenAI encoding for pre-existing roles.
    encoded = encode_openai_chat_messages((system, user, soft, ctx))
    assert encoded == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hi"},
        {"role": "system", "content": soft.content},
        {"role": "system", "content": "Skill context body"},
    ]
    validate_provider_transcript((system, user, soft, ctx))


# ---------------------------------------------------------------------------
# Main Agent adapter
# ---------------------------------------------------------------------------


def test_main_agent_guard_completes_when_text_satisfies() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
    )
    d = guard.evaluate(_req(text="Final answer."))
    assert d.action == "complete"
    assert d.reason_code == REASON_ALL_SATISFIED
    assert d.instruction is None
    assert ledger.pending_blocking() == ()


def test_nonempty_text_cannot_override_pending_skill_terminal() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
    )
    d = guard.evaluate(_req(text="I claim we are done."))
    # Main agent terminal satisfied by text; skill still pending → continue.
    assert d.action == "continue"
    assert d.reason_code == REASON_SKILL_TERMINAL_PENDING
    assert d.instruction is not None
    assert d.instruction.role == "runtime_completion"
    assert "terminal_output" in d.instruction.content
    # Followup slot consumed.
    assert ledger.snapshot().followup_rounds_started == 1
    # Skill still pending.
    assert any(o.owner_kind == "skill_version" for o in ledger.pending_blocking())


def test_bounded_followup_rounds_then_fail() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
    )
    d1 = guard.evaluate(_req(text="still pending 1"))
    assert d1.action == "continue"
    d2 = guard.evaluate(_req(text="still pending 2"))
    assert d2.action == "continue"
    d3 = guard.evaluate(_req(text="still pending 3"))
    assert d3.action == "fail"
    assert d3.reason_code == REASON_COMPLETION_FOLLOWUP_LIMIT


def test_finalization_with_pending_non_text_fails() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
    )
    d = guard.evaluate(_req(text="finalization text", finalization=True))
    assert d.action == "fail"
    assert d.reason_code == REASON_OBLIGATIONS_PENDING_AT_FINALIZATION


def test_budget_exhausted_with_obligations() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
        can_continue_fn=lambda: False,
    )
    d = guard.evaluate(_req(text="cannot continue"))
    assert d.action == "fail"
    assert d.reason_code == REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS


def test_approval_pending_wait_action() -> None:
    state = create_initial_obligation_ledger_state()
    ob = build_reserved_obligation(
        run_id=RUN_ID,
        obligation_type="approval",
        owner_kind="main_agent",
        owner_id="main_agent",
    )
    state, _ = pure_create_obligation(state, ob)
    decision, _ = evaluate_completion(
        state,
        candidate_text="waiting for approval",
        finalization_round=False,
        can_continue=True,
        followup_budget_remaining=True,
        apply_text=True,
    )
    assert decision.action == "wait"
    assert decision.reason_code == "approval_pending"


def test_wait_without_obligation_protocol_error_projection() -> None:
    from app.assistant.policy.completion import project_wait_without_obligation_error

    d = project_wait_without_obligation_error()
    assert d.action == "fail"
    assert d.reason_code == REASON_WAITING_WITHOUT_OBLIGATION


def test_instruction_digest_deterministic() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    pending = ledger.pending_blocking()
    skill_only = [o for o in pending if o.owner_kind == "skill_version"]
    msg1 = build_completion_instruction(
        obligations=skill_only,
        locale="en",
        manifest_revision=0,
        manifest_digest=_MANIFEST_DIGEST,
        guard_state_digest=ledger.snapshot().ledger_digest,
    )
    msg2 = build_completion_instruction(
        obligations=skill_only,
        locale="en",
        manifest_revision=0,
        manifest_digest=_MANIFEST_DIGEST,
        guard_state_digest=ledger.snapshot().ledger_digest,
    )
    assert msg1.content == msg2.content
    assert msg1.guard_state_digest == msg2.guard_state_digest


def test_completion_decision_digest_stable() -> None:
    d1 = build_completion_decision(
        action="complete",
        reason_code=REASON_ALL_SATISFIED,
        blocking_obligation_ids=(),
    )
    d2 = build_completion_decision(
        action="complete",
        reason_code=REASON_ALL_SATISFIED,
        blocking_obligation_ids=(),
    )
    assert d1.decision_digest == d2.decision_digest
    expected = sha256_canonical_json(
        {
            "action": "complete",
            "reasonCode": REASON_ALL_SATISFIED,
            "blockingObligationIds": [],
            "instructionDigest": None,
        }
    )
    assert d1.decision_digest == expected


def test_provider_loop_ports_default_completion_guard() -> None:
    # Minimal ports construction is heavy; just check field default factory type.
    field = ProviderLoopPorts.__dataclass_fields__["completion_guard"]
    assert field.default_factory is NoOpProviderCompletionGuard


def test_budget_followup_fn_invoked_on_continue() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    calls: list[int] = []

    def _budget() -> None:
        calls.append(1)

    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
        budget_start_followup_fn=_budget,
    )
    d = guard.evaluate(_req(text="need more"))
    assert d.action == "continue"
    assert calls == [1]


def test_budget_followup_fn_raise_leaves_coherent_ledger() -> None:
    """Regression I2: budget raise must not consume followup after text apply."""
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    before = ledger.snapshot()
    assert before.followup_rounds_started == 0

    def _budget_raises() -> None:
        raise RuntimeError("budget denied")

    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=ledger,
        locale="en",
        max_completion_followup_rounds=2,
        budget_start_followup_fn=_budget_raises,
    )
    d = guard.evaluate(_req(text="need more"))
    assert d.action == "fail"
    assert d.reason_code == REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS
    assert d.instruction is None

    after = ledger.snapshot()
    # Followup counter must not advance on budget failure.
    assert after.followup_rounds_started == 0
    # Text evidence may satisfy Main Agent terminal; skill terminal stays pending.
    skill_pending = [
        o
        for o in after.obligations
        if o.owner_version_id == SKILL_A and o.status == "pending"
    ]
    assert skill_pending
    # No continue disposition means no partial "satisfied terminal + consumed followup".
    assert after.followup_rounds_started == before.followup_rounds_started


def test_direct_natural_answer_with_only_main_agent() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    guard = ObligationLedgerCompletionGuard(obligation_ledger=ledger)
    d = guard.evaluate(_req(text="42"))
    assert d.action == "complete"
    assert d.reason_code == REASON_ALL_SATISFIED


def test_artifact_pending_not_satisfied_by_text() -> None:
    state = create_initial_obligation_ledger_state()
    # Main agent terminal + artifact.
    from app.assistant.policy.obligations import build_main_agent_terminal_obligation

    main = build_main_agent_terminal_obligation(run_id=RUN_ID, revision=1)
    state, _ = pure_create_obligation(state, main)
    art = build_reserved_obligation(
        run_id=RUN_ID,
        obligation_type="required_artifact",
        owner_kind="main_agent",
        owner_id="main_agent",
        ordinal=1,
    )
    state, _ = pure_create_obligation(state, art)
    decision, new_state = evaluate_completion(
        state,
        candidate_text="here is prose only",
        finalization_round=False,
        can_continue=True,
        followup_budget_remaining=True,
    )
    assert decision.action == "continue"
    assert decision.reason_code == "artifact_pending"
    # Main agent satisfied; artifact remains.
    assert any(
        o.obligation_type == "required_artifact" and o.status == "pending"
        for o in new_state.obligations
    )


def test_finalization_budget_exhaustion_non_text_explicit_fail() -> None:
    state = create_initial_obligation_ledger_state()
    art = build_reserved_obligation(
        run_id=RUN_ID,
        obligation_type="required_artifact",
        owner_kind="main_agent",
        owner_id="main_agent",
    )
    state, _ = pure_create_obligation(state, art)
    decision, _ = evaluate_completion(
        state,
        candidate_text="done?",
        finalization_round=True,
        can_continue=False,
        followup_budget_remaining=False,
    )
    assert decision.action == "fail"
    assert decision.reason_code == REASON_OBLIGATIONS_PENDING_AT_FINALIZATION
