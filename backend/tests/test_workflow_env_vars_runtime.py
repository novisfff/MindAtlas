from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowEnvVarsRuntimeTests(unittest.TestCase):
    def test_workflow_runtime_updates_env_vars_and_renders_output(self) -> None:
        from app.assistant.workflow.engine.engine import build_workflow_dag_subgraph

        nodes = [
            {
                'node_id': 'start',
                'node_type': 'start',
                'label': 'Start',
                'config': {
                    'sessionVars': [
                        {'name': 'counter', 'type': 'integer', 'defaultValue': 1},
                        {'name': 'message', 'type': 'string', 'defaultValue': 'A'},
                    ],
                },
            },
            {
                'node_id': 'assign_increment',
                'node_type': 'variable_assign',
                'label': 'Inc',
                'config': {
                    'variableName': 'counter',
                    'operation': 'increment',
                    'valueTemplate': '2',
                },
            },
            {
                'node_id': 'assign_append',
                'node_type': 'variable_assign',
                'label': 'Append',
                'config': {
                    'variableName': 'message',
                    'operation': 'append',
                    'valueTemplate': '-B',
                },
            },
            {
                'node_id': 'output_final',
                'node_type': 'output',
                'label': 'Output',
                'config': {
                    'outputMode': 'text',
                    'textTemplate': '{{env.counter}}|{{env.message}}',
                },
            },
        ]
        edges = [
            {'source_node_id': 'start', 'target_node_id': 'assign_increment', 'source_handle': 'output'},
            {'source_node_id': 'assign_increment', 'target_node_id': 'assign_append', 'source_handle': 'output'},
            {'source_node_id': 'assign_append', 'target_node_id': 'output_final', 'source_handle': 'output'},
        ]

        compiled = build_workflow_dag_subgraph(
            skill=SimpleNamespace(name='wf_env_runtime'),
            nodes=nodes,
            edges=edges,
            llm=object(),
            args_llm=object(),
            tool_map={},
            db_bind=None,
            node_llms=None,
        )

        result = compiled.invoke(
            {
                'user_input': 'ignored',
                'metadata': {},
                'node_outputs': {},
                'execution_trace': [],
                'branch_decisions': {},
                'sys_vars': {},
                'workflow_node_types': {item['node_id']: item['node_type'] for item in nodes},
            }
        )

        node_outputs = result.get('node_outputs', {})
        self.assertEqual(node_outputs['output_final']['text'], '3|A-B')
        self.assertEqual(result.get('env_vars', {}).get('counter'), 3)
        self.assertEqual(result.get('env_vars', {}).get('message'), 'A-B')

    def test_container_body_variable_assign_updates_parent_env_state(self) -> None:
        from app.assistant.workflow.engine.engine import _execute_container_body

        container_result = _execute_container_body(
            container_node_id='iter_1',
            container_node_type='iteration',
            node_cfg={
                'bodyNodes': [
                    {'node_id': 'start', 'node_type': 'start', 'label': 'Start', 'config': {}},
                    {
                        'node_id': 'assign_1',
                        'node_type': 'variable_assign',
                        'label': 'Assign',
                        'config': {
                            'variableName': 'counter',
                            'operation': 'increment',
                            'valueTemplate': '5',
                        },
                    },
                ],
                'bodyEdges': [
                    {'source_node_id': 'start', 'target_node_id': 'assign_1', 'source_handle': 'output'},
                ],
            },
            parent_state={
                'metadata': {},
                'node_outputs': {},
                'sys_vars': {},
                'node_llms': {},
                'env_vars': {'counter': 1},
                'env_specs': {
                    'counter': {
                        'name': 'counter',
                        'type': 'integer',
                        'defaultValue': 0,
                        'description': '',
                    },
                },
            },
            llm=object(),
            args_llm=object(),
            tool_map={},
            db_bind=None,
            node_llms={},
            container_input='x',
            container_fields={'item': 'x', 'index': 0},
        )

        self.assertEqual(container_result.get('env_vars', {}).get('counter'), 6)
        assign_output = container_result.get('node_outputs', {}).get('assign_1', {})
        self.assertEqual(assign_output.get('json_fields', {}).get('before'), 1)
        self.assertEqual(assign_output.get('json_fields', {}).get('after'), 6)

    def test_clear_operation_resets_to_type_empty_values(self) -> None:
        from app.assistant.workflow.engine.engine import build_workflow_dag_subgraph

        nodes = [
            {
                'node_id': 'start',
                'node_type': 'start',
                'label': 'Start',
                'config': {
                    'sessionVars': [
                        {'name': 's', 'type': 'string', 'defaultValue': 'abc'},
                        {'name': 'n', 'type': 'number', 'defaultValue': 12.3},
                        {'name': 'i', 'type': 'integer', 'defaultValue': 9},
                        {'name': 'b', 'type': 'boolean', 'defaultValue': True},
                        {'name': 'o', 'type': 'object', 'defaultValue': {'k': 'v'}},
                        {'name': 'a', 'type': 'array', 'defaultValue': [1, 2]},
                    ],
                },
            },
            {
                'node_id': 'set_s',
                'node_type': 'variable_assign',
                'label': 'SetS',
                'config': {'variableName': 's', 'operation': 'set', 'valueTemplate': 'zzz'},
            },
            {
                'node_id': 'set_n',
                'node_type': 'variable_assign',
                'label': 'SetN',
                'config': {'variableName': 'n', 'operation': 'set', 'valueTemplate': '99.5'},
            },
            {
                'node_id': 'set_i',
                'node_type': 'variable_assign',
                'label': 'SetI',
                'config': {'variableName': 'i', 'operation': 'set', 'valueTemplate': '100'},
            },
            {
                'node_id': 'set_b',
                'node_type': 'variable_assign',
                'label': 'SetB',
                'config': {'variableName': 'b', 'operation': 'set', 'valueTemplate': 'false'},
            },
            {
                'node_id': 'set_o',
                'node_type': 'variable_assign',
                'label': 'SetO',
                'config': {'variableName': 'o', 'operation': 'set', 'valueTemplate': '{"x":1}'},
            },
            {
                'node_id': 'set_a',
                'node_type': 'variable_assign',
                'label': 'SetA',
                'config': {'variableName': 'a', 'operation': 'set', 'valueTemplate': '[3,4]'},
            },
            {'node_id': 'clear_s', 'node_type': 'variable_assign', 'label': 'ClearS', 'config': {'variableName': 's', 'operation': 'clear'}},
            {'node_id': 'clear_n', 'node_type': 'variable_assign', 'label': 'ClearN', 'config': {'variableName': 'n', 'operation': 'clear'}},
            {'node_id': 'clear_i', 'node_type': 'variable_assign', 'label': 'ClearI', 'config': {'variableName': 'i', 'operation': 'clear'}},
            {'node_id': 'clear_b', 'node_type': 'variable_assign', 'label': 'ClearB', 'config': {'variableName': 'b', 'operation': 'clear'}},
            {'node_id': 'clear_o', 'node_type': 'variable_assign', 'label': 'ClearO', 'config': {'variableName': 'o', 'operation': 'clear'}},
            {'node_id': 'clear_a', 'node_type': 'variable_assign', 'label': 'ClearA', 'config': {'variableName': 'a', 'operation': 'clear'}},
            {
                'node_id': 'output_final',
                'node_type': 'output',
                'label': 'Output',
                'config': {
                    'outputMode': 'text',
                    'textTemplate': 'done',
                },
            },
        ]
        edges = [
            {'source_node_id': 'start', 'target_node_id': 'set_s', 'source_handle': 'output'},
            {'source_node_id': 'set_s', 'target_node_id': 'set_n', 'source_handle': 'output'},
            {'source_node_id': 'set_n', 'target_node_id': 'set_i', 'source_handle': 'output'},
            {'source_node_id': 'set_i', 'target_node_id': 'set_b', 'source_handle': 'output'},
            {'source_node_id': 'set_b', 'target_node_id': 'set_o', 'source_handle': 'output'},
            {'source_node_id': 'set_o', 'target_node_id': 'set_a', 'source_handle': 'output'},
            {'source_node_id': 'set_a', 'target_node_id': 'clear_s', 'source_handle': 'output'},
            {'source_node_id': 'clear_s', 'target_node_id': 'clear_n', 'source_handle': 'output'},
            {'source_node_id': 'clear_n', 'target_node_id': 'clear_i', 'source_handle': 'output'},
            {'source_node_id': 'clear_i', 'target_node_id': 'clear_b', 'source_handle': 'output'},
            {'source_node_id': 'clear_b', 'target_node_id': 'clear_o', 'source_handle': 'output'},
            {'source_node_id': 'clear_o', 'target_node_id': 'clear_a', 'source_handle': 'output'},
            {'source_node_id': 'clear_a', 'target_node_id': 'output_final', 'source_handle': 'output'},
        ]

        compiled = build_workflow_dag_subgraph(
            skill=SimpleNamespace(name='wf_env_clear_runtime'),
            nodes=nodes,
            edges=edges,
            llm=object(),
            args_llm=object(),
            tool_map={},
            db_bind=None,
            node_llms=None,
        )

        result = compiled.invoke(
            {
                'user_input': 'ignored',
                'metadata': {},
                'node_outputs': {},
                'execution_trace': [],
                'branch_decisions': {},
                'sys_vars': {},
                'workflow_node_types': {item['node_id']: item['node_type'] for item in nodes},
            }
        )

        env_vars = result.get('env_vars', {})
        self.assertEqual(env_vars.get('s'), '')
        self.assertEqual(env_vars.get('n'), 0.0)
        self.assertEqual(env_vars.get('i'), 0)
        self.assertEqual(env_vars.get('b'), False)
        self.assertEqual(env_vars.get('o'), {})
        self.assertEqual(env_vars.get('a'), [])


if __name__ == '__main__':
    unittest.main()
