"""Atomic skill activation and Manifest-effect lifecycle (Plan 04 + Plan 05 Task 6).

Gateway success stages a PendingSkillActivationPackage; Plan 03 lineage
validation + ManifestEffectLifecyclePort.accept installs it. Active membership
is solely the accepted Manifest's active_skills.

Plan 05 extends the pending package so policy/exposure/owner-budget/obligation
candidate state is computed pre-lineage and committed only inside accept under
one Run-state revision/lock. Discard removes the entire candidate package with
zero residue. skill.inject reservation accounting is independent of discard.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityMetrics,
    CapabilityResult,
    FrozenCapabilityBinding,
    completed_result,
    failed_result,
)
from app.assistant.domain.contracts import (
    ResolvedCapabilityRef,
    ResolvedRunManifestRevision,
    ResolvedSkillRef,
    SkillVersionConflictError,
    append_skill_activations_batch,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.main_agent.catalog import (
    SKILL_NOT_CATALOGED,
    SKILL_NOT_DISCLOSED,
    CatalogError,
    CatalogSearchState,
)
from app.assistant.main_agent.control_runtime import PendingManifestEffect
from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS
from app.assistant.policy.conflicts import (
    SkillConflictIdentity,
    SkillConflictParticipant,
    evaluate_skill_conflicts,
)
from app.assistant.policy.contracts import (
    OwnerBudgetLimits,
    normalize_owner_budget_limits,
)
from app.assistant.policy.obligations import (
    SkillTerminalSatisfiabilityView,
    evaluate_skill_terminal_satisfiability,
)
from app.assistant.skills.contracts import SkillConflictRuleV1

SKILL_CAPABILITY_CONFLICT = "skill_capability_conflict"
SKILL_ALREADY_ACTIVE = "skill_already_active"
SKILL_VERSION_CONFLICT = "skill_version_conflict"
SKILL_CONTEXT_BUDGET_EXCEEDED = "skill_context_budget_exceeded"
ACTIVE_SKILL_LIMIT_EXCEEDED = "active_skill_limit_exceeded"
CONTROL_EFFECT_PROTOCOL_ERROR = "control_effect_protocol_error"
DUPLICATE_CAPABILITY_POLICY_CONFLICT = "duplicate_capability_policy_conflict"
SKILL_COMPLETION_UNSATISFIABLE = "skill_completion_unsatisfiable"


@dataclass(frozen=True)
class CandidateExposureView:
    """Minimal exposure metadata for a candidate-owned Domain Key.

    Used for §4.3 strict duplicate compatibility when full descriptor/binding
    inputs are not available at the pure stage layer. Digests must match the
    existing Manifest capability for a compatible non-owning consumer.
    """

    domain_key: str
    resolved_ref: ResolvedCapabilityRef
    binding_contract_digest: str
    descriptor_digest: str = ""
    max_skill_calls: int | None = None
    max_same_read_calls: int | None = None
    requires_terminal_output: bool | None = None
    terminal_text_allowed: bool | None = None


@dataclass(frozen=True)
class SkillActivationCandidate:
    """Exact published Skill + bindings prepared for one inject batch item.

    Plan 05 policy fields are optional so Plan 04 tests remain byte-compatible.
    When present they drive conflict/duplicate/budget/obligation preflight.
    """

    skill: ResolvedSkillRef
    capabilities: tuple[ResolvedCapabilityRef, ...]
    frozen_bindings: tuple[FrozenCapabilityBinding, ...] = ()
    instruction_char_count: int = 0
    resource_index_digest: str = ""
    author_allowed_side_effects: tuple[str, ...] = ()
    # Plan 05 policy fields (optional; activation-time evaluation).
    conflict_rules: tuple[SkillConflictRuleV1, ...] = ()
    max_skill_calls: int | None = None
    max_same_read_calls: int | None = None
    requires_terminal_output: bool = False
    terminal_text_allowed: bool = True
    aliases: tuple[str, ...] = ()
    exposure_views: tuple[CandidateExposureView, ...] = ()
    # When True, this skill owns no Capability exposure (instruction-only).
    is_instruction_only: bool = False


@dataclass
class PendingSkillActivationPackage:
    """Call-scoped staged activation; not durable; single-use.

    Plan 05 candidate policy/budget/obligation deltas live here until accept.
    Current Manifest, ledgers, exposure, and public events remain unchanged
    until ManifestEffectLifecyclePort.accept commits under one lock.
    """

    call_id: str
    effect: PendingManifestEffect
    activated_version_ids: tuple[UUID, ...]
    noop_version_ids: tuple[UUID, ...]
    post_commit_events: tuple[dict[str, JsonValue], ...] = ()
    accepted: bool = False
    discarded: bool = False
    # Total instruction chars after acceptance (existing + newly activated).
    resulting_instruction_chars: int = 0
    # Chars contributed by this package's newly activated skills only.
    activated_instruction_chars: int = 0
    # Plan 05 candidate policy state (committed only on accept).
    candidate_effective_policy_digest: str | None = None
    candidate_owner_budget_limits: tuple[OwnerBudgetLimits, ...] = ()
    # (skill_version_id, terminal_text_allowed) for requires_terminal_output skills.
    candidate_skill_terminals: tuple[tuple[UUID, bool], ...] = ()
    # (domain_key, consumer_version_id) non-owning compatible consumers.
    candidate_compatible_consumers: tuple[tuple[str, UUID], ...] = ()
    candidate_exposure_index_digest: str | None = None
    package_digest: str | None = None
    # Snapshot of Run limits digest at stage time (must not change on accept).
    run_budget_limits_digest: str | None = None


@dataclass(frozen=True)
class SkillInjectionPolicyContext:
    """Optional frozen policy inputs for candidate construction (Plan 05).

    When omitted, stage_skill_injection preserves Plan 04 behavior (no policy
    digest rewrite, no owner buckets, no skill terminals).
    """

    # Cap owner max_calls by these Run totals; never mutate Run counters.
    run_max_total_capability_calls: int = 16
    run_max_same_read_signature: int = 3
    run_max_active_skills: int = 4
    # Remaining provider/finalization slots for terminal satisfiability.
    remaining_provider_slots: int = 8
    # Existing active skills' conflict rules keyed by version_id.
    active_conflict_rules: tuple[tuple[UUID, tuple[SkillConflictRuleV1, ...]], ...] = ()
    active_aliases: tuple[tuple[UUID, tuple[str, ...]], ...] = ()
    # Existing exposure compatibility views keyed for duplicate checks.
    existing_exposures: tuple[CandidateExposureView, ...] = ()
    # Optional precomputed candidate effective policy digest (caller-owned).
    # When None and candidates carry policy fields, a deterministic package
    # digest is derived from activated versions + owner limits + conflict state.
    effective_policy_digest: str | None = None
    # Catalog identities for conflict target resolution (optional).
    catalog_skills: tuple[SkillConflictIdentity, ...] = ()


class MainAgentManifestEffectLifecycle:
    """Lifecycle port that commits staged activation packages after lineage accept.

    Plan 05: under the same lock, advances Manifest pointer, optional policy
    snapshot pointer, owner budget limits, and skill terminal obligations as
    one revision. Safe success events become post-commit deliverables only.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packages: dict[str, PendingSkillActivationPackage] = {}
        self._current_manifest: ResolvedRunManifestRevision | None = None
        self._accepted_version_ids: set[UUID] = set()
        self._accepted_instruction_chars: int = 0
        self._instruction_chars_by_version: dict[UUID, int] = {}
        self._post_commit_sink: Callable[[dict[str, JsonValue]], None] | None = None
        self._event_failures: list[str] = []
        # Optional rebind hooks for auth factory / control runtime after accept.
        self._on_accept_hooks: list[Callable[[ResolvedRunManifestRevision], None]] = []
        # Plan 05 process-local policy ledgers (optional; closed over by Run).
        self._budget_ledger: Any | None = None
        self._obligation_ledger: Any | None = None
        self._policy_snapshot: Any | None = None
        self._policy_snapshot_by_digest: dict[str, Any] = {}
        # Compatible consumers committed with the last accept (for tests).
        self._compatible_consumers: dict[str, set[UUID]] = {}
        # Owner budget digests applied (version_id -> digest).
        self._applied_owner_budget_digests: dict[UUID, str] = {}
        # Skill terminals created (version_id -> terminal_text_allowed).
        self._applied_skill_terminals: dict[UUID, bool] = {}

    def bind_policy_ledgers(
        self,
        *,
        budget_ledger: Any | None = None,
        obligation_ledger: Any | None = None,
        policy_snapshot: Any | None = None,
    ) -> None:
        """Attach process-local Budget/Obligation ledgers for atomic accept."""
        with self._lock:
            self._budget_ledger = budget_ledger
            self._obligation_ledger = obligation_ledger
            self._policy_snapshot = policy_snapshot

    def register_policy_snapshot(self, snapshot: Any) -> None:
        """Register a candidate EffectiveRunPolicySnapshot by its digest."""
        digest = getattr(snapshot, "effective_policy_digest", None)
        if not isinstance(digest, str) or not digest:
            raise ValueError("policy snapshot missing effective_policy_digest")
        with self._lock:
            self._policy_snapshot_by_digest[digest] = snapshot

    def set_event_sink(self, sink: Callable[[dict[str, JsonValue]], None] | None) -> None:
        self._post_commit_sink = sink

    def add_on_accept_hook(
        self, hook: Callable[[ResolvedRunManifestRevision], None]
    ) -> None:
        """Register a callback invoked with the accepted Manifest after commit."""
        self._on_accept_hooks.append(hook)

    def bind_current_manifest(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        instruction_chars_by_version: dict[UUID, int] | None = None,
    ) -> None:
        with self._lock:
            self._current_manifest = manifest
            self._accepted_version_ids = {item.version_id for item in manifest.active_skills}
            if instruction_chars_by_version is not None:
                self._instruction_chars_by_version = {
                    vid: max(0, int(n))
                    for vid, n in instruction_chars_by_version.items()
                    if vid in self._accepted_version_ids
                }
            else:
                # Drop counts for skills no longer active; keep known counts.
                self._instruction_chars_by_version = {
                    vid: n
                    for vid, n in self._instruction_chars_by_version.items()
                    if vid in self._accepted_version_ids
                }
            self._accepted_instruction_chars = sum(
                self._instruction_chars_by_version.values()
            )

    @property
    def accepted_instruction_chars(self) -> int:
        with self._lock:
            return int(self._accepted_instruction_chars)

    def note_instruction_chars(self, version_id: UUID, char_count: int) -> None:
        """Record instruction size for an accepted/active skill version."""
        with self._lock:
            if version_id not in self._accepted_version_ids:
                return
            self._instruction_chars_by_version[version_id] = max(0, int(char_count))
            self._accepted_instruction_chars = sum(
                self._instruction_chars_by_version.values()
            )

    @property
    def current_manifest(self) -> ResolvedRunManifestRevision | None:
        with self._lock:
            return self._current_manifest

    @property
    def policy_snapshot(self) -> Any | None:
        with self._lock:
            return self._policy_snapshot

    def accepted_skill_version_ids(self) -> frozenset[UUID]:
        with self._lock:
            return frozenset(self._accepted_version_ids)

    def is_skill_active(self, version_id: UUID) -> bool:
        with self._lock:
            if self._current_manifest is None:
                return False
            return any(s.version_id == version_id for s in self._current_manifest.active_skills)

    def applied_owner_budget_digests(self) -> dict[UUID, str]:
        with self._lock:
            return dict(self._applied_owner_budget_digests)

    def applied_skill_terminals(self) -> dict[UUID, bool]:
        with self._lock:
            return dict(self._applied_skill_terminals)

    def compatible_consumers(self) -> dict[str, frozenset[UUID]]:
        with self._lock:
            return {k: frozenset(v) for k, v in self._compatible_consumers.items()}

    def peek_package(self, call_id: str) -> PendingSkillActivationPackage | None:
        with self._lock:
            return self._packages.get(call_id)

    def stage(self, package: PendingSkillActivationPackage) -> None:
        with self._lock:
            if package.call_id in self._packages:
                raise ValueError("package already staged for call_id")
            self._packages[package.call_id] = package

    def accept(
        self,
        *,
        call_id: str,
        current_manifest: ResolvedRunManifestRevision,
        proposed_manifest: ResolvedRunManifestRevision,
    ) -> None:
        with self._lock:
            package = self._packages.get(call_id)
            if package is None:
                # No staged package (ordinary non-mutating control/business call).
                return
            if package.accepted or package.discarded:
                raise ValueError("package already finalized")
            effect = package.effect
            # Recompute effect digest and recheck parent/child.
            recomputed = _effect_digest(
                call_id=call_id,
                parent_revision=effect.expected_parent_revision,
                parent_digest=effect.expected_parent_digest,
                proposed=proposed_manifest,
            )
            if recomputed != effect.effect_digest:
                package.discarded = True
                self._packages.pop(call_id, None)
                raise ValueError("effect_digest mismatch")
            if (
                current_manifest.revision != effect.expected_parent_revision
                or current_manifest.manifest_digest != effect.expected_parent_digest
            ):
                package.discarded = True
                self._packages.pop(call_id, None)
                raise ValueError("parent_manifest mismatch")
            if proposed_manifest.manifest_digest != effect.proposed_manifest.manifest_digest:
                package.discarded = True
                self._packages.pop(call_id, None)
                raise ValueError("proposed_manifest mismatch")
            # Candidate effective_policy_digest must match proposed Manifest.
            if package.candidate_effective_policy_digest is not None:
                if (
                    proposed_manifest.effective_policy_digest
                    != package.candidate_effective_policy_digest
                ):
                    package.discarded = True
                    self._packages.pop(call_id, None)
                    raise ValueError("effective_policy_digest mismatch")

            # Failure-atomic: mutate only after all comparisons succeed.
            self._current_manifest = proposed_manifest
            self._accepted_version_ids = {
                item.version_id for item in proposed_manifest.active_skills
            }
            # Update instruction occupancy from the accepted package when known.
            if package.resulting_instruction_chars > 0:
                self._accepted_instruction_chars = int(
                    package.resulting_instruction_chars
                )
            elif package.activated_instruction_chars > 0:
                self._accepted_instruction_chars += int(
                    package.activated_instruction_chars
                )
            # Drop counts for skills no longer active.
            self._instruction_chars_by_version = {
                vid: n
                for vid, n in self._instruction_chars_by_version.items()
                if vid in self._accepted_version_ids
            }
            # Advance optional policy snapshot pointer.
            if package.candidate_effective_policy_digest is not None:
                snap = self._policy_snapshot_by_digest.get(
                    package.candidate_effective_policy_digest
                )
                if snap is not None:
                    self._policy_snapshot = snap
            # Commit owner budget limits (never mutates Run limits/usage).
            for owner_limits in package.candidate_owner_budget_limits:
                self._apply_owner_limits_locked(owner_limits)
            # Commit skill terminal obligations.
            for skill_version_id, terminal_text_allowed in package.candidate_skill_terminals:
                self._apply_skill_terminal_locked(
                    skill_version_id=skill_version_id,
                    terminal_text_allowed=terminal_text_allowed,
                )
            # Commit compatible consumers.
            for domain_key, consumer_id in package.candidate_compatible_consumers:
                self._compatible_consumers.setdefault(domain_key, set()).add(consumer_id)

            package.accepted = True
            events = package.post_commit_events
            accepted_manifest = proposed_manifest
            self._packages.pop(call_id, None)

        # Rebind dependents (auth factory, control runtime) so new Skill
        # bindings become dispatchable on the next call of this Run.
        for hook in list(self._on_accept_hooks):
            try:
                hook(accepted_manifest)
            except Exception:
                # Hooks must not unwind an already-accepted Manifest.
                self._event_failures.append(call_id)

        # Event delivery after accept cannot roll back the Manifest.
        sink = self._post_commit_sink
        if sink is not None:
            for event in events:
                try:
                    sink(event)
                except Exception:
                    self._event_failures.append(call_id)

    def _apply_owner_limits_locked(self, owner_limits: OwnerBudgetLimits) -> None:
        """Apply one owner bucket under the lifecycle lock.

        Failures after Manifest mutation are recorded but do not rewind the
        Manifest (events already post-commit). Caller must stage only limits
        that pure_add_owner_limits would accept.
        """
        self._applied_owner_budget_digests[owner_limits.owner_version_id] = (
            owner_limits.owner_budget_digest
        )
        ledger = self._budget_ledger
        if ledger is None:
            return
        try:
            decision = ledger.add_owner_limits(owner_limits)
            if getattr(decision, "allowed", True) is False:
                self._event_failures.append(
                    f"owner_limits_denied:{owner_limits.owner_version_id}"
                )
        except Exception:
            self._event_failures.append(
                f"owner_limits_error:{owner_limits.owner_version_id}"
            )

    def _apply_skill_terminal_locked(
        self,
        *,
        skill_version_id: UUID,
        terminal_text_allowed: bool,
    ) -> None:
        self._applied_skill_terminals[skill_version_id] = bool(terminal_text_allowed)
        ledger = self._obligation_ledger
        if ledger is None:
            return
        try:
            ledger.create_skill_terminal(
                skill_version_id=skill_version_id,
                terminal_text_allowed=bool(terminal_text_allowed),
            )
        except Exception:
            self._event_failures.append(f"skill_terminal_error:{skill_version_id}")

    def discard(self, *, call_id: str, reason_code: str) -> None:
        del reason_code
        with self._lock:
            package = self._packages.pop(call_id, None)
            if package is None:
                return
            if package.accepted:
                # Cannot discard after acceptance.
                self._packages[call_id] = package
                return
            package.discarded = True
            # Candidate state dies with the package: no owner buckets, no
            # obligations, no consumers, no events. skill.inject accounting is
            # independent and never rewound here.


