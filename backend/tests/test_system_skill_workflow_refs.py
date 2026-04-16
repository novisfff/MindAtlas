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

        self._assert_branch_targets_have_y_offset(SMART_CAPTURE, "if_has_candidates")
        self._assert_branch_targets_have_y_offset(SMART_CAPTURE, "if_triage_route")

    def test_smart_capture_guided_merge_topology_and_bindings(self) -> None:
        from app.assistant.skill_catalog.definitions import SMART_CAPTURE

        node_map = {n.node_id: n for n in (SMART_CAPTURE.workflow_nodes or [])}
        edges = list(SMART_CAPTURE.workflow_edges or [])

        self.assertIn("llm_prepare_lookup", node_map)
        self.assertIn("tool_search_similar", node_map)
        self.assertIn("llm_rank_candidates", node_map)
        self.assertIn("if_has_candidates", node_map)
        self.assertIn("human_triage", node_map)
        self.assertIn("if_triage_route", node_map)
        self.assertIn("llm_materialize", node_map)
        self.assertIn("if_write_mode", node_map)
        self.assertIn("code_prepare_write_payload", node_map)
        self.assertIn("human_confirm_write", node_map)
        self.assertIn("if_persist_route", node_map)
        self.assertIn("call_relation_followup", node_map)
        self.assertIn("llm_finalize_reply", node_map)
        self.assertIn("output_final", node_map)
        self.assertNotIn("code_candidates", node_map)
        self.assertNotIn("tool_search_primary", node_map)
        self.assertNotIn("tool_search_secondary", node_map)
        self.assertNotIn("llm_duplicate_notice", node_map)
        self.assertNotIn("llm_materialize_direct", node_map)
        self.assertNotIn("llm_materialize_create", node_map)
        self.assertNotIn("llm_materialize_merge", node_map)
        self.assertNotIn("human_confirm_create_direct", node_map)
        self.assertNotIn("human_confirm_create", node_map)
        self.assertNotIn("human_confirm_merge", node_map)
        self.assertNotIn("human_confirm_relations_direct", node_map)
        self.assertNotIn("human_confirm_relations_create", node_map)
        self.assertNotIn("human_confirm_relations_merge", node_map)
        self.assertNotIn("code_normalize_persisted", node_map)
        self.assertNotIn("tool_relation_recs", node_map)
        self.assertNotIn("iter_relation_details", node_map)
        self.assertNotIn("if_relation_candidates", node_map)
        self.assertNotIn("human_confirm_relations", node_map)
        self.assertNotIn("if_selected_relations", node_map)
        self.assertNotIn("iter_create_relations", node_map)

        start_cfg = node_map["start"].config if isinstance(node_map["start"].config, dict) else {}
        self.assertEqual(start_cfg.get("memoryMode"), "off")

        has_candidates_targets = {
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "if_has_candidates"
        }
        self.assertEqual(has_candidates_targets, {"human_triage", "llm_materialize"})

        triage_targets = {
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "human_triage"
        }
        self.assertEqual(triage_targets, {"output_triage_cancelled", "if_triage_route"})

        triage_route_targets = {
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "if_triage_route"
        }
        self.assertEqual(
            triage_route_targets,
            {"output_merge_target_required", "llm_materialize"},
        )

        materialize_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "llm_materialize"
        ]
        self.assertEqual(materialize_targets, ["if_write_mode"])

        write_mode_targets = {
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "if_write_mode"
        }
        self.assertEqual(write_mode_targets, {"tool_get_existing", "code_prepare_write_payload"})

        existing_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "tool_get_existing"
        ]
        self.assertEqual(existing_targets, ["llm_merge_rewrite"])

        merge_rewrite_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "llm_merge_rewrite"
        ]
        self.assertEqual(merge_rewrite_targets, ["code_prepare_write_payload"])

        lookup_cfg = node_map["llm_prepare_lookup"].config if isinstance(node_map["llm_prepare_lookup"].config, dict) else {}
        output_fields = lookup_cfg.get("outputFields") or []
        output_names = {
            str(item.get("name", "")).strip()
            for item in output_fields
            if isinstance(item, dict)
        }
        self.assertIn("lookup_query", output_names)
        self.assertIn("same_record_clues", output_names)

        triage_cfg = node_map["human_triage"].config if isinstance(node_map["human_triage"].config, dict) else {}
        triage_fields = triage_cfg.get("fields") or []
        self.assertEqual(len(triage_fields), 2)
        self.assertEqual(triage_fields[0].get("name"), "action")
        self.assertEqual(triage_fields[0].get("widget"), "radio")
        self.assertEqual(triage_fields[1].get("name"), "merge_target")
        self.assertEqual(triage_fields[1].get("widget"), "radio")
        self.assertEqual(triage_fields[1].get("optionsTemplate"), "{{llm_rank_candidates.merge_target_options}}")

        confirm_cfg = node_map["human_confirm_write"].config if isinstance(node_map["human_confirm_write"].config, dict) else {}
        self.assertEqual(confirm_cfg.get("title"), "{{code_prepare_write_payload.confirm_title}}")
        self.assertEqual(confirm_cfg.get("approveLabel"), "{{code_prepare_write_payload.approve_label}}")

        approved_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "human_confirm_write" and str(edge.source_handle or "").strip().lower() == "approved"
        ]
        self.assertEqual(approved_targets, ["if_persist_route"])

        create_cfg = node_map["tool_create"].config if isinstance(node_map["tool_create"].config, dict) else {}
        create_input_bindings = create_cfg.get("inputBindings")
        self.assertIsInstance(create_input_bindings, dict)
        for field_name in {"title", "summary", "content", "type_code", "tags", "time_mode", "time_at", "time_from", "time_to"}:
            self.assertEqual(create_input_bindings.get(field_name), f"{{{{human_confirm_write.{field_name}}}}}")

        update_cfg = node_map["tool_update"].config if isinstance(node_map["tool_update"].config, dict) else {}
        update_bindings = update_cfg.get("inputBindings")
        self.assertIsInstance(update_bindings, dict)
        self.assertEqual(update_bindings.get("entry_id"), "{{code_prepare_write_payload.affected_entry_id}}")
        for field_name in {"title", "summary", "content", "type_code", "tags", "time_mode", "time_at", "time_from", "time_to"}:
            self.assertEqual(update_bindings.get(field_name), f"{{{{human_confirm_write.{field_name}}}}}")

        relation_call_cfg = (
            node_map["call_relation_followup"].config
            if isinstance(node_map["call_relation_followup"].config, dict)
            else {}
        )
        self.assertEqual(relation_call_cfg.get("targetSystemAssetKey"), "smart_capture_relation_followup")
        self.assertEqual(
            relation_call_cfg.get("exposedOutputFields"),
            ["relation_status", "relation_candidate_count", "relation_created_count"],
        )
        relation_call_bindings = relation_call_cfg.get("inputBindings") or {}
        self.assertEqual(relation_call_bindings.get("action"), "{{code_prepare_write_payload.action}}")
        self.assertEqual(relation_call_bindings.get("create_id"), "{{tool_create.id}}")
        self.assertEqual(relation_call_bindings.get("update_id"), "{{tool_update.id}}")
        self.assertEqual(relation_call_bindings.get("confirmed_title"), "{{human_confirm_write.title}}")

        relation_followup_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "call_relation_followup"
        ]
        self.assertEqual(relation_followup_targets, ["llm_finalize_reply"])

        finalize_cfg = node_map["llm_finalize_reply"].config if isinstance(node_map["llm_finalize_reply"].config, dict) else {}
        self.assertEqual(node_map["llm_finalize_reply"].node_type, "llm")
        self.assertEqual(finalize_cfg.get("outputMode"), "text")
        finalize_user_input = str(finalize_cfg.get("userInput") or "")
        finalize_prompt = str(finalize_cfg.get("systemPrompt") or "")
        self.assertIn("{{start.user_input}}", finalize_user_input)
        self.assertIn("{{llm_rank_candidates.candidate_count}}", finalize_user_input)
        self.assertIn("{{call_relation_followup.relation_status}}", finalize_user_input)
        self.assertIn("自然", finalize_prompt)
        self.assertIn("不要输出字段名", finalize_prompt)

        finalize_targets = [
            edge.target_node_id
            for edge in edges
            if edge.source_node_id == "llm_finalize_reply"
        ]
        self.assertEqual(finalize_targets, ["output_final"])

    def test_smart_capture_prompts_include_materialization_and_duplicate_guardrails(self) -> None:
        from app.assistant.skill_catalog.definitions import SMART_CAPTURE

        node_map = {n.node_id: n for n in (SMART_CAPTURE.workflow_nodes or [])}

        lookup_cfg = node_map["llm_prepare_lookup"].config if isinstance(node_map["llm_prepare_lookup"].config, dict) else {}
        materialize_cfg = node_map["llm_materialize"].config if isinstance(node_map["llm_materialize"].config, dict) else {}
        merge_cfg = node_map["llm_merge_rewrite"].config if isinstance(node_map["llm_merge_rewrite"].config, dict) else {}
        finalize_cfg = node_map["llm_finalize_reply"].config if isinstance(node_map["llm_finalize_reply"].config, dict) else {}

        lookup_prompt = str(lookup_cfg.get("systemPrompt") or "")
        materialize_prompt = str(materialize_cfg.get("systemPrompt") or "")
        merge_prompt = str(merge_cfg.get("systemPrompt") or "")
        finalize_prompt = str(finalize_cfg.get("systemPrompt") or "")
        finalize_user_input = str(finalize_cfg.get("userInput") or "")

        self.assertIn("稳定主体", lookup_prompt)
        self.assertIn("same_record_clues", lookup_prompt)
        self.assertIn("selected_action", materialize_prompt)
        self.assertIn("待合并的新信息草稿", materialize_prompt)
        self.assertIn("不要使用对话历史", materialize_prompt)
        self.assertIn("只能输出 YYYY-MM-DD", materialize_prompt)
        self.assertIn("默认今天", merge_prompt)
        self.assertIn("稳定时间表达", merge_prompt)
        self.assertIn("只能输出 YYYY-MM-DD", merge_prompt)
        self.assertIn("original_input", finalize_prompt)
        self.assertIn("不要机械分段罗列", finalize_prompt)
        self.assertIn("{{start.user_input}}", finalize_user_input)
        self.assertIn("{{call_relation_followup.relation_created_count}}", finalize_user_input)

    def test_smart_capture_write_payload_normalizes_dates_for_human_confirm(self) -> None:
        from app.assistant.skill_catalog.definitions import SMART_CAPTURE

        node_map = {n.node_id: n for n in (SMART_CAPTURE.workflow_nodes or [])}
        payload_node = node_map["code_prepare_write_payload"]
        payload_cfg = payload_node.config if isinstance(payload_node.config, dict) else {}
        code = str(payload_cfg.get("code") or "")

        namespace: dict[str, object] = {}
        exec(code, namespace)
        main = namespace["main"]

        result = main(
            candidate_count=0,
            triage_action="create_new",
            create_title="记录标题",
            create_summary="摘要",
            create_content="正文",
            create_type_code="KNOWLEDGE",
            create_tags=["tag-a"],
            create_time_mode="POINT",
            create_time_at="2026-04-16T00:00:00+00:00",
            create_time_from=None,
            create_time_to="null",
        )

        self.assertEqual(result["time_at"], "2026-04-16")
        self.assertEqual(result["time_from"], "")
        self.assertEqual(result["time_to"], "")

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
