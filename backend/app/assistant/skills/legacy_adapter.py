"""Legacy Skill → disabled v2 shadow package / Main Agent bridge (Plan 01 Task 7).

Does not route, execute, or catalog-enable packages. Shadow aggregates stay
``migration_state=shadow`` and ``catalog_enabled=false`` until an administrator
edit promotes them to native via AgentSkillService.save_draft.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import yaml
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.skills.contracts import (
    MAX_DESCRIPTION_LEN,
    MAX_SKILL_NAME_LEN,
    ParsedSkillPackage,
    is_reserved_skill_lookup_name,
    normalize_skill_lookup_name,
    validate_canonical_skill_name,
)
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
    AssistantSkillVersion,
)
from app.assistant.skills.package_io import parse_skill_directory_files
from app.assistant.skills.schemas import (
    PublishMainAgentProfileCommand,
    PublishSkillVersionCommand,
    SaveMainAgentProfileDraftCommand,
    default_main_agent_profile_snapshot,
    MainAgentProfileSnapshotV1,
)
from app.assistant.skills.service import AgentSkillService, MainAgentProfileService
from app.assistant_config.models import (
    AssistantAgentProfile,
    AssistantAgentProfileVersion,
    AssistantSkill,
    AssistantWorkflow,
    AssistantWorkflowVersion,
)
from app.assistant_config.registry import ToolRegistry
from app.common.exceptions import ApiException

logger = logging.getLogger(__name__)

GENERAL_CHAT_NAME = "general_chat"
SyncStatus = Literal["published", "unchanged", "draft_unresolved", "failed"]

_NON_ASCII_OR_INVALID = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class LegacyTargetRef:
    """Secret-free target identity used for digests and diagnostics."""

    target_type: Literal["workflow", "agent"]
    target_name: str
    target_id: UUID | None
    published_version_id: UUID | None
    published_snapshot_digest: str | None = None


@dataclass
class LegacySyncDiagnostic:
    reason_code: str
    legacy_skill_id: UUID | None
    shadow_package_id: UUID | None = None
    source_path: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "reasonCode": self.reason_code,
            "legacySkillId": str(self.legacy_skill_id) if self.legacy_skill_id else None,
            "shadowPackageId": str(self.shadow_package_id) if self.shadow_package_id else None,
            "sourcePath": self.source_path,
            "message": self.message,
        }


@dataclass
class LegacySyncItem:
    status: SyncStatus
    legacy_skill_id: UUID | None
    shadow_package_id: UUID | None = None
    diagnostics: list[LegacySyncDiagnostic] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "legacySkillId": str(self.legacy_skill_id) if self.legacy_skill_id else None,
            "shadowPackageId": str(self.shadow_package_id) if self.shadow_package_id else None,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
        }


@dataclass
class LegacySyncReport:
    published: int = 0
    unchanged: int = 0
    draft_unresolved: int = 0
    failed: int = 0
    items: list[LegacySyncItem] = field(default_factory=list)

    def add(self, item: LegacySyncItem) -> None:
        self.items.append(item)
        if item.status == "published":
            self.published += 1
        elif item.status == "unchanged":
            self.unchanged += 1
        elif item.status == "draft_unresolved":
            self.draft_unresolved += 1
        else:
            self.failed += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "published": self.published,
            "unchanged": self.unchanged,
            "draftUnresolved": self.draft_unresolved,
            "failed": self.failed,
            "items": [item.as_dict() for item in self.items],
        }


def _stable_uuid_prefix(skill_id: UUID, length: int = 12) -> str:
    return skill_id.hex[:length]


def map_legacy_name_to_canonical_base(name: str, skill_id: UUID) -> str:
    """Map a legacy Skill name to a canonical base (no collision resolution)."""
    raw = name if isinstance(name, str) else ""
    lowered = raw.lower()
    # Underscores and whitespace become hyphens; other non [a-z0-9] collapse.
    collapsed = _NON_ASCII_OR_INVALID.sub("-", lowered)
    collapsed = re.sub(r"-{2,}", "-", collapsed).strip("-")
    if not collapsed:
        return f"legacy-skill-{_stable_uuid_prefix(skill_id)}"
    if len(collapsed) > MAX_SKILL_NAME_LEN:
        collapsed = collapsed[:MAX_SKILL_NAME_LEN].rstrip("-")
        if not collapsed:
            return f"legacy-skill-{_stable_uuid_prefix(skill_id)}"
    return collapsed


def legacy_skill_canonical_name(
    skill: AssistantSkill,
    *,
    occupied: set[str] | None = None,
) -> str:
    """Deterministic canonical name for a legacy Skill.

    ``general_chat`` / ``general-chat`` are reserved and never map to a package.
    Collisions against ``occupied`` resolve with a stable UUID-derived suffix.
    """
    if is_reserved_skill_lookup_name(getattr(skill, "name", "") or ""):
        raise ValueError(
            f"legacy skill {getattr(skill, 'name', None)!r} is reserved for the Main Agent bridge"
        )

    skill_id = skill.id if getattr(skill, "id", None) is not None else UUID(int=0)
    base = map_legacy_name_to_canonical_base(str(skill.name or ""), skill_id)
    # Validate shape; fall back if somehow invalid.
    try:
        base = validate_canonical_skill_name(base)
    except (TypeError, ValueError):
        base = f"legacy-skill-{_stable_uuid_prefix(skill_id)}"
        base = validate_canonical_skill_name(base)

    occupied_names = occupied or set()
    if base not in occupied_names and not is_reserved_skill_lookup_name(base):
        return base

    suffix = _stable_uuid_prefix(skill_id, 8)
    # room for "-" + 8-char suffix
    max_base = MAX_SKILL_NAME_LEN - (1 + len(suffix))
    trimmed = base[:max_base].rstrip("-")
    if not trimmed:
        trimmed = "legacy-skill"
        trimmed = trimmed[:max_base].rstrip("-") or "skill"
    candidate = f"{trimmed}-{suffix}"
    # Ensure uniqueness if the exact candidate is also occupied (extremely rare).
    if candidate in occupied_names:
        alt_suffix = skill_id.hex[8:16]
        candidate = f"{trimmed[: max_base]}-{alt_suffix}".rstrip("-")
        if len(candidate) > MAX_SKILL_NAME_LEN:
            candidate = candidate[:MAX_SKILL_NAME_LEN].rstrip("-")
    return validate_canonical_skill_name(candidate)


def _legacy_alias_if_valid(name: str) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    if is_reserved_skill_lookup_name(name):
        return None
    try:
        normalize_skill_lookup_name(name)
    except (TypeError, ValueError):
        return None
    if len(name) > 128:
        return None
    return name


def _description_for_skill(skill: AssistantSkill, canonical: str) -> str:
    base = (skill.description or "").strip()
    if not base:
        base = f"Legacy Skill {skill.name or canonical}"
    when = f"Use when the user intent matches the legacy skill {skill.name or canonical}."
    text = f"{base} {when}".strip()
    if len(text) > MAX_DESCRIPTION_LEN:
        text = text[:MAX_DESCRIPTION_LEN].rstrip()
    if not text:
        text = f"Legacy skill package for {canonical}."
    return text


def _agent_compatibility_contract() -> dict[str, Any]:
    """Locked Decision 1 generated agent callable surface (not inferred)."""
    return {
        "input_schema": {
            "type": "object",
            "properties": {"input": {}},
            "required": ["input"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "completion": {
            "terminal_output": False,
            "needs_followup": True,
            "followup_hint": None,
        },
    }


def resolve_legacy_target_ref(session: Session, skill: AssistantSkill) -> LegacyTargetRef | None:
    if skill.workflow_id is not None:
        workflow = session.get(AssistantWorkflow, skill.workflow_id)
        if workflow is None:
            return LegacyTargetRef(
                target_type="workflow",
                target_name="",
                target_id=skill.workflow_id,
                published_version_id=None,
            )
        published_digest = None
        if workflow.published_version_id is not None:
            version = (
                session.query(AssistantWorkflowVersion)
                .filter(
                    AssistantWorkflowVersion.id == workflow.published_version_id,
                    AssistantWorkflowVersion.workflow_id == workflow.id,
                    AssistantWorkflowVersion.version_source == "publish",
                )
                .one_or_none()
            )
            if version is not None and isinstance(version.snapshot, dict):
                published_digest = sha256_canonical_json(version.snapshot)
        return LegacyTargetRef(
            target_type="workflow",
            target_name=str(workflow.name or ""),
            target_id=workflow.id,
            published_version_id=workflow.published_version_id,
            published_snapshot_digest=published_digest,
        )

    if skill.agent_profile_id is not None:
        agent = session.get(AssistantAgentProfile, skill.agent_profile_id)
        if agent is None:
            return LegacyTargetRef(
                target_type="agent",
                target_name="",
                target_id=skill.agent_profile_id,
                published_version_id=None,
            )
        published_digest = None
        if agent.published_version_id is not None:
            version = (
                session.query(AssistantAgentProfileVersion)
                .filter(
                    AssistantAgentProfileVersion.id == agent.published_version_id,
                    AssistantAgentProfileVersion.agent_profile_id == agent.id,
                    AssistantAgentProfileVersion.version_source == "publish",
                )
                .one_or_none()
            )
            if version is not None and isinstance(version.snapshot, dict):
                published_digest = sha256_canonical_json(version.snapshot)
        return LegacyTargetRef(
            target_type="agent",
            target_name=str(agent.name or ""),
            target_id=agent.id,
            published_version_id=agent.published_version_id,
            published_snapshot_digest=published_digest,
        )
    return None


def legacy_source_digest(
    skill: AssistantSkill,
    target_ref: LegacyTargetRef | None,
) -> str:
    """Stable digest of legacy Skill source + pinned target publication evidence."""
    payload: dict[str, Any] = {
        "legacySkillId": str(skill.id) if skill.id is not None else None,
        "name": skill.name,
        "description": skill.description or "",
        "intentExamples": list(skill.intent_examples or [])
        if isinstance(skill.intent_examples, list)
        else skill.intent_examples,
        "tools": list(skill.tools or []) if isinstance(skill.tools, list) else skill.tools,
        "systemPrompt": skill.system_prompt,
        "kbConfig": skill.kb_config if isinstance(skill.kb_config, dict) else skill.kb_config,
        "langgraphPattern": skill.langgraph_pattern,
        "enabled": bool(skill.enabled),
        "isSystem": bool(skill.is_system),
        "target": None
        if target_ref is None
        else {
            "type": target_ref.target_type,
            "name": target_ref.target_name,
            "id": str(target_ref.target_id) if target_ref.target_id else None,
            "publishedVersionId": str(target_ref.published_version_id)
            if target_ref.published_version_id
            else None,
            "publishedSnapshotDigest": target_ref.published_snapshot_digest,
        },
    }
    return sha256_canonical_json(payload)


def render_legacy_skill_package(
    skill: AssistantSkill,
    *,
    target_ref: LegacyTargetRef,
    canonical_name: str | None = None,
    occupied: set[str] | None = None,
) -> ParsedSkillPackage:
    """Render a portable ParsedSkillPackage for a legacy Skill (no DB IDs in files)."""
    if is_reserved_skill_lookup_name(skill.name or ""):
        raise ValueError("general_chat cannot be rendered as a skill package")
    if not target_ref.target_name:
        raise ValueError("legacy skill target name is required for package rendering")

    canonical = canonical_name or legacy_skill_canonical_name(skill, occupied=occupied)
    description = _description_for_skill(skill, canonical)
    legacy_alias = _legacy_alias_if_valid(str(skill.name or ""))
    # Do not alias when it normalizes to the canonical name (duplicate).
    aliases: list[str] = []
    if legacy_alias is not None:
        try:
            if normalize_skill_lookup_name(legacy_alias) != normalize_skill_lookup_name(
                canonical
            ):
                aliases.append(legacy_alias)
        except (TypeError, ValueError):
            pass

    if target_ref.target_type == "workflow":
        capability: dict[str, Any] = {
            "type": "workflow",
            "key": target_ref.target_name,
        }
    else:
        capability = {
            "type": "agent",
            "key": target_ref.target_name,
            "contract": _agent_compatibility_contract(),
        }

    examples: list[str] = []
    if isinstance(skill.intent_examples, list):
        for item in skill.intent_examples:
            if isinstance(item, str) and item.strip():
                examples.append(item.strip()[:1000])
            if len(examples) >= 100:
                break

    manifest: dict[str, Any] = {
        "version": 1,
        "display_name": (skill.name or canonical)[:128],
        "legacy_aliases": aliases,
        "routing": {
            "include_examples": examples,
            "exclude_examples": [],
            "conflict_rules": [],
        },
        "capabilities": [capability],
        "policy": {
            "allowed_side_effects": [],
            "max_skill_calls": 16,
            "max_same_read_calls": 3,
            "requires_terminal_output": False,
            "terminal_text_allowed": False,
        },
        "provider_aliases": {},
        "metadata": {},
    }

    skill_md = (
        f"---\n"
        f"name: {canonical}\n"
        f"description: {description}\n"
        f"---\n\n"
        f"# {canonical}\n\n"
        f"{description}\n"
    )
    mindatlas_yaml = yaml.safe_dump(
        manifest,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return parse_skill_directory_files(
        {
            "SKILL.md": skill_md.encode("utf-8"),
            "mindatlas.yaml": mindatlas_yaml.encode("utf-8"),
        },
        expected_root_name=canonical,
    )


def _occupied_canonical_names(session: Session) -> set[str]:
    names = {
        row[0]
        for row in session.query(AssistantSkillPackage.canonical_name).all()
        if row[0]
    }
    alias_rows = session.query(AssistantSkillPackageAlias.normalized_alias).all()
    for (alias,) in alias_rows:
        if alias:
            names.add(alias)
    return names


def _map_publish_exception(
    exc: ApiException,
    *,
    legacy_skill_id: UUID,
    shadow_package_id: UUID | None,
) -> LegacySyncDiagnostic:
    details = getattr(exc, "details", None) or {}
    reason = None
    if isinstance(details, dict):
        reason = details.get("reason")
    # Normalize resolver codes to Task 7 diagnostic codes.
    if reason in {"default_model_unbound", "unbound_default_model"}:
        reason_code = "unbound_default_model"
    elif reason == "non_immutable_build_revision":
        reason_code = "non_immutable_build_revision"
    elif reason == "blank_build_revision":
        reason_code = "non_immutable_build_revision"
    elif reason == "credential_missing":
        reason_code = "unbound_default_model"
    else:
        message = (exc.message or "").lower()
        if "no published version" in message:
            reason_code = "target_unpublished"
        elif "not found" in message:
            reason_code = "target_missing"
        elif "disabled" in message:
            reason_code = "target_disabled"
        else:
            reason_code = reason or "publish_unresolved"

    source_path = None
    if isinstance(details, dict):
        source_path = details.get("path") or details.get("capabilityKey")
        if isinstance(source_path, str) and len(source_path) > 256:
            source_path = source_path[:256]

    return LegacySyncDiagnostic(
        reason_code=str(reason_code),
        legacy_skill_id=legacy_skill_id,
        shadow_package_id=shadow_package_id,
        source_path=str(source_path) if source_path is not None else None,
        message=exc.message,
    )


def _existing_package_for_skill(
    session: Session, legacy_skill_id: UUID
) -> AssistantSkillPackage | None:
    return (
        session.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.legacy_skill_id == legacy_skill_id)
        .one_or_none()
    )


def _try_publish_package(
    session: Session,
    *,
    package: AssistantSkillPackage,
    draft_version_id: UUID,
    legacy_skill_id: UUID,
) -> tuple[SyncStatus, list[LegacySyncDiagnostic]]:
    svc = AgentSkillService(session)
    try:
        svc.publish(
            package.id,
            PublishSkillVersionCommand(draft_version_id=draft_version_id),
        )
        return "published", []
    except ApiException as exc:
        diagnostic = _map_publish_exception(
            exc,
            legacy_skill_id=legacy_skill_id,
            shadow_package_id=package.id,
        )
        # Ensure package remains unpublished.
        session.rollback()
        # Re-load and clear any partial pointer if needed.
        package = session.get(AssistantSkillPackage, package.id)  # type: ignore[assignment]
        if package is not None and package.published_version_id is not None:
            # publish commits only on success; rollback leaves prior state.
            pass
        return "draft_unresolved", [diagnostic]
    except Exception as exc:  # noqa: BLE001 — isolate corrupt rows
        logger.exception(
            "legacy_shadow_publish_failed",
            extra={"legacy_skill_id": str(legacy_skill_id), "package_id": str(package.id)},
        )
        session.rollback()
        return "failed", [
            LegacySyncDiagnostic(
                reason_code="publish_failed",
                legacy_skill_id=legacy_skill_id,
                shadow_package_id=package.id,
                message=str(exc.__class__.__name__),
            )
        ]


def _materialize_shadow_draft(
    session: Session,
    *,
    skill: AssistantSkill,
    parsed: ParsedSkillPackage,
    source_digest: str,
    package: AssistantSkillPackage | None,
) -> AssistantSkillPackage:
    """Create or update a shadow package draft without promoting to native."""
    svc = AgentSkillService(session)

    if package is None:
        package = AssistantSkillPackage(
            canonical_name=parsed.canonical_name,
            display_name=(
                parsed.manifest.display_name
                if parsed.manifest and parsed.manifest.display_name
                else parsed.canonical_name
            ),
            description=parsed.frontmatter.description,
            migration_state="shadow",
            catalog_enabled=False,
            is_system=bool(skill.is_system),
            legacy_skill_id=skill.id,
            legacy_source_digest=source_digest,
        )
        session.add(package)
        session.flush()
        legacy_aliases = (
            list(parsed.manifest.legacy_aliases) if parsed.manifest else []
        )
        svc._reserve_aliases(  # noqa: SLF001 — shared alias reservation
            package_id=package.id,
            canonical_name=parsed.canonical_name,
            legacy_aliases=legacy_aliases,
        )
        version = svc._insert_draft_version(  # noqa: SLF001
            package=package,
            parsed=parsed,
            version_name="legacy-draft-1",
            origin="legacy",
            sequence_no=1,
        )
        package.draft_version_id = version.id
        session.commit()
        return package

    # Existing shadow package: never promote; append or re-point draft only.
    package.legacy_source_digest = source_digest
    package.display_name = (
        parsed.manifest.display_name
        if parsed.manifest and parsed.manifest.display_name
        else package.display_name
    )
    package.description = parsed.frontmatter.description
    package.catalog_enabled = False

    if parsed.manifest and parsed.manifest.legacy_aliases:
        svc._append_legacy_aliases(  # noqa: SLF001
            package_id=package.id,
            legacy_aliases=list(parsed.manifest.legacy_aliases),
        )

    existing = (
        session.query(AssistantSkillVersion)
        .filter(
            AssistantSkillVersion.skill_package_id == package.id,
            AssistantSkillVersion.version_source == "save",
            AssistantSkillVersion.content_digest == parsed.content_digest,
        )
        .one_or_none()
    )
    if existing is not None:
        package.draft_version_id = existing.id
        session.commit()
        return package

    next_seq = svc._next_sequence(package.id)  # noqa: SLF001
    version = svc._insert_draft_version(  # noqa: SLF001
        package=package,
        parsed=parsed,
        version_name=f"legacy-draft-{next_seq}",
        origin="legacy",
        sequence_no=next_seq,
    )
    package.draft_version_id = version.id
    session.commit()
    return package


def _load_published_agent_snapshot(
    session: Session, agent_profile_id: UUID
) -> tuple[AssistantAgentProfile, AssistantAgentProfileVersion, dict[str, Any]] | None:
    agent = session.get(AssistantAgentProfile, agent_profile_id)
    if agent is None or agent.published_version_id is None:
        return None
    version = (
        session.query(AssistantAgentProfileVersion)
        .filter(
            AssistantAgentProfileVersion.id == agent.published_version_id,
            AssistantAgentProfileVersion.agent_profile_id == agent.id,
            AssistantAgentProfileVersion.version_source == "publish",
        )
        .one_or_none()
    )
    if version is None or not isinstance(version.snapshot, dict):
        return None
    return agent, version, dict(version.snapshot)


def _validated_control_tool_keys(tools: Any) -> tuple[str, ...]:
    """Return tool names that exist in the system registry (no invented grants)."""
    if not isinstance(tools, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in tools:
        if not isinstance(item, str) or not item.strip():
            continue
        name = item.strip()
        if name in seen:
            continue
        if ToolRegistry.has_system_tool(name):
            seen.add(name)
            out.append(name)
    return tuple(out)


def _bridge_source_ref(
    *,
    skill: AssistantSkill,
    agent: AssistantAgentProfile,
    version: AssistantAgentProfileVersion,
    snapshot: dict[str, Any],
    validated_tools: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "legacySkillName": skill.name,
        "legacySkillId": str(skill.id),
        "agentProfileId": str(agent.id),
        "agentProfileName": agent.name,
        "agentVersionId": str(version.id),
        "agentSnapshotDigest": sha256_canonical_json(snapshot),
        "tools": list(snapshot.get("tools") or []),
        "validatedControlToolKeys": list(validated_tools),
        "kbConfig": snapshot.get("kb_config")
        if isinstance(snapshot.get("kb_config"), dict)
        else {},
        "modelSource": snapshot.get("model_source"),
        "modelId": snapshot.get("model_id"),
        # Plan 01 cannot publish non-empty controlCapabilityKeys; tools stay audit-only.
        "controlCapabilityKeysDeferred": True,
    }


def _main_agent_snapshot_from_agent(
    snapshot: dict[str, Any],
) -> MainAgentProfileSnapshotV1:
    defaults = default_main_agent_profile_snapshot()
    prompt = snapshot.get("system_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = defaults.base_prompt
    # Keep control_capability_keys empty so MainAgentProfileService.publish succeeds
    # under Plan 01 (42294 for non-empty keys). Validated tools live in source_ref.
    return MainAgentProfileSnapshotV1(
        schema_version=1,
        base_prompt=prompt,
        response_style=defaults.response_style,
        supported_entrypoints=defaults.supported_entrypoints,
        model_requirements=defaults.model_requirements,
        control_capability_keys=(),
        skill_catalog_scope=defaults.skill_catalog_scope,
        context_budget=defaults.context_budget,
        output_budget=defaults.output_budget,
        global_safety_policy=defaults.global_safety_policy,
        fallback_policy=defaults.fallback_policy,
    )


class LegacySkillShadowAdapter:
    """Mirror legacy Skills into disabled shadow packages / Main Agent baseline."""

    def sync_one(self, session: Session, legacy_skill_id: UUID) -> LegacySyncItem:
        try:
            skill = session.get(AssistantSkill, legacy_skill_id)
            if skill is None:
                return LegacySyncItem(
                    status="failed",
                    legacy_skill_id=legacy_skill_id,
                    diagnostics=[
                        LegacySyncDiagnostic(
                            reason_code="legacy_skill_missing",
                            legacy_skill_id=legacy_skill_id,
                            message="legacy skill row not found",
                        )
                    ],
                )

            if (skill.name or "") == GENERAL_CHAT_NAME or is_reserved_skill_lookup_name(
                skill.name or ""
            ):
                return self._sync_general_chat(session, skill)

            return self._sync_package_skill(session, skill)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "legacy_shadow_sync_one_failed",
                extra={"legacy_skill_id": str(legacy_skill_id)},
            )
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=legacy_skill_id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="sync_exception",
                        legacy_skill_id=legacy_skill_id,
                        message=str(exc.__class__.__name__),
                    )
                ],
            )

    def sync_all(self, session: Session) -> LegacySyncReport:
        report = LegacySyncReport()
        skill_ids = [
            row[0]
            for row in session.query(AssistantSkill.id)
            .order_by(AssistantSkill.name.asc())
            .all()
        ]
        for skill_id in skill_ids:
            item = self.sync_one(session, skill_id)
            report.add(item)
        return report

    def _sync_package_skill(
        self, session: Session, skill: AssistantSkill
    ) -> LegacySyncItem:
        package = _existing_package_for_skill(session, skill.id)
        if package is not None and package.migration_state != "shadow":
            # Administrator/native ownership — automatic shadow sync stops.
            return LegacySyncItem(
                status="unchanged",
                legacy_skill_id=skill.id,
                shadow_package_id=package.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="shadow_sync_stopped_native",
                        legacy_skill_id=skill.id,
                        shadow_package_id=package.id,
                        message=f"package migration_state={package.migration_state}",
                    )
                ],
            )

        target_ref = resolve_legacy_target_ref(session, skill)
        if target_ref is None:
            # Still materialize a draft only if we can render — without target we fail.
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                shadow_package_id=package.id if package else None,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="target_missing",
                        legacy_skill_id=skill.id,
                        shadow_package_id=package.id if package else None,
                        message="legacy skill has no workflow or agent binding",
                    )
                ],
            )

        if not target_ref.target_name:
            # Create nothing publishable; still attempt no false publish.
            if package is None:
                return LegacySyncItem(
                    status="draft_unresolved",
                    legacy_skill_id=skill.id,
                    diagnostics=[
                        LegacySyncDiagnostic(
                            reason_code="target_missing",
                            legacy_skill_id=skill.id,
                            message="legacy skill target row missing",
                        )
                    ],
                )
            return LegacySyncItem(
                status="draft_unresolved",
                legacy_skill_id=skill.id,
                shadow_package_id=package.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="target_missing",
                        legacy_skill_id=skill.id,
                        shadow_package_id=package.id,
                        message="legacy skill target row missing",
                    )
                ],
            )

        source_digest = legacy_source_digest(skill, target_ref)

        # Unchanged source: reconcile publish if needed, else unchanged.
        if (
            package is not None
            and package.legacy_source_digest == source_digest
            and package.draft_version_id is not None
        ):
            if package.published_version_id is not None:
                return LegacySyncItem(
                    status="unchanged",
                    legacy_skill_id=skill.id,
                    shadow_package_id=package.id,
                )
            status, diagnostics = _try_publish_package(
                session,
                package=package,
                draft_version_id=package.draft_version_id,
                legacy_skill_id=skill.id,
            )
            return LegacySyncItem(
                status=status,
                legacy_skill_id=skill.id,
                shadow_package_id=package.id,
                diagnostics=diagnostics,
            )

        occupied = _occupied_canonical_names(session)
        if package is not None:
            # Canonical name is immutable; do not reallocate.
            canonical = package.canonical_name
            occupied.discard(canonical)
        else:
            canonical = legacy_skill_canonical_name(skill, occupied=occupied)

        try:
            parsed = render_legacy_skill_package(
                skill,
                target_ref=target_ref,
                canonical_name=canonical,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "legacy_shadow_render_failed",
                extra={"legacy_skill_id": str(skill.id)},
            )
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                shadow_package_id=package.id if package else None,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="render_failed",
                        legacy_skill_id=skill.id,
                        shadow_package_id=package.id if package else None,
                        message=str(exc.__class__.__name__),
                    )
                ],
            )

        try:
            package = _materialize_shadow_draft(
                session,
                skill=skill,
                parsed=parsed,
                source_digest=source_digest,
                package=package,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "legacy_shadow_materialize_failed",
                extra={"legacy_skill_id": str(skill.id)},
            )
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="materialize_failed",
                        legacy_skill_id=skill.id,
                        message=str(exc.__class__.__name__),
                    )
                ],
            )

        if package.draft_version_id is None:
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                shadow_package_id=package.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="draft_missing",
                        legacy_skill_id=skill.id,
                        shadow_package_id=package.id,
                    )
                ],
            )

        if target_ref.published_version_id is None:
            return LegacySyncItem(
                status="draft_unresolved",
                legacy_skill_id=skill.id,
                shadow_package_id=package.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="target_unpublished",
                        legacy_skill_id=skill.id,
                        shadow_package_id=package.id,
                        source_path=target_ref.target_name,
                        message="target has no published version",
                    )
                ],
            )

        status, diagnostics = _try_publish_package(
            session,
            package=package,
            draft_version_id=package.draft_version_id,
            legacy_skill_id=skill.id,
        )
        return LegacySyncItem(
            status=status,
            legacy_skill_id=skill.id,
            shadow_package_id=package.id,
            diagnostics=diagnostics,
        )

    def _sync_general_chat(
        self, session: Session, skill: AssistantSkill
    ) -> LegacySyncItem:
        # Never create a general-chat package.
        existing_pkg = (
            session.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name.in_(("general-chat", "general_chat")))
            .first()
        )
        if existing_pkg is not None:
            # Should not exist; leave it alone and report.
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                shadow_package_id=existing_pkg.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="reserved_package_exists",
                        legacy_skill_id=skill.id,
                        shadow_package_id=existing_pkg.id,
                        message="general-chat package must not exist",
                    )
                ],
            )

        if skill.agent_profile_id is None:
            return LegacySyncItem(
                status="draft_unresolved",
                legacy_skill_id=skill.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="target_missing",
                        legacy_skill_id=skill.id,
                        message="general_chat has no agent profile binding",
                    )
                ],
            )

        loaded = _load_published_agent_snapshot(session, skill.agent_profile_id)
        if loaded is None:
            return LegacySyncItem(
                status="draft_unresolved",
                legacy_skill_id=skill.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="target_unpublished",
                        legacy_skill_id=skill.id,
                        message="general_chat agent has no published version",
                    )
                ],
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
                "basePrompt": snapshot.get("system_prompt"),
            }
        )

        profile_svc = MainAgentProfileService(session)
        profile_summary = profile_svc.ensure_default()
        profile = session.get(AssistantMainAgentProfile, profile_summary.id)
        if profile is None:
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="main_agent_missing",
                        legacy_skill_id=skill.id,
                        message="default main agent profile missing after ensure_default",
                    )
                ],
            )

        # Idempotent when source digest matches and a published version exists.
        if (
            profile.legacy_source_digest == bridge_digest
            and profile.published_version_id is not None
            and profile.legacy_skill_id == skill.id
        ):
            return LegacySyncItem(
                status="unchanged",
                legacy_skill_id=skill.id,
            )

        main_snapshot = _main_agent_snapshot_from_agent(snapshot)
        try:
            draft = profile_svc.save_draft(
                profile.id,
                SaveMainAgentProfileDraftCommand(
                    snapshot=main_snapshot,
                    version_name="legacy-general-chat",
                    origin="legacy",
                    source_ref=source_ref,
                ),
            )
            # save_draft commits; re-load and stamp legacy identity.
            profile = session.get(AssistantMainAgentProfile, profile.id)
            assert profile is not None
            profile.legacy_skill_id = skill.id
            profile.legacy_source_digest = bridge_digest
            session.commit()

            if (
                profile.published_version_id is not None
                and profile.legacy_source_digest == bridge_digest
            ):
                # Check whether published content already matches this draft digest.
                published = session.get(
                    AssistantMainAgentProfileVersion, profile.published_version_id
                )
                if (
                    published is not None
                    and published.content_digest == draft.content_digest
                ):
                    return LegacySyncItem(
                        status="unchanged",
                        legacy_skill_id=skill.id,
                    )

            profile_svc.publish(
                profile.id,
                PublishMainAgentProfileCommand(draft_version_id=draft.id),
            )
            profile = session.get(AssistantMainAgentProfile, profile.id)
            if profile is not None:
                profile.legacy_skill_id = skill.id
                profile.legacy_source_digest = bridge_digest
                session.commit()
            return LegacySyncItem(
                status="published",
                legacy_skill_id=skill.id,
            )
        except ApiException as exc:
            diagnostic = _map_publish_exception(
                exc,
                legacy_skill_id=skill.id,
                shadow_package_id=None,
            )
            if diagnostic.reason_code == "publish_unresolved":
                diagnostic = LegacySyncDiagnostic(
                    reason_code="main_agent_unresolved",
                    legacy_skill_id=skill.id,
                    message=exc.message,
                )
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return LegacySyncItem(
                status="draft_unresolved",
                legacy_skill_id=skill.id,
                diagnostics=[diagnostic],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "legacy_general_chat_bridge_failed",
                extra={"legacy_skill_id": str(skill.id)},
            )
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return LegacySyncItem(
                status="failed",
                legacy_skill_id=skill.id,
                diagnostics=[
                    LegacySyncDiagnostic(
                        reason_code="bridge_failed",
                        legacy_skill_id=skill.id,
                        message=str(exc.__class__.__name__),
                    )
                ],
            )


def best_effort_sync_one(session: Session, legacy_skill_id: UUID) -> None:
    """Post-commit reconciliation helper; never raises to callers."""
    try:
        item = LegacySkillShadowAdapter().sync_one(session, legacy_skill_id)
        if item.status in {"draft_unresolved", "failed"}:
            logger.warning(
                "legacy_shadow_sync_one_diagnostic",
                extra={"item": item.as_dict()},
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "legacy_shadow_sync_one_best_effort_failed",
            extra={"legacy_skill_id": str(legacy_skill_id)},
        )


def best_effort_sync_all(session: Session) -> LegacySyncReport | None:
    """Startup / catalog-warm repair pass; never raises to callers."""
    try:
        report = LegacySkillShadowAdapter().sync_all(session)
        logger.info(
            "legacy_shadow_sync_all_complete",
            extra={
                "published": report.published,
                "unchanged": report.unchanged,
                "draft_unresolved": report.draft_unresolved,
                "failed": report.failed,
            },
        )
        return report
    except Exception:  # noqa: BLE001
        logger.exception("legacy_shadow_sync_all_best_effort_failed")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


__all__ = [
    "GENERAL_CHAT_NAME",
    "LegacySkillShadowAdapter",
    "LegacySyncDiagnostic",
    "LegacySyncItem",
    "LegacySyncReport",
    "LegacyTargetRef",
    "best_effort_sync_all",
    "best_effort_sync_one",
    "legacy_skill_canonical_name",
    "legacy_source_digest",
    "map_legacy_name_to_canonical_base",
    "render_legacy_skill_package",
    "resolve_legacy_target_ref",
]
