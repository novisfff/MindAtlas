"""Tests for immutable per-Run Skill Catalog and lexical recall (Plan 04 Task 4)."""

from __future__ import annotations

import concurrent.futures
import copy
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.main_agent.catalog import (  # noqa: E402
    CATALOG_CHANGED,
    CATALOG_CURSOR_INVALID,
    DEFAULT_TOP_K,
    FIELD_WEIGHTS,
    MAX_TOP_K,
    SKILL_NOT_CATALOGED,
    SKILL_POLICY_UNSUPPORTED,
    CatalogCandidateProjection,
    CatalogError,
    CatalogSearchState,
    LivePackageRecheck,
    SkillCatalogRecord,
    SkillCatalogSnapshot,
    build_catalog_snapshot,
    compute_catalog_digest,
    compute_profile_scope_digest,
    evaluate_candidate_eligibility,
    merge_rankings_rrf,
    normalize_catalog_text,
    rank_records_lexical,
    recheck_candidates_for_activation,
    score_record_against_query,
    tokenize_catalog_text,
)
from app.assistant.skills.schemas import SkillCatalogScopeV1  # noqa: E402

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64

PKG_1 = UUID("00000000-0000-4000-8000-000000000001")
PKG_2 = UUID("00000000-0000-4000-8000-000000000002")
PKG_3 = UUID("00000000-0000-4000-8000-000000000003")
VER_1 = UUID("00000000-0000-4000-8000-000000000011")
VER_2 = UUID("00000000-0000-4000-8000-000000000012")
VER_3 = UUID("00000000-0000-4000-8000-000000000013")


def _candidate(**overrides: Any) -> CatalogCandidateProjection:
    base = dict(
        package_id=PKG_1,
        version_id=VER_1,
        canonical_name="weekly-review",
        display_name="Weekly Review",
        description="Review MindAtlas entries over a time range",
        locale="en",
        aliases=("weekly review", "周回顾"),
        include_examples=("summarize this week",),
        exclude_examples=("delete entries",),
        content_digest=DIGEST_A,
        version_digest=DIGEST_B,
        resource_index_digest=DIGEST_C,
        binding_set_digest=DIGEST_D,
        version_source="publish",
        catalog_enabled=True,
        conflict_rules=(),
        instruction_char_count=120,
        bindings_eligible=True,
        resource_index_verified=True,
        binding_set_verified=True,
        ownership_verified=True,
        entrypoint_compatible=True,
        locale_compatible=True,
    )
    base.update(overrides)
    return CatalogCandidateProjection(**base)


def _scope_all() -> SkillCatalogScopeV1:
    return SkillCatalogScopeV1(mode="all_published", package_ids=())


# ---------------------------------------------------------------------------
# Normalization / tokenizer fixed vectors
# ---------------------------------------------------------------------------


def test_normalize_nfkc_casefold_trim() -> None:
    # Fullwidth Latin + trailing spaces + case
    assert normalize_catalog_text("  Ｗｅｅｋｌｙ  ") == "weekly"
    assert normalize_catalog_text("Weekly-Review") == "weekly-review"


def test_normalize_rejects_controls() -> None:
    with pytest.raises(ValueError):
        normalize_catalog_text("bad\x00name")
    with pytest.raises(ValueError):
        normalize_catalog_text("bad\x07name")


def test_tokenize_ascii_and_cjk() -> None:
    tokens = tokenize_catalog_text("Weekly Review 周回顾")
    assert "weekly" in tokens
    assert "review" in tokens
    assert "周" in tokens
    assert "回" in tokens
    assert "顾" in tokens
    # adjacent CJK bigrams
    assert "周回" in tokens
    assert "回顾" in tokens


def test_tokenize_dedup_not_required_but_stable() -> None:
    # Multiset preserves frequency; ranker uses counts.
    tokens = tokenize_catalog_text("foo foo bar")
    assert tokens.count("foo") == 2
    assert tokens.count("bar") == 1


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_eligible_instruction_only_skill() -> None:
    # No bindings is fine when bindings_eligible stays True (instruction-only).
    reason = evaluate_candidate_eligibility(
        _candidate(bindings_eligible=True, instruction_char_count=10),
        scope=_scope_all(),
    )
    assert reason is None


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"catalog_enabled": False}, "catalog_disabled"),
        ({"version_source": "save"}, "version_not_published"),
        ({"ownership_verified": False}, "ownership_unverified"),
        ({"resource_index_verified": False}, "resource_index_unverified"),
        ({"binding_set_verified": False}, "binding_set_unverified"),
        ({"bindings_eligible": False}, "bindings_ineligible"),
        ({"conflict_rules": ({"kind": "excludes", "target_skill": "x"},)}, SKILL_POLICY_UNSUPPORTED),
        ({"entrypoint_compatible": False}, "entrypoint_incompatible"),
        ({"locale_compatible": False}, "locale_incompatible"),
        ({"instruction_char_count": 20_000}, "instruction_limit_exceeded"),
        ({"content_digest": "notadigest"}, "digest_invalid"),
    ],
)
def test_eligibility_filters(overrides: dict[str, Any], expected: str) -> None:
    reason = evaluate_candidate_eligibility(_candidate(**overrides), scope=_scope_all())
    assert reason == expected


