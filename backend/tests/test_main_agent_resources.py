"""Skill resource bounded read tests (Plan 04 Task 7)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
RUN_ID = UUID("00000000-0000-4000-8000-000000000501")
PKG = UUID("00000000-0000-4000-8000-000000000511")
VER = UUID("00000000-0000-4000-8000-000000000521")
PROFILE_VERSION = UUID("00000000-0000-4000-8000-000000000531")


class _MemoryResources:
    def __init__(self, blobs: dict[tuple[UUID, UUID, str], bytes], meta=None):
        self.blobs = blobs
        self.meta = meta or {}

    def get_resource_bytes(self, package_id, version_id, path):
        key = (package_id, version_id, path)
        if key not in self.blobs:
            raise KeyError(path)
        return self.blobs[key]

    def get_resource_meta(self, package_id, version_id, path):
        return self.meta.get((package_id, version_id, path), {"media_type": "text/plain"})


def _manifest(active: bool = True):
    from app.assistant.domain.contracts import (
        ResolvedMainAgentRef,
        ResolvedSkillRef,
        ResolvedRunManifestRevision,
        append_skill_activation,
        create_base_run_manifest,
    )

    base = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=ResolvedMainAgentRef(
            profile_id=uuid4(),
            version_id=PROFILE_VERSION,
            profile_key="default",
            sequence=1,
            content_digest=DIGEST_A,
        ),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    if not active:
        return base
    skill = ResolvedSkillRef(
        package_id=PKG,
        version_id=VER,
        canonical_name="weekly-review",
        sequence=1,
        content_digest=DIGEST_A,
        version_digest=DIGEST_A,
        requested_name_normalized=None,
        resolved_via_alias_id=None,
    )
    return append_skill_activation(base, skill=skill, capabilities=())


def test_active_resource_read_utf8_chunk() -> None:
    from app.assistant.main_agent.resources import read_skill_resource_chunk

    body = b"hello resource world"
    port = _MemoryResources({(PKG, VER, "references/guide.md"): body})
    result = read_skill_resource_chunk(
        call_id="r1",
        validated_input={
            "skillVersionId": str(VER),
            "path": "references/guide.md",
            "offset": 0,
            "limit": 5,
        },
        manifest=_manifest(True),
        resource_port=port,
    )
    assert result.status == "completed"
    out = result.structured_output
    assert out["encoding"] == "utf-8"
    assert out["content"] == "hello"
    assert out["returnedBytes"] == 5
    assert out["eof"] is False
    assert out["totalSize"] == len(body)


def test_inactive_version_denied() -> None:
    from app.assistant.main_agent.resources import RESOURCE_NOT_ACTIVE, read_skill_resource_chunk

    port = _MemoryResources({(PKG, VER, "references/guide.md"): b"x"})
    result = read_skill_resource_chunk(
        call_id="r2",
        validated_input={
            "skillVersionId": str(VER),
            "path": "references/guide.md",
        },
        manifest=_manifest(False),
        resource_port=port,
    )
    assert result.status == "failed"
    assert result.error.safe_code == RESOURCE_NOT_ACTIVE


def test_missing_path() -> None:
    from app.assistant.main_agent.resources import RESOURCE_NOT_FOUND, read_skill_resource_chunk

    port = _MemoryResources({})
    result = read_skill_resource_chunk(
        call_id="r3",
        validated_input={
            "skillVersionId": str(VER),
            "path": "missing.md",
        },
        manifest=_manifest(True),
        resource_port=port,
    )
    assert result.status == "failed"
    assert result.error.safe_code == RESOURCE_NOT_FOUND


def test_path_traversal_rejected() -> None:
    from app.assistant.main_agent.resources import RESOURCE_NOT_FOUND, read_skill_resource_chunk

    port = _MemoryResources({(PKG, VER, "references/guide.md"): b"x"})
    result = read_skill_resource_chunk(
        call_id="r4",
        validated_input={
            "skillVersionId": str(VER),
            "path": "../secrets.txt",
        },
        manifest=_manifest(True),
        resource_port=port,
    )
    assert result.status == "failed"
    assert result.error.safe_code == RESOURCE_NOT_FOUND


def test_binary_base64_and_eof() -> None:
    from app.assistant.main_agent.resources import read_skill_resource_chunk

    body = bytes([0xFF, 0xFE, 0x00, 0x01])
    port = _MemoryResources(
        {(PKG, VER, "assets/icon.bin"): body},
        meta={(PKG, VER, "assets/icon.bin"): {"media_type": "application/octet-stream"}},
    )
    result = read_skill_resource_chunk(
        call_id="r5",
        validated_input={
            "skillVersionId": str(VER),
            "path": "assets/icon.bin",
            "offset": 0,
            "limit": 100,
        },
        manifest=_manifest(True),
        resource_port=port,
    )
    assert result.status == "completed"
    assert result.structured_output["encoding"] == "base64"
    assert result.structured_output["eof"] is True


def test_range_invalid() -> None:
    from app.assistant.main_agent.resources import RESOURCE_RANGE_INVALID, read_skill_resource_chunk

    port = _MemoryResources({(PKG, VER, "references/guide.md"): b"abc"})
    result = read_skill_resource_chunk(
        call_id="r6",
        validated_input={
            "skillVersionId": str(VER),
            "path": "references/guide.md",
            "offset": 10,
            "limit": 1,
        },
        manifest=_manifest(True),
        resource_port=port,
    )
    assert result.status == "failed"
    assert result.error.safe_code == RESOURCE_RANGE_INVALID


def test_scripts_returned_inert() -> None:
    from app.assistant.main_agent.resources import read_skill_resource_chunk

    port = _MemoryResources({(PKG, VER, "scripts/run.py"): b"print('hi')\n"})
    result = read_skill_resource_chunk(
        call_id="r7",
        validated_input={
            "skillVersionId": str(VER),
            "path": "scripts/run.py",
        },
        manifest=_manifest(True),
        resource_port=port,
    )
    assert result.status == "completed"
    assert "print" in result.structured_output["content"]
    # No execute side channel in result.
    assert "executed" not in result.structured_output
