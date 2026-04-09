from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class WorkflowExecutionContextTests(unittest.TestCase):
    def test_parse_execution_context_exposes_generic_request_sys_vars(self) -> None:
        from app.assistant.workflow.engine.execution_context import parse_execution_context  # noqa: E402

        parsed = parse_execution_context(
            runtime_context={
                "request_source": "unit-test",
                "request_channel": "cli",
                "request_session": "session-1",
                "request_tool": "tool-1",
            },
            parse_output_boolean=bool,
        )

        self.assertEqual(parsed.sys_vars["request_source"], "unit-test")
        self.assertEqual(parsed.sys_vars["request_channel"], "cli")
        self.assertEqual(parsed.sys_vars["request_session"], "session-1")
        self.assertEqual(parsed.sys_vars["request_tool"], "tool-1")

    def test_parse_execution_context_keeps_legacy_openclaw_sys_var_aliases(self) -> None:
        from app.assistant.workflow.engine.execution_context import parse_execution_context  # noqa: E402

        parsed = parse_execution_context(
            runtime_context={
                "openclaw_source": "legacy-source",
                "openclaw_channel": "legacy-channel",
                "openclaw_session": "legacy-session",
                "openclaw_tool": "legacy-tool",
            },
            parse_output_boolean=bool,
        )

        self.assertEqual(parsed.sys_vars["request_source"], "legacy-source")
        self.assertEqual(parsed.sys_vars["request_channel"], "legacy-channel")
        self.assertEqual(parsed.sys_vars["request_session"], "legacy-session")
        self.assertEqual(parsed.sys_vars["request_tool"], "legacy-tool")
        self.assertEqual(parsed.sys_vars["openclaw_source"], "legacy-source")
        self.assertEqual(parsed.sys_vars["openclaw_channel"], "legacy-channel")
        self.assertEqual(parsed.sys_vars["openclaw_session"], "legacy-session")
        self.assertEqual(parsed.sys_vars["openclaw_tool"], "legacy-tool")
