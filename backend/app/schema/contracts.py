"""Frozen vocabulary for the first supported pre-GA schema family."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypeAlias

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


JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = (
    JsonScalar
    | list["JsonValue"]
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)


@dataclass(frozen=True, order=True)
class CanonicalObjectKey:
    kind: str
    schema: str
    name: str
    qualifier: str = ""

    def __post_init__(self) -> None:
        if not all(
            isinstance(item, str)
            for item in (self.kind, self.schema, self.name, self.qualifier)
        ):
            raise ValueError("canonical object key fields must be strings")
        if not self.kind or not self.schema or not self.name:
            raise ValueError("canonical object kind, schema, and name must be nonempty")

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "schema": self.schema,
            "name": self.name,
            "qualifier": self.qualifier,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CanonicalObjectKey":
        try:
            kind = payload["kind"]
            schema = payload["schema"]
            name = payload["name"]
            qualifier = payload.get("qualifier", "")
            if not all(
                isinstance(item, str) for item in (kind, schema, name, qualifier)
            ):
                raise TypeError("canonical object key fields must be strings")
            return cls(
                kind=kind,
                schema=schema,
                name=name,
                qualifier=qualifier,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid canonical object key") from exc


@dataclass(frozen=True)
class CanonicalSchemaObject:
    key: CanonicalObjectKey
    definition: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        from app.schema.canonical import validate_json_value

        validate_json_value(self.definition)

    @property
    def definition_digest(self) -> str:
        from app.schema.canonical import sha256_canonical_json

        return sha256_canonical_json(self.definition)

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "key": self.key.to_payload(),
            "definition": self.definition,
        }


@dataclass(frozen=True)
class CanonicalSchemaDocument:
    canonicalization_version: Literal[1]
    postgres_major: int
    objects: tuple[CanonicalSchemaObject, ...]

    def __post_init__(self) -> None:
        if self.canonicalization_version != 1:
            raise ValueError("unsupported canonicalization version")
        if type(self.postgres_major) is not int or self.postgres_major <= 0:
            raise ValueError("postgres_major must be a positive integer")
        keys = tuple(item.key for item in self.objects)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("canonical schema objects must be unique and sorted")

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "canonicalizationVersion": self.canonicalization_version,
            "postgresMajor": self.postgres_major,
            "objects": [item.to_payload() for item in self.objects],
        }

    def object_by_key(self, key: CanonicalObjectKey) -> CanonicalSchemaObject | None:
        return next((item for item in self.objects if item.key == key), None)
