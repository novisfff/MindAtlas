"""Reversible Main Agent golden-path rollout (Plan 04 Task 9).

Operations:
- ``plan``: select golden package, publish Profile draft with four controls,
  compute expected digests; never flip aggregate flags.
- ``enable``: cutover package catalog + Profile runtime after checks.
- ``disable``: set both aggregate flags false without deleting history.

All operations support ``dry_run``. Enable is idempotent when digests match.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS
from app.assistant.main_agent.golden_path import (
    GOLDEN_FIXTURE_CANONICAL_NAME,
    GoldenPackagePlan,
    publish_golden_profile,
    select_golden_package_plan,
)
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillPackage,
    AssistantSkillVersion,
)
from app.assistant.skills.service import AgentSkillService, MainAgentProfileService
from app.common.exceptions import ApiException

RolloutOperation = Literal["plan", "enable", "disable"]


class RolloutError(RuntimeError):
    """Operator-facing rollout failure with a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True, slots=True)
class RolloutExpectedState:
    package_id: UUID | None
    package_canonical_name: str | None
    package_version_id: UUID | None
    package_version_digest: str | None
    package_content_digest: str | None
    profile_id: UUID | None
    profile_version_id: UUID | None
    profile_content_digest: str | None
    golden_strategy: str | None
    control_keys: tuple[str, ...] = MAIN_AGENT_CONTROL_KEYS


@dataclass(slots=True)
class RolloutReport:
    operation: RolloutOperation
    dry_run: bool
    success: bool
    reason_code: str
    message: str
    expected: RolloutExpectedState | None = None
    package_catalog_enabled: bool | None = None
    package_migration_state: str | None = None
    profile_runtime_enabled: bool | None = None
    profile_migration_state: str | None = None
    other_catalog_enabled_packages: tuple[str, ...] = ()
    steps: list[str] = field(default_factory=list)
    golden_plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.expected is not None:
            exp = asdict(self.expected)
            # UUIDs → str for JSON
            for key, value in list(exp.items()):
                if isinstance(value, UUID):
                    exp[key] = str(value)
            payload["expected"] = exp
        return payload

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)


def _package_by_id(db: Session, package_id: UUID) -> AssistantSkillPackage:
    package = db.get(AssistantSkillPackage, package_id)
    if package is None:
        raise RolloutError("package_missing", f"skill package not found: {package_id}")
    return package


def _default_profile(db: Session) -> AssistantMainAgentProfile:
    profile = (
        db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.is_default.is_(True))
        .one_or_none()
    )
    if profile is None:
        raise RolloutError("profile_missing", "default main agent profile not found")
    return profile


def _other_catalog_enabled(db: Session, package_id: UUID | None) -> tuple[str, ...]:
    rows = (
        db.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.catalog_enabled.is_(True))
        .order_by(AssistantSkillPackage.canonical_name.asc())
        .all()
    )
    names: list[str] = []
    for row in rows:
        if package_id is not None and row.id == package_id:
            continue
        names.append(str(row.canonical_name))
    return tuple(names)


def _expected_from_plan(
    plan: GoldenPackagePlan,
    *,
    profile_id: UUID | None,
    profile_version_id: UUID | None,
    profile_content_digest: str | None,
) -> RolloutExpectedState:
    return RolloutExpectedState(
        package_id=plan.package_id,
        package_canonical_name=plan.canonical_name,
        package_version_id=plan.published_version_id,
        package_version_digest=plan.version_digest,
        package_content_digest=plan.content_digest,
        profile_id=profile_id,
        profile_version_id=profile_version_id,
        profile_content_digest=profile_content_digest,
        golden_strategy=plan.strategy,
        control_keys=MAIN_AGENT_CONTROL_KEYS,
    )