def _effect_digest(
    *,
    call_id: str,
    parent_revision: int,
    parent_digest: str,
    proposed: ResolvedRunManifestRevision,
) -> str:
    return sha256_canonical_json(
        {
            "callId": call_id,
            "parentRevision": parent_revision,
            "parentDigest": parent_digest,
            "proposedRevision": proposed.revision,
            "proposedDigest": proposed.manifest_digest,
            "activeSkillVersionIds": [str(s.version_id) for s in proposed.active_skills],
            "effectivePolicyDigest": proposed.effective_policy_digest,
        }
    )


def _package_digest(
    *,
    call_id: str,
    activated_version_ids: Sequence[UUID],
    owner_budget_digests: Sequence[str],
    skill_terminal_ids: Sequence[UUID],
    compatible_consumers: Sequence[tuple[str, UUID]],
    effective_policy_digest: str | None,
) -> str:
    return sha256_canonical_json(
        {
            "callId": call_id,
            "activatedVersionIds": [str(v) for v in activated_version_ids],
            "ownerBudgetDigests": list(owner_budget_digests),
            "skillTerminalIds": [str(v) for v in skill_terminal_ids],
            "compatibleConsumers": [
                {"domainKey": k, "consumerVersionId": str(v)}
                for k, v in sorted(compatible_consumers, key=lambda x: (x[0], x[1].bytes))
            ],
            "effectivePolicyDigest": effective_policy_digest,
        }
    )


