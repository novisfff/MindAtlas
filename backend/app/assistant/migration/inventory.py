"""Read-only inventory scan for AI runtime migration (Plan 10 Task 0)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.contracts import (
    InventoryItem,
    InventorySnapshot,
    L2NamespaceClass,
    MigrationItemState,
    SafeInventoryReport,
)
from app.assistant.skills.contracts import (
    RESERVED_SKILL_LOOKUP_NAMES,
    normalize_skill_lookup_name,
)

# Known system legacy skill names at Plan 09 tip (Task 0 baseline).
# Discovery still enumerates everything; unknown non-system names block.
KNOWN_SYSTEM_SKILL_NAMES: frozenset[str] = frozenset(
    {
        "general_chat",
        "quick_stats",
        "smart_capture",
        "periodic_review",
    }
)

# Related system workflow/asset keys retained for provenance classification.
KNOWN_SYSTEM_ASSET_KEYS: frozenset[str] = frozenset(
    {
        "smart_capture_relation_followup",
        "smart_capture_golden_create",
        "periodic_review_core",
        "context_capture",
        "weekly_report",
        "monthly_report",
    }
)

# Known migration map baseline (plan writing set). Custom skills are allowed
# when explicitly present as non-system; totally unknown new system-like names
# or unmapped enabled skills not in the baseline map become blockers when
# marked unknown by policy below.
KNOWN_MIGRATION_MAP_NAMES: frozenset[str] = frozenset(
    {
        *KNOWN_SYSTEM_SKILL_NAMES,
        *KNOWN_SYSTEM_ASSET_KEYS,
    }
)


def _stable_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _item_source_digest(
    *,
    subject_kind: str,
    source_id: str,
    source_name_normalized: str,
    extra: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "subject_kind": subject_kind,
        "source_id": source_id,
        "source_name_normalized": source_name_normalized,
    }
    if extra:
        for key, value in extra.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                payload[key] = value
            else:
                payload[key] = _stable_str(value)
    return sha256_canonical_json(payload)


def _normalize_name(raw: str) -> str:
    try:
        return normalize_skill_lookup_name(raw)
    except (TypeError, ValueError):
        # Fall back to a bounded casefold for inventory blockers only.
        return str(raw or "").casefold().strip()


def classify_l2_namespace(
    *,
    skill_package_id: str | None,
    memory_namespace: str | None,
) -> L2NamespaceClass:
    package_set = skill_package_id is not None and str(skill_package_id).strip() != ""
    ns = memory_namespace
    ns_set = ns is not None and str(ns).strip() != ""
    if not package_set and not ns_set:
        return "legacy_null_package"
    if package_set and ns_set:
        if str(ns).strip() == "default":
            return "native_default_namespace"
        return "native_custom_namespace"
    return "invalid_shape"


def _skill_state(record: Mapping[str, Any]) -> tuple[MigrationItemState, str | None]:
    name = _normalize_name(str(record.get("name") or ""))
    enabled = bool(record.get("enabled", True))
    is_system = bool(record.get("is_system", False))

    if name in RESERVED_SKILL_LOOKUP_NAMES or name == "general_chat":
        # general_chat migrates to Profile; still discovered, not a blocker.
        return "discovered", "general_chat_profile_target"

    if name in KNOWN_SYSTEM_SKILL_NAMES:
        return "discovered", None

    if name in KNOWN_SYSTEM_ASSET_KEYS:
        return "discovered", "system_asset_key"

    if not enabled:
        return "archived", "disabled_historical_source"

    # Enabled custom skills outside the known system set are discovered for
    # migration; names that look like unexpected new production system skills
    # (or are explicitly marked unknown by the adapter/policy) become blockers.
    if record.get("unknown") is True:
        return "blocked", "unknown_skill_source"

    if is_system and name not in KNOWN_MIGRATION_MAP_NAMES:
        return "blocked", "unknown_skill_source"

    return "discovered", None


def _scan_skills(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("skills") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        name = str(raw.get("name") or "")
        name_norm = _normalize_name(name)
        state, reason = _skill_state(raw)
        target_type = None
        target_id = None
        if raw.get("workflow_id"):
            target_type = "workflow"
            target_id = _stable_str(raw.get("workflow_id"))
        elif raw.get("agent_profile_id"):
            target_type = "agent_profile"
            target_id = _stable_str(raw.get("agent_profile_id"))
        digest = _item_source_digest(
            subject_kind="skill",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "enabled": bool(raw.get("enabled", True)),
                "is_system": bool(raw.get("is_system", False)),
                "target_type": target_type,
                "target_id": target_id,
            },
        )
        items.append(
            InventoryItem(
                subject_kind="skill",
                source_id=source_id,
                source_name=name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state=state,
                reason_code=reason,
                enabled=bool(raw.get("enabled", True)),
                is_system=bool(raw.get("is_system", False)),
                target_type=target_type,
                target_id=target_id,
            )
        )
    return items


def _scan_aliases(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("aliases") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        alias = str(raw.get("alias") or "")
        alias_norm = _normalize_name(alias)
        package_id = _stable_str(raw.get("package_id")) or None
        digest = _item_source_digest(
            subject_kind="alias",
            source_id=source_id,
            source_name_normalized=alias_norm,
            extra={
                "package_id": package_id,
                "alias_type": raw.get("alias_type"),
                "canonical_name": raw.get("canonical_name"),
            },
        )
        items.append(
            InventoryItem(
                subject_kind="alias",
                source_id=source_id,
                source_name=alias,
                source_name_normalized=alias_norm,
                source_digest=digest,
                state="discovered",
                target_type="package",
                target_id=package_id,
            )
        )
    return items


def _scan_packages(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("packages") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        name = str(raw.get("canonical_name") or "")
        name_norm = _normalize_name(name)
        published = _stable_str(raw.get("published_version_id")) or None
        digest = _item_source_digest(
            subject_kind="package",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "published_version_id": published,
                "catalog_enabled": bool(raw.get("catalog_enabled", False)),
                "migration_state": raw.get("migration_state"),
                "legacy_skill_id": _stable_str(raw.get("legacy_skill_id")) or None,
            },
        )
        items.append(
            InventoryItem(
                subject_kind="package",
                source_id=source_id,
                source_name=name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state="discovered",
                is_system=bool(raw.get("is_system", False)),
                target_type="skill_version",
                target_version_id=published,
                status=str(raw.get("migration_state") or "") or None,
            )
        )
    return items


def _scan_profiles(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("profiles") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        name = str(raw.get("profile_key") or "")
        name_norm = _normalize_name(name) if name else source_id
        published = _stable_str(raw.get("published_version_id")) or None
        digest = _item_source_digest(
            subject_kind="profile",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "published_version_id": published,
                "runtime_enabled": bool(raw.get("runtime_enabled", False)),
            },
        )
        items.append(
            InventoryItem(
                subject_kind="profile",
                source_id=source_id,
                source_name=name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state="discovered",
                enabled=bool(raw.get("runtime_enabled", False)),
                target_type="profile_version",
                target_version_id=published,
            )
        )
    return items


def _scan_l2(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("l2_rows") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        skill_name = str(raw.get("skill_name") or "")
        name_norm = _normalize_name(skill_name)
        package_id = _stable_str(raw.get("skill_package_id")) or None
        namespace = raw.get("memory_namespace")
        if namespace is not None:
            namespace = str(namespace)
            if namespace.strip() == "":
                namespace = None
        ns_class = classify_l2_namespace(
            skill_package_id=package_id,
            memory_namespace=namespace,
        )
        state: MigrationItemState = "discovered"
        reason = None
        if ns_class == "invalid_shape":
            state = "blocked"
            reason = "invalid_l2_namespace_shape"
        elif ns_class == "legacy_null_package":
            reason = "legacy_null_package_l2"
        digest = _item_source_digest(
            subject_kind="l2_memory",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "conversation_id": _stable_str(raw.get("conversation_id")),
                "skill_package_id": package_id,
                "memory_namespace": namespace,
                "namespace_class": ns_class,
            },
        )
        items.append(
            InventoryItem(
                subject_kind="l2_memory",
                source_id=source_id,
                source_name=skill_name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state=state,
                reason_code=reason,
                skill_package_id=package_id,
                memory_namespace=namespace,
                namespace_class=ns_class,
            )
        )
    return items


def _scan_approvals(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("approvals") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        status = str(raw.get("status") or "")
        channel = str(raw.get("channel_type") or "")
        name = f"{channel}:{status}"
        name_norm = _normalize_name(name) if name.strip(":") else source_id
        digest = _item_source_digest(
            subject_kind="approval",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "run_id": _stable_str(raw.get("run_id")),
                "status": status,
                "channel_type": channel,
                "skill_id": _stable_str(raw.get("skill_id")) or None,
                "node_id": raw.get("node_id"),
            },
        )
        items.append(
            InventoryItem(
                subject_kind="approval",
                source_id=source_id,
                source_name=name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state="discovered",
                status=status or None,
                channel_type=channel or None,
                target_id=_stable_str(raw.get("skill_id")) or None,
            )
        )
    return items


def _scan_entrypoints(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("entrypoints") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        name = str(raw.get("name") or source_id)
        name_norm = _normalize_name(name)
        runtime = str(raw.get("runtime") or "")
        digest = _item_source_digest(
            subject_kind="entrypoint",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "runtime": runtime,
                "supports_hitl": bool(raw.get("supports_hitl", False)),
            },
        )
        items.append(
            InventoryItem(
                subject_kind="entrypoint",
                source_id=source_id,
                source_name=name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state="discovered",
                runtime=runtime or None,
                supports_hitl=bool(raw.get("supports_hitl", False)),
            )
        )
    return items


def _scan_write_branches(records: Mapping[str, Any]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for raw in records.get("write_branches") or []:
        if not isinstance(raw, Mapping):
            continue
        source_id = _stable_str(raw.get("id"))
        skill_name = str(raw.get("skill_name") or "")
        branch = str(raw.get("branch") or "")
        name = f"{skill_name}:{branch}"
        name_norm = _normalize_name(name)
        has_evidence = bool(raw.get("plan08_evidence", False))
        supported = bool(raw.get("supported", True))
        state: MigrationItemState = "discovered"
        reason = None
        if supported and not has_evidence:
            state = "blocked"
            reason = "unsupported_or_unevidenced_write_branch"
        digest = _item_source_digest(
            subject_kind="write_branch",
            source_id=source_id,
            source_name_normalized=name_norm,
            extra={
                "skill_name": skill_name,
                "branch": branch,
                "supported": supported,
                "plan08_evidence": has_evidence,
            },
        )
        items.append(
            InventoryItem(
                subject_kind="write_branch",
                source_id=source_id,
                source_name=name,
                source_name_normalized=name_norm,
                source_digest=digest,
                state=state,
                reason_code=reason,
                branch=branch or None,
                plan08_evidence=has_evidence,
                enabled=supported,
            )
        )
    return items


def _count_by_kind(items: list[InventoryItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.subject_kind] = counts.get(item.subject_kind, 0) + 1
    counts["total"] = len(items)
    counts["blocked"] = sum(1 for i in items if i.state == "blocked")
    counts["archived"] = sum(1 for i in items if i.state == "archived")
    return counts


def digest_items_payload(items: tuple[InventoryItem, ...] | list[InventoryItem]) -> str:
    """Deterministic digest over safe item identity fields only."""
    ordered = sorted(
        items,
        key=lambda i: (i.subject_kind, i.source_name_normalized, i.source_id),
    )
    payload = [
        {
            "subject_kind": i.subject_kind,
            "source_id": i.source_id,
            "source_name_normalized": i.source_name_normalized,
            "source_digest": i.source_digest,
            "state": i.state,
            "reason_code": i.reason_code,
            "enabled": i.enabled,
            "is_system": i.is_system,
            "target_type": i.target_type,
            "target_id": i.target_id,
            "target_version_id": i.target_version_id,
            "skill_package_id": i.skill_package_id,
            "memory_namespace": i.memory_namespace,
            "namespace_class": i.namespace_class,
            "status": i.status,
            "channel_type": i.channel_type,
            "runtime": i.runtime,
            "branch": i.branch,
            "plan08_evidence": i.plan08_evidence,
        }
        for i in ordered
    ]
    return sha256_canonical_json(payload)


def scan_inventory_from_records(records: Mapping[str, Any]) -> InventorySnapshot:
    """Scan a sanitized records mapping (fixture or adapter projection).

    Never includes raw prompts, facts, approval payloads, or credentials.
    Unknown/new skills become blockers. L2 NULL package vs default namespace
    are classified distinctly.
    """
    items: list[InventoryItem] = []
    items.extend(_scan_skills(records))
    items.extend(_scan_aliases(records))
    items.extend(_scan_packages(records))
    items.extend(_scan_profiles(records))
    items.extend(_scan_l2(records))
    items.extend(_scan_approvals(records))
    items.extend(_scan_entrypoints(records))
    items.extend(_scan_write_branches(records))

    # Stable ordering for digests/reports.
    items.sort(key=lambda i: (i.subject_kind, i.source_name_normalized, i.source_id))
    item_tuple = tuple(items)
    counts = _count_by_kind(items)
    blocker_count = sum(1 for i in items if i.state == "blocked")
    snapshot_digest = digest_items_payload(item_tuple)

    return InventorySnapshot(
        environment=str(records.get("environment") or "unknown"),
        database_fingerprint=str(records.get("database_fingerprint") or "unknown"),
        schema_head=str(records.get("schema_head") or "unknown"),
        build_revision=str(records.get("build_revision") or "development"),
        items=item_tuple,
        counts=counts,
        blocker_count=blocker_count,
        snapshot_digest=snapshot_digest,
        known_system_skills=tuple(sorted(KNOWN_SYSTEM_SKILL_NAMES)),
        scanned_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _safe_item_dict(item: InventoryItem) -> dict[str, Any]:
    return {
        "subjectKind": item.subject_kind,
        "sourceId": item.source_id,
        "sourceNameNormalized": item.source_name_normalized,
        "sourceDigest": item.source_digest,
        "state": item.state,
        "reasonCode": item.reason_code,
        "enabled": item.enabled,
        "isSystem": item.is_system,
        "targetType": item.target_type,
        "targetId": item.target_id,
        "targetVersionId": item.target_version_id,
        "skillPackageId": item.skill_package_id,
        "memoryNamespace": item.memory_namespace,
        "namespaceClass": item.namespace_class,
        "status": item.status,
        "channelType": item.channel_type,
        "runtime": item.runtime,
        "supportsHitl": item.supports_hitl,
        "branch": item.branch,
        "plan08Evidence": item.plan08_evidence,
    }


def build_safe_inventory_report(
    snapshot: InventorySnapshot,
    *,
    dry_run: bool = True,
    request_id: str | None = None,
) -> SafeInventoryReport:
    """Project a snapshot into a CLI-safe report (no raw content)."""
    items = tuple(_safe_item_dict(i) for i in snapshot.items)
    blockers = tuple(
        _safe_item_dict(i) for i in snapshot.items if i.state == "blocked"
    )
    return SafeInventoryReport(
        ok=True,
        environment=snapshot.environment,
        database_fingerprint=snapshot.database_fingerprint,
        schema_head=snapshot.schema_head,
        build_revision=snapshot.build_revision,
        snapshot_digest=snapshot.snapshot_digest,
        counts=dict(snapshot.counts),
        blocker_count=snapshot.blocker_count,
        items=items,
        blockers=blockers,
        dry_run=dry_run,
        request_id=request_id,
    )


__all__ = (
    "KNOWN_SYSTEM_ASSET_KEYS",
    "KNOWN_SYSTEM_SKILL_NAMES",
    "build_safe_inventory_report",
    "classify_l2_namespace",
    "digest_items_payload",
    "scan_inventory_from_records",
)
