"""Plan 05 revisioned Obligation Ledger.

Process-local immutable-by-revision state under a lock/CAS facade. Plan 05 keeps
state in memory; Plan 06 maps the same transition contract to database CAS.

Hard rules (Plan 05 §8):
- Transitions are append/update-by-revision under the Run-state lock.
- ``pending -> satisfied|waived|failed`` only; terminal states never reopen.
- Every resolution has at least one exact evidence edge or a safe waiver reason.
- Text/result content is represented only by digests/refs — never raw content.
- IDs are deterministic from Run ID, owner, obligation type, source call, ordinal.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import JsonValue, sha256_bytes, sha256_canonical_json

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ObligationOwnerKind = Literal["main_agent", "skill_version", "capability_call"]
ObligationType = Literal[
    "terminal_output",
    "required_followup",
    "required_artifact",
    "approval",
    "user_input",
    "reconciliation",
]
ObligationStatus = Literal["pending", "satisfied", "waived", "failed"]
EvidenceKind = Literal[
    "provider_text",
    "capability_result",
    "artifact",
    "compatible_consumer",
]

OBLIGATION_EVENT_CREATED = "obligation_created"
OBLIGATION_EVENT_RESOLVED = "obligation_resolved"
OBLIGATION_EVENT_FOLLOWUP = "obligation_followup_started"
OBLIGATION_EVENT_DENIED = "obligation_denied"

REASON_ALLOWED = "allowed"
REASON_DUPLICATE_OBLIGATION = "duplicate_obligation_id"
REASON_OBLIGATION_NOT_FOUND = "obligation_not_found"
REASON_OBLIGATION_NOT_PENDING = "obligation_not_pending"
REASON_EVIDENCE_INVALID = "completion_evidence_invalid"
REASON_OWNER_MISMATCH = "obligation_owner_mismatch"
REASON_PROTOCOL_ERROR = "obligation_state_protocol_error"
REASON_UNSATISFIABLE = "skill_completion_unsatisfiable"
REASON_FOLLOWUP_LIMIT = "completion_followup_limit"

# Stable completion reason codes (Plan 05 §8.6) — also used by completion.py.
REASON_ALL_SATISFIED = "all_obligations_satisfied"
REASON_TERMINAL_TEXT_MISSING = "terminal_text_missing"
REASON_SKILL_TERMINAL_PENDING = "skill_terminal_output_pending"
REASON_CAPABILITY_FOLLOWUP_PENDING = "capability_followup_pending"
REASON_ARTIFACT_PENDING = "artifact_pending"
REASON_APPROVAL_PENDING = "approval_pending"
REASON_USER_INPUT_PENDING = "user_input_pending"
REASON_RECONCILIATION_PENDING = "reconciliation_pending"
REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS = "budget_exhausted_with_obligations"
REASON_OBLIGATIONS_PENDING_AT_FINALIZATION = "obligations_pending_at_finalization"
REASON_WAITING_WITHOUT_OBLIGATION = "waiting_without_obligation"
REASON_COMPLETION_EVIDENCE_INVALID = "completion_evidence_invalid"
REASON_COMPLETION_FOLLOWUP_LIMIT = "completion_followup_limit"

_DIGEST_RE_LEN = 64
_TERMINAL_STATUSES = frozenset({"satisfied", "waived", "failed"})
_TEXT_SATISFIABLE_TYPES = frozenset({"terminal_output", "required_followup"})
_WAIT_TYPES = frozenset({"approval", "user_input"})

EventSink = Callable[[Mapping[str, JsonValue]], None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != _DIGEST_RE_LEN:
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest"
        )
    if any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest"
        )
    return value


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


class CompletionObligation(FrozenContract):
    """One structured completion obligation owned by Main Agent / Skill / call."""

    obligation_id: str
    owner_kind: ObligationOwnerKind
    owner_id: str
    owner_version_id: UUID | None
    source_call_id: str | None
    obligation_type: ObligationType
    blocking: bool
    requirement_digest: str
    status: ObligationStatus
    evidence_refs: tuple[str, ...]
    created_revision: int
    resolved_revision: int | None

    @field_validator("obligation_id", "owner_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("requirement_digest")
    @classmethod
    def _req(cls, value: str) -> str:
        return _require_digest(value, field_name="requirement_digest")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("evidence_refs must be a sequence")
        out: list[str] = []
        for item in value:
            out.append(_require_digest(item, field_name="evidence_refs[]"))
        return tuple(out)

    @field_validator("created_revision")
    @classmethod
    def _created(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="created_revision")

    @field_validator("resolved_revision")
    @classmethod
    def _resolved(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _require_non_negative_int(value, field_name="resolved_revision")

    @model_validator(mode="after")
    def _status_rules(self) -> CompletionObligation:
        if self.status == "pending":
            if self.resolved_revision is not None:
                raise ValueError("pending obligation cannot have resolved_revision")
        else:
            if self.resolved_revision is None:
                raise ValueError(
                    f"{self.status} obligation requires resolved_revision"
                )
        if self.obligation_type == "required_followup" and not self.source_call_id:
            raise ValueError("required_followup requires source_call_id")
        return self


class ObligationEvidenceEdge(FrozenContract):
    """Exact evidence edge linking a digestable source to an obligation."""

    obligation_id: str
    evidence_kind: EvidenceKind
    source_owner_version_id: UUID | None
    source_call_id: str | None
    evidence_digest: str
    predicate_digest: str

    @field_validator("obligation_id")
    @classmethod
    def _oid(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="obligation_id")

    @field_validator("evidence_digest", "predicate_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)


class ObligationLedgerState(FrozenContract):
    """Immutable-by-revision Obligation Ledger snapshot."""

    revision: int
    obligations: tuple[CompletionObligation, ...]
    evidence_edges: tuple[ObligationEvidenceEdge, ...]
    followup_rounds_started: int
    ledger_digest: str

    @field_validator("revision", "followup_rounds_started")
    @classmethod
    def _non_neg(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("ledger_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="ledger_digest")


class ObligationDecision(FrozenContract):
    """Result of a ledger transition attempt."""

    allowed: bool
    reason_code: str
    obligation: CompletionObligation | None = None
    obligations: tuple[CompletionObligation, ...] = ()
    evidence_edge: ObligationEvidenceEdge | None = None
    ledger_revision: int
    ledger_digest: str
    event: dict[str, Any] | None = None

    @field_validator("reason_code")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="reason_code")

    @field_validator("ledger_revision")
    @classmethod
    def _rev(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="ledger_revision")

    @field_validator("ledger_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="ledger_digest")


# ---------------------------------------------------------------------------
# Digest / ID builders
# ---------------------------------------------------------------------------


def compute_obligation_id(
    *,
    run_id: UUID,
    owner_kind: ObligationOwnerKind,
    owner_id: str,
    obligation_type: ObligationType,
    source_call_id: str | None = None,
    ordinal: int = 0,
) -> str:
    """Deterministic obligation ID from Run/owner/type/source/ordinal."""
    payload: dict[str, JsonValue] = {
        "runId": str(run_id),
        "ownerKind": owner_kind,
        "ownerId": owner_id,
        "obligationType": obligation_type,
        "sourceCallId": source_call_id,
        "ordinal": int(ordinal),
    }
    return sha256_canonical_json(payload)


def compute_requirement_digest(
    *,
    obligation_type: ObligationType,
    owner_kind: ObligationOwnerKind,
    owner_id: str,
    owner_version_id: UUID | None = None,
    source_call_id: str | None = None,
    extra: Mapping[str, JsonValue] | None = None,
) -> str:
    """Digest of the structural requirement (no prose/content)."""
    payload: dict[str, JsonValue] = {
        "obligationType": obligation_type,
        "ownerKind": owner_kind,
        "ownerId": owner_id,
        "ownerVersionId": str(owner_version_id) if owner_version_id is not None else None,
        "sourceCallId": source_call_id,
    }
    if extra:
        payload["extra"] = dict(extra)
    return sha256_canonical_json(payload)


def compute_text_evidence_digest(*, text: str) -> str:
    """Digest of natural Provider final text (content never stored in ledger)."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return sha256_bytes(text.encode("utf-8"))


