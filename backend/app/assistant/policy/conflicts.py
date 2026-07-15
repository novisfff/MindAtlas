"""Symmetric Skill conflict rule evaluation using Plan 01 SkillConflictRuleV1.

Import Plan 01's locked model only — no second dialect, translation layer, or
normalization drift.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.assistant.skills.contracts import SkillConflictRuleV1


class SkillConflictError(ValueError):
    """Raised when skill activation fails closed on conflict rules."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


ConflictReasonCode = Literal[
    "skill_conflict_excludes",
    "skill_conflict_requires",
    "skill_conflict_exclusive_group",
    "skill_conflict_unresolved_target",
    "skill_conflict_self_target",
    "skill_conflict_invalid_rule",
]


@dataclass(frozen=True, slots=True)
class SkillConflictIdentity:
    """Canonical skill identity for conflict evaluation."""

    canonical_name: str
    version_id: UUID
    package_id: UUID | None = None
    # Optional aliases that resolve to this identity in the Catalog snapshot.
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillConflictParticipant:
    """One active or candidate skill with its published conflict rules."""

    identity: SkillConflictIdentity
    conflict_rules: tuple[SkillConflictRuleV1, ...]
    role: Literal["active", "candidate"]


@dataclass(frozen=True, slots=True)
class SkillConflictEvaluationResult:
    allowed: bool
    reason_code: str | None = None
    detail: str | None = None


def _normalize_lookup_name(name: str) -> str:
    return name.strip().lower()


def build_catalog_name_index(
    skills: Sequence[SkillConflictIdentity],
) -> dict[str, SkillConflictIdentity]:
    """Map normalized canonical names and aliases to identities.

    Duplicate mappings to different identities fail closed.
    """
    index: dict[str, SkillConflictIdentity] = {}
    for skill in skills:
        keys = [_normalize_lookup_name(skill.canonical_name)]
        keys.extend(_normalize_lookup_name(alias) for alias in skill.aliases)
        for key in keys:
            if not key:
                continue
            prior = index.get(key)
            if prior is not None and prior.version_id != skill.version_id:
                raise SkillConflictError(
                    "skill_conflict_unresolved_target",
                    f"ambiguous catalog name {key!r}",
                )
            index[key] = skill
    return index


def resolve_target_skill(
    target_skill: str,
    *,
    catalog: Mapping[str, SkillConflictIdentity],
) -> SkillConflictIdentity:
    key = _normalize_lookup_name(target_skill)
    if not key:
        raise SkillConflictError(
            "skill_conflict_invalid_rule",
            "empty conflict target_skill",
        )
    resolved = catalog.get(key)
    if resolved is None:
        raise SkillConflictError(
            "skill_conflict_unresolved_target",
            f"unresolved conflict target {target_skill!r}",
        )
    return resolved