def plan_rollout(
    db: Session,
    *,
    dry_run: bool = True,
    prefer_quick_stats: bool = True,
    allow_create_fixture: bool = True,
) -> RolloutReport:
    """Select golden package and prepare Profile publish (flags remain false)."""
    steps: list[str] = []
    plan = select_golden_package_plan(
        db,
        prefer_quick_stats=prefer_quick_stats,
        allow_create_fixture=allow_create_fixture and not dry_run,
    )
    steps.append(f"selected_strategy={plan.strategy}:{plan.reason}")

    if plan.package_id is None:
        if dry_run and allow_create_fixture:
            # Dry-run without writing: report intent to create fixture.
            return RolloutReport(
                operation="plan",
                dry_run=True,
                success=True,
                reason_code="plan_dry_run_fixture_pending",
                message=(
                    "dry-run: would create pure-read fixture package "
                    f"{GOLDEN_FIXTURE_CANONICAL_NAME} and publish golden Profile"
                ),
                expected=_expected_from_plan(
                    plan,
                    profile_id=None,
                    profile_version_id=None,
                    profile_content_digest=None,
                ),
                steps=steps,
                golden_plan=asdict(plan),
            )
        raise RolloutError(
            "golden_package_unavailable",
            "no golden package available; enable fixture creation or promote quick_stats",
        )

    if dry_run:
        profile = (
            db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one_or_none()
        )
        pub = None
        if profile is not None and profile.published_version_id is not None:
            pub = db.get(AssistantMainAgentProfileVersion, profile.published_version_id)
        expected = _expected_from_plan(
            plan,
            profile_id=profile.id if profile else None,
            profile_version_id=pub.id if pub else None,
            profile_content_digest=str(pub.content_digest) if pub else None,
        )
        return RolloutReport(
            operation="plan",
            dry_run=True,
            success=True,
            reason_code="plan_ok",
            message="dry-run plan complete; no flags changed",
            expected=expected,
            package_catalog_enabled=bool(
                _package_by_id(db, plan.package_id).catalog_enabled
            ),
            package_migration_state=str(
                _package_by_id(db, plan.package_id).migration_state
            ),
            profile_runtime_enabled=bool(profile.runtime_enabled) if profile else False,
            profile_migration_state=str(profile.migration_state) if profile else None,
            other_catalog_enabled_packages=_other_catalog_enabled(db, plan.package_id),
            steps=steps + ["dry_run_no_writes"],
            golden_plan=asdict(plan),
        )

    # Live plan: ensure package cutover publish pointer is current and Profile ready.
    package = _package_by_id(db, plan.package_id)
    skill_svc = AgentSkillService(db)
    # Ensure published pointer matches plan; do not mutate published rows.
    if package.published_version_id != plan.published_version_id:
        raise RolloutError(
            "package_version_drift",
            "package published version drifted before plan completion",
        )
    # Promote ownership to cutover only when enabling; plan leaves migration alone
    # unless already cutover. Publishing Profile with four controls + allowlist.
    profile_summary, profile_pub = publish_golden_profile(
        db, package_id=plan.package_id
    )
    steps.append(f"profile_published={profile_pub.id}:{profile_pub.content_digest[:12]}")
    # Confirm catalog still only disabled or the intended package.
    others = _other_catalog_enabled(db, plan.package_id)
    expected = _expected_from_plan(
        plan,
        profile_id=profile_summary.id,
        profile_version_id=profile_pub.id,
        profile_content_digest=profile_pub.content_digest,
    )
    # Re-read package flags (still false after plan).
    package = _package_by_id(db, plan.package_id)
    profile = _default_profile(db)
    return RolloutReport(
        operation="plan",
        dry_run=False,
        success=True,
        reason_code="plan_ok",
        message=(
            "golden package and Profile published; catalog_enabled and "
            "runtime_enabled remain false until enable"
        ),
        expected=expected,
        package_catalog_enabled=bool(package.catalog_enabled),
        package_migration_state=str(package.migration_state),
        profile_runtime_enabled=bool(profile.runtime_enabled),
        profile_migration_state=str(profile.migration_state),
        other_catalog_enabled_packages=others,
        steps=steps,
        golden_plan=asdict(plan),
    )