def compute_result_evidence_digest(
    *,
    call_id: str,
    result_status: str,
    terminal_output: bool,
    needs_followup: bool,
    output_digest: str,
) -> str:
    """Digest of a Capability Result used as evidence."""
    return sha256_canonical_json(
        {
            "callId": call_id,
            "resultStatus": result_status,
            "terminalOutput": bool(terminal_output),
            "needsFollowup": bool(needs_followup),
            "outputDigest": _require_digest(output_digest, field_name="output_digest"),
        }
    )


def compute_predicate_digest(
    *,
    evidence_kind: EvidenceKind,
    obligation_type: ObligationType,
    owner_kind: ObligationOwnerKind | None = None,
    terminal_text_allowed: bool | None = None,
    binding_contract_digest: str | None = None,
    completion_contract_digest: str | None = None,
) -> str:
    """Predicate digest describing *why* evidence satisfies an obligation."""
    payload: dict[str, JsonValue] = {
        "evidenceKind": evidence_kind,
        "obligationType": obligation_type,
        "ownerKind": owner_kind,
        "terminalTextAllowed": terminal_text_allowed,
        "bindingContractDigest": binding_contract_digest,
        "completionContractDigest": completion_contract_digest,
    }
    return sha256_canonical_json(payload)


def _obligation_payload(item: CompletionObligation) -> dict[str, JsonValue]:
    return {
        "obligationId": item.obligation_id,
        "ownerKind": item.owner_kind,
        "ownerId": item.owner_id,
        "ownerVersionId": (
            str(item.owner_version_id) if item.owner_version_id is not None else None
        ),
        "sourceCallId": item.source_call_id,
        "obligationType": item.obligation_type,
        "blocking": item.blocking,
        "requirementDigest": item.requirement_digest,
        "status": item.status,
        "evidenceRefs": list(item.evidence_refs),
        "createdRevision": item.created_revision,
        "resolvedRevision": item.resolved_revision,
    }


def _edge_payload(item: ObligationEvidenceEdge) -> dict[str, JsonValue]:
    return {
        "obligationId": item.obligation_id,
        "evidenceKind": item.evidence_kind,
        "sourceOwnerVersionId": (
            str(item.source_owner_version_id)
            if item.source_owner_version_id is not None
            else None
        ),
        "sourceCallId": item.source_call_id,
        "evidenceDigest": item.evidence_digest,
        "predicateDigest": item.predicate_digest,
    }


def build_obligation_ledger_digest_payload(
    *,
    revision: int,
    obligations: Sequence[CompletionObligation],
    evidence_edges: Sequence[ObligationEvidenceEdge],
    followup_rounds_started: int,
) -> dict[str, JsonValue]:
    ordered_obs = tuple(sorted(obligations, key=lambda o: o.obligation_id))
    ordered_edges = tuple(
        sorted(
            evidence_edges,
            key=lambda e: (
                e.obligation_id,
                e.evidence_kind,
                e.evidence_digest,
                e.predicate_digest,
            ),
        )
    )
    return {
        "revision": revision,
        "obligations": [_obligation_payload(o) for o in ordered_obs],
        "evidenceEdges": [_edge_payload(e) for e in ordered_edges],
        "followupRoundsStarted": followup_rounds_started,
    }


def compute_obligation_ledger_digest(
    *,
    revision: int,
    obligations: Sequence[CompletionObligation],
    evidence_edges: Sequence[ObligationEvidenceEdge],
    followup_rounds_started: int,
) -> str:
    return sha256_canonical_json(
        build_obligation_ledger_digest_payload(
            revision=revision,
            obligations=obligations,
            evidence_edges=evidence_edges,
            followup_rounds_started=followup_rounds_started,
        )
    )


def _rebuild_state(
    *,
    revision: int,
    obligations: Sequence[CompletionObligation],
    evidence_edges: Sequence[ObligationEvidenceEdge],
    followup_rounds_started: int,
) -> ObligationLedgerState:
    ordered_obs = tuple(sorted(obligations, key=lambda o: o.obligation_id))
    ordered_edges = tuple(
        sorted(
            evidence_edges,
            key=lambda e: (
                e.obligation_id,
                e.evidence_kind,
                e.evidence_digest,
                e.predicate_digest,
            ),
        )
    )
    digest = compute_obligation_ledger_digest(
        revision=revision,
        obligations=ordered_obs,
        evidence_edges=ordered_edges,
        followup_rounds_started=followup_rounds_started,
    )
    return ObligationLedgerState(
        revision=revision,
        obligations=ordered_obs,
        evidence_edges=ordered_edges,
        followup_rounds_started=followup_rounds_started,
        ledger_digest=digest,
    )


