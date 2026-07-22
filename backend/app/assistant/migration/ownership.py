"""Static import/query ownership audit for Plan 10 cleanup readiness."""

from __future__ import annotations

from app.assistant.migration.contracts import ModuleOwnershipRow, OwnerClass

# (path substring or exact marker, owner_class, subject_area, notes)
OWNERSHIP_RULES: tuple[tuple[str, OwnerClass, str, str | None], ...] = (
    (
        "assistant/orchestration/intent_router.py",
        "legacy",
        "routing",
        "Legacy intent router; Deploy B1 deletion candidate",
    ),
    (
        "assistant/orchestration/supervisor_graph.py",
        "legacy",
        "routing",
        "Legacy single-skill supervisor graph",
    ),
    (
        "assistant/orchestration/supervisor_state.py",
        "legacy",
        "routing",
        "Legacy supervisor state",
    ),
    (
        "assistant/orchestration/agent_runtime.py",
        "legacy",
        "routing",
        "Legacy agent runtime glue",
    ),
    (
        "assistant/skill_catalog/",
        "legacy",
        "catalog",
        "Legacy skill catalog definitions/loaders",
    ),
    (
        "assistant/workflow/human_approval_runtime.py",
        "legacy",
        "approval",
        "Blocking legacy HITL runtime",
    ),
    (
        "assistant_config/models.py",
        "legacy",
        "config",
        "AssistantSkill + AssistantHumanApproval ORM anchors",
    ),
    (
        "features/assistant-config/pages/SkillSettings.tsx",
        "frontend_legacy",
        "ui",
        "Legacy single-target Skill settings UI",
    ),
    (
        "assistant/main_agent/",
        "native_runtime",
        "main_agent",
        "Plan 04+ Main Agent runtime",
    ),
    (
        "assistant/skills/",
        "native_runtime",
        "skill_package",
        "Plan 01/09 universal skill package admin",
    ),
    (
        "assistant/evaluation/",
        "native_runtime",
        "evaluation",
        "Plan 09 eval/publish gates",
    ),
    (
        "assistant/capability_calls/",
        "shared_capability",
        "capability_ledger",
        "Plan 08 capability call ledger",
    ),
    (
        "assistant/durable/",
        "shared_capability",
        "durable_run",
        "Plan 06 durable run foundation",
    ),
    (
        "assistant/migration/",
        "migration_tooling",
        "migration",
        "Plan 10 migration tooling",
    ),
    (
        "dynamic:importlib.import_module",
        "dynamic_composition",
        "dynamic_import",
        "Dynamic import marker for ownership tests",
    ),
    (
        "dynamic:__import__",
        "dynamic_composition",
        "dynamic_import",
        "Builtin dynamic import marker",
    ),
)


def classify_module_ownership(module_path: str) -> ModuleOwnershipRow:
    """Classify a module path against the locked ownership rule table."""
    path = module_path.replace("\\", "/")
    for pattern, owner_class, subject_area, notes in OWNERSHIP_RULES:
        if pattern in path:
            return ModuleOwnershipRow(
                module_path=module_path,
                owner_class=owner_class,
                subject_area=subject_area,
                notes=notes,
            )
    return ModuleOwnershipRow(
        module_path=module_path,
        owner_class="unknown",
        subject_area="unclassified",
        notes="No ownership rule matched; treat as B1 review item",
    )


def audit_module_paths(module_paths: list[str] | tuple[str, ...]) -> tuple[ModuleOwnershipRow, ...]:
    return tuple(classify_module_ownership(path) for path in module_paths)


__all__ = (
    "OWNERSHIP_RULES",
    "audit_module_paths",
    "classify_module_ownership",
)
