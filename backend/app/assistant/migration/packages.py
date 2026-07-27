"""Plan 10 Task 2 — migrate legacy Skills to native packages / Main Agent Profile.

Deterministic source adapters convert only portable instructions/examples/resources
and exact published target refs. Credentials and mutable runtime secrets are rejected.
Native package/Profile bytes are produced through Plan 09 services; migration evidence
is recorded via RuntimeMigrationRepository item transitions. Catalog/traffic remains
off (cutover lock only); independent verify is a separate pass.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.repository import (
    CODE_FORBIDDEN_TRANSITION,
    CODE_STALE_REVISION,
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)
from app.assistant.skills.contracts import (
    ParsedSkillPackage,
    is_reserved_skill_lookup_name,
    normalize_skill_lookup_name,
)
from app.assistant.migration.legacy_names import (
    GENERAL_CHAT_NAME,
    _bridge_source_ref,
    _load_published_agent_snapshot,
    _main_agent_snapshot_from_agent,
    _occupied_canonical_names,
    _validated_control_tool_keys,
    legacy_skill_canonical_name,
    legacy_source_digest,
    render_legacy_skill_package,
    resolve_legacy_target_ref,
)
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
    AssistantSkillVersion,
)
from app.assistant.skills.schemas import (
    CreateSkillPackageCommand,
    PublishMainAgentProfileCommand,
    PublishSkillVersionCommand,
    SaveMainAgentProfileDraftCommand,
    SaveSkillDraftCommand,
)
from app.assistant.skills.service import AgentSkillService, MainAgentProfileService
from app.common.exceptions import ApiException

logger = logging.getLogger(__name__)

# Locked system migration order (profile first, then packages).
SYSTEM_PACKAGE_MIGRATION_ORDER: tuple[str, ...] = (
    "general_chat",
    "quick_stats",
    "periodic_review",
    "smart_capture",
)

_SECRET_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "secret",
    "credential",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "private_key",
    "client_secret",
    "fernet",
    "bearer",
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9]{16,}|api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+|"
    r"bearer\s+[a-z0-9\-._~+/]+=*|-----BEGIN (?:RSA )?PRIVATE KEY-----)"
)

WriteBranchAction = Literal["migrate", "block", "archive"]
SourceKind = Literal["profile", "package", "archive", "block"]


class PackageMigrationError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True, slots=True)
class SourceClassification:
    kind: SourceKind
    reason_code: str | None = None
    normalized_name: str = ""


@dataclass(frozen=True, slots=True)
class WriteBranchDecision:
    action: WriteBranchAction
    reason_code: str


@dataclass(frozen=True, slots=True)
class PortableLegacySource:
    """Secret-free portable source view used for rendering and digests."""

    source_id: str
    source_name: str
    source_name_normalized: str
    description: str
    intent_examples: tuple[str, ...]
    is_system: bool
    enabled: bool
    target_type: Literal["workflow", "agent"] | None
    target_id: str | None
    # Exact published target name (not UUID) for capability binding.
    target_name: str | None = None


@dataclass
class PackageMigrationItemResult:
    source_id: str
    source_name_normalized: str
    subject_kind: str
    outcome: str
    state: str
    reason_code: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    target_version: str | None = None
    target_digest: str | None = None
    migration_item_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "sourceNameNormalized": self.source_name_normalized,
            "subjectKind": self.subject_kind,
            "outcome": self.outcome,
            "state": self.state,
            "reasonCode": self.reason_code,
            "targetType": self.target_type,
            "targetId": self.target_id,
            "targetVersion": self.target_version,
            "targetDigest": self.target_digest,
            "migrationItemId": self.migration_item_id,
        }


@dataclass
class PackageMigrationReport:
    command: str
    dry_run: bool
    processed: int = 0
    succeeded: int = 0
    blocked: int = 0
    failed: int = 0
    archived: int = 0
    unchanged: int = 0
    batch_id: str | None = None
    report_digest: str | None = None
    request_id: str | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.failed == 0,
            "command": self.command,
            "dryRun": self.dry_run,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "blocked": self.blocked,
            "failed": self.failed,
            "archived": self.archived,
            "unchanged": self.unchanged,
            "batchId": self.batch_id,
            "reportDigest": self.report_digest,
            "requestId": self.request_id,
            "items": list(self.items),
            "steps": list(self.steps),
        }


def classify_legacy_source(record: Mapping[str, Any]) -> SourceClassification:
    name_raw = str(record.get("name") or "")
    try:
        name = normalize_skill_lookup_name(name_raw)
    except (TypeError, ValueError):
        name = name_raw.casefold().strip()
    enabled = bool(record.get("enabled", True))
    if is_reserved_skill_lookup_name(name_raw) or name == "general_chat":
        return SourceClassification(
            kind="profile",
            reason_code="general_chat_profile_target",
            normalized_name=name or "general_chat",
        )
    if not enabled:
        return SourceClassification(
            kind="archive",
            reason_code="disabled_historical_source",
            normalized_name=name,
        )
    if record.get("unknown") is True:
        return SourceClassification(
            kind="block",
            reason_code="unknown_skill_source",
            normalized_name=name,
        )
    return SourceClassification(kind="package", reason_code=None, normalized_name=name)


def decide_write_branch_action(
    *,
    skill_name: str,
    branch: str,
    supported: bool,
    plan08_evidence: bool,
) -> WriteBranchDecision:
    """Never silent-drop write branches.

    Only create_entry with Plan 08 evidence migrates. Other supported write
    branches block (even when plan08_evidence is true); unsupported branches
    archive explicitly.
    """
    del skill_name  # reserved for future per-skill policy
    branch_norm = str(branch or "").strip().casefold()
    if not supported:
        return WriteBranchDecision(action="archive", reason_code="write_branch_unsupported")
    if branch_norm == "create_entry" and plan08_evidence:
        return WriteBranchDecision(action="migrate", reason_code="plan08_create_entry")
    # Supported non-create_entry (even with Plan 08 evidence) → block, never migrate.
    if branch_norm != "create_entry":
        return WriteBranchDecision(
            action="block",
            reason_code="non_create_write_branch",
        )
    # create_entry without Plan 08 evidence → block (must not disappear silently).
    return WriteBranchDecision(
        action="block",
        reason_code="unsupported_or_unevidenced_write_branch",
    )


def _walk_for_secrets(value: Any, *, path: str = "root") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_s = str(key)
            lower = key_s.casefold()
            if any(m in lower for m in _SECRET_KEY_MARKERS):
                return f"{path}.{key_s}"
            found = _walk_for_secrets(item, path=f"{path}.{key_s}")
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            found = _walk_for_secrets(item, path=f"{path}[{idx}]")
            if found:
                return found
    elif isinstance(value, str):
        if _SECRET_VALUE_RE.search(value):
            return path
    return None


def _reject_secrets_in_mapping(record: Mapping[str, Any]) -> None:
    """Raise PackageMigrationError if portable fields carry credential-like data."""
    for field_name in ("tools", "kb_config", "config", "metadata", "credentials"):
        if field_name not in record:
            continue
        found = _walk_for_secrets(record.get(field_name), path=field_name)
        if found:
            raise PackageMigrationError(
                "secret_or_credential_rejected",
                f"legacy source contains credential-like field at {found}",
            )


def reject_secrets_in_legacy_skill(skill: Any) -> None:
    """Reject credentials/mutable secrets on an ORM legacy skill before publish."""
    record: dict[str, Any] = {}
    if skill.tools is not None:
        record["tools"] = skill.tools
    if skill.kb_config is not None:
        record["kb_config"] = skill.kb_config
    _reject_secrets_in_mapping(record)


def portable_source_from_legacy_record(
    record: Mapping[str, Any],
) -> PortableLegacySource:
    """Build a secret-free portable source; reject credentials/mutable secrets."""
    classification = classify_legacy_source(record)
    if classification.kind == "profile":
        raise PackageMigrationError(
            "general_chat_not_a_skill_package",
            "general_chat is Main Agent Profile provenance, not a Skill package",
        )

    # Reject secrets anywhere in portable fields (tools/kb_config/metadata/etc.).
    _reject_secrets_in_mapping(record)
    # System prompt is not a package secret but must not be copied into package
    # evidence; adapters only keep description/examples for package skills.
    source_id = str(record.get("id") or "")
    name = str(record.get("name") or "")
    try:
        name_norm = normalize_skill_lookup_name(name)
    except (TypeError, ValueError):
        name_norm = name.casefold().strip()
    examples: list[str] = []
    raw_examples = record.get("intent_examples") or record.get("intentExamples") or []
    if isinstance(raw_examples, list):
        for item in raw_examples:
            if isinstance(item, str) and item.strip():
                examples.append(item.strip()[:1000])
            if len(examples) >= 100:
                break

    target_type: Literal["workflow", "agent"] | None = None
    target_id: str | None = None
    if record.get("workflow_id"):
        target_type = "workflow"
        target_id = str(record.get("workflow_id"))
    elif record.get("agent_profile_id"):
        target_type = "agent"
        target_id = str(record.get("agent_profile_id"))

    return PortableLegacySource(
        source_id=source_id,
        source_name=name,
        source_name_normalized=name_norm,
        description=str(record.get("description") or "")[:1024],
        intent_examples=tuple(examples),
        is_system=bool(record.get("is_system", False)),
        enabled=bool(record.get("enabled", True)),
        target_type=target_type,
        target_id=target_id,
        target_name=str(record.get("target_name") or "") or None,
    )


def _source_type_skill() -> str:
    return "legacy_skill"


def _source_type_write_branch() -> str:
    return "legacy_write_branch"


def _skill_order_key(skill: Any) -> tuple[int, str, str]:
    try:
        name = normalize_skill_lookup_name(str(skill.name or ""))
    except (TypeError, ValueError):
        name = str(skill.name or "").casefold()
    if name in SYSTEM_PACKAGE_MIGRATION_ORDER:
        return (0, f"{SYSTEM_PACKAGE_MIGRATION_ORDER.index(name):02d}", name)
    # Enabled customs after system; disabled last.
    if not bool(skill.enabled):
        return (2, name, str(skill.id))
    return (1, name, str(skill.id))


def _ensure_discovered_skill_item(
    repo: RuntimeMigrationRepository,
    skill: Any,
    *,
    source_digest: str,
    actor_principal: str | None,
    build_revision: str | None,
    reason_code: str | None = None,
) -> Any:
    try:
        name_norm = normalize_skill_lookup_name(str(skill.name or ""))
    except (TypeError, ValueError):
        name_norm = str(skill.name or "").casefold()
    item, _outcome = repo.upsert_discovered_item(
        subject_kind="skill",
        source_type=_source_type_skill(),
        source_id=str(skill.id),
        source_name=str(skill.name or ""),
        source_name_normalized=name_norm,
        source_digest=source_digest,
        evidence_json={
            "enabled": bool(skill.enabled),
            "isSystem": bool(skill.is_system),
        },
        actor_principal=actor_principal,
        build_revision=build_revision,
        reason_code=reason_code,
    )
    return item


def _transition(
    repo: RuntimeMigrationRepository,
    item: Any,
    *,
    to_state: str,
    reason_code: str | None = None,
    evidence_json: Mapping[str, Any] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_version: str | None = None,
    target_digest: str | None = None,
    actor_principal: str | None = None,
    build_revision: str | None = None,
) -> Any:
    current = str(item.state)
    if current == to_state:
        return item
    # Walk legal multi-step path when needed (discovered→mapped→migrated→verified).
    path_order = ["discovered", "mapped", "migrated", "verified"]
    if current in path_order and to_state in path_order:
        start = path_order.index(current)
        end = path_order.index(to_state)
        if end > start:
            for nxt in path_order[start + 1 : end + 1]:
                item = repo.transition_item(
                    item_id=item.id,
                    expected_revision=int(item.state_revision),
                    to_state=nxt,
                    reason_code=reason_code if nxt == to_state else None,
                    evidence_json=evidence_json if nxt == to_state else None,
                    target_type=target_type if nxt in {"mapped", "migrated", "verified"} else None,
                    target_id=target_id if nxt in {"mapped", "migrated", "verified"} else None,
                    target_version=(
                        target_version if nxt in {"migrated", "verified"} else None
                    ),
                    target_digest=(
                        target_digest if nxt in {"migrated", "verified"} else None
                    ),
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
            return item
    return repo.transition_item(
        item_id=item.id,
        expected_revision=int(item.state_revision),
        to_state=to_state,
        reason_code=reason_code,
        evidence_json=evidence_json,
        target_type=target_type,
        target_id=target_id,
        target_version=target_version,
        target_digest=target_digest,
        actor_principal=actor_principal,
        build_revision=build_revision,
    )


def _block_item(
    repo: RuntimeMigrationRepository,
    item: Any,
    *,
    reason_code: str,
    actor_principal: str | None,
    build_revision: str | None,
    evidence_json: Mapping[str, Any] | None = None,
) -> Any:
    if str(item.state) == "blocked":
        return item
    if str(item.state) == "archived":
        # archived → blocked only if later inventory proves active; leave archived.
        return item
    return repo.transition_item(
        item_id=item.id,
        expected_revision=int(item.state_revision),
        to_state="blocked",
        reason_code=reason_code,
        evidence_json=evidence_json or {"reasonCode": reason_code},
        actor_principal=actor_principal,
        build_revision=build_revision,
    )


def _archive_item(
    repo: RuntimeMigrationRepository,
    item: Any,
    *,
    reason_code: str,
    actor_principal: str | None,
    build_revision: str | None,
) -> Any:
    if str(item.state) == "archived":
        return item
    return repo.transition_item(
        item_id=item.id,
        expected_revision=int(item.state_revision),
        to_state="archived",
        reason_code=reason_code,
        evidence_json={"reasonCode": reason_code},
        actor_principal=actor_principal,
        build_revision=build_revision,
    )


def _lock_package_cutover(
    session: Session,
    package: AssistantSkillPackage,
    *,
    actor_principal: str | None,
    request_id: str,
) -> AssistantSkillPackage:
    """Mark package cutover without enabling catalog traffic."""
    # Prefer service path when disabled (no gate required).
    if str(package.migration_state or "") == "cutover":
        return package
    svc = AgentSkillService(session)
    # set_catalog_enabled(enabled=False) allows migration_state without gate.
    summary = svc.set_catalog_enabled(
        package.id,
        enabled=False,
        migration_state="cutover",
        request_id=_short_request_id("mig-cutover", package.id),
        actor_principal=actor_principal or "system:package-migration",
    )
    refreshed = session.get(AssistantSkillPackage, _as_uuid(summary.id) or package.id)
    if refreshed is None:
        raise PackageMigrationError("package_missing_after_cutover", "package missing after cutover")
    # Ensure legacy_skill linkage survives.
    return refreshed


def _lock_profile_cutover(
    session: Session,
    profile: AssistantMainAgentProfile,
    *,
    actor_principal: str | None,
    request_id: str,
) -> AssistantMainAgentProfile:
    if str(profile.migration_state or "") == "cutover":
        return profile
    svc = MainAgentProfileService(session)
    rev = int(getattr(profile, "aggregate_revision", 0) or 0)
    summary = svc.set_runtime_enabled(
        profile.id,
        enabled=False,
        migration_state="cutover",
        request_id=_short_request_id("mig-pcut", profile.id, rev),
        expected_aggregate_revision=rev,
        actor_principal=actor_principal or "system:package-migration",
    )
    refreshed = session.get(
        AssistantMainAgentProfile, _as_uuid(summary.id) or profile.id
    )
    if refreshed is None:
        raise PackageMigrationError("profile_missing_after_cutover", "profile missing after cutover")
    return refreshed


def _find_package_for_skill(
    session: Session, skill: Any
) -> AssistantSkillPackage | None:
    by_legacy = (
        session.query(AssistantSkillPackage)
        .one_or_none()
    )
    if by_legacy is not None:
        return by_legacy
    try:
        canonical = legacy_skill_canonical_name(skill)
    except ValueError:
        return None
    return (
        session.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.canonical_name == canonical)
        .one_or_none()
    )


def _short_request_id(*parts: Any) -> str:
    """Build a CAS request_id within the 128-char service limit."""
    raw = ":".join(str(p) for p in parts if p is not None)
    if len(raw) <= 128:
        return raw
    digest = sha256_canonical_json({"request": raw})[:24]
    head = raw[:90].rstrip(":")
    return f"{head}:{digest}"[:128]


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _publish_package_from_parsed(
    session: Session,
    *,
    skill: Any,
    parsed: ParsedSkillPackage,
    package: AssistantSkillPackage | None,
    request_id: str,
    actor_principal: str | None,
) -> tuple[AssistantSkillPackage, AssistantSkillVersion]:
    svc = AgentSkillService(session)
    if package is None:
        detail = svc.create_native_package(
            CreateSkillPackageCommand(
                parsed=parsed,
                version_name=f"migration-draft-1",
                origin="api",
            )
        )
        package = session.get(AssistantSkillPackage, _as_uuid(detail.id))
        if package is None:
            raise PackageMigrationError("package_create_failed", "native package missing after create")
        package.is_system = bool(skill.is_system)
        session.flush()
    else:
        # Promote shadow → native via api draft if needed.
        rev = int(getattr(package, "aggregate_revision", 0) or 0)
        draft = svc.save_draft(
            SaveSkillDraftCommand(
                package_id=package.id,
                parsed=parsed,
                version_name="migration-draft",
                origin="api",
                expected_aggregate_revision=rev,
                request_id=_short_request_id("mig-draft", package.id, rev, request_id),
            )
        )
        package = session.get(AssistantSkillPackage, _as_uuid(package.id))
        if package is None:
            raise PackageMigrationError("package_missing", "package missing after draft")
        draft_id = _as_uuid(getattr(draft, "id", None))
        if package.draft_version_id is None and draft_id is not None:
            package.draft_version_id = draft_id
        session.flush()

    if package.draft_version_id is None:
        raise PackageMigrationError("draft_missing", "package has no draft to publish")

    # Idempotent: if published already matches draft content, skip publish.
    draft_row = session.get(
        AssistantSkillVersion, _as_uuid(package.draft_version_id)
    )
    if (
        package.published_version_id is not None
        and draft_row is not None
        and str(package.migration_state) in {"native", "cutover"}
    ):
        pub_row = session.get(
            AssistantSkillVersion, _as_uuid(package.published_version_id)
        )
        if (
            pub_row is not None
            and pub_row.content_digest
            and draft_row.content_digest == pub_row.content_digest
        ):
            return package, pub_row

    rev = int(getattr(package, "aggregate_revision", 0) or 0)
    published = svc.publish(
        package.id,
        PublishSkillVersionCommand(
            draft_version_id=_as_uuid(package.draft_version_id) or package.draft_version_id,
            request_id=_short_request_id(
                "mig-pub", package.id, package.draft_version_id, rev
            ),
            expected_aggregate_revision=rev,
        ),
        actor_principal=actor_principal or "system:package-migration",
    )
    package = session.get(AssistantSkillPackage, _as_uuid(package.id))
    if package is None or package.published_version_id is None:
        raise PackageMigrationError("publish_failed", "package missing published pointer")
    version = session.get(
        AssistantSkillVersion, _as_uuid(package.published_version_id)
    )
    if version is None:
        raise PackageMigrationError("publish_version_missing", "published version row missing")
    # Source digests stay in migration item evidence only (no package column stamp).
    # published summary may lag; use version row.
    del published
    return package, version


def _migrate_general_chat_profile(
    session: Session,
    skill: Any,
    *,
    request_id: str,
    actor_principal: str | None,
    build_revision: str | None,
    dry_run: bool,
    repo: RuntimeMigrationRepository,
) -> PackageMigrationItemResult:
    target_ref = resolve_legacy_target_ref(session, skill)
    if skill.agent_profile_id is None or target_ref is None:
        source_digest = legacy_source_digest(skill, None)
        item = _ensure_discovered_skill_item(
            repo,
            skill,
            source_digest=source_digest,
            actor_principal=actor_principal,
            build_revision=build_revision,
            reason_code="general_chat_profile_target",
        )
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="target_missing",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="target_missing",
            migration_item_id=str(item.id),
        )

    loaded = _load_published_agent_snapshot(session, skill.agent_profile_id)
    if loaded is None:
        source_digest = legacy_source_digest(skill, target_ref)
        item = _ensure_discovered_skill_item(
            repo,
            skill,
            source_digest=source_digest,
            actor_principal=actor_principal,
            build_revision=build_revision,
            reason_code="general_chat_profile_target",
        )
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="target_unpublished",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="target_unpublished",
            migration_item_id=str(item.id),
        )

    agent, version, snapshot = loaded
    validated_tools = _validated_control_tool_keys(snapshot.get("tools"))
    source_ref = _bridge_source_ref(
        skill=skill,
        agent=agent,
        version=version,
        snapshot=snapshot,
        validated_tools=validated_tools,
    )
    bridge_digest = sha256_canonical_json(
        {
            "kind": "general_chat_main_agent_bridge",
            "sourceRef": source_ref,
            "basePromptDigest": sha256_canonical_json(
                {"basePrompt": snapshot.get("system_prompt")}
            ),
        }
    )
    item = _ensure_discovered_skill_item(
        repo,
        skill,
        source_digest=bridge_digest,
        actor_principal=actor_principal,
        build_revision=build_revision,
        reason_code="general_chat_profile_target",
    )

    if dry_run:
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="projected",
            state=str(item.state),
            reason_code="general_chat_profile_target",
            target_type="main_agent_profile",
            migration_item_id=str(item.id),
        )

    # Never create a general-chat skill package.
    existing_pkg = (
        session.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.canonical_name.in_(("general-chat", "general_chat")))
        .first()
    )
    if existing_pkg is not None:
        item = _block_item(
            repo,
            item,
            reason_code="reserved_package_exists",
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="blocked",
            state="blocked",
            reason_code="reserved_package_exists",
            migration_item_id=str(item.id),
        )

    profile_svc = MainAgentProfileService(session)
    summary = profile_svc.ensure_default()
    profile = session.get(AssistantMainAgentProfile, summary.id)
    if profile is None:
        item = _block_item(
            repo,
            item,
            reason_code="main_agent_missing",
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="blocked",
            state="blocked",
            reason_code="main_agent_missing",
            migration_item_id=str(item.id),
        )

    # Idempotent cutover short-circuit (state + published pointer only;
    # legacy_source_digest no longer persisted on the profile row).
    if str(profile.migration_state) == "cutover" and profile.published_version_id is not None:
        pub = session.get(AssistantMainAgentProfileVersion, profile.published_version_id)
        target_digest = str(pub.content_digest) if pub is not None else bridge_digest
        if str(item.state) not in {"migrated", "verified"}:
            item = _transition(
                repo,
                item,
                to_state="migrated",
                reason_code="general_chat_profile_migrated",
                target_type="main_agent_profile",
                target_id=str(profile.id),
                target_version=str(profile.published_version_id),
                target_digest=target_digest,
                evidence_json={
                    "canonicalName": "default",
                    "migrationState": "cutover",
                    "sourceDigest": bridge_digest,
                },
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="unchanged",
            state=str(item.state),
            reason_code="general_chat_profile_migrated",
            target_type="main_agent_profile",
            target_id=str(profile.id),
            target_version=str(profile.published_version_id),
            target_digest=target_digest,
            migration_item_id=str(item.id),
        )

    # Native ownership — lock cutover without rewrite; digests live on migration items.
    if str(profile.migration_state) == "native":
        profile = _lock_profile_cutover(
            session,
            profile,
            actor_principal=actor_principal,
            request_id=request_id,
        )
        pub = (
            session.get(AssistantMainAgentProfileVersion, profile.published_version_id)
            if profile.published_version_id
            else None
        )
        target_digest = (
            str(pub.content_digest)
            if pub is not None and pub.content_digest
            else bridge_digest
        )
        item = _transition(
            repo,
            item,
            to_state="migrated",
            reason_code="general_chat_profile_owned",
            target_type="main_agent_profile",
            target_id=str(profile.id),
            target_version=str(profile.published_version_id) if profile.published_version_id else None,
            target_digest=target_digest,
            evidence_json={"sourceDigest": bridge_digest},
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="migrated",
            state=str(item.state),
            reason_code="general_chat_profile_owned",
            target_type="main_agent_profile",
            target_id=str(profile.id),
            target_version=str(profile.published_version_id) if profile.published_version_id else None,
            target_digest=target_digest,
            migration_item_id=str(item.id),
        )

    # Reject credentials/secrets before Plan 09 profile draft/publish.
    try:
        reject_secrets_in_legacy_skill(skill)
    except PackageMigrationError as exc:
        item = _block_item(
            repo,
            item,
            reason_code=exc.reason_code,
            actor_principal=actor_principal,
            build_revision=build_revision,
            evidence_json={"errorType": "PackageMigrationError"},
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized="general_chat",
            subject_kind="skill",
            outcome="blocked",
            state="blocked",
            reason_code=exc.reason_code,
            migration_item_id=str(item.id),
        )

    main_snapshot = _main_agent_snapshot_from_agent(snapshot)
    current_rev = int(getattr(profile, "aggregate_revision", 0) or 0)
    draft = profile_svc.save_draft(
        profile.id,
        SaveMainAgentProfileDraftCommand(
            snapshot=main_snapshot,
            version_name="migration-general-chat",
            origin="api",
            source_ref=source_ref,
            expected_aggregate_revision=current_rev,
            request_id=_short_request_id(
                "mig-pdraft",
                skill.id,
                main_snapshot.content_digest()[:16],
                current_rev,
            ),
        ),
    )
    profile = session.get(AssistantMainAgentProfile, profile.id)
    assert profile is not None
    session.flush()

    rev = int(getattr(profile, "aggregate_revision", 0) or 0)
    published = profile_svc.publish(
        profile.id,
        PublishMainAgentProfileCommand(
            draft_version_id=draft.id,
            request_id=_short_request_id("mig-ppub", skill.id, draft.id, rev),
            expected_aggregate_revision=rev,
        ),
        actor_principal=actor_principal or "system:package-migration",
    )
    profile = session.get(AssistantMainAgentProfile, profile.id)
    assert profile is not None
    session.flush()

    profile = _lock_profile_cutover(
        session,
        profile,
        actor_principal=actor_principal,
        request_id=request_id,
    )
    target_digest = str(published.content_digest or bridge_digest)
    item = _transition(
        repo,
        item,
        to_state="migrated",
        reason_code="general_chat_profile_migrated",
        target_type="main_agent_profile",
        target_id=str(profile.id),
        target_version=str(profile.published_version_id),
        target_digest=target_digest,
        evidence_json={
            "canonicalName": "default",
            "migrationState": str(profile.migration_state),
            "runtimeEnabled": bool(profile.runtime_enabled),
            "sourceDigest": bridge_digest,
        },
        actor_principal=actor_principal,
        build_revision=build_revision,
    )
    return PackageMigrationItemResult(
        source_id=str(skill.id),
        source_name_normalized="general_chat",
        subject_kind="skill",
        outcome="migrated",
        state=str(item.state),
        reason_code="general_chat_profile_migrated",
        target_type="main_agent_profile",
        target_id=str(profile.id),
        target_version=str(profile.published_version_id),
        target_digest=target_digest,
        migration_item_id=str(item.id),
    )


def _migrate_package_skill(
    session: Session,
    skill: Any,
    *,
    request_id: str,
    actor_principal: str | None,
    build_revision: str | None,
    dry_run: bool,
    repo: RuntimeMigrationRepository,
) -> PackageMigrationItemResult:
    try:
        name_norm = normalize_skill_lookup_name(str(skill.name or ""))
    except (TypeError, ValueError):
        name_norm = str(skill.name or "").casefold()

    if not bool(skill.enabled):
        source_digest = legacy_source_digest(skill, resolve_legacy_target_ref(session, skill))
        item = _ensure_discovered_skill_item(
            repo,
            skill,
            source_digest=source_digest,
            actor_principal=actor_principal,
            build_revision=build_revision,
            reason_code="disabled_historical_source",
        )
        if not dry_run:
            item = _archive_item(
                repo,
                item,
                reason_code="disabled_historical_source",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="archived",
            state=str(item.state),
            reason_code="disabled_historical_source",
            migration_item_id=str(item.id),
        )

    target_ref = resolve_legacy_target_ref(session, skill)
    source_digest = legacy_source_digest(skill, target_ref)
    item = _ensure_discovered_skill_item(
        repo,
        skill,
        source_digest=source_digest,
        actor_principal=actor_principal,
        build_revision=build_revision,
    )

    if target_ref is None or not target_ref.target_name:
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="target_missing",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="target_missing",
            migration_item_id=str(item.id),
        )
    if target_ref.published_version_id is None:
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="target_unpublished",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="target_unpublished",
            migration_item_id=str(item.id),
        )

    existing = _find_package_for_skill(session, skill)
    occupied = _occupied_canonical_names(session)
    if existing is not None:
        occupied.discard(existing.canonical_name)
        # Existing cutover package for this skill — idempotent.
        if (
            str(existing.migration_state) == "cutover"
            and existing.published_version_id is not None
        ):
            pub = session.get(AssistantSkillVersion, existing.published_version_id)
            target_digest = (
                str(pub.version_digest or pub.content_digest)
                if pub is not None
                else source_digest
            )
            if not dry_run and str(item.state) not in {"migrated", "verified"}:
                item = _transition(
                    repo,
                    item,
                    to_state="migrated",
                    reason_code="package_already_cutover",
                    target_type="skill_package",
                    target_id=str(existing.id),
                    target_version=str(existing.published_version_id),
                    target_digest=target_digest if len(target_digest) == 64 else source_digest,
                    evidence_json={
                        "canonicalName": existing.canonical_name,
                        "migrationState": "cutover",
                        "catalogEnabled": bool(existing.catalog_enabled),
                    },
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="unchanged",
                state=str(item.state) if dry_run else str(item.state),
                reason_code="package_already_cutover",
                target_type="skill_package",
                target_id=str(existing.id),
                target_version=str(existing.published_version_id),
                target_digest=target_digest if len(str(target_digest)) == 64 else source_digest,
                migration_item_id=str(item.id),
            )

        # Includes native/cutover packages that already occupy the name/alias.
        if str(existing.migration_state) in {
            "native",
            "cutover",
        }:
            if not dry_run:
                item = _block_item(
                    repo,
                    item,
                    reason_code="canonical_name_collision",
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                    evidence_json={
                        "canonicalName": existing.canonical_name,
                        "packageId": str(existing.id),
                    },
                )
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="blocked",
                state=str(item.state),
                reason_code="canonical_name_collision",
                migration_item_id=str(item.id),
            )

        # alias ownership-by-legacy-skill-id check removed with column drop.

    # Reject credentials/secrets before render / Plan 09 publish.
    try:
        reject_secrets_in_legacy_skill(skill)
    except PackageMigrationError as exc:
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code=exc.reason_code,
                actor_principal=actor_principal,
                build_revision=build_revision,
                evidence_json={"errorType": "PackageMigrationError"},
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code=exc.reason_code,
            migration_item_id=str(item.id),
        )

    try:
        canonical = (
            existing.canonical_name
            if existing is not None
            else legacy_skill_canonical_name(skill, occupied=occupied)
        )
        parsed = render_legacy_skill_package(
            skill,
            target_ref=target_ref,
            canonical_name=canonical,
        )
    except Exception as exc:  # noqa: BLE001
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="render_failed",
                actor_principal=actor_principal,
                build_revision=build_revision,
                evidence_json={"errorType": type(exc).__name__},
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="render_failed",
            migration_item_id=str(item.id),
        )

    # Alias collision: if legacy alias is owned by a different package, block.
    try:
        legacy_alias_norm = normalize_skill_lookup_name(str(skill.name or ""))
    except (TypeError, ValueError):
        legacy_alias_norm = None
    if legacy_alias_norm and existing is None:
        alias_owner = (
            session.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.normalized_alias == legacy_alias_norm)
            .one_or_none()
        )
        if alias_owner is not None:
            if not dry_run:
                item = _block_item(
                    repo,
                    item,
                    reason_code="alias_collision",
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                    evidence_json={
                        "alias": legacy_alias_norm,
                        "ownerPackageId": str(alias_owner.skill_package_id),
                    },
                )
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="blocked",
                state=str(item.state),
                reason_code="alias_collision",
                migration_item_id=str(item.id),
            )

    if dry_run:
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="projected",
            state=str(item.state),
            target_type="skill_package",
            migration_item_id=str(item.id),
        )

    try:
        item = _transition(
            repo,
            item,
            to_state="mapped",
            reason_code="mapped_to_native_package",
            target_type="skill_package",
            target_id=str(existing.id) if existing is not None else None,
            evidence_json={
                "canonicalName": parsed.canonical_name,
                "targetName": target_ref.target_name,
                "targetType": target_ref.target_type,
            },
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        package, version = _publish_package_from_parsed(
            session,
            skill=skill,
            parsed=parsed,
            package=existing,
            request_id=request_id,
            actor_principal=actor_principal,
        )
        package = _lock_package_cutover(
            session,
            package,
            actor_principal=actor_principal,
            request_id=request_id,
        )
        target_digest = str(version.version_digest or version.content_digest or source_digest)
        if len(target_digest) != 64:
            target_digest = source_digest
        item = _transition(
            repo,
            item,
            to_state="migrated",
            reason_code="package_migrated",
            target_type="skill_package",
            target_id=str(package.id),
            target_version=str(version.id),
            target_digest=target_digest,
            evidence_json={
                "canonicalName": package.canonical_name,
                "migrationState": str(package.migration_state),
                "catalogEnabled": bool(package.catalog_enabled),
                "contentDigest": str(version.content_digest or "")[:64],
            },
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="migrated",
            state=str(item.state),
            reason_code="package_migrated",
            target_type="skill_package",
            target_id=str(package.id),
            target_version=str(version.id),
            target_digest=target_digest,
            migration_item_id=str(item.id),
        )
    except PackageMigrationError as exc:
        item = _block_item(
            repo,
            item,
            reason_code=exc.reason_code,
            actor_principal=actor_principal,
            build_revision=build_revision,
            evidence_json={"errorType": "PackageMigrationError"},
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state="blocked",
            reason_code=exc.reason_code,
            migration_item_id=str(item.id),
        )
    except ApiException as exc:
        reason = "publish_failed"
        details = getattr(exc, "details", None) or {}
        if isinstance(details, dict) and details.get("reason"):
            reason = str(details.get("reason"))
        item = _block_item(
            repo,
            item,
            reason_code=reason[:64],
            actor_principal=actor_principal,
            build_revision=build_revision,
            evidence_json={"errorType": "ApiException", "statusCode": exc.status_code},
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state="blocked",
            reason_code=reason[:64],
            migration_item_id=str(item.id),
        )
    except RuntimeMigrationRepositoryError as exc:
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="failed",
            state=str(item.state),
            reason_code=exc.code,
            migration_item_id=str(item.id),
        )


def _migrate_write_branch(
    session: Session,
    branch_record: Mapping[str, Any],
    *,
    actor_principal: str | None,
    build_revision: str | None,
    dry_run: bool,
    repo: RuntimeMigrationRepository,
) -> PackageMigrationItemResult:
    del session
    source_id = str(branch_record.get("id") or "")
    skill_name = str(branch_record.get("skill_name") or "")
    branch = str(branch_record.get("branch") or "")
    name = f"{skill_name}:{branch}"
    try:
        name_norm = normalize_skill_lookup_name(name)
    except (TypeError, ValueError):
        name_norm = name.casefold()
    decision = decide_write_branch_action(
        skill_name=skill_name,
        branch=branch,
        supported=bool(branch_record.get("supported", True)),
        plan08_evidence=bool(branch_record.get("plan08_evidence", False)),
    )
    digest = sha256_canonical_json(
        {
            "subject_kind": "write_branch",
            "source_id": source_id,
            "skill_name": skill_name,
            "branch": branch,
            "supported": bool(branch_record.get("supported", True)),
            "plan08_evidence": bool(branch_record.get("plan08_evidence", False)),
        }
    )
    item, _ = repo.upsert_discovered_item(
        subject_kind="write_branch",
        source_type=_source_type_write_branch(),
        source_id=source_id,
        source_name=name,
        source_name_normalized=name_norm,
        source_digest=digest,
        evidence_json={
            "branch": branch,
            "skillName": skill_name,
            "plan08Evidence": bool(branch_record.get("plan08_evidence", False)),
        },
        actor_principal=actor_principal,
        build_revision=build_revision,
        reason_code=decision.reason_code,
    )
    if dry_run:
        return PackageMigrationItemResult(
            source_id=source_id,
            source_name_normalized=name_norm,
            subject_kind="write_branch",
            outcome="projected",
            state=str(item.state),
            reason_code=decision.reason_code,
            migration_item_id=str(item.id),
        )
    if decision.action == "migrate":
        item = _transition(
            repo,
            item,
            to_state="migrated",
            reason_code=decision.reason_code,
            target_type="write_branch",
            target_id=source_id,
            target_digest=digest,
            evidence_json={"action": "migrate", "branch": branch},
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        outcome = "migrated"
    elif decision.action == "archive":
        item = _archive_item(
            repo,
            item,
            reason_code=decision.reason_code,
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        outcome = "archived"
    else:
        item = _block_item(
            repo,
            item,
            reason_code=decision.reason_code,
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        outcome = "blocked"
    return PackageMigrationItemResult(
        source_id=source_id,
        source_name_normalized=name_norm,
        subject_kind="write_branch",
        outcome=outcome,
        state=str(item.state),
        reason_code=decision.reason_code,
        migration_item_id=str(item.id),
    )


def _verify_one_skill(
    session: Session,
    skill: Any,
    *,
    actor_principal: str | None,
    build_revision: str | None,
    dry_run: bool,
    repo: RuntimeMigrationRepository,
) -> PackageMigrationItemResult:
    try:
        name_norm = normalize_skill_lookup_name(str(skill.name or ""))
    except (TypeError, ValueError):
        name_norm = str(skill.name or "").casefold()

    item = repo.get_item_by_source(
        subject_kind="skill",
        source_type=_source_type_skill(),
        source_id=str(skill.id),
    )
    if item is None:
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="failed",
            state="missing",
            reason_code="migration_item_missing",
        )
    if str(item.state) in {"blocked", "archived"}:
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="unchanged",
            state=str(item.state),
            reason_code=item.reason_code,
            migration_item_id=str(item.id),
        )
    if str(item.state) == "verified":
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="unchanged",
            state="verified",
            reason_code=item.reason_code,
            target_type=item.target_type,
            target_id=item.target_id,
            target_version=item.target_version,
            target_digest=item.target_digest,
            migration_item_id=str(item.id),
        )
    if str(item.state) != "migrated":
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="verify_requires_migrated",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="verify_requires_migrated",
            migration_item_id=str(item.id),
        )

    # Independent checks.
    if name_norm == "general_chat" or is_reserved_skill_lookup_name(skill.name or ""):
        profile = (
            session.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one_or_none()
        )
        if profile is None or profile.published_version_id is None:
            if not dry_run:
                item = _block_item(
                    repo,
                    item,
                    reason_code="verify_profile_missing",
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="blocked",
                state=str(item.state),
                reason_code="verify_profile_missing",
                migration_item_id=str(item.id),
            )
        if str(profile.migration_state) != "cutover":
            if not dry_run:
                item = _block_item(
                    repo,
                    item,
                    reason_code="verify_profile_not_cutover",
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="blocked",
                state=str(item.state),
                reason_code="verify_profile_not_cutover",
                migration_item_id=str(item.id),
            )
        # No skill package for general_chat.
        pkgs = (
            session.query(AssistantSkillPackage)
            .filter(
                AssistantSkillPackage.canonical_name.in_(("general-chat", "general_chat"))
            )
            .all()
        )
        if pkgs:
            if not dry_run:
                item = _block_item(
                    repo,
                    item,
                    reason_code="verify_general_chat_package_exists",
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                )
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="blocked",
                state=str(item.state),
                reason_code="verify_general_chat_package_exists",
                migration_item_id=str(item.id),
            )
        pub = session.get(AssistantMainAgentProfileVersion, profile.published_version_id)
        target_digest = (
            str(pub.content_digest)
            if pub is not None and pub.content_digest
            else item.target_digest
        )
        if dry_run:
            return PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=name_norm,
                subject_kind="skill",
                outcome="projected",
                state="migrated",
                target_type="main_agent_profile",
                target_id=str(profile.id),
                migration_item_id=str(item.id),
            )
        item = _transition(
            repo,
            item,
            to_state="verified",
            reason_code="profile_verified",
            target_type="main_agent_profile",
            target_id=str(profile.id),
            target_version=str(profile.published_version_id),
            target_digest=target_digest if target_digest and len(target_digest) == 64 else None,
            evidence_json={
                "migrationState": str(profile.migration_state),
                "runtimeEnabled": bool(profile.runtime_enabled),
            },
            actor_principal=actor_principal,
            build_revision=build_revision,
        )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="verified",
            state="verified",
            reason_code="profile_verified",
            target_type="main_agent_profile",
            target_id=str(profile.id),
            target_version=str(profile.published_version_id),
            target_digest=item.target_digest,
            migration_item_id=str(item.id),
        )

    # Package path.
    package = None
    if item.target_id:
        try:
            package = session.get(AssistantSkillPackage, UUID(str(item.target_id)))
        except (TypeError, ValueError):
            package = None
    if package is None:
        package = _find_package_for_skill(session, skill)
    if package is not None and not isinstance(package.id, UUID):
        package = session.get(AssistantSkillPackage, _as_uuid(package.id))
    if package is None or package.published_version_id is None:
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="verify_package_missing",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="verify_package_missing",
            migration_item_id=str(item.id),
        )
    if str(package.migration_state) != "cutover":
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="verify_package_not_cutover",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="verify_package_not_cutover",
            migration_item_id=str(item.id),
        )
    version = session.get(AssistantSkillVersion, package.published_version_id)
    if version is None or str(version.version_source) != "publish":
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="verify_published_version_invalid",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="verify_published_version_invalid",
            migration_item_id=str(item.id),
        )
    # Alias uniqueness: canonical alias must point at this package.
    canonical_alias = (
        session.query(AssistantSkillPackageAlias)
        .filter(
            AssistantSkillPackageAlias.skill_package_id == package.id,
            AssistantSkillPackageAlias.alias_type == "canonical",
        )
        .one_or_none()
    )
    if canonical_alias is None:
        if not dry_run:
            item = _block_item(
                repo,
                item,
                reason_code="verify_canonical_alias_missing",
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="blocked",
            state=str(item.state),
            reason_code="verify_canonical_alias_missing",
            migration_item_id=str(item.id),
        )

    # Legacy shadow adapter removed with assistant_skill (Plan 10 B2).

    target_digest = str(version.version_digest or version.content_digest or "")
    if len(target_digest) != 64:
        target_digest = str(item.target_digest or ("0" * 64))
    if dry_run:
        return PackageMigrationItemResult(
            source_id=str(skill.id),
            source_name_normalized=name_norm,
            subject_kind="skill",
            outcome="projected",
            state="migrated",
            target_type="skill_package",
            target_id=str(package.id),
            migration_item_id=str(item.id),
        )
    item = _transition(
        repo,
        item,
        to_state="verified",
        reason_code="package_verified",
        target_type="skill_package",
        target_id=str(package.id),
        target_version=str(version.id),
        target_digest=target_digest,
        evidence_json={
            "canonicalName": package.canonical_name,
            "migrationState": str(package.migration_state),
            "catalogEnabled": bool(package.catalog_enabled),
            "contentDigest": str(version.content_digest or "")[:64],
            "legacyAdapterLocked": True,
        },
        actor_principal=actor_principal,
        build_revision=build_revision,
    )
    return PackageMigrationItemResult(
        source_id=str(skill.id),
        source_name_normalized=name_norm,
        subject_kind="skill",
        outcome="verified",
        state="verified",
        reason_code="package_verified",
        target_type="skill_package",
        target_id=str(package.id),
        target_version=str(version.id),
        target_digest=target_digest,
        migration_item_id=str(item.id),
    )


def _select_skills(
    session: Session,
    skill_ids: Sequence[UUID] | None,
) -> list[Any]:
    """Legacy assistant_skill table dropped — no live skill rows to migrate.

    Historical migration is fixture/CLI driven; post-drop this returns empty.
    """
    _ = (session, skill_ids)
    return []



def _tally(report: PackageMigrationReport, result: PackageMigrationItemResult) -> None:
    report.processed += 1
    report.items.append(result.as_dict())
    if result.outcome in {"migrated", "verified"}:
        report.succeeded += 1
    elif result.outcome == "unchanged":
        report.unchanged += 1
        report.succeeded += 1
    elif result.outcome == "archived":
        report.archived += 1
        report.succeeded += 1
    elif result.outcome == "blocked":
        report.blocked += 1
    elif result.outcome == "projected":
        report.succeeded += 1
    else:
        report.failed += 1


def migrate_packages(
    session: Session,
    *,
    request_id: str,
    actor_principal: str | None = None,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    schema_head: str,
    dry_run: bool = True,
    skill_ids: Sequence[UUID] | None = None,
    write_branches: Sequence[Mapping[str, Any]] | None = None,
    verify: bool = False,
    batch_size: int = 100,
    source_snapshot_digest: str | None = None,
) -> PackageMigrationReport:
    """Migrate legacy skills/profile to native packages with cutover locks.

    Does not enable catalog/runtime traffic. When ``verify=True``, runs the
    independent verify pass in the same call after migrate.
    """
    repo = RuntimeMigrationRepository(session)
    report = PackageMigrationReport(
        command="packages.migrate",
        dry_run=dry_run,
        request_id=request_id,
    )
    snapshot_digest = source_snapshot_digest or sha256_canonical_json(
        {
            "command": "packages.migrate",
            "requestId": request_id,
            "skillIds": [str(s) for s in (skill_ids or ())],
        }
    )
    if len(snapshot_digest) != 64:
        snapshot_digest = sha256_canonical_json({"raw": snapshot_digest})
    config_digest = sha256_canonical_json(
        {
            "command": "packages.migrate",
            "batchSize": int(batch_size),
            "verify": bool(verify),
            "dryRun": bool(dry_run),
        }
    )
    batch = None
    if not dry_run:
        batch = repo.prepare_batch(
            command_kind="package",
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
        report.steps.append("batch_running")

    skills = _select_skills(session, skill_ids)
    report.steps.append(f"selected_skills={len(skills)}")

    for skill in skills:
        try:
            if (skill.name or "") == GENERAL_CHAT_NAME or is_reserved_skill_lookup_name(
                skill.name or ""
            ):
                result = _migrate_general_chat_profile(
                    session,
                    skill,
                    request_id=request_id,
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                    dry_run=dry_run,
                    repo=repo,
                )
            else:
                result = _migrate_package_skill(
                    session,
                    skill,
                    request_id=request_id,
                    actor_principal=actor_principal,
                    build_revision=build_revision,
                    dry_run=dry_run,
                    repo=repo,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("package_migrate_skill_failed", extra={"skill_id": str(skill.id)})
            result = PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=str(skill.name or ""),
                subject_kind="skill",
                outcome="failed",
                state="error",
                reason_code=type(exc).__name__,
            )
        _tally(report, result)

    for branch in write_branches or ():
        try:
            result = _migrate_write_branch(
                session,
                branch,
                actor_principal=actor_principal,
                build_revision=build_revision,
                dry_run=dry_run,
                repo=repo,
            )
        except Exception as exc:  # noqa: BLE001
            result = PackageMigrationItemResult(
                source_id=str(branch.get("id") or ""),
                source_name_normalized=str(branch.get("branch") or ""),
                subject_kind="write_branch",
                outcome="failed",
                state="error",
                reason_code=type(exc).__name__,
            )
        _tally(report, result)

    if verify and not dry_run:
        vreport = verify_packages(
            session,
            request_id=f"{request_id}:verify",
            actor_principal=actor_principal,
            build_revision=build_revision,
            environment=environment,
            database_fingerprint=database_fingerprint,
            schema_head=schema_head,
            dry_run=False,
            skill_ids=skill_ids,
            batch_size=batch_size,
            source_snapshot_digest=snapshot_digest,
        )
        report.steps.append("verify_pass")
        report.steps.extend(vreport.steps)
        # Merge verify outcomes for skills (prefer verified).
        by_source = {i.get("sourceId"): i for i in vreport.items}
        for idx, item in enumerate(list(report.items)):
            sid = item.get("sourceId")
            if sid in by_source and by_source[sid].get("subjectKind") == "skill":
                report.items[idx] = by_source[sid]
        report.blocked += vreport.blocked
        report.failed += vreport.failed

    report_payload = report.to_dict()
    report.report_digest = sha256_canonical_json(
        {
            "command": report.command,
            "processed": report.processed,
            "succeeded": report.succeeded,
            "blocked": report.blocked,
            "failed": report.failed,
            "archived": report.archived,
            "items": [
                {
                    "sourceId": i.get("sourceId"),
                    "state": i.get("state"),
                    "outcome": i.get("outcome"),
                    "targetId": i.get("targetId"),
                }
                for i in report.items
            ],
        }
    )
    report_payload["reportDigest"] = report.report_digest

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


def verify_packages(
    session: Session,
    *,
    request_id: str,
    actor_principal: str | None = None,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    schema_head: str,
    dry_run: bool = True,
    skill_ids: Sequence[UUID] | None = None,
    batch_size: int = 100,
    source_snapshot_digest: str | None = None,
) -> PackageMigrationReport:
    """Independent verification pass: migrated → verified (or blocked on drift)."""
    repo = RuntimeMigrationRepository(session)
    report = PackageMigrationReport(
        command="packages.verify",
        dry_run=dry_run,
        request_id=request_id,
    )
    snapshot_digest = source_snapshot_digest or sha256_canonical_json(
        {
            "command": "packages.verify",
            "requestId": request_id,
            "skillIds": [str(s) for s in (skill_ids or ())],
        }
    )
    if len(snapshot_digest) != 64:
        snapshot_digest = sha256_canonical_json({"raw": snapshot_digest})
    config_digest = sha256_canonical_json(
        {
            "command": "packages.verify",
            "batchSize": int(batch_size),
            "dryRun": bool(dry_run),
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

    skills = _select_skills(session, skill_ids)
    for skill in skills:
        try:
            result = _verify_one_skill(
                session,
                skill,
                actor_principal=actor_principal,
                build_revision=build_revision,
                dry_run=dry_run,
                repo=repo,
            )
        except Exception as exc:  # noqa: BLE001
            result = PackageMigrationItemResult(
                source_id=str(skill.id),
                source_name_normalized=str(skill.name or ""),
                subject_kind="skill",
                outcome="failed",
                state="error",
                reason_code=type(exc).__name__,
            )
        _tally(report, result)

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
                processed_delta=report.processed,
                succeeded_delta=report.succeeded,
                blocked_delta=report.blocked,
                failed_delta=report.failed,
                report_digest=report.report_digest,
                completed_by=actor_principal,
            )
            report.batch_id = str(batch.id)
        except RuntimeMigrationRepositoryError:
            pass
    if not dry_run:
        session.flush()
    return report


__all__ = (
    "SYSTEM_PACKAGE_MIGRATION_ORDER",
    "PackageMigrationError",
    "PackageMigrationReport",
    "PortableLegacySource",
    "SourceClassification",
    "WriteBranchDecision",
    "classify_legacy_source",
    "decide_write_branch_action",
    "migrate_packages",
    "portable_source_from_legacy_record",
    "reject_secrets_in_legacy_skill",
    "verify_packages",
)
