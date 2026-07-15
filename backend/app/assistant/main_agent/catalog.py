"""Immutable per-Run Skill Catalog snapshot and lexical recall (Plan 04 Task 4).

Hard rules for this module:
- snapshot holds only frozen summary records (no ORM/Session/resource bodies)
- ranking is pure and deterministic (no hash()/locale DB collation)
- disclosed-version + cursor bookkeeping is one lock/CAS-protected atomic update
- resource bodies are never loaded during catalog construction or search
"""

from __future__ import annotations

import re
import threading
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.main_agent.contracts import CatalogSummaryRecord
from app.assistant.skills.schemas import SkillCatalogScopeV1

# ---------------------------------------------------------------------------
# Reason / error codes
# ---------------------------------------------------------------------------

CATALOG_UNAVAILABLE = "catalog_unavailable"
CATALOG_CHANGED = "catalog_changed"
CATALOG_CURSOR_INVALID = "catalog_cursor_invalid"
SKILL_NOT_DISCLOSED = "skill_not_disclosed"
SKILL_NOT_CATALOGED = "skill_not_cataloged"
SKILL_VERSION_CHANGED = "skill_version_changed"
SKILL_POLICY_UNSUPPORTED = "skill_policy_unsupported"

# ---------------------------------------------------------------------------
# Locked recall weights / thresholds (fixed vectors in tests)
# ---------------------------------------------------------------------------

FIELD_WEIGHTS: dict[str, float] = {
    "canonical_name": 10.0,
    "aliases": 8.0,
    "include_examples": 6.0,
    "description": 4.0,
    "display_name": 3.0,
}

EXCLUDE_HARD_COVERAGE = 0.80
EXCLUDE_MIN_NONEMPTY_TOKENS = 2
EXCLUDE_SOFT_PENALTY = 4.0

RRF_K = 60
DEFAULT_TOP_K = 8
MAX_TOP_K = 20
MAX_CURSORS_PER_RUN = 64
DEFAULT_CURSOR_TTL_SEARCHES = 32

# Single-skill instruction hard ceiling (chars). Catalog eligibility rejects
# oversized bodies without loading the body into the summary record.
DEFAULT_MAX_SINGLE_SKILL_INSTRUCTION_CHARS = 16_000

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+", re.ASCII)
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