def create_initial_obligation_ledger_state() -> ObligationLedgerState:
    """Empty revision-0 ledger (Main Agent obligation is added at Run start)."""
    return _rebuild_state(
        revision=0,
        obligations=(),
        evidence_edges=(),
        followup_rounds_started=0,
    )


def serialize_obligation_ledger_state(state: ObligationLedgerState) -> dict[str, JsonValue]:
    """Portable JSON object for Plan 06 persistence (digests/ids only)."""
    return {
        "revision": state.revision,
        "obligations": [_obligation_payload(o) for o in state.obligations],
        "evidenceEdges": [_edge_payload(e) for e in state.evidence_edges],
        "followupRoundsStarted": state.followup_rounds_started,
        "ledgerDigest": state.ledger_digest,
    }


def deserialize_obligation_ledger_state(
    payload: Mapping[str, Any],
) -> ObligationLedgerState:
    """Rehydrate ledger state and re-verify digest coherence."""
    obligations = tuple(
        CompletionObligation(
            obligation_id=item["obligationId"],
            owner_kind=item["ownerKind"],
            owner_id=item["ownerId"],
            owner_version_id=_optional_uuid(item.get("ownerVersionId")),
            source_call_id=item.get("sourceCallId"),
            obligation_type=item["obligationType"],
            blocking=bool(item["blocking"]),
            requirement_digest=item["requirementDigest"],
            status=item["status"],
            evidence_refs=tuple(item.get("evidenceRefs") or ()),
            created_revision=int(item["createdRevision"]),
            resolved_revision=(
                None
                if item.get("resolvedRevision") is None
                else int(item["resolvedRevision"])
            ),
        )
        for item in payload.get("obligations") or ()
    )
    edges = tuple(
        ObligationEvidenceEdge(
            obligation_id=item["obligationId"],
            evidence_kind=item["evidenceKind"],
            source_owner_version_id=_optional_uuid(item.get("sourceOwnerVersionId")),
            source_call_id=item.get("sourceCallId"),
            evidence_digest=item["evidenceDigest"],
            predicate_digest=item["predicateDigest"],
        )
        for item in payload.get("evidenceEdges") or ()
    )
    state = _rebuild_state(
        revision=int(payload["revision"]),
        obligations=obligations,
        evidence_edges=edges,
        followup_rounds_started=int(payload.get("followupRoundsStarted") or 0),
    )
    expected = payload.get("ledgerDigest")
    if expected is not None and expected != state.ledger_digest:
        raise ValueError("ledger_digest does not match deserialized fields")
    return state


# ---------------------------------------------------------------------------
# Creation helpers
# ---------------------------------------------------------------------------


def build_main_agent_terminal_obligation(
    *,
    run_id: UUID,
    revision: int,
    owner_id: str = "main_agent",
    ordinal: int = 0,
) -> CompletionObligation:
    """Base Main Agent terminal_output obligation created at Run start."""
    oid = compute_obligation_id(
        run_id=run_id,
        owner_kind="main_agent",
        owner_id=owner_id,
        obligation_type="terminal_output",
        source_call_id=None,
        ordinal=ordinal,
    )
    req = compute_requirement_digest(
        obligation_type="terminal_output",
        owner_kind="main_agent",
        owner_id=owner_id,
    )
    return CompletionObligation(
        obligation_id=oid,
        owner_kind="main_agent",
        owner_id=owner_id,
        owner_version_id=None,
        source_call_id=None,
        obligation_type="terminal_output",
        blocking=True,
        requirement_digest=req,
        status="pending",
        evidence_refs=(),
        created_revision=revision,
        resolved_revision=None,
    )


def build_skill_terminal_obligation(
    *,
    run_id: UUID,
    skill_version_id: UUID,
    revision: int,
    ordinal: int = 0,
) -> CompletionObligation:
    """Skill terminal_output obligation when requires_terminal_output=true."""
    owner_id = str(skill_version_id)
    oid = compute_obligation_id(
        run_id=run_id,
        owner_kind="skill_version",
        owner_id=owner_id,
        obligation_type="terminal_output",
        source_call_id=None,
        ordinal=ordinal,
    )
    req = compute_requirement_digest(
        obligation_type="terminal_output",
        owner_kind="skill_version",
        owner_id=owner_id,
        owner_version_id=skill_version_id,
    )
    return CompletionObligation(
        obligation_id=oid,
        owner_kind="skill_version",
        owner_id=owner_id,
        owner_version_id=skill_version_id,
        source_call_id=None,
        obligation_type="terminal_output",
        blocking=True,
        requirement_digest=req,
        status="pending",
        evidence_refs=(),
        created_revision=revision,
        resolved_revision=None,
    )


def build_required_followup_obligation(
    *,
    run_id: UUID,
    source_call_id: str,
    owner_kind: ObligationOwnerKind,
    owner_id: str,
    owner_version_id: UUID | None,
    revision: int,
    ordinal: int = 0,
) -> CompletionObligation:
    """required_followup after a completed Result with needs_followup=true."""
    oid = compute_obligation_id(
        run_id=run_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        obligation_type="required_followup",
        source_call_id=source_call_id,
        ordinal=ordinal,
    )
    req = compute_requirement_digest(
        obligation_type="required_followup",
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        source_call_id=source_call_id,
    )
    return CompletionObligation(
        obligation_id=oid,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        source_call_id=source_call_id,
        obligation_type="required_followup",
        blocking=True,
        requirement_digest=req,
        status="pending",
        evidence_refs=(),
        created_revision=revision,
        resolved_revision=None,
    )


