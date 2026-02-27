from __future__ import annotations

import re
import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")


class SystemSkillWorkflowReferenceTests(unittest.TestCase):
    def test_system_default_workflows_are_horizontal_layout(self) -> None:
        from app.assistant.skill_catalog.definitions import PERIODIC_REVIEW, QUICK_STATS, SMART_CAPTURE

        for skill in (QUICK_STATS, SMART_CAPTURE, PERIODIC_REVIEW):
            node_map = {n.node_id: n for n in (skill.workflow_nodes or [])}
            self.assertTrue(node_map, f"{skill.name} should define workflow nodes")

            for edge in skill.workflow_edges or []:
                source = node_map.get(edge.source_node_id)
                target = node_map.get(edge.target_node_id)
                self.assertIsNotNone(source, f"{skill.name} missing source node: {edge.source_node_id}")
                self.assertIsNotNone(target, f"{skill.name} missing target node: {edge.target_node_id}")
                self.assertGreater(
                    target.position_x,
                    source.position_x,
                    f"{skill.name} edge {edge.edge_id} should flow left-to-right",
                )

        self._assert_branch_targets_have_y_offset(SMART_CAPTURE, "start")
        self._assert_branch_targets_have_y_offset(SMART_CAPTURE, "human_confirm")
        self._assert_branch_targets_have_y_offset(PERIODIC_REVIEW, "llm_dates")

    def test_smart_capture_human_in_loop_topology_and_bindings(self) -> None:
        from app.assistant.skill_catalog.definitions import SMART_CAPTURE

        node_map = {n.node_id: n for n in (SMART_CAPTURE.workflow_nodes or [])}
        edges = list(SMART_CAPTURE.workflow_edges or [])

        self.assertIn("human_confirm", node_map)
        self.assertEqual(node_map["human_confirm"].node_type, "human_in_loop")

        llm_time_targets = {
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "llm_time"
        }
        self.assertEqual(llm_time_targets, {"human_confirm"})

        approved_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "human_confirm" and str(edge.source_handle or "").strip().lower() == "approved"
        ]
        rejected_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "human_confirm" and str(edge.source_handle or "").strip().lower() == "rejected"
        ]
        self.assertEqual(approved_targets, ["tool_create"])
        self.assertEqual(len(rejected_targets), 1)
        self.assertNotEqual(rejected_targets[0], "tool_create")

        tool_create = node_map.get("tool_create")
        self.assertIsNotNone(tool_create)
        cfg = tool_create.config if isinstance(tool_create.config, dict) else {}
        input_bindings = cfg.get("inputBindings")
        self.assertIsInstance(input_bindings, dict)
        expected_fields = {
            "title",
            "summary",
            "content",
            "type_code",
            "tags",
            "time_mode",
            "time_at",
            "time_from",
            "time_to",
        }
        self.assertEqual(set(input_bindings.keys()), expected_fields)
        for field_name in expected_fields:
            self.assertEqual(input_bindings.get(field_name), f"{{{{human_confirm.{field_name}}}}}")

    def test_system_workflow_references_match_node_output_contracts(self) -> None:
        from app.assistant.skill_catalog.definitions import SKILLS
        from app.assistant_config.registry import ToolRegistry

        output_map = ToolRegistry.SYSTEM_TOOL_OUTPUT_PARAMS
        errors: list[str] = []

        for skill in SKILLS:
            if skill.mode != "langgraph" or skill.langgraph_pattern != "workflow_dag":
                continue

            node_map = {n.node_id: n for n in (skill.workflow_nodes or [])}

            for node in (skill.workflow_nodes or []):
                cfg = node.config if isinstance(node.config, dict) else {}
                text_fields: list[tuple[str, str]] = []

                for key in (
                    "systemPrompt",
                    "system_prompt",
                    "userInput",
                    "user_input",
                    "textTemplate",
                    "text_template",
                    "template",
                    "instruction",
                    "argsTemplate",
                    "args_template",
                ):
                    value = cfg.get(key)
                    if isinstance(value, str):
                        text_fields.append((key, value))

                input_bindings = cfg.get("inputBindings")
                if isinstance(input_bindings, dict):
                    for k, v in input_bindings.items():
                        if isinstance(v, str):
                            text_fields.append((f"inputBindings.{k}", v))

                output_fields = cfg.get("outputFields")
                if isinstance(output_fields, list):
                    for index, item in enumerate(output_fields):
                        if not isinstance(item, dict):
                            continue
                        value = item.get("value")
                        if isinstance(value, str):
                            text_fields.append((f"outputFields[{index}].value", value))

                for key, text in text_fields:
                    for m in _VAR_RE.finditer(text):
                        ref_node_id, ref_field = m.group(1), m.group(2)
                        ref_node = node_map.get(ref_node_id)
                        if ref_node is None:
                            errors.append(
                                f"{skill.name}:{node.node_id}:{key} references unknown node {ref_node_id}"
                            )
                            continue
                        allowed = self._allowed_fields(ref_node, output_map)
                        if ref_field not in allowed:
                            errors.append(
                                f"{skill.name}:{node.node_id}:{key} invalid ref {ref_node_id}.{ref_field}; "
                                f"allowed={sorted(allowed)}"
                            )

        self.assertFalse(errors, "\n".join(errors))

    @staticmethod
    def _allowed_fields(node, output_map: dict[str, list[dict[str, str]]]) -> set[str]:
        cfg = node.config if isinstance(node.config, dict) else {}
        node_type = getattr(node, "node_type", "")

        if node_type == "start":
            return {"user_input"}

        if node_type == "llm":
            output_mode = str(cfg.get("outputMode", "text") or "text").strip().lower()
            if output_mode == "json":
                output_mode = "structured"
            if output_mode != "structured":
                return {"response"}
            fields = {"response"}
            for item in (cfg.get("outputFields") or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if name:
                    fields.add(name)
            return fields

        if node_type == "tool":
            tool_name = str(cfg.get("toolName", "")).strip()
            names = {
                str(item.get("name", "")).strip()
                for item in output_map.get(tool_name, [])
                if isinstance(item, dict)
            }
            names.discard("")
            names.add("result")
            return names

        if node_type == "code_executor":
            fields = {"response"}
            for item in (cfg.get("outputFields") or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if name:
                    fields.add(name)
            return fields

        if node_type == "if_else":
            return {"handle"}

        if node_type == "human_in_loop":
            fields = {"response", "decision", "comment"}
            for item in (cfg.get("fields") or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if name:
                    fields.add(name)
            return fields

        if node_type == "parameter_extractor":
            names = {
                str(item.get("name", "")).strip()
                for item in (cfg.get("outputFields") or [])
                if isinstance(item, dict)
            }
            names.discard("")
            return names or {"text"}

        if node_type == "knowledge_retrieval":
            return {"result", "query", "mode", "references", "references_count"}

        if node_type == "output":
            output_mode = str(cfg.get("outputMode", "text") or "text").strip().lower()
            if output_mode == "json":
                output_mode = "structured"
            if output_mode != "structured":
                return {"response"}
            fields = {"response"}
            for item in (cfg.get("outputFields") or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if name:
                    fields.add(name)
            return fields

        return {"text"}

    def _assert_branch_targets_have_y_offset(self, skill, source_node_id: str) -> None:
        node_map = {n.node_id: n for n in (skill.workflow_nodes or [])}
        target_ys = [
            node_map[e.target_node_id].position_y
            for e in (skill.workflow_edges or [])
            if e.source_node_id == source_node_id and e.target_node_id in node_map
        ]
        self.assertGreaterEqual(
            len(target_ys),
            2,
            f"{skill.name} source {source_node_id} should have at least two branch targets",
        )
        self.assertGreater(
            len(set(target_ys)),
            1,
            f"{skill.name} branch targets from {source_node_id} should split vertically",
        )


if __name__ == "__main__":
    unittest.main()
