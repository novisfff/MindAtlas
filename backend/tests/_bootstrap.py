from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap_backend_imports() -> None:
    """Ensure `import app.*` works and avoids requiring PostgreSQL drivers in unit tests."""
    repo_root = Path(__file__).resolve().parents[2]
    backend_dir = repo_root / "backend"
    sys.path.insert(0, str(backend_dir))

    # Avoid importing psycopg2 just to import `app.database` / models in unit tests.
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")


def reset_caches() -> None:
    """Clear lru_cache-backed singletons to isolate tests."""
    # bootstrap first so these imports work
    bootstrap_backend_imports()

    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass

    try:
        from app.common.storage import get_minio_client

        get_minio_client.cache_clear()
    except Exception:
        pass

