"""Plan 06 Task 10: rollout demos (unit/scripted; no live Docker/PG/MinIO).

Demonstrates the hard release boundary without a full deploy:

1. Mode transitions ``off -> worker ready -> read_only -> off`` never switch an
   active Run's ``runtime_kind``.
2. API admission fails safely (Legacy fallback / no Main Agent insert) when no
   compatible worker heartbeat exists.
3. During rolling deploy, an old-build worker continues to claim/drain Runs that
   require its build; a new-build worker cannot steal them.
4. Production Main Agent ceiling still forbids ``interrupt_mode=durable``
   (Plan 07 remains disabled).
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

BUILD_OLD = "build-old-rev"
BUILD_NEW = "build-new-rev"
DIGEST = "a" * 64


def _make_session():
    from tests._db import make_session

    return make_session()


def _identity(**kwargs: Any):
    from app.assistant.durable.worker_registry import WorkerIdentity

    defaults = dict(
        worker_id=f"w-{uuid.uuid4().hex[:12]}",
        app_build_revision=BUILD_OLD,
        runtime_contract_version=1,
        supported_checkpoint_codec_versions=(1,),
        capability_feature_digest=DIGEST,
        hostname_label="rollout-host",
    )
    defaults.update(kwargs)
    return WorkerIdentity(**defaults)


def _make_main_agent_run(db, *, status: str = "queued", **kwargs: Any):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"rollout-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision=kwargs.pop(
            "required_app_build_revision", BUILD_OLD
        ),
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _lease_service(db, *, app_build_revision: str = BUILD_OLD):
    from app.assistant.durable.leases import RunLeaseService
    from app.assistant.durable.worker_registry import WorkerIdentity

    identity = WorkerIdentity(
        worker_id=f"lease-{uuid.uuid4().hex[:10]}",
        app_build_revision=app_build_revision,
        runtime_contract_version=1,
        supported_checkpoint_codec_versions=(1,),
        capability_feature_digest=DIGEST,
        hostname_label="rollout-lease",
    )
    return RunLeaseService(
        db,
        identity=identity,
        lease_ttl=timedelta(seconds=30),
        retry_base_ms=500,
        retry_max_ms=30000,
    )


class ModeTransitionRolloutTests(unittest.TestCase):
    """off -> worker ready -> read_only -> off; active Runs keep runtime_kind."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_mode_off_selects_legacy_only(self) -> None:
        from app.assistant.durable.admission import admit_and_select_runtime

        with patch("app.assistant.durable.admission.get_settings") as gs:
            settings = MagicMock()
            settings.assistant_main_agent_mode = "off"
            settings.app_build_revision = BUILD_NEW
            settings.assistant_worker_registration_ttl_sec = 20
            gs.return_value = settings
            kind, reason, kwargs = admit_and_select_runtime(self.db, mode="off")
        self.assertEqual(kind, "legacy")
        self.assertEqual(reason, "mode_off")
        self.assertEqual(kwargs, {})

    def test_worker_ready_then_read_only_admits_main_agent(self) -> None:
        """After a compatible worker registers, read_only may admit main_agent."""
        from app.assistant.durable.admission import admit_and_select_runtime
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        reg.register(_identity(app_build_revision=BUILD_NEW))

        # Bypass full Main Agent profile admission; focus on worker preflight.
        fake_admission = MagicMock()
        fake_admission.snapshot = None

        with (
            patch("app.assistant.durable.admission.get_settings") as gs,
            patch(
                "app.assistant.main_agent.service.should_construct_main_agent",
                return_value=True,
            ),
            patch(
                "app.assistant.main_agent.service.admit_main_agent",
                return_value=fake_admission,
            ),
        ):
            settings = MagicMock()
            settings.assistant_main_agent_mode = "read_only"
            settings.app_build_revision = BUILD_NEW
            settings.assistant_worker_registration_ttl_sec = 60
            gs.return_value = settings
            kind, reason, kwargs = admit_and_select_runtime(
                self.db,
                mode="read_only",
                app_build_revision=BUILD_NEW,
            )

        self.assertEqual(kind, "main_agent")
        self.assertIsNone(reason)
        self.assertEqual(kwargs["runtime_kind"], "main_agent")
        self.assertEqual(kwargs["runtime_contract_version"], 1)
        self.assertEqual(kwargs["required_app_build_revision"], BUILD_NEW)

    def test_mode_back_to_off_does_not_switch_active_run(self) -> None:
        """Rolling mode off never mutates an already-inserted main_agent Run."""
        from app.assistant.durable.admission import admit_and_select_runtime
        from app.assistant.models import AssistantChatRun

        run = _make_main_agent_run(
            self.db,
            status="running",
            required_app_build_revision=BUILD_OLD,
            state_revision=3,
        )
        frozen_kind = run.runtime_kind
        frozen_build = run.required_app_build_revision
        frozen_rev = run.state_revision

        with patch("app.assistant.durable.admission.get_settings") as gs:
            settings = MagicMock()
            settings.assistant_main_agent_mode = "off"
            settings.app_build_revision = BUILD_NEW
            settings.assistant_worker_registration_ttl_sec = 20
            gs.return_value = settings
            # New admissions fall back to legacy when mode is off.
            kind, reason, _ = admit_and_select_runtime(self.db, mode="off")
        self.assertEqual(kind, "legacy")
        self.assertEqual(reason, "mode_off")

        # Existing Run is unchanged: no runtime_kind flip, no revision bump.
        reloaded = self.db.get(AssistantChatRun, run.id)
        assert reloaded is not None
        self.assertEqual(reloaded.runtime_kind, frozen_kind)
        self.assertEqual(reloaded.required_app_build_revision, frozen_build)
        self.assertEqual(int(reloaded.state_revision), frozen_rev)
        self.assertEqual(reloaded.status, "running")


