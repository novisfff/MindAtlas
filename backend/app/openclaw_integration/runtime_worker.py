"""Bounded awaited OpenClaw Capability worker boundary (Plan 02 Task 8 / 02B shared-only).

Snapshots immutable request data on the event loop, then runs authentication,
catalog freeze, and shared Capability Runtime dispatch inside a worker-owned Session.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
from sqlalchemy.orm import Session

from app.assistant.capabilities.ports import CancellationPort
from app.common.exceptions import ApiException
from app.common.request_context import get_request_id
from app.openclaw_integration.schemas import OpenClawCapabilityExecuteResponse

logger = logging.getLogger(__name__)

# Checked platform constant for v1 (not request-controlled).
OPENCLAW_CAPABILITY_WORKER_LIMIT = 8
OPENCLAW_CAPABILITY_WORKER_LIMITER = anyio.CapacityLimiter(OPENCLAW_CAPABILITY_WORKER_LIMIT)

# Bounded snapshot limits (opaque; reject oversized headers/payloads).
_MAX_HEADER_CHARS = 4096
_MAX_PAYLOAD_BYTES = 1_048_576
_MAX_CAPABILITY_KEY_CHARS = 256
_MAX_LOCALE_CHARS = 32


@dataclass(frozen=True)
class OpenClawCapabilityWorkerRequest:
    request_id: str
    capability_key: str
    preferred_locale: str | None
    payload_canonical_json: bytes = field(repr=False, compare=False)
    authorization_header: str = field(repr=False, compare=False)
    source_header: str | None = field(default=None, repr=False, compare=False)
    channel_header: str | None = field(default=None, repr=False, compare=False)
    session_header: str | None = field(default=None, repr=False, compare=False)
    tool_header: str | None = field(default=None, repr=False, compare=False)


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None


def _bound_optional_header(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiException(status_code=400, code=40000, message=f"Invalid header: {name}")
    if len(value) > _MAX_HEADER_CHARS:
        raise ApiException(status_code=400, code=40000, message=f"Header too large: {name}")
    stripped = value.strip()
    return stripped or None


def _bound_required_header(value: str | None, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        # Preserve auth semantics: missing Authorization becomes 401 inside worker auth.
        if name.lower() == "authorization":
            return ""
        raise ApiException(status_code=400, code=40000, message=f"Missing header: {name}")
    if len(value) > _MAX_HEADER_CHARS:
        raise ApiException(status_code=400, code=40000, message=f"Header too large: {name}")
    return value


def build_worker_request(
    *,
    capability_key: str,
    preferred_locale: str | None,
    raw_payload: dict[str, Any],
    authorization_header: str | None,
    source_header: str | None,
    channel_header: str | None,
    session_header: str | None,
    tool_header: str | None,
    request_id: str | None = None,
) -> OpenClawCapabilityWorkerRequest:
    if not isinstance(capability_key, str) or not capability_key.strip():
        raise ApiException(status_code=404, code=40461, message="Unknown OpenClaw capability")
    if len(capability_key) > _MAX_CAPABILITY_KEY_CHARS:
        raise ApiException(status_code=404, code=40461, message="Unknown OpenClaw capability")
    if preferred_locale is not None:
        if not isinstance(preferred_locale, str) or len(preferred_locale) > _MAX_LOCALE_CHARS:
            preferred_locale = None

    if not isinstance(raw_payload, dict):
        raise ApiException(
            status_code=422,
            code=42261,
            message="Capability input payload is invalid",
        )
    try:
        payload_bytes = json.dumps(
            raw_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code=42261,
            message="Capability input payload is invalid",
        ) from exc
    if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
        raise ApiException(
            status_code=422,
            code=42261,
            message="Capability input payload is too large",
        )

    return OpenClawCapabilityWorkerRequest(
        request_id=request_id or (get_request_id() or "-"),
        capability_key=capability_key.strip(),
        preferred_locale=preferred_locale,
        payload_canonical_json=payload_bytes,
        authorization_header=_bound_required_header(authorization_header, name="authorization"),
        source_header=_bound_optional_header(source_header, name="x-openclaw-source"),
        channel_header=_bound_optional_header(channel_header, name="x-openclaw-channel"),
        session_header=_bound_optional_header(session_header, name="x-openclaw-session"),
        tool_header=_bound_optional_header(tool_header, name="x-openclaw-tool"),
    )


def _run_worker_sync(
    request: OpenClawCapabilityWorkerRequest,
    *,
    session_factory: Callable[[], Session],
    cancellation: CancellationPort,
) -> OpenClawCapabilityExecuteResponse:
    from app.openclaw_integration.capability_adapter import (
        OpenClawAuthenticationProof,
    )
    from app.openclaw_integration.service import OpenClawIntegrationService

    session = session_factory()
    try:
        service = OpenClawIntegrationService(session)
        audit_context = service.authorize_runtime_headers(
            authorization_header=request.authorization_header,
            preferred_locale=request.preferred_locale,
            source_header=request.source_header,
            channel_header=request.channel_header,
            session_header=request.session_header,
            tool_header=request.tool_header,
        )
        auth_proof = OpenClawAuthenticationProof(principal_id="openclaw")

        try:
            payload = json.loads(request.payload_canonical_json.decode("utf-8"))
        except Exception as exc:
            raise ApiException(
                status_code=422,
                code=42261,
                message="Capability input payload is invalid",
            ) from exc
        if not isinstance(payload, dict):
            raise ApiException(
                status_code=422,
                code=42261,
                message="Capability input payload is invalid",
            )

        return service.execute_capability_in_worker(
            capability_key=request.capability_key,
            raw_payload=payload,
            audit_context=audit_context,
            preferred_locale=request.preferred_locale,
            auth_proof=auth_proof,
            cancellation=cancellation,
            request_id=request.request_id,
        )
    finally:
        try:
            session.close()
        except Exception:
            logger.error(
                "openclaw_worker_session_close_failed request_id=%s capability=%s",
                request.request_id,
                request.capability_key,
            )


async def execute_openclaw_capability_in_worker(
    request: OpenClawCapabilityWorkerRequest,
    *,
    session_factory: Callable[[], Session] | None = None,
    cancellation: CancellationPort | None = None,
) -> OpenClawCapabilityExecuteResponse:
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal
    cancel_port: CancellationPort = cancellation or _NeverCancelled()  # type: ignore[assignment]

    def _call() -> OpenClawCapabilityExecuteResponse:
        return _run_worker_sync(
            request,
            session_factory=session_factory,
            cancellation=cancel_port,
        )

    return await anyio.to_thread.run_sync(
        _call,
        abandon_on_cancel=False,
        limiter=OPENCLAW_CAPABILITY_WORKER_LIMITER,
    )


__all__ = [
    "OPENCLAW_CAPABILITY_WORKER_LIMIT",
    "OPENCLAW_CAPABILITY_WORKER_LIMITER",
    "OpenClawCapabilityWorkerRequest",
    "build_worker_request",
    "execute_openclaw_capability_in_worker",
]
