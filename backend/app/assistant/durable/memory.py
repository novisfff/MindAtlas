"""Ordered idempotent terminal memory finalizer (Plan 06 Task 8 / §11).

Contract highlights
-------------------
- L0 final assistant content is applied once by exact
  ``(run_id, assistant_message_id, final_content_digest)``. Same digest is a
  no-op; a different digest for the same Run/message is
  ``policy_state_protocol_error``. Recovery never inserts a second user-visible
  assistant Message.
- Final content + entry into internal ``ready_for_memory`` happen before any
  terminal status. Public status stays nonterminal ``running``; phase is never a
  public status value. The active-Run unique index continues to block a later
  conversation Run until memory is resolved.
- Prepared L1/L2 writes apply in one transaction with expected revisions /
  ``last_applied_run_id`` guards, then ``memory_commit_status=committed`` and
  ``completed``. On memory failure the user-visible L0 response is preserved and
  the Run still completes with ``memory_commit_status=failed``.
- Native L2 uses stable package ID + namespace (never Provider alias). Legacy
  name APIs on ``AssistantMemoryService`` remain unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.durable.repository import (
    CODE_PROTOCOL_ERROR,
    CODE_TERMINAL_IMMUTABLE,
    DurableChildBundle,
    DurableCommitResult,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
    STATUS_COMPLETED,
    STATUS_RUNNING,
)
from app.assistant.memory_service import AssistantMemoryService
from app.assistant.models import (
    AssistantChatRun,
    AssistantConversationL1Memory,
    AssistantConversationSkillL2Memory,
    Message,
)
from app.common.time import utcnow

logger = logging.getLogger(__name__)

CODE_POLICY_PROTOCOL = "policy_state_protocol_error"
CODE_INVALID_FINAL_CONTENT = "invalid_final_content"
CODE_MEMORY_REVISION_CONFLICT = "memory_revision_conflict"
CODE_DUPLICATE_NAMESPACE = "duplicate_memory_namespace"
CODE_INVALID_FACT = "invalid_fact_provenance"
CODE_INVALID_NAMESPACE = "invalid_memory_namespace"
CODE_MEMORY_PROVIDER_ERROR = "memory_provider_error"
CODE_NOT_READY = "not_ready_for_memory"

_BANNED_FINAL_MARKERS = frozenset(
    {
        "[provisional]",
        "[waiting]",
        "[cancelled]",
        "[failed]",
        "[fallback-discarded]",
    }
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class DurableMemoryError(Exception):
    """Stable memory finalizer failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class PreparedL1Update:
    summary_text: str
    expected_last_applied_run_id: UUID | None


@dataclass(frozen=True)
class PreparedL2Update:
    skill_package_id: UUID
    memory_namespace: str
    skill_name: str
    facts_v2: tuple[Mapping[str, Any], ...]
    expected_version: int | None
    """None means the native row must not exist yet; int is optimistic version."""


@dataclass(frozen=True)
class PreparedMemorySet:
    l1: PreparedL1Update | None = None
    l2: tuple[PreparedL2Update, ...] = ()


def digest_final_content(content: str) -> str:
    """SHA-256 hex digest of the exact final L0 UTF-8 content."""
    return hashlib.sha256(str(content).encode("utf-8")).hexdigest()


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    text = str(value or "").strip()
    if not text or not _UUID_RE.match(text):
        raise DurableMemoryError(CODE_INVALID_FACT, f"{field_name} must be a UUID")
    return UUID(text)


