"""Durable human Interrupt persistence helpers (Plan 07 Task 4).

Token service, bounded schema normalization/validation, BudgetSuspension helpers,
and the Run-first Interrupt repository. HTTP decision APIs and pause CAS wiring
live in later tasks.
"""

from __future__ import annotations

import hmac
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.models import AssistantRunInterrupt
from app.assistant.durable.repository import (
    CODE_NOT_MAIN_AGENT,
    CODE_PROTOCOL_ERROR,
    CODE_RUN_NOT_FOUND,
    CODE_STALE_REVISION,
    DurableRunConflict,
    RUNTIME_KIND_MAIN_AGENT,
)
from app.assistant.models import AssistantChatRun
from app.assistant.policy.budgets import (
    BudgetLedgerState,
    compute_ledger_digest,
)
from app.assistant.workflow.durable.contracts import (
    BudgetSuspensionStateV1,
    compute_suspension_digest,
)
from app.common.time import utcnow

# ---------------------------------------------------------------------------
# Stable conflict codes (repository / token)
# ---------------------------------------------------------------------------

CODE_INTERRUPT_NOT_FOUND = "interrupt_not_found"
CODE_INTERRUPT_NOT_PENDING = "interrupt_not_pending"
CODE_INTERRUPT_EXPIRED = "interrupt_expired"
CODE_INTERRUPT_NOT_EXPIRED = "interrupt_not_expired"
CODE_INTERRUPT_TOKEN_INVALID = "interrupt_token_invalid"
CODE_INTERRUPT_TOKEN_STALE = "interrupt_token_stale"
CODE_INTERRUPT_REQUEST_MISMATCH = "interrupt_request_mismatch"
CODE_INTERRUPT_PARENT_TAMPER = "interrupt_parent_tamper"
CODE_INTERRUPT_ZERO_ACTIVE_TIME = "interrupt_zero_active_time"
CODE_INTERRUPT_IDEMPOTENCY_CONFLICT = "resolution_idempotency_conflict"
CODE_INTERRUPT_ALREADY_RESOLVED = "interrupt_already_resolved"
CODE_INTERRUPT_SCHEMA_INVALID = "interrupt_schema_invalid"
CODE_INTERRUPT_VALUES_INVALID = "interrupt_values_invalid"
CODE_INTERRUPT_COMMENT_TOO_LONG = "interrupt_comment_too_long"
CODE_INTERRUPT_OUTCOME_INVALID = "interrupt_outcome_invalid"
CODE_INTERRUPT_PENDING_EXISTS = "interrupt_pending_exists"
CODE_INTERRUPT_KEY_CONFLICT = "interrupt_key_conflict"
CODE_INTERRUPT_IMMUTABLE = "interrupt_immutable"
CODE_INTERRUPT_PEPPER_REQUIRED = "interrupt_pepper_required"
CODE_CALL_OWNED_APPROVAL_REQUIRED = "capability_call_approval_required"

INTERRUPT_STATUSES_TERMINAL = frozenset(
    {"approved", "rejected", "submitted", "cancelled", "expired"}
)
INTERRUPT_STATUSES_ALL = frozenset({"pending"}) | INTERRUPT_STATUSES_TERMINAL

APPROVAL_OUTCOMES = frozenset({"approved", "rejected", "cancelled", "expired"})
INPUT_OUTCOMES = frozenset({"submitted", "cancelled", "expired"})

# Bounded schema contract (Plan 07 §9)
MAX_SCHEMA_NESTING_DEPTH = 4
MAX_SCHEMA_FIELD_COUNT = 40
MAX_REQUEST_JSON_BYTES = 64 * 1024
MAX_SUBMITTED_JSON_BYTES = 256 * 1024
MAX_COMMENT_CHARS_HARD = 4000

RENDER_FIELD_TYPES = frozenset(
    {
        "input",
        "textarea",
        "switch",
        "select",
        "radio",
        "checkbox_group",
        "tag_selector",
        "date",
        "time",
    }
)

_SUPPORTED_SCHEMA_TYPES = frozenset(
    {"string", "number", "integer", "boolean", "enum", "array", "object"}
)

_ALLOWED_SCHEMA_FORMATS = frozenset({"date", "time"})