def build_domain_key_ownership_map(
    *,
    current_manifest: ResolvedRunManifestRevision,
    candidates: Sequence[SkillActivationCandidate],
    allow_business_duplicates: bool = False,
) -> dict[str, str]:
    """Map domain_key -> owner label for base controls, active, and candidate batch.

    Main Agent base-control collisions remain unconditional failures.
    When allow_business_duplicates is True, existing-active / same-batch business
    Domain Keys are recorded but not raised here — §4.3 compatibility evaluation
    decides consumer-vs-conflict. When False (Plan 04 default), any collision fails.
    """
    ownership: dict[str, str] = {}
    for cap in current_manifest.capabilities:
        ownership[cap.capability_key] = f"manifest:{cap.capability_key}"
    for key in MAIN_AGENT_CONTROL_KEYS:
        ownership.setdefault(key, f"base_control:{key}")
    for candidate in candidates:
        owner = f"skill:{candidate.skill.canonical_name}:{candidate.skill.version_id}"
        for cap in candidate.capabilities:
            prior = ownership.get(cap.capability_key)
            if prior is None:
                ownership[cap.capability_key] = owner
                continue
            # Unconditional: never collide with base controls.
            if prior.startswith("base_control:"):
                raise CatalogError(SKILL_CAPABILITY_CONFLICT)
            if not allow_business_duplicates:
                # Plan 04 strict exclusivity (any prior owner fails).
                if prior != owner:
                    raise CatalogError(SKILL_CAPABILITY_CONFLICT)
                continue
            # Plan 05: business duplicates deferred to §4.3; keep first owner.
            # Same skill may re-declare its own keys.
            if prior == owner:
                continue
            # Leave prior owner; caller evaluates compatibility.
    return ownership