def build_reserved_obligation(
    *,
    run_id: UUID,
    obligation_type: ObligationType,
    owner_kind: ObligationOwnerKind,
    owner_id: str,
    owner_version_id: UUID | None = None,
    source_call_id: str | None = None,
    revision: int = 0,
    ordinal: int = 0,
    blocking: bool = True,
) -> CompletionObligation:
    """Build reserved types (artifact/approval/input/reconciliation) for fixtures."""
    if obligation_type not in {
        "required_artifact",
        "approval",
        "user_input",
        "reconciliation",
        "terminal_output",
        "required_followup",
    }:
        raise ValueError(f"unsupported obligation_type {obligation_type!r}")
    oid = compute_obligation_id(
        run_id=run_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        obligation_type=obligation_type,
        source_call_id=source_call_id,
        ordinal=ordinal,
    )
    req = compute_requirement_digest(
        obligation_type=obligation_type,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        source_call_id=source_call_id,
    )
    return CompletionObligation(
        obligation_id=oid,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        source_call_id=source_call_id,
        obligation_type=obligation_type,
        blocking=blocking,
        requirement_digest=req,
        status="pending",
        evidence_refs=(),
        created_revision=revision,
        resolved_revision=None,
    )


# ---------------------------------------------------------------------------
# Satisfiability (Skill terminal activation)
# ---------------------------------------------------------------------------


class SkillTerminalSatisfiabilityView(FrozenContract):
    """Inputs for the Plan 05 runtime Skill terminal satisfiability check."""

    skill_version_id: UUID
    requires_terminal_output: bool
    terminal_text_allowed: bool
    # Remaining Provider/finalization slots after skill.inject (text path).
    remaining_provider_slots: int
    # Capability path: at least one owned/compatible terminal-output exposure.
    has_terminal_capability_exposure: bool
    # Exposure available + grant-admitted + remaining Run/owner call allowance.
    terminal_capability_path_available: bool
    max_skill_calls: int


def evaluate_skill_terminal_satisfiability(
    view: SkillTerminalSatisfiabilityView,
) -> tuple[bool, str]:
    """Return (ok, reason). Fail activation with skill_completion_unsatisfiable."""
    if not view.requires_terminal_output:
        return True, REASON_ALLOWED

    text_path = (
        view.terminal_text_allowed and view.remaining_provider_slots >= 1
    )
    capability_path = (
        view.has_terminal_capability_exposure
        and view.terminal_capability_path_available
        and view.max_skill_calls >= 1
    )

    # Structural: instruction-only + text forbidden is unsatisfiable.
    if not view.terminal_text_allowed and not view.has_terminal_capability_exposure:
        return False, REASON_UNSATISFIABLE

    # Capability-only path requires positive max_skill_calls.
    if (
        not view.terminal_text_allowed
        and view.has_terminal_capability_exposure
        and view.max_skill_calls < 1
    ):
        return False, REASON_UNSATISFIABLE

    # Text path with no remaining finalization/provider route.
    if view.terminal_text_allowed and not text_path and not capability_path:
        return False, REASON_UNSATISFIABLE

    # Capability path present but currently unavailable/denied/no allowance.
    if not text_path and not capability_path:
        return False, REASON_UNSATISFIABLE

    return True, REASON_ALLOWED


# ---------------------------------------------------------------------------
# Pure transitions
# ---------------------------------------------------------------------------


def _find_obligation(
    state: ObligationLedgerState, obligation_id: str
) -> CompletionObligation | None:
    for item in state.obligations:
        if item.obligation_id == obligation_id:
            return item
    return None


def _safe_event(
    event_type: str,
    *,
    reason_code: str,
    state: ObligationLedgerState,
    obligation_id: str | None = None,
    extra: Mapping[str, JsonValue] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "eventType": event_type,
        "reasonCode": reason_code,
        "ledgerRevision": state.revision,
        "ledgerDigest": state.ledger_digest,
    }
    if obligation_id is not None:
        payload["obligationId"] = obligation_id
    if extra:
        payload.update(dict(extra))
    return payload


def _deny(
    state: ObligationLedgerState,
    *,
    reason_code: str,
    obligation_id: str | None = None,
) -> tuple[ObligationLedgerState, ObligationDecision]:
    event = _safe_event(
        OBLIGATION_EVENT_DENIED,
        reason_code=reason_code,
        state=state,
        obligation_id=obligation_id,
    )
    return state, ObligationDecision(
        allowed=False,
        reason_code=reason_code,
        obligation=None,
        obligations=(),
        evidence_edge=None,
        ledger_revision=state.revision,
        ledger_digest=state.ledger_digest,
        event=event,
    )


