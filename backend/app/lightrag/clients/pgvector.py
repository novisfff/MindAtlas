"""pgvector extension check and initialization."""
from __future__ import annotations

import logging
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PgVectorError(Exception):
    """pgvector extension not available."""
    pass


def check_pgvector_extension(db: Session) -> tuple[bool, str]:
    """Check if pgvector extension is available.

    Args:
        db: SQLAlchemy session

    Returns:
        Tuple of (is_available, message)
    """
    from app.config import get_settings

    settings = get_settings()

    if not settings.pgvector_enabled:
        return False, "pgvector is not enabled (PGVECTOR_ENABLED=false)"

    try:
        result = db.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
        if result.fetchone():
            return True, "pgvector extension is available"
        else:
            return False, "pgvector extension not found (run: CREATE EXTENSION vector;)"
    except Exception as e:
        return False, f"pgvector check failed: {e}"