_REJECTED_SCHEMA_KEYS = frozenset(
    {
        "$ref",
        "$dynamicRef",
        "$recursiveRef",
        "not",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "dependentRequired",
        "patternProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contentMediaType",
        "contentEncoding",
        "contentSchema",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InterruptConflict(DurableRunConflict):
    """Interrupt-specific CAS / protocol conflict (subclass of DurableRunConflict)."""


class InterruptSchemaError(ValueError):
    """Bounded schema normalization/validation failure."""


class InterruptTokenError(ValueError):
    """Token generation / verification failure."""


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def derive_interrupt_key(
    *,
    run_id: UUID,
    root_invocation_digest: str,
    frame_id: UUID,
    node_visit_id: str,
    logical_interrupt_ordinal: int,
) -> str:
    """Deterministic logical pause key (excludes execution_attempt).

    Crash retries must converge on the same Interrupt row via this key.
    """
    digest = str(root_invocation_digest).strip().lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("root_invocation_digest must be a 64-char lowercase hex digest")
    visit = str(node_visit_id).strip()
    if not visit:
        raise ValueError("node_visit_id must be non-empty")
    ordinal = int(logical_interrupt_ordinal)
    if ordinal < 1:
        raise ValueError("logical_interrupt_ordinal must be >= 1")
    # Stable short key: full identity hashed for column width.
    raw = (
        f"interrupt_key|{run_id}|{digest}|{frame_id}|{visit}|{ordinal}"
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def compute_request_digest(
    *,
    kind: str,
    request_payload: Mapping[str, Any],
    field_schema: Mapping[str, Any] | None,
    initial_values: Mapping[str, Any],
) -> str:
    return sha256_canonical_json(
        {
            "fieldSchema": field_schema,
            "initialValues": dict(initial_values),
            "kind": kind,
            "requestPayload": dict(request_payload),
        }
    )


def compute_field_schema_digest(field_schema: Mapping[str, Any] | None) -> str | None:
    if field_schema is None:
        return None
    return sha256_canonical_json(dict(field_schema))


def compute_resolution_digest(
    *,
    interrupt_id: UUID,
    resolution_request_id: UUID,
    expected_token_revision: int,
    expected_request_revision: int,
    expected_run_revision: int,
    outcome: str,
    submitted_values: Mapping[str, Any] | None,
    comment: str | None,
) -> str:
    """Internal equality guard. Excludes raw token and token digest."""
    return sha256_canonical_json(
        {
            "comment": comment,
            "expectedRequestRevision": int(expected_request_revision),
            "expectedRunRevision": int(expected_run_revision),
            "expectedTokenRevision": int(expected_token_revision),
            "interruptId": str(interrupt_id),
            "outcome": outcome,
            "resolutionRequestId": str(resolution_request_id),
            "submittedValues": dict(submitted_values) if submitted_values is not None else None,
        }
    )


# ---------------------------------------------------------------------------
# HMAC token service
# ---------------------------------------------------------------------------


def require_interrupt_token_pepper(pepper: str | None) -> str:
    value = (pepper or "").strip()
    if not value:
        raise InterruptTokenError(
            f"{CODE_INTERRUPT_PEPPER_REQUIRED}: ASSISTANT_INTERRUPT_TOKEN_PEPPER is required"
        )
    return value


def generate_resume_token(*, nbytes: int = 32) -> str:
    """Return URL-safe token text from at least 32 random bytes."""
    if nbytes < 32:
        raise ValueError("resume token must use at least 32 random bytes")
    return secrets.token_urlsafe(nbytes)


def digest_resume_token(*, pepper: str, token: str) -> str:
    """HMAC-SHA256(pepper, token) as lowercase hex. Never plain SHA256(pepper||token)."""
    p = require_interrupt_token_pepper(pepper)
    raw = hmac.new(p.encode("utf-8"), token.encode("utf-8"), sha256).hexdigest()
    return raw


def verify_resume_token(*, pepper: str, token: str, expected_digest: str | None) -> bool:
    if not expected_digest:
        return False
    actual = digest_resume_token(pepper=pepper, token=token)
    return hmac.compare_digest(actual, expected_digest)


# ---------------------------------------------------------------------------
# Budget suspension helpers
# ---------------------------------------------------------------------------


def compute_remaining_active_ms(
    *,
    parent_deadline_at_utc: datetime,
    database_now: datetime,
) -> int:
    """max(0, floor(parent.deadline_at_utc - database_now)) in milliseconds.

    Flooring and clamping may shorten but never extend the active allowance.
    """
    if parent_deadline_at_utc.tzinfo is None or database_now.tzinfo is None:
        raise ValueError("deadline and database_now must be timezone-aware")
    deadline = parent_deadline_at_utc.astimezone(timezone.utc)
    now = database_now.astimezone(timezone.utc)
    delta = (deadline - now).total_seconds() * 1000.0
    remaining = int(math.floor(delta))
    return max(0, remaining)


def build_budget_suspension_state(
    *,
    run_id: UUID,
    interrupt_id: UUID,
    parent_budget_revision_id: UUID,
    parent_ledger_revision: int,
    parent_ledger_digest: str,
    suspended_at_utc: datetime,
    remaining_active_ms: int,
    human_wait_expires_at_utc: datetime,
) -> BudgetSuspensionStateV1:
    if remaining_active_ms < 0:
        raise ValueError("remaining_active_ms must be >= 0")
    digest = compute_suspension_digest(
        run_id=run_id,
        interrupt_id=interrupt_id,
        parent_budget_revision_id=parent_budget_revision_id,
        parent_ledger_revision=parent_ledger_revision,
        parent_ledger_digest=parent_ledger_digest,
        suspended_at_utc=suspended_at_utc,
        remaining_active_ms=remaining_active_ms,
        human_wait_expires_at_utc=human_wait_expires_at_utc,
    )
    return BudgetSuspensionStateV1(
        run_id=run_id,
        interrupt_id=interrupt_id,
        parent_budget_revision_id=parent_budget_revision_id,
        parent_ledger_revision=parent_ledger_revision,
        parent_ledger_digest=parent_ledger_digest,
        suspended_at_utc=suspended_at_utc,
        remaining_active_ms=remaining_active_ms,
        human_wait_expires_at_utc=human_wait_expires_at_utc,
        suspension_digest=digest,
    )


def derive_resume_budget_ledger(
    *,
    parent: BudgetLedgerState,
    remaining_active_ms: int,
    database_now: datetime,
    child_revision: int | None = None,
) -> BudgetLedgerState:
    """Derive one Plan 05 child budget from a frozen suspension.

    Copies limits, owner limits, all call/round/token/depth/repeat usage,
    reservations, denial count, and started_at_utc byte-for-byte. Only
    revision, deadline_at_utc = database_now + remaining_active_ms, and
    the resulting ledger_digest change.
    """
    if remaining_active_ms < 0:
        raise ValueError("remaining_active_ms must be >= 0")
    if database_now.tzinfo is None:
        raise ValueError("database_now must be timezone-aware")
    now = database_now.astimezone(timezone.utc)
    new_deadline = now + timedelta(milliseconds=int(remaining_active_ms))
    revision = int(child_revision) if child_revision is not None else int(parent.revision) + 1
    if revision <= int(parent.revision):
        raise ValueError("child budget revision must be greater than parent revision")
    digest = compute_ledger_digest(
        revision=revision,
        limits=parent.limits,
        owner_limits=parent.owner_limits,
        provider_rounds_started=parent.provider_rounds_started,
        main_agent_cycles_started=parent.main_agent_cycles_started,
        capability_calls_started=parent.capability_calls_started,
        completion_followups_started=parent.completion_followups_started,
        prompt_tokens_used=parent.prompt_tokens_used,
        completion_tokens_used=parent.completion_tokens_used,
        owner_calls_started=parent.owner_calls_started,
        global_read_signatures=parent.global_read_signatures,
        owner_read_signatures=parent.owner_read_signatures,
        reservations=parent.reservations,
        denial_count=parent.denial_count,
        started_at_utc=parent.started_at_utc,
        deadline_at_utc=new_deadline,
    )
    return BudgetLedgerState(
        revision=revision,
        limits=parent.limits,
        owner_limits=parent.owner_limits,
        provider_rounds_started=parent.provider_rounds_started,
        main_agent_cycles_started=parent.main_agent_cycles_started,
        capability_calls_started=parent.capability_calls_started,
        completion_followups_started=parent.completion_followups_started,
        prompt_tokens_used=parent.prompt_tokens_used,
        completion_tokens_used=parent.completion_tokens_used,
        owner_calls_started=parent.owner_calls_started,
        global_read_signatures=parent.global_read_signatures,
        owner_read_signatures=parent.owner_read_signatures,
        reservations=parent.reservations,
        denial_count=parent.denial_count,
        started_at_utc=parent.started_at_utc,
        deadline_at_utc=new_deadline,
        ledger_digest=digest,
    )


def non_time_budget_snapshot(state: BudgetLedgerState) -> dict[str, Any]:
    """Byte-identical non-time fields for fixed-vector assertions."""
    return {
        "limits": state.limits.model_dump(mode="json", by_alias=True),
        "ownerLimits": [o.model_dump(mode="json", by_alias=True) for o in state.owner_limits],
        "providerRoundsStarted": state.provider_rounds_started,
        "mainAgentCyclesStarted": state.main_agent_cycles_started,
        "capabilityCallsStarted": state.capability_calls_started,
        "completionFollowupsStarted": state.completion_followups_started,
        "promptTokensUsed": state.prompt_tokens_used,
        "completionTokensUsed": state.completion_tokens_used,
        "ownerCallsStarted": [
            o.model_dump(mode="json", by_alias=True) for o in state.owner_calls_started
        ],
        "globalReadSignatures": [
            s.model_dump(mode="json", by_alias=True) for s in state.global_read_signatures
        ],
        "ownerReadSignatures": [
            s.model_dump(mode="json", by_alias=True) for s in state.owner_read_signatures
        ],
        "reservations": [
            r.model_dump(mode="json", by_alias=True) for r in state.reservations
        ],
        "denialCount": state.denial_count,
        "startedAtUtc": state.started_at_utc.astimezone(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Bounded schema normalization / validation / render
# ---------------------------------------------------------------------------


def _json_size_bytes(value: Any) -> int:
    import json

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _count_fields(schema: Mapping[str, Any]) -> int:
    props = schema.get("properties")
    if not isinstance(props, Mapping):
        return 0
    total = len(props)
    for child in props.values():
        if isinstance(child, Mapping):
            total += _count_fields(child)
            items = child.get("items")
            if isinstance(items, Mapping):
                total += _count_fields(items)
    return total


def _max_depth(schema: Mapping[str, Any], *, depth: int = 1) -> int:
    deepest = depth
    props = schema.get("properties")
    if isinstance(props, Mapping):
        for child in props.values():
            if isinstance(child, Mapping):
                deepest = max(deepest, _max_depth(child, depth=depth + 1))
    items = schema.get("items")
    if isinstance(items, Mapping):
        deepest = max(deepest, _max_depth(items, depth=depth + 1))
    return deepest


def normalize_interrupt_field_schema(
    schema: Mapping[str, Any] | None,
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    """Normalize a Draft 2020-12 subset for durable Interrupt storage.

    Object root only; max depth 4; max 40 fields; no remote $ref; expands local
    $defs only after depth-check. Rejects executable/secret/URL-action widgets.
    """
    if schema is None:
        if required:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: field_schema is required"
            )
        return None
    if not isinstance(schema, Mapping):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: field_schema must be an object"
        )
    if _json_size_bytes(schema) > MAX_REQUEST_JSON_BYTES:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: field_schema exceeds {MAX_REQUEST_JSON_BYTES} bytes"
        )

    # Expand local $defs once if present (no remote $ref).
    expanded = _expand_local_defs(dict(schema))
    normalized = _normalize_schema_node(expanded, path="$", depth=1)
    if not isinstance(normalized, dict):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: normalized root must be an object"
        )
    root_type = normalized.get("type")
    if root_type != "object":
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: root type must be object"
        )
    if normalized.get("additionalProperties") is not False:
        normalized["additionalProperties"] = False
    field_count = _count_fields(normalized)
    if field_count > MAX_SCHEMA_FIELD_COUNT:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: field count {field_count} exceeds {MAX_SCHEMA_FIELD_COUNT}"
        )
    depth = _max_depth(normalized)
    if depth > MAX_SCHEMA_NESTING_DEPTH:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: nesting depth {depth} exceeds {MAX_SCHEMA_NESTING_DEPTH}"
        )
    return normalized


