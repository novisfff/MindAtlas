"""Runtime admission immediately before durable Run insertion (Plan 06 Task 6).

Selects immutable ``runtime_kind`` after conversation/message preparation and
Main Agent + compatible-worker preflight. A permitted Legacy fallback happens
only before the durable Run row exists.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)


def admit_and_select_runtime(
    db: Session,
    *,
    mode: str | None = None,
    execution_kind: str = "production",
    app_build_revision: str | None = None,
    require_compatible_worker: bool = True,
) -> tuple[str, str | None, dict[str, Any]]:
    """Return ``(runtime_kind, reason_code, create_run_kwargs)``.

    - ``runtime_kind`` is ``legacy`` or ``main_agent`` (immutable once inserted).
    - On Main Agent admission failure in read_only mode, returns legacy with a
      reason when automatic pre-insert fallback is allowed.
    - Raises only for hard configuration errors that must fail the request.
    """
    settings = get_settings()
    main_agent_mode = str(
        mode if mode is not None else getattr(settings, "assistant_main_agent_mode", "off")
        or "off"
    ).strip().lower()
    build = (
        app_build_revision
        or getattr(settings, "app_build_revision", None)
        or "development"
    ).strip()

    # Mode off / shadow production: never construct Main Agent for production.
    if main_agent_mode == "off":
        return "legacy", "mode_off", {}
    if main_agent_mode == "shadow" and execution_kind == "production":
        return "legacy", "mode_shadow_production", {}

    # read_only (or shadow evaluation): attempt Main Agent admission.
    if main_agent_mode not in {"read_only", "shadow"}:
        return "legacy", "mode_off", {}

    try:
        from app.assistant.main_agent.service import (
            MainAgentAdmissionError,
            admit_main_agent,
            should_construct_main_agent,
        )
    except Exception:
        logger.exception("main agent admission import failed")
        return "legacy", "adapter_unavailable_before_request", {}

    if not should_construct_main_agent(
        mode=main_agent_mode, execution_kind=execution_kind  # type: ignore[arg-type]
    ):
        return "legacy", "mode_off", {}

    try:
        admission = admit_main_agent(
            db,
            mode=main_agent_mode,  # type: ignore[arg-type]
            execution_kind=execution_kind,  # type: ignore[arg-type]
            app_build_revision=build,
        )
    except MainAgentAdmissionError as exc:
        logger.info("main agent admission denied reason=%s", exc.reason_code)
        # Pre-insert fallback to Legacy is allowed for safe admission failures.
        return "legacy", str(exc.reason_code), {}
    except Exception:
        logger.exception("main agent admission failed")
        return "legacy", "adapter_unavailable_before_request", {}

    if require_compatible_worker:
        try:
            from app.assistant.durable.worker_registry import (
                RUNTIME_CONTRACT_VERSION,
                WorkerRegistry,
            )

            ttl = timedelta(
                seconds=int(
                    getattr(settings, "assistant_worker_registration_ttl_sec", 20) or 20
                )
            )
            ok = WorkerRegistry(db).has_compatible_worker(
                app_build_revision=build,
                runtime_contract_version=RUNTIME_CONTRACT_VERSION,
                required_checkpoint_codec_version=1,
                registration_ttl=ttl,
            )
            if not ok:
                logger.info(
                    "no compatible worker for main_agent admission build=%s", build
                )
                return "legacy", "no_compatible_worker", {}
        except Exception:
            logger.exception("compatible worker check failed")
            return "legacy", "no_compatible_worker", {}

    kwargs: dict[str, Any] = {
        "runtime_kind": "main_agent",
        "runtime_contract_version": 1,
        "required_app_build_revision": build,
        "memory_commit_status": "pending",
    }
    # Optional deadline from profile wall-time budget if available.
    try:
        wall_ms = getattr(
            getattr(admission, "snapshot", None), "output_budget", None
        )
        max_wall = getattr(wall_ms, "max_wall_time_ms", None) if wall_ms else None
        if max_wall is not None and int(max_wall) > 0:
            from app.common.time import utcnow

            kwargs["deadline_at"] = utcnow() + timedelta(milliseconds=int(max_wall))
    except Exception:
        pass

    return "main_agent", None, kwargs


__all__ = ["admit_and_select_runtime"]