class CatalogError(ValueError):
    """Safe catalog error with a stable reason code (no query/body text)."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


# ---------------------------------------------------------------------------
# Normalization / tokenization
# ---------------------------------------------------------------------------


def normalize_catalog_text(value: str) -> str:
    """NFKC + trim + casefold; reject control/NUL."""
    if not isinstance(value, str):
        raise TypeError("catalog text must be a string")
    if "\x00" in value or _CONTROL_RE.search(value):
        raise ValueError("catalog text must not contain control characters")
    cleaned = unicodedata.normalize("NFKC", value).strip()
    return cleaned.casefold()


def tokenize_catalog_text(value: str) -> list[str]:
    """Tokenize normalized text into ASCII alnum runs + CJK unigrams/bigrams.

    Frequencies are retained by callers via Counter-like counting; this returns
    the multiset as a list (order is stable left-to-right).
    """
    normalized = normalize_catalog_text(value)
    if not normalized:
        return []
    tokens: list[str] = []
    # ASCII letter/digit runs
    for match in _ASCII_TOKEN_RE.finditer(normalized):
        tokens.append(match.group(0))
    # CJK unigrams and adjacent bigrams
    cjk_chars = _CJK_RE.findall(normalized)
    if cjk_chars:
        tokens.extend(cjk_chars)
        if len(cjk_chars) >= 2:
            tokens.extend(
                f"{cjk_chars[i]}{cjk_chars[i + 1]}" for i in range(len(cjk_chars) - 1)
            )
    return tokens


def _token_multiset(value: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokenize_catalog_text(value):
        counts[token] = counts.get(token, 0) + 1
    return counts


def _overlap_score(query_counts: Mapping[str, int], field_counts: Mapping[str, int]) -> float:
    """Sum of min frequencies over shared tokens (bag-of-words overlap)."""
    if not query_counts or not field_counts:
        return 0.0
    score = 0.0
    for token, q_count in query_counts.items():
        f_count = field_counts.get(token)
        if f_count:
            score += float(min(q_count, f_count))
    return score


def _query_token_coverage(query_counts: Mapping[str, int], field_counts: Mapping[str, int]) -> float:
    """Fraction of distinct query tokens covered by the field."""
    if not query_counts:
        return 0.0
    covered = sum(1 for token in query_counts if token in field_counts)
    return covered / float(len(query_counts))


# ---------------------------------------------------------------------------
# Frozen records
# ---------------------------------------------------------------------------


class SkillCatalogRecord(FrozenContract):
    """Eligible published Skill summary for one per-Run catalog snapshot."""

    package_id: UUID
    version_id: UUID
    canonical_name: str
    display_name: str | None = None
    description: str
    locale: str = "und"
    aliases: tuple[str, ...] = ()
    include_examples: tuple[str, ...] = ()
    exclude_examples: tuple[str, ...] = ()
    content_digest: str
    version_digest: str
    resource_index_digest: str
    binding_set_digest: str
    # Eligibility bookkeeping (not Provider-visible): instruction length only.
    instruction_char_count: int = 0

    @field_validator("canonical_name", "description", "locale")
    @classmethod
    def _required_text(cls, value: str, info: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{info.field_name} must be a string")
        if not value.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        if _CONTROL_RE.search(value) or "\x00" in value:
            raise ValueError(f"{info.field_name} must not contain control characters")
        return value

    @field_validator("display_name")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("display_name must be a string")
        if _CONTROL_RE.search(value) or "\x00" in value:
            raise ValueError("display_name must not contain control characters")
        return value

    @field_validator(
        "content_digest",
        "version_digest",
        "resource_index_digest",
        "binding_set_digest",
    )
    @classmethod
    def _digest(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            raise ValueError(
                f"{info.field_name} must be a lowercase 64-character SHA-256 hex digest"
            )
        return value

    @field_validator("aliases", "include_examples", "exclude_examples", mode="before")
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
            if _CONTROL_RE.search(item) or "\x00" in item:
                raise ValueError("sequence items must not contain control characters")
            out.append(item)
        return tuple(out)

    @field_validator("instruction_char_count")
    @classmethod
    def _instruction_chars(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("instruction_char_count must be a non-negative int")
        return value


class SkillCatalogSnapshot(FrozenContract):
    """Immutable process-local catalog for one Assistant Run."""

    catalog_digest: str
    profile_scope_digest: str
    locale: str
    records: tuple[SkillCatalogRecord, ...]
    excluded_count: int = 0
    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("catalog_digest", "profile_scope_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            raise ValueError(
                f"{info.field_name} must be a lowercase 64-character SHA-256 hex digest"
            )
        return value

    @field_validator("locale")
    @classmethod
    def _locale(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("locale must be non-empty")
        return value

    @field_validator("records", mode="before")
    @classmethod
    def _records(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("records must be a sequence")
        return tuple(value)

    @field_validator("excluded_count")
    @classmethod
    def _excluded(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("excluded_count must be a non-negative int")
        return value

    @model_validator(mode="after")
    def _invariants(self) -> SkillCatalogSnapshot:
        version_ids: set[UUID] = set()
        package_ids: set[UUID] = set()
        names: list[str] = []
        for record in self.records:
            if not isinstance(record, SkillCatalogRecord):
                raise TypeError("records must contain SkillCatalogRecord")
            if record.version_id in version_ids:
                raise ValueError("catalog records must have unique version_id")
            if record.package_id in package_ids:
                raise ValueError("catalog records must have unique package_id")
            version_ids.add(record.version_id)
            package_ids.add(record.package_id)
            names.append(record.canonical_name)
        # Stable order: canonical UTF-8 name, then version UUID bytes.
        expected_order = sorted(
            self.records,
            key=lambda item: (item.canonical_name.encode("utf-8"), item.version_id.bytes),
        )
        if tuple(expected_order) != self.records:
            raise ValueError("catalog records must be sorted by name then version_id")
        return self

    def get_by_version_id(self, version_id: UUID) -> SkillCatalogRecord | None:
        for record in self.records:
            if record.version_id == version_id:
                return record
        return None

    def get_by_name_or_alias(self, name: str) -> SkillCatalogRecord | None:
        try:
            needle = normalize_catalog_text(name)
        except (TypeError, ValueError):
            return None
        for record in self.records:
            if normalize_catalog_text(record.canonical_name) == needle:
                return record
            for alias in record.aliases:
                try:
                    if normalize_catalog_text(alias) == needle:
                        return record
                except (TypeError, ValueError):
                    continue
        return None

    def provider_summaries(
        self,
        *,
        version_ids: Sequence[UUID],
        ranks: Mapping[UUID, int] | None = None,
    ) -> tuple[CatalogSummaryRecord, ...]:
        """Bounded Provider-visible projection (no package ID / body / aliases)."""
        out: list[CatalogSummaryRecord] = []
        rank_map = ranks or {}
        for version_id in version_ids:
            record = self.get_by_version_id(version_id)
            if record is None:
                continue
            out.append(
                CatalogSummaryRecord(
                    version_id=record.version_id,
                    canonical_name=record.canonical_name,
                    description=record.description,
                    content_digest=record.content_digest,
                    rank=int(rank_map.get(version_id, 0)),
                )
            )
        return tuple(out)


# ---------------------------------------------------------------------------
# Search result + cursors
# ---------------------------------------------------------------------------


class CatalogSearchHit(FrozenContract):
    version_id: UUID
    score: float
    rank: int

    @field_validator("score")
    @classmethod
    def _score(cls, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("score must be a number")
        return float(value)

    @field_validator("rank")
    @classmethod
    def _rank(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("rank must be a non-negative int")
        return value


class CatalogSearchResult(FrozenContract):
    catalog_digest: str
    query_digest: str
    hits: tuple[CatalogSearchHit, ...]
    next_cursor: str | None = None
    excluded_count: int = 0
    semantic_fallback: bool = False
    disclosed_version_ids: tuple[UUID, ...] = ()

    @field_validator("catalog_digest", "query_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            raise ValueError(
                f"{info.field_name} must be a lowercase 64-character SHA-256 hex digest"
            )
        return value

    @field_validator("hits", mode="before")
    @classmethod
    def _hits(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("hits must be a sequence")
        return tuple(value)

    @field_validator("disclosed_version_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> tuple[UUID, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("disclosed_version_ids must be a sequence")
        out: list[UUID] = []
        seen: set[UUID] = set()
        for item in value:
            if not isinstance(item, UUID):
                raise TypeError("disclosed_version_ids must contain UUID values")
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return tuple(out)


@dataclass(frozen=True)
class _CursorState:
    cursor_id: str
    catalog_digest: str
    query_digest: str
    locale: str
    next_offset: int
    remaining_uses: int


# ---------------------------------------------------------------------------
# Pure lexical scoring
# ---------------------------------------------------------------------------


def compute_query_digest(*, query: str, locale: str, limit: int) -> str:
    return sha256_canonical_json(
        {
            "query": normalize_catalog_text(query),
            "locale": locale,
            "limit": int(limit),
        }
    )


def score_record_against_query(
    record: SkillCatalogRecord,
    *,
    query: str,
) -> float | None:
    """Return lexical score, or None when the record is hard-excluded."""
    query_counts = _token_multiset(query)
    if not query_counts:
        return 0.0

    # Exclude examples: hard exclude on exact normalized match or high coverage.
    for example in record.exclude_examples:
        try:
            if normalize_catalog_text(example) == normalize_catalog_text(query):
                return None
            example_counts = _token_multiset(example)
        except (TypeError, ValueError):
            continue
        nontrivial = [t for t in example_counts if len(t) > 1 or _CJK_RE.fullmatch(t)]
        if len(nontrivial) >= EXCLUDE_MIN_NONEMPTY_TOKENS:
            coverage = _query_token_coverage(query_counts, example_counts)
            if coverage >= EXCLUDE_HARD_COVERAGE:
                return None

    score = 0.0
    score += FIELD_WEIGHTS["canonical_name"] * _overlap_score(
        query_counts, _token_multiset(record.canonical_name)
    )
    if record.display_name:
        score += FIELD_WEIGHTS["display_name"] * _overlap_score(
            query_counts, _token_multiset(record.display_name)
        )
    score += FIELD_WEIGHTS["description"] * _overlap_score(
        query_counts, _token_multiset(record.description)
    )
    alias_score = 0.0
    for alias in record.aliases:
        alias_score = max(alias_score, _overlap_score(query_counts, _token_multiset(alias)))
    score += FIELD_WEIGHTS["aliases"] * alias_score
    include_score = 0.0
    for example in record.include_examples:
        include_score = max(
            include_score, _overlap_score(query_counts, _token_multiset(example))
        )
    score += FIELD_WEIGHTS["include_examples"] * include_score

    # Soft penalty for partial exclude overlap that did not hard-exclude.
    for example in record.exclude_examples:
        try:
            example_counts = _token_multiset(example)
        except (TypeError, ValueError):
            continue
        if _overlap_score(query_counts, example_counts) > 0:
            score -= EXCLUDE_SOFT_PENALTY
            break
    return score


def rank_records_lexical(
    records: Sequence[SkillCatalogRecord],
    *,
    query: str,
) -> list[tuple[SkillCatalogRecord, float]]:
    """Score + sort: score desc, canonical UTF-8 name asc, version UUID bytes asc."""
    scored: list[tuple[SkillCatalogRecord, float]] = []
    for record in records:
        score = score_record_against_query(record, query=query)
        if score is None:
            continue
        scored.append((record, float(score)))
    scored.sort(
        key=lambda item: (
            -item[1],
            item[0].canonical_name.encode("utf-8"),
            item[0].version_id.bytes,
        )
    )
    return scored


def merge_rankings_rrf(
    *,
    lexical: Sequence[UUID],
    semantic: Sequence[UUID],
    k: int = RRF_K,
) -> list[UUID]:
    """Reciprocal Rank Fusion over eligible version IDs (deterministic)."""
    scores: dict[UUID, float] = {}
    for rank, version_id in enumerate(lexical, start=1):
        scores[version_id] = scores.get(version_id, 0.0) + 1.0 / (k + rank)
    for rank, version_id in enumerate(semantic, start=1):
        scores[version_id] = scores.get(version_id, 0.0) + 1.0 / (k + rank)
    return sorted(
        scores.keys(),
        key=lambda vid: (-scores[vid], vid.bytes),
    )


# ---------------------------------------------------------------------------
# Optional semantic port
# ---------------------------------------------------------------------------


class CatalogSemanticRecallPort(Protocol):
    def rank(
        self,
        *,
        query: str,
        eligible_version_ids: tuple[UUID, ...],
        locale: str,
    ) -> tuple[UUID, ...]: ...


# ---------------------------------------------------------------------------
# Snapshot construction (pure + optional projection ports)
# ---------------------------------------------------------------------------


def compute_profile_scope_digest(scope: SkillCatalogScopeV1) -> str:
    return sha256_canonical_json(
        {
            "mode": scope.mode,
            "packageIds": [str(item) for item in scope.package_ids],
        }
    )


def compute_catalog_digest(
    *,
    records: Sequence[SkillCatalogRecord],
    profile_scope_digest: str,
) -> str:
    payload = {
        "profileScopeDigest": profile_scope_digest,
        "records": [
            {
                "packageId": str(item.package_id),
                "versionId": str(item.version_id),
                "canonicalName": item.canonical_name,
                "displayName": item.display_name,
                "description": item.description,
                "locale": item.locale,
                "aliases": list(item.aliases),
                "includeExamples": list(item.include_examples),
                "excludeExamples": list(item.exclude_examples),
                "contentDigest": item.content_digest,
                "versionDigest": item.version_digest,
                "resourceIndexDigest": item.resource_index_digest,
                "bindingSetDigest": item.binding_set_digest,
                "instructionCharCount": item.instruction_char_count,
            }
            for item in sorted(
                records,
                key=lambda r: (r.canonical_name.encode("utf-8"), r.version_id.bytes),
            )
        ],
    }
    return sha256_canonical_json(payload)


@dataclass(frozen=True)
class CatalogCandidateProjection:
    """Session-free projection used to build a snapshot (no resource body)."""

    package_id: UUID
    version_id: UUID
    canonical_name: str
    display_name: str | None
    description: str
    locale: str
    aliases: tuple[str, ...]
    include_examples: tuple[str, ...]
    exclude_examples: tuple[str, ...]
    content_digest: str
    version_digest: str
    resource_index_digest: str
    binding_set_digest: str
    version_source: Literal["save", "publish"]
    catalog_enabled: bool
    conflict_rules: tuple[Any, ...]
    instruction_char_count: int
    # Precomputed eligibility flags from caller (binding/descriptor checks).
    bindings_eligible: bool = True
    resource_index_verified: bool = True
    binding_set_verified: bool = True
    ownership_verified: bool = True
    entrypoint_compatible: bool = True
    locale_compatible: bool = True


def evaluate_candidate_eligibility(
    candidate: CatalogCandidateProjection,
    *,
    scope: SkillCatalogScopeV1,
    max_single_skill_instruction_chars: int = DEFAULT_MAX_SINGLE_SKILL_INSTRUCTION_CHARS,
) -> str | None:
    """Return a reason code when ineligible, else None."""
    if not candidate.catalog_enabled:
        return "catalog_disabled"
    if candidate.version_source != "publish":
        return "version_not_published"
    if not candidate.ownership_verified:
        return "ownership_unverified"
    if scope.mode == "allowlist" and candidate.package_id not in set(scope.package_ids):
        return "out_of_scope"
    if not candidate.resource_index_verified:
        return "resource_index_unverified"
    if not candidate.binding_set_verified:
        return "binding_set_unverified"
    if not candidate.bindings_eligible:
        return "bindings_ineligible"
    # Plan 05: conflict_rules are catalog-eligible; structured evaluation runs at
    # skill.inject activation via evaluate_skill_conflicts (not here).
    # Invalid / unparseable rule payloads still fail closed at activation.
    if not candidate.entrypoint_compatible:
        return "entrypoint_incompatible"
    if not candidate.locale_compatible:
        return "locale_incompatible"
    if candidate.instruction_char_count > max_single_skill_instruction_chars:
        return "instruction_limit_exceeded"
    # Digests must be well-formed even if already verified.
    for digest in (
        candidate.content_digest,
        candidate.version_digest,
        candidate.resource_index_digest,
        candidate.binding_set_digest,
    ):
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            return "digest_invalid"
    return None


def build_catalog_snapshot(
    candidates: Sequence[CatalogCandidateProjection],
    *,
    scope: SkillCatalogScopeV1,
    locale: str = "und",
    max_single_skill_instruction_chars: int = DEFAULT_MAX_SINGLE_SKILL_INSTRUCTION_CHARS,
) -> SkillCatalogSnapshot:
    """Build an immutable snapshot from pure projections (no Session retained)."""
    eligible: list[SkillCatalogRecord] = []
    reason_counts: dict[str, int] = {}
    excluded = 0
    for candidate in candidates:
        reason = evaluate_candidate_eligibility(
            candidate,
            scope=scope,
            max_single_skill_instruction_chars=max_single_skill_instruction_chars,
        )
        if reason is not None:
            excluded += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        eligible.append(
            SkillCatalogRecord(
                package_id=candidate.package_id,
                version_id=candidate.version_id,
                canonical_name=candidate.canonical_name,
                display_name=candidate.display_name,
                description=candidate.description,
                locale=candidate.locale,
                aliases=candidate.aliases,
                include_examples=candidate.include_examples,
                exclude_examples=candidate.exclude_examples,
                content_digest=candidate.content_digest,
                version_digest=candidate.version_digest,
                resource_index_digest=candidate.resource_index_digest,
                binding_set_digest=candidate.binding_set_digest,
                instruction_char_count=candidate.instruction_char_count,
            )
        )
    # De-dupe by package (keep first after name/version sort for determinism).
    eligible.sort(key=lambda r: (r.canonical_name.encode("utf-8"), r.version_id.bytes))
    deduped: list[SkillCatalogRecord] = []
    seen_packages: set[UUID] = set()
    for record in eligible:
        if record.package_id in seen_packages:
            excluded += 1
            reason_counts["duplicate_package"] = reason_counts.get("duplicate_package", 0) + 1
            continue
        seen_packages.add(record.package_id)
        deduped.append(record)
    scope_digest = compute_profile_scope_digest(scope)
    catalog_digest = compute_catalog_digest(
        records=deduped,
        profile_scope_digest=scope_digest,
    )
    return SkillCatalogSnapshot(
        catalog_digest=catalog_digest,
        profile_scope_digest=scope_digest,
        locale=locale,
        records=tuple(deduped),
        excluded_count=excluded,
        exclusion_reason_counts=dict(sorted(reason_counts.items())),
    )


# ---------------------------------------------------------------------------
# Thread-safe search state (disclosed versions + opaque cursors)
# ---------------------------------------------------------------------------


TokenFactory = Callable[[], str]


def _default_token_factory() -> str:
    return uuid.uuid4().hex


class CatalogSearchState:
    """Process-local Run state for disclosed versions and opaque cursors.

    ``search`` performs ranking from the immutable snapshot, then under one lock:
    - unions returned version IDs into the disclosed set (idempotent)
    - allocates/stores that call's cursor record
    Overlapping searches cannot lose disclosed IDs or cursors.
    """

    def __init__(
        self,
        snapshot: SkillCatalogSnapshot,
        *,
        token_factory: TokenFactory | None = None,
        max_cursors: int = MAX_CURSORS_PER_RUN,
        cursor_ttl_searches: int = DEFAULT_CURSOR_TTL_SEARCHES,
        semantic_port: CatalogSemanticRecallPort | None = None,
        default_top_k: int = DEFAULT_TOP_K,
    ) -> None:
        if not isinstance(snapshot, SkillCatalogSnapshot):
            raise TypeError("snapshot must be SkillCatalogSnapshot")
        if default_top_k < 1 or default_top_k > MAX_TOP_K:
            raise ValueError(f"default_top_k must be 1..{MAX_TOP_K}")
        self._snapshot = snapshot
        self._token_factory = token_factory or _default_token_factory
        self._max_cursors = max(1, int(max_cursors))
        self._cursor_ttl_searches = max(1, int(cursor_ttl_searches))
        self._semantic_port = semantic_port
        self._default_top_k = int(default_top_k)
        self._lock = threading.RLock()
        self._disclosed: set[UUID] = set()
        self._cursors: dict[str, _CursorState] = {}
        self._search_count = 0

    @property
    def snapshot(self) -> SkillCatalogSnapshot:
        return self._snapshot

    def disclosed_version_ids(self) -> frozenset[UUID]:
        with self._lock:
            return frozenset(self._disclosed)

    def is_disclosed(self, version_id: UUID) -> bool:
        with self._lock:
            return version_id in self._disclosed

    def mark_disclosed(self, version_ids: Iterable[UUID]) -> frozenset[UUID]:
        """Idempotent union used by initial Top-K recall and search."""
        with self._lock:
            for version_id in version_ids:
                if not isinstance(version_id, UUID):
                    raise TypeError("version_ids must contain UUID values")
                self._disclosed.add(version_id)
            return frozenset(self._disclosed)

    def initial_topk(
        self,
        *,
        query: str,
        limit: int | None = None,
    ) -> CatalogSearchResult:
        """Initial catalog recall (same engine as skill.search)."""
        return self.search(query=query, limit=limit, cursor=None)

    def search(
        self,
        *,
        query: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> CatalogSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise CatalogError("invalid_query")
        if len(query) > 1000:
            raise CatalogError("invalid_query")
        try:
            normalize_catalog_text(query)
        except (TypeError, ValueError) as exc:
            raise CatalogError("invalid_query") from exc

        top_k = self._default_top_k if limit is None else int(limit)
        if top_k < 1 or top_k > MAX_TOP_K:
            raise CatalogError("invalid_limit")

        query_digest = compute_query_digest(
            query=query,
            locale=self._snapshot.locale,
            limit=top_k,
        )

        # Cursor validation and offset resolution happen under the lock together
        # with disclosed/cursor mutation so overlapping searches are atomic.
        with self._lock:
            offset = 0
            if cursor is not None:
                if not isinstance(cursor, str) or not cursor:
                    raise CatalogError(CATALOG_CURSOR_INVALID)
                state = self._cursors.get(cursor)
                if state is None:
                    raise CatalogError(CATALOG_CURSOR_INVALID)
                if state.catalog_digest != self._snapshot.catalog_digest:
                    raise CatalogError(CATALOG_CHANGED)
                if state.query_digest != query_digest:
                    raise CatalogError(CATALOG_CURSOR_INVALID)
                if state.locale != self._snapshot.locale:
                    raise CatalogError(CATALOG_CURSOR_INVALID)
                if state.remaining_uses <= 0:
                    raise CatalogError(CATALOG_CURSOR_INVALID)
                offset = state.next_offset
                # Consume one use of the presented cursor (single-Run, capped).
                self._cursors[cursor] = _CursorState(
                    cursor_id=state.cursor_id,
                    catalog_digest=state.catalog_digest,
                    query_digest=state.query_digest,
                    locale=state.locale,
                    next_offset=state.next_offset,
                    remaining_uses=state.remaining_uses - 1,
                )

            # Ranking is pure over the immutable snapshot; compute inside the lock
            # so completion order cannot affect disclosed/cursor bookkeeping for
            # concurrent callers that share this state. Ranking itself does not
            # depend on mutable disclosed state.
            lexical_ranked = rank_records_lexical(self._snapshot.records, query=query)
            lexical_ids = [record.version_id for record, _score in lexical_ranked]
            score_by_id = {
                record.version_id: score for record, score in lexical_ranked
            }

            semantic_fallback = False
            ordered_ids = list(lexical_ids)
            if self._semantic_port is not None and lexical_ids:
                try:
                    semantic_ids = tuple(
                        self._semantic_port.rank(
                            query=query,
                            eligible_version_ids=tuple(lexical_ids),
                            locale=self._snapshot.locale,
                        )
                    )
                    # Drop unknown IDs (must be eligible snapshot members).
                    eligible = set(lexical_ids)
                    semantic_ids = tuple(vid for vid in semantic_ids if vid in eligible)
                    if semantic_ids:
                        ordered_ids = merge_rankings_rrf(
                            lexical=lexical_ids,
                            semantic=semantic_ids,
                        )
                    else:
                        semantic_fallback = True
                except Exception:
                    semantic_fallback = True
                    ordered_ids = list(lexical_ids)

            page = ordered_ids[offset : offset + top_k]
            next_offset = offset + len(page)
            next_cursor: str | None = None
            if next_offset < len(ordered_ids):
                if len(self._cursors) >= self._max_cursors:
                    # Evict oldest insertion order (dict preserves order on 3.7+).
                    oldest = next(iter(self._cursors))
                    self._cursors.pop(oldest, None)
                cursor_id = self._token_factory()
                if not isinstance(cursor_id, str) or not cursor_id:
                    raise CatalogError("cursor_token_invalid")
                # Opaque: random ID only; no encoded query/data.
                self._cursors[cursor_id] = _CursorState(
                    cursor_id=cursor_id,
                    catalog_digest=self._snapshot.catalog_digest,
                    query_digest=query_digest,
                    locale=self._snapshot.locale,
                    next_offset=next_offset,
                    remaining_uses=self._cursor_ttl_searches,
                )
                next_cursor = cursor_id

            # Idempotent disclosed union for this page.
            for version_id in page:
                self._disclosed.add(version_id)

            self._search_count += 1
            hits = tuple(
                CatalogSearchHit(
                    version_id=version_id,
                    score=float(score_by_id.get(version_id, 0.0)),
                    rank=offset + index,
                )
                for index, version_id in enumerate(page)
            )
            return CatalogSearchResult(
                catalog_digest=self._snapshot.catalog_digest,
                query_digest=query_digest,
                hits=hits,
                next_cursor=next_cursor,
                excluded_count=self._snapshot.excluded_count,
                semantic_fallback=semantic_fallback,
                disclosed_version_ids=tuple(
                    sorted(self._disclosed, key=lambda item: item.bytes)
                ),
            )


# ---------------------------------------------------------------------------
# Activation recheck helpers (catalog_changed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LivePackageRecheck:
    package_id: UUID
    catalog_enabled: bool
    published_version_id: UUID | None
    version_digest: str | None


def recheck_candidates_for_activation(
    *,
    snapshot: SkillCatalogSnapshot,
    version_ids: Sequence[UUID],
    live: Sequence[LivePackageRecheck],
) -> None:
    """Fail closed with catalog_changed if live aggregate pointers drifted.

    Locks are the caller's responsibility (sorted package-ID order). This pure
    helper only compares snapshot expectations to live projections.
    """
    live_by_package = {item.package_id: item for item in live}
    for version_id in version_ids:
        record = snapshot.get_by_version_id(version_id)
        if record is None:
            raise CatalogError(SKILL_NOT_CATALOGED)
        live_row = live_by_package.get(record.package_id)
        if live_row is None:
            raise CatalogError(CATALOG_CHANGED)
        if not live_row.catalog_enabled:
            raise CatalogError(CATALOG_CHANGED)
        if live_row.published_version_id != record.version_id:
            raise CatalogError(CATALOG_CHANGED)
        if live_row.version_digest != record.version_digest:
            raise CatalogError(CATALOG_CHANGED)


__all__ = [
    "CATALOG_CHANGED",
    "CATALOG_CURSOR_INVALID",
    "CATALOG_UNAVAILABLE",
    "CatalogCandidateProjection",
    "CatalogError",
    "CatalogSearchHit",
    "CatalogSearchResult",
    "CatalogSearchState",
    "CatalogSemanticRecallPort",
    "DEFAULT_MAX_SINGLE_SKILL_INSTRUCTION_CHARS",
    "DEFAULT_TOP_K",
    "EXCLUDE_HARD_COVERAGE",
    "FIELD_WEIGHTS",
    "LivePackageRecheck",
    "MAX_TOP_K",
    "RRF_K",
    "SKILL_NOT_CATALOGED",
    "SKILL_NOT_DISCLOSED",
    "SKILL_POLICY_UNSUPPORTED",
    "SKILL_VERSION_CHANGED",
    "SkillCatalogRecord",
    "SkillCatalogSnapshot",
    "build_catalog_snapshot",
    "compute_catalog_digest",
    "compute_profile_scope_digest",
    "compute_query_digest",
    "evaluate_candidate_eligibility",
    "merge_rankings_rrf",
    "normalize_catalog_text",
    "rank_records_lexical",
    "recheck_candidates_for_activation",
    "score_record_against_query",
    "tokenize_catalog_text",
]
