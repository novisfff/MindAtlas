"""API-safe schemas for Agent Skill package aggregates and version history."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract, MainAgentMigrationState
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.skills.contracts import MAX_CAPABILITY_KEY_LEN, ParsedSkillPackage
from app.common.schemas import CamelModel


SkillPackageMigrationState = Literal["shadow", "native", "cutover"]
VersionSource = Literal["save", "publish"]
AliasType = Literal["canonical", "legacy", "custom"]
ResourceKind = Literal["scripts", "references", "assets", "other"]
MainAgentEntrypoint = Literal["assistant_chat"]
SkillCatalogScopeMode = Literal["all_published", "allowlist"]

# Main Agent Profile v1 hard ceilings (Decision 6 / Task 6 bootstrap defaults).
MAX_BASE_PROMPT_LEN = 72_000
MAX_RESPONSE_STYLE_ENTRIES = 50
MAX_RESPONSE_STYLE_VALUE_LEN = 1024
MAX_CONTROL_CAPABILITY_KEYS = 100
MAX_ALLOWLIST_PACKAGE_IDS = 1000

MAX_PROMPT_CHARACTERS = 72_000
MAX_ACTIVE_SKILLS = 4
MAX_SKILL_INSTRUCTION_CHARACTERS = 24_000
MAX_SINGLE_SKILL_INSTRUCTION_CHARACTERS = 12_000
MAX_HISTORY_CHARACTERS = 24_000
MAX_TOOL_SUMMARY_CHARACTERS = 24_000
MAX_RESOURCE_BYTES_PER_CALL = 65_536

MAX_COMPLETION_TOKENS = 4_096
MAX_PROVIDER_ROUNDS = 8
MAX_OUTER_AGENT_ROUNDS = 8
MAX_TOTAL_CAPABILITY_CALLS = 16
MAX_PARALLEL_CALLS = 4
MAX_CAPABILITY_DEPTH = 4
MAX_AGENT_DEPTH = 2
MAX_SAME_READ_SIGNATURE = 3
MAX_COMPLETION_FOLLOWUP_ROUNDS = 2
MAX_WALL_TIME_MS = 120_000

DEFAULT_MAIN_AGENT_PROFILE_KEY = "default"
DEFAULT_MAIN_AGENT_DISPLAY_NAME = "Default Main Agent"
DEFAULT_BOOTSTRAP_BASE_PROMPT = (
    "You are the MindAtlas main assistant. Answer directly when no specialized "
    "Skill is required. Prefer concise, accurate responses grounded in the "
    "user's knowledge graph when relevant."
)

KNOWN_MAIN_AGENT_ENTRYPOINTS: tuple[MainAgentEntrypoint, ...] = ("assistant_chat",)


class SkillResourceInput(CamelModel):
    """JSON API resource entry: path + base64 content only."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=None,
        extra="forbid",
    )

    path: str
    content_base64: str = Field(alias="contentBase64")


class SkillPackageJsonCreateRequest(CamelModel):
    """Router-facing create body. Media types/IDs/digests are forbidden."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    skill_md: str = Field(alias="skillMd")
    mindatlas_yaml: str | None = Field(default=None, alias="mindatlasYaml")
    resources: list[SkillResourceInput] = Field(default_factory=list)
    version_name: str | None = Field(default=None, alias="versionName")


class SkillPackageJsonSaveRequest(CamelModel):
    """Router-facing draft save body. Media types/IDs/digests are forbidden.

    Plan 09: optional ``expectedAggregateRevision`` + ``requestId`` enable
    optimistic concurrency and identical-retry idempotency. When either is
    supplied both should be supplied for CAS; mismatch → 409.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    skill_md: str = Field(alias="skillMd")
    mindatlas_yaml: str | None = Field(default=None, alias="mindatlasYaml")
    # None = preserve previous draft resources (server-side copy).
    # Explicit [] = clear all resources. List = full replacement snapshot.
    resources: list[SkillResourceInput] | None = None
    version_name: str | None = Field(default=None, alias="versionName")
    expected_aggregate_revision: int | None = Field(
        default=None, alias="expectedAggregateRevision", ge=0
    )
    request_id: str | None = Field(default=None, alias="requestId", min_length=1, max_length=128)


