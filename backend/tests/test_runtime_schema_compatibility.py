from __future__ import annotations

import pytest

from app.schema.contracts import DeploymentClass, SchemaCompatibilitySnapshot
from app.schema.compatibility import (
    FamilyBoundRuntimeSchemaCompatibility,
    PLAN3_SCHEMA_REQUIREMENT,
    runtime_schema_compatibility,
)
from app.schema import compatibility as compatibility_module


def test_runtime_schema_compatibility_is_family_bound_singleton() -> None:
    service = runtime_schema_compatibility()

    assert isinstance(service, FamilyBoundRuntimeSchemaCompatibility)
    assert service is runtime_schema_compatibility()
    assert PLAN3_SCHEMA_REQUIREMENT.schema_family == "pre_ga_v1"
    assert PLAN3_SCHEMA_REQUIREMENT.compatible_revisions == {
        "pre_ga_v1_0002": 2,
    }


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("missing_marker", "marker_missing"),
        ("multiple_alembic_heads", "head_ambiguous"),
        ("wrong_family", "family_mismatch"),
        ("old_revision", "revision_incompatible"),
        ("fingerprint", "fingerprint_mismatch"),
        ("marker_control", "marker_control_mismatch"),
        ("runtime_identity", "runtime_identity_mismatch"),
        ("seed_contract", "seed_contract_mismatch"),
        ("runtime_contract", "runtime_contract_mismatch"),
        ("checkpoint_codec", "checkpoint_codec_mismatch"),
        ("capability_feature", "capability_feature_mismatch"),
        ("operator_auth", "operator_auth_contract_mismatch"),
        ("deployment_class", "deployment_class_mismatch"),
        ("unknown_build", "build_identity_invalid"),
        ("legacy_object", "legacy_object_present"),
    ],
)
def test_family_compatibility_fails_closed_with_bounded_diagnostic(
    diagnostic: str,
    expected: str,
) -> None:
    service = FamilyBoundRuntimeSchemaCompatibility()

    snapshot = service._incompatible(expected)  # noqa: SLF001

    assert snapshot == SchemaCompatibilitySnapshot(
        compatible=False,
        safe_reason="schema_incompatible",
        diagnostic_code=expected,
        schema_family=None,
        schema_revision=None,
        deployment_class=None,
        structural_fingerprint=None,
        runtime_identity_digest=None,
    )


def test_compatible_snapshot_shape_is_bounded() -> None:
    snapshot = SchemaCompatibilitySnapshot(
        compatible=True,
        safe_reason=None,
        diagnostic_code=None,
        schema_family="pre_ga_v1",
        schema_revision="pre_ga_v1_0002",
        deployment_class=DeploymentClass.DEVELOPMENT,
        structural_fingerprint="a" * 64,
        runtime_identity_digest="b" * 64,
    )

    assert snapshot.compatible is True
    assert snapshot.safe_reason is None


def test_manifest_load_failure_is_bounded_at_evaluate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable():
        raise RuntimeError("raw manifest details")

    monkeypatch.setattr(compatibility_module, "_load_requirement", unavailable)

    snapshot = FamilyBoundRuntimeSchemaCompatibility().evaluate(object())

    assert snapshot.compatible is False
    assert snapshot.safe_reason == "schema_incompatible"
    assert snapshot.diagnostic_code == "schema_manifest_invalid"
