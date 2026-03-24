from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.models import AssistantConversationL1Memory, AssistantConversationSkillL2Memory


class AssistantMemoryService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def truncate_summary(text: str, max_chars: int) -> str:
        value = str(text or "").strip()
        limit = max(1, int(max_chars or 1))
        if len(value) <= limit:
            return value
        return value[:limit]

    def get_l1_summary(self, conversation_id: UUID) -> str:
        row = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation_id)
            .first()
        )
        if row is None:
            return ""
        return str(row.summary_text or "").strip()

    def upsert_l1_summary(self, conversation_id: UUID, summary_text: str) -> None:
        normalized = str(summary_text or "").strip()
        row = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation_id)
            .first()
        )
        if row is None:
            row = AssistantConversationL1Memory(
                conversation_id=conversation_id,
                summary_text=normalized,
            )
            self.db.add(row)
        else:
            row.summary_text = normalized
        self.db.commit()

    @staticmethod
    def normalize_l2_facts(facts: object, *, max_items: int) -> list[str]:
        limit = max(1, int(max_items or 1))
        if not isinstance(facts, list):
            return []
        seen: set[str] = set()
        normalized: list[str] = []
        for item in facts:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
            if len(normalized) >= limit:
                break
        return normalized

    @staticmethod
    def render_l2_text(facts: list[str]) -> str:
        normalized = [str(item).strip() for item in facts if str(item).strip()]
        if not normalized:
            return ""
        return "\n".join(f"- {item}" for item in normalized)

    def get_l2_facts(self, conversation_id: UUID, skill_name: str) -> list[str]:
        normalized_skill_name = str(skill_name or "").strip()
        if not normalized_skill_name:
            return []
        row = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == conversation_id,
                AssistantConversationSkillL2Memory.skill_name == normalized_skill_name,
            )
            .first()
        )
        if row is None:
            return []
        return self.normalize_l2_facts(row.facts, max_items=10000)

    def upsert_l2_facts(self, conversation_id: UUID, skill_name: str, facts: list[str]) -> None:
        normalized_skill_name = str(skill_name or "").strip()
        normalized_facts = self.normalize_l2_facts(facts, max_items=10000)
        if not normalized_skill_name:
            return
        row = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == conversation_id,
                AssistantConversationSkillL2Memory.skill_name == normalized_skill_name,
            )
            .first()
        )
        if row is None:
            row = AssistantConversationSkillL2Memory(
                conversation_id=conversation_id,
                skill_name=normalized_skill_name,
                facts=normalized_facts,
                version=1,
            )
            self.db.add(row)
        else:
            if list(row.facts or []) != normalized_facts:
                row.version = int(row.version or 1) + 1
            row.facts = normalized_facts
        self.db.commit()