class CreateSkillPackageCommand(CamelModel):
    """Service command after router-side parse of package files."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    parsed: ParsedSkillPackage
    version_name: str | None = None
    origin: Literal["api", "import"] = "api"


class SaveSkillDraftCommand(CamelModel):
    """Service command to append or re-point a draft version."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    package_id: UUID
    parsed: ParsedSkillPackage
    version_name: str | None = None
    origin: Literal["api", "import", "legacy"] = "api"
    expected_aggregate_revision: int | None = None
    request_id: str | None = None
    # When True, server copies resources from the current draft version
    # instead of using parsed.resources (used when client omits resources).
    preserve_previous_resources: bool = False


class PublishSkillVersionCommand(CamelModel):
    """Publish requires an explicit draft version id; never resolves latest.

    Plan 09: optional ``gate_id`` + ``gate_subject`` for publish-gate enforcement.
    Client never supplies passed/decision/metrics — only evidence refs via gate.
    """

    model_config = ConfigDict(extra="forbid")

    draft_version_id: UUID
    gate_id: UUID | None = None
    # Opaque server-recomputed subject closure (PublishGateSubject dict / model).
    # When omitted, service rebuilds closure from draft digests under lock.
    gate_subject: dict[str, Any] | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class PublishMainAgentProfileCommand(CamelModel):
    """Publish requires an explicit draft version id; never resolves latest."""

    model_config = ConfigDict(extra="forbid")

    draft_version_id: UUID
    gate_id: UUID | None = None
    gate_subject: dict[str, Any] | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class SkillResourceMetadata(CamelModel):
    """Resource metadata without bytes."""

    path: str
    resource_kind: ResourceKind
    media_type: str
    byte_size: int
    sha256: str
    executable: bool = False


class SkillVersionSummary(CamelModel):
    id: UUID
    skill_package_id: UUID
    sequence_no: int
    version_name: str
    version_source: VersionSource
    origin: str
    content_digest: str
    skill_md_digest: str
    manifest_digest: str
    resource_index_digest: str
    binding_set_digest: str | None = None
    version_digest: str | None = None
    source_draft_version_id: UUID | None = None
    created_at: datetime | None = None


class SkillVersionDetail(SkillVersionSummary):
    frontmatter: dict[str, Any]
    extension_manifest: dict[str, Any] | None = None
    resource_index: list[dict[str, Any]]
    resources: list[SkillResourceMetadata] = Field(default_factory=list)
    # skill_md / mindatlas_yaml text are available on detail for authoring UIs;
    # resource *bytes* are never included.
    skill_md: str
    mindatlas_yaml: str | None = None


class SkillPackageAliasSummary(CamelModel):
    id: UUID
    alias: str
    normalized_alias: str
    alias_type: AliasType
    created_at: datetime | None = None
    disabled_at: datetime | None = None
    disabled_by: str | None = None


class SkillPackageSummary(CamelModel):
    id: UUID
    canonical_name: str
    display_name: str
    description: str
    migration_state: SkillPackageMigrationState
    catalog_enabled: bool
    is_system: bool
    aggregate_revision: int = 0
    archived_at: datetime | None = None
    archived_by: str | None = None
    catalog_enabled_at: datetime | None = None
    catalog_enabled_by: str | None = None
    draft_version: SkillVersionSummary | None = None
    published_version: SkillVersionSummary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillPackageDetail(SkillPackageSummary):
    aliases: list[SkillPackageAliasSummary] = Field(default_factory=list)
    legacy_skill_id: UUID | None = None
    legacy_source_digest: str | None = None


# ---------------------------------------------------------------------------
# Plan 09 aggregate admin commands / DTOs
# ---------------------------------------------------------------------------