def test_allowlist_scope_excludes_other_packages() -> None:
    scope = SkillCatalogScopeV1(mode="allowlist", package_ids=(PKG_2,))
    reason = evaluate_candidate_eligibility(_candidate(package_id=PKG_1), scope=scope)
    assert reason == "out_of_scope"
    reason_ok = evaluate_candidate_eligibility(_candidate(package_id=PKG_2), scope=scope)
    assert reason_ok is None


def test_build_snapshot_sorts_and_digests() -> None:
    c1 = _candidate(
        package_id=PKG_2,
        version_id=VER_2,
        canonical_name="zeta-skill",
        content_digest=DIGEST_E,
        version_digest=DIGEST_F,
    )
    c2 = _candidate(
        package_id=PKG_1,
        version_id=VER_1,
        canonical_name="alpha-skill",
    )
    snap = build_catalog_snapshot([c1, c2], scope=_scope_all(), locale="en")
    assert [r.canonical_name for r in snap.records] == ["alpha-skill", "zeta-skill"]
    assert snap.catalog_digest == compute_catalog_digest(
        records=snap.records,
        profile_scope_digest=snap.profile_scope_digest,
    )
    assert snap.profile_scope_digest == compute_profile_scope_digest(_scope_all())
    # No Session / ORM retained — only frozen records.
    assert isinstance(snap.records[0], SkillCatalogRecord)


def test_snapshot_rejects_unsorted_records() -> None:
    r1 = SkillCatalogRecord(
        package_id=PKG_1,
        version_id=VER_1,
        canonical_name="b-skill",
        description="b",
        content_digest=DIGEST_A,
        version_digest=DIGEST_B,
        resource_index_digest=DIGEST_C,
        binding_set_digest=DIGEST_D,
    )
    r2 = SkillCatalogRecord(
        package_id=PKG_2,
        version_id=VER_2,
        canonical_name="a-skill",
        description="a",
        content_digest=DIGEST_A,
        version_digest=DIGEST_B,
        resource_index_digest=DIGEST_C,
        binding_set_digest=DIGEST_D,
    )
    with pytest.raises(ValidationError):
        SkillCatalogSnapshot(
            catalog_digest=DIGEST_A,
            profile_scope_digest=DIGEST_B,
            locale="en",
            records=(r1, r2),
        )


def test_snapshot_has_no_resource_body_fields() -> None:
    snap = build_catalog_snapshot([_candidate()], scope=_scope_all())
    payload = snap.model_dump()
    blob = str(payload)
    assert "skill_md" not in blob
    assert "instructions" not in blob
    assert "content" not in blob or "content_digest" in blob


# ---------------------------------------------------------------------------
# Lexical ranking fixed vectors
# ---------------------------------------------------------------------------


def test_field_weights_locked() -> None:
    assert FIELD_WEIGHTS == {
        "canonical_name": 10.0,
        "aliases": 8.0,
        "include_examples": 6.0,
        "description": 4.0,
        "display_name": 3.0,
    }


def test_canonical_name_outranks_description() -> None:
    name_hit = _candidate(
        package_id=PKG_1,
        version_id=VER_1,
        canonical_name="weekly-review",
        description="unrelated text",
    )
    desc_hit = _candidate(
        package_id=PKG_2,
        version_id=VER_2,
        canonical_name="other-skill",
        description="weekly review helper",
    )
    snap = build_catalog_snapshot([name_hit, desc_hit], scope=_scope_all())
    ranked = rank_records_lexical(snap.records, query="weekly review")
    assert ranked[0][0].canonical_name == "weekly-review"
    assert ranked[0][1] > ranked[1][1]