def pure_create_obligation(
    state: ObligationLedgerState,
    obligation: CompletionObligation,
) -> tuple[ObligationLedgerState, ObligationDecision]:
    """Append a new pending obligation. Rejects duplicate IDs and non-pending."""
    if obligation.status != "pending":
        return _deny(
            state,
            reason_code=REASON_PROTOCOL_ERROR,
            obligation_id=obligation.obligation_id,
        )
    if _find_obligation(state, obligation.obligation_id) is not None:
        return _deny(
            state,
            reason_code=REASON_DUPLICATE_OBLIGATION,
            obligation_id=obligation.obligation_id,
        )
    # Stamp created_revision to the new ledger revision.
    new_rev = state.revision + 1
    stamped = CompletionObligation(
        obligation_id=obligation.obligation_id,
        owner_kind=obligation.owner_kind,
        owner_id=obligation.owner_id,
        owner_version_id=obligation.owner_version_id,
        source_call_id=obligation.source_call_id,
        obligation_type=obligation.obligation_type,
        blocking=obligation.blocking,
        requirement_digest=obligation.requirement_digest,
        status="pending",
        evidence_refs=(),
        created_revision=new_rev,
        resolved_revision=None,
    )
    new_state = _rebuild_state(
        revision=new_rev,
        obligations=(*state.obligations, stamped),
        evidence_edges=state.evidence_edges,
        followup_rounds_started=state.followup_rounds_started,
    )
    event = _safe_event(
        OBLIGATION_EVENT_CREATED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        obligation_id=stamped.obligation_id,
        extra={
            "obligationType": stamped.obligation_type,
            "ownerKind": stamped.owner_kind,
            "ownerId": stamped.owner_id,
            "blocking": stamped.blocking,
        },
    )
    return new_state, ObligationDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        obligation=stamped,
        obligations=(stamped,),
        evidence_edge=None,
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_resolve_obligation(
    state: ObligationLedgerState,
    *,
    obligation_id: str,
    status: Literal["satisfied", "waived", "failed"],
    evidence_edge: ObligationEvidenceEdge | None = None,
    waiver_reason: str | None = None,
) -> tuple[ObligationLedgerState, ObligationDecision]:
    """pending -> satisfied|waived|failed. Terminal states never reopen."""
    if status not in _TERMINAL_STATUSES:
        return _deny(state, reason_code=REASON_PROTOCOL_ERROR, obligation_id=obligation_id)

    current = _find_obligation(state, obligation_id)
    if current is None:
        return _deny(
            state, reason_code=REASON_OBLIGATION_NOT_FOUND, obligation_id=obligation_id
        )
    if current.status != "pending":
        return _deny(
            state,
            reason_code=REASON_OBLIGATION_NOT_PENDING,
            obligation_id=obligation_id,
        )

    if status == "satisfied":
        if evidence_edge is None:
            return _deny(
                state,
                reason_code=REASON_EVIDENCE_INVALID,
                obligation_id=obligation_id,
            )
        if evidence_edge.obligation_id != obligation_id:
            return _deny(
                state,
                reason_code=REASON_EVIDENCE_INVALID,
                obligation_id=obligation_id,
            )
    elif status in {"waived", "failed"}:
        # Explicit waiver/failure reason required when no evidence edge.
        if evidence_edge is None and not (waiver_reason and waiver_reason.strip()):
            return _deny(
                state,
                reason_code=REASON_EVIDENCE_INVALID,
                obligation_id=obligation_id,
            )
        if evidence_edge is not None and evidence_edge.obligation_id != obligation_id:
            return _deny(
                state,
                reason_code=REASON_EVIDENCE_INVALID,
                obligation_id=obligation_id,
            )

    new_rev = state.revision + 1
    new_refs = current.evidence_refs
    new_edges = state.evidence_edges
    edge_out: ObligationEvidenceEdge | None = None
    if evidence_edge is not None:
        # Reject exact duplicate evidence edges.
        for existing in state.evidence_edges:
            if (
                existing.obligation_id == evidence_edge.obligation_id
                and existing.evidence_kind == evidence_edge.evidence_kind
                and existing.evidence_digest == evidence_edge.evidence_digest
                and existing.predicate_digest == evidence_edge.predicate_digest
            ):
                return _deny(
                    state,
                    reason_code=REASON_EVIDENCE_INVALID,
                    obligation_id=obligation_id,
                )
        edge_out = evidence_edge
        new_edges = (*state.evidence_edges, evidence_edge)
        new_refs = (*current.evidence_refs, evidence_edge.evidence_digest)

    updated = CompletionObligation(
        obligation_id=current.obligation_id,
        owner_kind=current.owner_kind,
        owner_id=current.owner_id,
        owner_version_id=current.owner_version_id,
        source_call_id=current.source_call_id,
        obligation_type=current.obligation_type,
        blocking=current.blocking,
        requirement_digest=current.requirement_digest,
        status=status,
        evidence_refs=new_refs,
        created_revision=current.created_revision,
        resolved_revision=new_rev,
    )
    new_obs = tuple(
        updated if o.obligation_id == obligation_id else o for o in state.obligations
    )
    new_state = _rebuild_state(
        revision=new_rev,
        obligations=new_obs,
        evidence_edges=new_edges,
        followup_rounds_started=state.followup_rounds_started,
    )
    event = _safe_event(
        OBLIGATION_EVENT_RESOLVED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        obligation_id=obligation_id,
        extra={
            "status": status,
            "obligationType": current.obligation_type,
            "waiverReason": waiver_reason,
        },
    )
    return new_state, ObligationDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        obligation=updated,
        obligations=(updated,),
        evidence_edge=edge_out,
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_start_completion_followup(
    state: ObligationLedgerState,
    *,
    max_completion_followup_rounds: int,
) -> tuple[ObligationLedgerState, ObligationDecision]:
    """Consume one completion-followup slot (ledger counter; budget is separate)."""
    if max_completion_followup_rounds < 0:
        raise ValueError("max_completion_followup_rounds must be >= 0")
    if state.followup_rounds_started >= max_completion_followup_rounds:
        return _deny(state, reason_code=REASON_FOLLOWUP_LIMIT)
    new_state = _rebuild_state(
        revision=state.revision + 1,
        obligations=state.obligations,
        evidence_edges=state.evidence_edges,
        followup_rounds_started=state.followup_rounds_started + 1,
    )
    event = _safe_event(
        OBLIGATION_EVENT_FOLLOWUP,
        reason_code=REASON_ALLOWED,
        state=new_state,
        extra={"followupRoundsStarted": new_state.followup_rounds_started},
    )
    return new_state, ObligationDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        obligation=None,
        obligations=(),
        evidence_edge=None,
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


# ---------------------------------------------------------------------------
# Satisfaction application (pure)
# ---------------------------------------------------------------------------


def pending_blocking(state: ObligationLedgerState) -> tuple[CompletionObligation, ...]:
    return tuple(
        o for o in state.obligations if o.status == "pending" and o.blocking
    )


def reason_code_for_pending(obligations: Sequence[CompletionObligation]) -> str:
    """Pick a stable primary reason code for remaining blocking obligations."""
    if not obligations:
        return REASON_ALL_SATISFIED
    # Priority order mirrors Plan 05 §8.6 usage in completion decisions.
    types = {o.obligation_type for o in obligations}
    if "approval" in types:
        return REASON_APPROVAL_PENDING
    if "user_input" in types:
        return REASON_USER_INPUT_PENDING
    if "reconciliation" in types:
        return REASON_RECONCILIATION_PENDING
    if "required_artifact" in types:
        return REASON_ARTIFACT_PENDING
    if "required_followup" in types:
        return REASON_CAPABILITY_FOLLOWUP_PENDING
    if "terminal_output" in types:
        # Prefer skill-specific code when any skill terminal remains.
        if any(o.owner_kind == "skill_version" for o in obligations):
            return REASON_SKILL_TERMINAL_PENDING
        return REASON_TERMINAL_TEXT_MISSING
    return REASON_TERMINAL_TEXT_MISSING


def is_text_satisfiable(obligation: CompletionObligation, *, terminal_text_allowed_for_skill: bool) -> bool:
    """Whether nonempty natural Provider text can satisfy this obligation."""
    if obligation.obligation_type == "required_followup":
        return True
    if obligation.obligation_type != "terminal_output":
        return False
    if obligation.owner_kind == "main_agent":
        return True
    if obligation.owner_kind == "skill_version":
        return terminal_text_allowed_for_skill
    return False