class UpdateSkillPackageMetadataCommand(CamelModel):
    """Revision-CAS metadata update (display name / description only)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(ge=0)
    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)


class AggregateRevisionCommand(CamelModel):
    """Shared body for archive / unarchive / catalog / alias CAS mutations."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(ge=0)
    # Plan 09: required for catalog/runtime enable (never for archive/alias).
    gate_id: UUID | None = None
    gate_subject: dict[str, Any] | None = None


class AddSkillPackageAliasCommand(CamelModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(ge=0)
    alias: str = Field(min_length=1, max_length=512)


class DisableSkillPackageAliasCommand(CamelModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(ge=0)


class RestoreSkillVersionAsDraftCommand(CamelModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Plan 09 import preview / apply contracts
# ---------------------------------------------------------------------------


ImportMode = Literal["create", "append_to_existing", "fork_as_new"]


class ImportPreviewToken(FrozenContract):
    """Opaque server-side preview binding (never echoes raw archive bytes)."""

    preview_id: UUID
    actor_scope_digest: str
    mode: ImportMode
    target_package_id: UUID | None = None
    expected_aggregate_revision: int | None = None
    upload_digest: str
    candidate_content_digest: str
    expires_at: datetime


class ImportPreviewResult(CamelModel):
    """Bounded dry-run preview for create / append / fork import."""

    model_config = ConfigDict(extra="forbid")

    preview_id: UUID
    mode: ImportMode
    upload_digest: str
    candidate_content_digest: str
    candidate_canonical_name: str
    target_package_id: UUID | None = None
    expected_aggregate_revision: int | None = None
    expires_at: datetime
    resource_index: list[dict[str, Any]] = Field(default_factory=list)
    capability_keys: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    structural_diff: list[dict[str, Any]] = Field(default_factory=list)
    # Explicit safety flags for clients / audits.
    resource_bytes_excluded: bool = True
    raw_archive_excluded: bool = True


class ImportApplyResult(CamelModel):
    """Result of consuming a preview token into an unpublished draft."""

    model_config = ConfigDict(extra="forbid")

    mode: ImportMode
    preview_id: UUID
    request_id: str
    package: SkillPackageDetail


class ImportApplyCommand(CamelModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: UUID
    request_id: str = Field(min_length=1, max_length=128)


class SkillVersionDiffHunk(CamelModel):
    """Bounded text hunk for version compare (no secrets / unbounded bodies)."""

    path: str
    kind: Literal["added", "removed", "changed", "unchanged_meta"]
    left_digest: str | None = None
    right_digest: str | None = None
    left_preview: str | None = None
    right_preview: str | None = None
    truncated: bool = False


class SkillVersionDiffResult(CamelModel):
    package_id: UUID
    left_version_id: UUID
    right_version_id: UUID
    left_content_digest: str
    right_content_digest: str
    left_metadata: dict[str, Any]
    right_metadata: dict[str, Any]
    hunks: list[SkillVersionDiffHunk] = Field(default_factory=list)
    resource_bytes_excluded: bool = True
    secrets_excluded: bool = True
    unbounded_bodies_excluded: bool = True


# ---------------------------------------------------------------------------
# Main Agent Profile v1 snapshot + service DTOs
# ---------------------------------------------------------------------------


def _positive_bounded(value: int, *, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    if value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


class ModelRequirementsV1(FrozenContract):
    tool_calling: bool
    streaming: bool
    multi_tool_calls: bool
    json_schema: bool

    @field_validator(
        "tool_calling",
        "streaming",
        "multi_tool_calls",
        "json_schema",
        mode="before",
    )
    @classmethod
    def _strict_bool(cls, value: Any) -> bool:
        if type(value) is not bool:
            raise ValueError("model requirement flags must be booleans")
        return value


class SkillCatalogScopeV1(FrozenContract):
    mode: SkillCatalogScopeMode = "all_published"
    package_ids: tuple[UUID, ...] = ()

    @field_validator("package_ids", mode="before")
    @classmethod
    def _coerce_package_ids(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(value)
        raise ValueError("packageIds must be a list of UUIDs")

    @model_validator(mode="after")
    def _validate_scope(self) -> SkillCatalogScopeV1:
        ids = self.package_ids
        if self.mode == "all_published":
            if ids:
                raise ValueError("all_published requires empty packageIds")
            return self
        if self.mode == "allowlist":
            if not ids or len(ids) > MAX_ALLOWLIST_PACKAGE_IDS:
                raise ValueError(
                    f"allowlist requires 1–{MAX_ALLOWLIST_PACKAGE_IDS} unique packageIds"
                )
            normalized = tuple(str(item) for item in ids)
            if len(set(normalized)) != len(normalized):
                raise ValueError("packageIds must be unique")
            ordered = tuple(sorted(ids, key=lambda item: str(item)))
            if ordered != ids:
                raise ValueError(
                    "packageIds must be sorted by canonical UUID text"
                )
            return self
        raise ValueError("skillCatalogScope.mode is invalid")


class ContextBudgetV1(FrozenContract):
    max_prompt_characters: int
    max_active_skills: int
    max_skill_instruction_characters: int
    max_single_skill_instruction_characters: int
    max_history_characters: int
    max_tool_summary_characters: int
    max_resource_bytes_per_call: int

    @model_validator(mode="after")
    def _validate_bounds(self) -> ContextBudgetV1:
        _positive_bounded(
            self.max_prompt_characters,
            name="maxPromptCharacters",
            maximum=MAX_PROMPT_CHARACTERS,
        )
        _positive_bounded(
            self.max_active_skills,
            name="maxActiveSkills",
            maximum=MAX_ACTIVE_SKILLS,
        )
        _positive_bounded(
            self.max_skill_instruction_characters,
            name="maxSkillInstructionCharacters",
            maximum=MAX_SKILL_INSTRUCTION_CHARACTERS,
        )
        _positive_bounded(
            self.max_single_skill_instruction_characters,
            name="maxSingleSkillInstructionCharacters",
            maximum=MAX_SINGLE_SKILL_INSTRUCTION_CHARACTERS,
        )
        _positive_bounded(
            self.max_history_characters,
            name="maxHistoryCharacters",
            maximum=MAX_HISTORY_CHARACTERS,
        )
        _positive_bounded(
            self.max_tool_summary_characters,
            name="maxToolSummaryCharacters",
            maximum=MAX_TOOL_SUMMARY_CHARACTERS,
        )
        _positive_bounded(
            self.max_resource_bytes_per_call,
            name="maxResourceBytesPerCall",
            maximum=MAX_RESOURCE_BYTES_PER_CALL,
        )
        if (
            self.max_single_skill_instruction_characters
            > self.max_skill_instruction_characters
        ):
            raise ValueError(
                "maxSingleSkillInstructionCharacters must be <= maxSkillInstructionCharacters"
            )
        budget_sum = (
            self.max_history_characters
            + self.max_tool_summary_characters
            + self.max_skill_instruction_characters
        )
        if budget_sum > self.max_prompt_characters:
            raise ValueError(
                "maxHistoryCharacters + maxToolSummaryCharacters + "
                "maxSkillInstructionCharacters must be <= maxPromptCharacters"
            )
        return self


class OutputBudgetV1(FrozenContract):
    max_completion_tokens: int
    max_provider_rounds: int
    max_outer_agent_rounds: int
    max_total_capability_calls: int
    max_parallel_calls: int
    max_capability_depth: int
    max_agent_depth: int
    max_same_read_signature: int
    max_completion_followup_rounds: int
    max_wall_time_ms: int

    @model_validator(mode="after")
    def _validate_bounds(self) -> OutputBudgetV1:
        _positive_bounded(
            self.max_completion_tokens,
            name="maxCompletionTokens",
            maximum=MAX_COMPLETION_TOKENS,
        )
        _positive_bounded(
            self.max_provider_rounds,
            name="maxProviderRounds",
            maximum=MAX_PROVIDER_ROUNDS,
        )
        _positive_bounded(
            self.max_outer_agent_rounds,
            name="maxOuterAgentRounds",
            maximum=MAX_OUTER_AGENT_ROUNDS,
        )
        _positive_bounded(
            self.max_total_capability_calls,
            name="maxTotalCapabilityCalls",
            maximum=MAX_TOTAL_CAPABILITY_CALLS,
        )
        _positive_bounded(
            self.max_parallel_calls,
            name="maxParallelCalls",
            maximum=MAX_PARALLEL_CALLS,
        )
        _positive_bounded(
            self.max_capability_depth,
            name="maxCapabilityDepth",
            maximum=MAX_CAPABILITY_DEPTH,
        )
        _positive_bounded(
            self.max_agent_depth,
            name="maxAgentDepth",
            maximum=MAX_AGENT_DEPTH,
        )
        _positive_bounded(
            self.max_same_read_signature,
            name="maxSameReadSignature",
            maximum=MAX_SAME_READ_SIGNATURE,
        )
        # followup rounds may be 0 conceptually, but bootstrap uses 2; accept 1..ceiling
        # and enforce strict < maxProviderRounds below.
        if (
            not isinstance(self.max_completion_followup_rounds, int)
            or isinstance(self.max_completion_followup_rounds, bool)
            or self.max_completion_followup_rounds < 0
            or self.max_completion_followup_rounds > MAX_COMPLETION_FOLLOWUP_ROUNDS
        ):
            raise ValueError(
                f"maxCompletionFollowupRounds must be between 0 and {MAX_COMPLETION_FOLLOWUP_ROUNDS}"
            )
        _positive_bounded(
            self.max_wall_time_ms,
            name="maxWallTimeMs",
            maximum=MAX_WALL_TIME_MS,
        )
        if self.max_parallel_calls > self.max_total_capability_calls:
            raise ValueError(
                "maxParallelCalls must be <= maxTotalCapabilityCalls"
            )
        if self.max_same_read_signature > self.max_total_capability_calls:
            raise ValueError(
                "maxSameReadSignature must be <= maxTotalCapabilityCalls"
            )
        if self.max_completion_followup_rounds >= self.max_provider_rounds:
            raise ValueError(
                "maxCompletionFollowupRounds must be < maxProviderRounds"
            )
        return self


class GlobalSafetyPolicyV1(FrozenContract):
    deny_by_default: Literal[True] = True


class FallbackPolicyV1(FrozenContract):
    legacy_runtime_allowed: bool
    before_side_effects_only: bool

    @model_validator(mode="after")
    def _validate_fallback(self) -> FallbackPolicyV1:
        if self.legacy_runtime_allowed and not self.before_side_effects_only:
            raise ValueError(
                "legacyRuntimeAllowed cannot be true when beforeSideEffectsOnly is false"
            )
        return self


class MainAgentProfileSnapshotV1(FrozenContract):
    """Immutable Main Agent Profile v1 snapshot (Decision 6)."""

    schema_version: Literal[1]
    base_prompt: str
    response_style: dict[str, str] = Field(default_factory=dict)
    supported_entrypoints: tuple[MainAgentEntrypoint, ...]
    model_requirements: ModelRequirementsV1
    control_capability_keys: tuple[str, ...] = ()
    skill_catalog_scope: SkillCatalogScopeV1 = Field(
        default_factory=SkillCatalogScopeV1
    )
    context_budget: ContextBudgetV1
    output_budget: OutputBudgetV1
    global_safety_policy: GlobalSafetyPolicyV1
    fallback_policy: FallbackPolicyV1

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_schema_version(cls, value: Any) -> int:
        if value is not True and value == 1 and type(value) is int:
            return 1
        raise ValueError("schemaVersion must be exactly integer 1")

    @field_validator("base_prompt")
    @classmethod
    def _validate_base_prompt(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("basePrompt must be a non-empty string")
        if len(value) > MAX_BASE_PROMPT_LEN:
            raise ValueError(
                f"basePrompt exceeds {MAX_BASE_PROMPT_LEN} characters"
            )
        return value

    @field_validator("response_style", mode="before")
    @classmethod
    def _validate_response_style(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("responseStyle must be a string-to-string map")
        if len(value) > MAX_RESPONSE_STYLE_ENTRIES:
            raise ValueError(
                f"responseStyle exceeds {MAX_RESPONSE_STYLE_ENTRIES} entries"
            )
        out: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise ValueError("responseStyle keys and values must be strings")
            if not key or len(key) > 128:
                raise ValueError("responseStyle key length invalid")
            if len(item) > MAX_RESPONSE_STYLE_VALUE_LEN:
                raise ValueError("responseStyle value length invalid")
            out[key] = item
        return out

    @field_validator("supported_entrypoints", mode="before")
    @classmethod
    def _validate_entrypoints(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("supportedEntrypoints must be a non-empty list")
        known = set(KNOWN_MAIN_AGENT_ENTRYPOINTS)
        seen: set[str] = set()
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in known:
                raise ValueError(f"unknown entrypoint {item!r}")
            if item in seen:
                raise ValueError("supportedEntrypoints must be unique")
            seen.add(item)
            out.append(item)
        ordered = tuple(sorted(out))
        if ordered != tuple(out):
            raise ValueError(
                "supportedEntrypoints must be deterministically ordered"
            )
        return ordered

    @field_validator("control_capability_keys", mode="before")
    @classmethod
    def _validate_control_keys(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("controlCapabilityKeys must be a list of Domain Keys")
        if len(value) > MAX_CONTROL_CAPABILITY_KEYS:
            raise ValueError(
                f"controlCapabilityKeys exceed {MAX_CONTROL_CAPABILITY_KEYS} items"
            )
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            if (
                not isinstance(item, str)
                or not item
                or len(item) > MAX_CAPABILITY_KEY_LEN
            ):
                raise ValueError(
                    f"control Capability key must be 1–{MAX_CAPABILITY_KEY_LEN} characters"
                )
            if item in seen:
                raise ValueError("controlCapabilityKeys must be unique")
            seen.add(item)
            out.append(item)
        return tuple(out)

    def normalized_payload(self) -> dict[str, Any]:
        """Return the canonical camelCase JSON payload used for digests/storage."""
        return self.model_dump(by_alias=True, mode="json")

    def content_digest(self) -> str:
        return sha256_canonical_json(self.normalized_payload())


def default_main_agent_profile_snapshot() -> MainAgentProfileSnapshotV1:
    """Conservative bootstrap default (Decision 6). Not an activated runtime."""
    return MainAgentProfileSnapshotV1(
        schema_version=1,
        base_prompt=DEFAULT_BOOTSTRAP_BASE_PROMPT,
        response_style={},
        supported_entrypoints=("assistant_chat",),
        model_requirements=ModelRequirementsV1(
            tool_calling=True,
            streaming=True,
            multi_tool_calls=True,
            json_schema=True,
        ),
        control_capability_keys=(),
        skill_catalog_scope=SkillCatalogScopeV1(
            mode="all_published",
            package_ids=(),
        ),
        context_budget=ContextBudgetV1(
            max_prompt_characters=MAX_PROMPT_CHARACTERS,
            max_active_skills=MAX_ACTIVE_SKILLS,
            max_skill_instruction_characters=MAX_SKILL_INSTRUCTION_CHARACTERS,
            max_single_skill_instruction_characters=MAX_SINGLE_SKILL_INSTRUCTION_CHARACTERS,
            max_history_characters=MAX_HISTORY_CHARACTERS,
            max_tool_summary_characters=MAX_TOOL_SUMMARY_CHARACTERS,
            max_resource_bytes_per_call=MAX_RESOURCE_BYTES_PER_CALL,
        ),
        output_budget=OutputBudgetV1(
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            max_provider_rounds=MAX_PROVIDER_ROUNDS,
            max_outer_agent_rounds=MAX_OUTER_AGENT_ROUNDS,
            max_total_capability_calls=MAX_TOTAL_CAPABILITY_CALLS,
            max_parallel_calls=MAX_PARALLEL_CALLS,
            max_capability_depth=MAX_CAPABILITY_DEPTH,
            max_agent_depth=MAX_AGENT_DEPTH,
            max_same_read_signature=MAX_SAME_READ_SIGNATURE,
            max_completion_followup_rounds=MAX_COMPLETION_FOLLOWUP_ROUNDS,
            max_wall_time_ms=MAX_WALL_TIME_MS,
        ),
        global_safety_policy=GlobalSafetyPolicyV1(deny_by_default=True),
        fallback_policy=FallbackPolicyV1(
            legacy_runtime_allowed=True,
            before_side_effects_only=True,
        ),
    )


class SaveMainAgentProfileDraftCommand(CamelModel):
    """Append or re-point a Main Agent Profile draft."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    snapshot: MainAgentProfileSnapshotV1
    version_name: str | None = None
    origin: Literal["api", "legacy"] = "api"
    source_ref: dict[str, Any] | None = None


class MainAgentProfileVersionSummary(CamelModel):
    id: UUID
    profile_id: UUID
    sequence_no: int
    version_name: str
    version_source: VersionSource
    origin: str
    content_digest: str
    source_draft_version_id: UUID | None = None
    created_at: datetime | None = None


class MainAgentProfileVersionDetail(MainAgentProfileVersionSummary):
    snapshot: dict[str, Any]
    source_ref: dict[str, Any] | None = None


class MainAgentProfileSummary(CamelModel):
    id: UUID
    profile_key: str
    display_name: str
    is_default: bool
    migration_state: MainAgentMigrationState
    runtime_enabled: bool
    draft_version: MainAgentProfileVersionSummary | None = None
    published_version: MainAgentProfileVersionSummary | None = None
    legacy_skill_id: UUID | None = None
    legacy_source_digest: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "AliasType",
    "ContextBudgetV1",
    "CreateSkillPackageCommand",
    "DEFAULT_BOOTSTRAP_BASE_PROMPT",
    "DEFAULT_MAIN_AGENT_DISPLAY_NAME",
    "DEFAULT_MAIN_AGENT_PROFILE_KEY",
    "FallbackPolicyV1",
    "GlobalSafetyPolicyV1",
    "ImportApplyCommand",
    "ImportApplyResult",
    "ImportMode",
    "ImportPreviewResult",
    "ImportPreviewToken",
    "KNOWN_MAIN_AGENT_ENTRYPOINTS",
    "MainAgentEntrypoint",
    "MainAgentProfileSnapshotV1",
    "MainAgentProfileSummary",
    "MainAgentProfileVersionDetail",
    "MainAgentProfileVersionSummary",
    "ModelRequirementsV1",
    "OutputBudgetV1",
    "PublishMainAgentProfileCommand",
    "PublishSkillVersionCommand",
    "ResourceKind",
    "SaveMainAgentProfileDraftCommand",
    "SaveSkillDraftCommand",
    "SkillCatalogScopeMode",
    "SkillCatalogScopeV1",
    "SkillPackageAliasSummary",
    "SkillPackageDetail",
    "SkillPackageJsonCreateRequest",
    "SkillPackageJsonSaveRequest",
    "SkillPackageMigrationState",
    "SkillPackageSummary",
    "SkillResourceInput",
    "SkillResourceMetadata",
    "SkillVersionDetail",
    "SkillVersionSummary",
    "VersionSource",
    "default_main_agent_profile_snapshot",
]
