from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.schema.contracts import (
    ARCHIVED_REVISION_COUNT,
    CLEAN_ROOT_REVISION,
    NEXT_RESERVED_REVISION,
    PRE_SQUASH_HEAD,
    SCHEMA_FAMILY,
    DeploymentClass,
    SchemaCompatibilitySnapshot,
    SchemaRuntimeIdentityMaterial,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEVIATION_PATH = (
    REPO_ROOT
    / "docs"
    / "superpowers"
    / "evidence"
    / "2026-07-28-pre-ga-clean-baseline-deviation.md"
)
EXPECTED_DEVIATION = """# Pre-GA Clean Baseline Design Deviation

1. MindAtlas had not launched when Legacy code and schema were removed.
2. The current clean schema is the first supported `pre_ga_v1` baseline.
3. Legacy in-place upgrade and Legacy restore are unsupported.
4. The original Plan 10 production canary, legacy-zero, restore, B1/B2 sequence, and calendar soak were not executed and are not claimed.
5. Full deterministic automation and one production-shaped rehearsal replace those omitted pre-launch operational gates.
6. This deviation changes the release baseline and does not retroactively mark the original Plan 10 checklist complete.
"""


def test_pre_ga_family_and_revision_boundary_is_exact() -> None:
    assert SCHEMA_FAMILY == "pre_ga_v1"
    assert PRE_SQUASH_HEAD == "b6e2d4f8a901"
    assert CLEAN_ROOT_REVISION == "pre_ga_v1_0001"
    assert NEXT_RESERVED_REVISION == "pre_ga_v1_0002"
    assert ARCHIVED_REVISION_COUNT == 60


def test_deployment_class_is_closed() -> None:
    assert tuple(item.value for item in DeploymentClass) == (
        "development",
        "rehearsal",
        "production",
    )
    with pytest.raises(ValueError):
        DeploymentClass("staging")


def _identity_material(**overrides: object) -> SchemaRuntimeIdentityMaterial:
    values: dict[str, object] = {
        "schema_family": SCHEMA_FAMILY,
        "schema_revision": CLEAN_ROOT_REVISION,
        "structural_fingerprint": "1" * 64,
        "seed_contract_digest": "2" * 64,
        "deployment_class": DeploymentClass.REHEARSAL,
        "runtime_contract_version": 1,
        "checkpoint_codec_version": 3,
        "capability_feature_digest": "3" * 64,
        "operator_auth_contract_version": "operator-auth-v1",
    }
    values.update(overrides)
    return SchemaRuntimeIdentityMaterial(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "structural_fingerprint",
        "seed_contract_digest",
        "capability_feature_digest",
    ],
)
@pytest.mark.parametrize("invalid", ["0" * 63, "A" * 64, "g" * 64])
def test_runtime_identity_rejects_non_sha256_digest(field: str, invalid: str) -> None:
    with pytest.raises(ValueError, match=field):
        _identity_material(**{field: invalid})


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("runtime_contract_version", 0),
        ("runtime_contract_version", -1),
        ("checkpoint_codec_version", 0),
        ("checkpoint_codec_version", -1),
    ],
)
def test_runtime_identity_requires_positive_versions(field: str, invalid: int) -> None:
    with pytest.raises(ValueError, match=field):
        _identity_material(**{field: invalid})


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("schema_family", "pre_ga_v2"),
        ("schema_revision", "b6e2d4f8a901"),
        ("schema_revision", "pre_ga_v1_1"),
    ],
)
def test_runtime_identity_requires_supported_family_revision_syntax(
    field: str,
    invalid: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        _identity_material(**{field: invalid})


def test_compatibility_snapshot_validates_optional_digests() -> None:
    snapshot = SchemaCompatibilitySnapshot(
        compatible=True,
        safe_reason=None,
        diagnostic_code=None,
        schema_family=SCHEMA_FAMILY,
        schema_revision=CLEAN_ROOT_REVISION,
        deployment_class=DeploymentClass.DEVELOPMENT,
        structural_fingerprint="a" * 64,
        runtime_identity_digest="b" * 64,
    )
    assert snapshot.structural_fingerprint == "a" * 64

    with pytest.raises(ValueError, match="runtime_identity_digest"):
        SchemaCompatibilitySnapshot(
            compatible=False,
            safe_reason="schema_incompatible",
            diagnostic_code="fingerprint_mismatch",
            schema_family=SCHEMA_FAMILY,
            schema_revision=CLEAN_ROOT_REVISION,
            deployment_class=DeploymentClass.DEVELOPMENT,
            structural_fingerprint=None,
            runtime_identity_digest="BAD",
        )


def test_deviation_record_has_exact_accepted_facts() -> None:
    text = DEVIATION_PATH.read_text("utf-8")
    assert text == EXPECTED_DEVIATION
    assert text.count("\n1.") == 1
    assert all(f"\n{number}." in text for number in range(1, 7))
    assert "were not executed and are not claimed" in text
    assert "does not retroactively mark" in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest().islower()
