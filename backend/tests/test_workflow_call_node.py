from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class _FakeHumanLoopRuntime:  # patched to behave as HumanLoopRuntime proxy target
    def __init__(self) -> None:
        self._on_requested = None
        self._on_resolved = None
        self.requested_node_ids: list[str] = []

    def create_and_wait(
        self,
        *,
        node_id: str,
        node_label: str,
        request_payload: dict,
        field_schema: list[dict],
        initial_values: dict,
    ) -> dict:
        self.requested_node_ids.append(node_id)
        pending = {
            "id": "approval-1",
            "nodeId": node_id,
            "nodeLabel": node_label,
            "status": "pending",
            "requestPayload": request_payload,
            "fieldSchema": field_schema,
            "initialValues": initial_values,
            "submittedValues": {},
            "comment": None,
        }
        if callable(self._on_requested):
            self._on_requested(pending)

        resolved = {
            "id": "approval-1",
            "nodeId": node_id,
            "nodeLabel": node_label,
            "status": "approved",
            "decision": "approved",
            "submittedValues": {
                "answer": "approved-by-human",
            },
            "comment": "looks good",
        }
        if callable(self._on_resolved):
            self._on_resolved(resolved)
        return resolved


class WorkflowCallNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    @staticmethod
    def _structured_workflow_input(
        *,
        input_field: str = "name",
        output_field: str = "greeting",
        output_template: str | None = None,
    ):
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(
                    node_id="start",
                    node_type="start",
                    label="Start",
                    config={
                        "inputMode": "structured",
                        "structuredFields": [
                            {
                                "name": input_field,
                                "type": "string",
                                "required": True,
                            }
                        ],
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={
                        "outputMode": "structured",
                        "outputFields": [
                            {
                                "name": output_field,
                                "type": "string",
                                "value": output_template or f"{{{{start.{input_field}}}}}",
                            }
                        ],
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(
                    edge_id="e1",
                    source_node_id="start",
                    target_node_id="output_1",
                    source_handle="output",
                    target_handle="input",
                )
            ],
        )

    @staticmethod
    def _text_start_workflow_input():
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={"inputMode": "text"}),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={
                        "outputMode": "structured",
                        "outputFields": [
                            {
                                "name": "summary",
                                "type": "string",
                                "value": "{{start.user_input}}",
                            }
                        ],
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(
                    edge_id="e1",
                    source_node_id="start",
                    target_node_id="output_1",
                    source_handle="output",
                    target_handle="input",
                )
            ],
        )

    @staticmethod
    def _ambiguous_output_workflow_input():
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(
                    node_id="start",
                    node_type="start",
                    label="Start",
                    config={
                        "inputMode": "structured",
                        "structuredFields": [{"name": "name", "type": "string", "required": True}],
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_a",
                    node_type="output",
                    label="Output A",
                    config={
                        "outputMode": "structured",
                        "outputFields": [{"name": "first", "type": "string", "value": "{{start.name}}"}],
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_b",
                    node_type="output",
                    label="Output B",
                    config={
                        "outputMode": "structured",
                        "outputFields": [{"name": "second", "type": "string", "value": "{{start.name}}"}],
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="ea", source_node_id="start", target_node_id="output_a"),
                WorkflowEdgeInput(edge_id="eb", source_node_id="start", target_node_id="output_b"),
            ],
        )

    @staticmethod
    def _workflow_calling_input(
        *,
        target_workflow_id: str,
        target_version_id: str | None,
        binding_mode: str = "pinned",
        input_binding: str = "{{start.user_input}}",
    ):
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={"inputMode": "text"}),
                WorkflowNodeInput(
                    node_id="call_child",
                    node_type="workflow_call",
                    label="Call Child",
                    config={
                        "targetWorkflowId": target_workflow_id,
                        "bindingMode": binding_mode,
                        "targetPublishedVersionId": target_version_id,
                        "inputBindings": {"name": input_binding},
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={
                        "outputMode": "text",
                        "textTemplate": "{{call_child.response}}",
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="call_child"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="call_child", target_node_id="output_1"),
            ],
        )

    @staticmethod
    def _workflow_calling_hitl_input(*, target_workflow_id: str, target_version_id: str):
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={"inputMode": "text"}),
                WorkflowNodeInput(
                    node_id="call_child",
                    node_type="workflow_call",
                    label="Call HITL Child",
                    config={
                        "targetWorkflowId": target_workflow_id,
                        "bindingMode": "pinned",
                        "targetPublishedVersionId": target_version_id,
                        "inputBindings": {"request": "{{start.user_input}}"},
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={
                        "outputMode": "text",
                        "textTemplate": "{{call_child.response}}",
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="call_child"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="call_child", target_node_id="output_1"),
            ],
        )

    @staticmethod
    def _human_loop_child_workflow_input():
        from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

        return WorkflowInput(
            nodes=[
                WorkflowNodeInput(
                    node_id="start",
                    node_type="start",
                    label="Start",
                    config={
                        "inputMode": "structured",
                        "structuredFields": [{"name": "request", "type": "string", "required": True}],
                    },
                ),
                WorkflowNodeInput(
                    node_id="human_1",
                    node_type="human_in_loop",
                    label="Human",
                    config={
                        "title": "Approve request",
                        "instruction": "Please review",
                        "fields": [
                            {
                                "name": "answer",
                                "label": "Answer",
                                "type": "string",
                                "required": True,
                                "valueTemplate": "{{start.request}}",
                            }
                        ],
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={
                        "outputMode": "structured",
                        "outputFields": [
                            {"name": "answer", "type": "string", "value": "{{human_1.answer}}"},
                            {"name": "decision", "type": "string", "value": "{{human_1.decision}}"},
                        ],
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="human_1"),
                WorkflowEdgeInput(
                    edge_id="e2",
                    source_node_id="human_1",
                    target_node_id="output_1",
                    source_handle="approved",
                    target_handle="input",
                ),
                WorkflowEdgeInput(
                    edge_id="e3",
                    source_node_id="human_1",
                    target_node_id="output_1",
                    source_handle="rejected",
                    target_handle="input",
                ),
            ],
        )

    def _service(self):
        from app.assistant_config.service import AssistantConfigService

        return AssistantConfigService(self.db)

    def _create_workflow(self, *, name: str, workflow_input, enabled: bool = True):
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest

        return self._service().create_workflow(
            AssistantWorkflowCreateRequest(
                name=name,
                description=f"{name} description",
                enabled=enabled,
                workflow=workflow_input,
            )
        )

    def test_list_callable_workflows_returns_only_callable_published_enabled_workflows(self) -> None:
        valid = self._create_workflow(
            name="workflow_call__valid",
            workflow_input=self._structured_workflow_input(output_field="summary"),
        )
        self._create_workflow(
            name="workflow_call__disabled",
            workflow_input=self._structured_workflow_input(output_field="summary"),
            enabled=False,
        )
        self._create_workflow(
            name="workflow_call__text_start",
            workflow_input=self._text_start_workflow_input(),
        )
        self._create_workflow(
            name="workflow_call__ambiguous_output",
            workflow_input=self._ambiguous_output_workflow_input(),
        )

        items = self._service().list_callable_workflows()
        by_name = {item["name"]: item for item in items}

        self.assertIn("workflow_call__valid", by_name)
        self.assertNotIn("workflow_call__disabled", by_name)
        self.assertNotIn("workflow_call__text_start", by_name)
        self.assertNotIn("workflow_call__ambiguous_output", by_name)

        valid_item = by_name["workflow_call__valid"]
        self.assertEqual(str(valid.id), str(valid_item["id"]))
        self.assertEqual(valid_item["input_params"][0]["name"], "name")
        self.assertEqual(valid_item["output_params"][0]["name"], "summary")
        self.assertTrue(valid_item["available_versions"])

    def test_validate_workflow_dependencies_rejects_invalid_bindings_and_cycles(self) -> None:
        from app.common.exceptions import ApiException
        from app.assistant_config.schemas import AssistantWorkflowUpdateRequest

        child = self._create_workflow(
            name="workflow_call__child_for_validation",
            workflow_input=self._structured_workflow_input(),
        )
        parent = self._create_workflow(
            name="workflow_call__parent_for_validation",
            workflow_input=self._structured_workflow_input(output_field="result"),
        )
        other = self._create_workflow(
            name="workflow_call__other_for_cycle",
            workflow_input=self._structured_workflow_input(output_field="result"),
        )

        invalid_missing_binding = self._workflow_calling_input(
            target_workflow_id=str(child.id),
            target_version_id=str(child.published_version_id),
        )
        invalid_missing_binding.nodes[1].config = {
            "targetWorkflowId": str(child.id),
            "bindingMode": "pinned",
            "targetPublishedVersionId": str(child.published_version_id),
            "inputBindings": {},
        }

        with self.assertRaises(ApiException) as ctx_missing:
            self._service().validate_workflow_dependencies(
                invalid_missing_binding,
                current_workflow_id=parent.id,
            )
        self.assertEqual(ctx_missing.exception.code, 42257)

        other_calls_child = self._workflow_calling_input(
            target_workflow_id=str(child.id),
            target_version_id=str(child.published_version_id),
        )
        self._service().update_workflow_entity(
            other.id,
            AssistantWorkflowUpdateRequest(workflow=other_calls_child),
        )

        parent_calls_other = self._workflow_calling_input(
            target_workflow_id=str(other.id),
            target_version_id=str(other.published_version_id),
        )
        self._service().validate_workflow_dependencies(
            parent_calls_other,
            current_workflow_id=parent.id,
        )

        child_calls_parent = self._workflow_calling_input(
            target_workflow_id=str(parent.id),
            target_version_id=str(parent.published_version_id),
        )
        self._service().update_workflow_entity(
            child.id,
            AssistantWorkflowUpdateRequest(workflow=child_calls_parent),
        )

        with self.assertRaises(ApiException) as ctx_cycle:
            self._service().validate_workflow_dependencies(
                parent_calls_other,
                current_workflow_id=parent.id,
            )
        self.assertEqual(ctx_cycle.exception.code, 42258)
        self.assertIn("recursive dependency", ctx_cycle.exception.message)

    def test_resolve_workflow_call_target_respects_pinned_and_latest_versions(self) -> None:
        from app.assistant_config.schemas import WorkflowPublishRequest

        workflow = self._create_workflow(
            name="workflow_call__version_target",
            workflow_input=self._structured_workflow_input(output_field="first_name"),
        )
        version_v1 = workflow.published_version_id

        updated_input = self._structured_workflow_input(output_field="display_name")
        published = self._service().publish_workflow(
            workflow.id,
            WorkflowPublishRequest(
                workflow=updated_input,
                version_name="v2",
            ),
        )

        resolved_pinned = self._service()._resolve_workflow_call_target(
            target_workflow_id=workflow.id,
            binding_mode="pinned",
            target_published_version_id=version_v1,
        )
        resolved_latest = self._service()._resolve_workflow_call_target(
            target_workflow_id=workflow.id,
            binding_mode="latest",
            target_published_version_id=version_v1,
        )

        self.assertEqual(resolved_pinned.version.id, version_v1)
        self.assertEqual([field.name for field in resolved_pinned.contract.output_fields], ["first_name"])
        self.assertEqual(resolved_latest.version.id, published.published_version_id)
        self.assertEqual([field.name for field in resolved_latest.contract.output_fields], ["display_name"])

    def test_delete_protection_blocks_referenced_workflow_and_pinned_version(self) -> None:
        from app.common.exceptions import ApiException

        child = self._create_workflow(
            name="workflow_call__child_for_delete",
            workflow_input=self._structured_workflow_input(),
        )
        self._create_workflow(
            name="workflow_call__parent_for_delete",
            workflow_input=self._workflow_calling_input(
                target_workflow_id=str(child.id),
                target_version_id=str(child.published_version_id),
            ),
        )

        with self.assertRaises(ApiException) as ctx_version:
            self._service().delete_workflow_version(child.id, child.published_version_id)
        self.assertEqual(ctx_version.exception.code, 40963)

        with self.assertRaises(ApiException) as ctx_workflow:
            self._service().delete_workflow(child.id)
        self.assertEqual(ctx_workflow.exception.code, 40964)

    def test_workflow_call_runtime_maps_structured_child_outputs(self) -> None:
        from app.assistant.workflow.engine.node_builders.workflow_call_node import build_workflow_call_node

        child = self._create_workflow(
            name="workflow_call__runtime_child",
            workflow_input=self._structured_workflow_input(
                output_field="greeting",
                output_template="Hello {{start.name}}",
            ),
        )

        node_fn = build_workflow_call_node(
            "call_child",
            {
                "targetWorkflowId": str(child.id),
                "bindingMode": "pinned",
                "targetPublishedVersionId": str(child.published_version_id),
                "inputBindings": {
                    "name": "{{start.user_input}}",
                },
            },
            object(),
            object(),
            {},
            self.db.get_bind(),
        )

        class _CompiledGraph:
            def invoke(self, initial_state: dict) -> dict:
                name = initial_state["structured_input"]["name"]
                response = f'{{"greeting": "Hello {name}"}}'
                return {
                    "node_outputs": {
                        "output_1": {
                            "status": "ok",
                            "text": response,
                            "raw": {"greeting": f"Hello {name}"},
                            "json_fields": {
                                "greeting": f"Hello {name}",
                                "response": response,
                            },
                        }
                    },
                    "execution_trace": ["output_1"],
                }

        with patch(
            "app.assistant.workflow.engine.engine.build_workflow_dag_subgraph",
            return_value=_CompiledGraph(),
        ):
            result = node_fn(
                {
                    "metadata": {},
                    "node_outputs": {
                        "start": {
                            "status": "ok",
                            "text": "Ada",
                            "raw": "Ada",
                            "json_fields": {"user_input": "Ada"},
                        }
                    },
                    "memory_mode": "auto",
                    "memory_context": {},
                    "sys_vars": {},
                }
            )

        output = result["node_outputs"]["call_child"]
        self.assertEqual(output["raw"], {"greeting": "Hello Ada"})
        self.assertEqual(output["json_fields"]["greeting"], "Hello Ada")
        self.assertEqual(output["json_fields"]["response"], '{"greeting": "Hello Ada"}')

    def test_workflow_call_runtime_scopes_human_approval_events(self) -> None:
        from app.assistant.workflow.engine.container_runtime import ScopedHumanLoopRuntimeProxy
        from app.assistant.workflow.engine.node_builders.workflow_call_node import build_workflow_call_node
        from app.assistant.workflow.human_approval_runtime import HumanLoopRuntime

        child = self._create_workflow(
            name="workflow_call__runtime_hitl_child",
            workflow_input=self._human_loop_child_workflow_input(),
        )

        fake_runtime = _FakeHumanLoopRuntime()
        requested_events: list[dict] = []
        resolved_events: list[dict] = []
        fake_runtime._on_requested = lambda payload: requested_events.append(payload)
        fake_runtime._on_resolved = lambda payload: resolved_events.append(payload)

        proxy = ScopedHumanLoopRuntimeProxy(fake_runtime, "call_child")
        self.assertIsInstance(proxy, HumanLoopRuntime)

        node_fn = build_workflow_call_node(
            "call_child",
            {
                "targetWorkflowId": str(child.id),
                "bindingMode": "pinned",
                "targetPublishedVersionId": str(child.published_version_id),
                "inputBindings": {
                    "request": "{{start.user_input}}",
                },
            },
            object(),
            object(),
            {},
            self.db.get_bind(),
        )

        class _CompiledGraph:
            def invoke(self, initial_state: dict) -> dict:
                runtime = initial_state["metadata"]["human_loop_runtime"]
                approval = runtime.create_and_wait(
                    node_id="human_1",
                    node_label="Human",
                    request_payload={"instruction": "Please review"},
                    field_schema=[{"name": "answer", "type": "string", "required": True}],
                    initial_values={"answer": initial_state["structured_input"]["request"]},
                )
                response = (
                    f'{{"answer": "{approval["submittedValues"]["answer"]}", '
                    f'"decision": "{approval["decision"]}"}}'
                )
                return {
                    "node_outputs": {
                        "output_1": {
                            "status": "ok",
                            "text": response,
                            "raw": {
                                "answer": approval["submittedValues"]["answer"],
                                "decision": approval["decision"],
                            },
                            "json_fields": {
                                "answer": approval["submittedValues"]["answer"],
                                "decision": approval["decision"],
                                "response": response,
                            },
                        }
                    },
                    "execution_trace": ["human_1", "output_1"],
                }

        with patch(
            "app.assistant.workflow.engine.engine.build_workflow_dag_subgraph",
            return_value=_CompiledGraph(),
        ):
            result = node_fn(
                {
                    "metadata": {
                        "human_loop_runtime": fake_runtime,
                    },
                    "node_outputs": {
                        "start": {
                            "status": "ok",
                            "text": "Ship it",
                            "raw": "Ship it",
                            "json_fields": {"user_input": "Ship it"},
                        }
                    },
                    "memory_mode": "auto",
                    "memory_context": {},
                    "sys_vars": {},
                }
            )

        self.assertEqual(fake_runtime.requested_node_ids, ["call_child::human_1"])
        self.assertEqual(requested_events[0]["nodeId"], "call_child::human_1")
        self.assertEqual(resolved_events[0]["nodeId"], "call_child::human_1")

        output = result["node_outputs"]["call_child"]
        self.assertEqual(output["json_fields"]["answer"], "approved-by-human")
        self.assertEqual(output["json_fields"]["decision"], "approved")


if __name__ == "__main__":
    unittest.main()