def normalize_facts_v2(
    facts: object,
    *,
    max_items: int = 10000,
    default_run_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Strictly validate facts_v2 provenance shape.

    Each fact requires:
    - text (nonempty string)
    - sourceSkillVersionId (UUID)
    - sourceRunId (UUID; may default when provided)
    - sourceCapabilityCallId (UUID | null; Plan 08 fills this)
    - observedAt (ISO-8601 string)
    """
    if facts is None:
        return []
    if not isinstance(facts, (list, tuple)):
        raise DurableMemoryError(CODE_INVALID_FACT, "facts_v2 must be a list")

    limit = max(1, int(max_items or 1))
    seen_text: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in facts:
        if not isinstance(raw, Mapping):
            raise DurableMemoryError(CODE_INVALID_FACT, "each fact must be an object")
        text = str(raw.get("text") or "").strip()
        if not text:
            raise DurableMemoryError(CODE_INVALID_FACT, "fact text must be nonempty")
        if text in seen_text:
            continue

        try:
            version_id = _parse_uuid(
                raw.get("sourceSkillVersionId") or raw.get("source_skill_version_id"),
                field_name="sourceSkillVersionId",
            )
        except DurableMemoryError:
            raise DurableMemoryError(
                CODE_INVALID_FACT, "sourceSkillVersionId is required"
            ) from None

        run_raw = raw.get("sourceRunId") or raw.get("source_run_id")
        if run_raw is None and default_run_id is not None:
            run_id = default_run_id
        else:
            try:
                run_id = _parse_uuid(run_raw, field_name="sourceRunId")
            except DurableMemoryError:
                raise DurableMemoryError(
                    CODE_INVALID_FACT, "sourceRunId is required"
                ) from None

        call_raw = raw.get("sourceCapabilityCallId", raw.get("source_capability_call_id"))
        call_id: str | None
        if call_raw is None or call_raw == "":
            call_id = None
        else:
            call_id = str(_parse_uuid(call_raw, field_name="sourceCapabilityCallId"))

        observed = raw.get("observedAt") or raw.get("observed_at")
        if isinstance(observed, datetime):
            observed_at = observed.isoformat()
        else:
            observed_at = str(observed or "").strip()
        if not observed_at:
            raise DurableMemoryError(CODE_INVALID_FACT, "observedAt is required")

        seen_text.add(text)
        out.append(
            {
                "text": text,
                "sourceSkillVersionId": str(version_id),
                "sourceRunId": str(run_id),
                "sourceCapabilityCallId": call_id,
                "observedAt": observed_at,
            }
        )
        if len(out) >= limit:
            break
    return out


def validate_prepared_memory_set(prepared: PreparedMemorySet) -> None:
    """Reject duplicate package/namespace entries and invalid namespaces."""
    seen: set[tuple[UUID, str]] = set()
    for item in prepared.l2 or ():
        ns = str(item.memory_namespace or "").strip()
        if not ns:
            raise DurableMemoryError(
                CODE_INVALID_NAMESPACE,
                "native memory_namespace must be nonempty",
            )
        key = (item.skill_package_id, ns)
        if key in seen:
            raise DurableMemoryError(
                CODE_DUPLICATE_NAMESPACE,
                f"duplicate package/namespace in prepared set: {item.skill_package_id}/{ns}",
            )
        seen.add(key)
        # Validate provenance early so apply never partially mutates.
        normalize_facts_v2(item.facts_v2)


def _assert_allowed_final_content(content: str) -> str:
    value = str(content)
    stripped = value.strip()
    if not stripped:
        raise DurableMemoryError(
            CODE_INVALID_FINAL_CONTENT,
            "final assistant content must be nonempty",
        )
    lowered = stripped.lower()
    if lowered in _BANNED_FINAL_MARKERS:
        raise DurableMemoryError(
            CODE_INVALID_FINAL_CONTENT,
            f"refusing to persist non-final marker content: {stripped}",
        )
    # Also reject exact marker tokens that may be padded.
    for marker in _BANNED_FINAL_MARKERS:
        if lowered == marker:
            raise DurableMemoryError(
                CODE_INVALID_FINAL_CONTENT,
                f"refusing to persist non-final marker content: {stripped}",
            )
    return value


class DurableMemoryFinalizer:
    """Apply L0/L1/L2 once and finalize Run memory outcome."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = DurableRunRepository(db)
        self._legacy = AssistantMemoryService(db)

    # ------------------------------------------------------------------
    # L0 final content
    # ------------------------------------------------------------------

    def apply_final_l0_content(
        self,
        *,
        run_id: UUID,
        assistant_message_id: UUID,
        content: str,
        content_digest: str,
        commit: bool = False,
    ) -> Message:
        """Persist accepted final L0 content with digest idempotency.

        Does not change Run status. Callers that also enter ``ready_for_memory``
        should use :meth:`enter_ready_for_memory_with_final_content` so both
        happen under one CAS transaction.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            raise DurableMemoryError(CODE_POLICY_PROTOCOL, f"run not found: {run_id}")
        if str(run.runtime_kind or "") != "main_agent":
            raise DurableMemoryError(CODE_POLICY_PROTOCOL, "not a main_agent run")
        if run.assistant_message_id is None:
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL, "run has no assistant_message_id"
            )
        if run.assistant_message_id != assistant_message_id:
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL,
                "assistant_message_id does not match run.assistant_message_id",
            )

        allowed = _assert_allowed_final_content(content)
        expected_digest = digest_final_content(allowed)
        provided = str(content_digest or "").strip().lower()
        if provided != expected_digest:
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL,
                "content_digest does not match content",
            )

        msg = self.db.get(Message, assistant_message_id)
        if msg is None:
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL,
                f"assistant message not found: {assistant_message_id}",
            )
        if msg.conversation_id != run.conversation_id:
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL,
                "assistant message conversation mismatch",
            )
        if str(msg.role or "") != "assistant":
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL,
                "target message is not an assistant message",
            )

        existing = str(msg.content or "")
        if existing.strip():
            existing_digest = digest_final_content(existing)
            if existing_digest == expected_digest and existing == allowed:
                if commit:
                    self.db.commit()
                return msg
            # Same digest but different exact bytes is still a conflict.
            if existing_digest == expected_digest:
                # Normalize to the exact accepted form once.
                msg.content = allowed
                self.db.add(msg)
                if commit:
                    self.db.commit()
                    self.db.refresh(msg)
                return msg
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL,
                "conflicting final content digest for run/message",
            )

        msg.content = allowed
        self.db.add(msg)
        if commit:
            self.db.commit()
            self.db.refresh(msg)
        else:
            self.db.flush()
        return msg

    def enter_ready_for_memory_with_final_content(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        final_content: str,
        content_digest: str,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """Persist accepted L0 content and enter internal ready_for_memory.

        Public status remains ``running``. This is the last cancellation fence.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            raise DurableMemoryError(CODE_POLICY_PROTOCOL, f"run not found: {run_id}")
        if run.assistant_message_id is None:
            raise DurableMemoryError(
                CODE_POLICY_PROTOCOL, "run has no assistant_message_id"
            )

        # Stage L0 mutation on the same Session; repo._commit will commit it
        # together with the CAS transition (or roll everything back on conflict).
        self.apply_final_l0_content(
            run_id=run_id,
            assistant_message_id=run.assistant_message_id,
            content=final_content,
            content_digest=content_digest,
            commit=False,
        )
        try:
            return self.repo.enter_ready_for_memory(
                run_id=run_id,
                expected_revision=expected_revision,
                lease=lease,
                events=events,
                children=children,
            )
        except DurableRunConflict:
            self.db.rollback()
            raise

    # ------------------------------------------------------------------
    # Native L2 package/namespace APIs (Legacy name APIs stay on memory_service)
    # ------------------------------------------------------------------

    def get_l2_facts_v2(
        self,
        conversation_id: UUID,
        skill_package_id: UUID,
        memory_namespace: str = "default",
    ) -> list[dict[str, Any]]:
        ns = str(memory_namespace or "").strip() or "default"
        row = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == conversation_id,
                AssistantConversationSkillL2Memory.skill_package_id == skill_package_id,
                AssistantConversationSkillL2Memory.memory_namespace == ns,
            )
            .first()
        )
        if row is None:
            return []
        if row.facts_v2 is None:
            # Compat: legacy facts without provenance cannot be reconstructed.
            return []
        try:
            return normalize_facts_v2(row.facts_v2)
        except DurableMemoryError:
            return []

    def upsert_l2_facts_v2(
        self,
        *,
        conversation_id: UUID,
        skill_package_id: UUID,
        memory_namespace: str,
        skill_name: str,
        facts_v2: Sequence[Mapping[str, Any]] | Sequence[Any],
        last_applied_run_id: UUID | None = None,
        expected_version: int | None = None,
        commit: bool = True,
    ) -> AssistantConversationSkillL2Memory:
        ns = str(memory_namespace or "").strip()
        if not ns:
            raise DurableMemoryError(
                CODE_INVALID_NAMESPACE,
                "native memory_namespace must be nonempty",
            )
        display_name = str(skill_name or "").strip() or "skill"
        normalized = normalize_facts_v2(facts_v2)
        legacy_facts = AssistantMemoryService.normalize_l2_facts(
            [item["text"] for item in normalized],
            max_items=10000,
        )

        row = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == conversation_id,
                AssistantConversationSkillL2Memory.skill_package_id == skill_package_id,
                AssistantConversationSkillL2Memory.memory_namespace == ns,
            )
            .first()
        )
        if row is None:
            if expected_version is not None:
                raise DurableMemoryError(
                    CODE_MEMORY_REVISION_CONFLICT,
                    "expected existing L2 row version but row is missing",
                )
            row = AssistantConversationSkillL2Memory(
                conversation_id=conversation_id,
                skill_name=display_name,
                facts=legacy_facts,
                version=1,
                skill_package_id=skill_package_id,
                memory_namespace=ns,
                facts_v2=normalized,
                last_applied_run_id=last_applied_run_id,
            )
            self.db.add(row)
        else:
            if expected_version is not None and int(row.version or 0) != int(
                expected_version
            ):
                raise DurableMemoryError(
                    CODE_MEMORY_REVISION_CONFLICT,
                    f"L2 version conflict: expected {expected_version}, got {row.version}",
                )
            changed = (
                list(row.facts or []) != legacy_facts
                or list(row.facts_v2 or []) != normalized
                or str(row.skill_name or "") != display_name
            )
            if changed:
                row.version = int(row.version or 1) + 1
            row.skill_name = display_name
            row.facts = legacy_facts
            row.facts_v2 = normalized
            if last_applied_run_id is not None:
                row.last_applied_run_id = last_applied_run_id
            self.db.add(row)

        if commit:
            self.db.commit()
            self.db.refresh(row)
        else:
            self.db.flush()
        return row

    # ------------------------------------------------------------------
    # L1 apply helpers
    # ------------------------------------------------------------------

    def apply_l1_if_needed(
        self,
        *,
        conversation_id: UUID,
        run_id: UUID,
        summary_text: str,
        expected_last_applied_run_id: UUID | None,
        commit: bool = True,
    ) -> bool:
        """Apply L1 summary when this run has not already been applied.

        Returns False when ``last_applied_run_id`` already equals ``run_id``
        (idempotent re-apply). Raises on expected-token mismatch.
        """
        summary = AssistantMemoryService.truncate_summary(
            str(summary_text or ""),
            max_chars=100_000,
        )
        row = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation_id)
            .first()
        )
        if row is not None and row.last_applied_run_id == run_id:
            return False

        if row is None:
            if expected_last_applied_run_id is not None:
                raise DurableMemoryError(
                    CODE_MEMORY_REVISION_CONFLICT,
                    "expected existing L1 last_applied_run_id but row is missing",
                )
            row = AssistantConversationL1Memory(
                conversation_id=conversation_id,
                summary_text=summary,
                last_applied_run_id=run_id,
            )
            self.db.add(row)
        else:
            current = row.last_applied_run_id
            if current != expected_last_applied_run_id:
                raise DurableMemoryError(
                    CODE_MEMORY_REVISION_CONFLICT,
                    "L1 last_applied_run_id mismatch",
                )
            row.summary_text = summary
            row.last_applied_run_id = run_id
            self.db.add(row)

        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return True

    def _apply_prepared_rows(
        self,
        *,
        run: AssistantChatRun,
        prepared: PreparedMemorySet,
    ) -> None:
        validate_prepared_memory_set(prepared)
        conversation_id = run.conversation_id
        run_id = run.id

        # If this run already applied memory rows, skip re-writes (crash after apply).
        if prepared.l1 is not None:
            self.apply_l1_if_needed(
                conversation_id=conversation_id,
                run_id=run_id,
                summary_text=prepared.l1.summary_text,
                expected_last_applied_run_id=prepared.l1.expected_last_applied_run_id,
                commit=False,
            )

        for item in prepared.l2 or ():
            ns = str(item.memory_namespace or "").strip()
            existing = (
                self.db.query(AssistantConversationSkillL2Memory)
                .filter(
                    AssistantConversationSkillL2Memory.conversation_id == conversation_id,
                    AssistantConversationSkillL2Memory.skill_package_id
                    == item.skill_package_id,
                    AssistantConversationSkillL2Memory.memory_namespace == ns,
                )
                .first()
            )
            if existing is not None and existing.last_applied_run_id == run_id:
                continue
            expected_version = item.expected_version
            if existing is not None and expected_version is None:
                # Allow create-or-update when caller did not pin a version, but still
                # enforce last_applied guard above.
                expected_version = int(existing.version or 1)
            self.upsert_l2_facts_v2(
                conversation_id=conversation_id,
                skill_package_id=item.skill_package_id,
                memory_namespace=ns,
                skill_name=item.skill_name,
                facts_v2=item.facts_v2,
                last_applied_run_id=run_id,
                expected_version=expected_version if existing is not None else None,
                commit=False,
            )

    # ------------------------------------------------------------------
    # Terminal finalizers
    # ------------------------------------------------------------------

    def apply_prepared_memory_and_finalize(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        prepared: PreparedMemorySet,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """Apply complete L1/L2 set then commit completed + memory committed.

        Idempotent when the Run is already ``completed`` with
        ``memory_commit_status=committed`` for this run.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            raise DurableMemoryError(CODE_POLICY_PROTOCOL, f"run not found: {run_id}")

        if (
            str(run.status) == STATUS_COMPLETED
            and str(run.memory_commit_status or "") == "committed"
        ):
            return DurableCommitResult(
                run=run,
                state_revision=int(run.state_revision),
                status=str(run.status),
                events=(),
                reused_event_keys=(),
                inserted_event_keys=(),
            )

        if str(run.status) != STATUS_RUNNING or not self.repo.is_ready_for_memory(run):
            # Terminal other outcomes are immutable; otherwise require ready_for_memory.
            if str(run.status) in {"completed", "failed", "cancelled"}:
                raise DurableMemoryError(
                    CODE_TERMINAL_IMMUTABLE,
                    f"run already terminal: {run.status}",
                )
            raise DurableMemoryError(
                CODE_NOT_READY,
                "memory finalizer requires running + ready_for_memory phase",
            )

        try:
            self._apply_prepared_rows(run=run, prepared=prepared)
            return self.repo.finalize_memory(
                run_id=run_id,
                expected_revision=expected_revision,
                lease=lease,
                memory_commit_status="committed",
                events=events,
                children=children,
            )
        except DurableMemoryError:
            self.db.rollback()
            raise
        except DurableRunConflict as exc:
            self.db.rollback()
            # Duplicate finalizer race: if another worker committed completed,
            # converge to the committed view when memory outcome matches.
            self.db.expire_all()
            current = self.repo.get_run(run_id)
            if (
                current is not None
                and str(current.status) == STATUS_COMPLETED
                and str(current.memory_commit_status or "") == "committed"
            ):
                return DurableCommitResult(
                    run=current,
                    state_revision=int(current.state_revision),
                    status=str(current.status),
                    events=(),
                    reused_event_keys=(),
                    inserted_event_keys=(),
                )
            raise DurableMemoryError(exc.code, str(exc)) from exc
        except Exception:
            self.db.rollback()
            raise

    def finalize_memory_failed(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        diagnostic_code: str = CODE_MEMORY_PROVIDER_ERROR,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """Complete the Run with ``memory_commit_status=failed`` (no L1/L2 writes).

        Preserves any previously accepted L0 final content. Idempotent when the
        Run is already completed with failed memory outcome.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            raise DurableMemoryError(CODE_POLICY_PROTOCOL, f"run not found: {run_id}")

        if (
            str(run.status) == STATUS_COMPLETED
            and str(run.memory_commit_status or "") == "failed"
        ):
            return DurableCommitResult(
                run=run,
                state_revision=int(run.state_revision),
                status=str(run.status),
                events=(),
                reused_event_keys=(),
                inserted_event_keys=(),
            )

        if str(run.status) != STATUS_RUNNING or not self.repo.is_ready_for_memory(run):
            if str(run.status) in {"completed", "failed", "cancelled"}:
                raise DurableMemoryError(
                    CODE_TERMINAL_IMMUTABLE,
                    f"run already terminal: {run.status}",
                )
            raise DurableMemoryError(
                CODE_NOT_READY,
                "memory finalizer requires running + ready_for_memory phase",
            )

        logger.warning(
            "memory finalizer failed run_id=%s code=%s",
            run_id,
            diagnostic_code,
        )
        try:
            return self.repo.finalize_memory(
                run_id=run_id,
                expected_revision=expected_revision,
                lease=lease,
                memory_commit_status="failed",
                events=events,
                children=children,
            )
        except DurableRunConflict as exc:
            self.db.rollback()
            self.db.expire_all()
            current = self.repo.get_run(run_id)
            if (
                current is not None
                and str(current.status) == STATUS_COMPLETED
                and str(current.memory_commit_status or "") == "failed"
            ):
                return DurableCommitResult(
                    run=current,
                    state_revision=int(current.state_revision),
                    status=str(current.status),
                    events=(),
                    reused_event_keys=(),
                    inserted_event_keys=(),
                )
            raise DurableMemoryError(exc.code, str(exc)) from exc

    def finalize_run_memory(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        prepared: PreparedMemorySet | None,
        compute_error: BaseException | None = None,
        events_on_success: Sequence[EventSpec] = (),
        events_on_failure: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """High-level finalizer: commit prepared set or mark memory failed.

        Memory-provider / computation failure is explicit via
        ``memory_commit_status=failed`` and never erases accepted L0 content.
        """
        if compute_error is not None or prepared is None:
            code = CODE_MEMORY_PROVIDER_ERROR
            if compute_error is not None:
                logger.exception(
                    "memory computation/provider failed run_id=%s",
                    run_id,
                    exc_info=compute_error,
                )
            return self.finalize_memory_failed(
                run_id=run_id,
                expected_revision=expected_revision,
                lease=lease,
                diagnostic_code=code,
                events=events_on_failure,
                children=children,
            )
        try:
            return self.apply_prepared_memory_and_finalize(
                run_id=run_id,
                expected_revision=expected_revision,
                lease=lease,
                prepared=prepared,
                events=events_on_success,
                children=children,
            )
        except DurableMemoryError as exc:
            if exc.code == CODE_MEMORY_REVISION_CONFLICT:
                raise
            logger.warning(
                "memory apply failed run_id=%s code=%s detail=%s",
                run_id,
                exc.code,
                exc.message,
            )
            return self.finalize_memory_failed(
                run_id=run_id,
                expected_revision=expected_revision,
                lease=lease,
                diagnostic_code=exc.code,
                events=events_on_failure,
                children=children,
            )


__all__ = [
    "CODE_DUPLICATE_NAMESPACE",
    "CODE_INVALID_FACT",
    "CODE_INVALID_FINAL_CONTENT",
    "CODE_INVALID_NAMESPACE",
    "CODE_MEMORY_PROVIDER_ERROR",
    "CODE_MEMORY_REVISION_CONFLICT",
    "CODE_NOT_READY",
    "CODE_POLICY_PROTOCOL",
    "DurableMemoryError",
    "DurableMemoryFinalizer",
    "PreparedL1Update",
    "PreparedL2Update",
    "PreparedMemorySet",
    "digest_final_content",
    "normalize_facts_v2",
    "validate_prepared_memory_set",
]