def _expand_local_defs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", None)
    if defs is None:
        defs = schema.pop("definitions", None)
    if defs is None:
        return schema
    if not isinstance(defs, Mapping):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: $defs must be an object"
        )
    # Reject any $ref that is not a local #/$defs/... pointer after expansion pass.
    return _resolve_local_refs(schema, defs=dict(defs), seen=set())


def _resolve_local_refs(
    node: Any,
    *,
    defs: Mapping[str, Any],
    seen: set[str],
) -> Any:
    if isinstance(node, Mapping):
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: only local $defs $ref is allowed"
                )
            name = ref[len("#/$defs/") :]
            if name in seen:
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: cyclic $ref {ref}"
                )
            if name not in defs:
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: unknown $ref {ref}"
                )
            return _resolve_local_refs(defs[name], defs=defs, seen=seen | {name})
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _REJECTED_SCHEMA_KEYS:
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: forbidden schema key {key}"
                )
            out[key] = _resolve_local_refs(value, defs=defs, seen=seen)
        return out
    if isinstance(node, list):
        return [_resolve_local_refs(item, defs=defs, seen=seen) for item in node]
    return node


def _normalize_schema_node(
    node: Any,
    *,
    path: str,
    depth: int,
) -> Any:
    if depth > MAX_SCHEMA_NESTING_DEPTH + 1:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: nesting depth exceeds {MAX_SCHEMA_NESTING_DEPTH} at {path}"
        )
    if not isinstance(node, Mapping):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: schema node must be object at {path}"
        )
    for key in node:
        if key in _REJECTED_SCHEMA_KEYS:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: forbidden schema key {key} at {path}"
            )
    out: dict[str, Any] = {}
    type_val = node.get("type")
    if "enum" in node and type_val is None:
        type_val = "string"
    if type_val is None:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: type is required at {path}"
        )
    if isinstance(type_val, list):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: union types are not allowed at {path}"
        )
    if type_val not in {"string", "number", "integer", "boolean", "array", "object"}:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_SCHEMA_INVALID}: unsupported type {type_val!r} at {path}"
        )
    out["type"] = type_val

    # Preserve safe annotations only.
    for key in (
        "title",
        "description",
        "default",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "exclusiveMinimum",
        "exclusiveMaximum",
    ):
        if key in node:
            out[key] = node[key]

    # Allowlisted formats for HITL date/time render widgets.
    if "format" in node:
        fmt = node["format"]
        if fmt in _ALLOWED_SCHEMA_FORMATS:
            out["format"] = fmt

    if "enum" in node:
        enum_vals = node["enum"]
        if not isinstance(enum_vals, list) or not enum_vals:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: enum must be a non-empty array at {path}"
            )
        if len(enum_vals) > 64:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: enum too large at {path}"
            )
        for item in enum_vals:
            if not isinstance(item, (str, int, float, bool)) or item is True or item is False:
                if not isinstance(item, (str, int, float)) and not isinstance(item, bool):
                    raise InterruptSchemaError(
                        f"{CODE_INTERRUPT_SCHEMA_INVALID}: enum items must be primitives at {path}"
                    )
        out["enum"] = list(enum_vals)

    # Reject complex / unsafe pattern.
    if "pattern" in node:
        pattern = node["pattern"]
        if not isinstance(pattern, str) or len(pattern) > 128:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: unsafe pattern at {path}"
            )
        # Very crude complexity guard.
        if any(tok in pattern for tok in ("(?", "*+", "++", "{,")):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: unsafe pattern complexity at {path}"
            )
        out["pattern"] = pattern

    if type_val == "object":
        props = node.get("properties")
        if props is None:
            props = {}
        if not isinstance(props, Mapping):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: properties must be an object at {path}"
            )
        norm_props: dict[str, Any] = {}
        for prop_name, prop_schema in props.items():
            if not isinstance(prop_name, str) or not prop_name:
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: property names must be non-empty strings at {path}"
                )
            # Reject secret-looking property names as schema fields for HITL.
            compact = "".join(ch for ch in prop_name.lower() if ch.isalnum())
            if compact in {
                "password",
                "secret",
                "apikey",
                "authorization",
                "credential",
                "token",
                "cookie",
            }:
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: secret property name rejected at {path}.{prop_name}"
                )
            norm_props[prop_name] = _normalize_schema_node(
                prop_schema, path=f"{path}.{prop_name}", depth=depth + 1
            )
        out["properties"] = norm_props
        required = node.get("required")
        if required is None:
            out["required"] = []
        else:
            if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: required must be string array at {path}"
                )
            for name in required:
                if name not in norm_props:
                    raise InterruptSchemaError(
                        f"{CODE_INTERRUPT_SCHEMA_INVALID}: required field {name!r} missing at {path}"
                    )
            out["required"] = list(required)
        out["additionalProperties"] = False
    elif type_val == "array":
        items = node.get("items")
        if items is None:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: array items required at {path}"
            )
        out["items"] = _normalize_schema_node(items, path=f"{path}.items", depth=depth + 1)
        if "minItems" in node:
            out["minItems"] = int(node["minItems"])
        if "maxItems" in node:
            out["maxItems"] = int(node["maxItems"])
            if int(out["maxItems"]) > 100:
                raise InterruptSchemaError(
                    f"{CODE_INTERRUPT_SCHEMA_INVALID}: maxItems exceeds 100 at {path}"
                )

    return out


