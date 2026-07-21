"""Pure skill candidate-closure resolver shared by evaluation and publish.

Recomputes content digests, capability binding closures, and version digests
from immutable skill version payloads. Flushes and commits nothing — callers
own transaction boundaries. Client-authored digests are never trusted.
"""

from __future__ import annotations

from typing import Any, Literal, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.domain.contracts import FrozenContract, ResolvedCapabilityBinding
from app.assistant.skills.models import (
    AssistantSkillPackage,
    AssistantSkillResourceBlob,
    AssistantSkillVersion,
    AssistantSkillVersionResource,
)
from app.assistant.skills.package_io import parse_skill_directory_files
from app.assistant.skills.resolution import (
    CapabilityReferenceResolver,
    binding_set_digest_from_bindings,
    version_digest_from_parts,
)
from app.common.exceptions import ApiException

CandidateSubjectKind = Literal["skill_draft", "skill_version"]


class CandidateClosureError(ValueError):
    """Raised when a candidate package/version cannot be closed."""

    def __init__(self, code: str, *, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class SkillCandidateClosure(FrozenContract):
    """Frozen candidate evidence shared by evaluation admission and publish."""

    schema_version: Literal[1] = 1
    subject_kind: CandidateSubjectKind
    package_id: UUID
    version_id: UUID
    content_digest: str
    binding_set_digest: str
    version_digest: str
    # Typed as Any (not recursive JsonValue) so Pydantic schema generation does
    # not explode. Runtime values are model_dump(mode="json") of
    # ResolvedCapabilityBinding — i.e. JSON-compatible dicts.
    bindings: tuple[dict[str, Any], ...]
    durable_capability_keys: tuple[str, ...] = ()


def load_immutable_skill_version_files(
    session: Session,
    version: AssistantSkillVersion,
) -> dict[str, bytes]:
    """Reconstruct the immutable skill directory payload for a version row.

    Does not flush or commit. Resource bytes are loaded via the version→blob
    join already used by publish reconstruction.
    """
    files: dict[str, bytes] = {
        "SKILL.md": (version.skill_md or "").encode("utf-8"),
    }
    if version.mindatlas_yaml:
        files["mindatlas.yaml"] = version.mindatlas_yaml.encode("utf-8")
    resource_rows = (
        session.query(AssistantSkillVersionResource, AssistantSkillResourceBlob)
        .join(
            AssistantSkillResourceBlob,
            AssistantSkillResourceBlob.id == AssistantSkillVersionResource.blob_id,
        )
        .filter(AssistantSkillVersionResource.skill_version_id == version.id)
        .all()
    )
    for resource, blob in resource_rows:
        files[resource.path] = bytes(blob.content)
    return files


def apply_candidate_durable_extensions(
    session: Session,
    *,
    bindings: Sequence[ResolvedCapabilityBinding],
    durable_capability_keys: Sequence[str] = (),
) -> list[ResolvedCapabilityBinding]:
    """Attach validated durable plan extensions to matching workflow/agent bindings.

    Fail-closed: unknown keys, non-workflow/agent targets, or planner denials
    raise ``ApiException``. Does not mutate the input list items in place and
    does not flush or commit.
    """
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.workflow.durable.planner import (
        DurablePlanError,
        plan_durable_execution_from_surface,
        publish_durable_binding_snapshot,
    )

    wanted = {str(k) for k in durable_capability_keys if str(k).strip()}
    if not wanted:
        return list(bindings)

    matched: set[str] = set()
    registry = CapabilityRegistry(session)
    out: list[ResolvedCapabilityBinding] = []
    for resolved in bindings:
        key = str(resolved.capability_key)
        if key not in wanted:
            out.append(resolved)
            continue
        if resolved.capability_type not in {"workflow", "agent"}:
            raise ApiException(
                status_code=422,
                code=42291,
                message=(
                    f"durable publish requires workflow/agent capability: {key}"
                ),
            )
        matched.add(key)
        # Provenance digest is publish-time only; not stored on the binding row.
        provenance_digest = sha256_canonical_json(
            {
                "capabilityKey": key,
                "resolutionDigest": str(resolved.resolution_digest or ""),
                "bindingContractDigest": str(resolved.binding_contract_digest or ""),
            }
        )
        frozen = project_frozen_capability_binding(
            resolved=resolved,
            provenance=FrozenBindingProvenance(
                origin="skill_version",
                binding_row_id=None,
                owner_version_id=None,
                source_snapshot_digest=provenance_digest,
            ),
        )
        try:
            surface = registry.resolve_surface(frozen)
            plan = plan_durable_execution_from_surface(surface)
            new_resolved = publish_durable_binding_snapshot(resolved, plan=plan)
        except DurablePlanError as exc:
            raise ApiException(
                status_code=422,
                code=42291,
                message=f"durable plan denied for {key}: {exc}",
            ) from exc
        except ApiException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ApiException(
                status_code=422,
                code=42291,
                message=f"durable plan publish failed for {key}: {exc}",
            ) from exc
        out.append(new_resolved)

    missing = wanted - matched
    if missing:
        raise ApiException(
            status_code=422,
            code=42291,
            message=(
                "durable_capability_keys not present in package declarations: "
                + ", ".join(sorted(missing))
            ),
        )
    return out


def resolve_skill_candidate_closure(
    session: Session,
    *,
    package_id: UUID,
    version_id: UUID,
    subject_kind: CandidateSubjectKind,
    durable_capability_keys: tuple[str, ...] = (),
) -> SkillCandidateClosure:
    """Resolve the pure candidate closure for a skill draft or published version.

    Never flushes or commits. Raises ``CandidateClosureError`` for missing
    package/version rows or content-digest drift between stored digest and
    reconstructed payload.
    """
    package = session.get(AssistantSkillPackage, package_id)
    if package is None:
        raise CandidateClosureError("skill_package_not_found")
    version = session.scalar(
        select(AssistantSkillVersion).where(
            AssistantSkillVersion.id == version_id,
            AssistantSkillVersion.skill_package_id == package_id,
        )
    )
    if version is None:
        raise CandidateClosureError("skill_version_not_found")

    files = load_immutable_skill_version_files(session, version)
    parsed = parse_skill_directory_files(
        files,
        expected_root_name=str(package.canonical_name),
    )
    if parsed.content_digest != str(version.content_digest):
        raise CandidateClosureError("skill_content_digest_drift")

    declarations = tuple(parsed.manifest.capabilities) if parsed.manifest else ()
    bindings = sorted(
        CapabilityReferenceResolver(session).resolve_many(declarations),
        key=lambda item: (item.capability_type, item.capability_key),
    )
    bindings = apply_candidate_durable_extensions(
        session,
        bindings=bindings,
        durable_capability_keys=durable_capability_keys,
    )
    binding_digest = binding_set_digest_from_bindings(bindings)
    return SkillCandidateClosure(
        subject_kind=subject_kind,
        package_id=package_id,
        version_id=version_id,
        content_digest=parsed.content_digest,
        binding_set_digest=binding_digest,
        version_digest=version_digest_from_parts(
            content_digest=parsed.content_digest,
            binding_set_digest=binding_digest,
        ),
        bindings=tuple(binding.model_dump(mode="json") for binding in bindings),
        durable_capability_keys=tuple(durable_capability_keys),
    )


def resolved_bindings_from_closure(
    closure: SkillCandidateClosure,
) -> list[ResolvedCapabilityBinding]:
    """Rehydrate ``ResolvedCapabilityBinding`` instances from a frozen closure."""
    return [
        ResolvedCapabilityBinding.model_validate(item) for item in closure.bindings
    ]


__all__ = [
    "CandidateClosureError",
    "CandidateSubjectKind",
    "SkillCandidateClosure",
    "apply_candidate_durable_extensions",
    "load_immutable_skill_version_files",
    "resolve_skill_candidate_closure",
    "resolved_bindings_from_closure",
]