def enable_rollout(
    db: Session,
    *,
    dry_run: bool = False,
    prefer_quick_stats: bool = True,
    allow_create_fixture: bool = True,
    expected: RolloutExpectedState | None = None,
    require_probe: bool = False,
) -> RolloutReport:
    """Enable catalog + runtime for the golden path after checks.

    ``require_probe`` is optional: CI/scripted environments may skip live probe
    and leave model eligibility to runtime admission. Operator enablement in a
    production environment should set ``require_probe=True``.
    """
    steps: list[str] = []
    # Always re-plan to freeze current digests.
    if expected is None:
        plan_report = plan_rollout(
            db,
            dry_run=False if not dry_run else True,
            prefer_quick_stats=prefer_quick_stats,
            allow_create_fixture=allow_create_fixture,
        )
        if not plan_report.success:
            return plan_report
        expected = plan_report.expected
        steps.extend(plan_report.steps)
        if expected is None or expected.package_id is None:
            raise RolloutError("plan_incomplete", "plan did not produce expected digests")
        if dry_run:
            return RolloutReport(
                operation="enable",
                dry_run=True,
                success=True,
                reason_code="enable_dry_run",
                message="dry-run: would set package cutover+catalog and profile runtime",
                expected=expected,
                steps=steps + ["dry_run_no_flag_writes"],
                golden_plan=plan_report.golden_plan,
            )
    else:
        steps.append("using_provided_expected_digests")

    assert expected is not None
    if expected.package_id is None or expected.package_version_id is None:
        raise RolloutError("expected_package_missing", "expected package digests incomplete")
    if expected.profile_id is None or expected.profile_version_id is None:
        # Ensure profile exists when caller supplied package-only expected.
        if dry_run:
            return RolloutReport(
                operation="enable",
                dry_run=True,
                success=True,
                reason_code="enable_dry_run_profile_pending",
                message="dry-run: would publish golden Profile then enable flags",
                expected=expected,
                steps=steps,
            )
        plan_report = plan_rollout(
            db,
            dry_run=False,
            prefer_quick_stats=prefer_quick_stats,
            allow_create_fixture=allow_create_fixture,
        )
        expected = plan_report.expected
        steps.extend(plan_report.steps)
        if expected is None or expected.profile_version_id is None:
            raise RolloutError("profile_publish_failed", "could not publish golden Profile")

    package = _package_by_id(db, expected.package_id)
    version = db.get(AssistantSkillVersion, expected.package_version_id)
    if version is None or version.skill_package_id != package.id:
        raise RolloutError("package_version_unowned", "expected package version not owned")
    if str(version.version_digest or "") != str(expected.package_version_digest or ""):
        raise RolloutError(
            "package_digest_drift",
            "package version_digest drifted from expected",
        )
    if package.published_version_id != expected.package_version_id:
        raise RolloutError(
            "package_pointer_drift",
            "package published_version_id drifted from expected",
        )

    profile = _default_profile(db)
    if expected.profile_id is not None and profile.id != expected.profile_id:
        raise RolloutError("profile_id_mismatch", "default profile id mismatch")
    profile_version = db.get(
        AssistantMainAgentProfileVersion, expected.profile_version_id
    )
    if profile_version is None or profile_version.profile_id != profile.id:
        raise RolloutError("profile_version_unowned", "expected profile version not owned")
    if str(profile_version.content_digest or "") != str(
        expected.profile_content_digest or ""
    ):
        raise RolloutError(
            "profile_digest_drift",
            "profile content_digest drifted from expected",
        )
    if profile.published_version_id != expected.profile_version_id:
        # Idempotent path: if already pointing at matching digest publish, accept.
        current = (
            db.get(AssistantMainAgentProfileVersion, profile.published_version_id)
            if profile.published_version_id is not None
            else None
        )
        if (
            current is None
            or str(current.content_digest or "")
            != str(expected.profile_content_digest or "")
        ):
            raise RolloutError(
                "profile_pointer_drift",
                "profile published_version_id drifted from expected",
            )

    if require_probe:
        steps.append("probe_check_required")
        try:
            from app.assistant.main_agent.service import resolve_assistant_model_identity
            from app.config import get_settings

            settings = get_settings()
            snapshot = __import__(
                "app.assistant.skills.schemas", fromlist=["MainAgentProfileSnapshotV1"]
            ).MainAgentProfileSnapshotV1.model_validate(profile_version.snapshot or {})
            resolve_assistant_model_identity(
                db,
                requirements=snapshot.model_requirements,
                app_build_revision=str(
                    getattr(settings, "app_build_revision", None) or "plan04-dev"
                ),
            )
            steps.append("probe_eligible")
        except Exception as exc:
            raise RolloutError(
                "model_ineligible",
                f"current model probe ineligible: {type(exc).__name__}",
            ) from exc

    # Idempotent short-circuit when already enabled with matching digests.
    already = (
        bool(package.catalog_enabled)
        and str(package.migration_state) == "cutover"
        and bool(profile.runtime_enabled)
        and package.published_version_id == expected.package_version_id
        and profile.published_version_id
        in {expected.profile_version_id, profile.published_version_id}
    )
    if already:
        others = _other_catalog_enabled(db, package.id)
        if others:
            raise RolloutError(
                "other_packages_catalog_enabled",
                f"other catalog-enabled packages present: {others}",
            )
        return RolloutReport(
            operation="enable",
            dry_run=False,
            success=True,
            reason_code="enable_idempotent",
            message="golden path already enabled with matching digests",
            expected=expected,
            package_catalog_enabled=True,
            package_migration_state="cutover",
            profile_runtime_enabled=True,
            profile_migration_state=str(profile.migration_state),
            other_catalog_enabled_packages=others,
            steps=steps + ["idempotent_no_op"],
        )

    if dry_run:
        return RolloutReport(
            operation="enable",
            dry_run=True,
            success=True,
            reason_code="enable_dry_run",
            message="dry-run: would enable cutover catalog and runtime flags",
            expected=expected,
            package_catalog_enabled=bool(package.catalog_enabled),
            package_migration_state=str(package.migration_state),
            profile_runtime_enabled=bool(profile.runtime_enabled),
            profile_migration_state=str(profile.migration_state),
            other_catalog_enabled_packages=_other_catalog_enabled(db, package.id),
            steps=steps + ["dry_run_no_flag_writes"],
        )

    # Single-transaction enable: never leave package cutover without profile runtime.
    # Lock package then profile (UUID-independent fixed order by resource kind).
    try:
        package = (
            db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.id == package.id)
            .with_for_update()
            .one()
        )
        profile = (
            db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.id == profile.id)
            .with_for_update()
            .one()
        )

        # Re-validate under locks before any write.
        if package.published_version_id != expected.package_version_id:
            raise RolloutError(
                "package_version_drift",
                "published skill version drifted under lock",
            )
        pkg_version = db.get(AssistantSkillVersion, package.published_version_id)
        if (
            pkg_version is None
            or str(pkg_version.version_digest or "") != expected.package_version_digest
            or str(pkg_version.version_source) != "publish"
        ):
            raise RolloutError(
                "package_version_invalid",
                "published skill version missing, not publish, or digest drift",
            )
        if profile.published_version_id is None:
            raise RolloutError(
                "profile_unpublished",
                "profile has no published version under lock",
            )
        prof_version = db.get(
            AssistantMainAgentProfileVersion, profile.published_version_id
        )
        if (
            prof_version is None
            or str(prof_version.content_digest or "") != expected.profile_content_digest
            or str(prof_version.version_source) != "publish"
        ):
            raise RolloutError(
                "profile_version_invalid",
                "published profile version missing, not publish, or digest drift",
            )

        others = _other_catalog_enabled(db, package.id)
        if others:
            raise RolloutError(
                "other_packages_catalog_enabled",
                f"other catalog-enabled packages present: {others}",
            )

        package.catalog_enabled = True
        package.migration_state = "cutover"
        profile.runtime_enabled = True
        if str(profile.migration_state or "") != "cutover":
            profile.migration_state = "cutover"
        db.flush()
        # Final visibility check before commit.
        others = _other_catalog_enabled(db, package.id)
        if others:
            raise RolloutError(
                "other_packages_catalog_enabled",
                f"other catalog-enabled packages present after write: {others}",
            )
        db.commit()
        steps.append("atomic_package_and_profile_cutover")
    except RolloutError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise RolloutError(
            "enable_transaction_failed",
            f"atomic enable failed: {type(exc).__name__}",
        ) from exc

    package = _package_by_id(db, package.id)
    profile = _default_profile(db)
    others = _other_catalog_enabled(db, package.id)

    return RolloutReport(
        operation="enable",
        dry_run=False,
        success=True,
        reason_code="enable_ok",
        message="golden path enabled: package cutover+catalog, profile runtime",
        expected=expected,
        package_catalog_enabled=bool(package.catalog_enabled),
        package_migration_state=str(package.migration_state),
        profile_runtime_enabled=bool(profile.runtime_enabled),
        profile_migration_state=str(profile.migration_state),
        other_catalog_enabled_packages=others,
        steps=steps,
    )


