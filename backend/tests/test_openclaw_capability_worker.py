"""OpenClaw bounded capability worker boundary tests (Plan 02 Task 8)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)
os.environ.setdefault("APP_BUILD_REVISION", "plan02-task8-local")
os.environ.setdefault("APP_ENV", "test")
os.environ.pop("OPENCLAW_CAPABILITY_RUNTIME_MODE", None)

bootstrap_backend_imports()
reset_caches()


@pytest.fixture()
def db_session():
    reset_caches()
    from app.config import get_settings

    get_settings.cache_clear()
    from tests._db import make_session

    session = make_session()
    try:
        yield session
    finally:
        session.close()
        get_settings.cache_clear()


def test_worker_request_repr_hides_authorization() -> None:
    from app.openclaw_integration.runtime_worker import build_worker_request

    req = build_worker_request(
        capability_key="search_entries",
        preferred_locale="zh",
        raw_payload={"query": "x"},
        authorization_header="Bearer super-secret-token",
        source_header="src",
        channel_header="ch",
        session_header="sess",
        tool_header="tool",
    )
    rendered = repr(req)
    self_text = str(req)
    assert "super-secret-token" not in rendered
    assert "Bearer" not in rendered
    assert "super-secret-token" not in self_text
    assert not hasattr(req, "selected_mode")
    assert req.capability_key == "search_entries"


def test_worker_request_bounds_payload_size() -> None:
    from app.common.exceptions import ApiException
    from app.openclaw_integration.runtime_worker import build_worker_request

    huge = {"blob": "x" * (1_048_576 + 10)}
    with pytest.raises(ApiException) as exc:
        build_worker_request(
            capability_key="search_entries",
            preferred_locale=None,
            raw_payload=huge,
            authorization_header="Bearer x",
            source_header=None,
            channel_header=None,
            session_header=None,
            tool_header=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.code == 42261


def test_event_loop_heartbeat_during_blocking_worker() -> None:
    from app.openclaw_integration.runtime_worker import (
        build_worker_request,
        execute_openclaw_capability_in_worker,
    )
    from app.openclaw_integration.schemas import OpenClawCapabilityExecuteResponse
    from app.database import SessionLocal

    heartbeats: list[float] = []

    async def heartbeat() -> None:
        for _ in range(5):
            heartbeats.append(time.perf_counter())
            await asyncio.sleep(0.02)

    def slow_execute(*args: Any, **kwargs: Any) -> OpenClawCapabilityExecuteResponse:
        time.sleep(0.12)
        return OpenClawCapabilityExecuteResponse(
            capability_key="search_entries",
            tool_name="mindatlas_search_entries",
            result={"total": 0, "items": []},
        )

    req = build_worker_request(
        capability_key="search_entries",
        preferred_locale="zh",
        raw_payload={"query": "hb"},
        authorization_header="Bearer unused",
        source_header=None,
        channel_header=None,
        session_header=None,
        tool_header=None,
    )

    async def run() -> OpenClawCapabilityExecuteResponse:
        with patch(
            "app.openclaw_integration.runtime_worker._run_worker_sync",
            side_effect=slow_execute,
        ):
            hb_task = asyncio.create_task(heartbeat())
            result = await execute_openclaw_capability_in_worker(
                req,
                session_factory=SessionLocal,
            )
            await hb_task
            return result

    result = asyncio.run(run())
    assert result.capability_key == "search_entries"
    assert len(heartbeats) >= 3


def test_worker_limiter_caps_concurrency() -> None:
    from app.openclaw_integration.runtime_worker import (
        OPENCLAW_CAPABILITY_WORKER_LIMIT,
        build_worker_request,
        execute_openclaw_capability_in_worker,
    )
    from app.openclaw_integration.schemas import OpenClawCapabilityExecuteResponse

    entered = 0
    max_entered = 0

    def blocking_worker(*args: Any, **kwargs: Any) -> OpenClawCapabilityExecuteResponse:
        nonlocal entered, max_entered
        entered += 1
        max_entered = max(max_entered, entered)
        time.sleep(0.05)
        entered -= 1
        return OpenClawCapabilityExecuteResponse(
            capability_key="search_entries",
            tool_name="mindatlas_search_entries",
            result={"ok": True},
        )

    def make_req(i: int):
        return build_worker_request(
            capability_key="search_entries",
            preferred_locale=None,
            raw_payload={"query": str(i)},
            authorization_header="Bearer x",
            source_header=None,
            channel_header=None,
            session_header=None,
            tool_header=None,
        )

    async def run() -> None:
        with patch(
            "app.openclaw_integration.runtime_worker._run_worker_sync",
            side_effect=blocking_worker,
        ):
            tasks = [
                asyncio.create_task(execute_openclaw_capability_in_worker(make_req(i)))
                for i in range(OPENCLAW_CAPABILITY_WORKER_LIMIT + 2)
            ]
            results = await asyncio.gather(*tasks)
            assert len(results) == OPENCLAW_CAPABILITY_WORKER_LIMIT + 2

    asyncio.run(run())
    assert max_entered <= OPENCLAW_CAPABILITY_WORKER_LIMIT


def test_worker_closes_session_on_failure(db_session) -> None:
    from app.common.exceptions import ApiException
    from app.database import SessionLocal
    from app.openclaw_integration.runtime_worker import (
        build_worker_request,
        execute_openclaw_capability_in_worker,
    )

    closed: list[bool] = []

    class TrackingSession:
        def __init__(self) -> None:
            self._inner = SessionLocal()
            self.closed = False

        def close(self) -> None:
            self.closed = True
            closed.append(True)
            self._inner.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def factory() -> TrackingSession:
        return TrackingSession()

    fail_req = build_worker_request(
        capability_key="search_entries",
        preferred_locale="zh",
        raw_payload={"query": "fail"},
        authorization_header="Bearer invalid",
        source_header=None,
        channel_header=None,
        session_header=None,
        tool_header=None,
    )

    async def run() -> None:
        with pytest.raises(ApiException) as exc:
            await execute_openclaw_capability_in_worker(
                fail_req,
                session_factory=factory,
            )
        assert exc.value.code in {40161, 40361}

    asyncio.run(run())
    assert closed and closed[-1] is True


def test_execute_route_has_no_request_db_dependency() -> None:
    import inspect

    from app.openclaw_integration import router as oc_router

    sig = inspect.signature(oc_router.execute_openclaw_capability)
    assert "db" not in sig.parameters
    source = inspect.getsource(oc_router.execute_openclaw_capability)
    assert "Depends(get_db)" not in source
    assert "execute_openclaw_capability_in_worker" in source
    assert "OpenClawRuntimeModeSelector" not in source
    assert "selected_mode" not in source


def test_import_boundary_capabilities_do_not_import_openclaw() -> None:
    caps = Path(__file__).resolve().parents[1] / "app" / "assistant" / "capabilities"
    hits = []
    for path in caps.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "openclaw_integration" in text:
            hits.append(str(path))
    assert hits == []
