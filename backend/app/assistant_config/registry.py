from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.assistant_config.models import AssistantSkill, AssistantTool
from app.assistant_config.remote_tool import RemoteTool


@dataclass(frozen=True)
class SystemToolDefinition:
    name: str
    description: str


class ToolRegistry:
    """工具注册表 - 解析系统本地工具和数据库自定义工具"""

    @staticmethod
    def list_system_tools() -> list[SystemToolDefinition]:
        from app.assistant import tools as assistant_tools

        results: list[SystemToolDefinition] = []
        for tool_name in getattr(assistant_tools, "__all__", []):
            tool_obj = getattr(assistant_tools, tool_name, None)
            description = ""
            if tool_obj is not None:
                description = (
                    getattr(tool_obj, "description", None)
                    or getattr(tool_obj, "__doc__", "")
                    or ""
                ).strip()
            results.append(SystemToolDefinition(name=tool_name, description=description))
        return results

    @staticmethod
    def resolve_system_tool(tool_name: str) -> Any | None:
        from app.assistant import tools as assistant_tools
        return getattr(assistant_tools, tool_name, None)

    def __init__(self, db: Session):
        self.db = db

    def resolve(self, tool_name: str) -> Any | None:
        """解析工具 - 优先从数据库查找，正确处理禁用状态

        逻辑:
        1. 先查询数据库中是否存在该工具（不管启用状态）
        2. 如果存在且被禁用，返回 None（不回退到系统工具）
        3. 如果存在且启用，返回对应工具
        4. 如果不存在，回退到系统工具
        """
        # 先查询是否存在该工具（不过滤 enabled）
        record = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.name == tool_name)
            .first()
        )

        if record:
            # 工具存在于数据库
            if not record.enabled:
                # 工具被禁用，返回 None（不回退到系统工具）
                return None
            # 工具启用
            if (record.kind or "").lower() == "remote":
                return RemoteTool.from_model(record)
            return self.resolve_system_tool(tool_name)

        # 工具不在数据库中，回退到系统工具
        return self.resolve_system_tool(tool_name)


class SkillRegistry:
    """技能注册表 - 解析系统技能和数据库自定义技能"""

    @staticmethod
    def list_system_skills() -> list[Any]:
        from app.assistant.skills.definitions import SKILLS
        return list(SKILLS)

    def __init__(self, db: Session):
        self.db = db

    def list_enabled_db_skills(self, include_steps: bool = False) -> list[AssistantSkill]:
        """获取启用的数据库 Skills

        Args:
            include_steps: 是否预加载 steps（路由阶段不需要，执行阶段需要）
        """
        query = (
            self.db.query(AssistantSkill)
            .filter(AssistantSkill.enabled.is_(True))
        )
        if include_steps:
            query = query.options(joinedload(AssistantSkill.steps))
        return query.order_by(AssistantSkill.created_at.desc()).all()
