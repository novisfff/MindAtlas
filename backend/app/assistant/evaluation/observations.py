"""Observed evaluation outcomes (Plan 09 Task 6).

Actual execution fields are folded exclusively from Eval events, isolated
capability call ledgers, obligations, completion state, and adapter probes.
Dataset assertion fields (acceptable skills, expected completion) are never
accepted as actual constructor inputs.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence
from uuid import UUID

from app.assistant.domain.contracts import FrozenContract

ExecutionKind = Literal["direct_answer", "golden_skill", "capability"]

# Required hard-safety counter keys. Missing evidence stays None (never 0).
REQUIRED_SAFETY_COUNTER_KEYS: tuple[str, ...] = (
    "budget_policy_bypass",
    "false_completion_pending_obligation",
    "unresolved_obligation_falsely_completed",
    "schema_escape",
    "secret_exposure",
    "duplicate_write",
)

# Production mutation probe keys used by dataset assertions.
DEFAULT_PRODUCTION_DELTA_KEYS: tuple[str, ...] = (
    "assistant_chat_run",
    "capability_call",
    "assistant_memory",
    "artifact",
)


class ObservedEvalCaseOutcome(FrozenContract):
    """Immutable per-case actual outcome derived only from observed runtime state.

    Construction must never accept acceptable Skill keys or expected completion
    as actual fields. Compare with dataset assertions only after observation.
    """

    schema_version: Literal[1] = 1
    eval_case_id: UUID
    execution_kind: ExecutionKind
    actual_active_skills: tuple[str, ...]
    capability_path: tuple[str, ...]
    completed: bool
    stop_reason: str
    obligations_pending: int
    production_delta: dict[str, int | None]
    safety_counters: dict[str, int | None]

    # Banned constructor aliases — Pydantic forbids extra, but keep explicit
    # documentation that these must never be mapped into actual fields.
    # acceptable_skill_keys / expect_completion / expected_mode are not fields.


def fold_observed_outcome(
    *,
    eval_case_id: UUID,
    events: Sequence[Mapping[str, Any]] | None = None,
    call_records: Sequence[Any] | None = None,
    active_skills: Sequence[str] | None = None,
    capability_path: Sequence[str] | None = None,
    completed: bool | None = None,
    stop_reason: str | None = None,
    obligations_pending: int | None = None,
    production_delta: Mapping[str, int | None] | None = None,
    safety_counters: Mapping[str, int | None] | None = None,
    execution_kind: ExecutionKind | None = None,
) -> ObservedEvalCaseOutcome:
    """Fold owner-qualified Eval events and test-owned call rows into an outcome.

    Only observed runtime evidence is accepted. Missing safety counters remain
    ``None`` (never manufactured zeros). Active skills come from runtime state
    (manifest / events), never from case.acceptable_skill_keys.
    """
    del call_records  # reserved for future ledger folding; callers pass skills directly

    skills = _skills_from_events(events) if events else ()
    if active_skills is not None:
        skills = tuple(str(s) for s in active_skills if str(s).strip())

    path = tuple(str(p) for p in (capability_path or ()) if str(p).strip())
    if not path and events:
        path = _capability_path_from_events(events)

    done = bool(completed) if completed is not None else _completed_from_events(events)
    reason = str(stop_reason or _stop_reason_from_events(events) or "unknown")
    pending = int(obligations_pending if obligations_pending is not None else 0)

    kind: ExecutionKind
    if execution_kind is not None:
        kind = execution_kind
    elif skills:
        kind = "golden_skill"
    elif path:
        kind = "capability"
    else:
        kind = "direct_answer"

    delta = _normalize_optional_int_map(
        production_delta,
        required_keys=DEFAULT_PRODUCTION_DELTA_KEYS,
        default_missing=None,
    )
    counters = _normalize_optional_int_map(
        safety_counters,
        required_keys=REQUIRED_SAFETY_COUNTER_KEYS,
        default_missing=None,
    )

    return ObservedEvalCaseOutcome(
        eval_case_id=eval_case_id,
        execution_kind=kind,
        actual_active_skills=skills,
        capability_path=path,
        completed=done,
        stop_reason=reason,
        obligations_pending=pending,
        production_delta=delta,
        safety_counters=counters,
    )


def observed_to_case_outcome_mapping(
    observed: ObservedEvalCaseOutcome,
    *,
    case: Any | None = None,
) -> dict[str, Any]:
    """Project observed actuals + separate case assertions into runner mapping.

    Assertion fields (acceptable/forbidden/expect_completion) are read only from
    the dataset case object, never inverted into actual fields.
    """
    acceptable: list[str] = []
    forbidden: list[str] = []
    acceptable_paths: list[list[str]] = []
    expect_completion = True
    direct_answer_allowed = observed.execution_kind == "direct_answer"
    case_key = ""
    if case is not None:
        acceptable = [str(x) for x in (getattr(case, "acceptable_skill_keys", None) or ())]
        forbidden = [str(x) for x in (getattr(case, "forbidden_skill_keys", None) or ())]
        raw_paths = list(getattr(case, "acceptable_capability_paths", None) or ())
        for item in raw_paths:
            if isinstance(item, (list, tuple)):
                acceptable_paths.append([str(x) for x in item])
            elif isinstance(item, str):
                acceptable_paths.append([item])
        expect_completion = bool(getattr(case, "expect_completion", True))
        case_key = str(getattr(case, "case_key", "") or "")
        expected_mode = str(getattr(case, "expected_mode", "") or "")
        if expected_mode == "direct_answer":
            direct_answer_allowed = True
        assertion_json = getattr(case, "assertion_json", None) or {}
        if isinstance(assertion_json, Mapping):
            if "direct_answer_allowed" in assertion_json:
                direct_answer_allowed = bool(assertion_json["direct_answer_allowed"])

    return {
        "eval_case_id": str(observed.eval_case_id),
        "case_key": case_key,
        "execution_kind": observed.execution_kind,
        "activated_skills": list(observed.actual_active_skills),
        "actual_active_skills": list(observed.actual_active_skills),
        "acceptable_skills": acceptable,
        "forbidden_skills": forbidden,
        "capability_path": list(observed.capability_path),
        "acceptable_capability_paths": acceptable_paths,
        "direct_answer_allowed": direct_answer_allowed,
        "expect_completion": expect_completion,
        "completed": bool(observed.completed),
        "stop_reason": observed.stop_reason,
        "obligations_pending": int(observed.obligations_pending),
        "production_delta": dict(observed.production_delta),
        "safety_counters": dict(observed.safety_counters),
    }


def merge_safety_counters(
    *sources: Mapping[str, int | None] | None,
) -> dict[str, int | None]:
    """Merge counter maps; first non-None value wins per key. Missing stays None."""
    out: dict[str, int | None] = {k: None for k in REQUIRED_SAFETY_COUNTER_KEYS}
    for source in sources:
        if not source:
            continue
        for key, value in source.items():
            name = str(key)
            if name not in out:
                out[name] = value if value is None else int(value)
                continue
            if out[name] is None and value is not None:
                out[name] = int(value)
            elif out[name] is None and value is None:
                continue
            elif value is not None:
                # Prefer explicit later non-None only when earlier was None.
                pass
    return out


def _skills_from_events(events: Sequence[Mapping[str, Any]] | None) -> tuple[str, ...]:
    if not events:
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for event in events:
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if not isinstance(payload, Mapping):
            payload = event if isinstance(event, Mapping) else {}
        candidates = (
            payload.get("actual_active_skills")
            or payload.get("activated_skills")
            or payload.get("active_skills")
            or ()
        )
        if isinstance(candidates, str):
            candidates = (candidates,)
        for skill in candidates:
            name = str(skill).strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        single = payload.get("skill_key") or payload.get("canonical_name")
        if single:
            name = str(single).strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
    return tuple(ordered)


def _capability_path_from_events(
    events: Sequence[Mapping[str, Any]] | None,
) -> tuple[str, ...]:
    if not events:
        return ()
    for event in reversed(list(events)):
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if not isinstance(payload, Mapping):
            continue
        path = payload.get("capability_path") or payload.get("capabilityPath")
        if isinstance(path, (list, tuple)):
            return tuple(str(x) for x in path)
    return ()


def _completed_from_events(events: Sequence[Mapping[str, Any]] | None) -> bool:
    if not events:
        return False
    for event in reversed(list(events)):
        et = str(event.get("event_type") or event.get("eventType") or "")
        if et in {"eval.case_completed", "loop.completed", "eval.run_completed"}:
            return True
        if et in {"eval.case_failed", "loop.failed", "eval.run_failed"}:
            return False
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if isinstance(payload, Mapping) and "completed" in payload:
            return bool(payload["completed"])
    return False


def _stop_reason_from_events(events: Sequence[Mapping[str, Any]] | None) -> str | None:
    if not events:
        return None
    for event in reversed(list(events)):
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if isinstance(payload, Mapping):
            reason = payload.get("stop_reason") or payload.get("failure_code")
            if reason:
                return str(reason)
        et = str(event.get("event_type") or "")
        if et:
            return et
    return None


def _normalize_optional_int_map(
    raw: Mapping[str, int | None] | None,
    *,
    required_keys: Sequence[str],
    default_missing: int | None,
) -> dict[str, int | None]:
    out: dict[str, int | None] = {k: default_missing for k in required_keys}
    if raw is None:
        return out
    for key, value in raw.items():
        name = str(key)
        if value is None:
            out[name] = None
        else:
            try:
                out[name] = int(value)
            except (TypeError, ValueError):
                out[name] = None
    return out


__all__ = [
    "DEFAULT_PRODUCTION_DELTA_KEYS",
    "REQUIRED_SAFETY_COUNTER_KEYS",
    "ObservedEvalCaseOutcome",
    "fold_observed_outcome",
    "merge_safety_counters",
    "observed_to_case_outcome_mapping",
]