def pure_apply_provider_text_evidence(
    state: ObligationLedgerState,
    *,
    text: str,
    skill_terminal_text_allowed: Mapping[str, bool] | None = None,
) -> tuple[ObligationLedgerState, ObligationDecision]:
    """Apply nonempty natural Provider final text to satisfiable obligations.

    Satisfies:
    - Main Agent terminal_output
    - Skill terminal_output when that Skill has terminal_text_allowed=true
    - Any pending required_followup

    Does not satisfy: reserved types, skill terminals with text forbidden.
    Empty/whitespace text is a no-op denial (caller handles empty separately).
    """
    if not isinstance(text, str) or not text.strip():
        return _deny(state, reason_code=REASON_TERMINAL_TEXT_MISSING)

    allowed_map = dict(skill_terminal_text_allowed or {})
    evidence_digest = compute_text_evidence_digest(text=text)
    working = state
    last_decision: ObligationDecision | None = None
    satisfied_any = False

    for obligation in list(working.obligations):
        if obligation.status != "pending":
            continue
        if obligation.obligation_type == "required_followup":
            can = True
            predicate = compute_predicate_digest(
                evidence_kind="provider_text",
                obligation_type="required_followup",
                owner_kind=obligation.owner_kind,
            )
        elif obligation.obligation_type == "terminal_output":
            if obligation.owner_kind == "main_agent":
                can = True
                predicate = compute_predicate_digest(
                    evidence_kind="provider_text",
                    obligation_type="terminal_output",
                    owner_kind="main_agent",
                    terminal_text_allowed=True,
                )
            elif obligation.owner_kind == "skill_version":
                can = bool(allowed_map.get(obligation.owner_id, False))
                predicate = compute_predicate_digest(
                    evidence_kind="provider_text",
                    obligation_type="terminal_output",
                    owner_kind="skill_version",
                    terminal_text_allowed=can,
                )
            else:
                can = False
                predicate = compute_predicate_digest(
                    evidence_kind="provider_text",
                    obligation_type="terminal_output",
                    owner_kind=obligation.owner_kind,
                )
        else:
            can = False
            predicate = ""

        if not can:
            continue

        edge = ObligationEvidenceEdge(
            obligation_id=obligation.obligation_id,
            evidence_kind="provider_text",
            source_owner_version_id=None,
            source_call_id=None,
            evidence_digest=evidence_digest,
            predicate_digest=predicate,
        )
        working, decision = pure_resolve_obligation(
            working,
            obligation_id=obligation.obligation_id,
            status="satisfied",
            evidence_edge=edge,
        )
        last_decision = decision
        if decision.allowed:
            satisfied_any = True
        else:
            # Evidence rejected (e.g. duplicate) — stop.
            return working, decision

    if not satisfied_any:
        # Text present but no obligation accepted it (all already satisfied or non-text).
        return working, ObligationDecision(
            allowed=True,
            reason_code=REASON_ALLOWED,
            obligation=None,
            obligations=(),
            evidence_edge=None,
            ledger_revision=working.revision,
            ledger_digest=working.ledger_digest,
            event=None,
        )
    assert last_decision is not None
    return working, last_decision


def pure_apply_capability_result_evidence(
    state: ObligationLedgerState,
    *,
    call_id: str,
    result_status: str,
    terminal_output: bool,
    needs_followup: bool,
    output_digest: str,
    owner_kind: ObligationOwnerKind,
    owner_id: str,
    owner_version_id: UUID | None,
    run_id: UUID,
    # Compatible consumer satisfaction (optional).
    compatible_consumer_version_ids: Sequence[UUID] = (),
    binding_contract_digest: str | None = None,
    completion_contract_digest: str | None = None,
    target_consumer_obligation_ids: Sequence[str] = (),
) -> tuple[ObligationLedgerState, ObligationDecision]:
    """Apply a completed Capability Result for terminal_output / required_followup creation.

    Rules:
    - Intermediate / failed / cancelled / empty results satisfy nothing.
    - terminal_output=true + completed + nonempty output satisfies the execution
      owner's matching terminal_output obligation (and optional compatible consumers).
    - needs_followup=true on a completed result creates a required_followup obligation.
    """
    if result_status != "completed":
        return state, ObligationDecision(
            allowed=True,
            reason_code=REASON_ALLOWED,
            obligation=None,
            obligations=(),
            evidence_edge=None,
            ledger_revision=state.revision,
            ledger_digest=state.ledger_digest,
            event=None,
        )

    working = state
    last_decision = ObligationDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        obligation=None,
        obligations=(),
        evidence_edge=None,
        ledger_revision=working.revision,
        ledger_digest=working.ledger_digest,
        event=None,
    )

    has_output = bool(output_digest) and len(output_digest) == _DIGEST_RE_LEN
    if terminal_output and has_output:
        evidence_digest = compute_result_evidence_digest(
            call_id=call_id,
            result_status=result_status,
            terminal_output=True,
            needs_followup=needs_followup,
            output_digest=output_digest,
        )
        # Satisfy owner's matching terminal_output.
        for obligation in list(working.obligations):
            if obligation.status != "pending":
                continue
            if obligation.obligation_type != "terminal_output":
                continue
            if obligation.owner_id != owner_id or obligation.owner_kind != owner_kind:
                continue
            predicate = compute_predicate_digest(
                evidence_kind="capability_result",
                obligation_type="terminal_output",
                owner_kind=owner_kind,
                binding_contract_digest=binding_contract_digest,
                completion_contract_digest=completion_contract_digest,
            )
            edge = ObligationEvidenceEdge(
                obligation_id=obligation.obligation_id,
                evidence_kind="capability_result",
                source_owner_version_id=owner_version_id,
                source_call_id=call_id,
                evidence_digest=evidence_digest,
                predicate_digest=predicate,
            )
            working, last_decision = pure_resolve_obligation(
                working,
                obligation_id=obligation.obligation_id,
                status="satisfied",
                evidence_edge=edge,
            )
            if not last_decision.allowed:
                return working, last_decision

        # Compatible consumer edges (strict). Non-empty targets always validate.
        if target_consumer_obligation_ids:
            if (
                binding_contract_digest is None
                or completion_contract_digest is None
            ):
                return _deny(
                    working,
                    reason_code=REASON_EVIDENCE_INVALID,
                    obligation_id=None,
                )
            compat_set = {str(v) for v in compatible_consumer_version_ids}
            for target_id in target_consumer_obligation_ids:
                target = _find_obligation(working, target_id)
                if target is None or target.status != "pending":
                    return _deny(
                        working,
                        reason_code=REASON_EVIDENCE_INVALID,
                        obligation_id=target_id,
                    )
                if target.obligation_type != "terminal_output":
                    return _deny(
                        working,
                        reason_code=REASON_EVIDENCE_INVALID,
                        obligation_id=target_id,
                    )
                # Consumer must appear in exposure's compatible_consumer_version_ids.
                if (
                    target.owner_version_id is None
                    or str(target.owner_version_id) not in compat_set
                ):
                    return _deny(
                        working,
                        reason_code=REASON_OWNER_MISMATCH,
                        obligation_id=target_id,
                    )
                predicate = compute_predicate_digest(
                    evidence_kind="compatible_consumer",
                    obligation_type="terminal_output",
                    owner_kind=target.owner_kind,
                    binding_contract_digest=binding_contract_digest,
                    completion_contract_digest=completion_contract_digest,
                )
                edge = ObligationEvidenceEdge(
                    obligation_id=target.obligation_id,
                    evidence_kind="compatible_consumer",
                    source_owner_version_id=owner_version_id,
                    source_call_id=call_id,
                    evidence_digest=evidence_digest,
                    predicate_digest=predicate,
                )
                working, last_decision = pure_resolve_obligation(
                    working,
                    obligation_id=target.obligation_id,
                    status="satisfied",
                    evidence_edge=edge,
                )
                if not last_decision.allowed:
                    return working, last_decision

    if needs_followup:
        followup = build_required_followup_obligation(
            run_id=run_id,
            source_call_id=call_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            owner_version_id=owner_version_id,
            revision=working.revision + 1,
        )
        working, last_decision = pure_create_obligation(working, followup)
        if not last_decision.allowed:
            return working, last_decision

    return working, last_decision