def validate_submitted_values(
    *,
    field_schema: Mapping[str, Any] | None,
    values: Mapping[str, Any] | None,
    kind: str,
    outcome: str,
) -> dict[str, Any]:
    """Server-authoritative submitted value validation."""
    kind = str(kind)
    outcome = str(outcome)
    if values is None:
        values = {}
    if not isinstance(values, Mapping):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: submitted_values must be an object"
        )
    if _json_size_bytes(values) > MAX_SUBMITTED_JSON_BYTES:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: submitted_values exceed {MAX_SUBMITTED_JSON_BYTES} bytes"
        )

    if kind == "approval" and field_schema is None:
        if values:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: simple approval accepts empty values only"
            )
        return {}

    if outcome in {"cancelled", "expired"}:
        # Values ignored for terminal non-submit outcomes.
        return {}

    if kind == "input" and outcome == "submitted":
        if field_schema is None:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: input requires field_schema"
            )
        return _validate_object_against_schema(dict(values), field_schema, path="$")

    if kind == "approval" and outcome in {"approved", "rejected"} and field_schema is not None:
        return _validate_object_against_schema(dict(values), field_schema, path="$")

    if values:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: values not permitted for outcome {outcome}"
        )
    return {}


def _validate_object_against_schema(
    values: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    if schema.get("type") != "object":
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: expected object schema at {path}"
        )
    props = schema.get("properties") or {}
    if not isinstance(props, Mapping):
        props = {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    for name in required:
        if name not in values:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: missing required field {name} at {path}"
            )
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in props:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: unexpected field {key} at {path}"
            )
        out[key] = _validate_value_against_schema(
            value, props[key], path=f"{path}.{key}"
        )
    return out


def _check_numeric_bounds(
    value: int | float,
    schema: Mapping[str, Any],
    *,
    path: str,
) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: value below minimum at {path}"
        )
    if "maximum" in schema and value > schema["maximum"]:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: value above maximum at {path}"
        )
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: value not above exclusiveMinimum at {path}"
        )
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_VALUES_INVALID}: value not below exclusiveMaximum at {path}"
        )


def _validate_value_against_schema(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: str,
) -> Any:
    type_val = schema.get("type")
    if "enum" in schema:
        if value not in schema["enum"]:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: value not in enum at {path}"
            )
        return value
    if type_val == "string":
        if not isinstance(value, str):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: expected string at {path}"
            )
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: string too short at {path}"
            )
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: string too long at {path}"
            )
        return value
    if type_val == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: expected integer at {path}"
            )
        _check_numeric_bounds(value, schema, path=path)
        return value
    if type_val == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: expected number at {path}"
            )
        _check_numeric_bounds(value, schema, path=path)
        return value
    if type_val == "boolean":
        if not isinstance(value, bool):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: expected boolean at {path}"
            )
        return value
    if type_val == "array":
        if not isinstance(value, list):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: expected array at {path}"
            )
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: array too short at {path}"
            )
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: array too long at {path}"
            )
        items_schema = schema.get("items") or {}
        return [
            _validate_value_against_schema(item, items_schema, path=f"{path}[{idx}]")
            for idx, item in enumerate(value)
        ]
    if type_val == "object":
        if not isinstance(value, Mapping):
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_VALUES_INVALID}: expected object at {path}"
            )
        return _validate_object_against_schema(value, schema, path=path)
    raise InterruptSchemaError(
        f"{CODE_INTERRUPT_VALUES_INVALID}: unsupported schema type at {path}"
    )