def _normalize_conflict_rules(
    rules: Sequence[SkillConflictRuleV1 | dict[str, Any] | Any],
) -> tuple[SkillConflictRuleV1, ...]:
    normalized: list[SkillConflictRuleV1] = []
    for rule in rules:
        if isinstance(rule, SkillConflictRuleV1):
            normalized.append(rule)
            continue
        if isinstance(rule, dict):
            normalized.append(SkillConflictRuleV1.model_validate(rule))
            continue
        # Fail closed on unknown payload shapes.
        raise CatalogError("skill_conflict_invalid_rule")
    return tuple(normalized)


def _evaluate_candidate_conflicts(
    *,
    current_manifest: ResolvedRunManifestRevision,
    to_append: Sequence[SkillActivationCandidate],
    policy: SkillInjectionPolicyContext | None,
) -> str | None:
    """Return a conflict reason_code or None when allowed."""
    # Collect any rules from candidates or active context.
    has_rules = any(c.conflict_rules for c in to_append)
    active_rules_map: dict[UUID, tuple[SkillConflictRuleV1, ...]] = {}
    active_aliases_map: dict[UUID, tuple[str, ...]] = {}
    if policy is not None:
        active_rules_map = {vid: rules for vid, rules in policy.active_conflict_rules}
        active_aliases_map = {vid: aliases for vid, aliases in policy.active_aliases}
        has_rules = has_rules or bool(active_rules_map)
    if not has_rules:
        return None

    active_participants: list[SkillConflictParticipant] = []
    for skill in current_manifest.active_skills:
        rules = active_rules_map.get(skill.version_id, ())
        aliases = active_aliases_map.get(skill.version_id, ())
        active_participants.append(
            SkillConflictParticipant(
                identity=SkillConflictIdentity(
                    canonical_name=skill.canonical_name,
                    version_id=skill.version_id,
                    package_id=skill.package_id,
                    aliases=aliases,
                ),
                conflict_rules=_normalize_conflict_rules(rules),
                role="active",
            )
        )

    candidate_participants: list[SkillConflictParticipant] = []
    for candidate in to_append:
        try:
            rules = _normalize_conflict_rules(candidate.conflict_rules)
        except Exception:
            return "skill_conflict_invalid_rule"
        candidate_participants.append(
            SkillConflictParticipant(
                identity=SkillConflictIdentity(
                    canonical_name=candidate.skill.canonical_name,
                    version_id=candidate.skill.version_id,
                    package_id=candidate.skill.package_id,
                    aliases=candidate.aliases,
                ),
                conflict_rules=rules,
                role="candidate",
            )
        )

    catalog_skills: Sequence[SkillConflictIdentity] | None = None
    if policy is not None and policy.catalog_skills:
        catalog_skills = policy.catalog_skills
    result = evaluate_skill_conflicts(
        active=active_participants,
        candidates=candidate_participants,
        catalog_skills=catalog_skills,
    )
    if result.allowed:
        return None
    return result.reason_code or "skill_conflict_invalid_rule"


def _capability_identity_tuple(cap: ResolvedCapabilityRef) -> tuple[Any, ...]:
    return (
        cap.capability_type,
        cap.capability_key,
        cap.target_identity,
        cap.target_id,
        cap.target_version_id,
        cap.target_revision,
        cap.input_schema_digest,
        cap.output_schema_digest,
        cap.resolution_digest,
        cap.dependency_closure_digest,
        cap.binding_contract_digest,
    )