def test_tie_break_name_then_version_uuid() -> None:
    # Same score via identical description token overlap; different names.
    low_name = _candidate(
        package_id=PKG_1,
        version_id=VER_1,
        canonical_name="aaa-skill",
        description="shared-token",
        aliases=(),
        include_examples=(),
        display_name=None,
    )
    high_name = _candidate(
        package_id=PKG_2,
        version_id=VER_2,
        canonical_name="zzz-skill",
        description="shared-token",
        aliases=(),
        include_examples=(),
        display_name=None,
    )
    snap = build_catalog_snapshot([high_name, low_name], scope=_scope_all())
    ranked = rank_records_lexical(snap.records, query="shared-token")
    assert [r.canonical_name for r, _ in ranked] == ["aaa-skill", "zzz-skill"]


def test_exclude_exact_match_hard() -> None:
    cand = _candidate(exclude_examples=("delete entries now",))
    snap = build_catalog_snapshot([cand], scope=_scope_all())
    score = score_record_against_query(snap.records[0], query="delete entries now")
    assert score is None


def test_exclude_high_coverage_hard() -> None:
    cand = _candidate(exclude_examples=("delete all entries permanently",))
    snap = build_catalog_snapshot([cand], scope=_scope_all())
    # Query shares >= 80% tokens with exclude example and >= 2 nontrivial tokens.
    score = score_record_against_query(
        snap.records[0],
        query="delete all entries permanently please",
    )
    assert score is None


def test_exclude_soft_penalty_not_hard() -> None:
    cand = _candidate(
        canonical_name="entry-helper",
        description="manage entries safely",
        exclude_examples=("delete entries",),
        aliases=(),
        include_examples=(),
    )
    snap = build_catalog_snapshot([cand], scope=_scope_all())
    score = score_record_against_query(snap.records[0], query="manage entries")
    assert score is not None
    # Soft penalty applied when partial exclude overlap exists.
    score_no_exclude = score_record_against_query(
        snap.records[0].model_copy(update={"exclude_examples": ()}),
        query="manage entries",
    )
    assert score_no_exclude is not None
    assert score < score_no_exclude


def test_catalog_digest_stable_against_source_mutation() -> None:
    candidates = [
        _candidate(package_id=PKG_1, version_id=VER_1, canonical_name="alpha"),
        _candidate(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="beta",
            content_digest=DIGEST_E,
            version_digest=DIGEST_F,
        ),
    ]
    snap = build_catalog_snapshot(candidates, scope=_scope_all())
    digest_before = snap.catalog_digest
    # Mutating input list must not affect frozen snapshot.
    candidates.clear()
    assert snap.catalog_digest == digest_before
    rebuilt = compute_catalog_digest(
        records=snap.records,
        profile_scope_digest=snap.profile_scope_digest,
    )
    assert rebuilt == digest_before


def test_insertion_order_does_not_affect_ranking() -> None:
    c_a = _candidate(package_id=PKG_1, version_id=VER_1, canonical_name="alpha-review", description="review")
    c_b = _candidate(
        package_id=PKG_2,
        version_id=VER_2,
        canonical_name="beta-review",
        description="review",
        content_digest=DIGEST_E,
        version_digest=DIGEST_F,
    )
    snap1 = build_catalog_snapshot([c_a, c_b], scope=_scope_all())
    snap2 = build_catalog_snapshot([c_b, c_a], scope=_scope_all())
    r1 = [r.version_id for r, _ in rank_records_lexical(snap1.records, query="review")]
    r2 = [r.version_id for r, _ in rank_records_lexical(snap2.records, query="review")]
    assert r1 == r2
    assert snap1.catalog_digest == snap2.catalog_digest


# ---------------------------------------------------------------------------
# Search state: disclosed versions + cursors + concurrency
# ---------------------------------------------------------------------------


def _two_skill_snapshot() -> SkillCatalogSnapshot:
    return build_catalog_snapshot(
        [
            _candidate(
                package_id=PKG_1,
                version_id=VER_1,
                canonical_name="alpha-review",
                description="weekly review alpha",
            ),
            _candidate(
                package_id=PKG_2,
                version_id=VER_2,
                canonical_name="beta-review",
                description="weekly review beta",
                content_digest=DIGEST_E,
                version_digest=DIGEST_F,
                aliases=("beta",),
            ),
            _candidate(
                package_id=PKG_3,
                version_id=VER_3,
                canonical_name="gamma-other",
                description="unrelated skill",
                content_digest="1" * 64,
                version_digest="2" * 64,
                resource_index_digest="3" * 64,
                binding_set_digest="4" * 64,
            ),
        ],
        scope=_scope_all(),
        locale="en",
    )


