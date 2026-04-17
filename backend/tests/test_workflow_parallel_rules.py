from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


def _node(node_id: str, node_type: str) -> dict[str, object]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": node_id,
        "config": {},
    }


def _edge(source: str, target: str, source_handle: str = "output") -> dict[str, str]:
    return {
        "source_node_id": source,
        "target_node_id": target,
        "source_handle": source_handle,
    }


class WorkflowParallelRulesTests(unittest.TestCase):
    def test_if_else_after_parallel_join_is_allowed(self) -> None:
        from app.assistant.workflow.validation.validator import validate_parallel_branches

        nodes = [
            _node("start", "start"),
            _node("split", "tool"),
            _node("branch_a", "llm"),
            _node("branch_b", "tool"),
            _node("join", "llm"),
            _node("route", "if_else"),
            _node("output_merge", "output"),
            _node("output_create", "output"),
        ]
        edges = [
            _edge("start", "split"),
            _edge("split", "branch_a"),
            _edge("split", "branch_b"),
            _edge("branch_a", "join"),
            _edge("branch_b", "join"),
            _edge("join", "route"),
            _edge("route", "output_merge", "merge"),
            _edge("route", "output_create", "else"),
        ]

        result = validate_parallel_branches(nodes, edges)

        self.assertTrue(result.valid, [err.message for err in result.errors])

    def test_if_else_inside_unmerged_parallel_branch_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_parallel_branches

        nodes = [
            _node("start", "start"),
            _node("split", "tool"),
            _node("route", "if_else"),
            _node("branch_b", "tool"),
            _node("output_merge", "output"),
            _node("output_create", "output"),
            _node("output_branch", "output"),
        ]
        edges = [
            _edge("start", "split"),
            _edge("split", "route"),
            _edge("split", "branch_b"),
            _edge("route", "output_merge", "merge"),
            _edge("route", "output_create", "else"),
            _edge("branch_b", "output_branch"),
        ]

        result = validate_parallel_branches(nodes, edges)

        self.assertFalse(result.valid)
        self.assertTrue(
            any("before parallel branches reconverge" in err.message for err in result.errors),
            [err.message for err in result.errors],
        )

    def test_sequential_parallel_sections_do_not_count_as_nested_depth(self) -> None:
        from app.assistant.workflow.validation.validator import validate_parallel_branches

        nodes = [_node("start", "start"), _node("output_final", "output")]
        edges = []
        previous_join = "start"

        for index in range(1, 5):
            split_id = f"split_{index}"
            branch_a_id = f"branch_{index}_a"
            branch_b_id = f"branch_{index}_b"
            join_id = f"join_{index}"
            nodes.extend(
                [
                    _node(split_id, "tool"),
                    _node(branch_a_id, "tool"),
                    _node(branch_b_id, "tool"),
                    _node(join_id, "llm"),
                ]
            )
            edges.extend(
                [
                    _edge(previous_join, split_id),
                    _edge(split_id, branch_a_id),
                    _edge(split_id, branch_b_id),
                    _edge(branch_a_id, join_id),
                    _edge(branch_b_id, join_id),
                ]
            )
            previous_join = join_id

        edges.append(_edge(previous_join, "output_final"))

        result = validate_parallel_branches(nodes, edges)

        self.assertTrue(result.valid, [err.message for err in result.errors])

    def test_human_in_loop_approval_rejection_does_not_count_as_parallel_fanout(self) -> None:
        from app.assistant.workflow.validation.validator import validate_parallel_branches

        nodes = [
            _node("start", "start"),
            _node("split", "tool"),
            _node("branch_a", "tool"),
            _node("branch_b", "tool"),
            _node("join", "llm"),
            _node("approval", "human_in_loop"),
            _node("output_cancel", "output"),
            _node("route", "if_else"),
            _node("output_merge", "output"),
            _node("output_create", "output"),
        ]
        edges = [
            _edge("start", "split"),
            _edge("split", "branch_a"),
            _edge("split", "branch_b"),
            _edge("branch_a", "join"),
            _edge("branch_b", "join"),
            _edge("join", "approval"),
            _edge("approval", "output_cancel", "rejected"),
            _edge("approval", "route", "approved"),
            _edge("route", "output_merge", "merge"),
            _edge("route", "output_create", "else"),
        ]

        result = validate_parallel_branches(nodes, edges)

        self.assertTrue(result.valid, [err.message for err in result.errors])


if __name__ == "__main__":
    unittest.main()
