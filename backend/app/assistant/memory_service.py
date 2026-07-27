from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.models import (
    AssistantConversationL1Memory,
    AssistantConversationSkillL2Memory,
    AssistantConversationWorkflowCallMemory,
)

DEFAULT_L2_MEMORY_NAMESPACE = "default"


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
        # Plan 09 Task 4: hard tripwire when Eval scope reaches production L1 writer.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer("L1MemoryWriter")
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

    @classmethod
    def normalize_workflow_call_scope_memory(
        cls,
        value: object,
        *,
        max_chars: int,
        max_items: int,
    ) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        summary = cls.truncate_summary(
            str(raw.get("conversation_summary", raw.get("conversationSummary", "")) or "").strip(),
            max_chars=max_chars,
        )
        facts = cls.normalize_l2_facts(
            raw.get("skill_facts", raw.get("skillFacts", [])),
            max_items=max_items,
        )
        return {
            "conversationSummary": summary,
            "skillFacts": facts,
        }

    @classmethod
    def normalize_workflow_call_scopes(
        cls,
        value: object,
        *,
        max_chars: int,
        max_items: int,
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_key, raw_scope in value.items():
            scope_key = str(raw_key or "").strip()
            if not scope_key:
                continue
            scope = cls.normalize_workflow_call_scope_memory(
                raw_scope,
                max_chars=max_chars,
                max_items=max_items,
            )
            if scope["conversationSummary"] or scope["skillFacts"]:
                normalized[scope_key] = scope
        return normalized

    def _resolve_l2_package_target(
        self, skill_name: str
    ) -> tuple[UUID, str, str] | None:
        """Resolve lookup name to (package_id, namespace, display_name).

        Deploy B2 requires package identity; unmapped names return None and
        callers must not create name-only L2 rows.
        """
        try:
            from app.assistant.migration.l2 import (
                L2MigrationError,
                resolve_l2_package_mapping,
            )
        except Exception:  # noqa: BLE001
            return None
        try:
            mapping = resolve_l2_package_mapping(self.db, skill_name)
        except L2MigrationError:
            return None
        except Exception:  # noqa: BLE001
            return None
        if mapping is None:
            return None
        return (
            mapping.skill_package_id,
            mapping.memory_namespace,
            mapping.skill_name,
        )

    def _get_l2_row_by_package(
        self,
        conversation_id: UUID,
        skill_package_id: UUID,
        memory_namespace: str,
    ) -> AssistantConversationSkillL2Memory | None:
        ns = str(memory_namespace or "").strip() or DEFAULT_L2_MEMORY_NAMESPACE
        return (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == conversation_id,
                AssistantConversationSkillL2Memory.skill_package_id == skill_package_id,
                AssistantConversationSkillL2Memory.memory_namespace == ns,
            )
            .first()
        )

    def resolve_l2_row(
        self, conversation_id: UUID, skill_name: str
    ) -> AssistantConversationSkillL2Memory | None:
        """Resolve by package triple after name→package mapping."""
        normalized_skill_name = str(skill_name or "").strip()
        if not normalized_skill_name:
            return None
        target = self._resolve_l2_package_target(normalized_skill_name)
        if target is None:
            return None
        package_id, namespace, _display = target
        return self._get_l2_row_by_package(conversation_id, package_id, namespace)

    def get_l2_facts(self, conversation_id: UUID, skill_name: str) -> list[str]:
        """Name-keyed API that reads package-triple L2 only.

        ``skill_name`` is a lookup key resolved to package identity; unmapped
        names return empty facts (no legacy name column remains).
        """
        normalized_skill_name = str(skill_name or "").strip()
        if not normalized_skill_name:
            return []
        row = self.resolve_l2_row(conversation_id, normalized_skill_name)
        if row is None:
            return []
        return self.normalize_l2_facts(row.facts, max_items=10000)

    def upsert_l2_facts(self, conversation_id: UUID, skill_name: str, facts: list[str]) -> None:
        """Name-keyed write that stores only package/namespace identity.

        ``skill_name`` is resolved to a package; unmapped names are no-ops so
        callers cannot invent name-only rows after skill_name column removal.
        """
        # Plan 09 Task 4: hard tripwire when Eval scope reaches production L2 writer.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer("L2MemoryWriter")
        normalized_skill_name = str(skill_name or "").strip()
        normalized_facts = self.normalize_l2_facts(facts, max_items=10000)
        if not normalized_skill_name:
            return

        target = self._resolve_l2_package_target(normalized_skill_name)
        if target is None:
            # Deploy B2: no skill_name column / no pure-legacy L2 identity.
            return

        package_id, namespace, _display_name = target
        ns = str(namespace or "").strip() or DEFAULT_L2_MEMORY_NAMESPACE
        row = self._get_l2_row_by_package(conversation_id, package_id, ns)
        if row is None:
            row = AssistantConversationSkillL2Memory(
                conversation_id=conversation_id,
                facts=normalized_facts,
                version=1,
                skill_package_id=package_id,
                memory_namespace=ns,
            )
            self.db.add(row)
        else:
            if list(row.facts or []) != normalized_facts:
                row.version = int(row.version or 1) + 1
            row.facts = normalized_facts
            row.skill_package_id = package_id
            row.memory_namespace = ns
        self.db.commit()

    def get_workflow_call_memory(
        self,
        *,
        conversation_id: UUID,
        source_workflow_id: UUID,
        source_node_scope: str,
        target_workflow_id: UUID,
    ) -> dict[str, Any]:
        scope = str(source_node_scope or "").strip()
        if not scope:
            return {"conversationSummary": "", "skillFacts": []}
        row = (
            self.db.query(AssistantConversationWorkflowCallMemory)
            .filter(
                AssistantConversationWorkflowCallMemory.conversation_id == conversation_id,
                AssistantConversationWorkflowCallMemory.source_workflow_id == source_workflow_id,
                AssistantConversationWorkflowCallMemory.source_node_scope == scope,
                AssistantConversationWorkflowCallMemory.target_workflow_id == target_workflow_id,
            )
            .first()
        )
        if row is None:
            return {"conversationSummary": "", "skillFacts": []}
        return {
            "conversationSummary": str(row.summary_text or "").strip(),
            "skillFacts": self.normalize_l2_facts(row.facts, max_items=10000),
        }

    def upsert_workflow_call_memory(
        self,
        *,
        conversation_id: UUID,
        source_workflow_id: UUID,
        source_node_scope: str,
        target_workflow_id: UUID,
        summary_text: str,
        facts: list[str],
    ) -> None:
        scope = str(source_node_scope or "").strip()
        if not scope:
            return
        normalized_summary = str(summary_text or "").strip()
        normalized_facts = self.normalize_l2_facts(facts, max_items=10000)
        row = (
            self.db.query(AssistantConversationWorkflowCallMemory)
            .filter(
                AssistantConversationWorkflowCallMemory.conversation_id == conversation_id,
                AssistantConversationWorkflowCallMemory.source_workflow_id == source_workflow_id,
                AssistantConversationWorkflowCallMemory.source_node_scope == scope,
                AssistantConversationWorkflowCallMemory.target_workflow_id == target_workflow_id,
            )
            .first()
        )
        if row is None:
            row = AssistantConversationWorkflowCallMemory(
                conversation_id=conversation_id,
                source_workflow_id=source_workflow_id,
                source_node_scope=scope,
                target_workflow_id=target_workflow_id,
                summary_text=normalized_summary,
                facts=normalized_facts,
                version=1,
            )
            self.db.add(row)
        else:
            if str(row.summary_text or "").strip() != normalized_summary or list(row.facts or []) != normalized_facts:
                row.version = int(row.version or 1) + 1
            row.summary_text = normalized_summary
            row.facts = normalized_facts
        self.db.commit()
