"""Frozen vocabulary for the first supported pre-GA schema family."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

SCHEMA_FAMILY = "pre_ga_v1"
PRE_SQUASH_HEAD = "b6e2d4f8a901"
CLEAN_ROOT_REVISION = "pre_ga_v1_0001"
NEXT_RESERVED_REVISION = "pre_ga_v1_0002"
ARCHIVED_REVISION_COUNT = 60
SCHEMA_IDENTITY_SINGLETON_KEY = "current"
SCHEMA_IDENTITY_CONTRACT_VERSION = 1


class DeploymentClass(StrEnum):
    DEVELOPMENT = "development"
    REHEARSAL = "rehearsal"
    PRODUCTION = "production"


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SCHEMA_REVISION_PATTERN = re.compile(r"pre_ga_v1_[0-9]{4}")


def _require_sha256(value: str, *, field_name: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _require_positive_integer(value: int, *, field_name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


@dataclass(frozen=True)
class SchemaRuntimeIdentityMaterial:
    schema_family: str
    schema_revision: str
    structural_fingerprint: str
    seed_contract_digest: str
    deployment_class: DeploymentClass
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str

    def __post_init__(self) -> None:
        if self.schema_family != SCHEMA_FAMILY:
            raise ValueError(f"schema_family must be {SCHEMA_FAMILY}")
        if _SCHEMA_REVISION_PATTERN.fullmatch(self.schema_revision) is None:
            raise ValueError("schema_revision must use pre_ga_v1_NNNN syntax")
        _require_sha256(
            self.structural_fingerprint,
            field_name="structural_fingerprint",
        )
        _require_sha256(
            self.seed_contract_digest,
            field_name="seed_contract_digest",
        )
        _require_sha256(
            self.capability_feature_digest,
            field_name="capability_feature_digest",
        )
        _require_positive_integer(
            self.runtime_contract_version,
            field_name="runtime_contract_version",
        )
        _require_positive_integer(
            self.checkpoint_codec_version,
            field_name="checkpoint_codec_version",
        )


@dataclass(frozen=True)
class SchemaCompatibilitySnapshot:
    compatible: bool
    safe_reason: Literal["schema_incompatible"] | None
    diagnostic_code: str | None
    schema_family: str | None
    schema_revision: str | None
    deployment_class: DeploymentClass | None
    structural_fingerprint: str | None
    runtime_identity_digest: str | None

    def __post_init__(self) -> None:
        if self.structural_fingerprint is not None:
            _require_sha256(
                self.structural_fingerprint,
                field_name="structural_fingerprint",
            )
        if self.runtime_identity_digest is not None:
            _require_sha256(
                self.runtime_identity_digest,
                field_name="runtime_identity_digest",
            )
