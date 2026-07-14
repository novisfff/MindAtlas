"""Atomic skill activation and Manifest-effect lifecycle (Plan 04 Task 6).

Gateway success stages a PendingSkillActivationPackage; Plan 03 lineage
validation + ManifestEffectLifecyclePort.accept installs it. Active membership
is solely the accepted Manifest's active_skills.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
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
    append_skill_activation,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.main_agent.catalog import (
    CATALOG_CHANGED,
    SKILL_NOT_CATALOGED,
    SKILL_NOT_DISCLOSED,
    CatalogError,
    CatalogSearchState,
    LivePackageRecheck,
    recheck_candidates_for_activation,
)
from app.assistant.main_agent.control_runtime import PendingManifestEffect
from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS

SKILL_CAPABILITY_CONFLICT = "skill_capability_conflict"
SKILL_ALREADY_ACTIVE = "skill_already_active"
SKILL_VERSION_CONFLICT = "skill_version_conflict"
SKILL_CONTEXT_BUDGET_EXCEEDED = "skill_context_budget_exceeded"
ACTIVE_SKILL_LIMIT_EXCEEDED = "active_skill_limit_exceeded"
CONTROL_EFFECT_PROTOCOL_ERROR = "control_effect_protocol_error"


@dataclass(frozen=True)
class SkillActivationCandidate:
    """Exact published Skill + bindings prepared for one inject batch item."""

    skill: ResolvedSkillRef
    capabilities: tuple[ResolvedCapabilityRef, ...]
    frozen_bindings: tuple[FrozenCapabilityBinding, ...] = ()
    instruction_char_count: int = 0
    resource_index_digest: str = ""
    author_allowed_side_effects: tuple[str, ...] = ()


@dataclass
class PendingSkillActivationPackage:
    """Call-scoped staged activation; not durable; single-use."""

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


class MainAgentManifestEffectLifecycle:
    """Lifecycle port that commits staged activation packages after lineage accept."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packages: dict[str, PendingSkillActivationPackage] = {}
        self._current_manifest: ResolvedRunManifestRevision | None = None
        self._accepted_version_ids: set[UUID] = set()
        self._accepted_instruction_chars: int = 0
        self._instruction_chars_by_version: dict[UUID, int] = {}
        self._post_commit_sink: Callable[[dict[str, JsonValue]], None] | None = None
        self._event_failures: list[str] = []

    def set_event_sink(self, sink: Callable[[dict[str, JsonValue]], None] | None) -> None:
        self._post_commit_sink = sink

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

    def accepted_skill_version_ids(self) -> frozenset[UUID]:
        with self._lock:
            return frozenset(self._accepted_version_ids)

    def is_skill_active(self, version_id: UUID) -> bool:
        with self._lock:
            if self._current_manifest is None:
                return False
            return any(s.version_id == version_id for s in self._current_manifest.active_skills)

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
            package.accepted = True
            events = package.post_commit_events
            self._packages.pop(call_id, None)

        # Event delivery after accept cannot roll back the Manifest.
        sink = self._post_commit_sink
        if sink is not None:
            for event in events:
                try:
                    sink(event)
                except Exception:
                    self._event_failures.append(call_id)

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
        }
    )


def build_domain_key_ownership_map(
    *,
    current_manifest: ResolvedRunManifestRevision,
    candidates: Sequence[SkillActivationCandidate],
) -> dict[str, str]:
    """Map domain_key -> owner label for base controls, active, and candidate batch."""
    ownership: dict[str, str] = {}
    for cap in current_manifest.capabilities:
        ownership[cap.capability_key] = f"manifest:{cap.capability_key}"
    for key in MAIN_AGENT_CONTROL_KEYS:
        ownership.setdefault(key, f"base_control:{key}")
    for candidate in candidates:
        owner = f"skill:{candidate.skill.canonical_name}:{candidate.skill.version_id}"
        for cap in candidate.capabilities:
            prior = ownership.get(cap.capability_key)
            if prior is not None and not prior.startswith(f"skill:{candidate.skill.canonical_name}:"):
                # Collision with base/active/other candidate.
                raise CatalogError(SKILL_CAPABILITY_CONFLICT)
            if prior is not None and prior != owner:
                raise CatalogError(SKILL_CAPABILITY_CONFLICT)
            ownership[cap.capability_key] = owner
    return ownership


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
) -> tuple[CapabilityResult, PendingManifestEffect | None, PendingSkillActivationPackage | None]:
    """Validate batch ownership/budgets and stage one pending package (or no-op)."""
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
        # Pure reinjection: unchanged Manifest, no package/event.
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

    # Pre-staging ownership map across base + active + full candidate batch.
    try:
        build_domain_key_ownership_map(
            current_manifest=current_manifest,
            candidates=to_append,
        )
    except CatalogError as exc:
        return (
            _fail(call_id, exc.reason_code, "skill capability conflict"),
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

    proposed = current_manifest
    try:
        for candidate in to_append:
            proposed = append_skill_activation(
                proposed,
                skill=candidate.skill,
                capabilities=candidate.capabilities,
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
        },
        post_commit_events=(
            {
                "eventType": "skill_activation_end",
                "status": "success",
                "callId": call_id,
                "manifestRevision": proposed.revision,
                "manifestDigest": proposed.manifest_digest,
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
    "MainAgentManifestEffectLifecycle",
    "MainAgentToolsProvider",
    "PendingSkillActivationPackage",
    "SKILL_ALREADY_ACTIVE",
    "SKILL_CAPABILITY_CONFLICT",
    "SKILL_CONTEXT_BUDGET_EXCEEDED",
    "SKILL_VERSION_CONFLICT",
    "SkillActivationCandidate",
    "build_domain_key_ownership_map",
    "resolve_inject_selectors",
    "stage_skill_injection",
]
