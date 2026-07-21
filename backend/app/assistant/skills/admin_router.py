"""Plan 09 Skill package admin HTTP surface (aggregate lifecycle).

Every Plan 09 admin route lives under one parent router that is only mounted
when an explicit trusted-dev/test guard is set. Staging/production keep this
router unmounted and absent from OpenAPI until a real principal dependency
exists. Service methods still reject missing ``OperatorPrincipal``.
"""

from __future__ import annotations

import os
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from app.assistant.skills.admin_service import SkillAdminService
from app.assistant.skills.diff import diff_skill_versions
from app.assistant.skills.import_preview import ImportPreviewService
from app.assistant.skills.package_io import MAX_ZIP_UPLOAD_BYTES, STREAM_CHUNK_SIZE
from app.assistant.skills.principal import OperatorPrincipal
from app.assistant.skills.schemas import (
    AddSkillPackageAliasCommand,
    AggregateRevisionCommand,
    DisableSkillPackageAliasCommand,
    RestoreSkillVersionAsDraftCommand,
    UpdateSkillPackageMetadataCommand,
)
from app.assistant.skills.service import MainAgentProfileService
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.common.schemas import CamelModel
from app.database import get_db


# Explicit trusted-dev/test guard only. Never treat feature flags / Origin /
# loopback / CORS as authentication.
TRUSTED_MOUNT_ENV = "ASSISTANT_SKILL_ADMIN_TRUSTED_MOUNT"
TRUSTED_MOUNT_VALUE = "1"

PLAN09_ADMIN_PREFIX = "/api/assistant-config/skill-admin"


def skill_admin_trusted_mount_enabled() -> bool:
    """Return True only when the explicit trusted-dev/test guard is set."""
    return os.environ.get(TRUSTED_MOUNT_ENV, "").strip() == TRUSTED_MOUNT_VALUE


def should_mount_skill_admin_router(*, app_env: str | None = None) -> bool:
    """Mount decision for Plan 09 parent router.

    Staging/production never mount without a real principal dependency (absent
    today). Trusted test/dev may mount only via the explicit env guard.
    """
    env = (app_env or os.environ.get("APP_ENV", "development")).strip().lower()
    if env in {"staging", "production"}:
        return False
    return skill_admin_trusted_mount_enabled()


# Parent router — all Plan 09 subroutes attach here.
skill_admin_parent_router = APIRouter(
    prefix=PLAN09_ADMIN_PREFIX,
    tags=["assistant-skill-admin"],
)


class MetadataPatchBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(alias="expectedAggregateRevision", ge=0)
    display_name: str | None = Field(default=None, alias="displayName", max_length=128)
    description: str | None = Field(default=None, max_length=1024)


class RevisionBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    expected_aggregate_revision: int = Field(alias="expectedAggregateRevision", ge=0)


class AddAliasBody(RevisionBody):
    alias: str = Field(min_length=1, max_length=512)


class CatalogEnableBody(RevisionBody):
    expected_published_version_id: UUID | None = Field(
        default=None, alias="expectedPublishedVersionId"
    )
    # Plan 09: required promotion gate for catalog enable (server-derived).
    gate_id: UUID | None = Field(default=None, alias="gateId")


def _dto(model: Any) -> Any:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, mode="json")
    return model


def _parse_trusted_principal(
    *,
    x_mindatlas_operator_id: str | None,
    x_mindatlas_operator_role: str | None,
) -> OperatorPrincipal:
    """Mint a principal only behind the trusted mount guard.

    This is **not** release authentication. Forged headers outside the mount
    guard never reach these routes because the router is unmounted.
    """
    if not skill_admin_trusted_mount_enabled():
        raise ApiException(
            status_code=401,
            code=40190,
            message="verified OperatorPrincipal is required",
        )
    principal_id = (x_mindatlas_operator_id or "").strip()
    role = (x_mindatlas_operator_role or "").strip().lower()
    if not principal_id:
        raise ApiException(
            status_code=401,
            code=40190,
            message="verified OperatorPrincipal is required",
        )
    if role not in {"operator", "viewer"}:
        raise ApiException(
            status_code=403,
            code=40391,
            message="operator role is required for this transition"
            if role
            else "verified OperatorPrincipal is required",
        )
    return OperatorPrincipal(principal_id=principal_id, role=role)  # type: ignore[arg-type]


def get_trusted_operator_principal(
    x_mindatlas_operator_id: Annotated[
        str | None, Header(alias="X-MindAtlas-Operator-Id")
    ] = None,
    x_mindatlas_operator_role: Annotated[
        str | None, Header(alias="X-MindAtlas-Operator-Role")
    ] = None,
) -> OperatorPrincipal:
    return _parse_trusted_principal(
        x_mindatlas_operator_id=x_mindatlas_operator_id,
        x_mindatlas_operator_role=x_mindatlas_operator_role,
    )


