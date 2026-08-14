"""Server-side constructors for the pre-GA launch service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.pre_ga_launch.qualification_target import (
    ServerOwnedQualificationTargetProvider,
)
from app.pre_ga_launch.service import PreGaLaunchService
from app.release.evidence import ContentAddressedEvidenceStore
from app.release.trust import ReleaseEvidenceTrustError, load_trust_set


def default_pre_ga_launch_service(
    db: Session,
    *,
    settings: Any | None = None,
) -> PreGaLaunchService:
    """Construct a fail-closed service from deployment-owned configuration.

    Missing paths intentionally become missing dependencies instead of being
    replaced with test fixtures or request-provided locations.
    """
    active_settings = settings if settings is not None else get_settings()
    evidence_root = str(
        getattr(active_settings, "release_evidence_root", "") or ""
    ).strip()
    trust_path = str(
        getattr(active_settings, "release_trust_set_path", "") or ""
    ).strip()
    store = ContentAddressedEvidenceStore(Path(evidence_root)) if evidence_root else None
    trust = None
    if trust_path:
        try:
            trust = load_trust_set(Path(trust_path))
        except (OSError, UnicodeError, ValueError, ReleaseEvidenceTrustError):
            trust = None
    return PreGaLaunchService(
        db,
        evidence_store=store,
        trust_set=trust,
        target_provider=ServerOwnedQualificationTargetProvider(
            db,
            settings=active_settings,
        ).current,
    )


__all__ = ["default_pre_ga_launch_service"]
