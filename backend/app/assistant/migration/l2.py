"""Plan 10 Task 3/10 — L2 stable package-ID backfill and verify.

Backfills legacy name-keyed L2 rows onto the native triple
``(conversation_id, skill_package_id, memory_namespace)``. Mapping is
deterministic and never fuzzy. Archive digests/ids/counts before mutation; raw
facts stay out of general migration event JSON.

Deploy B2 drops the ``skill_name`` column after zero-legacy-row verify. This
module still accepts name strings for mapping resolution; row attribute access
uses ``getattr`` so pre- and post-B2 schemas both work in tooling.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.memory_service import AssistantMemoryService
from app.assistant.migration.repository import (
    CODE_FORBIDDEN_TRANSITION,
    CODE_STALE_REVISION,
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)
from app.assistant.models import AssistantConversationSkillL2Memory
from app.assistant.skills.contracts import normalize_skill_lookup_name
from app.assistant.skills.legacy_adapter import map_legacy_name_to_canonical_base
from app.assistant.skills.models import (
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
)

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_NAMESPACE = "default"
L2_SOURCE_TYPE = "conversation_skill_l2"
L2_MAX_FACTS = 10000

# Checked-in system migration map (precedence 3). Namespace is always default
# unless an exact contract later supplies another nonempty value.
SYSTEM_L2_NAMESPACE_MAP: dict[str, str] = {
    "general_chat": DEFAULT_MEMORY_NAMESPACE,  # profile provenance; not a package
    "quick_stats": DEFAULT_MEMORY_NAMESPACE,
    "periodic_review": DEFAULT_MEMORY_NAMESPACE,
    "smart_capture": DEFAULT_MEMORY_NAMESPACE,
}

Outcome = Literal[
    "migrated",
    "verified",
    "blocked",
    "unchanged",
    "failed",
    "skipped",
]


class L2MigrationError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True, slots=True)
class L2PackageMapping:
    """Resolved package target for a legacy skill_name."""

    skill_package_id: UUID
    memory_namespace: str
    skill_name: str
    mapping_source: str  # alias | system_map | package_canonical
    package_canonical_name: str
    mapping_digest: str


@dataclass(frozen=True, slots=True)
class L2ItemResult:
    source_id: str
    source_name_normalized: str
    conversation_id: str | None
    outcome: Outcome
    state: str
    reason_code: str | None = None
    target_package_id: str | None = None
    memory_namespace: str | None = None
    facts_digest: str | None = None
    fact_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "sourceNameNormalized": self.source_name_normalized,
            "conversationId": self.conversation_id,
            "subjectKind": "l2_memory",
            "outcome": self.outcome,
            "state": self.state,
            "reasonCode": self.reason_code,
            "targetPackageId": self.target_package_id,
            "memoryNamespace": self.memory_namespace,
            "factsDigest": self.facts_digest,
            "factCount": self.fact_count,
        }


@dataclass
class L2MigrationReport:
    command: str
    dry_run: bool
    request_id: str
    processed: int = 0
    succeeded: int = 0
    blocked: int = 0
    failed: int = 0
    unchanged: int = 0
    batch_id: str | None = None
    report_digest: str | None = None
    resume_cursor: str | None = None
    consecutive_zero_delta: int = 0
    steps: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.failed == 0 and self.blocked == 0,
            "command": self.command,
            "dryRun": self.dry_run,
            "requestId": self.request_id,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "blocked": self.blocked,
            "failed": self.failed,
            "unchanged": self.unchanged,
            "batchId": self.batch_id,
            "reportDigest": self.report_digest,
            "resumeCursor": self.resume_cursor,
            "consecutiveZeroDelta": self.consecutive_zero_delta,
            "steps": list(self.steps),
            "items": list(self.items),
        }


def normalize_memory_namespace(value: object | None, *, default: str = DEFAULT_MEMORY_NAMESPACE) -> str:
    """Normalize a package-backed namespace; never returns empty/null."""
    text = str(value or "").strip()
    if not text:
        return str(default or DEFAULT_MEMORY_NAMESPACE).strip() or DEFAULT_MEMORY_NAMESPACE
    return text


def facts_digest(facts: Sequence[str] | None) -> str:
    normalized = AssistantMemoryService.normalize_l2_facts(list(facts or []), max_items=L2_MAX_FACTS)
    return sha256_canonical_json({"facts": normalized, "count": len(normalized)})


def _safe_name_norm(name: str) -> str:
    try:
        return normalize_skill_lookup_name(name)
    except (TypeError, ValueError):
        return str(name or "").casefold().strip()


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _row_created_sort_key(row: AssistantConversationSkillL2Memory) -> tuple[str, str]:
    created = getattr(row, "created_at", None)
    created_s = created.isoformat() if created is not None else ""
    return (created_s, str(row.id))


def _mapping_digest(
    *,
    skill_package_id: UUID,
    memory_namespace: str,
    mapping_source: str,
    package_canonical_name: str,
    skill_name_normalized: str,
) -> str:
    return sha256_canonical_json(
        {
            "skillPackageId": str(skill_package_id),
            "memoryNamespace": memory_namespace,
            "mappingSource": mapping_source,
            "packageCanonicalName": package_canonical_name,
            "skillNameNormalized": skill_name_normalized,
        }
    )


def resolve_l2_package_mapping(
    session: Session,
    skill_name: str,
    *,
    system_namespace_map: Mapping[str, str] | None = None,
) -> L2PackageMapping | None:
    """Resolve legacy skill_name → package triple using plan precedence.

    Precedence:
    1. (removed) AssistantSkill name→legacy_skill_id join after table drop
    2. exact normalized canonical/legacy/custom alias unique match
    3. checked-in system map + unique package canonical match
    4. unique package.canonical_name from map_legacy_name_to_canonical_base

    Returns None when unmapped. Raises L2MigrationError on ambiguity.
    """
    raw_name = str(skill_name or "").strip()
    if not raw_name:
        return None
    name_norm = _safe_name_norm(raw_name)
    ns_map = dict(system_namespace_map or SYSTEM_L2_NAMESPACE_MAP)

    # general_chat is profile provenance — never a skill package.
    if name_norm in {"general_chat", "general-chat"} or raw_name in {"general_chat", "general-chat"}:
        raise L2MigrationError(
            "general_chat_not_a_skill_package",
            "general_chat L2 memory has no package target",
        )

    candidates: list[tuple[AssistantSkillPackage, str, str]] = []

    # 1) legacy_skill_id name match is unavailable after assistant_skill drop.
    #    Packages retain legacy_skill_id UUID provenance only (no join table).
    #    Prefer alias / system map / canonical match.

    # 2) exact unique alias match (canonical / legacy / custom; ignore disabled custom).
    alias = (
        session.query(AssistantSkillPackageAlias)
        .filter(AssistantSkillPackageAlias.normalized_alias == name_norm)
        .one_or_none()
    )
    if alias is not None and alias.disabled_at is None:
        pkg = session.get(AssistantSkillPackage, alias.skill_package_id)
        if pkg is not None:
            candidates.append((pkg, "alias", DEFAULT_MEMORY_NAMESPACE))
    elif alias is None:
        # Also try exact alias column match for non-normalized forms.
        alias2 = (
            session.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.alias == raw_name)
            .one_or_none()
        )
        if alias2 is not None and alias2.disabled_at is None:
            pkg = session.get(AssistantSkillPackage, alias2.skill_package_id)
            if pkg is not None:
                candidates.append((pkg, "alias", DEFAULT_MEMORY_NAMESPACE))

    # 3) checked-in system map → unique package by canonical name.
    system_ns = None
    for key, ns in ns_map.items():
        if _safe_name_norm(key) == name_norm or key == raw_name:
            system_ns = normalize_memory_namespace(ns)
            try:
                base = map_legacy_name_to_canonical_base(key, UUID(int=0))
            except Exception:  # noqa: BLE001
                base = key.replace("_", "-").casefold()
            pkg = (
                session.query(AssistantSkillPackage)
                .filter(AssistantSkillPackage.canonical_name == base)
                .one_or_none()
            )
            if pkg is not None:
                candidates.append((pkg, "system_map", system_ns))
            break

    # 4) unique package canonical derived from the skill_name itself.
    try:
        derived = map_legacy_name_to_canonical_base(raw_name, UUID(int=0))
    except Exception:  # noqa: BLE001
        derived = None
    if derived:
        pkg = (
            session.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name == derived)
            .one_or_none()
        )
        if pkg is not None:
            candidates.append((pkg, "package_canonical", DEFAULT_MEMORY_NAMESPACE))

    if not candidates:
        return None

    # Deduplicate by package id; conflicting packages → ambiguity.
    by_pkg: dict[UUID, list[tuple[AssistantSkillPackage, str, str]]] = defaultdict(list)
    for pkg, src, ns in candidates:
        by_pkg[pkg.id].append((pkg, src, ns))
    if len(by_pkg) > 1:
        raise L2MigrationError(
            "ambiguous_package_mapping",
            f"skill_name {raw_name!r} maps to multiple packages",
        )

    pkg, src, ns = next(iter(by_pkg.values()))[0]
    # Prefer strongest mapping source among same package.
    priority = {"alias": 0, "system_map": 1, "package_canonical": 2}
    best = min(next(iter(by_pkg.values())), key=lambda t: priority.get(t[1], 99))
    pkg, src, ns = best
    ns = normalize_memory_namespace(ns)
    display = str(pkg.canonical_name or raw_name)
    digest = _mapping_digest(
        skill_package_id=pkg.id,
        memory_namespace=ns,
        mapping_source=src,
        package_canonical_name=str(pkg.canonical_name or ""),
        skill_name_normalized=name_norm,
    )
    return L2PackageMapping(
        skill_package_id=pkg.id,
        memory_namespace=ns,
        skill_name=display,
        mapping_source=src,
        package_canonical_name=str(pkg.canonical_name or ""),
        mapping_digest=digest,
    )


def _row_skill_name(row: AssistantConversationSkillL2Memory) -> str:
    """Compatibility accessor — skill_name column may be absent post-B2."""
    return str(getattr(row, "skill_name", None) or "")


def _archive_evidence_payload(
    row: AssistantConversationSkillL2Memory,
    *,
    mapping: L2PackageMapping | None,
) -> dict[str, Any]:
    """Bounded safe evidence — digests/ids/counts only, never raw facts."""
    facts = AssistantMemoryService.normalize_l2_facts(row.facts, max_items=L2_MAX_FACTS)
    payload: dict[str, Any] = {
        "sourceRowId": str(row.id),
        "conversationId": str(row.conversation_id),
        "skillName": _row_skill_name(row)[:200],
        "sourceVersion": int(row.version or 1),
        "factCount": len(facts),
        "factsDigest": facts_digest(facts),
        "hadFactsV2": row.facts_v2 is not None,
        "lastAppliedRunId": str(row.last_applied_run_id) if row.last_applied_run_id else None,
        "previousPackageId": str(row.skill_package_id) if row.skill_package_id else None,
        "previousNamespace": row.memory_namespace,
    }
    if mapping is not None:
        payload["targetPackageId"] = str(mapping.skill_package_id)
        payload["targetNamespace"] = mapping.memory_namespace
        payload["mappingSource"] = mapping.mapping_source
        payload["mappingDigest"] = mapping.mapping_digest
    return payload


def _ensure_discovered_item(
    repo: RuntimeMigrationRepository,
    row: AssistantConversationSkillL2Memory,
    *,
    actor_principal: str | None,
    build_revision: str | None,
) -> Any:
    name_norm = _safe_name_norm(_row_skill_name(row))
    digest = sha256_canonical_json(
        {
            "subjectKind": "l2_memory",
            "sourceId": str(row.id),
            "conversationId": str(row.conversation_id),
            "skillName": name_norm,
            "version": int(row.version or 1),
            "factsDigest": facts_digest(row.facts),
            "skillPackageId": str(row.skill_package_id) if row.skill_package_id else None,
            "memoryNamespace": row.memory_namespace,
        }
    )
    item, _outcome = repo.upsert_discovered_item(
        subject_kind="l2_memory",
        source_type=L2_SOURCE_TYPE,
        source_id=str(row.id),
        source_name=_row_skill_name(row)[:256],
        source_name_normalized=name_norm[:256],
        source_digest=digest,
        evidence_json={
            "conversationId": str(row.conversation_id),
            "factCount": len(
                AssistantMemoryService.normalize_l2_facts(row.facts, max_items=L2_MAX_FACTS)
            ),
            "factsDigest": facts_digest(row.facts),
            "sourceVersion": int(row.version or 1),
        },
        actor_principal=actor_principal,
        build_revision=build_revision,
        reason_code="legacy_null_package_l2"
        if row.skill_package_id is None
        else "native_l2_row",
    )
    return item


def _transition(
    repo: RuntimeMigrationRepository,
    item: Any,
    *,
    to_state: str,
    reason_code: str | None,
    evidence_json: Mapping[str, Any] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_digest: str | None = None,
    actor_principal: str | None = None,
    build_revision: str | None = None,
) -> Any:
    if item is None:
        return None
    if str(item.state) == to_state:
        return item
    # Allow blocked → mapped via remap when reprocessing.
    try:
        return repo.transition_item(
            item_id=item.id,
            expected_revision=int(item.state_revision),
            to_state=to_state,
            reason_code=reason_code,
            evidence_json=evidence_json,
            target_type=target_type,
            target_id=target_id,
            target_digest=target_digest,
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
    except RuntimeMigrationRepositoryError as exc:
        if exc.code == CODE_FORBIDDEN_TRANSITION and str(item.state) == "blocked" and to_state == "mapped":
            item = repo.transition_item(
                item_id=item.id,
                expected_revision=int(item.state_revision),
                to_state="discovered",
                reason_code="rediscover_for_remap",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
            return repo.transition_item(
                item_id=item.id,
                expected_revision=int(item.state_revision),
                to_state=to_state,
                reason_code=reason_code,
                evidence_json=evidence_json,
                target_type=target_type,
                target_id=target_id,
                target_digest=target_digest,
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        if exc.code == CODE_FORBIDDEN_TRANSITION and str(item.state) in {"migrated", "verified"} and to_state == "migrated":
            return item
        if exc.code == CODE_FORBIDDEN_TRANSITION and str(item.state) == "verified" and to_state == "verified":
            return item
        if exc.code == CODE_FORBIDDEN_TRANSITION and str(item.state) == "migrated" and to_state == "verified":
            return repo.transition_item(
                item_id=item.id,
                expected_revision=int(item.state_revision),
                to_state="verified",
                reason_code=reason_code,
                evidence_json=evidence_json,
                target_type=target_type,
                target_id=target_id,
                target_digest=target_digest,
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        raise


def _merge_facts(sources: Sequence[AssistantConversationSkillL2Memory]) -> list[str]:
    ordered = sorted(sources, key=_row_created_sort_key)
    merged: list[str] = []
    for row in ordered:
        merged.extend(list(row.facts or []))
    return AssistantMemoryService.normalize_l2_facts(merged, max_items=L2_MAX_FACTS)


def _merge_facts_v2(sources: Sequence[AssistantConversationSkillL2Memory]) -> list[dict[str, Any]] | None:
    ordered = sorted(sources, key=_row_created_sort_key)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    any_v2 = False
    for row in ordered:
        if row.facts_v2 is None:
            continue
        any_v2 = True
        if not isinstance(row.facts_v2, list):
            continue
        for raw in row.facts_v2:
            if not isinstance(raw, Mapping):
                continue
            text_val = str(raw.get("text") or "").strip()
            if not text_val or text_val in seen:
                continue
            seen.add(text_val)
            out.append(dict(raw))
            if len(out) >= L2_MAX_FACTS:
                return out
    return out if any_v2 else None


def _select_legacy_rows(
    session: Session,
    *,
    after_id: UUID | None,
    limit: int,
    conversation_ids: Sequence[UUID] | None = None,
    row_ids: Sequence[UUID] | None = None,
) -> list[AssistantConversationSkillL2Memory]:
    q = session.query(AssistantConversationSkillL2Memory).filter(
        AssistantConversationSkillL2Memory.skill_package_id.is_(None)
    )
    if conversation_ids:
        q = q.filter(AssistantConversationSkillL2Memory.conversation_id.in_(list(conversation_ids)))
    if row_ids:
        q = q.filter(AssistantConversationSkillL2Memory.id.in_(list(row_ids)))
    if after_id is not None:
        # Cursor is the last processed row id; scan continues in id order so
        # resume is stable even when created_at ties or clocks skew.
        q = q.filter(AssistantConversationSkillL2Memory.id > after_id)
    # Cursor-stable order: UUID primary key. Group merge still orders sources
    # by created_at then UUID for deterministic fact merge.
    q = q.order_by(AssistantConversationSkillL2Memory.id.asc())
    return list(q.limit(max(1, int(limit))).all())


def _find_native_row(
    session: Session,
    *,
    conversation_id: UUID,
    skill_package_id: UUID,
    memory_namespace: str,
) -> AssistantConversationSkillL2Memory | None:
    ns = normalize_memory_namespace(memory_namespace)
    return (
        session.query(AssistantConversationSkillL2Memory)
        .filter(
            AssistantConversationSkillL2Memory.conversation_id == conversation_id,
            AssistantConversationSkillL2Memory.skill_package_id == skill_package_id,
            AssistantConversationSkillL2Memory.memory_namespace == ns,
        )
        .one_or_none()
    )


def _apply_group_merge(
    session: Session,
    *,
    sources: Sequence[AssistantConversationSkillL2Memory],
    mapping: L2PackageMapping,
    dry_run: bool,
) -> tuple[AssistantConversationSkillL2Memory | None, list[str], str]:
    """Merge sources into one native triple row. Returns (row, facts, facts_digest)."""
    if not sources:
        return None, [], facts_digest([])
    ordered = sorted(sources, key=_row_created_sort_key)
    ns = normalize_memory_namespace(mapping.memory_namespace)

    existing_native = _find_native_row(
        session,
        conversation_id=ordered[0].conversation_id,
        skill_package_id=mapping.skill_package_id,
        memory_namespace=ns,
    )
    # Include pre-existing native triple in the merge set so legacy→native
    # never drops already-present package-backed facts.
    merge_inputs: list[AssistantConversationSkillL2Memory] = list(ordered)
    if existing_native is not None and all(r.id != existing_native.id for r in merge_inputs):
        merge_inputs.append(existing_native)
    merge_inputs = sorted(merge_inputs, key=_row_created_sort_key)

    merged_facts = _merge_facts(merge_inputs)
    merged_v2 = _merge_facts_v2(merge_inputs)
    digest = facts_digest(merged_facts)

    # Prefer existing native, else first source as survivor.
    if existing_native is not None:
        survivor = existing_native
        extras = [r for r in ordered if r.id != survivor.id]
    else:
        survivor = ordered[0]
        extras = list(ordered[1:])

    if dry_run:
        return survivor, merged_facts, digest

    # Recheck source versions under lock when dialect supports it.
    try:
        locked = (
            session.execute(
                select(AssistantConversationSkillL2Memory)
                .where(AssistantConversationSkillL2Memory.id.in_([r.id for r in ordered]))
                .with_for_update()
            )
            .scalars()
            .all()
        )
        locked_by_id = {r.id: r for r in locked}
        for src in ordered:
            current = locked_by_id.get(src.id)
            if current is None:
                raise L2MigrationError("source_row_missing", f"L2 row {src.id} vanished during merge")
            if int(current.version or 1) != int(src.version or 1):
                raise L2MigrationError(
                    "source_version_drift",
                    f"L2 row {src.id} version changed during merge",
                )
            if current.skill_package_id is not None and (
                current.skill_package_id != mapping.skill_package_id
                or normalize_memory_namespace(current.memory_namespace) != ns
            ):
                # Already migrated to same triple is ok if it's the survivor.
                if not (
                    current.skill_package_id == mapping.skill_package_id
                    and normalize_memory_namespace(current.memory_namespace) == ns
                ):
                    raise L2MigrationError(
                        "source_already_mapped_elsewhere",
                        f"L2 row {src.id} already has a different package mapping",
                    )
    except L2MigrationError:
        raise
    except Exception:  # noqa: BLE001
        # SQLite / dialects without FOR UPDATE: proceed with re-read checks.
        for src in ordered:
            session.refresh(src)
            if src.skill_package_id is not None and src.id != getattr(survivor, "id", None):
                if not (
                    src.skill_package_id == mapping.skill_package_id
                    and normalize_memory_namespace(src.memory_namespace) == ns
                ):
                    raise L2MigrationError(
                        "source_already_mapped_elsewhere",
                        f"L2 row {src.id} already has a different package mapping",
                    )

    # Re-resolve survivor after locks.
    existing_native = _find_native_row(
        session,
        conversation_id=ordered[0].conversation_id,
        skill_package_id=mapping.skill_package_id,
        memory_namespace=ns,
    )
    if existing_native is not None:
        survivor = existing_native
        extras = [r for r in ordered if r.id != survivor.id]
    else:
        survivor = ordered[0]
        extras = list(ordered[1:])

    prev_facts = list(survivor.facts or [])
    survivor.skill_package_id = mapping.skill_package_id
    survivor.memory_namespace = ns
    # skill_name column is removed in Deploy B2; keep assignment only when present.
    if hasattr(survivor, "skill_name"):
        survivor.skill_name = str(
            mapping.skill_name or _row_skill_name(survivor) or "skill"
        )[:100]
    if prev_facts != merged_facts or survivor.facts_v2 != merged_v2:
        survivor.version = int(survivor.version or 1) + 1
    survivor.facts = merged_facts
    if merged_v2 is not None:
        survivor.facts_v2 = merged_v2
    # Prefer last non-null last_applied_run_id from newest merge input.
    for row in reversed(merge_inputs):
        if row.last_applied_run_id is not None:
            survivor.last_applied_run_id = row.last_applied_run_id
            break
    session.add(survivor)

    for extra in extras:
        if extra.id == survivor.id:
            continue
        # If an extra is already the same native triple (shouldn't), skip delete.
        if (
            extra.skill_package_id == mapping.skill_package_id
            and normalize_memory_namespace(extra.memory_namespace) == ns
        ):
            continue
        session.delete(extra)

    session.flush()
    return survivor, merged_facts, digest


def _tally(report: L2MigrationReport, result: L2ItemResult) -> None:
    report.processed += 1
    report.items.append(result.as_dict())
    if result.outcome == "blocked":
        report.blocked += 1
    elif result.outcome == "failed":
        report.failed += 1
    elif result.outcome == "unchanged":
        report.unchanged += 1
    elif result.outcome in {"migrated", "verified"}:
        report.succeeded += 1


def backfill_l2(
    session: Session,
    *,
    request_id: str,
    actor_principal: str | None = None,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    schema_head: str,
    dry_run: bool = True,
    batch_size: int = 100,
    source_snapshot_digest: str | None = None,
    conversation_ids: Sequence[UUID] | None = None,
    row_ids: Sequence[UUID] | None = None,
    resume_cursor: str | None = None,
    system_namespace_map: Mapping[str, str] | None = None,
) -> L2MigrationReport:
    """Backfill legacy L2 rows onto package/namespace identity.

    Archive-before-mutation, bounded batches with cursor, deterministic merge.
    Idempotent: already-native rows are skipped; reruns re-merge to same digest.
    """
    repo = RuntimeMigrationRepository(session)
    report = L2MigrationReport(
        command="l2.backfill",
        dry_run=dry_run,
        request_id=request_id,
        resume_cursor=resume_cursor,
    )
    snapshot_digest = source_snapshot_digest or sha256_canonical_json(
        {
            "command": "l2.backfill",
            "requestId": request_id,
            "conversationIds": [str(c) for c in (conversation_ids or ())],
            "rowIds": [str(r) for r in (row_ids or ())],
        }
    )
    if len(snapshot_digest) != 64:
        snapshot_digest = sha256_canonical_json({"raw": snapshot_digest})
    config_digest = sha256_canonical_json(
        {
            "command": "l2.backfill",
            "batchSize": int(batch_size),
            "dryRun": bool(dry_run),
            "systemMap": dict(system_namespace_map or SYSTEM_L2_NAMESPACE_MAP),
        }
    )

    batch = None
    if not dry_run:
        batch = repo.prepare_batch(
            command_kind="l2",
            source_snapshot_digest=snapshot_digest,
            configuration_digest=config_digest,
            build_revision=build_revision,
            schema_revision=schema_head,
            environment=environment,
            database_fingerprint=database_fingerprint,
            request_id=request_id,
            batch_size=batch_size,
            started_by=actor_principal,
        )
        if str(batch.status) == "prepared":
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status="running",
                resume_cursor=resume_cursor,
            )
        report.batch_id = str(batch.id)
        report.steps.append("batch_running")
        if batch.resume_cursor and not resume_cursor:
            resume_cursor = batch.resume_cursor

    after_id = _as_uuid(resume_cursor)
    rows = _select_legacy_rows(
        session,
        after_id=after_id,
        limit=batch_size,
        conversation_ids=conversation_ids,
        row_ids=row_ids,
    )
    report.steps.append(f"selected_legacy_rows={len(rows)}")

    # Resolve mappings and group by triple.
    blocked_rows: list[tuple[AssistantConversationSkillL2Memory, str]] = []
    groups: dict[tuple[UUID, UUID, str], list[tuple[AssistantConversationSkillL2Memory, L2PackageMapping]]] = (
        defaultdict(list)
    )

    for row in rows:
        try:
            mapping = resolve_l2_package_mapping(
                session,
                _row_skill_name(row),
                system_namespace_map=system_namespace_map,
            )
        except L2MigrationError as exc:
            blocked_rows.append((row, exc.reason_code))
            continue
        if mapping is None:
            blocked_rows.append((row, "unmapped_skill_name"))
            continue
        key = (row.conversation_id, mapping.skill_package_id, mapping.memory_namespace)
        groups[key].append((row, mapping))

    # Process blockers first (evidence only).
    for row, reason in blocked_rows:
        item = None
        if not dry_run:
            item = _ensure_discovered_item(
                repo,
                row,
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
            evidence = _archive_evidence_payload(row, mapping=None)
            item = _transition(
                repo,
                item,
                to_state="blocked",
                reason_code=reason,
                evidence_json=evidence,
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        _tally(
            report,
            L2ItemResult(
                source_id=str(row.id),
                source_name_normalized=_safe_name_norm(_row_skill_name(row)),
                conversation_id=str(row.conversation_id),
                outcome="blocked",
                state="blocked",
                reason_code=reason,
                fact_count=len(
                    AssistantMemoryService.normalize_l2_facts(row.facts, max_items=L2_MAX_FACTS)
                ),
                facts_digest=facts_digest(row.facts),
            ),
        )

    # Process each target triple group.
    last_cursor: str | None = resume_cursor
    max_processed_id: UUID | None = _as_uuid(resume_cursor)
    for (_conv, _pkg, _ns), members in sorted(
        groups.items(),
        key=lambda kv: (
            str(kv[0][0]),
            str(kv[0][1]),
            kv[0][2],
            min(_row_created_sort_key(m[0]) for m in kv[1]),
        ),
    ):
        sources = [m[0] for m in members]
        mapping = members[0][1]
        # Archive evidence before mutation for every source.
        items_by_source: dict[UUID, Any] = {}
        if not dry_run:
            for src in sources:
                item = _ensure_discovered_item(
                    repo,
                    src,
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
                evidence = _archive_evidence_payload(src, mapping=mapping)
                item = _transition(
                    repo,
                    item,
                    to_state="mapped",
                    reason_code="mapped_to_package_namespace",
                    evidence_json=evidence,
                    target_type="skill_package",
                    target_id=str(mapping.skill_package_id),
                    target_digest=mapping.mapping_digest,
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
                items_by_source[src.id] = item

        try:
            survivor, merged_facts, digest = _apply_group_merge(
                session,
                sources=sources,
                mapping=mapping,
                dry_run=dry_run,
            )
        except L2MigrationError as exc:
            for src in sources:
                if not dry_run and src.id in items_by_source:
                    _transition(
                        repo,
                        items_by_source[src.id],
                        to_state="blocked",
                        reason_code=exc.reason_code,
                        evidence_json={"error": exc.message[:200]},
                        actor_principal=actor_principal,
                        build_revision=build_revision,
                    )
                _tally(
                    report,
                    L2ItemResult(
                        source_id=str(src.id),
                        source_name_normalized=_safe_name_norm(_row_skill_name(src)),
                        conversation_id=str(src.conversation_id),
                        outcome="blocked",
                        state="blocked",
                        reason_code=exc.reason_code,
                        target_package_id=str(mapping.skill_package_id),
                        memory_namespace=mapping.memory_namespace,
                    ),
                )
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("l2_backfill_group_failed")
            for src in sources:
                _tally(
                    report,
                    L2ItemResult(
                        source_id=str(src.id),
                        source_name_normalized=_safe_name_norm(_row_skill_name(src)),
                        conversation_id=str(src.conversation_id),
                        outcome="failed",
                        state="error",
                        reason_code=type(exc).__name__,
                        target_package_id=str(mapping.skill_package_id),
                        memory_namespace=mapping.memory_namespace,
                    ),
                )
            continue

        for src in sources:
            if not dry_run and src.id in items_by_source:
                _transition(
                    repo,
                    items_by_source[src.id],
                    to_state="migrated",
                    reason_code="l2_package_backfilled",
                    evidence_json={
                        "targetPackageId": str(mapping.skill_package_id),
                        "targetNamespace": mapping.memory_namespace,
                        "mergedFactsDigest": digest,
                        "mergedFactCount": len(merged_facts),
                        "survivorRowId": str(survivor.id) if survivor is not None else None,
                        "mappingDigest": mapping.mapping_digest,
                    },
                    target_type="skill_package",
                    target_id=str(mapping.skill_package_id),
                    target_digest=digest,
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
            if max_processed_id is None or src.id > max_processed_id:
                max_processed_id = src.id
            _tally(
                report,
                L2ItemResult(
                    source_id=str(src.id),
                    source_name_normalized=_safe_name_norm(_row_skill_name(src)),
                    conversation_id=str(src.conversation_id),
                    outcome="migrated",
                    state="migrated",
                    reason_code="l2_package_backfilled",
                    target_package_id=str(mapping.skill_package_id),
                    memory_namespace=mapping.memory_namespace,
                    facts_digest=digest,
                    fact_count=len(merged_facts),
                ),
            )

    # Also advance cursor past blocked rows in this batch so resume does not loop.
    for row, _reason in blocked_rows:
        if max_processed_id is None or row.id > max_processed_id:
            max_processed_id = row.id

    last_cursor = str(max_processed_id) if max_processed_id is not None else resume_cursor
    report.resume_cursor = last_cursor
    report.report_digest = sha256_canonical_json(
        {
            "command": report.command,
            "processed": report.processed,
            "succeeded": report.succeeded,
            "blocked": report.blocked,
            "failed": report.failed,
            "items": [
                {
                    "sourceId": i.get("sourceId"),
                    "state": i.get("state"),
                    "outcome": i.get("outcome"),
                    "targetPackageId": i.get("targetPackageId"),
                    "factsDigest": i.get("factsDigest"),
                }
                for i in report.items
            ],
        }
    )

    if batch is not None and not dry_run:
        try:
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status="completed",
                resume_cursor=last_cursor,
                processed_delta=report.processed,
                succeeded_delta=report.succeeded,
                blocked_delta=report.blocked,
                failed_delta=report.failed,
                report_digest=report.report_digest,
                completed_by=actor_principal,
            )
            report.batch_id = str(batch.id)
            report.steps.append("batch_completed")
        except RuntimeMigrationRepositoryError as exc:
            if exc.code not in {CODE_FORBIDDEN_TRANSITION, CODE_STALE_REVISION}:
                raise
            report.steps.append(f"batch_complete_skipped:{exc.code}")

    if not dry_run:
        session.flush()
    return report


def _native_invariants(session: Session) -> dict[str, Any]:
    rows = session.query(AssistantConversationSkillL2Memory).all()
    legacy = [r for r in rows if r.skill_package_id is None]
    native = [r for r in rows if r.skill_package_id is not None]
    invalid_shape = [
        r
        for r in native
        if r.memory_namespace is None or not str(r.memory_namespace).strip()
    ]
    # Detect (package,NULL) vs (package,default) split is already prevented by
    # check constraint; also detect duplicate triples.
    triple_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    digests: dict[str, str] = {}
    for r in native:
        ns = normalize_memory_namespace(r.memory_namespace)
        key = (str(r.conversation_id), str(r.skill_package_id), ns)
        triple_counts[key] += 1
        digests[f"{key[0]}|{key[1]}|{key[2]}"] = facts_digest(r.facts)

    return {
        "totalRows": len(rows),
        "legacyRows": len(legacy),
        "nativeRows": len(native),
        "invalidShapeRows": len(invalid_shape),
        "duplicateTriples": sum(1 for c in triple_counts.values() if c > 1),
        "tripleDigests": digests,
        "legacyRowIds": sorted(str(r.id) for r in legacy),
    }


def verify_l2(
    session: Session,
    *,
    request_id: str,
    actor_principal: str | None = None,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    schema_head: str,
    dry_run: bool = True,
    batch_size: int = 100,
    source_snapshot_digest: str | None = None,
    require_zero_legacy: bool = False,
    stability_scans: int = 2,
) -> L2MigrationReport:
    """Independent L2 verify pass.

    Requires ``stability_scans`` consecutive locked-window zero-delta scans when
    not dry-run. Marks migrated items verified when their target triple matches
    the archived mapping digest and fact digest invariants hold.
    """
    repo = RuntimeMigrationRepository(session)
    report = L2MigrationReport(
        command="l2.verify",
        dry_run=dry_run,
        request_id=request_id,
    )
    snapshot_digest = source_snapshot_digest or sha256_canonical_json(
        {"command": "l2.verify", "requestId": request_id}
    )
    if len(snapshot_digest) != 64:
        snapshot_digest = sha256_canonical_json({"raw": snapshot_digest})
    config_digest = sha256_canonical_json(
        {
            "command": "l2.verify",
            "batchSize": int(batch_size),
            "requireZeroLegacy": bool(require_zero_legacy),
            "stabilityScans": int(stability_scans),
        }
    )

    batch = None
    if not dry_run:
        batch = repo.prepare_batch(
            command_kind="verify",
            source_snapshot_digest=snapshot_digest,
            configuration_digest=config_digest,
            build_revision=build_revision,
            schema_revision=schema_head,
            environment=environment,
            database_fingerprint=database_fingerprint,
            request_id=request_id,
            batch_size=batch_size,
            started_by=actor_principal,
        )
        if str(batch.status) == "prepared":
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status="running",
            )
        report.batch_id = str(batch.id)

    prev_invariants: dict[str, Any] | None = None
    zero_delta = 0
    scans = max(1, int(stability_scans))
    for scan_idx in range(scans):
        inv = _native_invariants(session)
        report.steps.append(
            f"scan_{scan_idx}:legacy={inv['legacyRows']}:native={inv['nativeRows']}"
            f":invalid={inv['invalidShapeRows']}:dup={inv['duplicateTriples']}"
        )
        if prev_invariants is not None:
            delta = {
                "legacyRows": inv["legacyRows"] - prev_invariants["legacyRows"],
                "nativeRows": inv["nativeRows"] - prev_invariants["nativeRows"],
                "digestsEqual": inv["tripleDigests"] == prev_invariants["tripleDigests"],
            }
            if (
                delta["legacyRows"] == 0
                and delta["nativeRows"] == 0
                and delta["digestsEqual"]
            ):
                zero_delta += 1
            else:
                zero_delta = 0
        prev_invariants = inv

    report.consecutive_zero_delta = zero_delta
    assert prev_invariants is not None

    # Block on structural problems.
    if prev_invariants["invalidShapeRows"] > 0:
        report.blocked += prev_invariants["invalidShapeRows"]
        report.steps.append("blocked:invalid_namespace_shape")
    if prev_invariants["duplicateTriples"] > 0:
        report.blocked += prev_invariants["duplicateTriples"]
        report.steps.append("blocked:duplicate_triples")
    if require_zero_legacy and prev_invariants["legacyRows"] > 0:
        report.blocked += prev_invariants["legacyRows"]
        report.steps.append("blocked:legacy_rows_remain")

    # Verify migrated items → verified when survivor exists with matching digest.
    if not dry_run:
        from app.assistant.migration.models import AssistantRuntimeMigrationItem

        items = (
            session.query(AssistantRuntimeMigrationItem)
            .filter(
                AssistantRuntimeMigrationItem.subject_kind == "l2_memory",
                AssistantRuntimeMigrationItem.source_type == L2_SOURCE_TYPE,
            )
            .all()
        )
        for item in items:
            report.processed += 1
            state = str(item.state)
            if state == "blocked":
                report.blocked += 1
                report.items.append(
                    {
                        "sourceId": item.source_id,
                        "state": state,
                        "outcome": "blocked",
                        "reasonCode": item.reason_code,
                    }
                )
                continue
            if state not in {"migrated", "verified", "mapped"}:
                report.unchanged += 1
                report.items.append(
                    {
                        "sourceId": item.source_id,
                        "state": state,
                        "outcome": "unchanged",
                    }
                )
                continue

            evidence = item.evidence_json or {}
            target_pkg = evidence.get("targetPackageId") or item.target_id
            target_ns = evidence.get("targetNamespace") or DEFAULT_MEMORY_NAMESPACE
            conv_id = evidence.get("conversationId")
            if not target_pkg or not conv_id:
                # Fall back: load original row if still present.
                row = session.get(AssistantConversationSkillL2Memory, _as_uuid(item.source_id))
                if row is None or row.skill_package_id is None:
                    report.blocked += 1
                    report.items.append(
                        {
                            "sourceId": item.source_id,
                            "state": "blocked",
                            "outcome": "blocked",
                            "reasonCode": "verify_target_missing",
                        }
                    )
                    if state != "blocked":
                        try:
                            _transition(
                                repo,
                                item,
                                to_state="blocked",
                                reason_code="verify_target_missing",
                                actor_principal=actor_principal,
                                build_revision=build_revision,
                            )
                        except RuntimeMigrationRepositoryError:
                            pass
                    continue
                target_pkg = str(row.skill_package_id)
                target_ns = normalize_memory_namespace(row.memory_namespace)
                conv_id = str(row.conversation_id)

            native = _find_native_row(
                session,
                conversation_id=UUID(str(conv_id)),
                skill_package_id=UUID(str(target_pkg)),
                memory_namespace=str(target_ns),
            )
            if native is None:
                report.blocked += 1
                report.items.append(
                    {
                        "sourceId": item.source_id,
                        "state": "blocked",
                        "outcome": "blocked",
                        "reasonCode": "verify_native_row_missing",
                    }
                )
                try:
                    _transition(
                        repo,
                        item,
                        to_state="blocked",
                        reason_code="verify_native_row_missing",
                        actor_principal=actor_principal,
                        build_revision=build_revision,
                    )
                except RuntimeMigrationRepositoryError:
                    pass
                continue

            expected_digest = evidence.get("mergedFactsDigest")
            actual_digest = facts_digest(native.facts)
            if expected_digest and expected_digest != actual_digest:
                # Idempotent re-merge may bump version; digest mismatch is a real drift.
                report.blocked += 1
                report.items.append(
                    {
                        "sourceId": item.source_id,
                        "state": "blocked",
                        "outcome": "blocked",
                        "reasonCode": "verify_facts_digest_mismatch",
                        "factsDigest": actual_digest,
                    }
                )
                try:
                    _transition(
                        repo,
                        item,
                        to_state="blocked",
                        reason_code="verify_facts_digest_mismatch",
                        evidence_json={
                            "expectedFactsDigest": expected_digest,
                            "observedFactsDigest": actual_digest,
                        },
                        actor_principal=actor_principal,
                        build_revision=build_revision,
                    )
                except RuntimeMigrationRepositoryError:
                    pass
                continue

            if state != "verified":
                try:
                    if state == "mapped":
                        item = _transition(
                            repo,
                            item,
                            to_state="migrated",
                            reason_code="verify_promoted_mapped",
                            actor_principal=actor_principal,
                            build_revision=build_revision,
                        )
                    _transition(
                        repo,
                        item,
                        to_state="verified",
                        reason_code="l2_package_verified",
                        evidence_json={
                            "verifiedFactsDigest": actual_digest,
                            "verifiedFactCount": len(
                                AssistantMemoryService.normalize_l2_facts(
                                    native.facts, max_items=L2_MAX_FACTS
                                )
                            ),
                        },
                        target_type="skill_package",
                        target_id=str(target_pkg),
                        target_digest=actual_digest,
                        actor_principal=actor_principal,
                        build_revision=build_revision,
                    )
                except RuntimeMigrationRepositoryError as exc:
                    report.failed += 1
                    report.items.append(
                        {
                            "sourceId": item.source_id,
                            "state": state,
                            "outcome": "failed",
                            "reasonCode": exc.code,
                        }
                    )
                    continue

            report.succeeded += 1
            report.items.append(
                {
                    "sourceId": item.source_id,
                    "state": "verified",
                    "outcome": "verified",
                    "targetPackageId": str(target_pkg),
                    "memoryNamespace": normalize_memory_namespace(target_ns),
                    "factsDigest": actual_digest,
                }
            )
    else:
        # Dry-run verify: structural summary only.
        report.processed = prev_invariants["totalRows"]
        report.succeeded = prev_invariants["nativeRows"]
        report.unchanged = prev_invariants["legacyRows"]

    # Stability requirement for non-dry-run: need scans-1 consecutive zero deltas
    # (i.e. all scans equal).
    if not dry_run and scans >= 2 and zero_delta < (scans - 1):
        report.blocked += 1
        report.steps.append("blocked:stability_not_reached")

    report.report_digest = sha256_canonical_json(
        {
            "command": report.command,
            "processed": report.processed,
            "succeeded": report.succeeded,
            "blocked": report.blocked,
            "failed": report.failed,
            "invariants": {
                "legacyRows": prev_invariants["legacyRows"],
                "nativeRows": prev_invariants["nativeRows"],
                "invalidShapeRows": prev_invariants["invalidShapeRows"],
                "duplicateTriples": prev_invariants["duplicateTriples"],
                "tripleDigests": prev_invariants["tripleDigests"],
            },
            "consecutiveZeroDelta": report.consecutive_zero_delta,
        }
    )

    if batch is not None and not dry_run:
        try:
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status="completed",
                processed_delta=report.processed,
                succeeded_delta=report.succeeded,
                blocked_delta=report.blocked,
                failed_delta=report.failed,
                report_digest=report.report_digest,
                completed_by=actor_principal,
            )
            report.batch_id = str(batch.id)
            report.steps.append("batch_completed")
        except RuntimeMigrationRepositoryError as exc:
            if exc.code not in {CODE_FORBIDDEN_TRANSITION, CODE_STALE_REVISION}:
                raise
            report.steps.append(f"batch_complete_skipped:{exc.code}")

    if not dry_run:
        session.flush()
    return report


__all__ = [
    "DEFAULT_MEMORY_NAMESPACE",
    "L2_MAX_FACTS",
    "L2_SOURCE_TYPE",
    "L2ItemResult",
    "L2MigrationError",
    "L2MigrationReport",
    "L2PackageMapping",
    "SYSTEM_L2_NAMESPACE_MAP",
    "backfill_l2",
    "facts_digest",
    "normalize_memory_namespace",
    "resolve_l2_package_mapping",
    "verify_l2",
]