def _evaluate_business_duplicates(
    *,
    current_manifest: ResolvedRunManifestRevision,
    to_append: Sequence[SkillActivationCandidate],
    policy: SkillInjectionPolicyContext | None,
) -> tuple[str | None, tuple[tuple[str, UUID], ...]]:
    """§4.3 strict duplicate compatibility.

    Returns (reason_code_or_None, compatible_consumer_pairs).
    Base-control collisions are already rejected by ownership map.
    """
    # Index existing Manifest capabilities.
    existing_caps = {
        cap.capability_key: cap for cap in current_manifest.capabilities
    }
    # Existing owner version per domain key (skill owners only when known).
    existing_owner_by_key: dict[str, UUID | None] = {
        cap.capability_key: None for cap in current_manifest.capabilities
    }
    for key in MAIN_AGENT_CONTROL_KEYS:
        existing_owner_by_key.setdefault(key, None)

    # Policy-provided exposure views for richer checks.
    existing_views: dict[str, CandidateExposureView] = {}
    if policy is not None:
        for view in policy.existing_exposures:
            existing_views[view.domain_key] = view

    # Same-batch ownership: first owner by deterministic (name, version_id) order
    # for each Domain Key claimed by multiple candidates.
    claims: dict[str, list[SkillActivationCandidate]] = {}
    for candidate in to_append:
        for cap in candidate.capabilities:
            claims.setdefault(cap.capability_key, []).append(candidate)

    consumers: list[tuple[str, UUID]] = []

    for domain_key, claimants in claims.items():
        # Base control: unconditional (already handled, but belt-and-suspenders).
        if domain_key in MAIN_AGENT_CONTROL_KEYS:
            return SKILL_CAPABILITY_CONFLICT, ()

        existing_cap = existing_caps.get(domain_key)
        # Deterministic owner among same-batch claimants.
        ordered = sorted(
            claimants,
            key=lambda c: (c.skill.canonical_name, c.skill.version_id.bytes),
        )
        # Existing active owner wins; otherwise lowest name/version.
        if existing_cap is not None:
            # Every claimant must be §4.3-compatible with the existing capability.
            for candidate in ordered:
                match = next(
                    (c for c in candidate.capabilities if c.capability_key == domain_key),
                    None,
                )
                if match is None:
                    continue
                if _capability_identity_tuple(match) != _capability_identity_tuple(
                    existing_cap
                ):
                    return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
                # Policy fields must match when both sides declare them.
                view = existing_views.get(domain_key)
                cand_view = next(
                    (v for v in candidate.exposure_views if v.domain_key == domain_key),
                    None,
                )
                if view is not None and cand_view is not None:
                    if (
                        view.max_skill_calls is not None
                        and cand_view.max_skill_calls is not None
                        and view.max_skill_calls != cand_view.max_skill_calls
                    ):
                        return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
                    if (
                        view.max_same_read_calls is not None
                        and cand_view.max_same_read_calls is not None
                        and view.max_same_read_calls != cand_view.max_same_read_calls
                    ):
                        return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
                    if (
                        view.requires_terminal_output is not None
                        and cand_view.requires_terminal_output is not None
                        and view.requires_terminal_output
                        != cand_view.requires_terminal_output
                    ):
                        return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
                    if (
                        view.terminal_text_allowed is not None
                        and cand_view.terminal_text_allowed is not None
                        and view.terminal_text_allowed != cand_view.terminal_text_allowed
                    ):
                        return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
                # Compatible non-owning consumer.
                consumers.append((domain_key, candidate.skill.version_id))
            continue

        # No existing owner: first ordered claimant owns; rest must be compatible.
        if len(ordered) == 1:
            continue
        owner = ordered[0]
        owner_cap = next(
            c for c in owner.capabilities if c.capability_key == domain_key
        )
        for consumer_candidate in ordered[1:]:
            consumer_cap = next(
                (
                    c
                    for c in consumer_candidate.capabilities
                    if c.capability_key == domain_key
                ),
                None,
            )
            if consumer_cap is None:
                continue
            if _capability_identity_tuple(consumer_cap) != _capability_identity_tuple(
                owner_cap
            ):
                return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
            # Policy field parity when both declare exposure views.
            owner_view = next(
                (v for v in owner.exposure_views if v.domain_key == domain_key),
                None,
            )
            consumer_view = next(
                (
                    v
                    for v in consumer_candidate.exposure_views
                    if v.domain_key == domain_key
                ),
                None,
            )
            if owner_view is not None and consumer_view is not None:
                if (
                    owner_view.max_skill_calls is not None
                    and consumer_view.max_skill_calls is not None
                    and owner_view.max_skill_calls != consumer_view.max_skill_calls
                ):
                    return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
                if (
                    owner_view.max_same_read_calls is not None
                    and consumer_view.max_same_read_calls is not None
                    and owner_view.max_same_read_calls
                    != consumer_view.max_same_read_calls
                ):
                    return DUPLICATE_CAPABILITY_POLICY_CONFLICT, ()
            consumers.append((domain_key, consumer_candidate.skill.version_id))

    # Deduplicate consumer pairs.
    unique = sorted(set(consumers), key=lambda x: (x[0], x[1].bytes))
    return None, tuple(unique)


def _build_owner_budget_limits(
    *,
    to_append: Sequence[SkillActivationCandidate],
    policy: SkillInjectionPolicyContext,
) -> tuple[OwnerBudgetLimits, ...]:
    """Compute owner-budget-limit additions capped by unchanged Run limits."""
    from app.assistant.policy.contracts import RunBudgetLimits

    # Minimal RunBudgetLimits for capping; only fields used by normalize.
    run_limits = RunBudgetLimits(
        max_provider_rounds=8,
        max_main_agent_cycles=1,
        max_active_skills=policy.run_max_active_skills,
        max_total_capability_calls=policy.run_max_total_capability_calls,
        max_parallel_calls=min(4, policy.run_max_total_capability_calls),
        max_capability_depth=4,
        max_agent_depth=2,
        max_same_read_signature=policy.run_max_same_read_signature,
        max_prompt_tokens=None,
        max_completion_tokens=4096,
        max_wall_time_ms=120_000,
        max_completion_followup_rounds=2,
    )
    limits: list[OwnerBudgetLimits] = []
    for candidate in to_append:
        # Compatible consumers still get their own owner bucket for *their*
        # owned exposures; instruction-only gets zero-call bucket.
        is_instruction_only = candidate.is_instruction_only or not candidate.capabilities
        owner_limits = normalize_owner_budget_limits(
            owner_kind="skill_version",
            owner_version_id=candidate.skill.version_id,
            run_limits=run_limits,
            max_skill_calls=candidate.max_skill_calls,
            max_same_read_calls=candidate.max_same_read_calls,
            is_instruction_only=is_instruction_only,
        )
        limits.append(owner_limits)
    # Deterministic order by owner_version_id.
    limits.sort(key=lambda item: item.owner_version_id.bytes)
    return tuple(limits)


