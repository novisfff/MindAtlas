"""Bounded skill version diff (Plan 09 Task 1).

Compares two owned immutable versions using normalized metadata and bounded
text previews. Resource bytes, secrets, Provider payloads, and unbounded
instruction bodies are excluded from the result.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.skills.models import (
    AssistantSkillPackage,
    AssistantSkillVersion,
    AssistantSkillVersionResource,
)
from app.assistant.skills.schemas import SkillVersionDiffHunk, SkillVersionDiffResult
from app.common.exceptions import ApiException

# Hard bounds for text previews returned in hunks.
MAX_PREVIEW_CHARS = 512
MAX_HUNKS = 200

# Paths / keys that must never appear as preview text.
_SECRET_KEY_MARKERS = (
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "authorization",
)


def _bounded_preview(text: str | None) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    if len(text) <= MAX_PREVIEW_CHARS:
        return text, False
    return text[:MAX_PREVIEW_CHARS], True


def _looks_secret_path(path: str) -> bool:
    lower = path.lower()
    return any(marker in lower for marker in _SECRET_KEY_MARKERS)


def _normalized_version_metadata(version: AssistantSkillVersion) -> dict[str, Any]:
    """Immutable metadata only — no skill_md body, no resource bytes."""
    return {
        "id": str(version.id),
        "sequenceNo": int(version.sequence_no),
        "versionName": version.version_name,
        "versionSource": version.version_source,
        "origin": version.origin,
        "contentDigest": version.content_digest,
        "skillMdDigest": version.skill_md_digest,
        "manifestDigest": version.manifest_digest,
        "resourceIndexDigest": version.resource_index_digest,
        "bindingSetDigest": version.binding_set_digest,
        "versionDigest": version.version_digest,
        "sourceDraftVersionId": (
            str(version.source_draft_version_id)
            if version.source_draft_version_id
            else None
        ),
        "resourceIndex": list(version.resource_index or []),
    }


def _resource_meta_map(
    db: Session, version_id: UUID
) -> dict[str, AssistantSkillVersionResource]:
    rows = (
        db.query(AssistantSkillVersionResource)
        .filter(AssistantSkillVersionResource.skill_version_id == version_id)
        .order_by(AssistantSkillVersionResource.path.asc())
        .all()
    )
    return {r.path: r for r in rows}


def diff_skill_versions(
    db: Session,
    *,
    package_id: UUID,
    left_version_id: UUID,
    right_version_id: UUID,
) -> SkillVersionDiffResult:
    """Return a bounded structural/text diff of two owned versions."""
    package = db.get(AssistantSkillPackage, package_id)
    if package is None:
        raise ApiException(
            status_code=404,
            code=40490,
            message=f"Skill package not found: {package_id}",
        )

    def _load(vid: UUID) -> AssistantSkillVersion:
        version = (
            db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == vid,
                AssistantSkillVersion.skill_package_id == package_id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"Skill version not found: {vid}",
            )
        return version

    left = _load(left_version_id)
    right = _load(right_version_id)

    left_meta = _normalized_version_metadata(left)
    right_meta = _normalized_version_metadata(right)
    hunks: list[SkillVersionDiffHunk] = []

    # SKILL.md: digests always; bounded preview only when both are small text.
    if left.skill_md_digest != right.skill_md_digest:
        left_preview, left_trunc = _bounded_preview(left.skill_md)
        right_preview, right_trunc = _bounded_preview(right.skill_md)
        # Exclude unbounded instruction bodies: if either side exceeds bound,
        # drop previews and report digests only.
        if left_trunc or right_trunc:
            left_preview, right_preview = None, None
            truncated = True
        else:
            truncated = False
        hunks.append(
            SkillVersionDiffHunk(
                path="SKILL.md",
                kind="changed",
                left_digest=left.skill_md_digest,
                right_digest=right.skill_md_digest,
                left_preview=left_preview,
                right_preview=right_preview,
                truncated=truncated,
            )
        )
    else:
        hunks.append(
            SkillVersionDiffHunk(
                path="SKILL.md",
                kind="unchanged_meta",
                left_digest=left.skill_md_digest,
                right_digest=right.skill_md_digest,
            )
        )

    # mindatlas.yaml: digest-level only (may contain policy; keep bounded).
    left_man = left.manifest_digest
    right_man = right.manifest_digest
    if left_man != right_man:
        left_preview, left_trunc = _bounded_preview(left.mindatlas_yaml)
        right_preview, right_trunc = _bounded_preview(right.mindatlas_yaml)
        if left_trunc or right_trunc:
            left_preview, right_preview = None, None
            truncated = True
        else:
            truncated = False
        hunks.append(
            SkillVersionDiffHunk(
                path="mindatlas.yaml",
                kind="changed",
                left_digest=left_man,
                right_digest=right_man,
                left_preview=left_preview,
                right_preview=right_preview,
                truncated=truncated,
            )
        )
    else:
        hunks.append(
            SkillVersionDiffHunk(
                path="mindatlas.yaml",
                kind="unchanged_meta",
                left_digest=left_man,
                right_digest=right_man,
            )
        )

    # Resources: metadata/digest only — never bytes.
    left_resources = _resource_meta_map(db, left.id)
    right_resources = _resource_meta_map(db, right.id)
    all_paths = sorted(set(left_resources) | set(right_resources))
    for path in all_paths:
        if len(hunks) >= MAX_HUNKS:
            break
        if _looks_secret_path(path):
            # Secrets excluded: report presence change as meta only, no preview.
            left_r = left_resources.get(path)
            right_r = right_resources.get(path)
            if left_r is None:
                kind = "added"
            elif right_r is None:
                kind = "removed"
            elif left_r.sha256 != right_r.sha256:
                kind = "changed"
            else:
                kind = "unchanged_meta"
            hunks.append(
                SkillVersionDiffHunk(
                    path=path,
                    kind=kind,  # type: ignore[arg-type]
                    left_digest=left_r.sha256 if left_r else None,
                    right_digest=right_r.sha256 if right_r else None,
                    left_preview=None,
                    right_preview=None,
                    truncated=False,
                )
            )
            continue
        left_r = left_resources.get(path)
        right_r = right_resources.get(path)
        if left_r is None and right_r is not None:
            hunks.append(
                SkillVersionDiffHunk(
                    path=path,
                    kind="added",
                    left_digest=None,
                    right_digest=right_r.sha256,
                    left_preview=None,
                    right_preview=None,
                )
            )
        elif left_r is not None and right_r is None:
            hunks.append(
                SkillVersionDiffHunk(
                    path=path,
                    kind="removed",
                    left_digest=left_r.sha256,
                    right_digest=None,
                    left_preview=None,
                    right_preview=None,
                )
            )
        elif left_r is not None and right_r is not None:
            kind = "changed" if left_r.sha256 != right_r.sha256 else "unchanged_meta"
            hunks.append(
                SkillVersionDiffHunk(
                    path=path,
                    kind=kind,  # type: ignore[arg-type]
                    left_digest=left_r.sha256,
                    right_digest=right_r.sha256,
                    left_preview=None,
                    right_preview=None,
                )
            )

    return SkillVersionDiffResult(
        package_id=package_id,
        left_version_id=left.id,
        right_version_id=right.id,
        left_content_digest=left.content_digest,
        right_content_digest=right.content_digest,
        left_metadata=left_meta,
        right_metadata=right_meta,
        hunks=hunks,
        resource_bytes_excluded=True,
        secrets_excluded=True,
        unbounded_bodies_excluded=True,
    )
