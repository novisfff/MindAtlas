"""Evidence allowlist and sensitive-fragment gates for Plan 1 Task 11.

The verification runner may emit only the fixed safe key set. Serialized
evidence must never contain secret/material fragments.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from scripts.verify_operator_control_plane import (  # noqa: E402
    ALLOWED_EVIDENCE_KEYS,
    SENSITIVE_FRAGMENTS,
    finalize_evidence,
    validate_evidence,
)


def _safe_payload() -> dict[str, Any]:
    """Minimal allowlisted payload before digest finalization."""
    return {
        "schemaVersion": "1",
        "buildRevision": "deadbeef",
        "alembicHead": "9f3c1a7e2b40",
        "postgresVersion": "15.18",
        "routePolicyCounts": {
            "public": 1,
            "credential_exchange": 1,
            "setup_initialization": 1,
            "protected_browser": 10,
            "authenticated_machine": 2,
        },
        "testSuites": {
            "suiteCount": 4,
            "passed": True,
            "totalPassed": 40,
            "totalFailed": 0,
            "totalSkipped": 0,
            "pytestVersion": "9.0.2",
            "pythonVersion": "3.12.0",
        },
        "restartSessionPreserved": True,
        "rotationSucceeded": True,
        "previousKeySessionsRevoked": True,
        "generatedAtUtc": "2026-07-28T00:00:00+00:00",
    }


def test_evidence_contains_no_sensitive_keys() -> None:
    evidence = finalize_evidence(_safe_payload())
    assert set(evidence) == ALLOWED_EVIDENCE_KEYS
    serialized = json.dumps(evidence).lower()
    for fragment in (
        "password",
        "token",
        "cookie",
        "api_key",
        "prompt",
        "entry_content",
    ):
        assert fragment not in serialized
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in serialized


def test_finalize_evidence_rejects_unknown_keys() -> None:
    payload = _safe_payload()
    payload["sessionSecret"] = "nope"
    with pytest.raises(ValueError, match="allowlist"):
        finalize_evidence(payload)


def test_finalize_evidence_rejects_missing_keys() -> None:
    payload = _safe_payload()
    del payload["alembicHead"]
    with pytest.raises(ValueError, match="allowlist"):
        finalize_evidence(payload)


def test_finalize_evidence_digest_is_64_lowercase_hex() -> None:
    evidence = finalize_evidence(_safe_payload())
    digest = evidence["aggregateDigest"]
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_finalize_evidence_digest_covers_canonical_payload() -> None:
    first = finalize_evidence(_safe_payload())
    second = finalize_evidence(_safe_payload())
    assert first["aggregateDigest"] == second["aggregateDigest"]
    # Mutating a covered field must change the digest.
    altered = _safe_payload()
    altered["buildRevision"] = "cafebabe"
    third = finalize_evidence(altered)
    assert third["aggregateDigest"] != first["aggregateDigest"]


def test_validate_evidence_round_trip() -> None:
    evidence = finalize_evidence(_safe_payload())
    validate_evidence(evidence)


def test_validate_evidence_detects_tampered_digest() -> None:
    evidence = finalize_evidence(_safe_payload())
    evidence = dict(evidence)
    evidence["aggregateDigest"] = "0" * 64
    with pytest.raises(ValueError, match="aggregateDigest"):
        validate_evidence(evidence)


def test_safe_payload_fragments_stay_clean() -> None:
    """Guard the fixture itself so suite labels never reintroduce fragments."""
    serialized = json.dumps(_safe_payload()).lower()
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in serialized