def _evaluate_terminal_satisfiability(
    *,
    to_append: Sequence[SkillActivationCandidate],
    policy: SkillInjectionPolicyContext,
) -> str | None:
    """Return skill_completion_unsatisfiable or None."""
    for candidate in to_append:
        if not candidate.requires_terminal_output:
            continue
        has_terminal_cap = any(
            # Without full descriptors, treat any capability as potential terminal path
            # when max_skill_calls allows; pure structural check only.
            True
            for _ in candidate.capabilities
        ) if candidate.capabilities else False
        view = SkillTerminalSatisfiabilityView(
            skill_version_id=candidate.skill.version_id,
            requires_terminal_output=True,
            terminal_text_allowed=bool(candidate.terminal_text_allowed),
            remaining_provider_slots=int(policy.remaining_provider_slots),
            has_terminal_capability_exposure=has_terminal_cap,
            terminal_capability_path_available=has_terminal_cap
            and (candidate.max_skill_calls is None or candidate.max_skill_calls >= 1),
            max_skill_calls=(
                0
                if candidate.max_skill_calls is None and not candidate.capabilities
                else (
                    int(candidate.max_skill_calls)
                    if candidate.max_skill_calls is not None
                    else 16
                )
            ),
        )
        ok, reason = evaluate_skill_terminal_satisfiability(view)
        if not ok:
            return reason or SKILL_COMPLETION_UNSATISFIABLE
    return None


def _derive_candidate_policy_digest(
    *,
    current_manifest: ResolvedRunManifestRevision,
    to_append: Sequence[SkillActivationCandidate],
    owner_limits: Sequence[OwnerBudgetLimits],
    consumers: Sequence[tuple[str, UUID]],
    policy: SkillInjectionPolicyContext | None,
) -> str | None:
    """Derive a candidate effective_policy_digest for the proposed Manifest.

    When policy.effective_policy_digest is provided, use it. Otherwise when any
    policy-bearing field is present, derive a deterministic digest from the
    parent policy digest + activated versions + owner limits + consumers so the
    Manifest child carries a distinct policy identity.
    """
    if policy is not None and policy.effective_policy_digest is not None:
        return policy.effective_policy_digest
    has_policy_fields = any(
        c.conflict_rules
        or c.max_skill_calls is not None
        or c.requires_terminal_output
        or c.exposure_views
        for c in to_append
    ) or bool(owner_limits) or bool(consumers)
    if not has_policy_fields and policy is None:
        # Plan 04 path: keep parent digest.
        return None
    return sha256_canonical_json(
        {
            "kind": "candidate_effective_policy",
            "parentEffectivePolicyDigest": current_manifest.effective_policy_digest,
            "activatedVersionIds": [
                str(c.skill.version_id) for c in sorted(
                    to_append, key=lambda x: x.skill.version_id.bytes
                )
            ],
            "ownerBudgetDigests": [o.owner_budget_digest for o in owner_limits],
            "compatibleConsumers": [
                {"domainKey": k, "consumerVersionId": str(v)}
                for k, v in sorted(consumers, key=lambda x: (x[0], x[1].bytes))
            ],
        }
    )


def resolve_inject_selectors(
    *,
    catalog: CatalogSearchState,
    skills_input: Sequence[dict[str, Any]],
) -> list[UUID]:
    """Resolve inject selectors against the Run catalog + disclosed set."""
    if not skills_input:
        raise CatalogError("invalid_input")
    if len(skills_input) > 4:
        raise CatalogError("invalid_input")
    resolved: list[UUID] = []
    seen_selectors: set[str] = set()
    for item in skills_input:
        if not isinstance(item, dict):
            raise CatalogError("invalid_input")
        version_raw = item.get("versionId") or item.get("version_id")
        name_raw = item.get("name")
        if version_raw is None and name_raw is None:
            raise CatalogError("invalid_input")
        if version_raw is not None and name_raw is not None:
            # Exactly one selector field preferred; both is invalid duplicate selector shape.
            raise CatalogError("invalid_input")
        if version_raw is not None:
            selector_key = f"v:{version_raw}"
            if selector_key in seen_selectors:
                raise CatalogError("invalid_input")
            seen_selectors.add(selector_key)
            try:
                version_id = UUID(str(version_raw))
            except (TypeError, ValueError) as exc:
                raise CatalogError(SKILL_NOT_CATALOGED) from exc
            if not catalog.is_disclosed(version_id):
                raise CatalogError(SKILL_NOT_DISCLOSED)
            record = catalog.snapshot.get_by_version_id(version_id)
            if record is None:
                raise CatalogError(SKILL_NOT_CATALOGED)
            resolved.append(version_id)
        else:
            selector_key = f"n:{name_raw}"
            if selector_key in seen_selectors:
                raise CatalogError("invalid_input")
            seen_selectors.add(selector_key)
            record = catalog.snapshot.get_by_name_or_alias(str(name_raw))
            if record is None:
                raise CatalogError(SKILL_NOT_CATALOGED)
            resolved.append(record.version_id)
    return resolved


