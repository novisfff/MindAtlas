"""Stable schema-family contracts and compatibility entry points."""

from app.schema.contracts import (
    ARCHIVED_REVISION_COUNT,
    CLEAN_ROOT_REVISION,
    NEXT_RESERVED_REVISION,
    PRE_SQUASH_HEAD,
    SCHEMA_FAMILY,
    SCHEMA_IDENTITY_CONTRACT_VERSION,
    SCHEMA_IDENTITY_SINGLETON_KEY,
    DeploymentClass,
    SchemaCompatibilitySnapshot,
    SchemaRuntimeIdentityMaterial,
)

__all__ = (
    "ARCHIVED_REVISION_COUNT",
    "CLEAN_ROOT_REVISION",
    "DeploymentClass",
    "NEXT_RESERVED_REVISION",
    "PRE_SQUASH_HEAD",
    "SCHEMA_FAMILY",
    "SCHEMA_IDENTITY_CONTRACT_VERSION",
    "SCHEMA_IDENTITY_SINGLETON_KEY",
    "SchemaCompatibilitySnapshot",
    "SchemaRuntimeIdentityMaterial",
)
