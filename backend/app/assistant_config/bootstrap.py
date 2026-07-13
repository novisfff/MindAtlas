from __future__ import annotations

import logging

from app.assistant_config.service import AssistantConfigService
from app.database import SessionLocal


def warm_assistant_config_system_catalog() -> None:
    """Warm legacy system catalog, then mirror into disabled v2 shadow packages.

    Order is intentional:
    1. system Tools / Workflows / Agents and legacy Skills are restored;
    2. published target versions exist;
    3. best-effort LegacySkillShadowAdapter.sync_all runs (authoritative repair).
    """
    logger = logging.getLogger("app.startup")
    db = SessionLocal()
    try:
        AssistantConfigService(db).ensure_system_catalog_warm()
        # Shadow sync is best-effort and must not fail startup after legacy warm.
        from app.assistant.skills.legacy_adapter import best_effort_sync_all

        best_effort_sync_all(db)
    except Exception:
        db.rollback()
        logger.exception("assistant_config_system_catalog_sync_failed")
        raise
    finally:
        db.close()
