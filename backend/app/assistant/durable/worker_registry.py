"""Worker registration and compatible-admission queries (Plan 06 Task 5).

A worker process advertises its app-build, runtime contract, checkpoint codec
support, and capability feature digest. API admission and claim filters require
a fresh non-draining registration that matches the Run's requirements.

The worker ID is generated from a stable instance label plus a boot UUID; it is
never accepted from model/user input.
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.codec import (
    CURRENT_CHECKPOINT_CODEC_VERSION,
    SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS,
)
from app.assistant.durable.models import AssistantWorkerRegistration
from app.common.time import utcnow
from app.config import get_settings

# Plan 06 locks runtime contract version 1 for Main Agent durable Runs.
RUNTIME_CONTRACT_VERSION = 1


def plan08_capability_ledger_feature_digest() -> str:
    """Stable digest advertised by workers that can execute Plan 08 ledgers.

    This is a release compatibility boundary, not a digest of runtime settings.
    Old workers retain their prior digest; enforced admissions and frozen
    enforced Runs require this exact contract before any worker can execute
    them.
    """
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
    )

    return sha256_canonical_json(
        {
            "runtimeContractVersion": RUNTIME_CONTRACT_VERSION,
            "supportedCheckpointCodecVersions": sorted(
                int(v) for v in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS
            ),
            "capabilityLedger": {
                "contractVersion": 1,
                "modes": ["legacy_read_only", "enforced"],
                "attemptLifecycle": [
                    "claimed",
                    "dispatched",
                    "response_received",
                    "committed",
                ],
                "checkpointSchemaVersion": 3,
            },
            "createEntryWriteSafety": {
                "createEntryContractDigest": CREATE_ENTRY_CONTRACT_DIGEST,
                "writePolicyDigest": WRITE_POLICY_DIGEST,
                "writeCohortDigest": WRITE_COHORT_DIGEST,
                "reconciliationContractVersion": RECONCILIATION_CONTRACT_VERSION,
            },
        }
    )


def default_capability_feature_digest() -> str:
    """Feature digest advertised by workers built from the current source."""
    return plan08_capability_ledger_feature_digest()


def generate_worker_id(*, instance_label: str | None = None) -> str:
    """Build a worker ID from a stable instance label + random boot UUID.

    Never accept a worker ID from model/user input.
    """
    label = (instance_label or "").strip()
    if not label:
        host = socket.gethostname() or "worker"
        label = f"{host}-{os.getpid()}"
    # Keep within column length (160) with room for UUID suffix.
    label = label[:100]
    return f"{label}:{uuid.uuid4().hex}"


@dataclass(frozen=True)
class WorkerIdentity:
    """Immutable process identity for one worker boot."""

    worker_id: str
    app_build_revision: str
    runtime_contract_version: int = RUNTIME_CONTRACT_VERSION
    supported_checkpoint_codec_versions: tuple[int, ...] = field(
        default_factory=lambda: tuple(sorted(SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS))
    )
    capability_feature_digest: str = field(default_factory=default_capability_feature_digest)
    hostname_label: str | None = None

    def __post_init__(self) -> None:
        if not str(self.worker_id or "").strip():
            raise ValueError("worker_id must be nonempty")
        if not str(self.app_build_revision or "").strip():
            raise ValueError("app_build_revision must be nonempty")
        if int(self.runtime_contract_version) <= 0:
            raise ValueError("runtime_contract_version must be positive")
        if not self.supported_checkpoint_codec_versions:
            raise ValueError("supported_checkpoint_codec_versions must be nonempty")
        digest = str(self.capability_feature_digest or "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError("capability_feature_digest must be a 64-char hex digest")

    @classmethod
    def from_settings(
        cls,
        *,
        worker_id: str | None = None,
        instance_label: str | None = None,
        settings: Any | None = None,
    ) -> WorkerIdentity:
        s = settings or get_settings()
        build = str(getattr(s, "app_build_revision", None) or "development").strip()
        host = socket.gethostname() or None
        configured_label = instance_label or os.environ.get("ASSISTANT_WORKER_INSTANCE_LABEL")
        return cls(
            worker_id=worker_id or generate_worker_id(instance_label=configured_label),
            app_build_revision=build,
            runtime_contract_version=RUNTIME_CONTRACT_VERSION,
            supported_checkpoint_codec_versions=tuple(
                sorted(int(v) for v in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS)
            ),
            capability_feature_digest=default_capability_feature_digest(),
            hostname_label=host,
        )


@dataclass(frozen=True)
class WorkerCompatibility:
    """Canonical Run/closure requirements a worker registration must satisfy.

    Feature digest is required for production Main Agent Runs — never optional
    on the construction path. ``matches`` requires exact build, contract, feature
    digest equality, and membership of ``required_checkpoint_codec_version`` in
    the identity's supported codec set.
    """

    app_build_revision: str
    runtime_contract_version: int
    required_checkpoint_codec_version: int
    required_capability_feature_digest: str
    required_create_entry_contract_digest: str | None = None
    required_write_policy_digest: str | None = None
    required_write_cohort_digest: str | None = None
    required_reconciliation_contract_version: int | None = None

    def __post_init__(self) -> None:
        if not str(self.app_build_revision or "").strip():
            raise ValueError("app_build_revision must be nonempty")
        if int(self.runtime_contract_version) <= 0:
            raise ValueError("runtime_contract_version must be positive")
        if int(self.required_checkpoint_codec_version) <= 0:
            raise ValueError("required_checkpoint_codec_version must be positive")
        digest = str(self.required_capability_feature_digest or "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError(
                "required_capability_feature_digest must be a 64-char hex digest"
            )
        from app.assistant.capability_calls.write_guard import (
            CREATE_ENTRY_CONTRACT_DIGEST,
            RECONCILIATION_CONTRACT_VERSION,
            WRITE_COHORT_DIGEST,
            WRITE_POLICY_DIGEST,
        )

        write_requirements = (
            CREATE_ENTRY_CONTRACT_DIGEST
            if self.required_create_entry_contract_digest is None
            else self.required_create_entry_contract_digest,
            WRITE_POLICY_DIGEST
            if self.required_write_policy_digest is None
            else self.required_write_policy_digest,
            WRITE_COHORT_DIGEST
            if self.required_write_cohort_digest is None
            else self.required_write_cohort_digest,
            RECONCILIATION_CONTRACT_VERSION
            if self.required_reconciliation_contract_version is None
            else self.required_reconciliation_contract_version,
        )
        for name, value in zip(
            (
                "required_create_entry_contract_digest",
                "required_write_policy_digest",
                "required_write_cohort_digest",
            ),
            write_requirements[:3],
            strict=True,
        ):
            digest = str(value or "")
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest.lower()
            ):
                raise ValueError(f"{name} must be a 64-char hex digest")
        if int(write_requirements[3] or 0) <= 0:
            raise ValueError(
                "required_reconciliation_contract_version must be positive"
            )
        object.__setattr__(
            self, "required_create_entry_contract_digest", write_requirements[0]
        )
        object.__setattr__(self, "required_write_policy_digest", write_requirements[1])
        object.__setattr__(self, "required_write_cohort_digest", write_requirements[2])
        object.__setattr__(
            self, "required_reconciliation_contract_version", write_requirements[3]
        )

    @classmethod
    def from_closure(cls, closure: Any) -> "WorkerCompatibility":
        """Build requirements from a release/runtime closure."""
        return cls(
            app_build_revision=str(getattr(closure, "build_revision", "") or ""),
            runtime_contract_version=int(
                getattr(closure, "runtime_contract_version", 0) or 0
            ),
            required_checkpoint_codec_version=int(
                getattr(closure, "checkpoint_codec_version", 0) or 0
            ),
            required_capability_feature_digest=str(
                getattr(closure, "capability_feature_digest", "") or ""
            ),
            required_create_entry_contract_digest=str(
                getattr(closure, "create_entry_contract_digest", "") or ""
            ),
            required_write_policy_digest=str(
                getattr(closure, "write_policy_digest", "") or ""
            ),
            required_write_cohort_digest=str(
                getattr(closure, "write_cohort_digest", "") or ""
            ),
            required_reconciliation_contract_version=int(
                getattr(closure, "reconciliation_contract_version", 0) or 0
            ),
        )

    @classmethod
    def from_run(cls, run: Any) -> "WorkerCompatibility":
        """Build requirements from a Run's frozen admission fields.

        Never rewrites Run requirements to match a Worker — claim filters
        consume the Run as-is.
        """
        return cls(
            app_build_revision=str(
                getattr(run, "required_app_build_revision", "") or ""
            ),
            runtime_contract_version=int(
                getattr(run, "runtime_contract_version", 0) or 0
            ),
            required_checkpoint_codec_version=int(
                getattr(run, "required_checkpoint_codec_version", 0) or 0
            ),
            required_capability_feature_digest=str(
                getattr(run, "required_capability_feature_digest", "") or ""
            ),
            required_create_entry_contract_digest=str(
                getattr(run, "required_create_entry_contract_digest", "") or ""
            ),
            required_write_policy_digest=str(
                getattr(run, "required_write_policy_digest", "") or ""
            ),
            required_write_cohort_digest=str(
                getattr(run, "required_write_cohort_digest", "") or ""
            ),
            required_reconciliation_contract_version=int(
                getattr(run, "required_reconciliation_contract_version", 0) or 0
            ),
        )

    def matches(self, identity: WorkerIdentity | AssistantWorkerRegistration) -> bool:
        build = str(getattr(identity, "app_build_revision", "") or "")
        contract = int(getattr(identity, "runtime_contract_version", 0) or 0)
        supported = getattr(identity, "supported_checkpoint_codec_versions", None) or []
        if isinstance(supported, str):
            # Defensive: JSON may arrive as a string in some drivers.
            supported = []
        try:
            supported_set = {int(v) for v in supported}
        except (TypeError, ValueError):
            return False
        feature_digest = str(getattr(identity, "capability_feature_digest", "") or "")
        from app.assistant.capability_calls.write_guard import (
            CREATE_ENTRY_CONTRACT_DIGEST,
            RECONCILIATION_CONTRACT_VERSION,
            WRITE_COHORT_DIGEST,
            WRITE_POLICY_DIGEST,
        )
        return (
            build == str(self.app_build_revision)
            and contract == int(self.runtime_contract_version)
            and int(self.required_checkpoint_codec_version) in supported_set
            and feature_digest == str(self.required_capability_feature_digest)
            and self.required_create_entry_contract_digest
            == CREATE_ENTRY_CONTRACT_DIGEST
            and self.required_write_policy_digest == WRITE_POLICY_DIGEST
            and self.required_write_cohort_digest == WRITE_COHORT_DIGEST
            and self.required_reconciliation_contract_version
            == RECONCILIATION_CONTRACT_VERSION
        )


class WorkerRegistry:
    """Mutable worker liveness registration (not a Run lease)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def register(self, identity: WorkerIdentity) -> AssistantWorkerRegistration:
        """Upsert registration for this boot identity (fresh heartbeat)."""
        now = self._db_now()
        row = self.db.get(AssistantWorkerRegistration, identity.worker_id)
        if row is None:
            row = AssistantWorkerRegistration(
                worker_id=identity.worker_id,
                app_build_revision=identity.app_build_revision,
                runtime_contract_version=int(identity.runtime_contract_version),
                supported_checkpoint_codec_versions=list(
                    identity.supported_checkpoint_codec_versions
                ),
                capability_feature_digest=identity.capability_feature_digest,
                started_at=now,
                heartbeat_at=now,
                draining_at=None,
                hostname_label=identity.hostname_label,
            )
            self.db.add(row)
        else:
            row.app_build_revision = identity.app_build_revision
            row.runtime_contract_version = int(identity.runtime_contract_version)
            row.supported_checkpoint_codec_versions = list(
                identity.supported_checkpoint_codec_versions
            )
            row.capability_feature_digest = identity.capability_feature_digest
            row.heartbeat_at = now
            row.draining_at = None
            if identity.hostname_label is not None:
                row.hostname_label = identity.hostname_label
        self.db.commit()
        self.db.refresh(row)
        return row

    def heartbeat(self, worker_id: str) -> bool:
        """Refresh registration heartbeat. Returns False if unknown."""
        now = self._db_now()
        result = self.db.execute(
            update(AssistantWorkerRegistration)
            .where(AssistantWorkerRegistration.worker_id == str(worker_id))
            .values(heartbeat_at=now)
        )
        if result.rowcount == 0:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def mark_draining(self, worker_id: str) -> bool:
        """Mark registration draining (stop new claims; keep heartbeats)."""
        now = self._db_now()
        result = self.db.execute(
            update(AssistantWorkerRegistration)
            .where(AssistantWorkerRegistration.worker_id == str(worker_id))
            .values(draining_at=now, heartbeat_at=now)
        )
        if result.rowcount == 0:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def get(self, worker_id: str) -> AssistantWorkerRegistration | None:
        return self.db.get(AssistantWorkerRegistration, str(worker_id))

    def is_fresh(
        self,
        row: AssistantWorkerRegistration,
        *,
        registration_ttl: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """True when heartbeat is within TTL and not draining."""
        if row.draining_at is not None:
            return False
        hb = row.heartbeat_at
        if hb is None:
            return False
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        clock = now or self._db_now()
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)
        return hb + registration_ttl >= clock

    def has_compatible_worker(
        self,
        compatibility: WorkerCompatibility,
        *,
        registration_ttl: timedelta | None = None,
    ) -> bool:
        """True when at least one fresh non-draining compatible worker exists."""
        return (
            self.find_compatible_workers(
                compatibility,
                registration_ttl=registration_ttl,
                limit=1,
            )
            != []
        )

    def find_compatible_workers(
        self,
        compatibility: WorkerCompatibility,
        *,
        registration_ttl: timedelta | None = None,
        limit: int = 50,
    ) -> list[AssistantWorkerRegistration]:
        """Return fresh non-draining registrations matching ``compatibility``.

        Filters by database-time heartbeat cutoff, ``draining_at IS NULL``, exact
        build/contract/feature digest, then requires codec membership. Orders by
        ``worker_id ASC`` after compatibility filtering. Never mutates
        registration state.
        """
        ttl = registration_ttl
        if ttl is None:
            s = get_settings()
            ttl = timedelta(seconds=int(s.assistant_worker_registration_ttl_sec))
        now = self._db_now()
        cutoff = now - ttl

        # Codec versions are stored as JSON arrays of ints. Compare in Python
        # after a build/contract/heartbeat/digest prefilter — keeps SQLite + PG
        # simple while preserving deterministic worker_id order.
        stmt = (
            select(AssistantWorkerRegistration)
            .where(
                AssistantWorkerRegistration.app_build_revision
                == str(compatibility.app_build_revision),
                AssistantWorkerRegistration.runtime_contract_version
                == int(compatibility.runtime_contract_version),
                AssistantWorkerRegistration.capability_feature_digest
                == str(compatibility.required_capability_feature_digest),
                AssistantWorkerRegistration.heartbeat_at >= cutoff,
                AssistantWorkerRegistration.draining_at.is_(None),
            )
            .order_by(AssistantWorkerRegistration.worker_id.asc())
        )

        rows = list(self.db.scalars(stmt).all())
        matched: list[AssistantWorkerRegistration] = []
        for row in rows:
            # matches encodes build/contract/feature equality + codec membership.
            if not compatibility.matches(row):
                continue
            matched.append(row)
            if len(matched) >= int(limit):
                break
        return matched

    def healthcheck(
        self,
        *,
        worker_id: str,
        app_build_revision: str,
        runtime_contract_version: int = RUNTIME_CONTRACT_VERSION,
        required_checkpoint_codec_version: int = CURRENT_CHECKPOINT_CODEC_VERSION,
        required_capability_feature_digest: str = default_capability_feature_digest(),
        registration_ttl: timedelta | None = None,
    ) -> dict[str, Any]:
        """Validate a fresh compatible registration (not mere PID liveness).

        Used by the Docker healthcheck for the assistant-worker service.
        Reports only stable reasons — never raw SQL/Alembic errors.
        """
        ttl = registration_ttl
        if ttl is None:
            s = get_settings()
            ttl = timedelta(seconds=int(s.assistant_worker_registration_ttl_sec))
        row = self.get(worker_id)
        if row is None:
            return {
                "ok": False,
                "reason": "registration_missing",
                "worker_id": worker_id,
            }
        if not self.is_fresh(row, registration_ttl=ttl):
            return {
                "ok": False,
                "reason": "registration_stale_or_draining",
                "worker_id": worker_id,
                "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
                "draining_at": row.draining_at.isoformat() if row.draining_at else None,
            }
        try:
            compat = WorkerCompatibility(
                app_build_revision=app_build_revision,
                runtime_contract_version=runtime_contract_version,
                required_checkpoint_codec_version=required_checkpoint_codec_version,
                required_capability_feature_digest=required_capability_feature_digest,
            )
        except ValueError:
            return {
                "ok": False,
                "reason": "registration_incompatible",
                "worker_id": worker_id,
            }
        if not compat.matches(row):
            return {
                "ok": False,
                "reason": "registration_incompatible",
                "worker_id": worker_id,
                "app_build_revision": row.app_build_revision,
                "runtime_contract_version": row.runtime_contract_version,
                "supported_checkpoint_codec_versions": row.supported_checkpoint_codec_versions,
            }
        return {
            "ok": True,
            "reason": "fresh_compatible",
            "worker_id": worker_id,
            "app_build_revision": row.app_build_revision,
            "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        }

    def _db_now(self) -> datetime:
        """Prefer database time; fall back to process UTC."""
        try:
            value = self.db.scalar(select(func.now()))
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    return value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
        except Exception:
            pass
        return utcnow()


__all__ = [
    "RUNTIME_CONTRACT_VERSION",
    "WorkerCompatibility",
    "WorkerIdentity",
    "WorkerRegistry",
    "default_capability_feature_digest",
    "generate_worker_id",
    "plan08_capability_ledger_feature_digest",
]
