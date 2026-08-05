"""Plan 2 Task 9 — Main-Agent-only live import and selector boundary.

AST-enforced: live application code never imports ``app.assistant.migration``
and never calls retired runtime-selector symbols. Test inventory of retired
selector env/fixture strings is allowlisted explicitly.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LIVE_APP_ROOT = BACKEND_ROOT / "app"
ARCHIVED_RUNTIME_PACKAGE = LIVE_APP_ROOT / "assistant" / "migration"
TESTS_ROOT = BACKEND_ROOT / "tests"

FORBIDDEN_SELECTOR_SYMBOLS = frozenset(
    {
        "admit_and_select_runtime",
        "admit_with_rollout",
        "validate_runtime_rollout_startup",
        "decide_assigned_runtime_kind",
    }
)

# Every residual inventory hit under tests/ for the retired selector patterns
# must appear here. Keep this tight: removed-variable rejection, historical
# migration fixtures, and V1 read-only parser fixtures only.
INVENTORY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "test_assistant_atomic_admission.py:623",
        "test_assistant_atomic_admission.py:629",
        "test_assistant_runtime_config.py:15",
        "test_assistant_runtime_config.py:16",
        "test_assistant_runtime_config.py:27",
        "test_assistant_runtime_migration_postgres.py:418",
        "test_durable_main_agent_runner.py:156",
        "test_durable_run_migration_postgres.py:488",
        "test_durable_run_migration_postgres.py:497",
        "test_durable_run_repository.py:910",
        "test_main_agent_golden_create_entry.py:92",
    }
)

INVENTORY_PATTERN = re.compile(
    r"ASSISTANT_RUNTIME_MODE|ASSISTANT_RUNTIME_ROLLOUT_REVISION|"
    r"admit_and_select_runtime|runtime_kind\s*=\s*[\"']legacy[\"']"
)


def _iter_live_py_files() -> list[Path]:
    return sorted(
        path
        for path in LIVE_APP_ROOT.rglob("*.py")
        if not path.is_relative_to(ARCHIVED_RUNTIME_PACKAGE)
    )


def find_live_symbol_references(
    root: Path, forbidden: set[str] | frozenset[str]
) -> list[str]:
    """Return ``path:lineno`` for Name/Attribute references to forbidden symbols."""
    hits: list[str] = []
    archived = root / "assistant" / "migration"
    for path in sorted(root.rglob("*.py")):
        if path.is_relative_to(archived):
            continue
        try:
            source = path.read_text("utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in forbidden:
                        hits.append(f"{path}:{node.lineno}")
                continue
            if name in forbidden:
                hits.append(f"{path}:{node.lineno}")
    return hits


class MainAgentOnlyLiveImportBoundaryTests(unittest.TestCase):
    def test_live_application_never_imports_assistant_migration(self) -> None:
        violations: list[str] = []
        for path in _iter_live_py_files():
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                if any(
                    name == "app.assistant.migration"
                    or name.startswith("app.assistant.migration.")
                    for name in modules
                ):
                    rel = path.relative_to(LIVE_APP_ROOT)
                    violations.append(f"{rel}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_removed_runtime_selector_symbols_have_no_live_callers(self) -> None:
        hits = find_live_symbol_references(LIVE_APP_ROOT, FORBIDDEN_SELECTOR_SYMBOLS)
        # Normalize to relative paths for stable assertion messages.
        normalized = [
            str(Path(h).relative_to(LIVE_APP_ROOT.parent))
            if h.startswith(str(LIVE_APP_ROOT.parent))
            else h
            for h in hits
        ]
        # Re-run with relative formatting.
        relative_hits: list[str] = []
        for raw in hits:
            path_str, _, lineno = raw.rpartition(":")
            path = Path(path_str)
            try:
                rel = path.relative_to(BACKEND_ROOT)
            except ValueError:
                rel = path
            relative_hits.append(f"{rel}:{lineno}")
        self.assertEqual(relative_hits, [])

    def test_live_run_model_is_main_agent_only(self) -> None:
        source = (LIVE_APP_ROOT / "assistant" / "models.py").read_text("utf-8")
        self.assertIn("runtime_kind = 'main_agent'", source)
        self.assertNotIn("runtime_kind IN ('legacy','main_agent')", source)

    def test_profile_v2_has_no_fallback_field(self) -> None:
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV2,
            MainAgentRuntimePolicyV2,
        )

        fields = MainAgentProfileSnapshotV2.model_fields
        self.assertNotIn("fallback_policy", fields)
        self.assertEqual(MainAgentRuntimePolicyV2().runtime_kind, "main_agent")

    def test_durable_admission_module_is_removed(self) -> None:
        path = LIVE_APP_ROOT / "assistant" / "durable" / "admission.py"
        self.assertFalse(path.exists(), "durable/admission.py must be deleted")

    def test_main_agent_rollout_module_is_removed(self) -> None:
        path = LIVE_APP_ROOT / "assistant" / "main_agent" / "rollout.py"
        self.assertFalse(path.exists(), "main_agent/rollout.py must be deleted")

    def test_main_agent_service_has_no_legacy_fallback_scaffolding(self) -> None:
        source = (LIVE_APP_ROOT / "assistant" / "main_agent" / "service.py").read_text(
            "utf-8"
        )
        for retired_symbol in (
            "AdmissionDecision",
            "MainAgentFallbackState",
            "_maybe_fallback",
        ):
            self.assertNotIn(retired_symbol, source)

    def test_selector_inventory_matches_allowlist_only(self) -> None:
        """rg inventory of retired selector strings under tests/ is allowlisted."""
        hits: list[str] = []
        for path in sorted(TESTS_ROOT.rglob("*.py")):
            # This file itself documents the pattern; skip.
            if path.name == "test_main_agent_only_live_imports.py":
                continue
            try:
                lines = path.read_text("utf-8").splitlines()
            except OSError:
                continue
            rel = path.relative_to(TESTS_ROOT)
            for idx, line in enumerate(lines, start=1):
                if INVENTORY_PATTERN.search(line):
                    hits.append(f"{rel}:{idx}")
        unexpected = sorted(set(hits) - INVENTORY_ALLOWLIST)
        missing = sorted(INVENTORY_ALLOWLIST - set(hits))
        self.assertEqual(
            unexpected,
            [],
            f"unexpected inventory hits (add to allowlist only if historical): {unexpected}",
        )
        self.assertEqual(
            missing,
            [],
            f"stale allowlist entries (remove if rewritten): {missing}",
        )


if __name__ == "__main__":
    unittest.main()
