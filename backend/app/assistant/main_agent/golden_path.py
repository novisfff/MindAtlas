"""Golden read-only Skill package fixture helpers (Plan 04 Task 9).

Prefer promoting ``quick_stats`` when its recursive descriptor closure is
``read|compute`` with ``interrupt_mode=none``. When that is not available in a
given environment, create a pure-read fixture package bound only to system tools
classified as ``read``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS
from app.assistant.skills.package_io import parse_skill_directory_files
from app.assistant.skills.schemas import (
    CreateSkillPackageCommand,
    MainAgentProfileSnapshotV1,
    PublishMainAgentProfileCommand,
    PublishSkillVersionCommand,
    SaveMainAgentProfileDraftCommand,
    SaveSkillDraftCommand,
    SkillCatalogScopeV1,
    default_main_agent_profile_snapshot,
)
from app.assistant.skills.service import AgentSkillService, MainAgentProfileService

GOLDEN_FIXTURE_CANONICAL_NAME = "main-agent-read-only-fixture"
GOLDEN_FIXTURE_DISPLAY_NAME = "Main Agent Read-Only Fixture"
GOLDEN_FIXTURE_DESCRIPTION = (
    "Plan 04 golden path pure-read fixture skill; activates only classified "
    "read system tools for statistics overview."
)
GOLDEN_QUICK_STATS_CANONICAL = "quick-stats"
GOLDEN_QUICK_STATS_LEGACY_ALIAS = "quick_stats"

# Pure-read system tools used by the fixture package (Plan 02 classifications).
GOLDEN_FIXTURE_READ_TOOLS: tuple[str, ...] = (
    "get_statistics",
    "analyze_activity",
    "get_tag_statistics",
)

READ_ONLY_SIDE_EFFECTS = frozenset({"none", "read", "compute"})


@dataclass(frozen=True, slots=True)
class GoldenPackagePlan:
    """Decision record for which package the rollout will promote."""

    strategy: str  # "quick_stats" | "fixture"
    canonical_name: str
    package_id: UUID | None
    published_version_id: UUID | None
    version_digest: str | None
    content_digest: str | None
    reason: str
    side_effects: tuple[str, ...]
    interrupt_modes: tuple[str, ...]


def build_fixture_skill_md(
    *,
    name: str = GOLDEN_FIXTURE_CANONICAL_NAME,
    description: str = GOLDEN_FIXTURE_DESCRIPTION,
) -> bytes:
    body = (
        "# Main Agent Read-Only Fixture\n\n"
        "Use this Skill for read-only statistics overview questions.\n"
        "Do not draft, write, or mutate any knowledge entries.\n"
        "Prefer get_statistics for totals and analyze_activity for trends.\n"
    )
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    ).encode("utf-8")


def build_fixture_mindatlas_yaml(
    *,
    display_name: str = GOLDEN_FIXTURE_DISPLAY_NAME,
    legacy_aliases: Sequence[str] = (),
    tools: Sequence[str] = GOLDEN_FIXTURE_READ_TOOLS,
) -> bytes:
    alias_block = (
        "\n".join(f"  - {alias}" for alias in legacy_aliases) if legacy_aliases else "  []"
    )
    caps = "".join(f"  - type: tool\n    key: {tool}\n" for tool in tools)
    include = (
        "  - show me my knowledge statistics\n"
        "  - 给我看一下知识库统计\n"
        "  - quick stats for this month\n"
        "  - 本月快速统计\n"
    )
    exclude = (
        "  - create a new entry\n"
        "  - delete my notes\n"
        "  - draft a weekly report to publish\n"
        "  - write a new project summary\n"
    )
    return (
        "version: 1\n"
        f"display_name: {display_name}\n"
        f"legacy_aliases:\n{alias_block}\n"
        "\n"
        "routing:\n"
        "  include_examples:\n"
        f"{include}"
        "  exclude_examples:\n"
        f"{exclude}"
        "  conflict_rules: []\n"
        "\n"
        "capabilities:\n"
        f"{caps}"
        "\n"
        "policy:\n"
        "  allowed_side_effects:\n"
        "    - read\n"
        "    - compute\n"
        "  max_skill_calls: 8\n"
        "  max_same_read_calls: 3\n"
        "  requires_terminal_output: true\n"
        "  terminal_text_allowed: true\n"
        "\n"
        "provider_aliases: {}\n"
        "metadata:\n"
        "  plan04_golden: \"true\"\n"
        "  golden_strategy: fixture\n"
    ).encode("utf-8")


def parse_fixture_package(
    *,
    name: str = GOLDEN_FIXTURE_CANONICAL_NAME,
    tools: Sequence[str] = GOLDEN_FIXTURE_READ_TOOLS,
):
    return parse_skill_directory_files(
        {
            "SKILL.md": build_fixture_skill_md(name=name),
            "mindatlas.yaml": build_fixture_mindatlas_yaml(tools=tools),
        },
        expected_root_name=None,
    )


def create_or_refresh_fixture_package(
    db: Session,
    *,
    canonical_name: str = GOLDEN_FIXTURE_CANONICAL_NAME,
    tools: Sequence[str] = GOLDEN_FIXTURE_READ_TOOLS,
) -> tuple[Any, Any]:
    """Create/publish a pure-read fixture package. Returns (package detail, publish summary)."""
    from app.assistant.skills.models import AssistantSkillPackage

    svc = AgentSkillService(db)
    existing = (
        db.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.canonical_name == canonical_name)
        .one_or_none()
    )
    parsed = parse_fixture_package(name=canonical_name, tools=tools)
    if existing is None:
        detail = svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="golden-draft-1")
        )
        package_id = detail.id
        draft_id = detail.draft_version.id if detail.draft_version else None
    else:
        package_id = existing.id
        rev = int(getattr(existing, "aggregate_revision", 0) or 0)
        draft = svc.save_draft(
            SaveSkillDraftCommand(
                package_id=package_id,
                parsed=parsed,
                version_name="golden-draft",
                origin="api",
                expected_aggregate_revision=rev,
                request_id=f"golden-draft:{package_id}:{rev}",
            )
        )
        draft_id = draft.id
    if draft_id is None:
        raise RuntimeError("fixture package has no draft to publish")
    published = svc.publish(
        package_id,
        PublishSkillVersionCommand(
            draft_version_id=draft_id,
            request_id=f"golden-publish:{package_id}:{draft_id}",
        ),
    )
    detail = svc.get_package(package_id)
    return detail, published


def is_read_only_behavior(
    *,
    side_effect: str | None,
    interrupt_mode: str | None,
) -> bool:
    if side_effect is None or interrupt_mode is None:
        return False
    return side_effect in READ_ONLY_SIDE_EFFECTS and interrupt_mode == "none"


def classify_published_package_read_only(
    db: Session,
    *,
    package_id: UUID,
    version_id: UUID | None = None,
) -> tuple[bool, tuple[str, ...], tuple[str, ...], str]:
    """Best-effort recursive classification of published bindings.

    Returns ``(ok, side_effects, interrupt_modes, reason)``.
    """
    from app.assistant.capabilities.classification import CapabilityClassifier
    from app.assistant.skills.models import (
        AssistantSkillCapabilityBinding,
        AssistantSkillCapabilityDependency,
        AssistantSkillPackage,
        AssistantSkillVersion,
    )
    from app.assistant.skills.resolution import reconstruct_binding_snapshot

    package = db.get(AssistantSkillPackage, package_id)
    if package is None:
        return False, (), (), "package_missing"
    vid = version_id or package.published_version_id
    if vid is None:
        return False, (), (), "unpublished"
    version = db.get(AssistantSkillVersion, vid)
    if version is None or version.skill_package_id != package.id:
        return False, (), (), "version_unowned"
    if str(version.version_source) != "publish":
        return False, (), (), "version_not_publish"

    bindings = (
        db.query(AssistantSkillCapabilityBinding)
        .filter(AssistantSkillCapabilityBinding.skill_version_id == version.id)
        .order_by(AssistantSkillCapabilityBinding.ordinal.asc())
        .all()
    )
    if not bindings:
        # Instruction-only package is still read-only.
        return True, ("none",), ("none",), "instruction_only"

    classifier = CapabilityClassifier()
    side_effects: list[str] = []
    interrupt_modes: list[str] = []
    for binding in bindings:
        deps = (
            db.query(AssistantSkillCapabilityDependency)
            .filter(AssistantSkillCapabilityDependency.binding_id == binding.id)
            .order_by(AssistantSkillCapabilityDependency.ordinal.asc())
            .all()
        )
        try:
            snapshot = reconstruct_binding_snapshot(binding, deps)
        except Exception:
            return False, (), (), "binding_snapshot_failed"
        # Prefer direct system-tool classification when available.
        target = str(binding.target_identity or "")
        if target.startswith("system-tool:"):
            tool_name = target.split(":", 1)[1]
            from app.assistant.capabilities.classification import SYSTEM_TOOL_CLASSIFICATIONS

            entry = SYSTEM_TOOL_CLASSIFICATIONS.get(tool_name)
            if entry is None:
                return False, tuple(side_effects), tuple(interrupt_modes), f"unknown_tool:{tool_name}"
            side, _parallel = entry
            side_effects.append(side)
            interrupt_modes.append("none")
            if not is_read_only_behavior(side_effect=side, interrupt_mode="none"):
                return (
                    False,
                    tuple(side_effects),
                    tuple(interrupt_modes),
                    f"tool_not_read_only:{tool_name}:{side}",
                )
            continue
        # Workflow/agent surfaces: attempt classifier when surface reconstructable.
        try:
            # Reconstructing a full Capability surface needs Registry; keep conservative.
            _ = snapshot
            _ = classifier
            return (
                False,
                tuple(side_effects),
                tuple(interrupt_modes),
                f"non_system_tool_binding:{target or binding.capability_key}",
            )
        except Exception:
            return False, tuple(side_effects), tuple(interrupt_modes), "classification_failed"
    return True, tuple(side_effects), tuple(interrupt_modes), "ok"


def select_golden_package_plan(
    db: Session,
    *,
    prefer_quick_stats: bool = True,
    allow_create_fixture: bool = True,
) -> GoldenPackagePlan:
    """Choose quick_stats (if classifiable read-only) else pure-read fixture."""
    from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion

    if prefer_quick_stats:
        candidates = (
            db.query(AssistantSkillPackage)
            .filter(
                AssistantSkillPackage.canonical_name.in_(
                    (GOLDEN_QUICK_STATS_CANONICAL, GOLDEN_QUICK_STATS_LEGACY_ALIAS)
                )
            )
            .order_by(AssistantSkillPackage.canonical_name.asc())
            .all()
        )
        for package in candidates:
            ok, sides, interrupts, reason = classify_published_package_read_only(
                db, package_id=package.id
            )
            if ok and package.published_version_id is not None:
                version = db.get(AssistantSkillVersion, package.published_version_id)
                return GoldenPackagePlan(
                    strategy="quick_stats",
                    canonical_name=package.canonical_name,
                    package_id=package.id,
                    published_version_id=package.published_version_id,
                    version_digest=str(version.version_digest) if version else None,
                    content_digest=str(version.content_digest) if version else None,
                    reason=f"quick_stats_classifiable:{reason}",
                    side_effects=sides,
                    interrupt_modes=interrupts,
                )
            if package is not None:
                # Record rejection reason for the first candidate and continue.
                continue

    if not allow_create_fixture:
        return GoldenPackagePlan(
            strategy="fixture",
            canonical_name=GOLDEN_FIXTURE_CANONICAL_NAME,
            package_id=None,
            published_version_id=None,
            version_digest=None,
            content_digest=None,
            reason="fixture_not_created",
            side_effects=(),
            interrupt_modes=(),
        )

    detail, published = create_or_refresh_fixture_package(db)
    ok, sides, interrupts, reason = classify_published_package_read_only(
        db, package_id=detail.id, version_id=published.id
    )
    if not ok:
        raise RuntimeError(f"fixture package failed read-only checks: {reason}")
    return GoldenPackagePlan(
        strategy="fixture",
        canonical_name=detail.canonical_name,
        package_id=detail.id,
        published_version_id=published.id,
        version_digest=published.version_digest,
        content_digest=published.content_digest,
        reason=f"fixture_created:{reason}",
        side_effects=sides,
        interrupt_modes=interrupts,
    )


def build_golden_profile_snapshot(
    *,
    package_id: UUID,
    base_prompt: str | None = None,
) -> MainAgentProfileSnapshotV1:
    """Build a Profile snapshot with four controls and allowlist catalog scope."""
    base = default_main_agent_profile_snapshot()
    payload = base.normalized_payload()
    payload["basePrompt"] = base_prompt or (
        "You are the MindAtlas Main Agent. Prefer direct answers for general chat. "
        "When the user asks for statistics or activity overview, search the Skill "
        "catalog and inject the golden read-only statistics Skill. Never draft or "
        "write knowledge entries in this mode. Use only declared controls and "
        "active Skill capabilities."
    )
    payload["controlCapabilityKeys"] = list(MAIN_AGENT_CONTROL_KEYS)
    payload["skillCatalogScope"] = {
        "mode": "allowlist",
        "packageIds": [str(package_id)],
    }
    # Budgets already coherent on default snapshot; keep conservative.
    payload["fallbackPolicy"] = {
        "legacyRuntimeAllowed": True,
        "beforeSideEffectsOnly": True,
    }
    return MainAgentProfileSnapshotV1.model_validate(payload)


def publish_golden_profile(
    db: Session,
    *,
    package_id: UUID,
    base_prompt: str | None = None,
) -> tuple[Any, Any]:
    """Ensure default Profile, save draft with four controls, publish. Runtime stays off."""
    profile_svc = MainAgentProfileService(db)
    profile = profile_svc.ensure_default()
    snapshot = build_golden_profile_snapshot(
        package_id=package_id, base_prompt=base_prompt
    )
    rev = int(getattr(profile, "aggregate_revision", 0) or 0)
    # ensure_default returns summary; re-read ORM for CAS revision when needed
    from app.assistant.skills.models import AssistantMainAgentProfile

    profile_row = db.get(AssistantMainAgentProfile, profile.id)
    if profile_row is not None:
        rev = int(getattr(profile_row, "aggregate_revision", 0) or 0)
    draft = profile_svc.save_draft(
        profile.id,
        SaveMainAgentProfileDraftCommand(
            snapshot=snapshot,
            version_name="plan04-golden",
            origin="api",
            expected_aggregate_revision=rev,
            request_id=f"golden-profile-draft:{profile.id}:{rev}",
        ),
    )
    published = profile_svc.publish(
        profile.id,
        PublishMainAgentProfileCommand(
            draft_version_id=draft.id,
            request_id=f"golden-profile-publish:{profile.id}:{draft.id}",
        ),
    )
    return profile_svc.get_default(), published


__all__ = [
    "GOLDEN_FIXTURE_CANONICAL_NAME",
    "GOLDEN_FIXTURE_DESCRIPTION",
    "GOLDEN_FIXTURE_DISPLAY_NAME",
    "GOLDEN_FIXTURE_READ_TOOLS",
    "GOLDEN_QUICK_STATS_CANONICAL",
    "GOLDEN_QUICK_STATS_LEGACY_ALIAS",
    "GoldenPackagePlan",
    "READ_ONLY_SIDE_EFFECTS",
    "build_fixture_mindatlas_yaml",
    "build_fixture_skill_md",
    "build_golden_profile_snapshot",
    "classify_published_package_read_only",
    "create_or_refresh_fixture_package",
    "is_read_only_behavior",
    "parse_fixture_package",
    "publish_golden_profile",
    "select_golden_package_plan",
]
