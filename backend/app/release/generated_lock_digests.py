"""Generated SHA-256 identities for the reviewed Python lock set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

LOCK_SET_DOMAIN: Final = "mindatlas:python-lock-set:v1"
PYTHON_VERSION: Final = "3.11"
RELEASE_PLATFORM: Final = "linux/amd64"

API_WORKER_LOCK_SHA256: Final = "73f02c14059e0e40994822451b23c94ff15ac3aba95f20251bf1ada6f6c930d9"
PARSE_WORKER_LOCK_SHA256: Final = "d1846fca87b2242d38782b04f84515b2a9553dd33b7f3e740708397d3bf14419"
COMPILER_BOOTSTRAP_LOCK_SHA256: Final = "c9f458d2f0cb25f89d84b4375d9ac1c9b69bf0dbdaa3cadeb6a8c328f1e2640f"
DEPENDENCY_LOCK_SET_SHA256: Final = "4f55315dba8826009556ce50a207e6776a0bca7449aa0fd900dfa521dab5eb35"

def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")

def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def _lock_set_digest(api_digest: str, parse_digest: str) -> str:
    return _sha256_bytes(_canonical_json_bytes({"domain": LOCK_SET_DOMAIN, "python": PYTHON_VERSION, "platform": RELEASE_PLATFORM, "locks": [["api-worker.lock", api_digest], ["parse-worker.lock", parse_digest]]}))

def verify_generated_lock_digests(requirements_dir: Path | str) -> None:
    root = Path(requirements_dir)
    expected = {"api-worker.lock": API_WORKER_LOCK_SHA256, "parse-worker.lock": PARSE_WORKER_LOCK_SHA256, "compiler-bootstrap.lock": COMPILER_BOOTSTRAP_LOCK_SHA256}
    actual = {}
    for name, digest in expected.items():
        path = root / name
        if not path.is_file():
            raise RuntimeError(f"missing dependency lock: {name}")
        actual[name] = _sha256_bytes(path.read_bytes())
        if actual[name] != digest:
            raise RuntimeError(f"dependency lock digest mismatch: {name}")
    if _lock_set_digest(actual["api-worker.lock"], actual["parse-worker.lock"]) != DEPENDENCY_LOCK_SET_SHA256:
        raise RuntimeError("dependency lock-set digest mismatch")

__all__ = ("API_WORKER_LOCK_SHA256", "PARSE_WORKER_LOCK_SHA256", "COMPILER_BOOTSTRAP_LOCK_SHA256", "DEPENDENCY_LOCK_SET_SHA256", "verify_generated_lock_digests")