class AdmissionNoCompatibleWorkerTests(unittest.TestCase):
    """API admission fails safely without a compatible worker heartbeat."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_no_worker_registration_returns_no_compatible_worker(self) -> None:
        from app.assistant.durable.admission import admit_and_select_runtime

        fake_admission = MagicMock()
        fake_admission.snapshot = None

        with (
            patch("app.assistant.durable.admission.get_settings") as gs,
            patch(
                "app.assistant.main_agent.service.should_construct_main_agent",
                return_value=True,
            ),
            patch(
                "app.assistant.main_agent.service.admit_main_agent",
                return_value=fake_admission,
            ),
        ):
            settings = MagicMock()
            settings.assistant_main_agent_mode = "read_only"
            settings.app_build_revision = BUILD_NEW
            settings.assistant_worker_registration_ttl_sec = 20
            gs.return_value = settings
            kind, reason, kwargs = admit_and_select_runtime(
                self.db,
                mode="read_only",
                app_build_revision=BUILD_NEW,
            )

        self.assertEqual(kind, "legacy")
        self.assertEqual(reason, "no_compatible_worker")
        self.assertEqual(kwargs, {})

    def test_stale_or_draining_worker_not_admitted(self) -> None:
        from app.assistant.durable.admission import admit_and_select_runtime
        from app.assistant.durable.worker_registry import WorkerRegistry
        from app.common.time import utcnow

        reg = WorkerRegistry(self.db)
        identity = _identity(app_build_revision=BUILD_NEW)
        reg.register(identity)
        # Force stale heartbeat.
        row = reg.get(identity.worker_id)
        assert row is not None
        row.heartbeat_at = utcnow() - timedelta(hours=1)
        self.db.commit()

        fake_admission = MagicMock()
        fake_admission.snapshot = None
        with (
            patch("app.assistant.durable.admission.get_settings") as gs,
            patch(
                "app.assistant.main_agent.service.should_construct_main_agent",
                return_value=True,
            ),
            patch(
                "app.assistant.main_agent.service.admit_main_agent",
                return_value=fake_admission,
            ),
        ):
            settings = MagicMock()
            settings.assistant_main_agent_mode = "read_only"
            settings.app_build_revision = BUILD_NEW
            settings.assistant_worker_registration_ttl_sec = 20
            gs.return_value = settings
            kind, reason, _ = admit_and_select_runtime(
                self.db,
                mode="read_only",
                app_build_revision=BUILD_NEW,
            )
        self.assertEqual(kind, "legacy")
        self.assertEqual(reason, "no_compatible_worker")

        # Fresh register then drain — still not admitted for new Runs.
        reg.register(identity)
        reg.mark_draining(identity.worker_id)
        with (
            patch("app.assistant.durable.admission.get_settings") as gs,
            patch(
                "app.assistant.main_agent.service.should_construct_main_agent",
                return_value=True,
            ),
            patch(
                "app.assistant.main_agent.service.admit_main_agent",
                return_value=fake_admission,
            ),
        ):
            settings = MagicMock()
            settings.assistant_main_agent_mode = "read_only"
            settings.app_build_revision = BUILD_NEW
            settings.assistant_worker_registration_ttl_sec = 60
            gs.return_value = settings
            kind2, reason2, _ = admit_and_select_runtime(
                self.db,
                mode="read_only",
                app_build_revision=BUILD_NEW,
            )
        self.assertEqual(kind2, "legacy")
        self.assertEqual(reason2, "no_compatible_worker")


class RollingDeployDrainTests(unittest.TestCase):
    """Old compatible worker drains old-build Runs; new build cannot claim them."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_old_worker_claims_old_build_run(self) -> None:
        run = _make_main_agent_run(
            self.db,
            status="queued",
            required_app_build_revision=BUILD_OLD,
        )
        old = _lease_service(self.db, app_build_revision=BUILD_OLD)
        claimed = old.claim_next()
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.run_id, run.id)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.kind, "queued")

    def test_new_worker_cannot_claim_old_build_run(self) -> None:
        _make_main_agent_run(
            self.db,
            status="queued",
            required_app_build_revision=BUILD_OLD,
        )
        new = _lease_service(self.db, app_build_revision=BUILD_NEW)
        self.assertIsNone(new.claim_next())

    def test_old_worker_drains_while_new_worker_serves_new_build(self) -> None:
        """Rolling deploy: keep old image until old Runs drain; new image takes new."""
        old_run = _make_main_agent_run(
            self.db,
            status="queued",
            required_app_build_revision=BUILD_OLD,
        )
        new_run = _make_main_agent_run(
            self.db,
            status="queued",
            required_app_build_revision=BUILD_NEW,
        )

        old = _lease_service(self.db, app_build_revision=BUILD_OLD)
        new = _lease_service(self.db, app_build_revision=BUILD_NEW)

        claimed_old = old.claim_next()
        claimed_new = new.claim_next()

        self.assertIsNotNone(claimed_old)
        self.assertIsNotNone(claimed_new)
        assert claimed_old is not None and claimed_new is not None
        self.assertEqual(claimed_old.run_id, old_run.id)
        self.assertEqual(claimed_new.run_id, new_run.id)

        # Cross-claim remains impossible after the first claim.
        self.assertIsNone(old.claim_next())
        self.assertIsNone(new.claim_next())

    def test_build_mismatch_recovery_needs_reconciliation(self) -> None:
        """New worker must not execute old-build recovering Runs."""
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(
            self.db,
            status="recovering",
            required_app_build_revision=BUILD_OLD,
            state_revision=1,
        )
        decision = RecoveryClassifier(self.db).classify(
            run=run,
            claim_kind="takeover_running",
            worker_app_build_revision=BUILD_NEW,
        )
        self.assertEqual(decision.kind, "needs_reconciliation")
        self.assertEqual(decision.reason_code, "build_revision_mismatch")
        self.assertFalse(decision.allow_provider_io)
        self.assertFalse(decision.allow_capability_io)


