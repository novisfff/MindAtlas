from __future__ import annotations

import logging

from app.assistant_config.service import AssistantConfigService
from app.database import SessionLocal


def warm_assistant_config_system_catalog() -> None:
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