def render_interrupt_fields(
    field_schema: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Allowlisted shared HITL render model (no arbitrary component code)."""
    if field_schema is None:
        return []
    props = field_schema.get("properties") or {}
    if not isinstance(props, Mapping):
        return []
    required = set(field_schema.get("required") or [])
    fields: list[dict[str, Any]] = []
    for name, prop in props.items():
        if not isinstance(prop, Mapping):
            continue
        fields.append(_render_one_field(name, prop, required=name in required))
    return fields


def _render_one_field(
    name: str,
    prop: Mapping[str, Any],
    *,
    required: bool,
) -> dict[str, Any]:
    type_val = prop.get("type")
    title = prop.get("title") or name
    description = prop.get("description")
    field_type = "input"
    options: list[Any] | None = None
    if "enum" in prop:
        field_type = "select"
        options = list(prop["enum"])
    elif type_val == "boolean":
        field_type = "switch"
    elif type_val == "string":
        max_len = prop.get("maxLength")
        if isinstance(max_len, int) and max_len > 200:
            field_type = "textarea"
        fmt = prop.get("format")
        if fmt == "date":
            field_type = "date"
        elif fmt == "time":
            field_type = "time"
    elif type_val == "array":
        field_type = "checkbox_group"
        items = prop.get("items") or {}
        if isinstance(items, Mapping) and "enum" in items:
            options = list(items["enum"])
    if field_type not in RENDER_FIELD_TYPES:
        field_type = "input"
    out: dict[str, Any] = {
        "name": name,
        "type": field_type,
        "label": title,
        "required": required,
    }
    if description:
        out["description"] = description
    if options is not None:
        out["options"] = options
    return out


def validate_comment(comment: str | None, *, max_chars: int = MAX_COMMENT_CHARS_HARD) -> str | None:
    if comment is None:
        return None
    if not isinstance(comment, str):
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_COMMENT_TOO_LONG}: comment must be plain text"
        )
    limit = min(int(max_chars), MAX_COMMENT_CHARS_HARD)
    if len(comment) > limit:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_COMMENT_TOO_LONG}: comment exceeds {limit} characters"
        )
    return comment


def allowed_outcomes_for_kind(kind: str) -> frozenset[str]:
    if kind == "approval":
        return APPROVAL_OUTCOMES
    if kind == "input":
        return INPUT_OUTCOMES
    raise ValueError(f"unknown interrupt kind: {kind}")


def map_outcome_to_status(*, kind: str, outcome: str) -> str:
    allowed = allowed_outcomes_for_kind(kind)
    if outcome not in allowed:
        raise InterruptSchemaError(
            f"{CODE_INTERRUPT_OUTCOME_INVALID}: outcome {outcome!r} not allowed for kind {kind}"
        )
    return outcome


# ---------------------------------------------------------------------------
# Repository result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterruptCreateResult:
    interrupt: AssistantRunInterrupt
    created: bool
    suspension: BudgetSuspensionStateV1


@dataclass(frozen=True)
class InterruptTokenResult:
    interrupt: AssistantRunInterrupt
    token: str
    token_revision: int


@dataclass(frozen=True)
class InterruptResolveResult:
    interrupt: AssistantRunInterrupt
    created_resolution: bool
    idempotent_replay: bool


# ---------------------------------------------------------------------------
# Interrupt repository (Run-first lock order)
# ---------------------------------------------------------------------------


class DurableInterruptRepository:
    """Persist and mutate durable human Interrupts.

    Lock order is always ``AssistantChatRun`` then ``assistant_run_interrupt``.
    Request/suspension identity is immutable after insert.
    """

    def __init__(
        self,
        db: Session,
        *,
        token_pepper: str | None = None,
        comment_max_chars: int = MAX_COMMENT_CHARS_HARD,
        default_ttl_sec: int = 86400,
        max_ttl_sec: int = 604800,
    ) -> None:
        self.db = db
        self._token_pepper = token_pepper
        self._comment_max_chars = min(int(comment_max_chars), MAX_COMMENT_CHARS_HARD)
        self._default_ttl_sec = int(default_ttl_sec)
        self._max_ttl_sec = min(int(max_ttl_sec), 604800)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_interrupt(
        self,
        interrupt_id: UUID,
        *,
        for_update: bool = False,
        nowait: bool = False,
    ) -> AssistantRunInterrupt | None:
        stmt = select(AssistantRunInterrupt).where(AssistantRunInterrupt.id == interrupt_id)
        if for_update:
            stmt = stmt.with_for_update(nowait=nowait)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_key(
        self,
        *,
        run_id: UUID,
        interrupt_key: str,
        for_update: bool = False,
    ) -> AssistantRunInterrupt | None:
        stmt = select(AssistantRunInterrupt).where(
            AssistantRunInterrupt.run_id == run_id,
            AssistantRunInterrupt.interrupt_key == interrupt_key,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_pending_for_run(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> AssistantRunInterrupt | None:
        stmt = select(AssistantRunInterrupt).where(
            AssistantRunInterrupt.run_id == run_id,
            AssistantRunInterrupt.status == "pending",
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def list_for_run(self, run_id: UUID) -> list[AssistantRunInterrupt]:
        stmt = (
            select(AssistantRunInterrupt)
            .where(AssistantRunInterrupt.run_id == run_id)
            .order_by(AssistantRunInterrupt.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_by_resolution_request_id(
        self,
        *,
        run_id: UUID,
        resolution_request_id: UUID,
        for_update: bool = False,
    ) -> AssistantRunInterrupt | None:
        stmt = select(AssistantRunInterrupt).where(
            AssistantRunInterrupt.run_id == run_id,
            AssistantRunInterrupt.resolution_request_id == resolution_request_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Create / insert-or-read
    # ------------------------------------------------------------------

    def create_pending_interrupt(
        self,
        *,
        run_id: UUID,
        interrupt_id: UUID,
        interrupt_key: str,
        kind: str,
        checkpoint_id: UUID,
        manifest_revision_id: UUID,
        budget_revision_id: UUID,
        workflow_frame_id: UUID | None,
        node_id: str | None,
        node_visit_id: str | None,
        request_run_revision: int,
        request_payload: Mapping[str, Any],
        field_schema: Mapping[str, Any] | None,
        initial_values: Mapping[str, Any] | None,
        parent_ledger: BudgetLedgerState,
        parent_budget_revision_id: UUID,
        suspended_at_utc: datetime | None = None,
        expires_at: datetime | None = None,
        ttl_sec: int | None = None,
        owner_skill_package_id: UUID | None = None,
        owner_skill_version_id: UUID | None = None,
        capability_call_id: UUID | None = None,
        interrupt_origin: str = "workflow_node",
        request_revision: int = 1,
        lock_run: bool = True,
    ) -> InterruptCreateResult:
        """Insert-or-read a pending Interrupt with immutable BudgetSuspensionStateV1.

        Run is locked first. Zero remaining active time refuses creation of a new
        row. Crash retries that hit an existing key with the same interrupt_id and
        request_digest return the stored suspension as truth (clock may have moved).
        """
        kind = str(kind)
        if kind not in {"approval", "input"}:
            raise InterruptConflict(CODE_PROTOCOL_ERROR, f"invalid interrupt kind {kind}")
        origin = str(interrupt_origin)
        if origin == "workflow_node":
            if capability_call_id is not None or not all(
                (workflow_frame_id, node_id, node_visit_id)
            ):
                raise InterruptConflict(
                    CODE_PROTOCOL_ERROR,
                    "workflow_node interrupt requires frame/node/visit and forbids capability_call_id",
                )
        elif origin == "capability_call":
            if capability_call_id is None or any(
                value is not None for value in (workflow_frame_id, node_id, node_visit_id)
            ):
                raise InterruptConflict(
                    CODE_PROTOCOL_ERROR,
                    "capability_call interrupt requires call id and forbids workflow identity",
                )
        else:
            raise InterruptConflict(
                CODE_PROTOCOL_ERROR, f"invalid interrupt_origin {origin!r}"
            )

        run = self._lock_run(run_id) if lock_run else self._require_run(run_id)
        now = self._db_now()

        # Parent pointer agreement (independent of suspension clock).
        if parent_budget_revision_id != budget_revision_id:
            raise InterruptConflict(
                CODE_INTERRUPT_PARENT_TAMPER,
                "budget_revision_id must equal parent_budget_revision_id",
                run=run,
            )
        if run.current_budget_revision_id is not None and run.current_budget_revision_id != parent_budget_revision_id:
            raise InterruptConflict(
                CODE_INTERRUPT_PARENT_TAMPER,
                "run current_budget_revision_id must match suspension parent",
                run=run,
            )
        if int(parent_ledger.revision) < 0:
            raise InterruptConflict(CODE_PROTOCOL_ERROR, "invalid parent ledger revision", run=run)

        # Schema normalize + request identity (must match on crash retry).
        if kind == "input" and field_schema is None:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: input requires field_schema"
            )
        norm_schema = normalize_interrupt_field_schema(
            field_schema, required=(kind == "input")
        )
        init_vals = dict(initial_values or {})
        if _json_size_bytes(dict(request_payload)) > MAX_REQUEST_JSON_BYTES:
            raise InterruptSchemaError(
                f"{CODE_INTERRUPT_SCHEMA_INVALID}: request_payload exceeds bound"
            )
        req_digest = compute_request_digest(
            kind=kind,
            request_payload=request_payload,
            field_schema=norm_schema,
            initial_values=init_vals,
        )
        schema_digest = compute_field_schema_digest(norm_schema)

        # Insert-or-read BEFORE recomputing suspension from wall clock.
        # Stored suspension is immutable identity; a later retry must not reject
        # the committed row because remaining_active_ms / suspended_at moved.
        existing = self.get_by_key(run_id=run_id, interrupt_key=interrupt_key, for_update=True)
        if existing is not None:
            if existing.id != interrupt_id:
                raise InterruptConflict(
                    CODE_INTERRUPT_KEY_CONFLICT,
                    "interrupt_key owned by a different interrupt_id",
                    run=run,
                )
            if str(existing.request_digest) != req_digest:
                raise InterruptConflict(
                    CODE_INTERRUPT_REQUEST_MISMATCH,
                    "immutable request_digest mismatch on re-create",
                    run=run,
                )
            return InterruptCreateResult(
                interrupt=existing,
                created=False,
                suspension=BudgetSuspensionStateV1.model_validate(
                    existing.budget_suspension_state
                ),
            )

        # New insert path only: compute suspension from current clock.
        suspended_at = suspended_at_utc or now
        if suspended_at.tzinfo is None:
            raise ValueError("suspended_at_utc must be timezone-aware")

        ttl = self._default_ttl_sec if ttl_sec is None else int(ttl_sec)
        if ttl < 1 or ttl > self._max_ttl_sec:
            raise InterruptConflict(
                CODE_PROTOCOL_ERROR,
                f"interrupt TTL must be in [1, {self._max_ttl_sec}]",
            )
        exp = expires_at or (suspended_at + timedelta(seconds=ttl))
        if exp.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

        remaining = compute_remaining_active_ms(
            parent_deadline_at_utc=parent_ledger.deadline_at_utc,
            database_now=suspended_at,
        )
        if remaining <= 0:
            raise InterruptConflict(
                CODE_INTERRUPT_ZERO_ACTIVE_TIME,
                "no positive active time remains; refuse interrupt creation",
                run=run,
            )

        suspension = build_budget_suspension_state(
            run_id=run_id,
            interrupt_id=interrupt_id,
            parent_budget_revision_id=parent_budget_revision_id,
            parent_ledger_revision=int(parent_ledger.revision),
            parent_ledger_digest=str(parent_ledger.ledger_digest),
            suspended_at_utc=suspended_at.astimezone(timezone.utc),
            remaining_active_ms=remaining,
            human_wait_expires_at_utc=exp.astimezone(timezone.utc),
        )

        pending = self.get_pending_for_run(run_id, for_update=True)
        if pending is not None:
            raise InterruptConflict(
                CODE_INTERRUPT_PENDING_EXISTS,
                "run already has a pending interrupt",
                run=run,
            )

        row = AssistantRunInterrupt(
            id=interrupt_id,
            run_id=run_id,
            interrupt_key=interrupt_key,
            kind=kind,
            status="pending",
            checkpoint_id=checkpoint_id,
            resolution_checkpoint_id=None,
            manifest_revision_id=manifest_revision_id,
            owner_skill_package_id=owner_skill_package_id,
            owner_skill_version_id=owner_skill_version_id,
            capability_call_id=capability_call_id,
            interrupt_origin=origin,
            workflow_frame_id=workflow_frame_id,
            node_id=str(node_id) if node_id is not None else None,
            node_visit_id=str(node_visit_id) if node_visit_id is not None else None,
            request_revision=int(request_revision),
            request_run_revision=int(request_run_revision),
            resolution_run_revision=None,
            budget_revision_id=budget_revision_id,
            budget_suspension_state=suspension.model_dump(mode="json", by_alias=True),
            budget_suspension_digest=suspension.suspension_digest,
            resolution_budget_revision_id=None,
            request_payload=dict(request_payload),
            request_digest=req_digest,
            field_schema=norm_schema,
            field_schema_digest=schema_digest,
            initial_values=init_vals,
            submitted_values=None,
            decision=None,
            comment=None,
            resume_token_digest=None,
            token_revision=0,
            resolution_request_id=None,
            resolution_digest=None,
            expires_at=exp.astimezone(timezone.utc),
            created_at=now,
            updated_at=now,
            resolved_at=None,
            token_rotated_at=None,
        )
        self.db.add(row)
        self.db.flush()
        return InterruptCreateResult(interrupt=row, created=True, suspension=suspension)

    # ------------------------------------------------------------------
    # Token rotation
    # ------------------------------------------------------------------

    def rotate_token(
        self,
        *,
        run_id: UUID,
        interrupt_id: UUID,
        expected_request_revision: int,
        expected_run_revision: int,
        token_pepper: str | None = None,
    ) -> InterruptTokenResult:
        """Lock Run then Interrupt; rotate digest; return raw token once.

        Does not extend expires_at or active budgets.
        """
        pepper = require_interrupt_token_pepper(
            token_pepper if token_pepper is not None else self._token_pepper
        )
        run = self._lock_run(run_id)
        row = self._lock_interrupt(interrupt_id)
        if row.run_id != run_id:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_FOUND,
                "interrupt does not belong to run",
                run=run,
            )
        if str(row.interrupt_origin) == "capability_call":
            raise InterruptConflict(
                CODE_CALL_OWNED_APPROVAL_REQUIRED,
                "call-owned approvals require the operator decision boundary",
                run=run,
            )
        if row.status != "pending":
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_PENDING,
                f"interrupt status is {row.status}",
                run=run,
            )
        now = self._db_now()
        if self._as_utc(row.expires_at) <= now:
            raise InterruptConflict(
                CODE_INTERRUPT_EXPIRED,
                "interrupt has expired",
                run=run,
            )
        if int(row.request_revision) != int(expected_request_revision):
            raise InterruptConflict(
                CODE_STALE_REVISION,
                "request_revision mismatch",
                run=run,
            )
        if int(row.request_run_revision) != int(expected_run_revision):
            raise InterruptConflict(
                CODE_STALE_REVISION,
                "request_run_revision mismatch",
                run=run,
            )
        if int(run.state_revision) != int(expected_run_revision):
            raise InterruptConflict(
                CODE_STALE_REVISION,
                "run state_revision mismatch",
                run=run,
            )

        raw = generate_resume_token()
        digest = digest_resume_token(pepper=pepper, token=raw)
        row.resume_token_digest = digest
        row.token_revision = int(row.token_revision) + 1
        row.token_rotated_at = now
        row.updated_at = now
        # expires_at unchanged; suspension unchanged.
        self.db.flush()
        return InterruptTokenResult(
            interrupt=row,
            token=raw,
            token_revision=int(row.token_revision),
        )

    # ------------------------------------------------------------------
    # Resolution (one-shot + idempotent request id)
    # ------------------------------------------------------------------

    def resolve_interrupt(
        self,
        *,
        run_id: UUID,
        interrupt_id: UUID,
        resolution_request_id: UUID,
        token: str,
        expected_token_revision: int,
        expected_request_revision: int,
        expected_run_revision: int,
        outcome: str,
        submitted_values: Mapping[str, Any] | None = None,
        comment: str | None = None,
        resolution_checkpoint_id: UUID | None = None,
        resolution_budget_revision_id: UUID | None = None,
        resolution_run_revision: int | None = None,
        token_pepper: str | None = None,
        queues_execution: bool = False,
        prepare_queued_children: (
            Callable[
                [AssistantChatRun, AssistantRunInterrupt],
                tuple[UUID, UUID, int],
            ]
            | None
        ) = None,
    ) -> InterruptResolveResult:
        """Resolve pending Interrupt with Run-first lock and idempotency-before-token.

        Ordering (Plan 07 §10.4 / §11.2):
        1. lock Run
        2. idempotency lookup by (run_id, resolution_request_id)
        3. only for unknown IDs: lock Interrupt, verify pending/token/revisions/deadline
        4. validate decision/values/comment
        5. optionally prepare/flush queued children under the same lock
        6. mutate Interrupt + flush

        ``prepare_queued_children`` (if provided) is invoked only after unknown-ID
        validation succeeds. Signature::

            (run, interrupt) -> (checkpoint_id, budget_id, resolution_run_revision)

        Explicit resolution pointer kwargs remain supported for direct repository tests.
        """
        pepper = require_interrupt_token_pepper(
            token_pepper if token_pepper is not None else self._token_pepper
        )
        run = self._lock_run(run_id)

        # Idempotency first (Plan 07 §10.4 / §11.2).
        existing = self.get_by_resolution_request_id(
            run_id=run_id,
            resolution_request_id=resolution_request_id,
            for_update=True,
        )
        if existing is not None:
            expected_digest = compute_resolution_digest(
                interrupt_id=interrupt_id,
                resolution_request_id=resolution_request_id,
                expected_token_revision=expected_token_revision,
                expected_request_revision=expected_request_revision,
                expected_run_revision=expected_run_revision,
                outcome=outcome,
                submitted_values=submitted_values,
                comment=comment,
            )
            if existing.id != interrupt_id:
                raise InterruptConflict(
                    CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
                    "resolution_request_id owned by another interrupt",
                    run=run,
                )
            if existing.resolution_digest != expected_digest:
                raise InterruptConflict(
                    CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
                    "resolution_request_id reused with different digest",
                    run=run,
                )
            if existing.status == "pending":
                raise InterruptConflict(
                    CODE_PROTOCOL_ERROR,
                    "resolution_request_id points at still-pending interrupt",
                    run=run,
                )
            return InterruptResolveResult(
                interrupt=existing,
                created_resolution=False,
                idempotent_replay=True,
            )

        row = self._lock_interrupt(interrupt_id)
        if row.run_id != run_id:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_FOUND,
                "interrupt does not belong to run",
                run=run,
            )
        if row.status != "pending":
            raise InterruptConflict(
                CODE_INTERRUPT_ALREADY_RESOLVED,
                f"interrupt already terminal with status {row.status}",
                run=run,
            )

        now = self._db_now()
        if self._as_utc(row.expires_at) <= now and outcome != "expired":
            raise InterruptConflict(
                CODE_INTERRUPT_EXPIRED,
                "interrupt has expired",
                run=run,
            )
        if int(row.request_revision) != int(expected_request_revision):
            raise InterruptConflict(CODE_STALE_REVISION, "request_revision mismatch", run=run)
        if int(row.request_run_revision) != int(expected_run_revision):
            raise InterruptConflict(
                CODE_STALE_REVISION, "request_run_revision mismatch", run=run
            )
        if int(run.state_revision) != int(expected_run_revision):
            raise InterruptConflict(
                CODE_STALE_REVISION,
                "run state_revision mismatch",
                run=run,
            )
        if int(row.token_revision) != int(expected_token_revision):
            raise InterruptConflict(
                CODE_INTERRUPT_TOKEN_STALE, "token_revision mismatch", run=run
            )
        if not verify_resume_token(
            pepper=pepper,
            token=token,
            expected_digest=row.resume_token_digest,
        ):
            raise InterruptConflict(
                CODE_INTERRUPT_TOKEN_INVALID, "resume token HMAC mismatch", run=run
            )

        status = map_outcome_to_status(kind=str(row.kind), outcome=outcome)
        clean_comment = validate_comment(comment, max_chars=self._comment_max_chars)
        clean_values = validate_submitted_values(
            field_schema=row.field_schema,
            values=submitted_values,
            kind=str(row.kind),
            outcome=outcome,
        )

        # Derive/flush children only after unknown-ID validation under Run lock.
        # Never build orphans on the replay path.
        if queues_execution:
            if prepare_queued_children is not None:
                prepared = prepare_queued_children(run, row)
                if prepared is None or len(prepared) != 3:
                    raise InterruptConflict(
                        CODE_PROTOCOL_ERROR,
                        "prepare_queued_children must return (checkpoint_id, budget_id, resolution_run_revision)",
                        run=run,
                    )
                resolution_checkpoint_id = prepared[0]
                resolution_budget_revision_id = prepared[1]
                if resolution_run_revision is None:
                    resolution_run_revision = prepared[2]
            if resolution_checkpoint_id is None or resolution_budget_revision_id is None:
                raise InterruptConflict(
                    CODE_PROTOCOL_ERROR,
                    "queued resolution requires resolution_checkpoint_id and resolution_budget_revision_id",
                    run=run,
                )
        else:
            if prepare_queued_children is not None:
                raise InterruptConflict(
                    CODE_PROTOCOL_ERROR,
                    "prepare_queued_children is only valid for queued resolutions",
                    run=run,
                )
            if resolution_checkpoint_id is not None or resolution_budget_revision_id is not None:
                raise InterruptConflict(
                    CODE_PROTOCOL_ERROR,
                    "terminal non-queued outcome requires null resolution budget/checkpoint",
                    run=run,
                )

        res_digest = compute_resolution_digest(
            interrupt_id=interrupt_id,
            resolution_request_id=resolution_request_id,
            expected_token_revision=expected_token_revision,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
            outcome=outcome,
            submitted_values=submitted_values,
            comment=comment,
        )

        # One pending -> terminal mutation. Consume token.
        row.status = status
        row.decision = outcome
        row.submitted_values = clean_values if clean_values else None
        row.comment = clean_comment
        row.resolution_request_id = resolution_request_id
        row.resolution_digest = res_digest
        row.resolution_checkpoint_id = resolution_checkpoint_id
        row.resolution_budget_revision_id = resolution_budget_revision_id
        row.resolution_run_revision = (
            int(resolution_run_revision)
            if resolution_run_revision is not None
            else int(run.state_revision)
        )
        row.resume_token_digest = None  # consume
        row.resolved_at = now
        row.updated_at = now
        self.db.flush()
        return InterruptResolveResult(
            interrupt=row,
            created_resolution=True,
            idempotent_replay=False,
        )

    def expire_interrupt(
        self,
        *,
        run_id: UUID,
        interrupt_id: UUID,
        resolution_request_id: UUID | None = None,
    ) -> InterruptResolveResult:
        """Mark pending Interrupt expired (typed expiry branch). No resume budget."""
        run = self._lock_run(run_id)
        row = self._lock_interrupt(interrupt_id)
        if row.run_id != run_id:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_FOUND, "interrupt does not belong to run", run=run
            )
        if row.status != "pending":
            if row.status == "expired":
                return InterruptResolveResult(
                    interrupt=row, created_resolution=False, idempotent_replay=True
                )
            raise InterruptConflict(
                CODE_INTERRUPT_ALREADY_RESOLVED,
                f"interrupt already terminal with status {row.status}",
                run=run,
            )
        now = self._db_now()
        if self._as_utc(row.expires_at) > now:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_EXPIRED,
                "interrupt has not reached expires_at",
                run=run,
            )
        req_id = resolution_request_id or uuid4()
        res_digest = compute_resolution_digest(
            interrupt_id=interrupt_id,
            resolution_request_id=req_id,
            expected_token_revision=int(row.token_revision),
            expected_request_revision=int(row.request_revision),
            expected_run_revision=int(row.request_run_revision),
            outcome="expired",
            submitted_values=None,
            comment=None,
        )
        row.status = "expired"
        row.decision = "expired"
        row.resolution_request_id = req_id
        row.resolution_digest = res_digest
        row.resolution_checkpoint_id = None
        row.resolution_budget_revision_id = None
        row.resolution_run_revision = int(run.state_revision)
        row.resume_token_digest = None
        row.resolved_at = now
        row.updated_at = now
        self.db.flush()
        return InterruptResolveResult(
            interrupt=row, created_resolution=True, idempotent_replay=False
        )

    def cancel_interrupt(
        self,
        *,
        run_id: UUID,
        interrupt_id: UUID,
        resolution_request_id: UUID | None = None,
        comment: str | None = None,
        resolution_run_revision: int | None = None,
    ) -> InterruptResolveResult:
        """Terminal cancellation; no active child budget revision."""
        run = self._lock_run(run_id)
        row = self._lock_interrupt(interrupt_id)
        if row.run_id != run_id:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_FOUND, "interrupt does not belong to run", run=run
            )
        if row.status != "pending":
            if row.status == "cancelled":
                return InterruptResolveResult(
                    interrupt=row, created_resolution=False, idempotent_replay=True
                )
            raise InterruptConflict(
                CODE_INTERRUPT_ALREADY_RESOLVED,
                f"interrupt already terminal with status {row.status}",
                run=run,
            )
        now = self._db_now()
        clean_comment = validate_comment(comment, max_chars=self._comment_max_chars)
        req_id = resolution_request_id or uuid4()
        res_digest = compute_resolution_digest(
            interrupt_id=interrupt_id,
            resolution_request_id=req_id,
            expected_token_revision=int(row.token_revision),
            expected_request_revision=int(row.request_revision),
            expected_run_revision=int(row.request_run_revision),
            outcome="cancelled",
            submitted_values=None,
            comment=clean_comment,
        )
        row.status = "cancelled"
        row.decision = "cancelled"
        row.comment = clean_comment
        row.resolution_request_id = req_id
        row.resolution_digest = res_digest
        row.resolution_checkpoint_id = None
        row.resolution_budget_revision_id = None
        row.resolution_run_revision = int(
            run.state_revision
            if resolution_run_revision is None
            else resolution_run_revision
        )
        row.resume_token_digest = None
        row.resolved_at = now
        row.updated_at = now
        self.db.flush()
        return InterruptResolveResult(
            interrupt=row, created_resolution=True, idempotent_replay=False
        )

    # ------------------------------------------------------------------
    # Controlled purge (Plan 06 flag)
    # ------------------------------------------------------------------

    def purge_for_run(self, run_id: UUID) -> int:
        """Delete Interrupt rows for a Run. Caller must set purge flag on PostgreSQL."""
        rows = self.list_for_run(run_id)
        for row in rows:
            self.db.delete(row)
        self.db.flush()
        return len(rows)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _db_now(self) -> datetime:
        """Database-time clock; falls back to process UTC (matches Plan 06 repo)."""
        try:
            value = self.db.scalar(select(func.now()))
            if isinstance(value, datetime):
                return self._as_utc(value)
        except Exception:
            pass
        return utcnow()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Normalize SQLite-naive timestamps to aware UTC for comparisons."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _lock_run(self, run_id: UUID) -> AssistantChatRun:
        stmt = (
            select(AssistantChatRun)
            .where(AssistantChatRun.id == run_id)
            .with_for_update()
        )
        run = self.db.execute(stmt).scalar_one_or_none()
        if run is None:
            raise InterruptConflict(CODE_RUN_NOT_FOUND, "run not found")
        if str(run.runtime_kind) != RUNTIME_KIND_MAIN_AGENT:
            raise InterruptConflict(CODE_NOT_MAIN_AGENT, "run is not main_agent", run=run)
        return run

    def _require_run(self, run_id: UUID) -> AssistantChatRun:
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            raise InterruptConflict(CODE_RUN_NOT_FOUND, "run not found")
        if str(run.runtime_kind) != RUNTIME_KIND_MAIN_AGENT:
            raise InterruptConflict(CODE_NOT_MAIN_AGENT, "run is not main_agent", run=run)
        return run

    def _lock_interrupt(self, interrupt_id: UUID) -> AssistantRunInterrupt:
        stmt = (
            select(AssistantRunInterrupt)
            .where(AssistantRunInterrupt.id == interrupt_id)
            .with_for_update()
        )
        row = self.db.execute(stmt).scalar_one_or_none()
        if row is None:
            raise InterruptConflict(CODE_INTERRUPT_NOT_FOUND, "interrupt not found")
        return row


def assert_no_sensitive_token_leak(payload: Any, *, corpus: Sequence[str]) -> None:
    """Redaction corpus helper: fail if any raw token/digest fragment appears."""
    import json

    text = json.dumps(payload, default=str) if not isinstance(payload, str) else payload
    for item in corpus:
        if item and item in text:
            raise AssertionError(f"sensitive token material leaked: {item[:8]}...")


__all__ = [
    "APPROVAL_OUTCOMES",
    "CODE_CALL_OWNED_APPROVAL_REQUIRED",
    "CODE_INTERRUPT_ALREADY_RESOLVED",
    "CODE_INTERRUPT_COMMENT_TOO_LONG",
    "CODE_INTERRUPT_EXPIRED",
    "CODE_INTERRUPT_IDEMPOTENCY_CONFLICT",
    "CODE_INTERRUPT_IMMUTABLE",
    "CODE_INTERRUPT_KEY_CONFLICT",
    "CODE_INTERRUPT_NOT_FOUND",
    "CODE_INTERRUPT_NOT_EXPIRED",
    "CODE_INTERRUPT_NOT_PENDING",
    "CODE_INTERRUPT_OUTCOME_INVALID",
    "CODE_INTERRUPT_PARENT_TAMPER",
    "CODE_INTERRUPT_PENDING_EXISTS",
    "CODE_INTERRUPT_PEPPER_REQUIRED",
    "CODE_INTERRUPT_REQUEST_MISMATCH",
    "CODE_INTERRUPT_SCHEMA_INVALID",
    "CODE_INTERRUPT_TOKEN_INVALID",
    "CODE_INTERRUPT_TOKEN_STALE",
    "CODE_INTERRUPT_VALUES_INVALID",
    "CODE_INTERRUPT_ZERO_ACTIVE_TIME",
    "DurableInterruptRepository",
    "INPUT_OUTCOMES",
    "INTERRUPT_STATUSES_ALL",
    "INTERRUPT_STATUSES_TERMINAL",
    "InterruptConflict",
    "InterruptCreateResult",
    "InterruptResolveResult",
    "InterruptSchemaError",
    "InterruptTokenError",
    "InterruptTokenResult",
    "MAX_COMMENT_CHARS_HARD",
    "MAX_REQUEST_JSON_BYTES",
    "MAX_SCHEMA_FIELD_COUNT",
    "MAX_SCHEMA_NESTING_DEPTH",
    "MAX_SUBMITTED_JSON_BYTES",
    "RENDER_FIELD_TYPES",
    "assert_no_sensitive_token_leak",
    "allowed_outcomes_for_kind",
    "build_budget_suspension_state",
    "compute_field_schema_digest",
    "compute_remaining_active_ms",
    "compute_request_digest",
    "compute_resolution_digest",
    "derive_interrupt_key",
    "derive_resume_budget_ledger",
    "digest_resume_token",
    "generate_resume_token",
    "map_outcome_to_status",
    "non_time_budget_snapshot",
    "normalize_interrupt_field_schema",
    "render_interrupt_fields",
    "require_interrupt_token_pepper",
    "validate_comment",
    "validate_submitted_values",
    "verify_resume_token",
]
