"""Plan 03 Task 1: provider message contracts and transcript validators."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    ArtifactRef,
    CapabilityError,
    CapabilityMetrics,
    CapabilityResult,
    ContinuationRef,
    cancelled_result,
    completed_result,
    failed_result,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderToolCall,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    ProviderUserMessage,
    digest_arguments,
    digest_provider_message,
    digest_provider_transcript,
    project_tool_result_envelope,
    seal_cancelled_continuation,
    validate_provider_transcript,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_1 = "1" * 64
DIGEST_2 = "2" * 64


def _metrics() -> CapabilityMetrics:
    return CapabilityMetrics(
        duration_ms=1.0,
        adapter_duration_ms=None,
        input_bytes=0,
        output_bytes=0,
    )


def _call(
    *,
    call_id: str = "call-1",
    call_index: int = 0,
    provider_alias: str = "search_entries",
    domain_key: str = "tools.search",
    arguments: dict[str, Any] | None = None,
    binding_contract_digest: str = DIGEST_A,
    descriptor_digest: str = DIGEST_B,
    behavior_digest: str = DIGEST_C,
    classification_revision: str = "plan02-v1",
    classification_ruleset_digest: str = DIGEST_D,
    manifest_revision: int = 2,
    manifest_digest: str = DIGEST_E,
    surface_digest: str = DIGEST_F,
) -> ProviderToolCall:
    args = {"query": "hello"} if arguments is None else arguments
    return ProviderToolCall(
        call_id=call_id,
        call_index=call_index,
        provider_alias=provider_alias,
        domain_key=domain_key,
        arguments=args,
        arguments_digest=digest_arguments(args),
        binding_contract_digest=binding_contract_digest,
        descriptor_digest=descriptor_digest,
        behavior_digest=behavior_digest,
        classification_revision=classification_revision,
        classification_ruleset_digest=classification_ruleset_digest,
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        surface_digest=surface_digest,
    )


def _completed_envelope(*, domain_key: str = "tools.search") -> ProviderToolResultEnvelope:
    return ProviderToolResultEnvelope(
        status="completed",
        domain_key=domain_key,
        user_text="ok",
        structured_output={"ok": True},
        terminal_output=True,
        needs_followup=False,
        error=None,
        artifact_refs=(),
    )


def test_valid_role_specific_messages() -> None:
    system = ProviderSystemMessage(content="sys")
    user = ProviderUserMessage(content="hi")
    assistant = ProviderAssistantMessage(content="thinking", tool_calls=(_call(),))
    tool = ProviderToolMessage(
        call_id="call-1",
        provider_alias="search_entries",
        content=_completed_envelope(),
    )
    runtime = ProviderRuntimeInstructionMessage(
        instruction_type="soft_finalization",
        locale="en",
        content="Summarize without tools",
    )
    assert system.role == "system"
    assert user.role == "user"
    assert assistant.role == "assistant"
    assert tool.role == "tool"
    assert runtime.role == "runtime_instruction"


def test_tool_calls_only_on_assistant() -> None:
    with pytest.raises(ValidationError):
        ProviderUserMessage.model_validate(
            {"role": "user", "content": "x", "tool_calls": [_call().model_dump()]}
        )
    with pytest.raises(ValidationError):
        ProviderSystemMessage.model_validate(
            {"role": "system", "content": "x", "tool_calls": [_call().model_dump()]}
        )


def test_duplicate_and_non_contiguous_call_indexes_rejected() -> None:
    with pytest.raises(ValidationError):
        ProviderAssistantMessage(
            content=None,
            tool_calls=(
                _call(call_id="a", call_index=0),
                _call(call_id="b", call_index=2),
            ),
        )
    with pytest.raises(ValidationError):
        ProviderAssistantMessage(
            content=None,
            tool_calls=(
                _call(call_id="a", call_index=0),
                _call(call_id="a", call_index=1),
            ),
        )


def test_empty_and_invalid_aliases_rejected() -> None:
    with pytest.raises(ValidationError):
        _call(provider_alias="")
    with pytest.raises(ValidationError):
        _call(provider_alias="bad alias!")
    with pytest.raises(ValidationError):
        _call(provider_alias="x" * 65)


def test_json_only_arguments_and_stable_digests() -> None:
    left = _call(arguments={"b": 1, "a": 2})
    right = _call(arguments={"a": 2, "b": 1})
    assert left.arguments_digest == right.arguments_digest
    assert digest_provider_message(
        ProviderAssistantMessage(content=None, tool_calls=(left,))
    ) == digest_provider_message(
        ProviderAssistantMessage(content=None, tool_calls=(right,))
    )
    with pytest.raises(ValidationError):
        ProviderToolCall(
            call_id="c1",
            call_index=0,
            provider_alias="search_entries",
            domain_key="tools.search",
            arguments={"x": 1},
            arguments_digest="0" * 64,
            binding_contract_digest=DIGEST_A,
            descriptor_digest=DIGEST_B,
            behavior_digest=DIGEST_C,
            classification_revision="plan02-v1",
            classification_ruleset_digest=DIGEST_D,
            manifest_revision=1,
            manifest_digest=DIGEST_E,
            surface_digest=DIGEST_F,
        )
    with pytest.raises((TypeError, ValidationError, ValueError)):
        _call(arguments={"bad": {1, 2, 3}})  # type: ignore[dict-item]


def test_descriptor_fields_participate_in_digests_and_reject_tampering() -> None:
    base = _call(descriptor_digest=DIGEST_B, behavior_digest=DIGEST_C)
    tampered = _call(descriptor_digest=DIGEST_1, behavior_digest=DIGEST_C)
    assert digest_provider_message(
        ProviderAssistantMessage(content=None, tool_calls=(base,))
    ) != digest_provider_message(
        ProviderAssistantMessage(content=None, tool_calls=(tampered,))
    )
    ruleset_tampered = _call(classification_ruleset_digest=DIGEST_2)
    assert digest_provider_message(
        ProviderAssistantMessage(content=None, tool_calls=(base,))
    ) != digest_provider_message(
        ProviderAssistantMessage(content=None, tool_calls=(ruleset_tampered,))
    )


def test_assistant_text_plus_calls_retained() -> None:
    msg = ProviderAssistantMessage(content="I will search", tool_calls=(_call(),))
    assert msg.content == "I will search"
    assert len(msg.tool_calls) == 1
    # Content participates in digest.
    other = ProviderAssistantMessage(content="other", tool_calls=(_call(),))
    assert digest_provider_message(msg) != digest_provider_message(other)


def test_no_arbitrary_provider_sdk_type_accepted() -> None:
    class FakeChunk:
        def __init__(self) -> None:
            self.role = "assistant"
            self.content = "x"

    with pytest.raises((TypeError, ValidationError)):
        ProviderAssistantMessage.model_validate(FakeChunk())  # type: ignore[arg-type]


def test_runtime_instruction_cannot_be_user_message() -> None:
    runtime = ProviderRuntimeInstructionMessage(
        instruction_type="soft_finalization",
        locale="zh",
        content="请总结已完成工作，不要调用工具",
    )
    assert runtime.role != "user"
    with pytest.raises(ValidationError):
        ProviderUserMessage.model_validate(
            {
                "role": "user",
                "instruction_type": "soft_finalization",
                "locale": "zh",
                "content": "x",
            }
        )
    # Never final text: transcript digest keeps role distinct from assistant final text.
    user = ProviderUserMessage(content=runtime.content)
    assert digest_provider_message(runtime) != digest_provider_message(user)


def test_tool_result_envelope_is_safe_projection() -> None:
    result = completed_result(
        user_text="done",
        structured_output={"count": 1},
        artifact_refs=(
            ArtifactRef(
                artifact_id="a1",
                media_type="text/plain",
                content_digest=DIGEST_A,
            ),
        ),
        metrics=_metrics(),
        terminal_output=True,
        needs_followup=False,
    )
    envelope = project_tool_result_envelope(domain_key="tools.search", result=result)
    assert envelope.status == "completed"
    assert envelope.user_text == "done"
    assert envelope.structured_output == {"count": 1}
    assert envelope.artifact_refs[0].artifact_id == "a1"
    assert envelope.error is None

    failed = failed_result(
        error=CapabilityError(
            error_type="execution_failed",
            safe_code="execution_failed",
            safe_message="failed safely",
            retry_disposition="model_may_continue",
        ),
        metrics=_metrics(),
    )
    failed_env = project_tool_result_envelope(domain_key="tools.search", result=failed)
    assert failed_env.status == "failed"
    assert failed_env.error is not None

    cancelled = cancelled_result(metrics=_metrics(), call_id="call-1")
    cancelled_env = project_tool_result_envelope(domain_key="tools.search", result=cancelled)
    assert cancelled_env.status == "cancelled"

    waiting = CapabilityResult(
        status="waiting",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=ContinuationRef(
            continuation_type="human_approval",
            contract_version=1,
            reference_id="c1",
            payload_digest=DIGEST_A,
        ),
        terminal_output=False,
        needs_followup=True,
        error=None,
        metrics=_metrics(),
    )
    with pytest.raises(ValueError, match="waiting"):
        project_tool_result_envelope(domain_key="tools.search", result=waiting)


def test_transcript_direct_text() -> None:
    messages = (
        ProviderSystemMessage(content="sys"),
        ProviderUserMessage(content="hi"),
        ProviderAssistantMessage(content="hello", tool_calls=()),
    )
    validate_provider_transcript(messages)
    digest = digest_provider_transcript(messages)
    assert digest == digest_provider_transcript(messages)
    assert len(digest) == 64


def test_transcript_one_and_multiple_paired_calls() -> None:
    c1 = _call(call_id="c1", call_index=0, provider_alias="a_tool", domain_key="a")
    c2 = _call(call_id="c2", call_index=1, provider_alias="b_tool", domain_key="b")
    one = (
        ProviderUserMessage(content="q"),
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
        ProviderToolMessage(
            call_id="c1",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
        ProviderAssistantMessage(content="done", tool_calls=()),
    )
    validate_provider_transcript(one)

    multi = (
        ProviderUserMessage(content="q"),
        ProviderAssistantMessage(content=None, tool_calls=(c1, c2)),
        ProviderToolMessage(
            call_id="c1",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
        ProviderToolMessage(
            call_id="c2",
            provider_alias="b_tool",
            content=_completed_envelope(domain_key="b"),
        ),
        ProviderAssistantMessage(content="done", tool_calls=()),
    )
    validate_provider_transcript(multi)


def test_transcript_missing_duplicate_wrong_order_and_before_call() -> None:
    c1 = _call(call_id="c1", call_index=0, provider_alias="a_tool", domain_key="a")
    c2 = _call(call_id="c2", call_index=1, provider_alias="b_tool", domain_key="b")
    missing = (
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
    )
    with pytest.raises(ValueError, match="unpaired"):
        validate_provider_transcript(missing)

    duplicate = (
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
        ProviderToolMessage(
            call_id="c1",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
        ProviderToolMessage(
            call_id="c1",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
    )
    with pytest.raises(ValueError):
        validate_provider_transcript(duplicate)

    wrong_order = (
        ProviderAssistantMessage(content=None, tool_calls=(c1, c2)),
        ProviderToolMessage(
            call_id="c2",
            provider_alias="b_tool",
            content=_completed_envelope(domain_key="b"),
        ),
        ProviderToolMessage(
            call_id="c1",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
    )
    with pytest.raises(ValueError, match="order"):
        validate_provider_transcript(wrong_order)

    before_call = (
        ProviderToolMessage(
            call_id="c1",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
    )
    with pytest.raises(ValueError):
        validate_provider_transcript(before_call)


def test_nested_assistant_before_pairing_rejected() -> None:
    c1 = _call(call_id="c1")
    nested = (
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
        ProviderAssistantMessage(content="premature", tool_calls=()),
    )
    with pytest.raises(ValueError, match="unpaired|cannot appear"):
        validate_provider_transcript(nested)


def test_waiting_exception_and_cancellation_seal() -> None:
    c1 = _call(call_id="c1", call_index=0, provider_alias="a_tool", domain_key="a")
    c2 = _call(call_id="c2", call_index=1, provider_alias="b_tool", domain_key="b")
    assistant = ProviderAssistantMessage(content=None, tool_calls=(c1, c2))
    messages = (
        ProviderUserMessage(content="q"),
        assistant,
    )
    # Without continuation, unpaired fails.
    with pytest.raises(ValueError, match="unpaired"):
        validate_provider_transcript(messages)

    class _Waiting:
        call_id = "c1"

    class _Cont:
        waiting_call = _Waiting()
        pending_call_ids = ("c2",)
        assistant_message_digest = digest_provider_message(assistant)

    validate_provider_transcript(messages, allowed_open_continuation=_Cont())

    sealed = seal_cancelled_continuation(
        messages,
        waiting_call=c1,
        pending_calls=(c2,),
    )
    validate_provider_transcript(sealed)
    assert isinstance(sealed[-2], ProviderToolMessage)
    assert sealed[-2].content.status == "cancelled"
    assert sealed[-1].content.status == "cancelled_before_start"


def test_deferred_status_not_encoded_as_tool_message() -> None:
    # Deferred is a scheduler record status only; Tool envelopes reject it.
    with pytest.raises(ValidationError):
        ProviderToolResultEnvelope(
            status="deferred",  # type: ignore[arg-type]
            domain_key="tools.search",
            user_text=None,
            structured_output=None,
            terminal_output=False,
            needs_followup=True,
            error=None,
            artifact_refs=(),
        )
    with pytest.raises(ValidationError):
        ProviderToolResultEnvelope(
            status="waiting",  # type: ignore[arg-type]
            domain_key="tools.search",
            user_text=None,
            structured_output=None,
            terminal_output=False,
            needs_followup=True,
            error=None,
            artifact_refs=(),
        )


def test_cross_round_duplicate_call_ids_rejected() -> None:
    c1 = _call(call_id="same", call_index=0, provider_alias="a_tool", domain_key="a")
    messages = (
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
        ProviderToolMessage(
            call_id="same",
            provider_alias="a_tool",
            content=_completed_envelope(domain_key="a"),
        ),
        ProviderAssistantMessage(content=None, tool_calls=(c1,)),
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_provider_transcript(messages)


def test_transcript_digest_stable() -> None:
    messages = (
        ProviderUserMessage(content="hi"),
        ProviderAssistantMessage(content="yo", tool_calls=()),
    )
    assert digest_provider_transcript(messages) == sha256_canonical_json(
        {
            "schemaVersion": 1,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo", "toolCalls": []},
            ],
        }
    )
