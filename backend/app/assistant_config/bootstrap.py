from __future__ import annotations

import logging

from app.assistant_config.service import AssistantConfigService
from app.database import SessionLocal


def warm_assistant_config_system_catalog() -> None:
    """Warm system Tools / Workflows / Agents catalog at startup.

    Legacy Skill rows and shadow package sync were removed in Plan 10 B2.
    Universal skill packages are managed via Plan 09 admin, not bootstrap mirror.
    """
    logger = logging.getLogger("app.startup")
    db = SessionLocal()
    try:
        AssistantConfigService(db).ensure_system_catalog_warm()
    except Exception:
        db.rollback()
        logger.exception("assistant_config_system_catalog_sync_failed")
        raise
    finally:
        db.close()
