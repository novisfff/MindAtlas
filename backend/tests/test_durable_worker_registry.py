"""Plan 06 Task 5 + Plan 2 Task 7: worker registration + compatible-admission tests.

Covers:
- register / heartbeat / draining
- build/codec mismatch is not admitted
- API admission sees no compatible worker when build/codec differ
- healthcheck validates fresh compatible registration (not mere PID)
- WorkerCompatibility is the sole matcher (from_closure / from_run / order)
"""

from __future__ import annotations

import unittest
import uuid
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


DIGEST = "a" * 64


def _identity(**kwargs):
    from app.assistant.durable.codec import SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS
    from app.assistant.durable.worker_registry import WorkerIdentity

    defaults = dict(
        worker_id=f"w-{uuid.uuid4().hex[:12]}",
        app_build_revision="build-test-1",
        runtime_contract_version=1,
        # Align with production workers: full supported codec set so the
        # healthcheck default (CURRENT_CHECKPOINT_CODEC_VERSION) matches.
        supported_checkpoint_codec_versions=tuple(
            sorted(SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS)
        ),
        capability_feature_digest=DIGEST,
        hostname_label="test-host",
    )
    defaults.update(kwargs)
    return WorkerIdentity(**defaults)


def _compat(**kwargs):
    from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
    from app.assistant.durable.worker_registry import WorkerCompatibility

    defaults = dict(
        app_build_revision="build-test-1",
        runtime_contract_version=1,
        required_checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
        required_capability_feature_digest=DIGEST,
    )
    defaults.update(kwargs)
    return WorkerCompatibility(**defaults)


class WorkerRegistryUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_register_and_heartbeat_fresh(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        identity = _identity()
        row = reg.register(identity)
        self.assertEqual(row.worker_id, identity.worker_id)
        self.assertEqual(row.app_build_revision, "build-test-1")
        self.assertIsNone(row.draining_at)
        self.assertTrue(reg.is_fresh(row, registration_ttl=timedelta(seconds=20)))
        self.assertTrue(reg.heartbeat(identity.worker_id))
        missing = reg.heartbeat("does-not-exist")
        self.assertFalse(missing)

    def test_generate_worker_id_not_from_user_input(self) -> None:
        from app.assistant.durable.worker_registry import generate_worker_id

        a = generate_worker_id(instance_label="api-1")
        b = generate_worker_id(instance_label="api-1")
        self.assertTrue(a.startswith("api-1:"))
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), 160)

    def test_draining_not_fresh_and_not_compatible_for_admission(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        identity = _identity()
        reg.register(identity)
        self.assertTrue(reg.mark_draining(identity.worker_id))
        row = reg.get(identity.worker_id)
        assert row is not None
        self.assertIsNotNone(row.draining_at)
        self.assertFalse(reg.is_fresh(row, registration_ttl=timedelta(seconds=60)))
        self.assertFalse(
            reg.has_compatible_worker(
                _compat(),
                registration_ttl=timedelta(seconds=60),
            )
        )

    def test_build_mismatch_not_admitted(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        reg.register(_identity(app_build_revision="build-A"))
        self.assertTrue(
            reg.has_compatible_worker(
                _compat(app_build_revision="build-A"),
                registration_ttl=timedelta(seconds=60),
            )
        )
        self.assertFalse(
            reg.has_compatible_worker(
                _compat(app_build_revision="build-B"),
                registration_ttl=timedelta(seconds=60),
            )
        )

    def test_codec_mismatch_not_admitted(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        # Worker only supports codec version 99 — not 1.
        reg.register(_identity(supported_checkpoint_codec_versions=(99,)))
        self.assertFalse(
            reg.has_compatible_worker(
                _compat(required_checkpoint_codec_version=1),
                registration_ttl=timedelta(seconds=60),
            )
        )
        self.assertTrue(
            reg.has_compatible_worker(
                _compat(required_checkpoint_codec_version=99),
                registration_ttl=timedelta(seconds=60),
            )
        )

    def test_required_capability_feature_digest_must_match(self) -> None:
        from app.assistant.durable.worker_registry import (
            WorkerRegistry,
            plan08_capability_ledger_feature_digest,
        )

        reg = WorkerRegistry(self.db)
        reg.register(_identity(capability_feature_digest=DIGEST))

        self.assertFalse(
            reg.has_compatible_worker(
                _compat(
                    required_capability_feature_digest=(
                        plan08_capability_ledger_feature_digest()
                    )
                ),
                registration_ttl=timedelta(seconds=60),
            )
        )

        reg.register(
            _identity(
                capability_feature_digest=(plan08_capability_ledger_feature_digest())
            )
        )
        self.assertTrue(
            reg.has_compatible_worker(
                _compat(
                    required_capability_feature_digest=(
                        plan08_capability_ledger_feature_digest()
                    )
                ),
                registration_ttl=timedelta(seconds=60),
            )
        )

    def test_feature_digest_filter_does_not_hide_older_matching_worker(self) -> None:
        from app.assistant.durable.worker_registry import (
            WorkerRegistry,
            plan08_capability_ledger_feature_digest,
        )
        from app.common.time import utcnow

        reg = WorkerRegistry(self.db)
        matching = reg.register(
            _identity(
                capability_feature_digest=plan08_capability_ledger_feature_digest()
            )
        )
        matching.heartbeat_at = utcnow() - timedelta(seconds=10)
        for offset in range(5):
            row = reg.register(_identity(capability_feature_digest=DIGEST))
            row.heartbeat_at = utcnow() - timedelta(seconds=offset)
        self.db.commit()

        self.assertTrue(
            reg.has_compatible_worker(
                _compat(
                    required_capability_feature_digest=(
                        plan08_capability_ledger_feature_digest()
                    )
                ),
                registration_ttl=timedelta(seconds=60),
            )
        )

    def test_stale_registration_not_admitted(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry
        from app.common.time import utcnow

        reg = WorkerRegistry(self.db)
        identity = _identity()
        reg.register(identity)
        row = reg.get(identity.worker_id)
        assert row is not None
        # Force heartbeat into the past beyond TTL.
        row.heartbeat_at = utcnow() - timedelta(hours=1)
        self.db.commit()
        self.assertFalse(
            reg.has_compatible_worker(
                _compat(),
                registration_ttl=timedelta(seconds=20),
            )
        )

    def test_healthcheck_fresh_compatible(self) -> None:
        from app.assistant.durable.worker_registry import (
            WorkerRegistry,
            default_capability_feature_digest,
        )

        reg = WorkerRegistry(self.db)
        identity = _identity(
            capability_feature_digest=default_capability_feature_digest()
        )
        reg.register(identity)
        result = reg.healthcheck(
            worker_id=identity.worker_id,
            app_build_revision=identity.app_build_revision,
            registration_ttl=timedelta(seconds=60),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "fresh_compatible")

    def test_healthcheck_rejects_missing_stale_incompatible(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry
        from app.common.time import utcnow

        reg = WorkerRegistry(self.db)
        missing = reg.healthcheck(
            worker_id="nope",
            app_build_revision="build-test-1",
            registration_ttl=timedelta(seconds=60),
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason"], "registration_missing")

        identity = _identity()
        reg.register(identity)
        row = reg.get(identity.worker_id)
        assert row is not None
        row.heartbeat_at = utcnow() - timedelta(hours=2)
        self.db.commit()
        stale = reg.healthcheck(
            worker_id=identity.worker_id,
            app_build_revision=identity.app_build_revision,
            registration_ttl=timedelta(seconds=20),
        )
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason"], "registration_stale_or_draining")

        # Re-register fresh then check build mismatch.
        reg.register(identity)
        bad_build = reg.healthcheck(
            worker_id=identity.worker_id,
            app_build_revision="other-build",
            registration_ttl=timedelta(seconds=60),
        )
        self.assertFalse(bad_build["ok"])
        self.assertEqual(bad_build["reason"], "registration_incompatible")

    def test_compatibility_matches_identity(self) -> None:
        from app.assistant.durable.worker_registry import WorkerCompatibility

        identity = _identity(
            app_build_revision="b1",
            supported_checkpoint_codec_versions=(1, 2),
        )
        ok = WorkerCompatibility(
            app_build_revision="b1",
            runtime_contract_version=1,
            required_checkpoint_codec_version=2,
            required_capability_feature_digest=DIGEST,
        )
        bad = WorkerCompatibility(
            app_build_revision="b1",
            runtime_contract_version=1,
            required_checkpoint_codec_version=9,
            required_capability_feature_digest=DIGEST,
        )
        self.assertTrue(ok.matches(identity))
        self.assertFalse(bad.matches(identity))

    def test_worker_identity_from_settings(self) -> None:
        from app.assistant.durable.worker_registry import WorkerIdentity
        from app.config import get_settings

        settings = get_settings()
        identity = WorkerIdentity.from_settings(
            worker_id="fixed-worker-1",
            settings=settings,
        )
        self.assertEqual(identity.worker_id, "fixed-worker-1")
        self.assertEqual(
            identity.app_build_revision,
            str(settings.app_build_revision or "development"),
        )
        self.assertIn(1, identity.supported_checkpoint_codec_versions)

    def test_api_admission_no_compatible_worker_when_codec_mismatched(self) -> None:
        """API admission path: build matches but codec does not → no worker."""
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        # Compatible build, wrong codec.
        reg.register(
            _identity(
                app_build_revision="prod-build",
                supported_checkpoint_codec_versions=(2,),
            )
        )
        admitted = reg.has_compatible_worker(
            _compat(
                app_build_revision="prod-build",
                required_checkpoint_codec_version=1,
            ),
            registration_ttl=timedelta(seconds=60),
        )
        self.assertFalse(admitted)

    def test_find_compatible_workers_orders_by_worker_id_asc(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry

        reg = WorkerRegistry(self.db)
        reg.register(_identity(worker_id="worker-z"))
        reg.register(_identity(worker_id="worker-a"))
        reg.register(_identity(worker_id="worker-m"))
        rows = reg.find_compatible_workers(
            _compat(),
            registration_ttl=timedelta(seconds=60),
        )
        self.assertEqual(
            [r.worker_id for r in rows],
            ["worker-a", "worker-m", "worker-z"],
        )


class WorkerDockerHealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_healthcheck_rejects_unsuccessful_worker_state_without_db_lookup(self) -> None:
        from app.assistant.worker import run_healthcheck

        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "worker-state.json"
            state_path.write_text(
                '{"ok": false, "worker_id": "worker-1", '
                '"app_build_revision": "build-test-1", '
                '"reason": "schema_incompatible"}',
                encoding="utf-8",
            )
            session_factory = MagicMock(
                side_effect=AssertionError("unsuccessful state must not query DB")
            )
            with (
                patch("app.assistant.worker.SessionLocal", session_factory),
                redirect_stdout(StringIO()),
            ):
                exit_code = run_healthcheck(state_path=state_path)

        self.assertEqual(exit_code, 1)
        session_factory.assert_not_called()

    def test_healthcheck_rejects_registration_with_release_feature_digest_mismatch(self) -> None:
        from app.assistant.durable.worker_registry import WorkerRegistry
        from app.assistant.worker import run_healthcheck

        identity = _identity(capability_feature_digest=DIGEST)
        WorkerRegistry(self.db).register(identity)
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "worker-state.json"
            state_path.write_text(
                (
                    "{"
                    '"ok": true, '
                    f'"worker_id": "{identity.worker_id}", '
                    f'"app_build_revision": "{identity.app_build_revision}", '
                    f'"runtime_contract_version": {identity.runtime_contract_version}'
                    "}"
                ),
                encoding="utf-8",
            )
            with (
                patch("app.assistant.worker.SessionLocal", return_value=self.db),
                patch(
                    "app.assistant.worker.get_settings",
                    return_value=SimpleNamespace(
                        assistant_worker_registration_ttl_sec=60
                    ),
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = run_healthcheck(state_path=state_path)

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
