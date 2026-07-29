"""Runtime admission immediately before durable Run insertion (Plan 06 Task 6).

Selects immutable ``runtime_kind`` after conversation/message preparation and
Main Agent + compatible-worker preflight. A permitted Legacy fallback happens
only before the durable Run row exists.

Plan 10 Task 6: when ``conversation_id`` is provided and an active rollout
revision exists, freeze the durable assignment first and apply pre-insert-only
fallback. Post-insert failures never open the fallback path.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)


class RuntimeAdmissionError(RuntimeError):
    """Fail-closed runtime selection failure before durable Run insertion."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def admit_and_select_runtime(
    db: Session,
    *,
    mode: str | None = None,
    execution_kind: str = "production",
    app_build_revision: str | None = None,
    require_compatible_worker: bool = True,
    conversation_id: UUID | None = None,
    request_id: str | None = None,
    principal_scope_digest: str | None = None,
    chat_run_already_inserted: bool = False,
    use_rollout_assignment: bool = True,
) -> tuple[str, str | None, dict[str, Any]]:
    """Return ``(runtime_kind, reason_code, create_run_kwargs)``.

    - ``runtime_kind`` is ``legacy`` or ``main_agent`` (immutable once inserted).
    - On Main Agent admission failure in read_only mode, returns legacy with a
      reason when automatic pre-insert fallback is allowed.
    - When ``conversation_id`` is set and an active Plan 10 rollout revision
      exists, freezes assignment and may return pre-insert fallback metadata in
      ``create_run_kwargs`` (``_preinsert_fallback`` / ``_rollout_decision``).
    - Raises only for hard configuration errors that must fail the request.
    """
    # Plan 10: prefer durable rollout assignment when available.
    if (
        use_rollout_assignment
        and conversation_id is not None
        and not chat_run_already_inserted
    ):
        try:
            from app.assistant.migration.repository import RuntimeMigrationRepository

            active = RuntimeMigrationRepository(db).get_active_rollout_revision()
        except Exception as exc:
            logger.exception("rollout revision lookup failed; admission denied")
            raise RuntimeAdmissionError(
                "rollout_infrastructure_unavailable",
                "durable rollout revision lookup failed",
            ) from exc
        if active is not None:
            try:
                from app.assistant.migration.rollout import admit_with_rollout

                kind, reason, kwargs, _decision = admit_with_rollout(
                    db,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    execution_kind=execution_kind,
                    app_build_revision=app_build_revision,
                    require_compatible_worker=require_compatible_worker,
                    principal_scope_digest=principal_scope_digest,
                    chat_run_already_inserted=False,
                )
                return kind, reason, kwargs
            except Exception as exc:
                logger.exception("rollout admission failed; admission denied")
                raise RuntimeAdmissionError(
                    "rollout_admission_failed",
                    "durable rollout admission failed",
                ) from exc

        settings = get_settings()
        runtime_mode = str(
            getattr(settings, "assistant_runtime_mode", "legacy") or "legacy"
        ).strip().lower()
        if runtime_mode != "legacy":
            raise RuntimeAdmissionError(
                "runtime_rollout_revision_missing",
                "native Main Agent mode requires an active durable rollout revision",
            )
        return "legacy", "no_active_rollout", {}

    return _admit_main_agent_candidate(
        db,
        mode=mode,
        execution_kind=execution_kind,
        app_build_revision=app_build_revision,
        require_compatible_worker=require_compatible_worker,
    )


def _admit_main_agent_candidate(
    db: Session,
    *,
    mode: str | None = None,
    execution_kind: str = "production",
    app_build_revision: str | None = None,
    require_compatible_worker: bool = True,
) -> tuple[str, str | None, dict[str, Any]]:
    """Main Agent candidate admission for explicit internal/evaluation calls."""
    settings = get_settings()
    if mode is None:
        native_mode = str(
            getattr(settings, "assistant_runtime_mode", "legacy") or "legacy"
        ).strip().lower()
        mode = "read_only" if native_mode == "main_agent" else "off"
    main_agent_mode = str(mode or "off").strip().lower()
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
                plan08_capability_ledger_feature_digest,
            )

            ttl = timedelta(
                seconds=int(
                    getattr(settings, "assistant_worker_registration_ttl_sec", 20) or 20
                )
            )
            ledger_mode = (
                str(
                    getattr(
                        settings,
                        "assistant_capability_ledger_mode",
                        "legacy_read_only",
                    )
                    or "legacy_read_only"
                )
                .strip()
                .lower()
            )
            write_mode = (
                str(
                    getattr(settings, "assistant_main_agent_write_mode", "off") or "off"
                )
                .strip()
                .lower()
            )
            # Task 7: feature digest is required for Main Agent compatibility.
            # Task 8 will share the readiness evaluator; this path keeps the
            # WorkerCompatibility constructor as the sole matcher.
            from app.assistant.durable.worker_registry import WorkerCompatibility

            # ledger_mode / write_mode still influence whether enforced ledger
            # workers are required later; the compatibility surface always
            # carries an exact feature digest (never optional).
            _ = (ledger_mode, write_mode)
            required_feature_digest = plan08_capability_ledger_feature_digest()
            ok = WorkerRegistry(db).has_compatible_worker(
                WorkerCompatibility(
                    app_build_revision=build,
                    runtime_contract_version=RUNTIME_CONTRACT_VERSION,
                    required_checkpoint_codec_version=1,
                    required_capability_feature_digest=required_feature_digest,
                ),
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
