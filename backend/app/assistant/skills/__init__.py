"""Skill 机制模块

Keep imports lightweight so submodules (e.g. converters) can be used without
pulling optional runtime dependencies at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.assistant.skills.base import SkillDefinition, SkillStep
from app.assistant.skills.definitions import SKILLS, get_skill_by_name

if TYPE_CHECKING:
    from app.assistant.skills.executor import SkillExecutor
    from app.assistant.skills.router import SkillRouter

__all__ = [
    "SkillStep",
    "SkillDefinition",
    "SKILLS",
    "get_skill_by_name",
    "SkillRouter",
    "SkillExecutor",
]


def __getattr__(name: str) -> Any:
    if name == "SkillExecutor":
        from app.assistant.skills.executor import SkillExecutor as _SkillExecutor

        return _SkillExecutor
    if name == "SkillRouter":
        from app.assistant.skills.router import SkillRouter as _SkillRouter

        return _SkillRouter
    raise AttributeError(name)
