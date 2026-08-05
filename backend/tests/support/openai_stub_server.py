"""Deterministic OpenAI-compatible chat-completions stub for Compose smoke.

Stdlib-only so the container can run on python:3.11-slim without app deps.
Accepts only model ``mindatlas-smoke-model`` with streaming, no tools.
Logs method/path/status/counts only — never bodies or keys.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

SMOKE_MODEL = "mindatlas-smoke-model"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8089

FIXED_CHUNKS: tuple[dict[str, Any], ...] = (
    {
        "id": "smoke-completion",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    },
    {
        "id": "smoke-completion",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {"content": "smoke-ok"}, "finish_reason": None}
        ],
    },
    {
        "id": "smoke-completion",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
)


class StubCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.accepted = 0
        self.rejected = 0

    def inc(self, *, accepted: bool) -> None:
        with self._lock:
            self.requests += 1
            if accepted:
                self.accepted += 1
            else:
                self.rejected += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "requests": self.requests,
                "accepted": self.accepted,
                "rejected": self.rejected,
            }


COUNTERS = StubCounters()


def _safe_log(method: str, path: str, status: int) -> None:
    counts = COUNTERS.snapshot()
    print(
        f"openai_stub method={method} path={path} status={status} "
        f"requests={counts['requests']} accepted={counts['accepted']} "
        f"rejected={counts['rejected']}",
        flush=True,
    )


def evaluate_chat_completion_request(body: dict[str, Any]) -> tuple[int, str]:
    """Return (status, reason) for a parsed chat.completions body."""
    model = str(body.get("model") or "")
    if model != SMOKE_MODEL:
        return 400, "unsupported_model"
    if body.get("stream") is not True:
        return 400, "stream_required"
    if body.get("tools") or body.get("functions") or body.get("function_call"):
        return 400, "tools_not_supported"
    tool_choice = body.get("tool_choice")
    if tool_choice not in (None, "none"):
        return 400, "tools_not_supported"
    return 200, "ok"


class OpenAIStubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Suppress default access log (may echo request lines).
        return

    def _read_json(self) -> dict[str, Any] | None:
        length_raw = self.headers.get("Content-Length", "0")
        try:
            length = int(length_raw)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, chunks: tuple[dict[str, Any], ...]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            self.wfile.write(line.encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/health", "/v1/health"}:
            COUNTERS.inc(accepted=True)
            _safe_log("GET", path, 200)
            self._send_json(200, {"status": "ok"})
            return
        COUNTERS.inc(accepted=False)
        _safe_log("GET", path, 404)
        self._send_json(404, {"error": {"message": "not_found", "type": "invalid_request"}})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/v1/chat/completions", "/chat/completions"}:
            COUNTERS.inc(accepted=False)
            _safe_log("POST", path, 404)
            self._send_json(
                404, {"error": {"message": "not_found", "type": "invalid_request"}}
            )
            return

        body = self._read_json()
        if body is None:
            COUNTERS.inc(accepted=False)
            _safe_log("POST", path, 400)
            self._send_json(
                400,
                {"error": {"message": "invalid_json", "type": "invalid_request"}},
            )
            return

        status, reason = evaluate_chat_completion_request(body)
        if status != 200:
            COUNTERS.inc(accepted=False)
            _safe_log("POST", path, status)
            self._send_json(
                status,
                {"error": {"message": reason, "type": "invalid_request"}},
            )
            return

        COUNTERS.inc(accepted=True)
        _safe_log("POST", path, 200)
        self._send_sse(FIXED_CHUNKS)

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_method("PATCH")

    def _reject_method(self, method: str) -> None:
        path = urlparse(self.path).path
        COUNTERS.inc(accepted=False)
        _safe_log(method, path, 405)
        self._send_json(
            405, {"error": {"message": "method_not_allowed", "type": "invalid_request"}}
        )


def make_server(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), OpenAIStubHandler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MindAtlas OpenAI smoke stub")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port)
    print(
        f"openai_stub listening host={args.host} port={args.port} model={SMOKE_MODEL}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
