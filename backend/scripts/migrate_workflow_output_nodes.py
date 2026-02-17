from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import selectinload

from app.assistant_config.models import AssistantSkill, AssistantSkillEdge, AssistantSkillNode
from app.database import SessionLocal


@dataclass
class MigrationConflict:
    skill_id: str
    skill_name: str
    reason: str
    llm_output_nodes: list[str]


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return False


def _next_unique_node_id(existing_ids: set[str], base: str = "output_final") -> str:
    candidate = base
    index = 2
    while candidate in existing_ids:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _next_unique_edge_id(existing_ids: set[str], source_node_id: str) -> str:
    base = f"e_{source_node_id}_output"
    candidate = base
    index = 2
    while candidate in existing_ids:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _cleanup_legacy_is_output_flags(nodes: list[AssistantSkillNode]) -> bool:
    changed = False
    for node in nodes:
        if node.node_type != "llm":
            continue
        cfg = node.config if isinstance(node.config, dict) else {}
        if "isOutput" in cfg or "is_output" in cfg:
            cfg = dict(cfg)
            cfg.pop("isOutput", None)
            cfg.pop("is_output", None)
            node.config = cfg
            changed = True
    return changed


def migrate_workflow_output_nodes(*, apply_changes: bool) -> int:
    db = SessionLocal()
    migrated_count = 0
    cleaned_count = 0
    already_ok_count = 0
    conflicts: list[MigrationConflict] = []

    try:
        skills = (
            db.query(AssistantSkill)
            .options(
                selectinload(AssistantSkill.nodes),
                selectinload(AssistantSkill.edges),
            )
            .filter(AssistantSkill.langgraph_pattern == "workflow_dag")
            .all()
        )

        for skill in skills:
            nodes = list(skill.nodes or [])
            edges = list(skill.edges or [])
            output_nodes = [node for node in nodes if node.node_type == "output"]
            llm_is_output_nodes = []
            for node in nodes:
                if node.node_type != "llm":
                    continue
                cfg = node.config if isinstance(node.config, dict) else {}
                if _parse_bool(cfg.get("isOutput")) or _parse_bool(cfg.get("is_output")):
                    llm_is_output_nodes.append(node)

            changed = False
            if output_nodes:
                if _cleanup_legacy_is_output_flags(nodes):
                    cleaned_count += 1
                    changed = True
                else:
                    already_ok_count += 1
            else:
                if len(llm_is_output_nodes) == 1:
                    source_llm = llm_is_output_nodes[0]
                    existing_node_ids = {node.node_id for node in nodes}
                    existing_edge_ids = {edge.edge_id for edge in edges}
                    output_node_id = _next_unique_node_id(existing_node_ids)
                    output_edge_id = _next_unique_edge_id(existing_edge_ids, source_llm.node_id)

                    source_cfg = source_llm.config if isinstance(source_llm.config, dict) else {}
                    source_cfg = dict(source_cfg)
                    source_cfg.pop("isOutput", None)
                    source_cfg.pop("is_output", None)
                    source_llm.config = source_cfg

                    output_node = AssistantSkillNode(
                        skill_id=skill.id,
                        node_id=output_node_id,
                        node_type="output",
                        label="Output",
                        position_x=float(source_llm.position_x) + 280.0,
                        position_y=float(source_llm.position_y),
                        config={
                            "outputMode": "text",
                            "textTemplate": f"{{{{{source_llm.node_id}.response}}}}",
                        },
                    )
                    output_edge = AssistantSkillEdge(
                        skill_id=skill.id,
                        edge_id=output_edge_id,
                        source_node_id=source_llm.node_id,
                        target_node_id=output_node_id,
                        source_handle="output",
                        target_handle="input",
                    )
                    db.add(output_node)
                    db.add(output_edge)
                    migrated_count += 1
                    changed = True
                else:
                    conflicts.append(
                        MigrationConflict(
                            skill_id=str(skill.id),
                            skill_name=skill.name,
                            reason="expected exactly one llm.isOutput=true and no output node",
                            llm_output_nodes=[node.node_id for node in llm_is_output_nodes],
                        )
                    )

            if changed:
                skill.workflow_version = (skill.workflow_version or 0) + 1

        if apply_changes:
            db.commit()
        else:
            db.rollback()

    finally:
        db.close()

    print("=== Workflow Output Node Migration ===")
    print(f"migrated: {migrated_count}")
    print(f"cleaned_legacy_flags: {cleaned_count}")
    print(f"already_ok: {already_ok_count}")
    print(f"conflicts: {len(conflicts)}")
    if conflicts:
        print("\n--- Conflicts (manual migration required) ---")
        for conflict in conflicts:
            print(
                f"- skill={conflict.skill_name} ({conflict.skill_id}) "
                f"reason={conflict.reason} llm_is_output_nodes={conflict.llm_output_nodes}"
            )

    if conflicts:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy llm.isOutput workflows to dedicated output nodes.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist migration changes. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()
    return migrate_workflow_output_nodes(apply_changes=bool(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
