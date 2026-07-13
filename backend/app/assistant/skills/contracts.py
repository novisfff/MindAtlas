from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    CapabilityType,
    DeclaredSideEffect,
    FrozenContract,
    ParsedSkillResource,
    SkillResourceIndexEntry,
)
from app.assistant.domain.json_schema import normalize_binding_schema

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_SKILL_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_COMPATIBILITY_LEN = 500
MAX_LICENSE_LEN = 256
MAX_FRONTMATTER_METADATA_ENTRIES = 50
MAX_DISPLAY_NAME_LEN = 128
MAX_ALIAS_LEN = 128
MAX_ALIAS_UTF8_BYTES = 512
MAX_CAPABILITY_KEY_LEN = 128
MAX_CAPABILITIES = 100
MAX_ROUTING_EXAMPLES = 100
MAX_ROUTING_EXAMPLE_LEN = 1000
MAX_CONFLICT_RULES = 50
MAX_PROVIDER_ALIASES = 100
MAX_PROVIDER_ALIAS_HINT_LEN = 128
MAX_MANIFEST_METADATA_ENTRIES = 50
MAX_METADATA_KEY_LEN = 128
MAX_METADATA_VALUE_LEN = 1024
MAX_ALLOWED_TOOLS_LEN = 2048
MAX_FOLLOWUP_HINT_LEN = 512
MAX_CONFLICT_GROUP_LEN = 128
MAX_MAX_SKILL_CALLS = 10_000
MAX_MAX_SAME_READ_CALLS = 10_000

RESERVED_SKILL_LOOKUP_NAMES = frozenset({"general_chat", "general-chat"})

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_TOOLS_TOKEN_RE = re.compile(r"^[^\s]+(?:\s+[^\s]+)*$")


def normalize_skill_lookup_name(value: str) -> str:
    """NFKC, trim, Unicode casefold; reject controls, NUL, slash/backslash, and empty."""
    if not isinstance(value, str):
        raise TypeError("skill lookup name must be a string")
    if "\x00" in value:
        raise ValueError("skill lookup name must not contain NUL")
    if "/" in value or "\\" in value:
        raise ValueError("skill lookup name must not contain slash or backslash")
    for ch in value:
        if unicodedata.category(ch).startswith("C") and ch not in {"\t"}:
            # Allow TAB as internal whitespace; reject other controls including \n/\r.
            if ch == "\t":
                continue
            raise ValueError("skill lookup name must not contain control characters")
    if len(value) > MAX_ALIAS_LEN:
        raise ValueError(
            f"skill lookup name exceeds {MAX_ALIAS_LEN} Unicode scalar values before normalization"
        )
    if len(value.encode("utf-8")) > MAX_ALIAS_UTF8_BYTES:
        raise ValueError(
            f"skill lookup name exceeds {MAX_ALIAS_UTF8_BYTES} UTF-8 bytes before normalization"
        )

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if not normalized:
        raise ValueError("skill lookup name is empty after normalization")
    if "\x00" in normalized or "/" in normalized or "\\" in normalized:
        raise ValueError("skill lookup name must not contain NUL, slash, or backslash")
    for ch in normalized:
        if unicodedata.category(ch).startswith("C") and ch != "\t":
            raise ValueError("skill lookup name must not contain control characters")
    if len(normalized) > MAX_ALIAS_LEN:
        raise ValueError(
            f"skill lookup name exceeds {MAX_ALIAS_LEN} Unicode scalar values after normalization"
        )
    if len(normalized.encode("utf-8")) > MAX_ALIAS_UTF8_BYTES:
        raise ValueError(
            f"skill lookup name exceeds {MAX_ALIAS_UTF8_BYTES} UTF-8 bytes after normalization"
        )
    return normalized


def is_reserved_skill_lookup_name(value: str) -> bool:
    try:
        return normalize_skill_lookup_name(value) in RESERVED_SKILL_LOOKUP_NAMES
    except (TypeError, ValueError):
        return False


def validate_canonical_skill_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("canonical skill name must be a string")
    if not value or len(value) > MAX_SKILL_NAME_LEN:
        raise ValueError(
            f"canonical skill name must be 1–{MAX_SKILL_NAME_LEN} characters"
        )
    if not _SKILL_NAME_RE.fullmatch(value):
        raise ValueError(
            "canonical skill name must be lowercase ASCII letters, digits, and single hyphens"
        )
    if is_reserved_skill_lookup_name(value):
        raise ValueError(f"canonical skill name {value!r} is reserved")
    return value


