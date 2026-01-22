"""Neo4j client initialization and healthcheck."""
from __future__ import annotations

from functools import lru_cache
import logging

logger = logging.getLogger(__name__)


class Neo4jClientError(Exception):
    """Neo4j client initialization or connection error."""
    pass


@lru_cache(maxsize=1)
def get_neo4j_driver():
    """Get Neo4j driver singleton.

    Returns:
        Neo4j driver instance

    Raises:
        Neo4jClientError: If Neo4j is not configured or connection fails
    """
    from app.config import get_settings

    settings = get_settings()

    if not settings.lightrag_enabled:
        raise Neo4jClientError("LightRAG is not enabled (LIGHTRAG_ENABLED=false)")

    uri = settings.neo4j_uri.strip()
    user = settings.neo4j_user.strip()
    password = settings.neo4j_password.strip()

    if not uri or not user:
        raise Neo4jClientError("Neo4j URI or user is not configured")

    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise Neo4jClientError("neo4j driver not installed") from e

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        # Verify connectivity
        driver.verify_connectivity()
        logger.info(f"Neo4j driver initialized: {uri}")
        return driver
    except Exception as e:
        raise Neo4jClientError(f"Failed to connect to Neo4j: {e}") from e


def check_neo4j_health() -> tuple[bool, str]:
    """Check Neo4j connection health.

    Returns:
        Tuple of (is_healthy, message)
    """
    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        return True, "Neo4j is healthy"
    except Neo4jClientError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Neo4j healthcheck failed: {e}"
