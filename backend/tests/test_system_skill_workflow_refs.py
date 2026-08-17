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

    def test_smart_capture_golden_create_topology_and_bindings(self) -> None:
        from app.assistant.skill_catalog.definitions import SMART_CAPTURE

        node_map = {n.node_id: n for n in (SMART_CAPTURE.workflow_nodes or [])}
        edges = list(SMART_CAPTURE.workflow_edges or [])

        self.assertEqual(set(node_map), {"start", "llm_prepare_create", "tool_create", "output_final"})
        self.assertEqual(
            [(edge.source_node_id, edge.target_node_id) for edge in edges],
            [
                ("start", "llm_prepare_create"),
                ("llm_prepare_create", "tool_create"),
                ("tool_create", "output_final"),
            ],
        )
        self.assertTrue(all(node.node_type not in {"human_in_loop", "workflow_call"} for node in node_map.values()))

        start_cfg = node_map["start"].config if isinstance(node_map["start"].config, dict) else {}
        self.assertEqual(start_cfg.get("memoryMode"), "off")

        prepare_cfg = (
            node_map["llm_prepare_create"].config
            if isinstance(node_map["llm_prepare_create"].config, dict)
            else {}
        )
        output_fields = prepare_cfg.get("outputFields") or []
        output_names = {
            str(item.get("name", "")).strip()
            for item in output_fields
            if isinstance(item, dict)
        }
        self.assertEqual(output_names, {"title", "summary", "content", "type_code", "tags", "time_mode", "time_at"})

        create_cfg = node_map["tool_create"].config if isinstance(node_map["tool_create"].config, dict) else {}
        create_input_bindings = create_cfg.get("inputBindings")
        self.assertIsInstance(create_input_bindings, dict)
        self.assertEqual(set(create_input_bindings), output_names)
        for field_name in output_names:
            self.assertEqual(create_input_bindings.get(field_name), f"{{{{llm_prepare_create.{field_name}}}}}")

    def test_smart_capture_prompt_forbids_unsupported_writes(self) -> None:
        from app.assistant.skill_catalog.definitions import SMART_CAPTURE

        node_map = {n.node_id: n for n in (SMART_CAPTURE.workflow_nodes or [])}
        prepare_cfg = node_map["llm_prepare_create"].config if isinstance(node_map["llm_prepare_create"].config, dict) else {}
        prompt = str(prepare_cfg.get("systemPrompt") or "")

        self.assertIn("不要合并、更新或创建关系", prompt)
        self.assertIn("{{start.user_input}}", str(prepare_cfg.get("userInput") or ""))
        self.assertFalse(any(node.node_type == "code_executor" for node in node_map.values()))

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
                        if ref_node_id in {"sys", "env", "container"}:
                            continue
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

        if node_type == "iteration":
            output_variable = str(cfg.get("outputVariable", cfg.get("output_variable", "results")) or "results").strip() or "results"
            return {output_variable, "count", "errors"}

        if node_type == "workflow_call":
            fields = {"response"}
            for name in (cfg.get("exposedOutputFields") or cfg.get("exposed_output_fields") or []):
                name_text = str(name or "").strip()
                if name_text:
                    fields.add(name_text)
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