def evaluate_skill_conflicts(
    *,
    active: Sequence[SkillConflictParticipant],
    candidates: Sequence[SkillConflictParticipant],
    catalog_skills: Sequence[SkillConflictIdentity] | None = None,
) -> SkillConflictEvaluationResult:
    """Evaluate excludes/requires/exclusive_group symmetrically.

    Rules (Plan 05 §4.4):
    - excludes: fail if target active or in same batch (evaluate both sides)
    - requires: fail if target not already active and not in same batch
    - exclusive_group: at most one active Skill may claim the group
    - Names resolve through Catalog snapshot to canonical identities
    - Self-target, unknown, unresolved targets fail closed
    - Order-independent / symmetric
    """
    if catalog_skills is None:
        catalog_skills = tuple(
            participant.identity for participant in (*active, *candidates)
        )
    try:
        catalog = build_catalog_name_index(catalog_skills)
    except SkillConflictError as exc:
        return SkillConflictEvaluationResult(
            allowed=False,
            reason_code=exc.reason_code,
            detail=str(exc),
        )

    active_by_version = {item.identity.version_id: item for item in active}
    candidate_by_version = {item.identity.version_id: item for item in candidates}
    batch_by_version = {**active_by_version, **candidate_by_version}

    # Fail closed on self-target / invalid rules before semantic evaluation.
    for participant in (*active, *candidates):
        for rule in participant.conflict_rules:
            try:
                _validate_rule_shape(rule)
            except SkillConflictError as exc:
                return SkillConflictEvaluationResult(
                    allowed=False,
                    reason_code=exc.reason_code,
                    detail=str(exc),
                )
            if rule.kind in {"excludes", "requires"}:
                assert rule.target_skill is not None
                try:
                    target = resolve_target_skill(rule.target_skill, catalog=catalog)
                except SkillConflictError as exc:
                    return SkillConflictEvaluationResult(
                        allowed=False,
                        reason_code=exc.reason_code,
                        detail=str(exc),
                    )
                if target.version_id == participant.identity.version_id or (
                    _normalize_lookup_name(target.canonical_name)
                    == _normalize_lookup_name(participant.identity.canonical_name)
                ):
                    return SkillConflictEvaluationResult(
                        allowed=False,
                        reason_code="skill_conflict_self_target",
                        detail=(
                            f"{participant.identity.canonical_name} self-targets "
                            f"via {rule.kind}"
                        ),
                    )

    # exclusive_group: at most one skill among active+candidates may claim a group.
    group_claimants: dict[str, list[SkillConflictParticipant]] = {}
    for participant in (*active, *candidates):
        claimed: set[str] = set()
        for rule in participant.conflict_rules:
            if rule.kind != "exclusive_group":
                continue
            assert rule.group is not None
            group = rule.group.strip()
            if group in claimed:
                # Duplicate exclusive_group on same skill is ignored for counting.
                continue
            claimed.add(group)
            group_claimants.setdefault(group, []).append(participant)
    for group, claimants in group_claimants.items():
        # Unique by version_id
        unique: dict[UUID, SkillConflictParticipant] = {
            item.identity.version_id: item for item in claimants
        }
        if len(unique) > 1:
            names = sorted(item.identity.canonical_name for item in unique.values())
            return SkillConflictEvaluationResult(
                allowed=False,
                reason_code="skill_conflict_exclusive_group",
                detail=f"exclusive_group {group!r} claimed by {names}",
            )

    # excludes / requires — evaluate every participant's rules against the batch.
    for participant in (*active, *candidates):
        for rule in participant.conflict_rules:
            if rule.kind == "exclusive_group":
                continue
            assert rule.target_skill is not None
            try:
                target = resolve_target_skill(rule.target_skill, catalog=catalog)
            except SkillConflictError as exc:
                return SkillConflictEvaluationResult(
                    allowed=False,
                    reason_code=exc.reason_code,
                    detail=str(exc),
                )
            target_present = target.version_id in batch_by_version
            if rule.kind == "excludes":
                if target_present:
                    # Only fail when at least one of the pair is a candidate, or
                    # when both are active (should not occur if prior activation
                    # was validated). Always fail closed for same-batch conflicts.
                    return SkillConflictEvaluationResult(
                        allowed=False,
                        reason_code="skill_conflict_excludes",
                        detail=(
                            f"{participant.identity.canonical_name} excludes "
                            f"{target.canonical_name}"
                        ),
                    )
            elif rule.kind == "requires":
                if not target_present:
                    return SkillConflictEvaluationResult(
                        allowed=False,
                        reason_code="skill_conflict_requires",
                        detail=(
                            f"{participant.identity.canonical_name} requires "
                            f"{target.canonical_name}"
                        ),
                    )

    return SkillConflictEvaluationResult(allowed=True)


def _validate_rule_shape(rule: SkillConflictRuleV1) -> None:
    if rule.kind in {"excludes", "requires"}:
        if not rule.target_skill or rule.group is not None:
            raise SkillConflictError(
                "skill_conflict_invalid_rule",
                f"invalid {rule.kind} rule shape",
            )
    elif rule.kind == "exclusive_group":
        if not rule.group or not rule.group.strip() or rule.target_skill is not None:
            raise SkillConflictError(
                "skill_conflict_invalid_rule",
                "invalid exclusive_group rule shape",
            )
    else:  # pragma: no cover - Literal prevents this at type level
        raise SkillConflictError(
            "skill_conflict_invalid_rule",
            f"unknown conflict rule kind {rule.kind!r}",
        )


def conflicts_symmetric(
    left: SkillConflictParticipant,
    right: SkillConflictParticipant,
    *,
    catalog_skills: Sequence[SkillConflictIdentity] | None = None,
) -> SkillConflictEvaluationResult:
    """Evaluate two candidates as a same-batch pair (order-independent)."""
    return evaluate_skill_conflicts(
        active=(),
        candidates=(left, right),
        catalog_skills=catalog_skills,
    )


__all__ = [
    "ConflictReasonCode",
    "SkillConflictError",
    "SkillConflictEvaluationResult",
    "SkillConflictIdentity",
    "SkillConflictParticipant",
    "build_catalog_name_index",
    "conflicts_symmetric",
    "evaluate_skill_conflicts",
    "resolve_target_skill",
]
