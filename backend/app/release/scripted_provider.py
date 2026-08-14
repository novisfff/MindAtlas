"""Deterministic, non-secret OpenAI-compatible provider for release rehearsals."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from app.assistant.domain.digests import sha256_canonical_json


class ScriptedProviderError(RuntimeError):
    safe_code = "scripted_provider_invalid"


UNSUPPORTED_BOUNDARY_TOOL_NAMES = frozenset(
    {"update_entry", "merge_entry", "create_relation", "relation_followup"}
)


@dataclass(frozen=True)
class ScriptedProviderStep:
    scenario_id: str
    request_ordinal: int
    expected_tool_names: tuple[str, ...]
    response_kind: Literal["tool_call", "content", "transport_fault"]
    tool_name: str | None
    fault_code: str | None

    def __post_init__(self) -> None:
        if self.request_ordinal < 1:
            raise ScriptedProviderError("request ordinal is invalid")
        if self.response_kind not in {"tool_call", "content", "transport_fault"}:
            raise ScriptedProviderError("response kind is invalid")
        if len(set(self.expected_tool_names)) != len(self.expected_tool_names):
            raise ScriptedProviderError("duplicate expected tool name")
        if self.response_kind == "tool_call" and self.tool_name not in (
            {"create_entry"} | UNSUPPORTED_BOUNDARY_TOOL_NAMES
        ):
            raise ScriptedProviderError("tool call name is not in the boundary fixture vocabulary")
        if self.response_kind == "tool_call" and self.tool_name not in self.expected_tool_names:
            raise ScriptedProviderError("tool call is absent from the expected tool declaration")
        if self.response_kind == "transport_fault" and not self.fault_code:
            raise ScriptedProviderError("transport fault requires fault code")
        if self.response_kind != "transport_fault" and self.fault_code is not None:
            raise ScriptedProviderError("non-fault step cannot carry fault code")
        if self.response_kind != "tool_call" and self.tool_name is not None:
            raise ScriptedProviderError("non-tool step cannot carry tool name")


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


def load_script(path: Path) -> ScriptedProviderScript:
    """Load the code-owned, JSON-only provider schedule."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ScriptedProviderError("script file is invalid") from None
    if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "scenarioId", "steps"}:
        raise ScriptedProviderError("script shape is invalid")
    if raw["schemaVersion"] != 1 or not isinstance(raw["scenarioId"], str) or not isinstance(raw["steps"], list):
        raise ScriptedProviderError("script version is invalid")
    steps: list[ScriptedProviderStep] = []
    try:
        for value in raw["steps"]:
            if not isinstance(value, dict):
                raise ValueError
            if set(value) != {
                "scenarioId",
                "requestOrdinal",
                "expectedToolNames",
                "responseKind",
                "toolName",
                "faultCode",
            }:
                raise ValueError
            if not isinstance(value["expectedToolNames"], list) or any(
                not isinstance(name, str) for name in value["expectedToolNames"]
            ):
                raise ValueError
            steps.append(ScriptedProviderStep(
                scenario_id=value["scenarioId"],
                request_ordinal=value["requestOrdinal"],
                expected_tool_names=tuple(value["expectedToolNames"]),
                response_kind=value["responseKind"],
                tool_name=value["toolName"],
                fault_code=value["faultCode"],
            ))
        return ScriptedProviderScript(scenario_id=raw["scenarioId"], steps=tuple(steps))
    except (KeyError, TypeError, ValueError):
        raise ScriptedProviderError("script step is invalid") from None


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
        tool_name = step.tool_name or "create_entry"
        arguments = (
            '{"title":"release qualification entry"}'
            if tool_name == "create_entry"
            else "{}"
        )
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
                                    "name": tool_name,
                                    "arguments": arguments,
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }


def _default_script() -> ScriptedProviderScript:
    """Small health-safe script used only when the profile supplies no schedule."""
    return ScriptedProviderScript(
        scenario_id="release",
        steps=tuple(
            ScriptedProviderStep(
                scenario_id="release",
                request_ordinal=ordinal,
                expected_tool_names=(),
                response_kind="content",
                tool_name=None,
                fault_code=None,
            )
            for ordinal in range(1, 65)
        ),
    )


class _ScriptedProviderHandler(BaseHTTPRequestHandler):
    provider = ScriptedProvider(_default_script())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Request bodies and provider prompts must never enter the release log.
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"provider":"scripted"}')
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1_048_576:
                raise ScriptedProviderError("request_too_large")
            raw = self.rfile.read(length)
            request = json.loads(raw.decode("utf-8"))
            scenario = self.headers.get("X-MindAtlas-Scenario", "release")
            ordinal = int(self.headers.get("X-MindAtlas-Request-Ordinal", "1"))
            response = self.provider.complete(
                request,
                scenario_id=scenario,
                request_ordinal=ordinal,
                endpoint=self.path,
            )
            encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (ValueError, TypeError, json.JSONDecodeError, ScriptedProviderError):
            self.send_error(400, "scripted_provider_request_invalid")


def serve(*, bind: str = "0.0.0.0:8081", script: Path | None = None) -> None:
    host, separator, raw_port = bind.rpartition(":")
    if not separator or not host or not raw_port.isdigit():
        raise ScriptedProviderError("scripted_provider_bind_invalid")
    if script is not None:
        _ScriptedProviderHandler.provider = ScriptedProvider(load_script(script))
    server = ThreadingHTTPServer((host, int(raw_port)), _ScriptedProviderHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic release provider")
    parser.add_argument("--bind", default=os.environ.get("SCRIPTED_PROVIDER_BIND", "127.0.0.1:8081"))
    parser.add_argument(
        "--script",
        type=Path,
        default=(Path(os.environ["SCRIPTED_PROVIDER_SCRIPT"]) if os.environ.get("SCRIPTED_PROVIDER_SCRIPT") else None),
    )
    args = parser.parse_args(argv)
    serve(bind=args.bind, script=args.script)
    return 0


__all__ = [
    "ScriptedProvider",
    "ScriptedProviderError",
    "ScriptedProviderScript",
    "ScriptedProviderStep",
    "UNSUPPORTED_BOUNDARY_TOOL_NAMES",
    "load_script",
    "main",
    "serve",
]


if __name__ == "__main__":
    raise SystemExit(main())
