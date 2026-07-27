"""Append-only Agent Skill package aggregate service (Plan 01 Task 4).

Does not publish executable catalog entries. Resource bytes are never included
in ordinary list/detail serialization.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Sequence
from uuid import UUID

# Shared mutex for any path that enables catalog visibility. Prevents two
# concurrent enable transactions from each observing "no other enabled package"
# and both succeeding. Transaction-scoped on PostgreSQL; no-op elsewhere.
_SKILL_CATALOG_ENABLE_LOCK_KEY = 2026071404

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.domain.contracts import ResolvedSkillRef, StoredSkillResource
from app.assistant.domain.digests import sha256_bytes
from app.assistant.skills.contracts import (
    ParsedSkillPackage,
    is_reserved_skill_lookup_name,
    normalize_skill_lookup_name,
    validate_canonical_skill_name,
)
from app.assistant.skills.package_io import export_skill_package, parse_skill_directory_files
from app.assistant.skills.models import (
    AssistantMainAgentProfile,
    AssistantMainAgentProfileVersion,
    AssistantSkillCapabilityBinding,
    AssistantSkillCapabilityDependency,
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
    AssistantSkillResourceBlob,
    AssistantSkillVersion,
    AssistantSkillVersionResource,
)
from app.assistant.skills.candidate_closure import (
    CandidateClosureError,
    resolve_skill_candidate_closure,
    resolved_bindings_from_closure,
)
from app.assistant.skills.resolution import (
    reconstruct_binding_snapshot,
)
from app.assistant.skills.schemas import (
    CreateSkillPackageCommand,
    DEFAULT_MAIN_AGENT_DISPLAY_NAME,
    DEFAULT_MAIN_AGENT_PROFILE_KEY,
    MainAgentProfileSnapshotV1,
    MainAgentProfileSummary,
    MainAgentProfileVersionDetail,
    MainAgentProfileVersionSummary,
    PublishMainAgentProfileCommand,
    PublishSkillVersionCommand,
    SaveMainAgentProfileDraftCommand,
    SaveSkillDraftCommand,
    SkillPackageAliasSummary,
    SkillPackageDetail,
    SkillPackageSummary,
    SkillResourceMetadata,
    SkillVersionDetail,
    SkillVersionSummary,
    default_main_agent_profile_snapshot,
)
from app.common.exceptions import ApiException

logger = logging.getLogger(__name__)

_CODE_CONFLICT_REVISION = 40994
_CODE_CONFLICT_REQUEST = 40997

# Aggregate-locked distinct referenced blob budget (in addition to per-version 25 MiB).
MAX_PACKAGE_DISTINCT_BLOB_BYTES = 256 * 1024 * 1024


def _frontmatter_json(parsed: ParsedSkillPackage) -> dict[str, Any]:
    return parsed.frontmatter.model_dump(by_alias=True, exclude_none=False)


def _manifest_json(parsed: ParsedSkillPackage) -> dict[str, Any] | None:
    if parsed.manifest is None:
        return None
    return parsed.manifest.model_dump(by_alias=True, exclude_none=False)


def _resource_index_json(parsed: ParsedSkillPackage) -> list[dict[str, Any]]:
    return [
        {
            "path": entry.path,
            "kind": entry.resource_kind,
            "mediaType": entry.media_type,
            "size": entry.byte_size,
            "sha256": entry.sha256,
        }
        for entry in parsed.resource_index
    ]


def _decode_text(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8")


def _display_name_for(parsed: ParsedSkillPackage) -> str:
    if parsed.manifest and parsed.manifest.display_name:
        return parsed.manifest.display_name
    return parsed.canonical_name


class AgentSkillService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Create / save
    # ------------------------------------------------------------------

    def create_native_package(
        self, command: CreateSkillPackageCommand
    ) -> SkillPackageDetail:
        parsed = command.parsed
        try:
            canonical = validate_canonical_skill_name(parsed.canonical_name)
        except ValueError as exc:
            raise ApiException(status_code=422, code=42290, message=str(exc)) from exc

        if is_reserved_skill_lookup_name(canonical):
            raise ApiException(
                status_code=409,
                code=40990,
                message=f"canonical skill name {canonical!r} is reserved",
            )

        version_name = command.version_name or "draft-1"
        origin = command.origin

        try:
            package = AssistantSkillPackage(
                canonical_name=canonical,
                display_name=_display_name_for(parsed),
                description=parsed.frontmatter.description,
                migration_state="native",
                catalog_enabled=False,
                is_system=False,
            )
            self.db.add(package)
            self.db.flush()

            self._reserve_aliases(
                package_id=package.id,
                canonical_name=canonical,
                legacy_aliases=list(parsed.manifest.legacy_aliases)
                if parsed.manifest
                else [],
            )
            self.db.flush()

            version = self._insert_draft_version(
                package=package,
                parsed=parsed,
                version_name=version_name,
                origin=origin,
                sequence_no=1,
            )
            package.draft_version_id = version.id
            package.display_name = _display_name_for(parsed)
            package.description = parsed.frontmatter.description
            self.db.commit()
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

        return self.get_package(package.id)

    def import_package(
        self,
        parsed: ParsedSkillPackage,
        *,
        actor_id: UUID | None,
        origin: str,
        admin_request_id: str | None = None,
        admin_request_digest: str | None = None,
        commit: bool = True,
    ) -> SkillPackageDetail:
        """Create-only import of a parsed package as a disabled native draft.

        Never merges, replaces, enables, or auto-publishes. Conflicting
        canonical names or aliases return ``40995``. ``actor_id`` is accepted
        for audit callers but is never mixed into content digests (no column
        yet; origin alone records the import channel on the draft version).

        Optional ``admin_request_id`` / ``admin_request_digest`` are stamped on
        the aggregate in the same transaction as package insert so Plan 09
        create/fork apply retries remain idempotent after durable success.

        When ``commit=False``, the package mutation is only flushed so a caller
        (ImportPreviewService.apply) can stamp preview consume fields in the
        same unit of work. On failure with ``commit=False`` this method does
        not roll back — the outer unit of work owns abort.
        """
        if origin != "import":
            raise ApiException(
                status_code=422,
                code=42292,
                message=f"import origin must be 'import', got {origin!r}",
            )
        # actor_id is intentionally unused in Plan 01 persistence; keep the
        # parameter so API layers can pass it without inventing digest fields.
        _ = actor_id
        if (admin_request_id is None) ^ (admin_request_digest is None):
            raise ApiException(
                status_code=422,
                code=42290,
                message=(
                    "admin_request_id and admin_request_digest must both be "
                    "provided or both omitted"
                ),
            )
        if admin_request_digest is not None and len(admin_request_digest) != 64:
            raise ApiException(
                status_code=422,
                code=42290,
                message="admin_request_digest must be a 64-char hex digest",
            )

        try:
            canonical = validate_canonical_skill_name(parsed.canonical_name)
        except ValueError as exc:
            raise ApiException(status_code=422, code=42290, message=str(exc)) from exc

        if is_reserved_skill_lookup_name(canonical):
            raise ApiException(
                status_code=409,
                code=40995,
                message=f"canonical skill name {canonical!r} is reserved",
            )

        # Pre-flight create-only conflicts → reserved 40995 (not create's 40990/40991).
        self._assert_import_namespace_free(
            canonical_name=canonical,
            legacy_aliases=list(parsed.manifest.legacy_aliases)
            if parsed.manifest
            else [],
        )

        try:
            package = AssistantSkillPackage(
                canonical_name=canonical,
                display_name=_display_name_for(parsed),
                description=parsed.frontmatter.description,
                migration_state="native",
                catalog_enabled=False,
                is_system=False,
            )
            self.db.add(package)
            self.db.flush()

            self._reserve_aliases(
                package_id=package.id,
                canonical_name=canonical,
                legacy_aliases=list(parsed.manifest.legacy_aliases)
                if parsed.manifest
                else [],
            )
            self.db.flush()

            version = self._insert_draft_version(
                package=package,
                parsed=parsed,
                version_name="draft-1",
                origin="import",
                sequence_no=1,
            )
            package.draft_version_id = version.id
            package.display_name = _display_name_for(parsed)
            package.description = parsed.frontmatter.description
            # Never auto-publish or enable catalog on import.
            package.published_version_id = None
            package.catalog_enabled = False
            # Stamp admin request CAS fields before the single commit so a
            # crash cannot leave a package without durable requestId evidence.
            if admin_request_id is not None and admin_request_digest is not None:
                package.last_admin_request_id = admin_request_id
                package.last_admin_request_digest = admin_request_digest
            if commit:
                self.db.commit()
            else:
                self.db.flush()
        except ApiException as exc:
            if commit:
                self.db.rollback()
            raise self._as_import_conflict(exc) from exc
        except IntegrityError as exc:
            if commit:
                self.db.rollback()
            # Preserve IntegrityError for flush-only callers so they can map
            # global requestId unique conflicts after outer rollback.
            if not commit:
                raise
            raise self._as_import_conflict(self._translate_integrity_error(exc)) from exc

        return self.get_package(package.id)

    def export_version(
        self,
        *,
        package_id: UUID,
        version_id: UUID,
    ) -> bytes:
        """Export one owned immutable version as a deterministic ZIP.

        Never resolves the aggregate's current/latest pointer implicitly.
        """
        package = self._get_package_or_404(package_id)
        version = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == version_id,
                AssistantSkillVersion.skill_package_id == package.id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"Skill version not found: {version_id}",
            )

        skill_md = (version.skill_md or "").encode("utf-8")
        mindatlas_yaml = (
            version.mindatlas_yaml.encode("utf-8")
            if version.mindatlas_yaml is not None
            else None
        )
        resources = self._load_stored_resources(version_id=version.id)
        return export_skill_package(
            package.canonical_name,
            skill_md=skill_md,
            mindatlas_yaml=mindatlas_yaml,
            resources=resources,
        )

    def save_draft(self, command: SaveSkillDraftCommand) -> SkillVersionSummary:
        """Append or re-point a draft with required aggregate revision CAS.

        When ``preserve_previous_resources`` is True, skill.md/yaml from the
        command are merged with bytes copied from the current draft version so
        a content-only edit cannot wipe package resources.

        Sequence: lock package → compare request ID/digest → compare expected
        revision → apply mutation → increment revision → stamp request evidence.
        """
        version_name = command.version_name or "draft"
        origin = command.origin

        try:
            package = self._lock_package(command.package_id)

            request_id = (command.request_id or "").strip()
            if not request_id:
                raise ApiException(
                    status_code=422,
                    code=42291,
                    message="requestId is required",
                )
            cas_payload = {
                "package_id": str(command.package_id),
                "version_name": version_name,
                "origin": origin,
                "content_digest": command.parsed.content_digest,
                "preserve_previous_resources": bool(
                    command.preserve_previous_resources
                ),
                "expected_aggregate_revision": int(
                    command.expected_aggregate_revision
                ),
            }
            body = json.dumps(
                {"operation": "save_draft", "payload": cas_payload},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            last_id = getattr(package, "last_admin_request_id", None)
            last_digest = getattr(package, "last_admin_request_digest", None)
            # Identical retry must short-circuit before revision CAS: the first
            # successful call already advanced aggregate_revision.
            if last_id == request_id:
                if last_digest == digest and package.draft_version_id is not None:
                    draft = (
                        self.db.query(AssistantSkillVersion)
                        .filter(
                            AssistantSkillVersion.id == package.draft_version_id
                        )
                        .one_or_none()
                    )
                    if draft is not None:
                        return self._version_summary(draft)
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                    details={"requestId": request_id},
                )

            current_rev = int(package.aggregate_revision or 0)
            if current_rev != int(command.expected_aggregate_revision):
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REVISION,
                    message=(
                        f"aggregate revision conflict: expected "
                        f"{command.expected_aggregate_revision}, current {current_rev}"
                    ),
                    details={
                        "expectedAggregateRevision": int(
                            command.expected_aggregate_revision
                        ),
                        "currentAggregateRevision": current_rev,
                        "packageId": str(package.id),
                    },
                )

            parsed = command.parsed
            if command.preserve_previous_resources and package.draft_version_id is not None:
                prev = (
                    self.db.query(AssistantSkillVersion)
                    .filter(
                        AssistantSkillVersion.id == package.draft_version_id,
                        AssistantSkillVersion.skill_package_id == package.id,
                    )
                    .one_or_none()
                )
                if prev is not None:
                    files: dict[str, bytes] = {
                        "SKILL.md": parsed.skill_md_bytes
                        if parsed.skill_md_bytes
                        else (prev.skill_md or "").encode("utf-8"),
                    }
                    yaml_bytes = parsed.mindatlas_yaml_bytes
                    if yaml_bytes is None and prev.mindatlas_yaml:
                        yaml_bytes = prev.mindatlas_yaml.encode("utf-8")
                    if yaml_bytes is not None:
                        files["mindatlas.yaml"] = yaml_bytes
                    for resource in self._load_stored_resources(version_id=prev.id):
                        # Client-supplied parsed resources (if any) win by path.
                        if resource.path not in {
                            r.path for r in (parsed.resources or ())
                        }:
                            files[resource.path] = resource.content
                    for resource in parsed.resources or ():
                        files[resource.path] = resource.content
                    parsed = parse_skill_directory_files(
                        files,
                        expected_root_name=package.canonical_name,
                    )

            try:
                canonical = validate_canonical_skill_name(parsed.canonical_name)
            except ValueError as exc:
                raise ApiException(
                    status_code=422, code=42290, message=str(exc)
                ) from exc

            if package.canonical_name != canonical:
                raise ApiException(
                    status_code=409,
                    code=40990,
                    message=(
                        f"canonical name is immutable: package has "
                        f"{package.canonical_name!r}, payload has {canonical!r}"
                    ),
                )

            # shadow -> native only on administrator/api edit; legacy origin keeps shadow.
            if package.migration_state == "shadow" and origin == "api":
                package.migration_state = "native"

            if parsed.manifest and parsed.manifest.legacy_aliases:
                self._append_legacy_aliases(
                    package_id=package.id,
                    legacy_aliases=list(parsed.manifest.legacy_aliases),
                )

            existing = (
                self.db.query(AssistantSkillVersion)
                .filter(
                    AssistantSkillVersion.skill_package_id == package.id,
                    AssistantSkillVersion.version_source == "save",
                    AssistantSkillVersion.content_digest == parsed.content_digest,
                )
                .one_or_none()
            )
            if existing is not None:
                package.draft_version_id = existing.id
                package.display_name = _display_name_for(parsed)
                package.description = parsed.frontmatter.description
                package.aggregate_revision = current_rev + 1
                package.last_admin_request_id = request_id
                package.last_admin_request_digest = digest
                self.db.commit()
                return self._version_summary(existing)

            next_seq = self._next_sequence(package.id)
            version = self._insert_draft_version(
                package=package,
                parsed=parsed,
                version_name=version_name,
                origin=origin,
                sequence_no=next_seq,
            )
            package.draft_version_id = version.id
            package.display_name = _display_name_for(parsed)
            package.description = parsed.frontmatter.description
            package.aggregate_revision = current_rev + 1
            package.last_admin_request_id = request_id
            package.last_admin_request_digest = digest
            self.db.commit()
            return self._version_summary(version)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(
        self,
        package_id: UUID,
        command: PublishSkillVersionCommand,
        *,
        durable_capability_keys: Sequence[str] | None = None,
        actor_principal: str | None = None,
    ) -> SkillVersionSummary:
        """Create an immutable publish version from an owned draft.

        Always inserts a new ``version_source=publish`` row. The draft is never
        mutated. Publish never auto-enables catalog.

        Sequence: lock package → compare request ID/digest (idempotent replay) →
        compare expected revision → apply mutation → always increment revision →
        stamp ``last_admin_request_*`` → commit. Gate consumption remains required
        where the live matrix already required it; CAS is additional integrity.

        Plan 09 gate matrix (server-enforced):
        - already ``catalog_enabled``: matching gate required in observe and enforce;
        - live-disabled: ungated bootstrap only in observe; enforce requires gateId;
        - gate-use is appended in the same transaction as the pointer advance.
        """
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            build_publish_gate_subject,
            current_build_revision,
            current_gate_environment_pins,
            publish_gate_mode,
            skill_catalog_pin_digest,
        )

        try:
            package = self._lock_package(package_id)
            if getattr(package, "archived_at", None) is not None:
                raise ApiException(
                    status_code=409,
                    code=40996,
                    message="cannot publish an archived skill package; unarchive first",
                )

            request_id = (command.request_id or "").strip()
            if not request_id:
                raise ApiException(
                    status_code=422,
                    code=42291,
                    message="requestId is required",
                )
            gate_id = getattr(command, "gate_id", None)
            cas_payload = {
                "package_id": str(package_id),
                "draft_version_id": str(command.draft_version_id),
                "gate_id": str(gate_id) if gate_id is not None else None,
                "expected_aggregate_revision": int(
                    command.expected_aggregate_revision
                ),
            }
            body = json.dumps(
                {"operation": "skill_publish", "payload": cas_payload},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            request_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            last_id = getattr(package, "last_admin_request_id", None)
            last_digest = getattr(package, "last_admin_request_digest", None)
            if last_id == request_id:
                if (
                    last_digest == request_digest
                    and package.published_version_id is not None
                ):
                    published = (
                        self.db.query(AssistantSkillVersion)
                        .filter(
                            AssistantSkillVersion.id == package.published_version_id
                        )
                        .one_or_none()
                    )
                    if published is not None:
                        return self._version_summary(published)
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                    details={"requestId": request_id},
                )

            current_rev = int(getattr(package, "aggregate_revision", 0) or 0)
            if current_rev != int(command.expected_aggregate_revision):
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REVISION,
                    message=(
                        f"aggregate revision conflict: expected "
                        f"{command.expected_aggregate_revision}, current {current_rev}"
                    ),
                    details={
                        "expectedAggregateRevision": int(
                            command.expected_aggregate_revision
                        ),
                        "currentAggregateRevision": current_rev,
                        "packageId": str(package.id),
                    },
                )

            # Live flag is decided before any write (Plan 09 matrix).
            live_enabled = bool(getattr(package, "catalog_enabled", False))
            draft = (
                self.db.query(AssistantSkillVersion)
                .filter(
                    AssistantSkillVersion.id == command.draft_version_id,
                    AssistantSkillVersion.skill_package_id == package.id,
                    AssistantSkillVersion.version_source == "save",
                )
                .one_or_none()
            )
            if draft is None:
                raise ApiException(
                    status_code=404,
                    code=40491,
                    message=(
                        f"owned draft version not found: {command.draft_version_id}"
                    ),
                )

            # Shared pure resolver — same closure evaluation admits against.
            # Flushes/commits nothing; durable keys freeze plan extensions when set.
            try:
                closure = resolve_skill_candidate_closure(
                    self.db,
                    package_id=package.id,
                    version_id=draft.id,
                    subject_kind="skill_draft",
                    durable_capability_keys=tuple(durable_capability_keys or ()),
                )
            except CandidateClosureError as exc:
                if exc.code == "skill_content_digest_drift":
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="draft content digest mismatch during publish reconstruction",
                    ) from exc
                raise ApiException(
                    status_code=404 if "not_found" in exc.code else 422,
                    code=40491 if "not_found" in exc.code else 42291,
                    message=str(exc),
                ) from exc
            ordered_bindings = resolved_bindings_from_closure(closure)
            set_digest = closure.binding_set_digest
            ver_digest = closure.version_digest

            # Resource rows still needed to copy blob refs onto the publish version.
            resource_rows = (
                self.db.query(AssistantSkillVersionResource, AssistantSkillResourceBlob)
                .join(
                    AssistantSkillResourceBlob,
                    AssistantSkillResourceBlob.id == AssistantSkillVersionResource.blob_id,
                )
                .filter(AssistantSkillVersionResource.skill_version_id == draft.id)
                .all()
            )

            # Rebuild candidate gate subject under lock (server-derived closure).
            # Content/binding digests always come from the draft. Environment
            # pins are recomputed independently (never copied from the gate row)
            # so compare fails closed on env drift.
            gate_svc = PublishGateService(self.db)
            mode = publish_gate_mode()
            # gate_id already resolved above for CAS payload.
            subject_kind = "skill_draft"
            subject_version_id = draft.id
            # Dataset ids: client gate_subject pins if present; else recompute
            # from qualifying eval runs (evidence), never from gate subject fields.
            dataset_ids = self._gate_dataset_version_ids_from_command(command)
            if dataset_ids is None and gate_id is not None:
                dataset_ids = self._dataset_ids_from_gate_evidence(gate_svc, gate_id)
            catalog_digest = skill_catalog_pin_digest(
                package_id=package.id,
                canonical_name=str(package.canonical_name),
                published_version_id=package.published_version_id,
                catalog_enabled=live_enabled,
            )
            # Server-derived build pin only — never trust client gate_subject.build_revision
            # (same contract as enable path via current_build_revision()).
            pins = current_gate_environment_pins(
                self.db,
                catalog_digest=catalog_digest,
                dataset_version_ids=dataset_ids,
                build_revision=current_build_revision(),
            )
            subject = build_publish_gate_subject(
                kind=subject_kind,
                aggregate_id=package.id,
                version_id=subject_version_id,
                content_digest=str(draft.content_digest),
                binding_digest=str(set_digest),
                profile_digest=pins.profile_digest,
                catalog_digest=pins.catalog_digest,
                dataset_version_ids=pins.dataset_version_ids,
                runtime_contract_version=pins.runtime_contract_version,
                policy_version=pins.policy_version,
                threshold_version=pins.threshold_version,
                build_revision=pins.build_revision,
            )
            # Client-supplied subject (if any) must match recomputed content/binding.
            if getattr(command, "gate_subject", None):
                drifts = self._gate_subject_client_drifts(command.gate_subject, subject)
                if drifts:
                    raise ApiException(
                        status_code=409,
                        code=40982,
                        message=f"client gate_subject drifted from server closure: {drifts}",
                        details={"type": "gate_subject_drift", "details": {"drifts": drifts}},
                    )

            sequence_no = self._next_sequence(package.id)
            publish_version = AssistantSkillVersion(
                skill_package_id=package.id,
                sequence_no=sequence_no,
                version_name=f"publish-{sequence_no}",
                version_source="publish",
                source_draft_version_id=draft.id,
                origin=draft.origin,
                skill_md=draft.skill_md,
                mindatlas_yaml=draft.mindatlas_yaml,
                frontmatter=draft.frontmatter,
                extension_manifest=draft.extension_manifest,
                resource_index=draft.resource_index,
                skill_md_digest=draft.skill_md_digest,
                manifest_digest=draft.manifest_digest,
                resource_index_digest=draft.resource_index_digest,
                content_digest=draft.content_digest,
                binding_set_digest=set_digest,
                version_digest=ver_digest,
            )
            self.db.add(publish_version)
            self.db.flush()

            # Copy resource references deterministically.
            for resource, blob in sorted(resource_rows, key=lambda item: item[0].path):
                self.db.add(
                    AssistantSkillVersionResource(
                        skill_version_id=publish_version.id,
                        path=resource.path,
                        resource_kind=resource.resource_kind,
                        media_type=resource.media_type,
                        byte_size=resource.byte_size,
                        sha256=resource.sha256,
                        blob_id=blob.id,
                        executable=False,
                    )
                )

            # Insert resolved bindings + dependencies in canonical order.
            binding_rows: list[AssistantSkillCapabilityBinding] = []
            for ordinal, resolved in enumerate(ordered_bindings):
                binding_row = AssistantSkillCapabilityBinding(
                    skill_version_id=publish_version.id,
                    ordinal=ordinal,
                    capability_type=resolved.capability_type,
                    capability_key=resolved.capability_key,
                    resolution_status="resolved",
                    target_identity=resolved.target_identity,
                    resolved_tool_id=resolved.resolved_tool_id,
                    resolved_workflow_version_id=resolved.resolved_workflow_version_id,
                    resolved_agent_version_id=resolved.resolved_agent_version_id,
                    resolved_revision=resolved.resolved_revision,
                    input_schema_digest=resolved.input_schema_digest,
                    output_schema_digest=resolved.output_schema_digest,
                    config_digest=resolved.config_digest,
                    executable_revision=resolved.executable_revision,
                    resolution_digest=resolved.resolution_digest,
                    dependency_closure_digest=resolved.dependency_closure_digest,
                    binding_contract_digest=resolved.binding_contract_digest,
                    resolution_snapshot=resolved.resolution_snapshot,
                )
                self.db.add(binding_row)
                self.db.flush()
                binding_rows.append(binding_row)
                for dep in sorted(resolved.dependencies, key=lambda d: d.ordinal):
                    self.db.add(
                        AssistantSkillCapabilityDependency(
                            binding_id=binding_row.id,
                            ordinal=dep.ordinal,
                            dependency_path=dep.dependency_path,
                            dependency_type=dep.dependency_type,
                            target_identity=dep.target_identity,
                            resolved_tool_id=dep.resolved_tool_id,
                            resolved_workflow_version_id=dep.resolved_workflow_version_id,
                            resolved_agent_version_id=dep.resolved_agent_version_id,
                            resolved_model_id=dep.resolved_model_id,
                            target_revision=dep.target_revision,
                            input_schema_digest=dep.input_schema_digest,
                            output_schema_digest=dep.output_schema_digest,
                            resolution_digest=dep.resolution_digest,
                            dependency_digest=dep.dependency_digest,
                            resolution_snapshot=dep.resolution_snapshot,
                        )
                    )
            self.db.flush()

            # Re-read/reconstruct every lossless snapshot and verify digests.
            for binding_row, resolved in zip(binding_rows, ordered_bindings, strict=True):
                dep_rows = (
                    self.db.query(AssistantSkillCapabilityDependency)
                    .filter(AssistantSkillCapabilityDependency.binding_id == binding_row.id)
                    .order_by(AssistantSkillCapabilityDependency.ordinal.asc())
                    .all()
                )
                reconstructed = reconstruct_binding_snapshot(binding_row, dep_rows)
                if reconstructed.get("bindingContractDigest") != resolved.binding_contract_digest:
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="published binding_contract_digest reconstruction mismatch",
                    )
                if reconstructed.get("dependencyClosureDigest") != resolved.dependency_closure_digest:
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="published dependency_closure_digest reconstruction mismatch",
                    )

            # Gate consume (or ungated non-live bootstrap) before pointer advance.
            actor = actor_principal or "system:skill-publish"
            try:
                gate_svc.enforce_or_bootstrap_publish(
                    live_enabled=live_enabled,
                    gate_id=gate_id,
                    subject=subject,
                    action="skill_publish",
                    aggregate_id=package.id,
                    resulting_version_id=publish_version.id,
                    actor_principal=actor,
                    request_id=str(request_id),
                    aggregate_revision=current_rev,
                    mode=mode,
                )
            except PublishGateError as exc:
                raise exc.to_api_exception() from exc

            package.published_version_id = publish_version.id
            # Never auto-enable; preserve existing live flag (enabled requires gate).
            if not live_enabled:
                package.catalog_enabled = False
            # Always advance CAS so draft/publish observers see pointer changes.
            package.aggregate_revision = current_rev + 1
            package.last_admin_request_id = request_id
            package.last_admin_request_digest = request_digest
            self.db.commit()
            return self._version_summary(publish_version)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    def _gate_dataset_version_ids_from_command(
        self, command: Any
    ) -> tuple[UUID, ...] | None:
        """Extract dataset version ids from optional client gate_subject, else None."""
        raw = getattr(command, "gate_subject", None) or {}
        ids = raw.get("dataset_version_ids") or raw.get("datasetVersionIds") or ()
        if ids:
            return tuple(UUID(str(x)) for x in ids)
        return None

    def _dataset_ids_from_gate_evidence(
        self, gate_svc: Any, gate_id: UUID
    ) -> tuple[UUID, ...] | None:
        """Recompute dataset pins from qualifying eval runs (not gate subject fields)."""
        gate = gate_svc.get_gate(gate_id)
        if gate is None:
            return None
        seen: list[UUID] = []
        for rid in gate.qualifying_eval_run_ids or []:
            run = gate_svc.repo.get_run(UUID(str(rid)))
            if run is None:
                continue
            for ds in run.dataset_version_ids or []:
                u = UUID(str(ds))
                if u not in seen:
                    seen.append(u)
        return tuple(seen) if seen else None

    def _gate_subject_client_drifts(
        self, client: dict[str, Any], subject: Any
    ) -> list[str]:
        """Compare optional client gate_subject digests to server-recomputed subject."""
        drifts: list[str] = []
        sub = subject.subject
        client_sub = client.get("subject") or client
        mapping = [
            ("content_digest", "contentDigest", sub.content_digest),
            ("resolved_binding_digest", "resolvedBindingDigest", sub.resolved_binding_digest),
            ("content_digest", "subject_content_digest", sub.content_digest),
            ("binding_digest", "subject_binding_digest", sub.resolved_binding_digest),
        ]
        # Only flag when client explicitly provided a digest that mismatches.
        for snake, camel, expected in mapping:
            for key in (snake, camel):
                if key in client_sub or key in client:
                    got = client_sub.get(key, client.get(key))
                    if got is not None and str(got).lower() != str(expected).lower():
                        drifts.append(snake)
                        break
        return sorted(set(drifts))

    def _acquire_catalog_enable_lock(self) -> None:
        """Serialize catalog-enable across sessions (PostgreSQL advisory xact lock)."""
        bind = self.db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "postgresql":
            return
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _SKILL_CATALOG_ENABLE_LOCK_KEY},
        )

    def set_catalog_enabled(
        self,
        package_id: UUID,
        *,
        enabled: bool,
        expected_published_version_id: UUID | None = None,
        expected_version_digest: str | None = None,
        migration_state: str | None = None,
        gate_id: UUID | None = None,
        request_id: str | None = None,
        actor_principal: str | None = None,
    ) -> SkillPackageSummary:
        """Toggle package aggregate ``catalog_enabled`` without mutating versions.

        Enabling requires a published version **and** a fresh matching promotion
        gate (observe and enforce). Disabling remains ungated. Optional expected
        digests make the operation fail closed on concurrent pointer/content drift.
        """
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            build_publish_gate_subject,
            current_gate_environment_pins,
            skill_catalog_pin_digest,
        )

        try:
            if enabled:
                # All enable entrypoints share this mutex so concurrent enablers
                # cannot both observe an empty catalog and commit.
                self._acquire_catalog_enable_lock()
            package = self._lock_package(package_id)
            if enabled and getattr(package, "archived_at", None) is not None:
                raise ApiException(
                    status_code=409,
                    code=40996,
                    message="cannot enable catalog for an archived skill package",
                )
            if enabled:
                if package.published_version_id is None:
                    raise ApiException(
                        status_code=422,
                        code=42291,
                        message="cannot enable catalog without a published skill version",
                    )
                version = self.db.get(AssistantSkillVersion, package.published_version_id)
                if version is None or version.skill_package_id != package.id:
                    raise ApiException(
                        status_code=422,
                        code=42291,
                        message="published skill version missing or unowned",
                    )
                if str(version.version_source) != "publish":
                    raise ApiException(
                        status_code=422,
                        code=42291,
                        message="catalog enable requires version_source=publish",
                    )
                if (
                    expected_published_version_id is not None
                    and version.id != expected_published_version_id
                ):
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="published skill version drifted during enable",
                    )
                if (
                    expected_version_digest is not None
                    and str(version.version_digest or "") != expected_version_digest
                ):
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="published skill version_digest drifted during enable",
                    )
                # Plan 09: close ungated enable path — require gate + consume.
                if gate_id is None:
                    raise ApiException(
                        status_code=409,
                        code=40980,
                        message=(
                            "matching promotion gate required for catalog enable; "
                            "use admin gated enable_catalog or supply gate_id"
                        ),
                        details={
                            "type": "gate_required",
                            "details": {"action": "skill_catalog_enable"},
                        },
                    )
                gate_svc = PublishGateService(self.db)
                content_digest = str(version.content_digest or ("0" * 64))
                binding_digest = str(version.binding_set_digest or ("0" * 64))
                dataset_ids = self._dataset_ids_from_gate_evidence(gate_svc, gate_id)
                catalog_digest = skill_catalog_pin_digest(
                    package_id=package.id,
                    canonical_name=str(package.canonical_name),
                    published_version_id=package.published_version_id,
                    content_digest=content_digest,
                )
                pins = current_gate_environment_pins(
                    self.db,
                    catalog_digest=catalog_digest,
                    dataset_version_ids=dataset_ids,
                )
                subject = build_publish_gate_subject(
                    kind="skill_version",
                    aggregate_id=package.id,
                    version_id=package.published_version_id,
                    content_digest=content_digest,
                    binding_digest=binding_digest,
                    profile_digest=pins.profile_digest,
                    catalog_digest=pins.catalog_digest,
                    dataset_version_ids=pins.dataset_version_ids,
                    runtime_contract_version=pins.runtime_contract_version,
                    policy_version=pins.policy_version,
                    threshold_version=pins.threshold_version,
                    build_revision=pins.build_revision,
                )
                actor = actor_principal or "system:set-catalog-enabled"
                rid = request_id or f"set-catalog-enabled:{package.id}:{gate_id}"
                try:
                    gate_svc.enforce_enable(
                        gate_id=gate_id,
                        subject=subject,
                        action="skill_catalog_enable",
                        aggregate_id=package.id,
                        resulting_version_id=package.published_version_id,
                        actor_principal=actor,
                        request_id=str(rid),
                        aggregate_revision=int(
                            getattr(package, "aggregate_revision", 0) or 0
                        ),
                    )
                except PublishGateError as exc:
                    raise exc.to_api_exception() from exc
            if migration_state is not None:
                allowed = {"shadow", "native", "cutover"}
                if migration_state not in allowed:
                    raise ApiException(
                        status_code=422,
                        code=42291,
                        message=f"invalid migration_state: {migration_state}",
                    )
                current = str(package.migration_state or "")
                if current in {"native", "cutover"} and migration_state == "shadow":
                    raise ApiException(
                        status_code=422,
                        code=42291,
                        message=(
                            f"cannot demote migration_state from {current} to "
                            f"{migration_state}"
                        ),
                    )
                package.migration_state = migration_state
            package.catalog_enabled = bool(enabled)
            if enabled:
                if getattr(package, "catalog_enabled_at", None) is None:
                    from app.common.time import utcnow

                    package.catalog_enabled_at = utcnow()
                    if getattr(package, "catalog_enabled_by", None) is None:
                        package.catalog_enabled_by = actor_principal or "system"
                if hasattr(package, "aggregate_revision"):
                    package.aggregate_revision = int(package.aggregate_revision or 0) + 1
            else:
                package.catalog_enabled_at = None
                package.catalog_enabled_by = None
            self.db.commit()
            return self._package_summary(package)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_packages(self) -> list[SkillPackageSummary]:
        packages = (
            self.db.query(AssistantSkillPackage)
            .order_by(AssistantSkillPackage.canonical_name.asc())
            .all()
        )
        return [self._package_summary(pkg) for pkg in packages]

    def get_package(self, package_id: UUID) -> SkillPackageDetail:
        package = self._get_package_or_404(package_id)
        aliases = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == package.id)
            .order_by(AssistantSkillPackageAlias.created_at.asc())
            .all()
        )
        summary = self._package_summary(package)
        return SkillPackageDetail(
            **summary.model_dump(),
            aliases=[
                SkillPackageAliasSummary(
                    id=a.id,
                    alias=a.alias,
                    normalized_alias=a.normalized_alias,
                    alias_type=a.alias_type,  # type: ignore[arg-type]
                    created_at=a.created_at,
                    disabled_at=getattr(a, "disabled_at", None),
                    disabled_by=getattr(a, "disabled_by", None),
                )
                for a in aliases
            ],
        )

    def list_versions(self, package_id: UUID) -> list[SkillVersionSummary]:
        self._get_package_or_404(package_id)
        versions = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == package_id)
            .order_by(AssistantSkillVersion.sequence_no.asc())
            .all()
        )
        return [self._version_summary(v) for v in versions]

    def get_version(self, package_id: UUID, version_id: UUID) -> SkillVersionDetail:
        self._get_package_or_404(package_id)
        version = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == version_id,
                AssistantSkillVersion.skill_package_id == package_id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"Skill version not found: {version_id}",
            )
        resources = (
            self.db.query(AssistantSkillVersionResource)
            .filter(AssistantSkillVersionResource.skill_version_id == version.id)
            .order_by(AssistantSkillVersionResource.path.asc())
            .all()
        )
        summary = self._version_summary(version)
        return SkillVersionDetail(
            **summary.model_dump(),
            frontmatter=version.frontmatter or {},
            extension_manifest=version.extension_manifest,
            resource_index=list(version.resource_index or []),
            resources=[
                SkillResourceMetadata(
                    path=r.path,
                    resource_kind=r.resource_kind,  # type: ignore[arg-type]
                    media_type=r.media_type,
                    byte_size=r.byte_size,
                    sha256=r.sha256,
                    executable=bool(r.executable),
                )
                for r in resources
            ],
            skill_md=version.skill_md,
            mindatlas_yaml=version.mindatlas_yaml,
        )

    def get_resource_bytes(
        self, package_id: UUID, version_id: UUID, path: str
    ) -> bytes:
        self._get_package_or_404(package_id)
        version = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == version_id,
                AssistantSkillVersion.skill_package_id == package_id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"Skill version not found: {version_id}",
            )
        resource = (
            self.db.query(AssistantSkillVersionResource)
            .filter(
                AssistantSkillVersionResource.skill_version_id == version.id,
                AssistantSkillVersionResource.path == path,
            )
            .one_or_none()
        )
        if resource is None:
            raise ApiException(
                status_code=404,
                code=40492,
                message=f"Skill resource not found: {path}",
            )
        blob = self.db.get(AssistantSkillResourceBlob, resource.blob_id)
        if blob is None:
            raise ApiException(
                status_code=404,
                code=40492,
                message=f"Skill resource blob missing for: {path}",
            )
        # Verify exact bytes match stored digest/size and content digests.
        content = bytes(blob.content)
        if blob.byte_size != resource.byte_size or blob.sha256 != resource.sha256:
            raise ApiException(
                status_code=409,
                code=40993,
                message="resource blob metadata mismatch",
            )
        if len(content) != blob.byte_size:
            raise ApiException(
                status_code=409,
                code=40993,
                message="resource blob size mismatch",
            )
        if sha256_bytes(content) != blob.sha256:
            raise ApiException(
                status_code=409,
                code=40993,
                message="resource blob content digest mismatch",
            )
        return content

    def resolve_published_alias(self, name: str) -> ResolvedSkillRef:
        try:
            normalized = normalize_skill_lookup_name(name)
        except (TypeError, ValueError) as exc:
            raise ApiException(
                status_code=404,
                code=40490,
                message=f"Skill package not found for name: {name!r}",
            ) from exc

        try:
            alias = (
                self.db.query(AssistantSkillPackageAlias)
                .filter(AssistantSkillPackageAlias.normalized_alias == normalized)
                .one_or_none()
            )
            if alias is None or getattr(alias, "disabled_at", None) is not None:
                raise ApiException(
                    status_code=404,
                    code=40490,
                    message=f"Skill package not found for name: {name!r}",
                )

            package = (
                self.db.query(AssistantSkillPackage)
                .filter(AssistantSkillPackage.id == alias.skill_package_id)
                .with_for_update()
                .one_or_none()
            )
            if package is None:
                raise ApiException(
                    status_code=404,
                    code=40490,
                    message=f"Skill package not found for name: {name!r}",
                )
            # Archived packages cannot be recalled via alias resolution.
            if getattr(package, "archived_at", None) is not None:
                raise ApiException(
                    status_code=404,
                    code=40490,
                    message=f"Skill package not found for name: {name!r}",
                )
            if package.published_version_id is None:
                raise ApiException(
                    status_code=404,
                    code=40491,
                    message=(
                        f"Skill package {package.canonical_name!r} has no published version"
                    ),
                )

            version = (
                self.db.query(AssistantSkillVersion)
                .filter(
                    AssistantSkillVersion.id == package.published_version_id,
                    AssistantSkillVersion.skill_package_id == package.id,
                    AssistantSkillVersion.version_source == "publish",
                )
                .one_or_none()
            )
            if version is None:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="published_version_id does not reference an owned publish version",
                )
            if version.version_digest is None:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="published version missing version_digest",
                )

            # Build frozen ref inside the transaction; do not re-follow pointer later.
            return ResolvedSkillRef(
                package_id=package.id,
                version_id=version.id,
                canonical_name=package.canonical_name,
                sequence=version.sequence_no,
                content_digest=version.content_digest,
                version_digest=version.version_digest,
                requested_name_normalized=normalized,
                resolved_via_alias_id=alias.id,
            )
        except ApiException:
            raise

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_package_or_404(self, package_id: UUID) -> AssistantSkillPackage:
        package = self.db.get(AssistantSkillPackage, package_id)
        if package is None:
            raise ApiException(
                status_code=404,
                code=40490,
                message=f"Skill package not found: {package_id}",
            )
        return package

    def _lock_package(self, package_id: UUID) -> AssistantSkillPackage:
        package = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.id == package_id)
            .with_for_update()
            .one_or_none()
        )
        if package is None:
            raise ApiException(
                status_code=404,
                code=40490,
                message=f"Skill package not found: {package_id}",
            )
        return package

    def _next_sequence(self, package_id: UUID) -> int:
        current = (
            self.db.query(func.max(AssistantSkillVersion.sequence_no))
            .filter(AssistantSkillVersion.skill_package_id == package_id)
            .scalar()
        )
        return int(current or 0) + 1

    def _reserve_aliases(
        self,
        *,
        package_id: UUID,
        canonical_name: str,
        legacy_aliases: list[str],
    ) -> None:
        # Canonical alias first.
        self._insert_alias_row(
            package_id=package_id,
            alias=canonical_name,
            alias_type="canonical",
        )
        seen_normalized = {normalize_skill_lookup_name(canonical_name)}
        for raw in legacy_aliases:
            try:
                normalized = normalize_skill_lookup_name(raw)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=42291, message=str(exc)
                ) from exc
            if is_reserved_skill_lookup_name(raw):
                raise ApiException(
                    status_code=409,
                    code=40991,
                    message=f"alias {raw!r} is reserved",
                )
            if normalized in seen_normalized:
                # Duplicate within the same package request is a no-op after first.
                continue
            seen_normalized.add(normalized)
            self._insert_alias_row(
                package_id=package_id,
                alias=raw,
                alias_type="legacy",
            )

    def _append_legacy_aliases(
        self, *, package_id: UUID, legacy_aliases: list[str]
    ) -> None:
        existing = {
            row.normalized_alias
            for row in self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == package_id)
            .all()
        }
        for raw in legacy_aliases:
            try:
                normalized = normalize_skill_lookup_name(raw)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=42291, message=str(exc)
                ) from exc
            if is_reserved_skill_lookup_name(raw):
                raise ApiException(
                    status_code=409,
                    code=40991,
                    message=f"alias {raw!r} is reserved",
                )
            if normalized in existing:
                continue
            # Collision with another package's alias is caught by unique index.
            self._insert_alias_row(
                package_id=package_id,
                alias=raw,
                alias_type="legacy",
            )
            existing.add(normalized)

    def _insert_alias_row(
        self,
        *,
        package_id: UUID,
        alias: str,
        alias_type: str,
    ) -> AssistantSkillPackageAlias:
        try:
            normalized = normalize_skill_lookup_name(alias)
        except (TypeError, ValueError) as exc:
            raise ApiException(status_code=422, code=42291, message=str(exc)) from exc
        if is_reserved_skill_lookup_name(alias) and alias_type != "canonical":
            # Canonical reservation of reserved names is already rejected earlier.
            raise ApiException(
                status_code=409,
                code=40991,
                message=f"alias {alias!r} is reserved",
            )
        # Pre-check for clearer conflict codes (unique index remains authoritative).
        conflict = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.normalized_alias == normalized)
            .one_or_none()
        )
        if conflict is not None:
            if conflict.skill_package_id == package_id:
                return conflict
            if alias_type == "canonical":
                raise ApiException(
                    status_code=409,
                    code=40990,
                    message=f"canonical name {alias!r} already reserved",
                )
            raise ApiException(
                status_code=409,
                code=40991,
                message=f"alias {alias!r} already reserved",
            )
        row = AssistantSkillPackageAlias(
            skill_package_id=package_id,
            alias=alias,
            normalized_alias=normalized,
            alias_type=alias_type,
        )
        self.db.add(row)
        return row

    def _insert_draft_version(
        self,
        *,
        package: AssistantSkillPackage,
        parsed: ParsedSkillPackage,
        version_name: str,
        origin: str,
        sequence_no: int,
        extension_manifest_extra: dict[str, Any] | None = None,
    ) -> AssistantSkillVersion:
        """Insert a new immutable save-row draft.

        ``extension_manifest_extra`` is merged into the parsed extension manifest
        at INSERT time only (e.g. restore provenance). Never used to UPDATE an
        existing version row — Plan 01 immutability rejects version UPDATE on PG.
        """
        manifest = _manifest_json(parsed)
        if extension_manifest_extra:
            base = dict(manifest or {})
            base.update(extension_manifest_extra)
            manifest = base
        version = AssistantSkillVersion(
            skill_package_id=package.id,
            sequence_no=sequence_no,
            version_name=version_name,
            version_source="save",
            source_draft_version_id=None,
            origin=origin,
            skill_md=_decode_text(parsed.skill_md_bytes) or "",
            mindatlas_yaml=_decode_text(parsed.mindatlas_yaml_bytes),
            frontmatter=_frontmatter_json(parsed),
            extension_manifest=manifest,
            resource_index=_resource_index_json(parsed),
            skill_md_digest=parsed.skill_md_digest,
            manifest_digest=parsed.manifest_digest,
            resource_index_digest=parsed.resource_index_digest,
            content_digest=parsed.content_digest,
            binding_set_digest=None,
            version_digest=None,
        )
        self.db.add(version)
        self.db.flush()

        self._insert_resources(package_id=package.id, version=version, parsed=parsed)
        self._insert_unresolved_bindings(version=version, parsed=parsed)
        self.db.flush()
        return version

    def _insert_resources(
        self,
        *,
        package_id: UUID,
        version: AssistantSkillVersion,
        parsed: ParsedSkillPackage,
    ) -> None:
        # Deterministic path order.
        resources = sorted(parsed.resources, key=lambda r: r.path)
        for item in resources:
            blob = self._get_or_create_blob(
                sha256=item.sha256,
                content=item.content,
                byte_size=item.byte_size,
            )
            self.db.add(
                AssistantSkillVersionResource(
                    skill_version_id=version.id,
                    path=item.path,
                    resource_kind=item.resource_kind,
                    media_type=item.media_type,
                    byte_size=item.byte_size,
                    sha256=item.sha256,
                    blob_id=blob.id,
                    executable=False,
                )
            )
        # Enforce aggregate distinct-blob budget after staging references.
        self._enforce_package_blob_quota(package_id)

    def _get_or_create_blob(
        self, *, sha256: str, content: bytes, byte_size: int
    ) -> AssistantSkillResourceBlob:
        if byte_size != len(content):
            raise ApiException(
                status_code=422,
                code=42292,
                message="resource byte_size does not match content length",
            )
        existing = (
            self.db.query(AssistantSkillResourceBlob)
            .filter(
                AssistantSkillResourceBlob.sha256 == sha256,
                AssistantSkillResourceBlob.byte_size == byte_size,
            )
            .one_or_none()
        )
        if existing is not None:
            if bytes(existing.content) != content:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob digest collision with different bytes",
                )
            return existing

        blob = AssistantSkillResourceBlob(
            sha256=sha256,
            byte_size=byte_size,
            content=content,
        )
        # Use a SAVEPOINT so a uniqueness race does not abort the outer unit of work.
        try:
            with self.db.begin_nested():
                self.db.add(blob)
                self.db.flush()
            return blob
        except IntegrityError:
            existing = (
                self.db.query(AssistantSkillResourceBlob)
                .filter(
                    AssistantSkillResourceBlob.sha256 == sha256,
                    AssistantSkillResourceBlob.byte_size == byte_size,
                )
                .one_or_none()
            )
            if existing is None:
                raise ApiException(
                    status_code=409,
                    code=40992,
                    message="resource blob insert conflict; retry",
                )
            if bytes(existing.content) != content:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob digest collision with different bytes",
                )
            return existing

    def _enforce_package_blob_quota(self, package_id: UUID) -> None:
        # Sum distinct blob sizes referenced by any version of this package.
        # Use subquery of distinct blob_ids then join for sizes.
        blob_ids = (
            self.db.query(AssistantSkillVersionResource.blob_id)
            .join(
                AssistantSkillVersion,
                AssistantSkillVersion.id
                == AssistantSkillVersionResource.skill_version_id,
            )
            .filter(AssistantSkillVersion.skill_package_id == package_id)
            .distinct()
            .subquery()
        )
        total = (
            self.db.query(func.coalesce(func.sum(AssistantSkillResourceBlob.byte_size), 0))
            .filter(AssistantSkillResourceBlob.id.in_(select(blob_ids.c.blob_id)))
            .scalar()
        )
        total_bytes = int(total or 0)
        if total_bytes > MAX_PACKAGE_DISTINCT_BLOB_BYTES:
            raise ApiException(
                status_code=413,
                code=41391,
                message=(
                    f"package distinct resource blob bytes {total_bytes} exceed "
                    f"limit {MAX_PACKAGE_DISTINCT_BLOB_BYTES}"
                ),
            )

    def _insert_unresolved_bindings(
        self, *, version: AssistantSkillVersion, parsed: ParsedSkillPackage
    ) -> None:
        if parsed.manifest is None:
            return
        for ordinal, cap in enumerate(parsed.manifest.capabilities):
            self.db.add(
                AssistantSkillCapabilityBinding(
                    skill_version_id=version.id,
                    ordinal=ordinal,
                    capability_type=cap.type,
                    capability_key=cap.key,
                    resolution_status="unresolved",
                )
            )

    def _version_summary(self, version: AssistantSkillVersion) -> SkillVersionSummary:
        return SkillVersionSummary(
            id=version.id,
            skill_package_id=version.skill_package_id,
            sequence_no=version.sequence_no,
            version_name=version.version_name,
            version_source=version.version_source,  # type: ignore[arg-type]
            origin=version.origin,
            content_digest=version.content_digest,
            skill_md_digest=version.skill_md_digest,
            manifest_digest=version.manifest_digest,
            resource_index_digest=version.resource_index_digest,
            binding_set_digest=version.binding_set_digest,
            version_digest=version.version_digest,
            source_draft_version_id=version.source_draft_version_id,
            created_at=version.created_at,
        )

    def _package_summary(self, package: AssistantSkillPackage) -> SkillPackageSummary:
        draft = None
        if package.draft_version_id is not None:
            draft_row = self.db.get(AssistantSkillVersion, package.draft_version_id)
            if draft_row is not None:
                draft = self._version_summary(draft_row)
        published = None
        if package.published_version_id is not None:
            pub_row = self.db.get(AssistantSkillVersion, package.published_version_id)
            if pub_row is not None:
                published = self._version_summary(pub_row)
        return SkillPackageSummary(
            id=package.id,
            canonical_name=package.canonical_name,
            display_name=package.display_name,
            description=package.description,
            migration_state=package.migration_state,  # type: ignore[arg-type]
            catalog_enabled=bool(package.catalog_enabled),
            is_system=bool(package.is_system),
            aggregate_revision=int(getattr(package, "aggregate_revision", 0) or 0),
            archived_at=getattr(package, "archived_at", None),
            archived_by=getattr(package, "archived_by", None),
            catalog_enabled_at=getattr(package, "catalog_enabled_at", None),
            catalog_enabled_by=getattr(package, "catalog_enabled_by", None),
            draft_version=draft,
            published_version=published,
            created_at=package.created_at,
            updated_at=package.updated_at,
        )

    def _assert_import_namespace_free(
        self,
        *,
        canonical_name: str,
        legacy_aliases: list[str],
    ) -> None:
        """Raise 40995 when any import name/alias is already reserved."""
        candidates: list[tuple[str, str]] = [(canonical_name, "canonical")]
        seen = {normalize_skill_lookup_name(canonical_name)}
        for raw in legacy_aliases:
            try:
                normalized = normalize_skill_lookup_name(raw)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=42291, message=str(exc)
                ) from exc
            if is_reserved_skill_lookup_name(raw):
                raise ApiException(
                    status_code=409,
                    code=40995,
                    message=f"alias {raw!r} is reserved",
                )
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((raw, "legacy"))

        # Canonical package table.
        existing_pkg = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name == canonical_name)
            .one_or_none()
        )
        if existing_pkg is not None:
            raise ApiException(
                status_code=409,
                code=40995,
                message=f"create-only import conflict: canonical name {canonical_name!r} exists",
            )

        for raw, _kind in candidates:
            try:
                normalized = normalize_skill_lookup_name(raw)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=42291, message=str(exc)
                ) from exc
            conflict = (
                self.db.query(AssistantSkillPackageAlias)
                .filter(AssistantSkillPackageAlias.normalized_alias == normalized)
                .one_or_none()
            )
            if conflict is not None:
                raise ApiException(
                    status_code=409,
                    code=40995,
                    message=(
                        f"create-only import conflict: name/alias {raw!r} already reserved"
                    ),
                )

    def _as_import_conflict(self, exc: ApiException) -> ApiException:
        """Map name/alias reservation failures onto create-only 40995."""
        if exc.code in {40990, 40991, 40995}:
            return ApiException(
                status_code=409,
                code=40995,
                message=exc.message
                if exc.code == 40995
                else f"create-only import conflict: {exc.message}",
            )
        return exc

    def _load_stored_resources(
        self, *, version_id: UUID
    ) -> tuple[StoredSkillResource, ...]:
        rows = (
            self.db.query(AssistantSkillVersionResource, AssistantSkillResourceBlob)
            .join(
                AssistantSkillResourceBlob,
                AssistantSkillResourceBlob.id == AssistantSkillVersionResource.blob_id,
            )
            .filter(AssistantSkillVersionResource.skill_version_id == version_id)
            .order_by(AssistantSkillVersionResource.path.asc())
            .all()
        )
        resources: list[StoredSkillResource] = []
        for resource, blob in rows:
            content = bytes(blob.content)
            if blob.byte_size != resource.byte_size or blob.sha256 != resource.sha256:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob metadata mismatch",
                )
            if len(content) != blob.byte_size:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob size mismatch",
                )
            if sha256_bytes(content) != blob.sha256:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob content digest mismatch",
                )
            resources.append(
                StoredSkillResource(
                    path=resource.path,
                    resource_kind=resource.resource_kind,  # type: ignore[arg-type]
                    media_type=resource.media_type,
                    byte_size=resource.byte_size,
                    sha256=resource.sha256,
                    content=content,
                )
            )
        return tuple(resources)

    def _translate_integrity_error(self, exc: IntegrityError) -> ApiException:
        msg = str(getattr(exc, "orig", exc)).lower()
        if (
            "last_admin_request_id" in msg
            or "uq_assistant_skill_package_last_admin_request_id" in msg
        ):
            return ApiException(
                status_code=409,
                code=40997,
                message="requestId was reused with a different payload",
            )
        if "canonical_name" in msg and "unique" in msg:
            return ApiException(
                status_code=409,
                code=40990,
                message="skill canonical name conflict",
            )
        if "normalized_alias" in msg or "assistant_skill_package_alias" in msg:
            return ApiException(
                status_code=409,
                code=40991,
                message="skill alias namespace conflict",
            )
        if "assistant_skill_package" in msg and "unique" in msg:
                        return ApiException(
                status_code=409,
                code=40993,
                message="skill package uniqueness conflict",
            )
        if "sequence" in msg or "uq_assistant_skill_version_seq" in msg:
            return ApiException(
                status_code=409,
                code=40992,
                message="skill version sequence conflict; retry",
            )
        if "draft_content" in msg or "content_digest" in msg:
            return ApiException(
                status_code=409,
                code=40992,
                message="skill draft content conflict; retry",
            )
        return ApiException(
            status_code=409,
            code=40992,
            message="skill package constraint violation",
        )


class MainAgentProfileService:
    """Append-only Main Agent Profile aggregate service (Plan 01 Task 6).

    Does not activate runtime, build prompts, resolve providers, or replace
    ``general_chat`` routing. ``runtime_enabled`` remains false throughout Plan 01.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Bootstrap / reads
    # ------------------------------------------------------------------

    def ensure_default(self) -> MainAgentProfileSummary:
        """Create the default bootstrap profile if missing; idempotent thereafter."""
        existing = self._find_default()
        if existing is not None:
            return self._profile_summary(existing)

        snapshot = default_main_agent_profile_snapshot()
        payload = snapshot.normalized_payload()
        digest = snapshot.content_digest()

        try:
            profile = AssistantMainAgentProfile(
                profile_key=DEFAULT_MAIN_AGENT_PROFILE_KEY,
                display_name=DEFAULT_MAIN_AGENT_DISPLAY_NAME,
                is_default=True,
                migration_state="bootstrap",
                runtime_enabled=False,
            )
            self.db.add(profile)
            self.db.flush()

            version = AssistantMainAgentProfileVersion(
                profile_id=profile.id,
                sequence_no=1,
                version_name="bootstrap-1",
                version_source="save",
                origin="bootstrap",
                source_draft_version_id=None,
                snapshot=payload,
                content_digest=digest,
                source_ref=None,
            )
            self.db.add(version)
            self.db.flush()
            profile.draft_version_id = version.id
            # Plan 01: never enable runtime.
            profile.runtime_enabled = False
            self.db.commit()
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            # Concurrent ensure_default: unique is_default / profile_key.
            raced = self._find_default()
            if raced is not None:
                return self._profile_summary(raced)
            raise self._translate_integrity_error(exc) from exc

        return self.get_default()

    def get_default(self) -> MainAgentProfileSummary:
        profile = self._find_default()
        if profile is None:
            raise ApiException(
                status_code=404,
                code=40493,
                message="default main agent profile not found",
            )
        return self._profile_summary(profile)

    def list_versions(self, profile_id: UUID) -> list[MainAgentProfileVersionSummary]:
        profile = self._get_profile_or_404(profile_id)
        versions = (
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(AssistantMainAgentProfileVersion.profile_id == profile.id)
            .order_by(AssistantMainAgentProfileVersion.sequence_no.asc())
            .all()
        )
        return [self._version_summary(row) for row in versions]

    def get_version(
        self, profile_id: UUID, version_id: UUID
    ) -> MainAgentProfileVersionDetail:
        profile = self._get_profile_or_404(profile_id)
        version = (
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(
                AssistantMainAgentProfileVersion.id == version_id,
                AssistantMainAgentProfileVersion.profile_id == profile.id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40493,
                message=f"main agent profile version not found: {version_id}",
            )
        return MainAgentProfileVersionDetail(
            **self._version_summary(version).model_dump(),
            snapshot=dict(version.snapshot or {}),
            source_ref=dict(version.source_ref) if version.source_ref else None,
        )

    # ------------------------------------------------------------------
    # Draft / publish
    # ------------------------------------------------------------------

    def save_draft(
        self,
        profile_id: UUID,
        command: SaveMainAgentProfileDraftCommand,
    ) -> MainAgentProfileVersionSummary:
        """Append or re-point a Main Agent Profile draft with required CAS.

        Does **not** mutate ``runtime_enabled`` or the published pointer. Live
        state changes only via explicit enable/disable or gated publish paths.
        Sequence: lock profile → compare request ID/digest → compare expected
        revision → apply mutation → increment revision → stamp request evidence.
        """
        try:
            snapshot = self._validate_snapshot(command.snapshot)
            payload = snapshot.normalized_payload()
            digest = snapshot.content_digest()
            version_name = command.version_name or "draft"
            origin = command.origin
            source_ref = command.source_ref
            request_id = (command.request_id or "").strip()
            if not request_id:
                raise ApiException(
                    status_code=422,
                    code=42294,
                    message="requestId is required",
                )

            profile = self._lock_profile(profile_id)

            cas_payload = {
                "profile_id": str(profile_id),
                "version_name": version_name,
                "origin": origin,
                "content_digest": digest,
                "expected_aggregate_revision": int(
                    command.expected_aggregate_revision
                ),
            }
            body = json.dumps(
                {"operation": "profile_save_draft", "payload": cas_payload},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            request_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            last_id = getattr(profile, "last_admin_request_id", None)
            last_digest = getattr(profile, "last_admin_request_digest", None)
            if last_id == request_id:
                if last_digest == request_digest and profile.draft_version_id is not None:
                    draft = (
                        self.db.query(AssistantMainAgentProfileVersion)
                        .filter(
                            AssistantMainAgentProfileVersion.id
                            == profile.draft_version_id
                        )
                        .one_or_none()
                    )
                    if draft is not None:
                        return self._version_summary(draft)
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                    details={"requestId": request_id},
                )

            current_rev = int(getattr(profile, "aggregate_revision", 0) or 0)
            if current_rev != int(command.expected_aggregate_revision):
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REVISION,
                    message=(
                        f"aggregate revision conflict: expected "
                        f"{command.expected_aggregate_revision}, current {current_rev}"
                    ),
                    details={
                        "expectedAggregateRevision": int(
                            command.expected_aggregate_revision
                        ),
                        "currentAggregateRevision": current_rev,
                        "profileId": str(profile.id),
                    },
                )

            # Migration ownership:
            # - origin=legacy keeps/sets migration_state=shadow (bootstrap→shadow)
            # - origin=api promotes bootstrap|shadow → native (administrator ownership)
            # - never demote native/cutover via either path
            if origin == "legacy":
                if profile.migration_state == "bootstrap":
                    profile.migration_state = "shadow"
            elif origin == "api":
                if profile.migration_state in {"bootstrap", "shadow"}:
                    profile.migration_state = "native"

            existing = (
                self.db.query(AssistantMainAgentProfileVersion)
                .filter(
                    AssistantMainAgentProfileVersion.profile_id == profile.id,
                    AssistantMainAgentProfileVersion.version_source == "save",
                    AssistantMainAgentProfileVersion.content_digest == digest,
                )
                .one_or_none()
            )
            if existing is not None:
                profile.draft_version_id = existing.id
                profile.aggregate_revision = current_rev + 1
                profile.last_admin_request_id = request_id
                profile.last_admin_request_digest = request_digest
                self.db.commit()
                return self._version_summary(existing)

            next_seq = self._next_sequence(profile.id)
            version = AssistantMainAgentProfileVersion(
                profile_id=profile.id,
                sequence_no=next_seq,
                version_name=version_name,
                version_source="save",
                origin=origin,
                source_draft_version_id=None,
                snapshot=payload,
                content_digest=digest,
                source_ref=source_ref,
            )
            self.db.add(version)
            self.db.flush()
            profile.draft_version_id = version.id
            profile.aggregate_revision = current_rev + 1
            profile.last_admin_request_id = request_id
            profile.last_admin_request_digest = request_digest
            self.db.commit()
            return self._version_summary(version)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    def publish(
        self,
        profile_id: UUID,
        command: PublishMainAgentProfileCommand,
        *,
        actor_principal: str | None = None,
    ) -> MainAgentProfileVersionSummary:
        """Create an immutable publish version from an owned draft.

        Always inserts a new ``version_source=publish`` row. Drafts are never
        mutated. Publish never auto-enables runtime.

        Sequence: lock profile → compare request ID/digest (idempotent replay) →
        compare expected revision → apply mutation → always increment revision →
        stamp ``last_admin_request_*`` → commit. Gate consumption remains required
        where the live matrix already required it; CAS is additional integrity.

        Plan 09: already ``runtime_enabled`` requires a matching gate in both
        observe and enforce; live-disabled bootstrap may be ungated only in observe.
        """
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            build_publish_gate_subject,
            current_gate_environment_pins,
            profile_catalog_pin_digest,
            publish_gate_mode,
        )

        try:
            profile = self._lock_profile(profile_id)

            request_id = (command.request_id or "").strip()
            if not request_id:
                raise ApiException(
                    status_code=422,
                    code=42294,
                    message="requestId is required",
                )
            gate_id = getattr(command, "gate_id", None)
            cas_payload = {
                "profile_id": str(profile_id),
                "draft_version_id": str(command.draft_version_id),
                "gate_id": str(gate_id) if gate_id is not None else None,
                "expected_aggregate_revision": int(
                    command.expected_aggregate_revision
                ),
            }
            body = json.dumps(
                {"operation": "profile_publish", "payload": cas_payload},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            request_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            last_id = getattr(profile, "last_admin_request_id", None)
            last_digest = getattr(profile, "last_admin_request_digest", None)
            if last_id == request_id:
                if (
                    last_digest == request_digest
                    and profile.published_version_id is not None
                ):
                    published = (
                        self.db.query(AssistantMainAgentProfileVersion)
                        .filter(
                            AssistantMainAgentProfileVersion.id
                            == profile.published_version_id
                        )
                        .one_or_none()
                    )
                    if published is not None:
                        return self._version_summary(published)
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                    details={"requestId": request_id},
                )

            current_rev = int(getattr(profile, "aggregate_revision", 0) or 0)
            if current_rev != int(command.expected_aggregate_revision):
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REVISION,
                    message=(
                        f"aggregate revision conflict: expected "
                        f"{command.expected_aggregate_revision}, current {current_rev}"
                    ),
                    details={
                        "expectedAggregateRevision": int(
                            command.expected_aggregate_revision
                        ),
                        "currentAggregateRevision": current_rev,
                        "profileId": str(profile.id),
                    },
                )

            live_enabled = bool(getattr(profile, "runtime_enabled", False))
            draft = (
                self.db.query(AssistantMainAgentProfileVersion)
                .filter(
                    AssistantMainAgentProfileVersion.id == command.draft_version_id,
                    AssistantMainAgentProfileVersion.profile_id == profile.id,
                    AssistantMainAgentProfileVersion.version_source == "save",
                )
                .one_or_none()
            )
            if draft is None:
                raise ApiException(
                    status_code=404,
                    code=40493,
                    message=(
                        f"owned draft version not found: {command.draft_version_id}"
                    ),
                )

            # Re-validate the whole snapshot inside the transaction.
            snapshot = self._validate_snapshot(draft.snapshot)
            self._assert_publishable_control_keys(snapshot.control_capability_keys)

            payload = snapshot.normalized_payload()
            digest = snapshot.content_digest()
            if digest != draft.content_digest:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="draft content digest mismatch during publish validation",
                )

            gate_svc = PublishGateService(self.db)
            mode = publish_gate_mode()
            from app.assistant.domain.digests import profile_binding_digest

            subject_kind = "main_agent_profile_draft"
            subject_version_id = draft.id
            dataset_ids = None
            if gate_id is not None:
                dataset_ids = self._dataset_ids_from_gate_evidence(gate_svc, gate_id)
            catalog_digest = profile_catalog_pin_digest(
                profile_id=profile.id,
                published_version_id=profile.published_version_id,
                runtime_enabled=live_enabled,
            )
            pins = current_gate_environment_pins(
                self.db,
                profile_digest=str(digest),
                catalog_digest=catalog_digest,
                dataset_version_ids=dataset_ids,
            )
            binding_digest = profile_binding_digest(
                profile_id=profile.id,
                version_id=subject_version_id,
                content_digest=str(digest),
            )
            subject = build_publish_gate_subject(
                kind=subject_kind,
                aggregate_id=profile.id,
                version_id=subject_version_id,
                content_digest=str(digest),
                binding_digest=binding_digest,
                profile_digest=pins.profile_digest,
                catalog_digest=pins.catalog_digest,
                dataset_version_ids=pins.dataset_version_ids,
                runtime_contract_version=pins.runtime_contract_version,
                policy_version=pins.policy_version,
                threshold_version=pins.threshold_version,
                build_revision=pins.build_revision,
            )

            sequence_no = self._next_sequence(profile.id)
            publish_version = AssistantMainAgentProfileVersion(
                profile_id=profile.id,
                sequence_no=sequence_no,
                version_name=f"publish-{sequence_no}",
                version_source="publish",
                origin=draft.origin,
                source_draft_version_id=draft.id,
                snapshot=payload,
                content_digest=digest,
                source_ref=dict(draft.source_ref) if draft.source_ref else None,
            )
            self.db.add(publish_version)
            self.db.flush()

            actor = actor_principal or "system:profile-publish"
            try:
                gate_svc.enforce_or_bootstrap_publish(
                    live_enabled=live_enabled,
                    gate_id=gate_id,
                    subject=subject,
                    action="profile_publish",
                    aggregate_id=profile.id,
                    resulting_version_id=publish_version.id,
                    actor_principal=actor,
                    request_id=str(request_id),
                    aggregate_revision=current_rev,
                    mode=mode,
                )
            except PublishGateError as exc:
                raise exc.to_api_exception() from exc

            profile.published_version_id = publish_version.id
            if not live_enabled:
                profile.runtime_enabled = False
            # Always advance CAS so draft/publish observers see pointer changes.
            profile.aggregate_revision = current_rev + 1
            profile.last_admin_request_id = request_id
            profile.last_admin_request_digest = request_digest
            self.db.commit()
            return self._version_summary(publish_version)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    def set_runtime_enabled(
        self,
        profile_id: UUID,
        *,
        enabled: bool,
        expected_published_version_id: UUID | None = None,
        expected_content_digest: str | None = None,
        migration_state: str | None = None,
        gate_id: UUID | None = None,
        request_id: str | None = None,
        expected_aggregate_revision: int | None = None,
        actor_principal: str | None = None,
    ) -> MainAgentProfileSummary:
        """Toggle Profile aggregate ``runtime_enabled`` without mutating versions.

        Enabling requires a published version and a fresh matching promotion gate
        (observe and enforce). Optional expected digests make the operation fail
        closed on concurrent pointer/content drift. When ``request_id`` /
        ``expected_aggregate_revision`` are supplied, applies the same
        idempotency-first CAS sequence as Skill package admin mutations.
        """
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            build_publish_gate_subject,
            current_gate_environment_pins,
            profile_catalog_pin_digest,
        )

        try:
            profile = self._lock_profile(profile_id)
            rid = (request_id or "").strip() or None
            cas_payload = {
                "profile_id": str(profile_id),
                "enabled": bool(enabled),
                "expected_published_version_id": (
                    str(expected_published_version_id)
                    if expected_published_version_id is not None
                    else None
                ),
                "expected_content_digest": expected_content_digest,
                "migration_state": migration_state,
                "gate_id": str(gate_id) if gate_id is not None else None,
                "expected_aggregate_revision": expected_aggregate_revision,
            }
            request_digest: str | None = None
            if rid is not None:
                body = json.dumps(
                    {"operation": "profile_set_runtime_enabled", "payload": cas_payload},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                request_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                last_id = getattr(profile, "last_admin_request_id", None)
                last_digest = getattr(profile, "last_admin_request_digest", None)
                if last_id == rid:
                    if last_digest == request_digest:
                        return self._profile_summary(profile)
                    raise ApiException(
                        status_code=409,
                        code=_CODE_CONFLICT_REQUEST,
                        message="requestId was reused with a different payload",
                        details={"requestId": rid},
                    )

            current_rev = int(getattr(profile, "aggregate_revision", 0) or 0)
            if expected_aggregate_revision is not None:
                if current_rev != int(expected_aggregate_revision):
                    raise ApiException(
                        status_code=409,
                        code=_CODE_CONFLICT_REVISION,
                        message=(
                            f"aggregate revision conflict: expected "
                            f"{expected_aggregate_revision}, current {current_rev}"
                        ),
                        details={
                            "expectedAggregateRevision": int(expected_aggregate_revision),
                            "currentAggregateRevision": current_rev,
                            "profileId": str(profile.id),
                        },
                    )

            if enabled:
                if profile.published_version_id is None:
                    raise ApiException(
                        status_code=422,
                        code=42294,
                        message="cannot enable runtime without a published profile version",
                    )
                version = self.db.get(
                    AssistantMainAgentProfileVersion, profile.published_version_id
                )
                if version is None or version.profile_id != profile.id:
                    raise ApiException(
                        status_code=422,
                        code=42294,
                        message="published profile version missing or unowned",
                    )
                if str(version.version_source) != "publish":
                    raise ApiException(
                        status_code=422,
                        code=42294,
                        message="runtime enable requires version_source=publish",
                    )
                if (
                    expected_published_version_id is not None
                    and version.id != expected_published_version_id
                ):
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="published profile version drifted during enable",
                    )
                if (
                    expected_content_digest is not None
                    and str(version.content_digest or "") != expected_content_digest
                ):
                    raise ApiException(
                        status_code=409,
                        code=40993,
                        message="published profile content digest drifted during enable",
                    )
                snapshot = self._validate_snapshot(version.snapshot)
                self._assert_publishable_control_keys(snapshot.control_capability_keys)
                if "assistant_chat" not in set(snapshot.supported_entrypoints):
                    raise ApiException(
                        status_code=422,
                        code=42294,
                        message="profile must support assistant_chat before runtime enable",
                    )

                gate_svc = PublishGateService(self.db)
                from app.assistant.domain.digests import profile_binding_digest

                content_digest = str(version.content_digest or ("0" * 64))
                dataset_ids = None
                if gate_id is not None:
                    dataset_ids = self._dataset_ids_from_gate_evidence(gate_svc, gate_id)
                catalog_digest = profile_catalog_pin_digest(
                    profile_id=profile.id,
                    published_version_id=profile.published_version_id,
                )
                pins = current_gate_environment_pins(
                    self.db,
                    profile_digest=content_digest,
                    catalog_digest=catalog_digest,
                    dataset_version_ids=dataset_ids,
                )
                binding_digest = profile_binding_digest(
                    profile_id=profile.id,
                    version_id=profile.published_version_id,
                    content_digest=content_digest,
                )
                subject = build_publish_gate_subject(
                    kind="main_agent_profile_version",
                    aggregate_id=profile.id,
                    version_id=profile.published_version_id,
                    content_digest=content_digest,
                    binding_digest=binding_digest,
                    profile_digest=pins.profile_digest,
                    catalog_digest=pins.catalog_digest,
                    dataset_version_ids=pins.dataset_version_ids,
                    runtime_contract_version=pins.runtime_contract_version,
                    policy_version=pins.policy_version,
                    threshold_version=pins.threshold_version,
                    build_revision=pins.build_revision,
                )
                try:
                    gate_svc.enforce_enable(
                        gate_id=gate_id,
                        subject=subject,
                        action="profile_runtime_enable",
                        aggregate_id=profile.id,
                        resulting_version_id=profile.published_version_id,
                        actor_principal=actor_principal or "system:profile-enable",
                        request_id=str(
                            rid or f"profile-enable:{profile.id}:{version.id}"
                        ),
                        aggregate_revision=current_rev,
                    )
                except PublishGateError as exc:
                    raise exc.to_api_exception() from exc
            if migration_state is not None:
                allowed = {"bootstrap", "shadow", "native", "cutover"}
                if migration_state not in allowed:
                    raise ApiException(
                        status_code=422,
                        code=42294,
                        message=f"invalid migration_state: {migration_state}",
                    )
                # Never demote cutover/native to bootstrap/shadow via this path.
                current = str(profile.migration_state or "")
                if current in {"native", "cutover"} and migration_state in {
                    "bootstrap",
                    "shadow",
                }:
                    raise ApiException(
                        status_code=422,
                        code=42294,
                        message=(
                            f"cannot demote migration_state from {current} to "
                            f"{migration_state}"
                        ),
                    )
                profile.migration_state = migration_state
            profile.runtime_enabled = bool(enabled)
            # Always advance aggregate revision on successful live-state toggle.
            profile.aggregate_revision = current_rev + 1
            if rid is not None and request_digest is not None:
                profile.last_admin_request_id = rid
                profile.last_admin_request_digest = request_digest
            self.db.commit()
            return self._profile_summary(profile)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_publishable_control_keys(keys: tuple[str, ...] | list[str]) -> None:
        """Plan 04: empty keys remain allowed; otherwise require the exact four controls."""
        present = tuple(keys or ())
        if not present:
            return
        try:
            from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS
        except Exception as exc:  # pragma: no cover - import guard
            raise ApiException(
                status_code=422,
                code=42294,
                message=(
                    "controlCapabilityKeys cannot be published until Main Agent "
                    "control resolvers are available"
                ),
            ) from exc
        required = set(MAIN_AGENT_CONTROL_KEYS)
        present_set = set(present)
        if present_set != required:
            missing = sorted(required - present_set)
            extra = sorted(present_set - required)
            raise ApiException(
                status_code=422,
                code=42294,
                message=(
                    "controlCapabilityKeys must be exactly the four Main Agent "
                    f"controls; missing={missing} extra={extra}"
                ),
            )

    def _validate_snapshot(
        self, snapshot: MainAgentProfileSnapshotV1 | dict[str, Any]
    ) -> MainAgentProfileSnapshotV1:
        try:
            if isinstance(snapshot, MainAgentProfileSnapshotV1):
                # Re-parse through model_validate to enforce invariants on copies.
                return MainAgentProfileSnapshotV1.model_validate(
                    snapshot.model_dump(by_alias=True)
                )
            return MainAgentProfileSnapshotV1.model_validate(snapshot)
        except Exception as exc:  # pydantic ValidationError + ValueError
            raise ApiException(
                status_code=422,
                code=42294,
                message=f"invalid main agent profile snapshot: {exc}",
            ) from exc

    def _find_default(self) -> AssistantMainAgentProfile | None:
        return (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one_or_none()
        )

    def _get_profile_or_404(self, profile_id: UUID) -> AssistantMainAgentProfile:
        profile = self.db.get(AssistantMainAgentProfile, profile_id)
        if profile is None:
            raise ApiException(
                status_code=404,
                code=40493,
                message=f"main agent profile not found: {profile_id}",
            )
        return profile

    def _lock_profile(self, profile_id: UUID) -> AssistantMainAgentProfile:
        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.id == profile_id)
            .with_for_update()
            .one_or_none()
        )
        if profile is None:
            raise ApiException(
                status_code=404,
                code=40493,
                message=f"main agent profile not found: {profile_id}",
            )
        return profile

    def _next_sequence(self, profile_id: UUID) -> int:
        current = (
            self.db.query(func.max(AssistantMainAgentProfileVersion.sequence_no))
            .filter(AssistantMainAgentProfileVersion.profile_id == profile_id)
            .scalar()
        )
        return int(current or 0) + 1

    def _version_summary(
        self, version: AssistantMainAgentProfileVersion
    ) -> MainAgentProfileVersionSummary:
        return MainAgentProfileVersionSummary(
            id=version.id,
            profile_id=version.profile_id,
            sequence_no=version.sequence_no,
            version_name=version.version_name,
            version_source=version.version_source,  # type: ignore[arg-type]
            origin=version.origin,
            content_digest=version.content_digest,
            source_draft_version_id=version.source_draft_version_id,
            created_at=version.created_at,
        )

    def _profile_summary(
        self, profile: AssistantMainAgentProfile
    ) -> MainAgentProfileSummary:
        draft = None
        if profile.draft_version_id is not None:
            draft_row = self.db.get(
                AssistantMainAgentProfileVersion, profile.draft_version_id
            )
            if draft_row is not None:
                draft = self._version_summary(draft_row)
        published = None
        if profile.published_version_id is not None:
            pub_row = self.db.get(
                AssistantMainAgentProfileVersion, profile.published_version_id
            )
            if pub_row is not None:
                published = self._version_summary(pub_row)
        return MainAgentProfileSummary(
            id=profile.id,
            profile_key=profile.profile_key,
            display_name=profile.display_name,
            is_default=bool(profile.is_default),
            migration_state=profile.migration_state,  # type: ignore[arg-type]
            runtime_enabled=bool(profile.runtime_enabled),
            aggregate_revision=int(getattr(profile, "aggregate_revision", 0) or 0),
            draft_version=draft,
            published_version=published,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def _translate_integrity_error(self, exc: IntegrityError) -> ApiException:
        msg = str(getattr(exc, "orig", exc)).lower()
        if "is_default" in msg or "profile_key" in msg or "uq_assistant_main_agent" in msg:
            return ApiException(
                status_code=409,
                code=40992,
                message="main agent profile concurrency conflict; retry",
            )
        if "sequence" in msg or "content_digest" in msg or "draft_content" in msg:
            return ApiException(
                status_code=409,
                code=40992,
                message="main agent profile version conflict; retry",
            )
        return ApiException(
            status_code=409,
            code=40992,
            message="main agent profile constraint violation",
        )


__all__ = [
    "MAX_PACKAGE_DISTINCT_BLOB_BYTES",
    "AgentSkillService",
    "MainAgentProfileService",
]