def test_search_discloses_returned_versions() -> None:
    state = CatalogSearchState(_two_skill_snapshot(), default_top_k=2)
    result = state.search(query="weekly review", limit=2)
    assert len(result.hits) == 2
    for hit in result.hits:
        assert state.is_disclosed(hit.version_id)
    assert set(result.disclosed_version_ids) >= {h.version_id for h in result.hits}


def test_search_cursor_pagination_deterministic() -> None:
    tokens = iter([f"cursor-{i:02d}" for i in range(10)])
    state = CatalogSearchState(
        _two_skill_snapshot(),
        token_factory=lambda: next(tokens),
        default_top_k=1,
    )
    page1 = state.search(query="review", limit=1)
    assert page1.next_cursor == "cursor-00"
    assert len(page1.hits) == 1
    page2 = state.search(query="review", limit=1, cursor=page1.next_cursor)
    assert page2.hits[0].version_id != page1.hits[0].version_id
    # Cursor values carry no encoded query.
    assert "review" not in (page1.next_cursor or "")


def test_invalid_cursor_fails() -> None:
    state = CatalogSearchState(_two_skill_snapshot())
    with pytest.raises(CatalogError) as exc:
        state.search(query="review", cursor="missing")
    assert exc.value.reason_code == CATALOG_CURSOR_INVALID


def test_cursor_query_mismatch_fails() -> None:
    tokens = iter(["c1", "c2"])
    state = CatalogSearchState(
        _two_skill_snapshot(),
        token_factory=lambda: next(tokens),
        default_top_k=1,
    )
    page1 = state.search(query="review", limit=1)
    with pytest.raises(CatalogError) as exc:
        state.search(query="other-query", limit=1, cursor=page1.next_cursor)
    assert exc.value.reason_code == CATALOG_CURSOR_INVALID


def test_limit_bounds() -> None:
    state = CatalogSearchState(_two_skill_snapshot())
    with pytest.raises(CatalogError):
        state.search(query="review", limit=0)
    with pytest.raises(CatalogError):
        state.search(query="review", limit=MAX_TOP_K + 1)
    ok = state.search(query="review", limit=DEFAULT_TOP_K)
    assert ok.hits is not None


def test_overlapping_searches_preserve_disclosed_and_cursors() -> None:
    """Force concurrent skill.search-like calls; no lost disclosed IDs/cursors."""
    snap = _two_skill_snapshot()
    counter = {"n": 0}
    lock = __import__("threading").Lock()

    def token() -> str:
        with lock:
            counter["n"] += 1
            return f"tok-{counter['n']}"

    state = CatalogSearchState(snap, token_factory=token, default_top_k=1)

    def worker(query: str) -> Any:
        return state.search(query=query, limit=1)

    queries = ["review", "weekly", "alpha", "beta", "review", "weekly"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(worker, queries))

    disclosed = state.disclosed_version_ids()
    # Every returned hit must be disclosed.
    for result in results:
        for hit in result.hits:
            assert hit.version_id in disclosed
        # Cursor IDs are unique when present.
        if result.next_cursor is not None:
            assert result.next_cursor.startswith("tok-")

    # Completion order does not change a given call's hit set for same query.
    review_hits = [
        tuple(h.version_id for h in r.hits)
        for r, q in zip(results, queries, strict=True)
        if q == "review"
    ]
    assert len(set(review_hits)) == 1


def test_rrf_merge_deterministic() -> None:
    a, b, c = VER_1, VER_2, VER_3
    merged = merge_rankings_rrf(lexical=[a, b, c], semantic=[c, a])
    # Same inputs always same order.
    assert merged == merge_rankings_rrf(lexical=[a, b, c], semantic=[c, a])
    assert set(merged) == {a, b, c}


def test_semantic_failure_falls_back_to_lexical() -> None:
    class BoomPort:
        def rank(self, **kwargs: Any) -> tuple[UUID, ...]:
            raise RuntimeError("lightrag down")

    state = CatalogSearchState(
        _two_skill_snapshot(),
        semantic_port=BoomPort(),
        default_top_k=2,
    )
    result = state.search(query="weekly review", limit=2)
    assert result.semantic_fallback is True
    assert len(result.hits) == 2


