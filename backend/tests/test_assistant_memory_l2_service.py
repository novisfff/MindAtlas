from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


def _make_package(db, name: str, aliases: list[str] | None = None):
    from app.assistant.skills.contracts import normalize_skill_lookup_name
    from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillPackageAlias

    pkg = AssistantSkillPackage(
        canonical_name=name,
        display_name=name.replace("-", " ").title(),
        description=f"package {name}",
        migration_state="cutover",
        catalog_enabled=False,
        is_system=name in {"quick-stats", "smart-capture", "periodic-review"},
    )
    db.add(pkg)
    db.flush()
    db.add(
        AssistantSkillPackageAlias(
            skill_package_id=pkg.id,
            alias=name,
            normalized_alias=normalize_skill_lookup_name(name),
            alias_type="canonical",
        )
    )
    for alias in aliases or []:
        db.add(
            AssistantSkillPackageAlias(
                skill_package_id=pkg.id,
                alias=alias,
                normalized_alias=normalize_skill_lookup_name(alias),
                alias_type="legacy",
            )
        )
    db.commit()
    db.refresh(pkg)
    return pkg


class AssistantMemoryL2ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation  # noqa: E402

        self.conv = Conversation(title="l2")
        self.db.add(self.conv)
        self.db.commit()
        self.db.refresh(self.conv)
        self.smart = _make_package(self.db, "smart-capture", aliases=["smart_capture"])
        self.quick = _make_package(self.db, "quick-stats", aliases=["quick_stats"])

    def tearDown(self) -> None:
        self.db.close()

    def test_get_l2_facts_returns_empty_when_not_exists(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402

        service = AssistantMemoryService(self.db)
        self.assertEqual(service.get_l2_facts(self.conv.id, "smart_capture"), [])

    def test_upsert_l2_facts_creates_and_updates_single_row(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402
        from app.assistant.models import AssistantConversationSkillL2Memory  # noqa: E402

        service = AssistantMemoryService(self.db)
        service.upsert_l2_facts(self.conv.id, "smart_capture", ["A", "B"])
        service.upsert_l2_facts(self.conv.id, "smart_capture", ["A", "C"])

        rows = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(
                AssistantConversationSkillL2Memory.conversation_id == self.conv.id,
                AssistantConversationSkillL2Memory.skill_package_id == self.smart.id,
            )
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].facts, ["A", "C"])
        self.assertEqual(int(rows[0].version or 0), 2)
        self.assertEqual(rows[0].memory_namespace, "default")

    def test_upsert_l2_facts_isolated_by_skill(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402

        service = AssistantMemoryService(self.db)
        service.upsert_l2_facts(self.conv.id, "smart_capture", ["A"])
        service.upsert_l2_facts(self.conv.id, "quick_stats", ["B"])

        self.assertEqual(service.get_l2_facts(self.conv.id, "smart_capture"), ["A"])
        self.assertEqual(service.get_l2_facts(self.conv.id, "quick_stats"), ["B"])

    def test_unmapped_name_is_noop(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402
        from app.assistant.models import AssistantConversationSkillL2Memory  # noqa: E402

        service = AssistantMemoryService(self.db)
        service.upsert_l2_facts(self.conv.id, "totally_unknown_skill_zz", ["X"])
        count = (
            self.db.query(AssistantConversationSkillL2Memory)
            .filter(AssistantConversationSkillL2Memory.conversation_id == self.conv.id)
            .count()
        )
        self.assertEqual(count, 0)
        self.assertEqual(service.get_l2_facts(self.conv.id, "totally_unknown_skill_zz"), [])

    def test_normalize_l2_facts_dedup_and_max_items(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402

        normalized = AssistantMemoryService.normalize_l2_facts(
            ["", "A", "A", " B ", 123, "C"],
            max_items=2,
        )
        self.assertEqual(normalized, ["A", "B"])

    def test_render_l2_text_formats_bullets(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402

        rendered = AssistantMemoryService.render_l2_text(["A", "B"])
        self.assertEqual(rendered, "- A\n- B")

    def test_upsert_workflow_call_memory_creates_and_updates_single_row(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402
        from app.assistant.models import AssistantConversationWorkflowCallMemory  # noqa: E402
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402

        source_workflow = AssistantWorkflow(
            name="source",
            description="source",
            enabled=True,
            is_system=False,
            workflow_version=1,
        )
        target_workflow = AssistantWorkflow(
            name="target",
            description="target",
            enabled=True,
            is_system=False,
            workflow_version=1,
        )
        self.db.add_all([source_workflow, target_workflow])
        self.db.commit()
        self.db.refresh(source_workflow)
        self.db.refresh(target_workflow)

        service = AssistantMemoryService(self.db)
        service.upsert_workflow_call_memory(
            conversation_id=self.conv.id,
            source_workflow_id=source_workflow.id,
            source_node_scope="node-a",
            target_workflow_id=target_workflow.id,
            summary_text="sum",
            facts=["f1"],
        )
        service.upsert_workflow_call_memory(
            conversation_id=self.conv.id,
            source_workflow_id=source_workflow.id,
            source_node_scope="node-a",
            target_workflow_id=target_workflow.id,
            summary_text="sum2",
            facts=["f2"],
        )
        rows = (
            self.db.query(AssistantConversationWorkflowCallMemory)
            .filter(
                AssistantConversationWorkflowCallMemory.conversation_id == self.conv.id
            )
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].summary_text, "sum2")
        self.assertEqual(rows[0].facts, ["f2"])


if __name__ == "__main__":
    unittest.main()