# ---------------------------------------------------------------------------
# Thread-safe facade
# ---------------------------------------------------------------------------


class ObligationLedger:
    """Process-local revisioned Obligation Ledger with lock/CAS interface."""

    def __init__(
        self,
        initial_state: ObligationLedgerState | None = None,
        *,
        run_id: UUID | None = None,
        event_sink: EventSink | None = None,
        create_main_agent_terminal: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        self._event_sink = event_sink
        self._run_id = run_id
        # skill_version_id(str) -> terminal_text_allowed
        self._skill_terminal_text_allowed: dict[str, bool] = {}
        if initial_state is None:
            initial_state = create_initial_obligation_ledger_state()
        self._state = initial_state
        if create_main_agent_terminal:
            if run_id is None:
                raise ValueError("run_id required to create Main Agent terminal obligation")
            obligation = build_main_agent_terminal_obligation(
                run_id=run_id,
                revision=self._state.revision + 1,
            )
            self._state, decision = pure_create_obligation(self._state, obligation)
            self._emit(decision)

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID,
        event_sink: EventSink | None = None,
        create_main_agent_terminal: bool = True,
    ) -> ObligationLedger:
        return cls(
            create_initial_obligation_ledger_state(),
            run_id=run_id,
            event_sink=event_sink,
            create_main_agent_terminal=create_main_agent_terminal,
        )

    def _emit(self, decision: ObligationDecision) -> None:
        if self._event_sink is not None and decision.event is not None:
            self._event_sink(decision.event)

    def _apply(
        self,
        transition: Callable[
            [ObligationLedgerState], tuple[ObligationLedgerState, ObligationDecision]
        ],
    ) -> ObligationDecision:
        with self._lock:
            new_state, decision = transition(self._state)
            if new_state is not self._state:
                if new_state.revision < self._state.revision:
                    raise RuntimeError(REASON_PROTOCOL_ERROR)
                if (
                    new_state.revision != self._state.revision
                    and new_state.revision != self._state.revision + 1
                ):
                    raise RuntimeError(REASON_PROTOCOL_ERROR)
                self._state = new_state
            self._emit(decision)
            return decision

    def snapshot(self) -> ObligationLedgerState:
        with self._lock:
            return self._state

    def run_id(self) -> UUID | None:
        with self._lock:
            return self._run_id

    def set_skill_terminal_text_allowed(
        self, skill_version_id: UUID | str, allowed: bool
    ) -> None:
        """Record whether a Skill policy permits terminal text satisfaction."""
        key = str(skill_version_id)
        with self._lock:
            self._skill_terminal_text_allowed[key] = bool(allowed)

    def skill_terminal_text_allowed_map(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._skill_terminal_text_allowed)

    def compare_and_swap(
        self,
        expected_revision: int,
        new_state: ObligationLedgerState,
    ) -> bool:
        with self._lock:
            if self._state.revision != expected_revision:
                return False
            if new_state.revision != expected_revision + 1:
                raise ValueError(
                    "new_state.revision must equal expected_revision + 1"
                )
            expected_digest = compute_obligation_ledger_digest(
                revision=new_state.revision,
                obligations=new_state.obligations,
                evidence_edges=new_state.evidence_edges,
                followup_rounds_started=new_state.followup_rounds_started,
            )
            if expected_digest != new_state.ledger_digest:
                raise ValueError("new_state.ledger_digest is inconsistent")
            self._state = new_state
            return True

    def create_obligation(self, obligation: CompletionObligation) -> ObligationDecision:
        return self._apply(lambda s: pure_create_obligation(s, obligation))

    def create_main_agent_terminal(self, *, run_id: UUID | None = None) -> ObligationDecision:
        rid = run_id or self._run_id
        if rid is None:
            raise ValueError("run_id required")
        with self._lock:
            self._run_id = rid
            obligation = build_main_agent_terminal_obligation(
                run_id=rid,
                revision=self._state.revision + 1,
            )
            new_state, decision = pure_create_obligation(self._state, obligation)
            if new_state is not self._state:
                self._state = new_state
            self._emit(decision)
            return decision

    def create_skill_terminal(
        self,
        *,
        skill_version_id: UUID,
        terminal_text_allowed: bool,
        run_id: UUID | None = None,
    ) -> ObligationDecision:
        rid = run_id or self._run_id
        if rid is None:
            raise ValueError("run_id required")
        with self._lock:
            self._skill_terminal_text_allowed[str(skill_version_id)] = bool(
                terminal_text_allowed
            )
            obligation = build_skill_terminal_obligation(
                run_id=rid,
                skill_version_id=skill_version_id,
                revision=self._state.revision + 1,
            )
            new_state, decision = pure_create_obligation(self._state, obligation)
            if new_state is not self._state:
                self._state = new_state
            self._emit(decision)
            return decision

    def resolve(
        self,
        *,
        obligation_id: str,
        status: Literal["satisfied", "waived", "failed"],
        evidence_edge: ObligationEvidenceEdge | None = None,
        waiver_reason: str | None = None,
    ) -> ObligationDecision:
        return self._apply(
            lambda s: pure_resolve_obligation(
                s,
                obligation_id=obligation_id,
                status=status,
                evidence_edge=evidence_edge,
                waiver_reason=waiver_reason,
            )
        )

    def apply_provider_text(self, text: str) -> ObligationDecision:
        with self._lock:
            allowed_map = dict(self._skill_terminal_text_allowed)
            new_state, decision = pure_apply_provider_text_evidence(
                self._state,
                text=text,
                skill_terminal_text_allowed=allowed_map,
            )
            if new_state is not self._state:
                if new_state.revision != self._state.revision + 1 and new_state.revision != self._state.revision:
                    # Multiple sequential resolves may bump >1; accept monotonic.
                    if new_state.revision < self._state.revision:
                        raise RuntimeError(REASON_PROTOCOL_ERROR)
                self._state = new_state
            self._emit(decision)
            return decision

    def apply_capability_result(
        self,
        *,
        call_id: str,
        result_status: str,
        terminal_output: bool,
        needs_followup: bool,
        output_digest: str,
        owner_kind: ObligationOwnerKind,
        owner_id: str,
        owner_version_id: UUID | None,
        run_id: UUID | None = None,
        compatible_consumer_version_ids: Sequence[UUID] = (),
        binding_contract_digest: str | None = None,
        completion_contract_digest: str | None = None,
        target_consumer_obligation_ids: Sequence[str] = (),
    ) -> ObligationDecision:
        rid = run_id or self._run_id
        if rid is None:
            raise ValueError("run_id required")
        with self._lock:
            new_state, decision = pure_apply_capability_result_evidence(
                self._state,
                call_id=call_id,
                result_status=result_status,
                terminal_output=terminal_output,
                needs_followup=needs_followup,
                output_digest=output_digest,
                owner_kind=owner_kind,
                owner_id=owner_id,
                owner_version_id=owner_version_id,
                run_id=rid,
                compatible_consumer_version_ids=compatible_consumer_version_ids,
                binding_contract_digest=binding_contract_digest,
                completion_contract_digest=completion_contract_digest,
                target_consumer_obligation_ids=target_consumer_obligation_ids,
            )
            if new_state is not self._state:
                if new_state.revision < self._state.revision:
                    raise RuntimeError(REASON_PROTOCOL_ERROR)
                self._state = new_state
            self._emit(decision)
            return decision

    def start_completion_followup(
        self, *, max_completion_followup_rounds: int
    ) -> ObligationDecision:
        return self._apply(
            lambda s: pure_start_completion_followup(
                s, max_completion_followup_rounds=max_completion_followup_rounds
            )
        )

    def pending_blocking(self) -> tuple[CompletionObligation, ...]:
        with self._lock:
            return pending_blocking(self._state)