def _require_str_str_map(
    value: Any,
    *,
    field_name: str,
    max_entries: int,
    max_key_len: int = MAX_METADATA_KEY_LEN,
    max_value_len: int = MAX_METADATA_VALUE_LEN,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a string-to-string mapping")
    if len(value) > max_entries:
        raise ValueError(f"{field_name} exceeds {max_entries} entries")
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{field_name} keys and values must be strings")
        if not key or len(key) > max_key_len:
            raise ValueError(f"{field_name} key length invalid")
        if len(item) > max_value_len:
            raise ValueError(f"{field_name} value length invalid")
        out[key] = item
    return out


class AgentSkillFrontmatter(FrozenContract):
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_canonical_skill_name(value)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_DESCRIPTION_LEN:
            raise ValueError(
                f"description must be 1–{MAX_DESCRIPTION_LEN} characters"
            )
        return value

    @field_validator("license")
    @classmethod
    def _validate_license(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > MAX_LICENSE_LEN:
            raise ValueError(f"license must be 1–{MAX_LICENSE_LEN} characters")
        return value

    @field_validator("compatibility")
    @classmethod
    def _validate_compatibility(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_COMPATIBILITY_LEN
        ):
            raise ValueError(
                f"compatibility must be 1–{MAX_COMPATIBILITY_LEN} characters"
            )
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: Any) -> dict[str, str]:
        return _require_str_str_map(
            value,
            field_name="metadata",
            max_entries=MAX_FRONTMATTER_METADATA_ENTRIES,
        )

    @field_validator("allowed_tools")
    @classmethod
    def _validate_allowed_tools(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            raise ValueError("allowed-tools must be a space-delimited string, not a list")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("allowed-tools must be a non-empty space-delimited string")
        if len(value) > MAX_ALLOWED_TOOLS_LEN:
            raise ValueError("allowed-tools exceeds length limit")
        if not _ALLOWED_TOOLS_TOKEN_RE.fullmatch(value.strip()):
            raise ValueError("allowed-tools must be a space-delimited string")
        # Reject YAML-list leakage represented as stringified list is already handled.
        return value


class SkillConflictRuleV1(FrozenContract):
    kind: Literal["excludes", "requires", "exclusive_group"]
    target_skill: str | None = None
    group: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> SkillConflictRuleV1:
        if self.kind in {"excludes", "requires"}:
            if not self.target_skill or self.group is not None:
                raise ValueError(
                    f"conflict rule kind {self.kind!r} requires target_skill and no group"
                )
            # Accept alias-like author input; store as provided after basic bounds.
            if len(self.target_skill) > MAX_ALIAS_LEN:
                raise ValueError("conflict rule target_skill is too long")
            if is_reserved_skill_lookup_name(self.target_skill):
                raise ValueError("conflict rule target_skill is reserved")
        elif self.kind == "exclusive_group":
            if not self.group or self.target_skill is not None:
                raise ValueError(
                    "exclusive_group requires group and no target_skill"
                )
            if not self.group.strip() or len(self.group) > MAX_CONFLICT_GROUP_LEN:
                raise ValueError("exclusive_group group is invalid")
        return self


class SkillRoutingContract(FrozenContract):
    include_examples: tuple[str, ...] = ()
    exclude_examples: tuple[str, ...] = ()
    conflict_rules: tuple[SkillConflictRuleV1, ...] = ()

    @field_validator("include_examples", "exclude_examples", mode="before")
    @classmethod
    def _validate_examples(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("routing examples must be a list of strings")
        if len(value) > MAX_ROUTING_EXAMPLES:
            raise ValueError(
                f"routing examples exceed {MAX_ROUTING_EXAMPLES} items"
            )
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item:
                raise ValueError("routing example must be a non-empty string")
            if len(item) > MAX_ROUTING_EXAMPLE_LEN:
                raise ValueError(
                    f"routing example exceeds {MAX_ROUTING_EXAMPLE_LEN} characters"
                )
            out.append(item)
        return tuple(out)

    @field_validator("conflict_rules", mode="before")
    @classmethod
    def _validate_conflict_rules(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("conflict_rules must be a list")
        if len(value) > MAX_CONFLICT_RULES:
            raise ValueError(f"conflict_rules exceed {MAX_CONFLICT_RULES} items")
        return tuple(value)


class SkillPolicyContract(FrozenContract):
    """Author-declared policy. Defaults grant no authorization."""

    allowed_side_effects: tuple[DeclaredSideEffect, ...] = ()
    max_skill_calls: int = 16
    max_same_read_calls: int = 3
    requires_terminal_output: bool = False
    terminal_text_allowed: bool = False

    @field_validator("allowed_side_effects", mode="before")
    @classmethod
    def _validate_side_effects(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("allowed_side_effects must be a list")
        allowed = {"read", "compute", "draft", "write", "control"}
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            if item not in allowed:
                raise ValueError(f"unknown side effect {item!r}")
            if item in seen:
                raise ValueError(f"duplicate side effect {item!r}")
            seen.add(item)
            out.append(item)
        return tuple(out)

    @field_validator("max_skill_calls", "max_same_read_calls", mode="before")
    @classmethod
    def _validate_budget(cls, value: Any) -> int:
        # mode="before" so bool is not coerced to int (True→1 / False→0).
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("budget must be an integer")
        if value < 0 or value > MAX_MAX_SKILL_CALLS:
            raise ValueError("budget out of range")
        return value


class CapabilityBindingContract(FrozenContract):
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    completion: CapabilityCompletionContract = Field(
        default_factory=CapabilityCompletionContract
    )

    @field_validator("input_schema", mode="before")
    @classmethod
    def _normalize_input(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("input_schema must be a mapping")
        return normalize_binding_schema(value, require_object_root=True)

    @field_validator("output_schema", mode="before")
    @classmethod
    def _normalize_output(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("output_schema must be a mapping")
        return normalize_binding_schema(value, require_object_root=False)

    @field_validator("completion", mode="before")
    @classmethod
    def _validate_completion(cls, value: Any) -> Any:
        if value is None:
            return CapabilityCompletionContract()
        if isinstance(value, CapabilityCompletionContract):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("completion must be a mapping")
        # Forbid unknown completion keys via FrozenContract extra=forbid.
        return value

    @model_validator(mode="after")
    def _validate_followup_hint(self) -> CapabilityBindingContract:
        hint = self.completion.followup_hint
        if hint is not None and (
            not isinstance(hint, str) or len(hint) > MAX_FOLLOWUP_HINT_LEN
        ):
            raise ValueError("followup_hint is invalid")
        return self


class CapabilityDeclaration(FrozenContract):
    type: CapabilityType
    key: str
    contract: CapabilityBindingContract | None = None

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_CAPABILITY_KEY_LEN:
            raise ValueError(
                f"capability key must be 1–{MAX_CAPABILITY_KEY_LEN} characters"
            )
        return value

    @model_validator(mode="after")
    def _agent_requires_schemas(self) -> CapabilityDeclaration:
        if self.type == "agent":
            if self.contract is None:
                raise ValueError("agent capability requires contract with schemas")
            if self.contract.input_schema is None or self.contract.output_schema is None:
                raise ValueError(
                    "agent capability contract requires input_schema and output_schema"
                )
        return self


class MindAtlasSkillManifestV1(FrozenContract):
    version: Literal[1]
    display_name: str | None = None
    legacy_aliases: tuple[str, ...] = ()
    routing: SkillRoutingContract = Field(default_factory=SkillRoutingContract)
    capabilities: tuple[CapabilityDeclaration, ...] = ()
    policy: SkillPolicyContract = Field(default_factory=SkillPolicyContract)
    provider_aliases: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("version", mode="before")
    @classmethod
    def _validate_version(cls, value: Any) -> int:
        if value is not True and value == 1 and type(value) is int:
            return 1
        raise ValueError("manifest version must be exactly integer 1")

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > MAX_DISPLAY_NAME_LEN:
            raise ValueError(
                f"display_name must be 1–{MAX_DISPLAY_NAME_LEN} characters"
            )
        return value

    @field_validator("legacy_aliases", mode="before")
    @classmethod
    def _validate_aliases(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("legacy_aliases must be a list of strings")
        out: list[str] = []
        seen_normalized: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item:
                raise ValueError("legacy alias must be a non-empty string")
            if len(item) > MAX_ALIAS_LEN:
                raise ValueError("legacy alias exceeds length limit")
            normalized = normalize_skill_lookup_name(item)
            if normalized in RESERVED_SKILL_LOOKUP_NAMES:
                raise ValueError(f"legacy alias {item!r} is reserved")
            if normalized in seen_normalized:
                raise ValueError(f"duplicate legacy alias {item!r}")
            seen_normalized.add(normalized)
            out.append(item)
        return tuple(out)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("capabilities must be a list")
        if len(value) > MAX_CAPABILITIES:
            raise ValueError(f"capabilities exceed {MAX_CAPABILITIES} items")
        return tuple(value)

    @field_validator("provider_aliases", mode="before")
    @classmethod
    def _validate_provider_aliases(cls, value: Any) -> dict[str, str]:
        return _require_str_str_map(
            value,
            field_name="provider_aliases",
            max_entries=MAX_PROVIDER_ALIASES,
            max_key_len=MAX_CAPABILITY_KEY_LEN,
            max_value_len=MAX_PROVIDER_ALIAS_HINT_LEN,
        )

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_manifest_metadata(cls, value: Any) -> dict[str, str]:
        return _require_str_str_map(
            value,
            field_name="metadata",
            max_entries=MAX_MANIFEST_METADATA_ENTRIES,
        )

    @field_validator("routing", mode="before")
    @classmethod
    def _validate_routing(cls, value: Any) -> Any:
        if value is None:
            return SkillRoutingContract()
        return value

    @field_validator("policy", mode="before")
    @classmethod
    def _validate_policy(cls, value: Any) -> Any:
        if value is None:
            return SkillPolicyContract()
        return value

    @model_validator(mode="after")
    def _cross_field(self) -> MindAtlasSkillManifestV1:
        pairs: set[tuple[str, str]] = set()
        keys: set[str] = set()
        for cap in self.capabilities:
            pair = (cap.type, cap.key)
            if pair in pairs:
                raise ValueError(
                    f"duplicate capability pair type={cap.type!r} key={cap.key!r}"
                )
            pairs.add(pair)
            keys.add(cap.key)

        for alias_key, hint in self.provider_aliases.items():
            if not hint.strip():
                raise ValueError("provider_aliases hints must be non-empty")
            if alias_key not in keys:
                raise ValueError(
                    f"provider_aliases key {alias_key!r} is not a declared capability key"
                )

        # Canonical name reservation also applies if an alias normalizes to reserved.
        for alias in self.legacy_aliases:
            if is_reserved_skill_lookup_name(alias):
                raise ValueError(f"legacy alias {alias!r} is reserved")
        return self


class ParsedSkillPackage(FrozenContract):
    canonical_name: str
    frontmatter: AgentSkillFrontmatter
    manifest: MindAtlasSkillManifestV1 | None
    skill_md_bytes: bytes
    mindatlas_yaml_bytes: bytes | None
    resources: tuple[ParsedSkillResource, ...]
    resource_index: tuple[SkillResourceIndexEntry, ...]
    skill_md_digest: str
    manifest_digest: str
    resource_index_digest: str
    content_digest: str


__all__ = [
    "AgentSkillFrontmatter",
    "CapabilityBindingContract",
    "CapabilityDeclaration",
    "CapabilityCompletionContract",
    "MAX_ALIAS_LEN",
    "MAX_ALIAS_UTF8_BYTES",
    "MAX_CAPABILITIES",
    "MAX_DESCRIPTION_LEN",
    "MAX_DISPLAY_NAME_LEN",
    "MAX_SKILL_NAME_LEN",
    "MindAtlasSkillManifestV1",
    "ParsedSkillPackage",
    "ParsedSkillResource",
    "RESERVED_SKILL_LOOKUP_NAMES",
    "SkillConflictRuleV1",
    "SkillPolicyContract",
    "SkillResourceIndexEntry",
    "SkillRoutingContract",
    "is_reserved_skill_lookup_name",
    "normalize_skill_lookup_name",
    "validate_canonical_skill_name",
]
