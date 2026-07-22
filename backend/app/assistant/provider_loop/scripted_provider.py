"""Deterministic scripted Provider adapter for Plan 03 tests.

No network I/O. Asserts exact round request identity and yields a scripted
normalized event sequence with exactly one terminal event.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.assistant.domain.contracts import ModelRef
from app.assistant.provider_loop.contracts import (
    ProviderAdapter,
    ProviderGenerationOptions,
    ProviderRoundRequest,
    ProviderRoundTerminal,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderUsageSnapshot,
)
from app.assistant.provider_loop.messages import ProviderMessage, digest_provider_message


class ScriptedProviderAssertionError(AssertionError):
    """Readable test-only assertion failure from the scripted adapter."""


@dataclass(frozen=True)
class ScriptedRoundScript:
    """One expected Provider round plus the stream events to emit.

    Evaluation fixtures may set ``assert_messages=False`` and/or leave
    ``expected_surface_digest`` empty so versioned Provider scripts do not need
    to precompute message digests or tool surfaces. Scripts never carry
    acceptable Skill keys or other dataset assertion fields.
    """

    expected_round_index: int
    expected_messages: tuple[ProviderMessage, ...]
    expected_surface_digest: str
    expected_tools_enabled: bool
    expected_finalization_round: bool
    events: tuple[ProviderStreamEvent, ...]
    expected_generation: ProviderGenerationOptions | None = None
    expected_tool_aliases: tuple[str, ...] | None = None
    raise_error: BaseException | None = None
    # When False, message digests / counts are not asserted (eval fixtures).
    assert_messages: bool = True
    # When False, tool surface digest is not asserted (eval fixtures).
    assert_surface_digest: bool = True

    def __post_init__(self) -> None:
        if self.raise_error is not None:
            return
        if not self.events:
            raise ValueError("scripted round events must be non-empty unless raise_error is set")
        _assert_contiguous_terminal_sequence(self.events)


@dataclass
class ScriptedProvider:
    """Queue-driven fake ProviderAdapter for loop tests and evaluation fixtures."""

    provider_protocol: str
    adapter_key: str
    adapter_revision: str
    model_config_digest: str
    expected_model_ref: ModelRef
    scripts: list[ScriptedRoundScript] = field(default_factory=list)
    request_count: int = 0
    seen_requests: list[ProviderRoundRequest] = field(default_factory=list)
    # When True, skip model_ref digest checks (eval fixtures may use placeholders).
    relax_model_ref: bool = False

    def enqueue(self, *scripts: ScriptedRoundScript) -> None:
        for script in scripts:
            if not isinstance(script, ScriptedRoundScript):
                raise TypeError("scripts must be ScriptedRoundScript instances")
            self.scripts.append(script)

    def stream_round(
        self,
        request: ProviderRoundRequest,
        *,
        cancellation: Any,
    ) -> Iterator[ProviderStreamEvent]:
        del cancellation  # cancellation is checked by the loop before/around stream
        self.request_count += 1
        self.seen_requests.append(request)

        if not self.scripts:
            raise ScriptedProviderAssertionError(
                f"unexpected Provider request #{self.request_count}: no scripted rounds remain"
            )
        script = self.scripts.pop(0)

        self._assert_request_matches(request, script)

        if script.raise_error is not None:
            raise script.raise_error

        for event in script.events:
            yield event

    def _assert_request_matches(
        self,
        request: ProviderRoundRequest,
        script: ScriptedRoundScript,
    ) -> None:
        if request.round_index != script.expected_round_index:
            raise ScriptedProviderAssertionError(
                f"round_index mismatch: got {request.round_index}, "
                f"expected {script.expected_round_index}"
            )
        if request.tools_enabled != script.expected_tools_enabled:
            raise ScriptedProviderAssertionError(
                f"tools_enabled mismatch: got {request.tools_enabled}, "
                f"expected {script.expected_tools_enabled}"
            )
        if request.finalization_round != script.expected_finalization_round:
            raise ScriptedProviderAssertionError(
                f"finalization_round mismatch: got {request.finalization_round}, "
                f"expected {script.expected_finalization_round}"
            )
        if script.assert_surface_digest and script.expected_surface_digest:
            if request.tool_surface.surface_digest != script.expected_surface_digest:
                raise ScriptedProviderAssertionError(
                    "tool_surface.surface_digest mismatch: "
                    f"got {request.tool_surface.surface_digest}, "
                    f"expected {script.expected_surface_digest}"
                )
        if (
            not self.relax_model_ref
            and request.model_ref.model_ref_digest
            != self.expected_model_ref.model_ref_digest
        ):
            raise ScriptedProviderAssertionError(
                "model_ref mismatch against scripted adapter expected_model_ref"
            )
        if script.expected_generation is not None:
            if request.generation.model_dump() != script.expected_generation.model_dump():
                raise ScriptedProviderAssertionError(
                    "generation options mismatch against scripted expectation"
                )
        if script.assert_messages:
            if len(request.messages) != len(script.expected_messages):
                raise ScriptedProviderAssertionError(
                    f"message count mismatch: got {len(request.messages)}, "
                    f"expected {len(script.expected_messages)}"
                )
            for index, (got, expected) in enumerate(
                zip(request.messages, script.expected_messages, strict=True)
            ):
                if digest_provider_message(got) != digest_provider_message(expected):
                    raise ScriptedProviderAssertionError(
                        f"message[{index}] digest mismatch against scripted expectation"
                    )
        if script.expected_tool_aliases is not None:
            got_aliases = tuple(tool.provider_alias for tool in request.tool_surface.tools)
            if got_aliases != script.expected_tool_aliases:
                raise ScriptedProviderAssertionError(
                    f"tool aliases mismatch: got {got_aliases}, "
                    f"expected {script.expected_tool_aliases}"
                )


def eval_text_round_script(
    *chunks: str,
    round_index: int = 0,
    tools_enabled: bool = True,
    finalization_round: bool = False,
) -> ScriptedRoundScript:
    """Build a relaxed eval fixture round that only asserts round identity."""
    return ScriptedRoundScript(
        expected_round_index=round_index,
        expected_messages=(),
        expected_surface_digest="",
        expected_tools_enabled=tools_enabled,
        expected_finalization_round=finalization_round,
        events=text_then_terminal(*chunks),
        assert_messages=False,
        assert_surface_digest=False,
    )


def eval_tool_call_round_script(
    *,
    call_id: str,
    provider_alias: str,
    arguments_json: str,
    round_index: int = 0,
    provisional_text: str | None = None,
    tools_enabled: bool = True,
    finalization_round: bool = False,
) -> ScriptedRoundScript:
    """Build a relaxed eval fixture tool-call round (no message digest pins)."""
    return ScriptedRoundScript(
        expected_round_index=round_index,
        expected_messages=(),
        expected_surface_digest="",
        expected_tools_enabled=tools_enabled,
        expected_finalization_round=finalization_round,
        events=tool_call_then_terminal(
            call_id=call_id,
            provider_alias=provider_alias,
            arguments_json=arguments_json,
            provisional_text=provisional_text,
        ),
        assert_messages=False,
        assert_surface_digest=False,
    )


def text_then_terminal(
    *chunks: str,
    finish_reason: str | None = "stop",
    usage: Any | None = None,
) -> tuple[ProviderStreamEvent, ...]:
    """Helper: ordered text deltas + optional usage + terminal."""
    events: list[ProviderStreamEvent] = []
    sequence = 0
    for chunk in chunks:
        events.append(ProviderTextDelta(sequence=sequence, delta=chunk))
        sequence += 1
    if usage is not None:
        events.append(ProviderUsageSnapshot(sequence=sequence, usage=usage))
        sequence += 1
    events.append(
        ProviderRoundTerminal(
            sequence=sequence,
            finish_reason=finish_reason,
            safe_request_id=None,
        )
    )
    return tuple(events)


def tool_call_then_terminal(
    *,
    call_index: int = 0,
    call_id: str,
    provider_alias: str,
    arguments_json: str,
    provisional_text: str | None = None,
    finish_reason: str | None = "tool_calls",
    usage: Any | None = None,
) -> tuple[ProviderStreamEvent, ...]:
    """Helper: optional provisional text + one tool call + terminal."""
    events: list[ProviderStreamEvent] = []
    sequence = 0
    if provisional_text is not None:
        events.append(ProviderTextDelta(sequence=sequence, delta=provisional_text))
        sequence += 1
    events.append(
        ProviderToolCallDelta(
            sequence=sequence,
            call_index=call_index,
            call_id=call_id,
            provider_alias_delta=provider_alias,
            arguments_delta=arguments_json,
        )
    )
    sequence += 1
    if usage is not None:
        events.append(ProviderUsageSnapshot(sequence=sequence, usage=usage))
        sequence += 1
    events.append(
        ProviderRoundTerminal(
            sequence=sequence,
            finish_reason=finish_reason,
            safe_request_id=None,
        )
    )
    return tuple(events)


def _assert_contiguous_terminal_sequence(events: Sequence[ProviderStreamEvent]) -> None:
    if not events:
        raise ValueError("events must be non-empty")
    for index, event in enumerate(events):
        if event.sequence != index:
            raise ValueError(
                f"event sequences must be contiguous from zero; "
                f"index={index} sequence={event.sequence}"
            )
    terminals = [event for event in events if isinstance(event, ProviderRoundTerminal)]
    if len(terminals) != 1:
        raise ValueError("scripted events must contain exactly one terminal event")
    if not isinstance(events[-1], ProviderRoundTerminal):
        raise ValueError("terminal event must be last")


# Structural satisfaction of ProviderAdapter Protocol.
_: type[ProviderAdapter] = ScriptedProvider  # type: ignore[assignment,misc]


__all__ = [
    "ScriptedProvider",
    "ScriptedProviderAssertionError",
    "ScriptedRoundScript",
    "eval_text_round_script",
    "eval_tool_call_round_script",
    "text_then_terminal",
    "tool_call_then_terminal",
]