def stage_skill_injection(
    *,
    call_id: str,
    current_manifest: ResolvedRunManifestRevision,
    candidates: Sequence[SkillActivationCandidate],
    max_active_skills: int = 4,
    max_active_instruction_chars: int = 24_000,
    lifecycle: MainAgentManifestEffectLifecycle | None = None,
    policy: SkillInjectionPolicyContext | None = None,
) -> tuple[CapabilityResult, PendingManifestEffect | None, PendingSkillActivationPackage | None]:
    """Validate batch ownership/budgets/policy and stage one pending package (or no-op).

    Plan 05: when ``policy`` is provided (or candidates carry policy fields),
    evaluates conflict rules, §4.3 duplicate compatibility, owner budget limits,
    and terminal-obligation satisfiability before staging. All candidate deltas
    live only in the pending package until accept.
    """
    # Idempotent reinjections of exact already-active versions.
    active_by_name = {s.canonical_name: s for s in current_manifest.active_skills}
    to_append: list[SkillActivationCandidate] = []
    noop: list[ResolvedSkillRef] = []
    for candidate in candidates:
        existing = active_by_name.get(candidate.skill.canonical_name)
        if existing is not None:
            if existing.version_id == candidate.skill.version_id:
                noop.append(candidate.skill)
                continue
            return (
                _fail(call_id, SKILL_VERSION_CONFLICT, "skill version conflict"),
                None,
                None,
            )
        to_append.append(candidate)

    if not to_append:
        # Pure reinjection: unchanged Manifest, no package/event/bucket/obligation.
        payload: dict[str, JsonValue] = {
            "status": "noop",
            "activated": [],
            "noop": [
                {
                    "canonicalName": s.canonical_name,
                    "versionId": str(s.version_id),
                    "contentDigest": s.content_digest,
                    "versionDigest": s.version_digest,
                }
                for s in noop
            ],
            "proposedManifestRevision": current_manifest.revision,
            "proposedManifestDigest": current_manifest.manifest_digest,
        }
        return (
            completed_result(
                user_text=None,
                structured_output=payload,
                metrics=_metrics(),
                terminal_output=False,
                needs_followup=True,
            ),
            None,
            None,
        )

    # Detect whether Plan 05 policy path is active.
    policy_active = policy is not None or any(
        c.conflict_rules
        or c.max_skill_calls is not None
        or c.requires_terminal_output
        or c.exposure_views
        for c in to_append
    )

    # Pre-staging ownership map across base + active + full candidate batch.
    # Base-control collisions always fail; business duplicates only when policy path.
    try:
        build_domain_key_ownership_map(
            current_manifest=current_manifest,
            candidates=to_append,
            allow_business_duplicates=policy_active,
        )
    except CatalogError as exc:
        return (
            _fail(call_id, exc.reason_code, "skill capability conflict"),
            None,
            None,
        )

    # Structured conflict rules (excludes/requires/exclusive_group).
    conflict_reason = _evaluate_candidate_conflicts(
        current_manifest=current_manifest,
        to_append=to_append,
        policy=policy,
    )
    if conflict_reason is not None:
        return (
            _fail(call_id, conflict_reason, "skill conflict rule denied activation"),
            None,
            None,
        )

    # §4.3 strict duplicate compatibility (business Domain Keys only).
    consumers: tuple[tuple[str, UUID], ...] = ()
    if policy_active:
        dup_reason, consumers = _evaluate_business_duplicates(
            current_manifest=current_manifest,
            to_append=to_append,
            policy=policy,
        )
        if dup_reason is not None:
            return (
                _fail(call_id, dup_reason, "duplicate capability policy conflict"),
                None,
                None,
            )

    new_active_count = len(current_manifest.active_skills) + len(to_append)
    if new_active_count > max_active_skills:
        return (
            _fail(call_id, ACTIVE_SKILL_LIMIT_EXCEEDED, "active skill limit exceeded"),
            None,
            None,
        )
    # Enforce full aggregate instruction budget BEFORE staging so acceptance cannot
    # leave an over-budget active Manifest. Existing active skills still count.
    existing_chars = 0
    if lifecycle is not None:
        existing_chars = int(lifecycle.accepted_instruction_chars)
    new_chars = sum(max(0, int(c.instruction_char_count)) for c in to_append)
    if existing_chars + new_chars > max_active_instruction_chars:
        return (
            _fail(call_id, SKILL_CONTEXT_BUDGET_EXCEEDED, "skill context budget exceeded"),
            None,
            None,
        )

    # Owner-budget-limit additions capped by unchanged Run limits.
    owner_limits: tuple[OwnerBudgetLimits, ...] = ()
    skill_terminals: tuple[tuple[UUID, bool], ...] = ()
    if policy is not None or any(
        c.max_skill_calls is not None
        or c.requires_terminal_output
        or c.is_instruction_only
        for c in to_append
    ):
        ctx = policy or SkillInjectionPolicyContext(
            run_max_active_skills=max_active_skills,
        )
        owner_limits = _build_owner_budget_limits(to_append=to_append, policy=ctx)
        # Terminal-obligation satisfiability before staging.
        unsat = _evaluate_terminal_satisfiability(to_append=to_append, policy=ctx)
        if unsat is not None:
            return (
                _fail(
                    call_id,
                    SKILL_COMPLETION_UNSATISFIABLE,
                    "skill terminal obligation unsatisfiable",
                ),
                None,
                None,
            )
        skill_terminals = tuple(
            (c.skill.version_id, bool(c.terminal_text_allowed))
            for c in to_append
            if c.requires_terminal_output
        )

    # Candidate effective policy digest (None keeps parent digest — Plan 04 path).
    candidate_policy_digest = _derive_candidate_policy_digest(
        current_manifest=current_manifest,
        to_append=to_append,
        owner_limits=owner_limits,
        consumers=consumers,
        policy=policy,
    )

    # Single lineage step for the whole batch (revision +1 only once).
    try:
        proposed = append_skill_activations_batch(
            current_manifest,
            activations=tuple(
                (candidate.skill, candidate.capabilities) for candidate in to_append
            ),
            effective_policy_digest=candidate_policy_digest,
        )
    except SkillVersionConflictError:
        return (
            _fail(call_id, SKILL_VERSION_CONFLICT, "skill version conflict"),
            None,
            None,
        )
    except ValueError:
        return (
            _fail(call_id, SKILL_CAPABILITY_CONFLICT, "skill capability conflict"),
            None,
            None,
        )

    # Compatible consumers must not introduce a second Manifest capability entry
    # (append_skill_activations_batch already keeps first identical ref).
    pkg_digest = _package_digest(
        call_id=call_id,
        activated_version_ids=tuple(c.skill.version_id for c in to_append),
        owner_budget_digests=tuple(o.owner_budget_digest for o in owner_limits),
        skill_terminal_ids=tuple(vid for vid, _ in skill_terminals),
        compatible_consumers=consumers,
        effective_policy_digest=candidate_policy_digest
        or proposed.effective_policy_digest,
    )

    effect = PendingManifestEffect(
        call_id=call_id,
        expected_parent_revision=current_manifest.revision,
        expected_parent_digest=current_manifest.manifest_digest,
        proposed_manifest=proposed,
        effect_digest=_effect_digest(
            call_id=call_id,
            parent_revision=current_manifest.revision,
            parent_digest=current_manifest.manifest_digest,
            proposed=proposed,
        ),
        activation_payload={
            "activatedVersionIds": [str(c.skill.version_id) for c in to_append],
            "effectivePolicyDigest": proposed.effective_policy_digest,
            "packageDigest": pkg_digest,
        },
        post_commit_events=(
            {
                "eventType": "skill_activation_end",
                "status": "success",
                "callId": call_id,
                "manifestRevision": proposed.revision,
                "manifestDigest": proposed.manifest_digest,
                "effectivePolicyDigest": proposed.effective_policy_digest,
                "activated": [
                    {
                        "canonicalName": c.skill.canonical_name,
                        "versionId": str(c.skill.version_id),
                        "contentDigest": c.skill.content_digest,
                        "versionDigest": c.skill.version_digest,
                    }
                    for c in to_append
                ],
            },
            {
                "eventType": "manifest_revision",
                "revision": proposed.revision,
                "manifestDigest": proposed.manifest_digest,
                "parentDigest": current_manifest.manifest_digest,
                "effectivePolicyDigest": proposed.effective_policy_digest,
            },
        ),
    )
    package = PendingSkillActivationPackage(
        call_id=call_id,
        effect=effect,
        activated_version_ids=tuple(c.skill.version_id for c in to_append),
        noop_version_ids=tuple(s.version_id for s in noop),
        post_commit_events=effect.post_commit_events,
        resulting_instruction_chars=existing_chars + new_chars,
        activated_instruction_chars=new_chars,
        candidate_effective_policy_digest=candidate_policy_digest
        or proposed.effective_policy_digest,
        candidate_owner_budget_limits=owner_limits,
        candidate_skill_terminals=skill_terminals,
        candidate_compatible_consumers=consumers,
        package_digest=pkg_digest,
    )
    if lifecycle is not None:
        lifecycle.stage(package)

    payload = {
        "status": "staged",
        "activated": [
            {
                "canonicalName": c.skill.canonical_name,
                "versionId": str(c.skill.version_id),
                "contentDigest": c.skill.content_digest,
                "versionDigest": c.skill.version_digest,
                "resourceIndexDigest": c.resource_index_digest,
            }
            for c in to_append
        ],
        "noop": [
            {
                "canonicalName": s.canonical_name,
                "versionId": str(s.version_id),
                "contentDigest": s.content_digest,
                "versionDigest": s.version_digest,
            }
            for s in noop
        ],
        "proposedManifestRevision": proposed.revision,
        "proposedManifestDigest": proposed.manifest_digest,
        "effectivePolicyDigest": proposed.effective_policy_digest,
        "packageDigest": pkg_digest,
    }
    return (
        completed_result(
            user_text=None,
            structured_output=payload,  # type: ignore[arg-type]
            metrics=_metrics(),
            terminal_output=False,
            needs_followup=True,
        ),
        effect,
        package,
    )


