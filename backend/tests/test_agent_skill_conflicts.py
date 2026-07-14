"""Plan 05 Task 1: symmetric SkillConflictRuleV1 evaluation (Plan 01 import only)."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.policy.conflicts import (  # noqa: E402
    SkillConflictIdentity,
    SkillConflictParticipant,
    build_catalog_name_index,
    conflicts_symmetric,
    evaluate_skill_conflicts,
    resolve_target_skill,
)
from app.assistant.skills.contracts import SkillConflictRuleV1  # noqa: E402

VERSION_A = UUID("00000000-0000-4000-8000-0000000000a1")
VERSION_B = UUID("00000000-0000-4000-8000-0000000000b2")
VERSION_C = UUID("00000000-0000-4000-8000-0000000000c3")
PACKAGE_A = UUID("00000000-0000-4000-8000-0000000000aa")
PACKAGE_B = UUID("00000000-0000-4000-8000-0000000000bb")
PACKAGE_C = UUID("00000000-0000-4000-8000-0000000000cc")


def _identity(
    name: str,
    version_id: UUID,
    *,
    package_id: UUID | None = None,
    aliases: tuple[str, ...] = (),
) -> SkillConflictIdentity:
    return SkillConflictIdentity(
        canonical_name=name,
        version_id=version_id,
        package_id=package_id,
        aliases=aliases,
    )


def _participant(
    name: str,
    version_id: UUID,
    rules: tuple[SkillConflictRuleV1, ...],
    *,
    role: str = "candidate",
    package_id: UUID | None = None,
    aliases: tuple[str, ...] = (),
) -> SkillConflictParticipant:
    return SkillConflictParticipant(
        identity=_identity(name, version_id, package_id=package_id, aliases=aliases),
        conflict_rules=rules,
        role=role,  # type: ignore[arg-type]
    )


def test_plan01_conflict_rule_fixed_vectors() -> None:
    """Import Plan 01 SkillConflictRuleV1 — exact shape, no second dialect."""
    excludes = SkillConflictRuleV1(kind="excludes", target_skill="other-skill")
    requires = SkillConflictRuleV1(kind="requires", target_skill="base-skill")
    group = SkillConflictRuleV1(kind="exclusive_group", group="review-family")
    assert excludes.kind == "excludes"
    assert excludes.target_skill == "other-skill"
    assert excludes.group is None
    assert requires.kind == "requires"
    assert group.kind == "exclusive_group"
    assert group.group == "review-family"
    assert group.target_skill is None

    with pytest.raises(ValidationError):
        SkillConflictRuleV1(kind="excludes")
    with pytest.raises(ValidationError):
        SkillConflictRuleV1(kind="exclusive_group", target_skill="x", group="g")
    with pytest.raises(ValidationError):
        SkillConflictRuleV1(kind="requires", target_skill="x", group="g")


def test_excludes_same_batch_fails_symmetrically() -> None:
    left = _participant(
        "alpha",
        VERSION_A,
        (SkillConflictRuleV1(kind="excludes", target_skill="beta"),),
        package_id=PACKAGE_A,
    )
    right = _participant(
        "beta",
        VERSION_B,
        (),
        package_id=PACKAGE_B,
    )
    result = evaluate_skill_conflicts(active=(), candidates=(left, right))
    assert result.allowed is False
    assert result.reason_code == "skill_conflict_excludes"

    # Reverse declaration order — still fails (symmetric).
    reverse = evaluate_skill_conflicts(active=(), candidates=(right, left))
    assert reverse.allowed is False
    assert reverse.reason_code == "skill_conflict_excludes"

    # Existing rule excludes candidate.
    active = _participant(
        "alpha",
        VERSION_A,
        (SkillConflictRuleV1(kind="excludes", target_skill="beta"),),
        role="active",
        package_id=PACKAGE_A,
    )
    candidate = _participant(
        "beta",
        VERSION_B,
        (),
        package_id=PACKAGE_B,
    )
    from_active = evaluate_skill_conflicts(active=(active,), candidates=(candidate,))
    assert from_active.allowed is False
    assert from_active.reason_code == "skill_conflict_excludes"

    # Candidate excludes active.
    active2 = _participant("alpha", VERSION_A, (), role="active", package_id=PACKAGE_A)
    candidate2 = _participant(
        "beta",
        VERSION_B,
        (SkillConflictRuleV1(kind="excludes", target_skill="alpha"),),
        package_id=PACKAGE_B,
    )
    from_candidate = evaluate_skill_conflicts(active=(active2,), candidates=(candidate2,))
    assert from_candidate.allowed is False
    assert from_candidate.reason_code == "skill_conflict_excludes"


def test_requires_missing_target_fails_without_auto_inject() -> None:
    candidate = _participant(
        "weekly-review",
        VERSION_A,
        (SkillConflictRuleV1(kind="requires", target_skill="base-skill"),),
        package_id=PACKAGE_A,
    )
    # Catalog knows base-skill but it is not active/candidate.
    catalog = (
        _identity("weekly-review", VERSION_A, package_id=PACKAGE_A),
        _identity("base-skill", VERSION_B, package_id=PACKAGE_B),
    )
    result = evaluate_skill_conflicts(
        active=(),
        candidates=(candidate,),
        catalog_skills=catalog,
    )
    assert result.allowed is False
    assert result.reason_code == "skill_conflict_requires"

    # Same batch satisfies requires.
    base = _participant("base-skill", VERSION_B, (), package_id=PACKAGE_B)
    ok = evaluate_skill_conflicts(
        active=(),
        candidates=(candidate, base),
        catalog_skills=catalog,
    )
    assert ok.allowed is True

    # Already active satisfies requires.
    ok_active = evaluate_skill_conflicts(
        active=(_participant("base-skill", VERSION_B, (), role="active", package_id=PACKAGE_B),),
        candidates=(candidate,),
        catalog_skills=catalog,
    )
    assert ok_active.allowed is True


def test_exclusive_group_at_most_one() -> None:
    left = _participant(
        "review-a",
        VERSION_A,
        (SkillConflictRuleV1(kind="exclusive_group", group="review-family"),),
        package_id=PACKAGE_A,
    )
    right = _participant(
        "review-b",
        VERSION_B,
        (SkillConflictRuleV1(kind="exclusive_group", group="review-family"),),
        package_id=PACKAGE_B,
    )
    result = evaluate_skill_conflicts(active=(), candidates=(left, right))
    assert result.allowed is False
    assert result.reason_code == "skill_conflict_exclusive_group"

    # Order independent.
    result_rev = evaluate_skill_conflicts(active=(), candidates=(right, left))
    assert result_rev.allowed is False

    # Active + candidate same group fails.
    active = _participant(
        "review-a",
        VERSION_A,
        (SkillConflictRuleV1(kind="exclusive_group", group="review-family"),),
        role="active",
        package_id=PACKAGE_A,
    )
    candidate = _participant(
        "review-b",
        VERSION_B,
        (SkillConflictRuleV1(kind="exclusive_group", group="review-family"),),
        package_id=PACKAGE_B,
    )
    assert (
        evaluate_skill_conflicts(active=(active,), candidates=(candidate,)).allowed
        is False
    )

    # Different groups ok.
    other = _participant(
        "review-c",
        VERSION_C,
        (SkillConflictRuleV1(kind="exclusive_group", group="other-family"),),
        package_id=PACKAGE_C,
    )
    ok = evaluate_skill_conflicts(active=(active,), candidates=(other,))
    assert ok.allowed is True


def test_unresolved_and_self_target_fail_closed() -> None:
    candidate = _participant(
        "alpha",
        VERSION_A,
        (SkillConflictRuleV1(kind="excludes", target_skill="unknown-skill"),),
        package_id=PACKAGE_A,
    )
    result = evaluate_skill_conflicts(active=(), candidates=(candidate,))
    assert result.allowed is False
    assert result.reason_code == "skill_conflict_unresolved_target"

    self_target = _participant(
        "alpha",
        VERSION_A,
        (SkillConflictRuleV1(kind="excludes", target_skill="alpha"),),
        package_id=PACKAGE_A,
    )
    self_result = evaluate_skill_conflicts(active=(), candidates=(self_target,))
    assert self_result.allowed is False
    assert self_result.reason_code == "skill_conflict_self_target"


def test_alias_resolution_through_catalog() -> None:
    catalog = (
        _identity("canonical-beta", VERSION_B, package_id=PACKAGE_B, aliases=("beta-alias",)),
        _identity("alpha", VERSION_A, package_id=PACKAGE_A),
    )
    index = build_catalog_name_index(catalog)
    resolved = resolve_target_skill("beta-alias", catalog=index)
    assert resolved.canonical_name == "canonical-beta"
    assert resolved.version_id == VERSION_B

    candidate = _participant(
        "alpha",
        VERSION_A,
        (SkillConflictRuleV1(kind="excludes", target_skill="beta-alias"),),
        package_id=PACKAGE_A,
    )
    other = _participant(
        "canonical-beta",
        VERSION_B,
        (),
        package_id=PACKAGE_B,
        aliases=("beta-alias",),
    )
    result = evaluate_skill_conflicts(
        active=(),
        candidates=(candidate, other),
        catalog_skills=catalog,
    )
    assert result.allowed is False
    assert result.reason_code == "skill_conflict_excludes"


def test_conflicts_symmetric_helper_order_independent() -> None:
    left = _participant(
        "alpha",
        VERSION_A,
        (SkillConflictRuleV1(kind="excludes", target_skill="beta"),),
        package_id=PACKAGE_A,
    )
    right = _participant("beta", VERSION_B, (), package_id=PACKAGE_B)
    assert conflicts_symmetric(left, right).allowed is False
    assert conflicts_symmetric(right, left).allowed is False


def test_no_rules_allows_coexistence() -> None:
    left = _participant("alpha", VERSION_A, (), package_id=PACKAGE_A)
    right = _participant("beta", VERSION_B, (), package_id=PACKAGE_B)
    result = evaluate_skill_conflicts(active=(), candidates=(left, right))
    assert result.allowed is True
    assert result.reason_code is None
