from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class SystemDefaultsLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.assistant.skills.defaults_loader import clear_system_defaults_cache  # noqa: E402

        clear_system_defaults_cache()

    def tearDown(self) -> None:
        from app.assistant.skills.defaults_loader import clear_system_defaults_cache  # noqa: E402

        clear_system_defaults_cache()

    def test_load_system_defaults_success(self) -> None:
        from app.assistant.skills.defaults_loader import load_system_skill_defaults  # noqa: E402

        defaults = load_system_skill_defaults()
        names = {item.name for item in defaults}
        self.assertIn("quick_stats", names)
        self.assertIn("smart_capture", names)
        self.assertIn("periodic_review", names)
        self.assertIn("general_chat", names)

    def test_get_system_baselines_success(self) -> None:
        from app.assistant.skills.defaults_loader import (  # noqa: E402
            get_system_agent_baseline,
            get_system_workflow_baseline,
        )

        workflow = get_system_workflow_baseline("quick_stats")
        self.assertIsNotNone(workflow)
        self.assertEqual(len(workflow.nodes), 4)

        agent = get_system_agent_baseline("general_chat")
        self.assertIsNotNone(agent)
        self.assertTrue(bool((agent.system_prompt or "").strip()))
        self.assertTrue(bool((agent.kb_config or {}).get("enabled", False)))

    def test_fail_fast_when_preset_file_missing(self) -> None:
        from app.assistant.skills.defaults_loader import _load_system_skill_defaults_from_dir  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = {
                "schemaVersion": 1,
                "skills": [
                    {
                        "name": "x",
                        "description": "x",
                        "intentExamples": [],
                        "targetType": "workflow",
                        "presetFile": "workflows/missing.json",
                    }
                ],
            }
            (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                _load_system_skill_defaults_from_dir(base)


if __name__ == "__main__":
    unittest.main()