def _fail(call_id: str, code: str, message: str) -> CapabilityResult:
    return failed_result(
        error=CapabilityError(
            error_type="execution_failed",
            safe_code=code[:64],
            safe_message=message[:256],
            retry_disposition="never",
            call_id=call_id,
        ),
        metrics=_metrics(),
    )


def _metrics() -> CapabilityMetrics:
    return CapabilityMetrics(
        duration_ms=0.0,
        adapter_duration_ms=0.0,
        input_bytes=0,
        output_bytes=0,
    )


class MainAgentToolsProvider:
    """ToolsProvider exposing base controls + active skill bindings only."""

    def __init__(
        self,
        *,
        control_bindings: Sequence[FrozenCapabilityBinding],
        active_bindings_by_version: dict[UUID, tuple[FrozenCapabilityBinding, ...]] | None = None,
        lifecycle: MainAgentManifestEffectLifecycle | None = None,
        surface_builder: Callable[..., Any] | None = None,
    ) -> None:
        self._control_bindings = tuple(control_bindings)
        self._active_bindings_by_version = dict(active_bindings_by_version or {})
        self._lifecycle = lifecycle
        self._surface_builder = surface_builder

    def register_active_bindings(
        self,
        version_id: UUID,
        bindings: Sequence[FrozenCapabilityBinding],
    ) -> None:
        self._active_bindings_by_version[version_id] = tuple(bindings)

    def resolve(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        scope: Any,
        locale: str,
    ) -> Any:
        del locale
        # Visible bindings: controls + bindings for active exact skill versions.
        active_ids = {s.version_id for s in manifest.active_skills}
        visible: list[FrozenCapabilityBinding] = list(self._control_bindings)
        for version_id in active_ids:
            visible.extend(self._active_bindings_by_version.get(version_id, ()))
        if self._surface_builder is None:
            # Minimal surface resolution for unit tests without full alias machinery.
            from app.assistant.provider_loop.aliases import build_provider_tool_surface

            return build_provider_tool_surface(
                manifest=manifest,
                provider_protocol="openai_chat",
                visible=visible,
                scope=scope,
            )
        return self._surface_builder(
            manifest=manifest,
            visible=visible,
            scope=scope,
        )


__all__ = [
    "ACTIVE_SKILL_LIMIT_EXCEEDED",
    "CONTROL_EFFECT_PROTOCOL_ERROR",
    "CandidateExposureView",
    "DUPLICATE_CAPABILITY_POLICY_CONFLICT",
    "MainAgentManifestEffectLifecycle",
    "MainAgentToolsProvider",
    "PendingSkillActivationPackage",
    "SKILL_ALREADY_ACTIVE",
    "SKILL_CAPABILITY_CONFLICT",
    "SKILL_COMPLETION_UNSATISFIABLE",
    "SKILL_CONTEXT_BUDGET_EXCEEDED",
    "SKILL_VERSION_CONFLICT",
    "SkillActivationCandidate",
    "SkillInjectionPolicyContext",
    "build_domain_key_ownership_map",
    "resolve_inject_selectors",
    "stage_skill_injection",
]