__all__ = [
    "OBLIGATION_EVENT_CREATED",
    "OBLIGATION_EVENT_DENIED",
    "OBLIGATION_EVENT_FOLLOWUP",
    "OBLIGATION_EVENT_RESOLVED",
    "REASON_ALL_SATISFIED",
    "REASON_ALLOWED",
    "REASON_APPROVAL_PENDING",
    "REASON_ARTIFACT_PENDING",
    "REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS",
    "REASON_CAPABILITY_FOLLOWUP_PENDING",
    "REASON_COMPLETION_EVIDENCE_INVALID",
    "REASON_COMPLETION_FOLLOWUP_LIMIT",
    "REASON_DUPLICATE_OBLIGATION",
    "REASON_EVIDENCE_INVALID",
    "REASON_FOLLOWUP_LIMIT",
    "REASON_OBLIGATIONS_PENDING_AT_FINALIZATION",
    "REASON_OBLIGATION_NOT_FOUND",
    "REASON_OBLIGATION_NOT_PENDING",
    "REASON_OWNER_MISMATCH",
    "REASON_PROTOCOL_ERROR",
    "REASON_RECONCILIATION_PENDING",
    "REASON_SKILL_TERMINAL_PENDING",
    "REASON_TERMINAL_TEXT_MISSING",
    "REASON_UNSATISFIABLE",
    "REASON_USER_INPUT_PENDING",
    "REASON_WAITING_WITHOUT_OBLIGATION",
    "CompletionObligation",
    "EvidenceKind",
    "ObligationDecision",
    "ObligationEvidenceEdge",
    "ObligationLedger",
    "ObligationLedgerState",
    "ObligationOwnerKind",
    "ObligationStatus",
    "ObligationType",
    "SkillTerminalSatisfiabilityView",
    "build_main_agent_terminal_obligation",
    "build_required_followup_obligation",
    "build_reserved_obligation",
    "build_skill_terminal_obligation",
    "compute_obligation_id",
    "compute_obligation_ledger_digest",
    "compute_predicate_digest",
    "compute_requirement_digest",
    "compute_result_evidence_digest",
    "compute_text_evidence_digest",
    "create_initial_obligation_ledger_state",
    "deserialize_obligation_ledger_state",
    "evaluate_skill_terminal_satisfiability",
    "is_text_satisfiable",
    "pending_blocking",
    "pure_apply_capability_result_evidence",
    "pure_apply_provider_text_evidence",
    "pure_create_obligation",
    "pure_resolve_obligation",
    "pure_start_completion_followup",
    "reason_code_for_pending",
    "serialize_obligation_ledger_state",
]