@skill_admin_parent_router.patch("/skill-packages/{package_id}/metadata")
def patch_skill_package_metadata(
    package_id: UUID,
    body: MetadataPatchBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.update_metadata(
        package_id,
        UpdateSkillPackageMetadataCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
            display_name=body.display_name,
            description=body.description,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.post("/skill-packages/{package_id}/archive")
def archive_skill_package(
    package_id: UUID,
    body: RevisionBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.archive(
        package_id,
        AggregateRevisionCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.post("/skill-packages/{package_id}/unarchive")
def unarchive_skill_package(
    package_id: UUID,
    body: RevisionBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.unarchive(
        package_id,
        AggregateRevisionCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.post("/skill-packages/{package_id}/catalog/enable")
def enable_skill_package_catalog(
    package_id: UUID,
    body: CatalogEnableBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.enable_catalog(
        package_id,
        AggregateRevisionCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
            gate_id=body.gate_id,
        ),
        principal=principal,
        expected_published_version_id=body.expected_published_version_id,
        gate_id=body.gate_id,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.post("/skill-packages/{package_id}/catalog/disable")
def disable_skill_package_catalog(
    package_id: UUID,
    body: RevisionBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.disable_catalog(
        package_id,
        AggregateRevisionCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.post("/skill-packages/{package_id}/aliases")
def add_skill_package_alias(
    package_id: UUID,
    body: AddAliasBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.add_alias(
        package_id,
        AddSkillPackageAliasCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
            alias=body.alias,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.post(
    "/skill-packages/{package_id}/aliases/{alias_id}/disable"
)
def disable_skill_package_alias(
    package_id: UUID,
    alias_id: UUID,
    body: RevisionBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    detail = svc.disable_alias(
        package_id,
        alias_id,
        DisableSkillPackageAliasCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(detail))


@skill_admin_parent_router.get(
    "/skill-packages/{package_id}/versions/{left_version_id}/diff/{right_version_id}"
)
def diff_skill_package_versions(
    package_id: UUID,
    left_version_id: UUID,
    right_version_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal  # auth required even for reads of admin surface
    result = diff_skill_versions(
        db,
        package_id=package_id,
        left_version_id=left_version_id,
        right_version_id=right_version_id,
    )
    return ApiResponse.ok(_dto(result))


@skill_admin_parent_router.post(
    "/skill-packages/{package_id}/versions/{version_id}/restore-draft"
)
def restore_skill_package_version_as_draft(
    package_id: UUID,
    version_id: UUID,
    body: RevisionBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = SkillAdminService(db)
    summary = svc.restore_as_new_draft(
        package_id,
        version_id,
        RestoreSkillVersionAsDraftCommand(
            request_id=body.request_id,
            expected_aggregate_revision=body.expected_aggregate_revision,
        ),
        principal=principal,
    )
    return ApiResponse.ok(_dto(summary))


# ---------------------------------------------------------------------------
# Import preview / apply (two-step; create | append | fork)
# ---------------------------------------------------------------------------


class ImportApplyBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preview_id: UUID = Field(alias="previewId")
    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    preview_digest: str | None = Field(
        default=None, alias="previewDigest", min_length=64, max_length=64
    )


async def _read_upload_bounded(file: UploadFile, *, max_bytes: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = await file.read(STREAM_CHUNK_SIZE)
        if not chunk:
            break
        remaining = max_bytes - len(buf)
        if len(chunk) > remaining:
            raise ApiException(
                status_code=413,
                code=41390,
                message=f"ZIP upload exceeds {max_bytes} bytes",
                details={
                    "type": "payload_too_large",
                    "details": {"maxBytes": max_bytes},
                },
            )
        buf.extend(chunk)
    return bytes(buf)


@skill_admin_parent_router.post("/skill-packages/import/preview")
async def preview_skill_package_import(
    file: UploadFile = File(...),
    mode: str = Form(...),
    target_package_id: UUID | None = Form(None, alias="targetPackageId"),
    expected_aggregate_revision: int = Form(..., alias="expectedAggregateRevision"),
    fork_canonical_name: str | None = Form(None, alias="forkCanonicalName"),
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Dry-run ZIP import preview. Never persists a package/version row.

    ``expectedAggregateRevision`` is required on the form for CAS parity with
    other Plan 09 mutations. Create/fork modes ignore the revision value after
    validation; append mode enforces it against the target package.
    """
    raw = await _read_upload_bounded(file, max_bytes=MAX_ZIP_UPLOAD_BYTES)
    svc = ImportPreviewService(db)
    result = svc.preview(
        raw_zip=raw,
        mode=mode,
        principal=principal,
        target_package_id=target_package_id,
        expected_aggregate_revision=expected_aggregate_revision,
        fork_canonical_name=fork_canonical_name,
    )
    return ApiResponse.ok(_dto(result))


@skill_admin_parent_router.post("/skill-packages/import/apply")
def apply_skill_package_import(
    body: ImportApplyBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Consume a preview token into one draft version.

    Create/fork produce an unpublished, catalog-disabled package. Append only
    advances the draft pointer and aggregate revision — it does not force
    catalog-disabled or clear publish evidence.
    """
    svc = ImportPreviewService(db)
    result = svc.apply(
        preview_id=body.preview_id,
        request_id=body.request_id,
        principal=principal,
        preview_digest=body.preview_digest,
    )
    return ApiResponse.ok(_dto(result))


@skill_admin_parent_router.get(
    "/main-agent-profiles/default/versions/{version_id}"
)
def get_default_main_agent_version(
    version_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Protected Profile version detail (Plan 09 trusted mount only)."""
    _ = principal  # principal required; viewer/operator both may read.
    service = MainAgentProfileService(db)
    try:
        profile = service.get_default()
    except ApiException as exc:
        if exc.code != 40493:
            raise
        profile = service.ensure_default()
    detail = service.get_version(profile.id, version_id)
    return ApiResponse.ok(_dto(detail))


def mount_skill_admin_router(app: Any, *, app_env: str | None = None) -> bool:
    """Conditionally mount Plan 09 parent router. Returns whether mounted."""
    if not should_mount_skill_admin_router(app_env=app_env):
        return False
    app.include_router(skill_admin_parent_router)
    return True