def test_provider_summaries_omit_sensitive_fields() -> None:
    snap = _two_skill_snapshot()
    state = CatalogSearchState(snap)
    result = state.search(query="review", limit=2)
    summaries = snap.provider_summaries(
        version_ids=[h.version_id for h in result.hits],
        ranks={h.version_id: h.rank for h in result.hits},
    )
    payload = [s.model_dump() for s in summaries]
    blob = str(payload)
    assert "package_id" not in blob
    assert "aliases" not in blob
    assert "include_examples" not in blob
    assert "skill_md" not in blob


# ---------------------------------------------------------------------------
# Activation recheck (catalog_changed)
# ---------------------------------------------------------------------------


def test_recheck_detects_flag_and_pointer_drift() -> None:
    snap = build_catalog_snapshot([_candidate()], scope=_scope_all())
    # Happy path.
    recheck_candidates_for_activation(
        snapshot=snap,
        version_ids=[VER_1],
        live=[
            LivePackageRecheck(
                package_id=PKG_1,
                catalog_enabled=True,
                published_version_id=VER_1,
                version_digest=DIGEST_B,
            )
        ],
    )
    # Disabled flag.
    with pytest.raises(CatalogError) as exc:
        recheck_candidates_for_activation(
            snapshot=snap,
            version_ids=[VER_1],
            live=[
                LivePackageRecheck(
                    package_id=PKG_1,
                    catalog_enabled=False,
                    published_version_id=VER_1,
                    version_digest=DIGEST_B,
                )
            ],
        )
    assert exc.value.reason_code == CATALOG_CHANGED

    # Published pointer moved to a newer version — never silently select it.
    with pytest.raises(CatalogError) as exc2:
        recheck_candidates_for_activation(
            snapshot=snap,
            version_ids=[VER_1],
            live=[
                LivePackageRecheck(
                    package_id=PKG_1,
                    catalog_enabled=True,
                    published_version_id=VER_2,
                    version_digest=DIGEST_B,
                )
            ],
        )
    assert exc2.value.reason_code == CATALOG_CHANGED

    # Version digest drift.
    with pytest.raises(CatalogError) as exc3:
        recheck_candidates_for_activation(
            snapshot=snap,
            version_ids=[VER_1],
            live=[
                LivePackageRecheck(
                    package_id=PKG_1,
                    catalog_enabled=True,
                    published_version_id=VER_1,
                    version_digest=DIGEST_F,
                )
            ],
        )
    assert exc3.value.reason_code == CATALOG_CHANGED


def test_recheck_unknown_version_not_cataloged() -> None:
    snap = build_catalog_snapshot([_candidate()], scope=_scope_all())
    with pytest.raises(CatalogError) as exc:
        recheck_candidates_for_activation(
            snapshot=snap,
            version_ids=[VER_2],
            live=[],
        )
    assert exc.value.reason_code == SKILL_NOT_CATALOGED


def test_name_or_alias_lookup() -> None:
    snap = _two_skill_snapshot()
    assert snap.get_by_name_or_alias("beta-review") is not None
    assert snap.get_by_name_or_alias("beta") is not None
    assert snap.get_by_name_or_alias("missing") is None


def test_mark_disclosed_idempotent() -> None:
    state = CatalogSearchState(_two_skill_snapshot())
    state.mark_disclosed([VER_1, VER_1, VER_2])
    first = state.disclosed_version_ids()
    state.mark_disclosed([VER_2])
    assert state.disclosed_version_ids() == first


def test_scale_10k_records_no_resource_load() -> None:
    """Bounded scale: 10k pure projections, no resource body field present."""
    candidates: list[CatalogCandidateProjection] = []
    for i in range(10_000):
        pkg = UUID(int=i + 1)
        ver = UUID(int=i + 100_000)
        digest = sha256_canonical_json({"i": i})
        candidates.append(
            _candidate(
                package_id=pkg,
                version_id=ver,
                canonical_name=f"skill-{i:05d}",
                description=f"description for skill {i}",
                content_digest=digest,
                version_digest=digest,
                resource_index_digest=digest,
                binding_set_digest=digest,
                aliases=(),
                include_examples=(),
                exclude_examples=(),
            )
        )
    snap = build_catalog_snapshot(candidates, scope=_scope_all())
    assert len(snap.records) == 10_000
    ranked = rank_records_lexical(snap.records, query="skill-05000")
    assert ranked[0][0].canonical_name == "skill-05000"
    # Ensure snapshot dump has no body-like keys.
    sample = snap.records[0].model_dump()
    assert set(sample.keys()) >= {
        "package_id",
        "version_id",
        "canonical_name",
        "content_digest",
        "version_digest",
    }
    assert "skill_md" not in sample
    assert "body" not in sample
