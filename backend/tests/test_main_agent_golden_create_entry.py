"""Plan 08 Task 8: golden write release gate, ledger admission, graph audit."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
STRONG_SECRET = "s" * 32


class ConfigGateTests(unittest.TestCase):
    def test_defaults_are_off(self) -> None:
        from app.config import Settings

        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            ASSISTANT_CAPABILITY_LEDGER_MODE="legacy_read_only",
            ASSISTANT_MAIN_AGENT_WRITE_MODE="off",
        )
        self.assertEqual(s.assistant_capability_ledger_mode, "legacy_read_only")
        self.assertEqual(s.assistant_main_agent_write_mode, "off")

    def test_golden_requires_enforced_ledger(self) -> None:
        from app.config import Settings
        from pydantic import ValidationError

        with self.assertRaises((ValidationError, ValueError)):
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                ASSISTANT_CAPABILITY_LEDGER_MODE="legacy_read_only",
                ASSISTANT_MAIN_AGENT_WRITE_MODE="golden",
                ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET=STRONG_SECRET,
            )

    def test_enforced_requires_strong_secret(self) -> None:
        from app.config import Settings
        from pydantic import ValidationError

        with self.assertRaises((ValidationError, ValueError)):
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                ASSISTANT_CAPABILITY_LEDGER_MODE="enforced",
                ASSISTANT_MAIN_AGENT_WRITE_MODE="off",
                ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET="short",
            )

    def test_golden_enforced_with_secret_ok(self) -> None:
        from app.config import Settings

        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            ASSISTANT_CAPABILITY_LEDGER_MODE="enforced",
            ASSISTANT_MAIN_AGENT_WRITE_MODE="golden",
            ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET=STRONG_SECRET,
        )
        self.assertEqual(s.assistant_main_agent_write_mode, "golden")
        self.assertEqual(s.assistant_capability_ledger_mode, "enforced")


class LedgerAdmissionTests(unittest.TestCase):
    def test_freeze_mode_for_main_agent(self) -> None:
        from app.assistant.capability_calls.release_admission import (
            freeze_capability_ledger_mode_for_run,
        )
        from app.config import Settings

        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            ASSISTANT_CAPABILITY_LEDGER_MODE="enforced",
            ASSISTANT_MAIN_AGENT_WRITE_MODE="off",
            ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET=STRONG_SECRET,
        )
        self.assertEqual(
            freeze_capability_ledger_mode_for_run(runtime_kind="main_agent", settings=s),
            "enforced",
        )
        self.assertIsNone(
            freeze_capability_ledger_mode_for_run(runtime_kind="legacy", settings=s)
        )

    def test_golden_eligibility_cohort(self) -> None:
        from app.assistant.capability_calls.release_admission import (
            is_golden_write_eligible,
        )
        from app.config import Settings

        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            ASSISTANT_CAPABILITY_LEDGER_MODE="enforced",
            ASSISTANT_MAIN_AGENT_WRITE_MODE="golden",
            ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET=STRONG_SECRET,
            ASSISTANT_MAIN_AGENT_WRITE_COHORT_DIGEST=DIGEST_A,
        )
        self.assertTrue(
            is_golden_write_eligible(
                capability_ledger_mode="enforced",
                cohort_digest=DIGEST_A,
                settings=s,
            )
        )
        self.assertFalse(
            is_golden_write_eligible(
                capability_ledger_mode="enforced",
                cohort_digest=DIGEST_B,
                settings=s,
            )
        )
        self.assertFalse(
            is_golden_write_eligible(
                capability_ledger_mode="legacy_read_only",
                cohort_digest=DIGEST_A,
                settings=s,
            )
        )


class GoldenGraphAuditTests(unittest.TestCase):
    def test_checked_in_golden_graph_is_create_only(self) -> None:
        from app.assistant.capability_calls.release_admission import (
            audit_golden_workflow_graph,
        )
        from app.assistant.workflow.system_assets.registry import (
            SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
        )

        path = (
            Path(__file__).resolve().parents[1]
            / "app/assistant/workflow/system_assets/workflows"
            / "smart_capture_golden_create.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        audit = audit_golden_workflow_graph(
            payload, asset_key=SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY
        )
        self.assertTrue(audit.ok, audit.deny_reasons)
        self.assertFalse(audit.has_human_node)
        self.assertEqual(audit.tool_names.count("create_entry"), 1)
        self.assertNotIn("update_entry", audit.tool_names)
        self.assertNotIn("human_in_loop", audit.node_types)
        self.assertNotIn("workflow_call", audit.node_types)
        self.assertNotIn("code_executor", audit.node_types)

    def test_full_smart_capture_fails_golden_audit(self) -> None:
        from app.assistant.capability_calls.release_admission import (
            audit_golden_workflow_graph,
        )

        path = (
            Path(__file__).resolve().parents[1]
            / "app/assistant/workflow/system_assets/workflows"
            / "smart_capture.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        audit = audit_golden_workflow_graph(payload, asset_key="smart_capture")
        self.assertFalse(audit.ok)
        self.assertTrue(audit.has_human_node)
        self.assertIn("update_entry", audit.tool_names)

    def test_registry_has_hidden_golden_asset(self) -> None:
        from app.assistant.workflow.system_assets.registry import (
            SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
            get_system_asset,
        )

        asset = get_system_asset(SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY, locale="zh")
        self.assertIsNotNone(asset)
        assert asset is not None
        self.assertTrue(asset.hidden)
        self.assertEqual(asset.kind, "workflow")

    def test_gateway_allowlist(self) -> None:
        from app.assistant.capability_calls.release_admission import gateway_allows_write

        self.assertTrue(
            gateway_allows_write(
                domain_key="create_entry",
                write_mode="golden",
                capability_ledger_mode="enforced",
            )
        )
        self.assertFalse(
            gateway_allows_write(
                domain_key="update_entry",
                write_mode="golden",
                capability_ledger_mode="enforced",
            )
        )
        self.assertFalse(
            gateway_allows_write(
                domain_key="create_entry",
                write_mode="off",
                capability_ledger_mode="enforced",
            )
        )


class GoldenReleaseRecordTests(unittest.TestCase):
    def test_build_checked_in_release(self) -> None:
        from app.assistant.capability_calls.release_admission import (
            build_checked_in_golden_release,
        )
        from app.assistant.policy.contracts import GOLDEN_WRITE_LATTICE_PREFIX

        owner_version = uuid.UUID("11111111-1111-1111-1111-111111111111")
        rel = build_checked_in_golden_release(
            principal_digest=DIGEST_A,
            cohort_digest=DIGEST_B,
            owner_version_id=owner_version,
            binding_contract_digest=DIGEST_A,
            target_digest=DIGEST_B,
        )
        self.assertEqual(rel.domain_key, "create_entry")
        self.assertEqual(rel.allowed_side_effects, GOLDEN_WRITE_LATTICE_PREFIX)
        self.assertEqual(rel.required_execution_mode, "local_transactional")
        self.assertEqual(rel.required_approval_origin, "capability_call")


class LogicalVsSemanticDedupeTests(unittest.TestCase):
    def test_two_independent_runs_may_create_two_entries(self) -> None:
        """Semantic dedupe is out of scope; only logical-call idempotency is enforced."""
        from app.assistant.capability_calls.idempotency import (
            make_provider_logical_call_key,
            make_server_idempotency_key,
        )

        logical = make_provider_logical_call_key(
            provider_round_index=0,
            assistant_message_index=0,
            provider_tool_call_id="same-shape",
        )
        k1 = make_server_idempotency_key(
            secret=STRONG_SECRET,
            run_id=uuid.uuid4(),
            logical_call_key=logical,
            frozen_target_digest=DIGEST_A,
            canonical_input_digest=DIGEST_B,
        )
        k2 = make_server_idempotency_key(
            secret=STRONG_SECRET,
            run_id=uuid.uuid4(),
            logical_call_key=logical,
            frozen_target_digest=DIGEST_A,
            canonical_input_digest=DIGEST_B,
        )
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
