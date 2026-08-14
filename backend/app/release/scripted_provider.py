"""Deterministic, non-secret OpenAI-compatible provider for release rehearsals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.assistant.domain.digests import sha256_canonical_json


class ScriptedProviderError(RuntimeError):
    safe_code = "scripted_provider_invalid"


@dataclass(frozen=True)
class ScriptedProviderStep:
    scenario_id: str
    request_ordinal: int
    expected_tool_names: tuple[str, ...]
    response_kind: Literal["tool_call", "content", "transport_fault"]
    tool_name: Literal["create_entry"] | None
    fault_code: str | None

    def __post_init__(self) -> None:
        if self.request_ordinal < 1:
            raise ScriptedProviderError("request ordinal is invalid")
        if self.response_kind == "tool_call" and self.tool_name != "create_entry":
            raise ScriptedProviderError("tool call must use create_entry")
        if self.response_kind == "transport_fault" and not self.fault_code:
            raise ScriptedProviderError("transport fault requires fault code")
        if self.response_kind != "transport_fault" and self.fault_code is not None:
            raise ScriptedProviderError("non-fault step cannot carry fault code")


@dataclass(frozen=True)
class ScriptedProviderScript:
    scenario_id: str
    steps: tuple[ScriptedProviderStep, ...]

    def __post_init__(self) -> None:
        if not self.steps:
            raise ScriptedProviderError("script must contain steps")
        if any(step.scenario_id != self.scenario_id for step in self.steps):
            raise ScriptedProviderError("script step scenario mismatch")
        ordinals = [step.request_ordinal for step in self.steps]
        if len(ordinals) != len(set(ordinals)):
            raise ScriptedProviderError("duplicate request ordinal")


class ScriptedProvider:
    """Provider whose schedule depends only on scenario and request ordinal."""

    def __init__(self, script: ScriptedProviderScript) -> None:
        self.script = script
        self._used: set[int] = set()
        self.request_shape_digests: list[str] = []

    @staticmethod
    def _tool_names(request: dict[str, Any]) -> tuple[str, ...]:
        tools = request.get("tools", [])
        if not isinstance(tools, list):
            raise ScriptedProviderError("tool declaration is invalid")
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                raise ScriptedProviderError("tool declaration is invalid")
            function = tool.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ScriptedProviderError("tool declaration is invalid")
            names.append(function["name"])
        return tuple(sorted(names))

    @classmethod
    def _structural_digest(cls, request: dict[str, Any]) -> str:
        messages = request.get("messages", [])
        if not isinstance(messages, list):
            raise ScriptedProviderError("messages are invalid")
        structure = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("role"), str):
                raise ScriptedProviderError("message shape is invalid")
            content = message.get("content")
            structure.append(
                {
                    "role": message["role"],
                    "contentType": type(content).__name__,
                    "contentPresent": content is not None,
                }
            )
        return sha256_canonical_json(
            {
                "domain": "mindatlas:scripted-provider-request-shape:v1",
                "toolNames": cls._tool_names(request),
                "messages": structure,
            }
        )

    def complete(
        self,
        request: dict[str, Any],
        *,
        scenario_id: str,
        request_ordinal: int,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        if endpoint is not None and endpoint not in {"/v1/chat/completions", "http://scripted-provider/v1/chat/completions"}:
            raise ScriptedProviderError("paid/live endpoint is not allowed")
        if scenario_id != self.script.scenario_id:
            raise ScriptedProviderError("unknown scenario")
        if request_ordinal in self._used:
            raise ScriptedProviderError("request ordinal was reused")
        step = next((item for item in self.script.steps if item.request_ordinal == request_ordinal), None)
        if step is None:
            raise ScriptedProviderError("unknown request ordinal")
        shape_digest = self._structural_digest(request)
        expected = tuple(sorted(step.expected_tool_names))
        actual = self._tool_names(request)
        if actual != expected:
            raise ScriptedProviderError("tool declaration does not match scripted step")
        self._used.add(request_ordinal)
        self.request_shape_digests.append(shape_digest)
        if step.response_kind == "transport_fault":
            raise ScriptedProviderError(step.fault_code or "scripted_transport_fault")
        if step.response_kind == "content":
            return {
                "id": f"scripted-{request_ordinal}",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "scripted release response"}, "finish_reason": "stop"}],
            }
        return {
            "id": f"scripted-{request_ordinal}",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"scripted-tool-{request_ordinal}",
                                "type": "function",
                                "function": {
                                    "name": "create_entry",
                                    "arguments": '{"title":"release qualification entry"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }


__all__ = [
    "ScriptedProvider",
    "ScriptedProviderError",
    "ScriptedProviderScript",
    "ScriptedProviderStep",
]