def disable_rollout(
    db: Session,
    *,
    dry_run: bool = False,
    package_id: UUID | None = None,
) -> RolloutReport:
    """Set catalog_enabled and runtime_enabled false without deleting history."""
    steps: list[str] = []
    profile = (
        db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.is_default.is_(True))
        .one_or_none()
    )
    packages: list[AssistantSkillPackage] = []
    if package_id is not None:
        packages = [_package_by_id(db, package_id)]
    else:
        packages = (
            db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.catalog_enabled.is_(True))
            .order_by(AssistantSkillPackage.canonical_name.asc())
            .all()
        )
        # Also include known golden fixture even if already disabled (idempotent).
        if not packages:
            fixture = (
                db.query(AssistantSkillPackage)
                .filter(
                    AssistantSkillPackage.canonical_name == GOLDEN_FIXTURE_CANONICAL_NAME
                )
                .one_or_none()
            )
            if fixture is not None:
                packages = [fixture]

    expected = RolloutExpectedState(
        package_id=packages[0].id if packages else None,
        package_canonical_name=packages[0].canonical_name if packages else None,
        package_version_id=packages[0].published_version_id if packages else None,
        package_version_digest=None,
        package_content_digest=None,
        profile_id=profile.id if profile else None,
        profile_version_id=profile.published_version_id if profile else None,
        profile_content_digest=None,
        golden_strategy=None,
    )

    if dry_run:
        return RolloutReport(
            operation="disable",
            dry_run=True,
            success=True,
            reason_code="disable_dry_run",
            message="dry-run: would set catalog_enabled and runtime_enabled false",
            expected=expected,
            package_catalog_enabled=bool(packages[0].catalog_enabled) if packages else None,
            package_migration_state=str(packages[0].migration_state) if packages else None,
            profile_runtime_enabled=bool(profile.runtime_enabled) if profile else None,
            profile_migration_state=str(profile.migration_state) if profile else None,
            other_catalog_enabled_packages=_other_catalog_enabled(
                db, packages[0].id if packages else None
            ),
            steps=["dry_run_no_writes"],
        )

    skill_svc = AgentSkillService(db)
    for package in packages:
        # Preserve published_version_id / history; only clear aggregate flag.
        before_pub = package.published_version_id
        skill_svc.set_catalog_enabled(package.id, enabled=False)
        after = _package_by_id(db, package.id)
        if after.published_version_id != before_pub:
            raise RolloutError(
                "history_mutated",
                "disable must not repoint published_version_id",
            )
        steps.append(f"package_disabled={package.canonical_name}")

    if profile is not None:
        before_pub = profile.published_version_id
        MainAgentProfileService(db).set_runtime_enabled(profile.id, enabled=False)
        profile = _default_profile(db)
        if profile.published_version_id != before_pub:
            raise RolloutError(
                "history_mutated",
                "disable must not repoint profile published_version_id",
            )
        steps.append("profile_runtime_disabled")

    profile = (
        db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.is_default.is_(True))
        .one_or_none()
    )
    remaining = _other_catalog_enabled(db, None)
    return RolloutReport(
        operation="disable",
        dry_run=False,
        success=True,
        reason_code="disable_ok",
        message="aggregate flags disabled; version history preserved",
        expected=expected,
        package_catalog_enabled=False,
        package_migration_state=str(packages[0].migration_state) if packages else None,
        profile_runtime_enabled=bool(profile.runtime_enabled) if profile else False,
        profile_migration_state=str(profile.migration_state) if profile else None,
        other_catalog_enabled_packages=remaining,
        steps=steps,
    )


