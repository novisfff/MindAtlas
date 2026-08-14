"""Database-time, locking, replay, and CAS primitives for launch state."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.pre_ga_launch.models import PreGaLaunchCandidate, PreGaLaunchControl, PreGaLaunchGateUse


LAUNCH_ADVISORY_LOCK_KEY = 0x4D494E444C41554E


def request_digest(*, action: str, operator_id: UUID, request_fields: dict[str, object]) -> str:
    return sha256_canonical_json(
        {
            "domain": "mindatlas:pre-ga-launch-request:v1",
            "action": action,
            "operatorId": str(operator_id),
            **request_fields,
        }
    )


class LaunchRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def lock_launch(self) -> None:
        bind = self.db.get_bind()
        if bind.dialect.name == "postgresql":
            self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LAUNCH_ADVISORY_LOCK_KEY})

    def database_now(self) -> datetime:
        bind = self.db.get_bind()
        if bind.dialect.name == "postgresql":
            value = self.db.execute(text("SELECT CURRENT_TIMESTAMP AT TIME ZONE 'UTC'")).scalar_one()
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    def find_candidate_by_request_id(self, request_id: UUID, *, for_update: bool = False) -> PreGaLaunchCandidate | None:
        query = self.db.query(PreGaLaunchCandidate).filter(PreGaLaunchCandidate.creation_request_id == request_id)
        if for_update and self.db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update()
        return query.one_or_none()

    def find_gate_use_by_request_id(self, request_id: UUID, *, for_update: bool = False) -> PreGaLaunchGateUse | None:
        query = self.db.query(PreGaLaunchGateUse).filter(PreGaLaunchGateUse.consumption_request_id == request_id)
        if for_update and self.db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update()
        return query.one_or_none()

    def lock_control(self) -> PreGaLaunchControl:
        query = self.db.query(PreGaLaunchControl).filter(PreGaLaunchControl.singleton_key == "pre_ga_launch")
        if self.db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update()
        control = query.one_or_none()
        if control is None:
            raise RuntimeError("launch_control_missing")
        return control

    def insert_candidate(self, candidate: PreGaLaunchCandidate) -> PreGaLaunchCandidate:
        self.db.add(candidate)
        self.db.flush()
        return candidate

    def append_use_and_advance_control(
        self,
        *,
        use: PreGaLaunchGateUse,
        expected_revision: int,
        subject_digest: str,
        candidate_id: UUID,
        used_at: datetime,
    ) -> PreGaLaunchControl:
        """Append one use and CAS the singleton in the same transaction."""
        self.db.add(use)
        self.db.flush()
        result = self.db.execute(
            update(PreGaLaunchControl)
            .where(
                PreGaLaunchControl.singleton_key == "pre_ga_launch",
                PreGaLaunchControl.revision == expected_revision,
            )
            .values(
                active_subject_digest=subject_digest,
                active_candidate_id=candidate_id,
                active_gate_use_id=use.id,
                revision=use.resulting_control_revision,
                launched_at=used_at,
                updated_at=used_at,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("launch_control_conflict")
        self.db.flush()
        control = self.db.get(PreGaLaunchControl, "pre_ga_launch")
        if control is None:
            raise RuntimeError("launch_control_missing")
        return control


__all__ = ["LAUNCH_ADVISORY_LOCK_KEY", "LaunchRepository", "request_digest"]
