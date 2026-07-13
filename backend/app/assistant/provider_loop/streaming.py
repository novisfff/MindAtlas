"""Provider stream assembly and soft-finalization helpers (Plan 03 Task 4).

Consumes normalized Provider stream events for one round and produces one
protocol-validated ``ProviderRoundResult``. Does not dispatch Tools or emit
provisional final text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from hashlib import sha256
from typing import Any, Protocol

from app.assistant.provider_loop.aliases import lookup_tool_by_alias
from app.assistant.provider_loop.contracts import (
    ProviderRoundResult,
    ProviderRoundTerminal,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderToolSurface,
    ProviderUsage,
    ProviderUsageSnapshot,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderRuntimeInstructionMessage,
    ProviderToolCall,
    digest_arguments,
)

# Aggregate UTF-8 byte limits for fragmented tool-call fields.
ARGUMENTS_BYTE_LIMIT = 64 * 1024
IDENTITY_BYTE_LIMIT = 256
SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
UNSAFE_REQUEST_ID_RE = re.compile(r"(?i)(sk-|secret|bearer\s|authorization|api[_-]?key)")


def is_safe_request_id(value: str | None) -> bool:
    """Accept only bounded printable provider request IDs."""
    if value is None:
        return False
    if not isinstance(value, str) or not value:
        return False
    if len(value) > 128:
        return False
    if UNSAFE_REQUEST_ID_RE.search(value):
        return False
    if not SAFE_REQUEST_ID_RE.fullmatch(value):
        return False
    return True


def is_finalization_round(
    *,
    round_index: int,
    max_rounds: int,
    prior_tool_call_count: int,
) -> bool:
    """Return True when this Provider request is the reserved tools-disabled slot.

    Once any Tool Call has occurred, the last allowed Provider request is reserved
    for soft finalization. Waiting/resume preserves prior counts so a resume cannot
    buy another finalization round.
    """
    if max_rounds < 1:
        return False
    if prior_tool_call_count <= 0:
        return False
    return round_index == max_rounds - 1


class FinalizationInstructionProvider(Protocol):
    def build(self, *, locale: str) -> ProviderRuntimeInstructionMessage: ...


class DefaultFinalizationInstructionProvider:
    """Localized soft-finalization instruction (internal runtime message only)."""

    def build(self, *, locale: str) -> ProviderRuntimeInstructionMessage:
        normalized = (locale or "en").strip().lower()
        if normalized.startswith("zh"):
            content = (
                "工具调用预算已用尽。请基于已完成与未完成的工作给出最终总结，"
                "不要再调用任何工具。"
            )
            locale_out = "zh"
        else:
            content = (
                "Tool budget is exhausted. Summarize completed and incomplete work "
                "as a final answer. Do not call any tools."
            )
            locale_out = "en"
        return ProviderRuntimeInstructionMessage(
            instruction_type="soft_finalization",
            locale=locale_out,
            content=content,
        )


class ProviderRoundAssembler:
    """Assemble one normalized assistant message from contiguous stream events.

    Receives the exact reverse alias map/surface and stamps resolved Domain Key,
    binding digest, descriptor digest, Manifest, and surface data into Tool Calls.
    Does not look up aliases globally or retain stream state after completion.
    """

    def __init__(
        self,
        *,
        surface: ProviderToolSurface,
        round_index: int,
    ) -> None:
        if not isinstance(surface, ProviderToolSurface):
            raise TypeError("surface must be a ProviderToolSurface")
        if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
            raise ValueError("round_index must be >= 0")
        self._surface = surface
        self._round_index = round_index
        self._next_sequence = 0
        self._text_parts: list[str] = []
        self._builders: dict[int, dict[str, Any]] = {}
        self._usage: ProviderUsage | None = None
        self._finish_reason: str | None = None
        self._safe_request_id: str | None = None
        self._saw_terminal = False
        self._finished = False
        self._warnings: list[str] = []

    def accept(self, event: ProviderStreamEvent) -> None:
        if self._finished:
            raise ValueError("assembler already finished")
        if self._saw_terminal:
            raise ValueError("stream event after terminal")
        sequence = getattr(event, "sequence", None)
        if sequence != self._next_sequence:
            raise ValueError("stream event sequences must be contiguous from zero")
        self._next_sequence += 1

        if isinstance(event, ProviderTextDelta):
            self._text_parts.append(event.delta)
            return

        if isinstance(event, ProviderToolCallDelta):
            self._accept_tool_delta(event)
            return

        if isinstance(event, ProviderUsageSnapshot):
            self._accept_usage(event.usage)
            return

        if isinstance(event, ProviderRoundTerminal):
            self._saw_terminal = True
            self._finish_reason = event.finish_reason
            if is_safe_request_id(event.safe_request_id):
                self._safe_request_id = event.safe_request_id
            return

        raise ValueError(f"unsupported stream event type {type(event)!r}")

    def finish(self) -> ProviderRoundResult:
        if self._finished:
            raise ValueError("assembler already finished")
        if self._next_sequence == 0:
            raise ValueError("provider stream was empty")
        if not self._saw_terminal:
            raise ValueError("provider stream missing terminal event")

        content = "".join(self._text_parts) if self._text_parts else None
        if content == "":
            content = None

        tool_calls = self._build_tool_calls()
        assistant = ProviderAssistantMessage(content=content, tool_calls=tool_calls)
        result = ProviderRoundResult(
            assistant_message=assistant,
            finish_reason=self._finish_reason,
            usage=self._usage,
            compatibility_warnings=tuple(self._warnings),
        )
        # Drop all intermediate stream state after completion.
        self._finished = True
        self._text_parts.clear()
        self._builders.clear()
        self._usage = None
        self._warnings.clear()
        self._surface = None  # type: ignore[assignment]
        return result

    def _accept_tool_delta(self, event: ProviderToolCallDelta) -> None:
        if getattr(event, "function_type", "function") != "function":
            raise ValueError("tool call function_type must be 'function'")

        builder = self._builders.setdefault(
            event.call_index,
            {
                "call_id": None,
                "provider_alias": "",
                "arguments": "",
                "alias_bytes": 0,
                "arguments_bytes": 0,
                "call_id_bytes": 0,
            },
        )

        if event.call_id is not None:
            call_id_bytes = len(event.call_id.encode("utf-8"))
            if call_id_bytes > IDENTITY_BYTE_LIMIT:
                raise ValueError("tool call_id identity byte limit exceeded")
            if builder["call_id"] is None:
                builder["call_id"] = event.call_id
                builder["call_id_bytes"] = call_id_bytes
            elif builder["call_id"] != event.call_id:
                raise ValueError("tool call id changed mid-stream")

        if event.provider_alias_delta:
            # Once a complete alias has already been resolved against the surface,
            # further non-empty alias fragments that would change the name are rejected.
            # Fragments may still append while the alias is incomplete.
            previous_alias = builder["provider_alias"]
            fragment = event.provider_alias_delta
            fragment_bytes = len(fragment.encode("utf-8"))
            if builder["alias_bytes"] + fragment_bytes > IDENTITY_BYTE_LIMIT:
                raise ValueError("tool call alias identity byte limit exceeded")
            candidate = previous_alias + fragment
            if previous_alias:
                # If previous_alias already maps on the surface, reject any further delta.
                try:
                    lookup_tool_by_alias(self._surface, previous_alias)
                except KeyError:
                    pass
                else:
                    if fragment:
                        raise ValueError("tool call provider alias/name changed mid-stream")
            builder["provider_alias"] = candidate
            builder["alias_bytes"] += fragment_bytes

        if event.arguments_delta:
            fragment_bytes = len(event.arguments_delta.encode("utf-8"))
            if builder["arguments_bytes"] + fragment_bytes > ARGUMENTS_BYTE_LIMIT:
                raise ValueError("tool call arguments byte limit exceeded")
            builder["arguments"] += event.arguments_delta
            builder["arguments_bytes"] += fragment_bytes

    def _accept_usage(self, usage: ProviderUsage) -> None:
        if self._usage is None:
            self._usage = usage
            return
        prior = self._usage
        # Snapshots must be non-decreasing and never contradict a prior field.
        if (
            usage.input_tokens < prior.input_tokens
            or usage.output_tokens < prior.output_tokens
            or usage.total_tokens < prior.total_tokens
        ):
            raise ValueError("usage snapshot decreased")
        if usage.input_tokens < prior.input_tokens or (
            usage.input_tokens == prior.input_tokens
            and usage.output_tokens < prior.output_tokens
        ):
            raise ValueError("usage snapshot inconsistent")
        # Inconsistent: same input but lower output already covered; also reject
        # same totals with lower component that isn't strictly monotonic overall.
        if (
            usage.input_tokens == prior.input_tokens
            and usage.output_tokens < prior.output_tokens
        ):
            raise ValueError("usage snapshot inconsistent")
        if (
            usage.input_tokens == prior.input_tokens
            and usage.output_tokens == prior.output_tokens
            and usage.total_tokens < prior.total_tokens
        ):
            raise ValueError("usage snapshot decreased")
        # Reject component regression already handled; also reject when a later
        # snapshot lowers any previously observed optional field.
        if prior.cached_input_tokens is not None and usage.cached_input_tokens is not None:
            if usage.cached_input_tokens < prior.cached_input_tokens:
                raise ValueError("usage snapshot decreased")
        if prior.reasoning_tokens is not None and usage.reasoning_tokens is not None:
            if usage.reasoning_tokens < prior.reasoning_tokens:
                raise ValueError("usage snapshot decreased")
        # Inconsistent: later snapshot with same input tokens but reduced output.
        if (
            usage.input_tokens >= prior.input_tokens
            and usage.output_tokens < prior.output_tokens
        ):
            raise ValueError("usage snapshot inconsistent")
        self._usage = usage

    def _build_tool_calls(self) -> tuple[ProviderToolCall, ...]:
        if not self._builders:
            return ()

        indexes = sorted(self._builders)
        if indexes != list(range(len(indexes))):
            raise ValueError("tool call indexes must be contiguous from zero")

        seen_ids: set[str] = set()
        tool_calls: list[ProviderToolCall] = []
        for call_index in indexes:
            builder = self._builders[call_index]
            call_id = builder["call_id"]
            if not call_id:
                call_id = _synthesize_call_id(
                    round_index=self._round_index,
                    call_index=call_index,
                    provider_alias=builder["provider_alias"],
                    arguments=builder["arguments"],
                )
                self._warnings.append("synthesized_call_id")
            if len(call_id.encode("utf-8")) > IDENTITY_BYTE_LIMIT:
                raise ValueError("tool call_id identity byte limit exceeded")
            if call_id in seen_ids:
                raise ValueError(f"duplicate tool call_id {call_id!r}")
            seen_ids.add(call_id)

            provider_alias = builder["provider_alias"]
            if not provider_alias:
                raise ValueError("tool call missing provider alias/name")
            try:
                definition = lookup_tool_by_alias(self._surface, provider_alias)
            except KeyError as exc:
                raise ValueError(f"unknown provider alias: {provider_alias!r}") from exc

            raw_args = builder["arguments"]
            if len(raw_args.encode("utf-8")) > ARGUMENTS_BYTE_LIMIT:
                raise ValueError("tool call arguments byte limit exceeded")
            try:
                parsed = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as exc:
                raise ValueError("tool call arguments are not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise ValueError("tool call arguments must be a JSON object")

            tool_calls.append(
                ProviderToolCall(
                    call_id=call_id,
                    call_index=call_index,
                    provider_alias=definition.provider_alias,
                    domain_key=definition.domain_key,
                    arguments=parsed,
                    arguments_digest=digest_arguments(parsed),
                    binding_contract_digest=definition.binding.ref.binding_contract_digest,
                    descriptor_digest=definition.descriptor.descriptor_digest,
                    behavior_digest=definition.descriptor.behavior.behavior_digest,
                    classification_revision=definition.descriptor.behavior.classification.revision,
                    classification_ruleset_digest=(
                        definition.descriptor.behavior.classification.ruleset_digest
                    ),
                    manifest_revision=self._surface.manifest_revision,
                    manifest_digest=self._surface.manifest_digest,
                    surface_digest=self._surface.surface_digest,
                )
            )
        return tuple(tool_calls)


def assemble_provider_round(
    *,
    events: Sequence[Any],
    surface: ProviderToolSurface,
    round_index: int,
) -> ProviderRoundResult:
    """Assemble one normalized assistant message from a complete event sequence."""
    assembler = ProviderRoundAssembler(surface=surface, round_index=round_index)
    if not events:
        # finish() also rejects empty, but give a clear message early.
        raise ValueError("provider stream was empty")
    for event in events:
        assembler.accept(event)
    return assembler.finish()


def _synthesize_call_id(
    *,
    round_index: int,
    call_index: int,
    provider_alias: str,
    arguments: str,
) -> str:
    material = f"{round_index}\0{call_index}\0{provider_alias}\0{arguments}".encode("utf-8")
    digest8 = sha256(material).hexdigest()[:8]
    return f"call_r{round_index}_i{call_index}_{digest8}"


__all__ = [
    "ARGUMENTS_BYTE_LIMIT",
    "IDENTITY_BYTE_LIMIT",
    "DefaultFinalizationInstructionProvider",
    "FinalizationInstructionProvider",
    "ProviderRoundAssembler",
    "assemble_provider_round",
    "is_finalization_round",
    "is_safe_request_id",
]
