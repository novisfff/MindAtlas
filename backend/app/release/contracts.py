"""Frozen, content-addressed release qualification contracts.

Release evidence is deliberately kept separate from the application domain
models.  These contracts are the boundary between the release runner, the
server verifier, and offline auditors: unknown fields, non-canonical values,
and self-digest mismatches are rejected before any evidence is trusted.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_canonical_json
from app.schema.canonical import canonical_json_bytes


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_FAILURE_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")

MANIFEST_DIGEST_DOMAIN = "mindatlas:release-manifest:v1"
ARTIFACT_AGGREGATE_DOMAIN = "mindatlas:release-artifact-aggregate:v1"
QUALIFICATION_TARGET_DOMAIN = "mindatlas:release-qualification-target:v1"
INFRASTRUCTURE_DOMAIN = "mindatlas:qualification-infrastructure:v1"
REHEARSAL_ATTEMPT_SUBJECT_DOMAIN = "mindatlas:rehearsal-attempt-subject:v1"
SCHEMA_CONTRACT_MATERIAL_DOMAIN = "mindatlas:schema-contract-material:v1"
RUNNER_IDENTITY_DOMAIN = "mindatlas:release-runner-identity:v1"
REHEARSAL_AUTH_DOMAIN = "mindatlas:rehearsal-authorization:v1"
REHEARSAL_AUTH_CLAIMS_DOMAIN = "mindatlas:rehearsal-authorization-claims:v1"


def _require_digest(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _require_safe_id(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or SAFE_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is not a safe identifier")
    return value


def _require_safe_failure(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or SAFE_FAILURE_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is not a safe failure code")
    return value


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _dump(model: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    return model.model_dump(
        mode="json",
        by_alias=True,
        exclude=exclude or set(),
        exclude_none=False,
    )


def _normalize_aliases(model_type: type[Any], values: dict[str, Any]) -> dict[str, Any]:
    data = dict(values)
    for field_name, field in model_type.model_fields.items():
        alias = field.alias
        if alias and alias != field_name and alias in data and field_name not in data:
            data[field_name] = data.pop(alias)
    return data


def _domain_digest(domain: str, model: Any, *, exclude: str) -> str:
    payload = {"domain": domain, **_dump(model, exclude={exclude})}
    return sha256_canonical_json(payload)


def _sorted_items(value: Any, *, key: str) -> tuple[Any, ...]:
    items = list(value or ())

    def item_key(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get(key, ""))
        return str(getattr(item, key, ""))

    return tuple(sorted(items, key=item_key))


def _field_value(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        if key in item:
            return item[key]
        aliases = {
            "assertion_id": "assertionId",
            "artifact_kind": "artifactKind",
            "artifact_id": "artifactId",
            "sha256_digest": "sha256Digest",
        }
        return item.get(aliases.get(key, key), default)
    return getattr(item, key, default)


class ReleaseContract(FrozenContract):
    """Shared strict validation for release contracts."""

    @field_validator("*")
    @classmethod
    def validate_digest_fields(cls, value: Any, info: ValidationInfo) -> Any:
        name = info.field_name
        if name.endswith("_digest") or name.endswith("_sha256"):
            if isinstance(value, tuple):
                for item in value:
                    _require_digest(item, field_name=name)
            elif value is not None:
                _require_digest(value, field_name=name)
        return value

    @field_validator(
        "started_at",
        "ended_at",
        "issued_at",
        "expires_at",
        "not_before",
        "not_after",
        mode="after",
        check_fields=False,
    )
    @classmethod
    def normalize_utc(cls, value: datetime, info: ValidationInfo) -> datetime:
        return _utc(value, field_name=info.field_name)

    @field_validator(
        "artifact_id",
        "assertion_id",
        "key_id",
        "scenario_id",
        "step_id",
        "fault_code",
        check_fields=False,
    )
    @classmethod
    def validate_identifier_fields(cls, value: str, info: ValidationInfo) -> str:
        return _require_safe_id(value, field_name=info.field_name)


class ContentAddressedEvidenceRef(ReleaseContract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["automated_qualification", "production_rehearsal"]
    manifest_digest: str
    attestation_digest: str


class ReleaseAssertionResultV1(ReleaseContract):
    schema_version: Literal[1] = 1
    assertion_id: str
    passed: bool
    safe_failure_code: str | None
    observation_digest: str
    artifact_digests: tuple[str, ...]
    duration_ms: int = Field(ge=0)

    @field_validator("safe_failure_code")
    @classmethod
    def validate_failure_code(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None:
            return _require_safe_failure(value, field_name=info.field_name)
        return value

    @field_validator("artifact_digests")
    @classmethod
    def validate_artifact_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate artifact digest in assertion")
        return tuple(_require_digest(item, field_name="artifact_digests") for item in value)

    @model_validator(mode="after")
    def validate_result_shape(self) -> "ReleaseAssertionResultV1":
        if self.passed and self.safe_failure_code is not None:
            raise ValueError("passed assertion cannot contain safe_failure_code")
        if not self.passed and self.safe_failure_code is None:
            raise ValueError("failed assertion requires safe_failure_code")
        return self


class ReleaseArtifactRefV1(ReleaseContract):
    schema_version: Literal[1] = 1
    artifact_id: str
    artifact_kind: Literal[
        "junit",
        "migration_summary",
        "audit_summary",
        "metric_summary",
        "scenario_trace",
        "test_order_summary",
        "profile_topology_summary",
        "secret_scan_summary",
        "teardown_summary",
    ]
    media_type: Literal["application/json", "application/xml"]
    sha256_digest: str
    byte_size: int = Field(ge=1, le=10_485_760)


class QualificationInfrastructureIdentityV1(ReleaseContract):
    schema_version: Literal[1] = 1
    platform: Literal["linux/amd64"]
    scripted_provider_image_digest: str
    postgres_image_digest: str
    minio_image_digest: str
    minio_client_image_digest: str
    release_images_lock_digest: str
    compiler_image_digest: str
    compiler_bootstrap_lock_digest: str
    identity_digest: str

    @classmethod
    def build(cls, **values: Any) -> "QualificationInfrastructureIdentityV1":
        values = _normalize_aliases(cls, values)
        values["identity_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["identity_digest"] = _domain_digest(INFRASTRUCTURE_DOMAIN, draft, exclude="identity_digest")
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_identity_digest(self) -> "QualificationInfrastructureIdentityV1":
        expected = _domain_digest(INFRASTRUCTURE_DOMAIN, self, exclude="identity_digest")
        if self.identity_digest != expected:
            raise ValueError("identity_digest does not match infrastructure identity")
        return self


class ReleaseEvidenceManifestV1(ReleaseContract):
    schema_version: Literal[1] = 1
    runner_contract_version: int = Field(ge=1)
    runner_identity_digest: str
    release_run_id: UUID
    evidence_kind: Literal["automated_qualification", "production_rehearsal"]
    qualification_target_digest: str
    build_revision: str = Field(min_length=1, max_length=128)
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_family: Literal["pre_ga_v1"]
    schema_revision: Literal["pre_ga_v1_0002"]
    schema_application_fingerprint: str
    schema_control_fingerprint: str
    schema_identity_contract_version: int = Field(ge=1)
    schema_contract_material_digest: str
    schema_deployment_class: Literal["rehearsal"]
    schema_seed_contract_digest: str
    schema_runtime_contract_version: int = Field(ge=1)
    schema_checkpoint_codec_version: int = Field(ge=1)
    schema_capability_feature_digest: str
    schema_runtime_identity_digest: str
    operator_auth_contract_version: str = Field(min_length=1, max_length=64)
    rollout_revision_id: UUID
    rollout_revision_digest: str
    runtime_closure_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    worker_runtime_contract_version: int = Field(ge=1)
    worker_checkpoint_codec_version: int = Field(ge=1)
    worker_capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int = Field(ge=1)
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    evidence_trust_set_digest: str
    qualification_infrastructure_identity: QualificationInfrastructureIdentityV1
    started_at: datetime
    ended_at: datetime
    assertion_results: tuple[ReleaseAssertionResultV1, ...]
    artifact_refs: tuple[ReleaseArtifactRefV1, ...]
    artifact_aggregate_digest: str
    manifest_digest: str

    _manifest_domain: ClassVar[str] = MANIFEST_DIGEST_DOMAIN

    @model_validator(mode="before")
    @classmethod
    def normalize_collections(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        data = dict(values)
        if "assertion_results" not in data and "assertionResults" in data:
            data["assertion_results"] = data.pop("assertionResults")
        if "artifact_refs" not in data and "artifactRefs" in data:
            data["artifact_refs"] = data.pop("artifactRefs")
        data["assertion_results"] = _sorted_items(data.get("assertion_results", ()), key="assertion_id")
        data["artifact_refs"] = tuple(
            sorted(
                list(data.get("artifact_refs", ())),
                key=lambda item: (
                    _field_value(item, "artifact_kind"),
                    _field_value(item, "artifact_id"),
                    _field_value(item, "sha256_digest"),
                ),
            )
        )
        return data

    @classmethod
    def build(cls, **values: Any) -> "ReleaseEvidenceManifestV1":
        values = _normalize_aliases(cls, values)
        refs = tuple(values.get("artifact_refs", ()))
        values["assertion_results"] = _sorted_items(
            values.get("assertion_results", ()), key="assertion_id"
        )
        values["artifact_refs"] = tuple(
            sorted(
                refs,
                key=lambda item: (
                    _field_value(item, "artifact_kind"),
                    _field_value(item, "artifact_id"),
                    _field_value(item, "sha256_digest"),
                ),
            )
        )
        refs = tuple(values["artifact_refs"])
        values["artifact_aggregate_digest"] = artifact_aggregate_digest(refs)
        values["manifest_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["manifest_digest"] = _domain_digest(MANIFEST_DIGEST_DOMAIN, draft, exclude="manifest_digest")
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ReleaseEvidenceManifestV1":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        assertion_ids = [item.assertion_id for item in self.assertion_results]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("duplicate assertion_id")
        artifact_ids = [item.artifact_id for item in self.artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("duplicate artifact_id")
        available = {item.sha256_digest for item in self.artifact_refs}
        referenced = {
            digest
            for item in self.assertion_results
            for digest in item.artifact_digests
        }
        if not referenced <= available:
            raise ValueError("assertion references an unlisted artifact")
        if available - referenced:
            raise ValueError("every artifact must be reachable from an assertion")
        expected_aggregate = artifact_aggregate_digest(self.artifact_refs)
        if self.artifact_aggregate_digest != expected_aggregate:
            raise ValueError("artifact_aggregate_digest does not match artifact refs")
        expected_manifest = _domain_digest(self._manifest_domain, self, exclude="manifest_digest")
        if self.manifest_digest != expected_manifest:
            raise ValueError("manifest_digest does not match canonical manifest")
        return self


class SignedReleaseAttestationV1(ReleaseContract):
    schema_version: Literal[1] = 1
    domain: Literal["mindatlas:release-evidence:v1"]
    key_id: str
    manifest_digest: str
    signature_base64url: str = Field(min_length=1)


def artifact_aggregate_digest(refs: Any) -> str:
    payload = []
    for ref in sorted(
        list(refs),
        key=lambda item: (
            _field_value(item, "artifact_kind"),
            _field_value(item, "artifact_id"),
            _field_value(item, "sha256_digest"),
        ),
    ):
        if hasattr(ref, "model_dump"):
            payload.append(_dump(ref))
        else:
            payload.append(dict(ref))
    return sha256_canonical_json({"domain": ARTIFACT_AGGREGATE_DOMAIN, "artifactRefs": payload})


def canonical_release_manifest_bytes(manifest: ReleaseEvidenceManifestV1) -> bytes:
    if not isinstance(manifest, ReleaseEvidenceManifestV1):
        raise TypeError("manifest must be ReleaseEvidenceManifestV1")
    return canonical_json_bytes(_dump(manifest))


class ReleaseQualificationTargetV1(ReleaseContract):
    schema_version: Literal[1] = 1
    build_revision: str = Field(min_length=1, max_length=128)
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_family: Literal["pre_ga_v1"]
    schema_revision: Literal["pre_ga_v1_0002"]
    schema_application_fingerprint: str
    schema_control_fingerprint: str
    schema_identity_contract_version: int = Field(ge=1)
    production_schema_deployment_class: Literal["production"]
    schema_seed_contract_digest: str
    schema_runtime_contract_version: int = Field(ge=1)
    schema_checkpoint_codec_version: int = Field(ge=1)
    schema_capability_feature_digest: str
    production_schema_runtime_identity_digest: str
    schema_contract_material_digest: str
    operator_auth_contract_version: str
    rollout_revision_id: UUID
    rollout_revision_digest: str
    runtime_closure_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    worker_runtime_contract_version: int = Field(ge=1)
    worker_checkpoint_codec_version: int = Field(ge=1)
    worker_capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int = Field(ge=1)
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int = Field(ge=1)
    runner_identity_digest: str
    evidence_trust_set_digest: str
    qualification_target_digest: str

    @classmethod
    def build(cls, **values: Any) -> "ReleaseQualificationTargetV1":
        values = _normalize_aliases(cls, values)
        values["qualification_target_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["qualification_target_digest"] = _domain_digest(
            QUALIFICATION_TARGET_DOMAIN, draft, exclude="qualification_target_digest"
        )
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_target_digest(self) -> "ReleaseQualificationTargetV1":
        expected = _domain_digest(QUALIFICATION_TARGET_DOMAIN, self, exclude="qualification_target_digest")
        if self.qualification_target_digest != expected:
            raise ValueError("qualification_target_digest does not match target")
        return self


class RehearsalAttemptSubjectV1(ReleaseContract):
    schema_version: Literal[1] = 1
    qualification_target_digest: str
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int = Field(ge=1)
    runner_identity_digest: str
    evidence_trust_set_digest: str
    subject_digest: str

    @classmethod
    def build(cls, **values: Any) -> "RehearsalAttemptSubjectV1":
        values = _normalize_aliases(cls, values)
        values["subject_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["subject_digest"] = _domain_digest(
            REHEARSAL_ATTEMPT_SUBJECT_DOMAIN, draft, exclude="subject_digest"
        )
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_subject_digest(self) -> "RehearsalAttemptSubjectV1":
        expected = _domain_digest(REHEARSAL_ATTEMPT_SUBJECT_DOMAIN, self, exclude="subject_digest")
        if self.subject_digest != expected:
            raise ValueError("subject_digest does not match rehearsal subject")
        return self


class RehearsalProfileAuthorizationV1(ReleaseContract):
    schema_version: Literal[1] = 1
    authorization_id: UUID
    profile_run_id: UUID
    deployment_class: Literal["rehearsal"]
    qualification_target_digest: str
    initialization_fixture_digest: str
    build_revision: str = Field(min_length=1, max_length=128)
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_runtime_identity_digest: str
    schema_contract_material_digest: str
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int = Field(ge=1)
    runner_identity_digest: str
    evidence_trust_set_digest: str
    issued_at: datetime
    expires_at: datetime
    nonce_digest: str
    authorization_digest: str

    @classmethod
    def build(cls, *, nonce: bytes, **values: Any) -> "RehearsalProfileAuthorizationV1":
        import hashlib

        values = dict(values)
        values.setdefault("deployment_class", "rehearsal")
        values["nonce_digest"] = hashlib.sha256(nonce).hexdigest()
        values["authorization_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["authorization_digest"] = _domain_digest(
            REHEARSAL_AUTH_CLAIMS_DOMAIN, draft, exclude="authorization_digest"
        )
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_authorization(self) -> "RehearsalProfileAuthorizationV1":
        if self.expires_at <= self.issued_at:
            raise ValueError("authorization expires_at must be after issued_at")
        if self.expires_at - self.issued_at > __import__("datetime").timedelta(minutes=120):
            raise ValueError("authorization lifetime exceeds 120 minutes")
        expected = _domain_digest(REHEARSAL_AUTH_CLAIMS_DOMAIN, self, exclude="authorization_digest")
        if self.authorization_digest != expected:
            raise ValueError("authorization_digest does not match claims")
        return self


class SignedRehearsalProfileAuthorizationV1(ReleaseContract):
    schema_version: Literal[1] = 1
    domain: Literal["mindatlas:rehearsal-authorization:v1"]
    key_id: str
    authorization: RehearsalProfileAuthorizationV1
    signature_base64url: str = Field(min_length=1)


def schema_contract_material_digest(
    *,
    schema_family: str,
    schema_revision: str,
    schema_application_fingerprint: str,
    schema_control_fingerprint: str,
    schema_identity_contract_version: int,
    schema_seed_contract_digest: str,
    schema_runtime_contract_version: int,
    schema_checkpoint_codec_version: int,
    schema_capability_feature_digest: str,
    operator_auth_contract_version: str,
) -> str:
    return sha256_canonical_json(
        {
            "domain": SCHEMA_CONTRACT_MATERIAL_DOMAIN,
            "schemaFamily": schema_family,
            "schemaRevision": schema_revision,
            "schemaApplicationFingerprint": schema_application_fingerprint,
            "schemaControlFingerprint": schema_control_fingerprint,
            "schemaIdentityContractVersion": schema_identity_contract_version,
            "schemaSeedContractDigest": schema_seed_contract_digest,
            "schemaRuntimeContractVersion": schema_runtime_contract_version,
            "schemaCheckpointCodecVersion": schema_checkpoint_codec_version,
            "schemaCapabilityFeatureDigest": schema_capability_feature_digest,
            "operatorAuthContractVersion": operator_auth_contract_version,
        }
    )


def runner_identity_digest(
    *,
    build_revision: str,
    runner_contract_version: int,
    scenario_set_digest: str,
    required_assertion_set_digest: str,
) -> str:
    return sha256_canonical_json(
        {
            "domain": RUNNER_IDENTITY_DOMAIN,
            "buildRevision": build_revision,
            "runnerContractVersion": runner_contract_version,
            "scenarioSetDigest": scenario_set_digest,
            "requiredAssertionSetDigest": required_assertion_set_digest,
        }
    )


__all__ = [
    "ARTIFACT_AGGREGATE_DOMAIN",
    "ContentAddressedEvidenceRef",
    "INFRASTRUCTURE_DOMAIN",
    "MANIFEST_DIGEST_DOMAIN",
    "QualificationInfrastructureIdentityV1",
    "ReleaseArtifactRefV1",
    "ReleaseAssertionResultV1",
    "ReleaseEvidenceManifestV1",
    "ReleaseQualificationTargetV1",
    "RehearsalAttemptSubjectV1",
    "RehearsalProfileAuthorizationV1",
    "SignedReleaseAttestationV1",
    "SignedRehearsalProfileAuthorizationV1",
    "artifact_aggregate_digest",
    "canonical_release_manifest_bytes",
    "runner_identity_digest",
    "schema_contract_material_digest",
]
