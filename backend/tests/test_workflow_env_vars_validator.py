from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowEnvVarsValidatorTests(unittest.TestCase):
    @staticmethod
    def _base_nodes_edges(
        *,
        start_session_vars: list[dict] | None = None,
        variable_assign_config: dict | None = None,
        output_template: str = '{{env.counter}}',
    ) -> tuple[list[dict], list[dict]]:
        nodes = [
            {
                'node_id': 'start',
                'node_type': 'start',
                'label': 'Start',
                'config': {
                    'sessionVars': start_session_vars if start_session_vars is not None else [
                        {'name': 'counter', 'type': 'integer', 'defaultValue': 1},
                    ],
                },
            },
            {
                'node_id': 'assign_1',
                'node_type': 'variable_assign',
                'label': 'Assign',
                'config': variable_assign_config if variable_assign_config is not None else {
                    'variableName': 'counter',
                    'operation': 'increment',
                    'valueTemplate': '2',
                },
            },
            {
                'node_id': 'output_final',
                'node_type': 'output',
                'label': 'Output',
                'config': {
                    'outputMode': 'text',
                    'textTemplate': output_template,
                },
            },
        ]
        edges = [
            {
                'source_node_id': 'start',
                'target_node_id': 'assign_1',
                'source_handle': 'output',
            },
            {
                'source_node_id': 'assign_1',
                'target_node_id': 'output_final',
                'source_handle': 'output',
            },
        ]
        return nodes, edges

    def test_validate_workflow_accepts_env_and_variable_assign(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow, validate_workflow_compile

        nodes, edges = self._base_nodes_edges(output_template='{{env.counter}}')

        save_validation = validate_workflow(nodes, edges)
        compile_validation = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_validation.valid, [item.message for item in save_validation.errors])
        self.assertTrue(compile_validation.valid, [item.message for item in compile_validation.errors])

    def test_validate_workflow_rejects_unknown_env_reference(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._base_nodes_edges(output_template='{{env.missing_var}}')

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any('unknown env variable' in item.message.lower() for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_validate_workflow_rejects_variable_assign_unknown_variable(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._base_nodes_edges(variable_assign_config={
            'variableName': 'not_defined',
            'operation': 'set',
            'valueTemplate': 'x',
        })

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any('not defined in start sessionvars' in item.message.lower() for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_validate_workflow_rejects_append_on_non_appendable_type(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._base_nodes_edges(
            start_session_vars=[{'name': 'counter', 'type': 'integer', 'defaultValue': 0}],
            variable_assign_config={
                'variableName': 'counter',
                'operation': 'append',
                'valueTemplate': 'x',
            },
        )

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any('append supports only string/array' in item.message.lower() for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_validate_workflow_accepts_clear_without_value_template(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._base_nodes_edges(
            variable_assign_config={
                'variableName': 'counter',
                'operation': 'clear',
            },
        )

        validation = validate_workflow(nodes, edges)
        self.assertTrue(validation.valid, [item.message for item in validation.errors])

    def test_iteration_body_supports_variable_assign(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow, validate_workflow_compile

        nodes = [
            {
                'node_id': 'start',
                'node_type': 'start',
                'label': 'Start',
                'config': {
                    'sessionVars': [
                        {'name': 'counter', 'type': 'integer', 'defaultValue': 0},
                    ],
                },
            },
            {
                'node_id': 'iter_1',
                'node_type': 'iteration',
                'label': 'Iteration',
                'config': {
                    'inputSource': '[1,2]',
                    'outputVariable': 'results',
                    'outputSelector': '{{container.item}}',
                    'parallelMode': False,
                    'errorStrategy': 'fail_fast',
                    'flattenOutput': True,
                    'bodyNodes': [
                        {'node_id': 'start', 'node_type': 'start', 'label': 'Start', 'config': {}},
                        {
                            'node_id': 'assign_body',
                            'node_type': 'variable_assign',
                            'label': 'Assign',
                            'config': {
                                'variableName': 'counter',
                                'operation': 'increment',
                                'valueTemplate': '1',
                            },
                        },
                    ],
                    'bodyEdges': [
                        {'source_node_id': 'start', 'target_node_id': 'assign_body', 'source_handle': 'output'},
                    ],
                },
            },
            {
                'node_id': 'output_final',
                'node_type': 'output',
                'label': 'Output',
                'config': {
                    'outputMode': 'text',
                    'textTemplate': '{{env.counter}}',
                },
            },
        ]
        edges = [
            {'source_node_id': 'start', 'target_node_id': 'iter_1', 'source_handle': 'output'},
            {'source_node_id': 'iter_1', 'target_node_id': 'output_final', 'source_handle': 'output'},
        ]

        save_validation = validate_workflow(nodes, edges)
        compile_validation = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_validation.valid, [item.message for item in save_validation.errors])
        self.assertTrue(compile_validation.valid, [item.message for item in compile_validation.errors])


if __name__ == '__main__':
    unittest.main()
