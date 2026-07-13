"""Provider-neutral message contracts and pure transcript validators (Plan 03).

No Provider SDK, database, Gateway, or Tool execution.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Sequence

from pydantic import Field, field_validator, model_validator
from typing_extensions import Annotated

from app.assistant.capabilities.contracts import (
    ArtifactRef,
    CapabilityError,
    CapabilityResult,
)
from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import JsonValue, sha256_canonical_json

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    if _CONTROL_RE.search(cleaned):
        raise ValueError(f"{field_name} must not contain control characters")
    return cleaned


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _json_copy(value: Any, *, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # noqa: PLR0124
            raise ValueError(f"NaN/Infinity are not valid JSON values at {path}")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"mapping keys must be strings at {path}")
            out[key] = _json_copy(item, path=f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_copy(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"unsupported JSON value type {type(value)!r} at {path}")


def _json_object_copy(value: Any, *, path: str = "$") -> dict[str, JsonValue]:
    copied = _json_copy(value, path=path)
    if not isinstance(copied, dict):
        raise ValueError(f"{path} must be a JSON object")
    return copied


def _require_provider_alias(value: Any) -> str:
    alias = _require_non_empty_str(value, field_name="provider_alias")
    if not _ALIAS_RE.fullmatch(alias):
        raise ValueError("provider_alias must match ^[A-Za-z0-9_-]{1,64}$")
    return alias


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class ProviderSystemMessage(FrozenContract):
    role: Literal["system"] = "system"
    content: str

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("content must be a string")
        if _CONTROL_RE.search(value):
            raise ValueError("content must not contain control characters")
        return value


class ProviderRuntimeInstructionMessage(FrozenContract):
    role: Literal["runtime_instruction"] = "runtime_instruction"
    instruction_type: Literal["soft_finalization"]
    locale: str
    content: str

    @field_validator("locale")
    @classmethod
    def _locale(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="locale")

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("content must be a string")
        if _CONTROL_RE.search(value):
            raise ValueError("content must not contain control characters")
        if not value.strip():
            raise ValueError("content must be non-empty")
        return value


class ProviderUserMessage(FrozenContract):
    role: Literal["user"] = "user"
    content: str

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("content must be a string")
        if _CONTROL_RE.search(value):
            raise ValueError("content must not contain control characters")
        return value


class ProviderToolCall(FrozenContract):
    call_id: str
    call_index: int
    provider_alias: str
    domain_key: str
    arguments: dict[str, Any]
    arguments_digest: str
    binding_contract_digest: str
    descriptor_digest: str
    behavior_digest: str
    classification_revision: str
    classification_ruleset_digest: str
    manifest_revision: int
    manifest_digest: str
    surface_digest: str

    @field_validator("call_id", "domain_key", "classification_revision")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("provider_alias")
    @classmethod
    def _alias(cls, value: str) -> str:
        return _require_provider_alias(value)

    @field_validator("call_index", "manifest_revision")
    @classmethod
    def _non_negative(cls, value: int, info: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return value

    @field_validator(
        "arguments_digest",
        "binding_contract_digest",
        "descriptor_digest",
        "behavior_digest",
        "classification_ruleset_digest",
        "manifest_digest",
        "surface_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("arguments", mode="before")
    @classmethod
    def _arguments(cls, value: Any) -> dict[str, JsonValue]:
        return _json_object_copy(value, path="arguments")

    @model_validator(mode="after")
    def _arguments_digest_matches(self) -> ProviderToolCall:
        expected = sha256_canonical_json(self.arguments)  # type: ignore[arg-type]
        if expected != self.arguments_digest:
            raise ValueError("arguments_digest does not match canonical arguments")
        return self


class ProviderAssistantMessage(FrozenContract):
    role: Literal["assistant"] = "assistant"
    content: str | None
    tool_calls: tuple[ProviderToolCall, ...] = ()

    @field_validator("content")
    @classmethod
    def _content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("content must be a string or null")
        if _CONTROL_RE.search(value):
            raise ValueError("content must not contain control characters")
        return value

    @field_validator("tool_calls", mode="before")
    @classmethod
    def _tool_calls(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("tool_calls must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _tool_call_invariants(self) -> ProviderAssistantMessage:
        if not self.tool_calls:
            return self
        seen_ids: set[str] = set()
        for index, call in enumerate(self.tool_calls):
            if call.call_index != index:
                raise ValueError("tool call indexes must be contiguous from zero")
            if call.call_id in seen_ids:
                raise ValueError(f"duplicate tool call_id {call.call_id!r}")
            seen_ids.add(call.call_id)
        return self


class ProviderToolResultEnvelope(FrozenContract):
    status: Literal[
        "completed",
        "failed",
        "blocked",
        "cancelled",
        "cancelled_before_start",
    ]
    domain_key: str
    user_text: str | None
    structured_output: Any | None
    terminal_output: bool
    needs_followup: bool
    error: CapabilityError | None
    artifact_refs: tuple[ArtifactRef, ...] = ()

    @field_validator("domain_key")
    @classmethod
    def _domain_key(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="domain_key")

    @field_validator("structured_output", mode="before")
    @classmethod
    def _structured_output(cls, value: Any) -> Any | None:
        if value is None:
            return None
        return _json_copy(value, path="structured_output")

    @field_validator("user_text")
    @classmethod
    def _user_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _CONTROL_RE.search(value):
            raise ValueError("user_text must not contain control characters")
        return value

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _artifact_refs(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("artifact_refs must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _status_rules(self) -> ProviderToolResultEnvelope:
        if self.status == "completed":
            if self.error is not None:
                raise ValueError("completed envelope cannot include an error")
        elif self.status in {"failed", "blocked", "cancelled", "cancelled_before_start"}:
            if self.error is None:
                raise ValueError(f"{self.status} envelope requires an error")
        return self


class ProviderToolMessage(FrozenContract):
    role: Literal["tool"] = "tool"
    call_id: str
    provider_alias: str
    content: ProviderToolResultEnvelope

    @field_validator("call_id")
    @classmethod
    def _call_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="call_id")

    @field_validator("provider_alias")
    @classmethod
    def _alias(cls, value: str) -> str:
        return _require_provider_alias(value)


ProviderMessage = Annotated[
    ProviderSystemMessage
    | ProviderRuntimeInstructionMessage
    | ProviderUserMessage
    | ProviderAssistantMessage
    | ProviderToolMessage,
    Field(discriminator="role"),
]


class ProviderToolCallRecord(FrozenContract):
    call: ProviderToolCall
    status: Literal[
        "completed",
        "failed",
        "blocked",
        "waiting",
        "deferred",
        "cancelled",
        "cancelled_before_start",
    ]
    result_message_digest: str | None
    safe_duration_ms: float | None

    @field_validator("result_message_digest")
    @classmethod
    def _result_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, field_name="result_message_digest")

    @field_validator("safe_duration_ms")
    @classmethod
    def _duration(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("safe_duration_ms must be a number")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # noqa: PLR0124
            raise ValueError("safe_duration_ms must be finite")
        if number < 0:
            raise ValueError("safe_duration_ms must be >= 0")
        return number

    @model_validator(mode="after")
    def _status_digest_rules(self) -> ProviderToolCallRecord:
        terminal = {
            "completed",
            "failed",
            "blocked",
            "cancelled",
            "cancelled_before_start",
        }
        if self.status in terminal and self.result_message_digest is None:
            raise ValueError(f"{self.status} call record requires result_message_digest")
        if self.status in {"waiting", "deferred"} and self.result_message_digest is not None:
            raise ValueError(f"{self.status} call record cannot include result_message_digest")
        return self


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def digest_arguments(arguments: dict[str, Any]) -> str:
    return sha256_canonical_json(_json_object_copy(arguments, path="arguments"))


def _tool_call_payload(call: ProviderToolCall) -> dict[str, JsonValue]:
    return {
        "callId": call.call_id,
        "callIndex": call.call_index,
        "providerAlias": call.provider_alias,
        "domainKey": call.domain_key,
        "argumentsDigest": call.arguments_digest,
        "bindingContractDigest": call.binding_contract_digest,
        "descriptorDigest": call.descriptor_digest,
        "behaviorDigest": call.behavior_digest,
        "classificationRevision": call.classification_revision,
        "classificationRulesetDigest": call.classification_ruleset_digest,
        "manifestRevision": call.manifest_revision,
        "manifestDigest": call.manifest_digest,
        "surfaceDigest": call.surface_digest,
        "arguments": call.arguments,
    }


def _error_payload(error: CapabilityError | None) -> JsonValue:
    if error is None:
        return None
    return {
        "errorType": error.error_type,
        "safeCode": error.safe_code,
        "safeMessage": error.safe_message,
        "retryDisposition": error.retry_disposition,
        "targetIdentity": error.target_identity,
        "callId": error.call_id,
        "validationIssues": [
            {
                "instancePointer": item.instance_pointer,
                "schemaPointer": item.schema_pointer,
                "keyword": item.keyword,
                "safeMessage": item.safe_message,
            }
            for item in error.validation_issues
        ],
    }


def _artifact_payload(ref: ArtifactRef) -> dict[str, JsonValue]:
    return {
        "artifactId": ref.artifact_id,
        "mediaType": ref.media_type,
        "contentDigest": ref.content_digest,
    }


def _envelope_payload(content: ProviderToolResultEnvelope) -> dict[str, JsonValue]:
    return {
        "status": content.status,
        "domainKey": content.domain_key,
        "userText": content.user_text,
        "structuredOutput": content.structured_output,
        "terminalOutput": content.terminal_output,
        "needsFollowup": content.needs_followup,
        "error": _error_payload(content.error),
        "artifactRefs": [_artifact_payload(item) for item in content.artifact_refs],
    }


def provider_message_payload(message: ProviderMessage) -> dict[str, JsonValue]:
    if isinstance(message, ProviderSystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, ProviderRuntimeInstructionMessage):
        return {
            "role": "runtime_instruction",
            "instructionType": message.instruction_type,
            "locale": message.locale,
            "content": message.content,
        }
    if isinstance(message, ProviderUserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, ProviderAssistantMessage):
        return {
            "role": "assistant",
            "content": message.content,
            "toolCalls": [_tool_call_payload(call) for call in message.tool_calls],
        }
    if isinstance(message, ProviderToolMessage):
        return {
            "role": "tool",
            "callId": message.call_id,
            "providerAlias": message.provider_alias,
            "content": _envelope_payload(message.content),
        }
    raise TypeError(f"unsupported provider message type {type(message)!r}")


def digest_provider_message(message: ProviderMessage) -> str:
    return sha256_canonical_json(provider_message_payload(message))


def digest_provider_transcript(messages: tuple[ProviderMessage, ...]) -> str:
    if not isinstance(messages, tuple):
        raise TypeError("messages must be a tuple")
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "messages": [provider_message_payload(item) for item in messages],
        }
    )


def project_tool_result_envelope(
    *,
    domain_key: str,
    result: CapabilityResult,
    status_override: Literal[
        "completed",
        "failed",
        "blocked",
        "cancelled",
        "cancelled_before_start",
    ]
    | None = None,
) -> ProviderToolResultEnvelope:
    """Project a terminal CapabilityResult into a safe Tool Result envelope.

    Waiting results are never projected into Tool messages.
    """
    if not isinstance(result, CapabilityResult):
        raise TypeError("result must be a CapabilityResult")
    if result.status == "waiting":
        raise ValueError("waiting capability results cannot become tool messages")

    if status_override is not None:
        status = status_override
    elif result.status == "completed":
        status = "completed"
    elif result.status == "failed":
        status = "failed"
    elif result.status == "cancelled":
        status = "cancelled"
    else:  # pragma: no cover - CapabilityResult status union is closed
        raise ValueError(f"unsupported capability result status {result.status!r}")

    return ProviderToolResultEnvelope(
        status=status,
        domain_key=domain_key,
        user_text=result.user_text,
        structured_output=result.structured_output,
        terminal_output=result.terminal_output,
        needs_followup=result.needs_followup,
        error=result.error,
        artifact_refs=result.artifact_refs,
    )


def make_cancelled_envelope(
    *,
    domain_key: str,
    status: Literal["cancelled", "cancelled_before_start"],
    safe_code: str,
    safe_message: str,
    call_id: str | None = None,
) -> ProviderToolResultEnvelope:
    error = CapabilityError(
        error_type="cancelled",
        safe_code=safe_code,
        safe_message=safe_message,
        retry_disposition="never",
        target_identity=None,
        call_id=call_id,
        validation_issues=(),
    )
    return ProviderToolResultEnvelope(
        status=status,
        domain_key=domain_key,
        user_text=None,
        structured_output=None,
        terminal_output=False,
        needs_followup=False,
        error=error,
        artifact_refs=(),
    )


# ---------------------------------------------------------------------------
# Transcript validation / cancellation sealing
# ---------------------------------------------------------------------------


class TranscriptOpenCall:
    __slots__ = ("call", "assistant_index", "message_digest")

    def __init__(
        self,
        *,
        call: ProviderToolCall,
        assistant_index: int,
        message_digest: str,
    ) -> None:
        self.call = call
        self.assistant_index = assistant_index
        self.message_digest = message_digest


def _open_calls_from_messages(
    messages: tuple[ProviderMessage, ...],
) -> dict[str, TranscriptOpenCall]:
    open_calls: dict[str, TranscriptOpenCall] = {}
    seen_ids: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, ProviderAssistantMessage):
            if open_calls:
                raise ValueError(
                    "assistant message cannot appear while prior tool calls remain unpaired"
                )
            assistant_digest = digest_provider_message(message)
            for call in message.tool_calls:
                if call.call_id in seen_ids:
                    raise ValueError(
                        f"duplicate tool call_id across transcript: {call.call_id!r}"
                    )
                seen_ids.add(call.call_id)
                open_calls[call.call_id] = TranscriptOpenCall(
                    call=call,
                    assistant_index=index,
                    message_digest=assistant_digest,
                )
            continue

        if isinstance(message, ProviderToolMessage):
            open = open_calls.get(message.call_id)
            if open is None:
                raise ValueError(
                    f"tool result references unknown or already closed call {message.call_id!r}"
                )
            if message.provider_alias != open.call.provider_alias:
                raise ValueError("tool result provider_alias must match the open call")
            if message.content.domain_key != open.call.domain_key:
                raise ValueError("tool result domain_key must match the open call")
            # Results must close in provider order within the open assistant message.
            expected_id = next(iter(open_calls))
            if message.call_id != expected_id:
                raise ValueError("tool results must follow assistant call order")
            del open_calls[message.call_id]
            continue

        if open_calls and not isinstance(
            message,
            (
                ProviderSystemMessage,
                ProviderUserMessage,
                ProviderRuntimeInstructionMessage,
            ),
        ):
            raise ValueError("unexpected message while tool calls remain open")

    return open_calls


def validate_provider_transcript(
    messages: tuple[ProviderMessage, ...],
    *,
    allowed_open_continuation: Any | None = None,
) -> None:
    """Validate provider protocol pairing.

    Fully paired transcripts must have zero open calls.
    The sole temporary exception is a waiting continuation that identifies exactly
    one open assistant message, one waiting call, and deferred pending siblings.
    """
    if not isinstance(messages, tuple):
        raise TypeError("messages must be a tuple")
    for message in messages:
        if not isinstance(
            message,
            (
                ProviderSystemMessage,
                ProviderRuntimeInstructionMessage,
                ProviderUserMessage,
                ProviderAssistantMessage,
                ProviderToolMessage,
            ),
        ):
            raise TypeError(f"unsupported provider message type {type(message)!r}")

    open_calls = _open_calls_from_messages(messages)
    if not open_calls:
        if allowed_open_continuation is not None:
            raise ValueError("continuation provided but transcript has no open tool calls")
        return

    if allowed_open_continuation is None:
        raise ValueError("transcript has unpaired tool calls")

    # Lazy import avoidance: accept duck-typed continuation contract fields.
    waiting_call = getattr(allowed_open_continuation, "waiting_call", None)
    pending_call_ids = getattr(allowed_open_continuation, "pending_call_ids", None)
    assistant_message_digest = getattr(
        allowed_open_continuation, "assistant_message_digest", None
    )
    if waiting_call is None or pending_call_ids is None or assistant_message_digest is None:
        raise ValueError("allowed_open_continuation is incomplete")

    waiting_id = getattr(waiting_call, "call_id", None)
    if waiting_id is None or waiting_id not in open_calls:
        raise ValueError("continuation waiting call is not open in the transcript")

    open_ids = list(open_calls.keys())
    expected_open = [waiting_id, *list(pending_call_ids)]
    if open_ids != expected_open:
        raise ValueError("continuation pending/waiting IDs do not match open transcript calls")

    first = open_calls[waiting_id]
    if first.message_digest != assistant_message_digest:
        raise ValueError("continuation assistant_message_digest mismatch")
    for call_id in open_ids:
        if open_calls[call_id].assistant_index != first.assistant_index:
            raise ValueError("open calls must belong to one assistant message")
        if open_calls[call_id].message_digest != assistant_message_digest:
            raise ValueError("continuation assistant_message_digest mismatch")


def seal_cancelled_continuation(
    messages: tuple[ProviderMessage, ...],
    *,
    waiting_call: ProviderToolCall,
    pending_calls: tuple[ProviderToolCall, ...],
    waiting_status: Literal["cancelled"] = "cancelled",
) -> tuple[ProviderMessage, ...]:
    """Seal unpaired waiting/pending calls with protocol Tool Result envelopes.

    Does not claim to cancel the underlying durable child business target.
    """
    if waiting_status != "cancelled":
        raise ValueError("waiting_status must be cancelled")

    sealed: list[ProviderMessage] = list(messages)
    sealed.append(
        ProviderToolMessage(
            call_id=waiting_call.call_id,
            provider_alias=waiting_call.provider_alias,
            content=make_cancelled_envelope(
                domain_key=waiting_call.domain_key,
                status="cancelled",
                safe_code="cancelled",
                safe_message="waiting call sealed as cancelled",
                call_id=waiting_call.call_id,
            ),
        )
    )
    for call in pending_calls:
        sealed.append(
            ProviderToolMessage(
                call_id=call.call_id,
                provider_alias=call.provider_alias,
                content=make_cancelled_envelope(
                    domain_key=call.domain_key,
                    status="cancelled_before_start",
                    safe_code="cancelled_before_start",
                    safe_message="pending sibling sealed before start",
                    call_id=call.call_id,
                ),
            )
        )
    result = tuple(sealed)
    validate_provider_transcript(result)
    return result


__all__ = [
    "ProviderAssistantMessage",
    "ProviderMessage",
    "ProviderRuntimeInstructionMessage",
    "ProviderSystemMessage",
    "ProviderToolCall",
    "ProviderToolCallRecord",
    "ProviderToolMessage",
    "ProviderToolResultEnvelope",
    "ProviderUserMessage",
    "digest_arguments",
    "digest_provider_message",
    "digest_provider_transcript",
    "make_cancelled_envelope",
    "project_tool_result_envelope",
    "provider_message_payload",
    "seal_cancelled_continuation",
    "validate_provider_transcript",
]
