from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantMemoryL0Tests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_l0_filters_roles_and_deduplicates_current_user_input(self) -> None:
        from app.assistant.orchestration.memory_context import build_l0_window

        result = build_l0_window(
            history=[
                {"role": "system", "content": "sys"},
                {"role": "tool", "content": "ignored"},
                {"role": "user", "content": "  hello  "},
                {"role": "assistant", "content": "  hi there "},
                {"role": "assistant", "content": "   "},
                {"role": "user", "content": "continue this"},
            ],
            user_input="continue this",
            turns_limit=6,
            chars_limit=1200,
        )

        self.assertEqual(result["l0_text"], "User: hello\nAssistant: hi there")
        self.assertEqual(
            result["l0_messages"],
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        )
        self.assertEqual(result["l0_source_count"], 2)
        self.assertEqual(result["l0_trimmed_chars"], 0)

    def test_l0_applies_turn_window_limit(self) -> None:
        from app.assistant.orchestration.memory_context import build_l0_window

        result = build_l0_window(
            history=[
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
            ],
            user_input="new input",
            turns_limit=1,
            chars_limit=1200,
        )

        self.assertEqual(result["l0_text"], "User: u3\nAssistant: a3")
        self.assertEqual(
            result["l0_messages"],
            [
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
            ],
        )
        self.assertEqual(result["l0_source_count"], 2)

    def test_l0_applies_chars_limit_by_dropping_oldest_lines(self) -> None:
        from app.assistant.orchestration.memory_context import build_l0_window

        result = build_l0_window(
            history=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
                {"role": "user", "content": "third"},
            ],
            user_input="new input",
            turns_limit=6,
            chars_limit=30,
        )

        self.assertNotIn("first", result["l0_text"])
        self.assertIn("Assistant: second", result["l0_text"])
        self.assertIn("User: third", result["l0_text"])
        self.assertEqual(
            result["l0_messages"],
            [
                {"role": "assistant", "content": "second"},
                {"role": "user", "content": "third"},
            ],
        )
        self.assertEqual(result["l0_source_count"], 2)
        self.assertGreater(result["l0_trimmed_chars"], 0)

    def test_l0_truncates_single_line_with_ellipsis(self) -> None:
        from app.assistant.orchestration.memory_context import build_l0_window

        result = build_l0_window(
            history=[{"role": "assistant", "content": "x" * 80}],
            user_input="different input",
            turns_limit=1,
            chars_limit=20,
        )

        self.assertEqual(result["l0_source_count"], 1)
        self.assertEqual(result["l0_messages"], [{"role": "assistant", "content": "x" * 6 + "..."}])
        self.assertTrue(result["l0_text"].endswith("..."))
        self.assertLessEqual(len(result["l0_text"]), 20)
        self.assertGreater(result["l0_trimmed_chars"], 0)

    def test_l0_handles_tiny_budget_without_error(self) -> None:
        from app.assistant.orchestration.memory_context import build_l0_window

        result = build_l0_window(
            history=[{"role": "assistant", "content": "abcdef"}],
            user_input="different input",
            turns_limit=1,
            chars_limit=2,
        )

        self.assertEqual(result["l0_source_count"], 1)
        self.assertEqual(result["l0_messages"], [])
        self.assertEqual(len(result["l0_text"]), 2)
        self.assertGreater(result["l0_trimmed_chars"], 0)
