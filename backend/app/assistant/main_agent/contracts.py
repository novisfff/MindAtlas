"""Frozen Main Agent contracts for protected prompt building (Plan 04 Task 3).

No database, Provider SDK, Gateway, or Skill body I/O. Prompt build reports are
intentionally safe: counts and digests only.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Sequence
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderContextUpdateMessage,
    ProviderMessage,
    ProviderSystemMessage,
    ProviderUserMessage,
)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # allow TAB; LF/CR normalized elsewhere

PromptLayerKind = Literal[
    "platform_safety",
    "profile_base",
    "entrypoint_policy",
    "skill_instructions",
    "manifest_identity",
    "catalog_summary",
    "memory_context",
    "l0_history",
    "current_user",
    "tool_artifact_summary",
]

PROMPT_BUDGET_EXCEEDED = "prompt_budget_exceeded"
SKILL_CONTEXT_BUDGET_EXCEEDED = "skill_context_budget_exceeded"
CURRENT_USER_BUDGET_EXCEEDED = "current_user_budget_exceeded"
PLATFORM_PROFILE_BUDGET_EXCEEDED = "platform_profile_budget_exceeded"
SINGLE_SKILL_BUDGET_EXCEEDED = "single_skill_budget_exceeded"
ACTIVE_SKILL_BUDGET_EXCEEDED = "active_skill_budget_exceeded"
ACTIVE_SKILL_LIMIT_EXCEEDED = "active_skill_limit_exceeded"


class MainAgentPromptBudgetExceeded(ValueError):
    """Mandatory prompt layers exceed the effective budget."""

    def __init__(self, reason_code: str, *, detail: str | None = None) -> None:
        self.reason_code = str(reason_code)
        # Keep exception args free of prompt/user/skill text.
        safe = self.reason_code if not detail else f"{self.reason_code}"
        super().__init__(safe)
        self._detail_code = detail  # optional secondary code only


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    if _CONTROL_RE.search(cleaned):
        raise ValueError(f"{field_name} must not contain control characters")
    return cleaned


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int")
    return value


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive int")
    return value


class PromptBudgetCaps(FrozenContract):
    """Optional caller/settings caps. May only lower defaults/profile budgets."""

    max_platform_profile_chars: int | None = None
    max_active_skill_instruction_chars: int | None = None
    max_single_skill_instruction_chars: int | None = None
    max_initial_catalog_chars: int | None = None
    max_history_chars: int | None = None
    max_current_user_chars: int | None = None
    max_tool_summary_chars: int | None = None
    max_total_protected_chars: int | None = None
    max_active_skills: int | None = None

    @field_validator(
        "max_platform_profile_chars",
        "max_active_skill_instruction_chars",
        "max_single_skill_instruction_chars",
        "max_initial_catalog_chars",
        "max_history_chars",
        "max_current_user_chars",
        "max_tool_summary_chars",
        "max_total_protected_chars",
        "max_active_skills",
    )
    @classmethod
    def _optional_positive(cls, value: int | None, info: Any) -> int | None:
        if value is None:
            return None
        return _require_positive_int(value, field_name=info.field_name)


class PromptBudgetLimits(FrozenContract):
    """Effective budgets after defaults, Profile snapshot, hard ceilings, and caps."""

    max_platform_profile_chars: int
    max_active_skill_instruction_chars: int
    max_single_skill_instruction_chars: int
    max_initial_catalog_chars: int
    max_history_chars: int
    max_current_user_chars: int
    max_tool_summary_chars: int
    max_total_protected_chars: int
    max_active_skills: int

    @field_validator(
        "max_platform_profile_chars",
        "max_active_skill_instruction_chars",
        "max_single_skill_instruction_chars",
        "max_initial_catalog_chars",
        "max_history_chars",
        "max_current_user_chars",
        "max_tool_summary_chars",
        "max_total_protected_chars",
        "max_active_skills",
    )
    @classmethod
    def _positive(cls, value: int, info: Any) -> int:
        return _require_positive_int(value, field_name=info.field_name)


class CatalogSummaryRecord(FrozenContract):
    """Provider-visible Catalog summary projection (no skill body)."""

    version_id: UUID
    canonical_name: str
    description: str
    content_digest: str
    rank: int = 0

    @field_validator("canonical_name", "description")
    @classmethod
    def _text(cls, value: str, info: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{info.field_name} must be a string")
        if _CONTROL_RE.search(value):
            raise ValueError(f"{info.field_name} must not contain control characters")
        return value

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="content_digest")

    @field_validator("rank")
    @classmethod
    def _rank(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="rank")


class ToolArtifactSummary(FrozenContract):
    """Bounded Capability/Artifact reference summary (no raw payload)."""

    summary_kind: Literal["tool", "artifact"]
    identity: str
    content_digest: str | None = None
    char_count: int = 0
    text: str = ""

    @field_validator("identity")
    @classmethod
    def _identity(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="identity")

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, field_name="content_digest")

    @field_validator("char_count")
    @classmethod
    def _chars(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="char_count")

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("text must be a string")
        if _CONTROL_RE.search(value):
            raise ValueError("text must not contain control characters")
        return value


class ActiveSkillInstruction(FrozenContract):
    """Exact published Skill instruction body for protected context injection."""

    package_id: UUID
    version_id: UUID
    canonical_name: str
    content_digest: str
    version_digest: str
    instructions: str

    @field_validator("canonical_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="canonical_name")

    @field_validator("content_digest", "version_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("instructions")
    @classmethod
    def _instructions(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("instructions must be a string")
        if not value.strip():
            raise ValueError("instructions must be non-empty")
        # Raw skill bodies may contain LF; builder normalizes before Provider emit.
        if "\x00" in value:
            raise ValueError("instructions must not contain NUL")
        return value


class PromptLayerReport(FrozenContract):
    """Safe per-layer report: IDs/digests/counts only."""

    layer_kind: PromptLayerKind
    source_ids: tuple[str, ...] = ()
    source_digests: tuple[str, ...] = ()
    included_char_count: int = 0
    included_byte_count: int = 0
    omitted_record_count: int = 0
    truncation_reason_codes: tuple[str, ...] = ()

    @field_validator("included_char_count", "included_byte_count", "omitted_record_count")
    @classmethod
    def _counts(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("source_ids", "source_digests", "truncation_reason_codes", mode="before")
    @classmethod
    def _str_tuple(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("expected a sequence of strings")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("sequence items must be strings")
            # Reject anything that looks like a secret-bearing blob in reports.
            if _CONTROL_RE.search(item) or "\n" in item or "\r" in item:
                raise ValueError("report fields must not contain control characters")
            if len(item) > 256:
                raise ValueError("report field values must be <= 256 characters")
            out.append(item)
        return tuple(out)


class PromptBuildReport(FrozenContract):
    """Safe aggregate prompt build report. Never contains prompt/user/skill text."""

    layers: tuple[PromptLayerReport, ...]
    total_char_count: int
    total_byte_count: int
    prompt_build_digest: str
    omitted_catalog_count: int = 0
    omitted_l0_pair_count: int = 0
    l1_truncated: bool = False
    reason_codes: tuple[str, ...] = ()
    applied_skill_version_ids: tuple[UUID, ...] = ()

    @field_validator("total_char_count", "total_byte_count", "omitted_catalog_count", "omitted_l0_pair_count")
    @classmethod
    def _counts(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("prompt_build_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="prompt_build_digest")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _reasons(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("reason_codes must be a sequence")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item or len(item) > 128:
                raise ValueError("reason_codes must be short non-empty strings")
            if _CONTROL_RE.search(item) or "\n" in item or "\r" in item:
                raise ValueError("reason_codes must not contain control characters")
            out.append(item)
        return tuple(out)

    @field_validator("applied_skill_version_ids", mode="before")
    @classmethod
    def _skill_ids(cls, value: Any) -> tuple[UUID, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("applied_skill_version_ids must be a sequence")
        out: list[UUID] = []
        seen: set[UUID] = set()
        for item in value:
            if not isinstance(item, UUID):
                raise TypeError("applied_skill_version_ids must contain UUID values")
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return tuple(out)

    @model_validator(mode="after")
    def _layers_ok(self) -> PromptBuildReport:
        for layer in self.layers:
            if not isinstance(layer, PromptLayerReport):
                raise TypeError("layers must contain PromptLayerReport")
        return self

    def __repr__(self) -> str:
        # Explicit safe repr: no chance of pydantic dumping nested text.
        return (
            "PromptBuildReport("
            f"layers={len(self.layers)}, "
            f"total_char_count={self.total_char_count}, "
            f"total_byte_count={self.total_byte_count}, "
            f"prompt_build_digest={self.prompt_build_digest!r}, "
            f"omitted_catalog_count={self.omitted_catalog_count}, "
            f"omitted_l0_pair_count={self.omitted_l0_pair_count}, "
            f"l1_truncated={self.l1_truncated}, "
            f"reason_codes={self.reason_codes!r}, "
            f"applied_skill_version_ids={len(self.applied_skill_version_ids)}"
            ")"
        )


class PromptBuildResult(FrozenContract):
    """Initial Provider messages plus safe report."""

    messages: tuple[ProviderMessage, ...]
    report: PromptBuildReport
    budgets: PromptBudgetLimits

    @field_validator("messages", mode="before")
    @classmethod
    def _messages(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("messages must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _message_types(self) -> PromptBuildResult:
        allowed = (
            ProviderSystemMessage,
            ProviderUserMessage,
            ProviderAssistantMessage,
            ProviderContextUpdateMessage,
        )
        for message in self.messages:
            if not isinstance(message, allowed):
                raise TypeError("messages must be Provider message contracts")
        if not isinstance(self.report, PromptBuildReport):
            raise TypeError("report must be PromptBuildReport")
        if not isinstance(self.budgets, PromptBudgetLimits):
            raise TypeError("budgets must be PromptBudgetLimits")
        return self


class SkillContextBuildResult(FrozenContract):
    """Incremental protected Skill context messages for the next Provider round."""

    messages: tuple[ProviderContextUpdateMessage, ...]
    report: PromptBuildReport
    applied_skill_version_ids: tuple[UUID, ...]

    @field_validator("messages", mode="before")
    @classmethod
    def _messages(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("messages must be a sequence")
        return tuple(value)

    @field_validator("applied_skill_version_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> tuple[UUID, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("applied_skill_version_ids must be a sequence")
        out: list[UUID] = []
        seen: set[UUID] = set()
        for item in value:
            if not isinstance(item, UUID):
                raise TypeError("applied_skill_version_ids must contain UUID values")
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return tuple(out)

    @model_validator(mode="after")
    def _ok(self) -> SkillContextBuildResult:
        for message in self.messages:
            if not isinstance(message, ProviderContextUpdateMessage):
                raise TypeError("messages must contain ProviderContextUpdateMessage")
        if not isinstance(self.report, PromptBuildReport):
            raise TypeError("report must be PromptBuildReport")
        return self


def report_digest_payload(report: PromptBuildReport) -> dict[str, Any]:
    """Canonical safe payload for digests/logs (no prompt text)."""
    return {
        "layers": [
            {
                "layerKind": layer.layer_kind,
                "sourceIds": list(layer.source_ids),
                "sourceDigests": list(layer.source_digests),
                "includedCharCount": layer.included_char_count,
                "includedByteCount": layer.included_byte_count,
                "omittedRecordCount": layer.omitted_record_count,
                "truncationReasonCodes": list(layer.truncation_reason_codes),
            }
            for layer in report.layers
        ],
        "totalCharCount": report.total_char_count,
        "totalByteCount": report.total_byte_count,
        "promptBuildDigest": report.prompt_build_digest,
        "omittedCatalogCount": report.omitted_catalog_count,
        "omittedL0PairCount": report.omitted_l0_pair_count,
        "l1Truncated": report.l1_truncated,
        "reasonCodes": list(report.reason_codes),
        "appliedSkillVersionIds": [str(item) for item in report.applied_skill_version_ids],
    }


def digest_prompt_build_report(report: PromptBuildReport) -> str:
    return sha256_canonical_json(report_digest_payload(report))


__all__ = [
    "ACTIVE_SKILL_BUDGET_EXCEEDED",
    "ACTIVE_SKILL_LIMIT_EXCEEDED",
    "ActiveSkillInstruction",
    "CURRENT_USER_BUDGET_EXCEEDED",
    "CatalogSummaryRecord",
    "MainAgentPromptBudgetExceeded",
    "PLATFORM_PROFILE_BUDGET_EXCEEDED",
    "PROMPT_BUDGET_EXCEEDED",
    "PromptBudgetCaps",
    "PromptBudgetLimits",
    "PromptBuildReport",
    "PromptBuildResult",
    "PromptLayerKind",
    "PromptLayerReport",
    "SINGLE_SKILL_BUDGET_EXCEEDED",
    "SKILL_CONTEXT_BUDGET_EXCEEDED",
    "SkillContextBuildResult",
    "ToolArtifactSummary",
    "digest_prompt_build_report",
    "report_digest_payload",
]