class Plan07StillDisabledTests(unittest.TestCase):
    """Production Main Agent ceiling keeps interrupt_mode=durable out of Plan 06."""

    def test_read_only_ceiling_forbids_durable_interrupt(self) -> None:
        from app.assistant.main_agent.authorization import (
            MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        )

        self.assertEqual(
            MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_interrupt_modes, ("none",)
        )
        self.assertNotIn(
            "durable", MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_interrupt_modes
        )
        self.assertEqual(
            set(MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_side_effects),
            {"none", "read", "compute"},
        )

    def test_platform_policy_rejects_durable_interrupt_in_ceiling_factory(self) -> None:
        from app.assistant.capabilities.policy import build_openclaw_effect_ceiling

        with self.assertRaises(ValueError):
            build_openclaw_effect_ceiling(
                ceiling_scope="system_item",
                ceiling_key="task10-probe",
                revision="plan06-v1",
                maximum_effect="compute",
                allowed_interrupt_modes=("none", "durable"),  # type: ignore[arg-type]
            )

    def test_default_mode_remains_off(self) -> None:
        from app.config import get_settings

        reset_caches()
        settings = get_settings()
        self.assertEqual(settings.assistant_main_agent_mode, "off")


class ConfigAndArtifactPolicyEvidenceTests(unittest.TestCase):
    """Record formula values + private Artifact bucket contract symbols."""

    def test_orphan_grace_defaults_above_floor(self) -> None:
        from app.config import (
            compute_artifact_orphan_grace_floor_sec,
            get_settings,
        )

        reset_caches()
        s = get_settings()
        floor = compute_artifact_orphan_grace_floor_sec(
            lease_ttl_sec=s.assistant_worker_lease_ttl_sec,
            retry_base_ms=s.assistant_worker_retry_base_ms,
            retry_max_ms=s.assistant_worker_retry_max_ms,
            max_recovery_attempts=s.assistant_worker_max_recovery_attempts,
            orphan_scan_interval_sec=s.assistant_artifact_orphan_scan_interval_sec,
            clock_skew_sec=s.assistant_durable_clock_skew_sec,
        )
        # Defaults: lease=30, backoff sum for 5 attempts with base 500/max 30000,
        # scan=60, skew=30 → floor 136; configured grace 900.
        self.assertEqual(floor, 136)
        self.assertGreaterEqual(s.assistant_artifact_orphan_grace_sec, floor)
        self.assertEqual(s.assistant_worker_lease_ttl_sec, 30)
        self.assertEqual(s.assistant_worker_heartbeat_interval_sec, 5)
        self.assertLess(
            s.assistant_worker_heartbeat_interval_sec * 3,
            s.assistant_worker_lease_ttl_sec,
        )
        self.assertEqual(s.assistant_artifact_bucket, "mindatlas-assistant-artifacts")

    def test_codec_and_runtime_contract_versions(self) -> None:
        from app.assistant.durable.codec import SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )

        self.assertEqual(RUNTIME_CONTRACT_VERSION, 1)
        self.assertEqual(list(SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS), [1])
        digest = default_capability_feature_digest()
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            digest,
            "de9af0d91cca357a53e11ac65c614fac5403484ed22bdb3bac55ac4f36b9c63a",
        )

    def test_minio_init_script_keeps_artifact_bucket_private(self) -> None:
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "deploy" / "minio-init.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("ASSISTANT_ARTIFACT_BUCKET", text)
        self.assertIn('mc anonymous set none "myminio/${ASSISTANT_ARTIFACT_BUCKET}"', text)
        self.assertIn("NEVER assign anonymous policy", text)
        # Attachment bucket remains public-download; Artifact must not reuse it.
        self.assertIn('mc anonymous set download "myminio/${MINIO_BUCKET}"', text)
        self.assertIn(
            'ASSISTANT_ARTIFACT_BUCKET must be distinct from MINIO_BUCKET',
            text,
        )


if __name__ == "__main__":
    unittest.main()
