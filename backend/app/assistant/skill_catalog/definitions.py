"""System skill definitions loaded from JSON defaults."""
from __future__ import annotations

from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME, SkillDefinition
from app.assistant.skill_catalog.defaults_loader import (
    get_system_skill_default,
    load_system_skill_defaults,
)
from app.system_settings.service import get_default_system_locale


def _require_system_skill(name: str) -> SkillDefinition:
    skill = get_system_skill_default(name)
    if skill is None:
        raise RuntimeError(f"Missing required system skill default: {name}")
    return skill


SKILLS: list[SkillDefinition] = load_system_skill_defaults(locale=get_default_system_locale())

# Compatibility exports for existing imports/tests.
QUICK_STATS = _require_system_skill("quick_stats")
SMART_CAPTURE = _require_system_skill("smart_capture")
PERIODIC_REVIEW = _require_system_skill("periodic_review")
GENERAL_CHAT = _require_system_skill(DEFAULT_SKILL_NAME)


def get_skill_by_name(name: str, locale: str | None = None) -> SkillDefinition | None:
    return get_system_skill_default(name, locale=locale)