def run_rollout(
    db: Session,
    operation: RolloutOperation,
    *,
    dry_run: bool = False,
    prefer_quick_stats: bool = True,
    allow_create_fixture: bool = True,
    require_probe: bool = False,
    package_id: UUID | None = None,
) -> RolloutReport:
    """Dispatch plan|enable|disable and normalize RolloutError into a report."""
    try:
        if operation == "plan":
            return plan_rollout(
                db,
                dry_run=dry_run,
                prefer_quick_stats=prefer_quick_stats,
                allow_create_fixture=allow_create_fixture,
            )
        if operation == "enable":
            return enable_rollout(
                db,
                dry_run=dry_run,
                prefer_quick_stats=prefer_quick_stats,
                allow_create_fixture=allow_create_fixture,
                require_probe=require_probe,
            )
        if operation == "disable":
            return disable_rollout(db, dry_run=dry_run, package_id=package_id)
        raise RolloutError("unknown_operation", f"unknown operation: {operation}")
    except RolloutError as exc:
        return RolloutReport(
            operation=operation,
            dry_run=dry_run,
            success=False,
            reason_code=exc.reason_code,
            message=exc.message,
            steps=[f"failed:{exc.reason_code}"],
        )
    except ApiException as exc:
        return RolloutReport(
            operation=operation,
            dry_run=dry_run,
            success=False,
            reason_code=f"api_{exc.code}",
            message=str(exc.message),
            steps=[f"api_exception:{exc.code}"],
        )


__all__ = [
    "RolloutError",
    "RolloutExpectedState",
    "RolloutOperation",
    "RolloutReport",
    "disable_rollout",
    "enable_rollout",
    "plan_rollout",
    "run_rollout",
]
