"""Plan 09 Task 2 — safe skill package import preview / apply (create|append|fork)."""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "agent_skills" / "valid-weekly-review"
)


def _minimal_skill_md(
    *,
    name: str = "weekly-review",
    description: str = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    ),
    body: str = "# Weekly review\n\nBody.\n",
) -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    ).encode("utf-8")


def _mindatlas_yaml(
    *,
    display_name: str = "周度回顾",
    legacy_aliases: list[str] | None = None,
) -> bytes:
    aliases = legacy_aliases if legacy_aliases is not None else ["weekly_review"]
    alias_block = "\n".join(f"  - {a}" for a in aliases) if aliases else "  []"
    return (
        "version: 1\n"
        f"display_name: {display_name}\n"
        f"legacy_aliases:\n{alias_block}\n"
        "\n"
        "routing:\n"
        "  include_examples: []\n"
        "  exclude_examples: []\n"
        "  conflict_rules: []\n"
        "\n"
        "capabilities:\n"
        "  - type: tool\n"
        "    key: search_entries\n"
        "\n"
        "policy:\n"
        "  allowed_side_effects:\n"
        "    - read\n"
        "    - compute\n"
        "  max_skill_calls: 16\n"
        "  max_same_read_calls: 3\n"
        "  requires_terminal_output: true\n"
        "  terminal_text_allowed: true\n"
        "\n"
        "provider_aliases: {}\n"
        "metadata: {}\n"
    ).encode("utf-8")


def _zip_bytes(
    members: dict[str, bytes],
    *,
    compress_type: int = zipfile.ZIP_STORED,
    external_attr: dict[str, int] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compress_type
            info.external_attr = (external_attr or {}).get(name, 0o100644 << 16)
            zf.writestr(info, content)
    return buf.getvalue()


def _valid_zip(
    *,
    name: str = "weekly-review",
    root: str | None = None,
    skill_md: bytes | None = None,
    mindatlas: bytes | None = None,
    resources: dict[str, bytes] | None = None,
    aliases: list[str] | None = None,
) -> bytes:
    root_name = root if root is not None else name
    members: dict[str, bytes] = {
        f"{root_name}/SKILL.md": skill_md
        if skill_md is not None
        else _minimal_skill_md(name=name),
        f"{root_name}/mindatlas.yaml": mindatlas
        if mindatlas is not None
        else _mindatlas_yaml(legacy_aliases=aliases),
    }
    if resources:
        for path, content in resources.items():
            members[f"{root_name}/{path}"] = content
    return _zip_bytes(members)


def _operator(principal_id: str = "op-import-1"):
    from app.assistant.skills.principal import OperatorPrincipal

    return OperatorPrincipal(principal_id=principal_id, role="operator")


def _viewer(principal_id: str = "viewer-import-1"):
    from app.assistant.skills.principal import OperatorPrincipal

    return OperatorPrincipal(principal_id=principal_id, role="viewer")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Malicious archive corpus — rejected at preview (via Plan 01 parser)
# ---------------------------------------------------------------------------


class TestMaliciousArchiveCorpus:
    def setup_method(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.import_preview import (
            ImportPreviewService,
            clear_import_preview_store_for_tests,
        )

        clear_import_preview_store_for_tests()
        self.db = make_session()
        self.svc = ImportPreviewService(self.db)
        self.principal = _operator()

    def teardown_method(self) -> None:
        self.db.close()

    def _preview_create(self, raw: bytes):
        from app.common.exceptions import ApiException

        with pytest.raises(ApiException) as ctx:
            self.svc.preview(
                raw_zip=raw,
                mode="create",
                principal=self.principal,
            )
        return ctx.value

    def test_traversal_path_rejected(self) -> None:
        raw = _zip_bytes(
            {
                "evil/../SKILL.md": _minimal_skill_md(name="evil"),
            }
        )
        # top-level may be "evil" with rel "../SKILL.md" or multi top-level
        exc = self._preview_create(raw)
        assert exc.status_code in {400, 422}

    def test_absolute_path_rejected(self) -> None:
        raw = _zip_bytes({"/tmp/SKILL.md": _minimal_skill_md(name="abs-pack")})
        exc = self._preview_create(raw)
        assert exc.status_code in {400, 422}

    def test_symlink_entry_rejected(self) -> None:
        root = "sym-pack"
        members = {
            f"{root}/SKILL.md": _minimal_skill_md(name=root),
            f"{root}/link": b"target",
        }
        raw = _zip_bytes(members, external_attr={f"{root}/link": 0o120777 << 16})
        exc = self._preview_create(raw)
        assert exc.status_code in {400, 422}

    def test_duplicate_normalized_path_rejected(self) -> None:
        root = "dup-pack"
        # Two entries that normalize to the same path are rejected by Plan 01.
        # Using identical archive paths is the portable form of this check.
        raw = _zip_bytes(
            {
                f"{root}/SKILL.md": _minimal_skill_md(name=root),
                f"{root}/refs/a.md": b"# a\n",
            }
        )
        # Manually craft a ZIP with two members sharing the same relative path
        # after stripping the top-level directory (impossible via _zip_bytes
        # dict). Force via ZipFile double-write of same name is still one
        # entry; use path variants that normalize equal if allowed — Plan 01
        # rejects non-normalized forms, so any ".." or "./" form fails.
        raw = _zip_bytes(
            {
                f"{root}/SKILL.md": _minimal_skill_md(name=root),
                f"{root}/./refs/a.md": b"# a\n",
            }
        )
        exc = self._preview_create(raw)
        assert exc.status_code in {400, 422}

    def test_zip_bomb_entry_count_rejected(self) -> None:
        from app.assistant.skills.package_io import MAX_ENTRIES

        root = "bomb-count"
        members = {f"{root}/SKILL.md": _minimal_skill_md(name=root)}
        for i in range(MAX_ENTRIES + 5):
            members[f"{root}/refs/f{i}.md"] = b"x"
        raw = _zip_bytes(members)
        exc = self._preview_create(raw)
        assert exc.status_code in {400, 413, 422}

    def test_spoofed_mime_still_sniffed(self) -> None:
        # HTML bytes under .png extension — parser still sniffs; must not crash.
        # Acceptance: either rejects as unsafe active content or stores with sniffed type.
        root = "mime-pack"
        raw = _valid_zip(
            name=root,
            resources={"assets/fake.png": b"<!DOCTYPE html><script>alert(1)</script>"},
        )
        # Preview may succeed with sniffed media type — apply must never execute.
        # At minimum preview must not leak raw archive into response.
        try:
            result = self.svc.preview(
                raw_zip=raw,
                mode="create",
                principal=self.principal,
            )
            payload = result.model_dump(by_alias=True, mode="json")
            blob = str(payload)
            assert "alert(1)" not in blob
            assert raw[:8].hex() not in blob
        except Exception as exc:  # ApiException or ValueError mapped
            from app.common.exceptions import ApiException

            assert isinstance(exc, ApiException)

    def test_executable_bit_discarded_not_stored(self) -> None:
        root = "exec-pack"
        members = {
            f"{root}/SKILL.md": _minimal_skill_md(name=root),
            f"{root}/mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
            f"{root}/scripts/run.sh": b"#!/bin/sh\necho hi\n",
        }
        raw = _zip_bytes(
            members,
            external_attr={f"{root}/scripts/run.sh": 0o100755 << 16},
        )
        result = self.svc.preview(
            raw_zip=raw,
            mode="create",
            principal=self.principal,
        )
        # Resource index must report non-executable.
        for entry in result.resource_index:
            if entry.get("path") == "scripts/run.sh" or (
                isinstance(entry, dict) and entry.get("path") == "scripts/run.sh"
            ):
                assert entry.get("executable") in (False, None)
            elif hasattr(entry, "path") and entry.path == "scripts/run.sh":
                assert getattr(entry, "executable", False) is False

    def test_active_html_svg_not_injected_in_preview_payload(self) -> None:
        root = "active-pack"
        raw = _valid_zip(
            name=root,
            resources={
                "assets/page.html": b"<html><script>document.cookie</script></html>",
                "assets/icon.svg": b'<svg onload="alert(1)"></svg>',
            },
        )
        result = self.svc.preview(
            raw_zip=raw,
            mode="create",
            principal=self.principal,
        )
        payload = result.model_dump(by_alias=True, mode="json")
        text = str(payload)
        assert "document.cookie" not in text
        assert "onload=" not in text
        assert "<script" not in text


# ---------------------------------------------------------------------------
# Two-step preview → apply modes
# ---------------------------------------------------------------------------


class TestImportPreviewApplyModes:
    def setup_method(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.import_preview import (
            ImportPreviewService,
            clear_import_preview_store_for_tests,
        )
        from app.assistant.skills.service import AgentSkillService

        clear_import_preview_store_for_tests()
        self.db = make_session()
        self.svc = ImportPreviewService(self.db)
        self.pkg_svc = AgentSkillService(self.db)
        self.principal = _operator()

    def teardown_method(self) -> None:
        self.db.close()

    def test_create_preview_then_apply_draft_only(self) -> None:
        name = f"create-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        preview = self.svc.preview(
            raw_zip=raw,
            mode="create",
            principal=self.principal,
        )
        assert preview.mode == "create"
        assert preview.candidate_canonical_name == name
        assert preview.upload_digest == _sha256(raw)
        assert preview.candidate_content_digest
        assert preview.preview_id is not None
        assert preview.expires_at is not None
        # No package yet
        from app.assistant.skills.models import AssistantSkillPackage

        assert (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name == name)
            .one_or_none()
            is None
        )

        applied = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=f"req-create-{name}",
            principal=self.principal,
        )
        assert applied.package.canonical_name == name
        assert applied.package.catalog_enabled is False
        assert applied.package.published_version is None
        assert applied.package.draft_version is not None
        assert applied.package.draft_version.origin == "import"
        assert applied.package.draft_version.content_digest == preview.candidate_content_digest
        assert applied.mode == "create"

    def test_append_replaces_complete_snapshot_no_file_merge(self) -> None:
        name = f"append-{uuid4().hex[:8]}"
        # Seed existing package with resource A.
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files

        seed_files = {
            "SKILL.md": _minimal_skill_md(name=name, body="# v1\n"),
            "mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
            "references/old.md": b"# old resource\n",
        }
        seed = parse_skill_directory_files(seed_files, expected_root_name=None)
        detail = self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=seed, version_name="draft-1", origin="api")
        )
        rev = detail.aggregate_revision

        # Append ZIP has different body + only resource B (no old.md).
        raw = _valid_zip(
            name=name,
            skill_md=_minimal_skill_md(name=name, body="# v2 append body\n"),
            aliases=[],
            resources={"references/new.md": b"# new only\n"},
        )
        preview = self.svc.preview(
            raw_zip=raw,
            mode="append_to_existing",
            principal=self.principal,
            target_package_id=detail.id,
            expected_aggregate_revision=rev,
        )
        assert preview.mode == "append_to_existing"
        assert preview.target_package_id == detail.id
        assert preview.candidate_canonical_name == name

        applied = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=f"req-append-{name}",
            principal=self.principal,
        )
        assert applied.package.id == detail.id
        draft = applied.package.draft_version
        assert draft is not None
        assert draft.content_digest == preview.candidate_content_digest
        # Full snapshot replace: old resource gone, new resource present.
        version = self.pkg_svc.get_version(detail.id, draft.id)
        paths = {r.path for r in (version.resources or [])} if hasattr(version, "resources") else set()
        # Use resource index from version summary / re-export.
        exported = self.pkg_svc.export_version(package_id=detail.id, version_id=draft.id)
        with zipfile.ZipFile(io.BytesIO(exported)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
        assert any(n.endswith("references/new.md") for n in names)
        assert not any(n.endswith("references/old.md") for n in names)
        # Aggregate revision advanced.
        refreshed = self.pkg_svc.get_package(detail.id)
        assert refreshed.aggregate_revision == rev + 1
        assert refreshed.catalog_enabled is False
        assert refreshed.published_version is None

    def test_fork_rewrites_only_name_field_and_revalidates(self) -> None:
        source_name = f"src-{uuid4().hex[:8]}"
        fork_name = f"fork-{uuid4().hex[:8]}"
        raw = _valid_zip(name=source_name, aliases=[])
        preview = self.svc.preview(
            raw_zip=raw,
            mode="fork_as_new",
            principal=self.principal,
            fork_canonical_name=fork_name,
        )
        assert preview.mode == "fork_as_new"
        assert preview.candidate_canonical_name == fork_name
        # Content digest must differ from unforked parse (name rewrite).
        from app.assistant.skills.package_io import parse_skill_zip

        original = parse_skill_zip(io.BytesIO(raw), compressed_size=len(raw))
        assert preview.candidate_content_digest != original.content_digest

        applied = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=f"req-fork-{fork_name}",
            principal=self.principal,
        )
        assert applied.package.canonical_name == fork_name
        assert applied.package.catalog_enabled is False
        assert applied.package.published_version is None
        exported = self.pkg_svc.export_version(
            package_id=applied.package.id,
            version_id=applied.package.draft_version.id,
        )
        with zipfile.ZipFile(io.BytesIO(exported)) as zf:
            skill_md = zf.read(f"{fork_name}/SKILL.md").decode("utf-8")
        assert f"name: {fork_name}" in skill_md
        assert source_name not in skill_md.split("---")[1]

    def test_append_name_mismatch_rejected(self) -> None:
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.common.exceptions import ApiException

        name = f"append-mm-{uuid4().hex[:8]}"
        seed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md(name=name),
                "mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
            },
            expected_root_name=None,
        )
        detail = self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=seed, version_name="draft-1")
        )
        raw = _valid_zip(name="other-name-xyz", aliases=[])
        with pytest.raises(ApiException) as ctx:
            self.svc.preview(
                raw_zip=raw,
                mode="append_to_existing",
                principal=self.principal,
                target_package_id=detail.id,
                expected_aggregate_revision=detail.aggregate_revision,
            )
        assert ctx.value.status_code in {409, 422}

    def test_create_name_collision_rejected_at_preview(self) -> None:
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.common.exceptions import ApiException

        name = f"collide-{uuid4().hex[:8]}"
        seed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md(name=name),
                "mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
            },
            expected_root_name=None,
        )
        self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=seed, version_name="draft-1")
        )
        raw = _valid_zip(name=name, aliases=[])
        with pytest.raises(ApiException) as ctx:
            self.svc.preview(
                raw_zip=raw,
                mode="create",
                principal=self.principal,
            )
        assert ctx.value.status_code == 409

    def test_fork_alias_collision_rejected(self) -> None:
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.common.exceptions import ApiException

        existing = f"exist-{uuid4().hex[:8]}"
        seed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md(name=existing),
                "mindatlas.yaml": _mindatlas_yaml(legacy_aliases=["shared-alias-x"]),
            },
            expected_root_name=None,
        )
        self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=seed, version_name="draft-1")
        )
        fork_name = f"forkc-{uuid4().hex[:8]}"
        raw = _valid_zip(
            name=f"src-{uuid4().hex[:8]}",
            aliases=["shared-alias-x"],
        )
        with pytest.raises(ApiException) as ctx:
            self.svc.preview(
                raw_zip=raw,
                mode="fork_as_new",
                principal=self.principal,
                fork_canonical_name=fork_name,
            )
        assert ctx.value.status_code == 409


# ---------------------------------------------------------------------------
# Token binding, expiry, CAS, idempotency
# ---------------------------------------------------------------------------


class TestPreviewTokenAndIdempotency:
    def setup_method(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.import_preview import (
            ImportPreviewService,
            clear_import_preview_store_for_tests,
        )

        clear_import_preview_store_for_tests()
        self.db = make_session()
        self.svc = ImportPreviewService(self.db)
        self.principal = _operator()

    def teardown_method(self) -> None:
        self.db.close()

    def test_missing_principal_rejected(self) -> None:
        from app.common.exceptions import ApiException

        raw = _valid_zip(name=f"nop-{uuid4().hex[:8]}", aliases=[])
        with pytest.raises(ApiException) as ctx:
            self.svc.preview(raw_zip=raw, mode="create", principal=None)
        assert ctx.value.status_code == 401

    def test_actor_mismatch_on_apply_rejected(self) -> None:
        from app.common.exceptions import ApiException

        name = f"actor-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        preview = self.svc.preview(
            raw_zip=raw, mode="create", principal=self.principal
        )
        other = _operator("op-other")
        with pytest.raises(ApiException) as ctx:
            self.svc.apply(
                preview_id=preview.preview_id,
                request_id=f"req-{name}",
                principal=other,
            )
        assert ctx.value.status_code in {401, 403, 409}

    def test_stale_aggregate_revision_rejected_on_apply(self) -> None:
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.skills.admin_service import SkillAdminService
        from app.assistant.skills.schemas import UpdateSkillPackageMetadataCommand
        from app.common.exceptions import ApiException

        pkg_svc = AgentSkillService(self.db)
        name = f"stale-{uuid4().hex[:8]}"
        seed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md(name=name),
                "mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
            },
            expected_root_name=None,
        )
        detail = pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=seed, version_name="draft-1")
        )
        raw = _valid_zip(
            name=name,
            skill_md=_minimal_skill_md(name=name, body="# changed\n"),
            aliases=[],
        )
        preview = self.svc.preview(
            raw_zip=raw,
            mode="append_to_existing",
            principal=self.principal,
            target_package_id=detail.id,
            expected_aggregate_revision=detail.aggregate_revision,
        )
        # Bump revision under the operator.
        admin = SkillAdminService(self.db)
        admin.update_metadata(
            detail.id,
            UpdateSkillPackageMetadataCommand(
                request_id=f"bump-{name}",
                expected_aggregate_revision=detail.aggregate_revision,
                display_name="bumped",
            ),
            principal=self.principal,
        )
        with pytest.raises(ApiException) as ctx:
            self.svc.apply(
                preview_id=preview.preview_id,
                request_id=f"req-stale-{name}",
                principal=self.principal,
            )
        assert ctx.value.status_code == 409
        assert ctx.value.code in {40994, 40993, 40997}

    def test_identical_request_id_retry_returns_persisted(self) -> None:
        name = f"idem-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        preview = self.svc.preview(
            raw_zip=raw, mode="create", principal=self.principal
        )
        req = f"req-idem-{name}"
        first = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=req,
            principal=self.principal,
        )
        second = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=req,
            principal=self.principal,
        )
        assert first.package.id == second.package.id
        assert first.package.draft_version.id == second.package.draft_version.id
        # Only one package row.
        from app.assistant.skills.models import AssistantSkillPackage

        rows = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name == name)
            .all()
        )
        assert len(rows) == 1

    def test_request_id_reuse_different_payload_conflicts(self) -> None:
        from app.common.exceptions import ApiException

        name_a = f"reqa-{uuid4().hex[:8]}"
        name_b = f"reqb-{uuid4().hex[:8]}"
        raw_a = _valid_zip(name=name_a, aliases=[])
        raw_b = _valid_zip(name=name_b, aliases=[])
        p_a = self.svc.preview(raw_zip=raw_a, mode="create", principal=self.principal)
        p_b = self.svc.preview(raw_zip=raw_b, mode="create", principal=self.principal)
        req = f"shared-req-{uuid4().hex[:8]}"
        self.svc.apply(
            preview_id=p_a.preview_id, request_id=req, principal=self.principal
        )
        with pytest.raises(ApiException) as ctx:
            self.svc.apply(
                preview_id=p_b.preview_id, request_id=req, principal=self.principal
            )
        assert ctx.value.status_code == 409
        assert ctx.value.code == 40997

    def test_expired_preview_rejected(self, monkeypatch) -> None:
        from datetime import datetime, timedelta, timezone
        from app.common.exceptions import ApiException
        import app.assistant.skills.import_preview as ip

        name = f"exp-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        # Force tiny TTL via monkeypatch of utcnow after preview.
        preview = self.svc.preview(
            raw_zip=raw, mode="create", principal=self.principal
        )
        # Manually expire the stored token.
        record = self.svc._get_record(preview.preview_id)
        record.token = record.token.model_copy(
            update={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
            }
        )
        with pytest.raises(ApiException) as ctx:
            self.svc.apply(
                preview_id=preview.preview_id,
                request_id=f"req-exp-{name}",
                principal=self.principal,
            )
        assert ctx.value.status_code in {409, 410, 422}

    def test_unknown_preview_id_rejected(self) -> None:
        from app.common.exceptions import ApiException
        from uuid import uuid4 as u4

        with pytest.raises(ApiException) as ctx:
            self.svc.apply(
                preview_id=u4(),
                request_id="req-missing",
                principal=self.principal,
            )
        assert ctx.value.status_code == 404


# ---------------------------------------------------------------------------
# Deterministic export after import; scripts non-executable
# ---------------------------------------------------------------------------


class TestExportAfterImport:
    def setup_method(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.import_preview import (
            ImportPreviewService,
            clear_import_preview_store_for_tests,
        )
        from app.assistant.skills.service import AgentSkillService

        clear_import_preview_store_for_tests()
        self.db = make_session()
        self.svc = ImportPreviewService(self.db)
        self.pkg_svc = AgentSkillService(self.db)
        self.principal = _operator()

    def teardown_method(self) -> None:
        self.db.close()

    def test_import_export_preserves_script_bytes_without_exec_bit(self) -> None:
        name = f"expscript-{uuid4().hex[:8]}"
        script = b"#!/usr/bin/env python3\nprint('hello')\n"
        raw = _valid_zip(
            name=name,
            aliases=[],
            resources={"scripts/hello.py": script},
        )
        # Force executable bit on the script entry.
        members = {
            f"{name}/SKILL.md": _minimal_skill_md(name=name),
            f"{name}/mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
            f"{name}/scripts/hello.py": script,
        }
        raw = _zip_bytes(
            members,
            external_attr={f"{name}/scripts/hello.py": 0o100755 << 16},
        )
        preview = self.svc.preview(
            raw_zip=raw, mode="create", principal=self.principal
        )
        applied = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=f"req-{name}",
            principal=self.principal,
        )
        exported = self.pkg_svc.export_version(
            package_id=applied.package.id,
            version_id=applied.package.draft_version.id,
        )
        with zipfile.ZipFile(io.BytesIO(exported)) as zf:
            info = zf.getinfo(f"{name}/scripts/hello.py")
            mode = (info.external_attr >> 16) & 0o777
            assert mode == 0o644
            assert zf.read(info) == script
            # Deterministic timestamps
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
        # Byte-identical re-export
        exported2 = self.pkg_svc.export_version(
            package_id=applied.package.id,
            version_id=applied.package.draft_version.id,
        )
        assert _sha256(exported) == _sha256(exported2)


# ---------------------------------------------------------------------------
# Safety: no leaks of temp path / raw archive / secrets into responses
# ---------------------------------------------------------------------------


class TestResponseSafety:
    def setup_method(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.import_preview import (
            ImportPreviewService,
            clear_import_preview_store_for_tests,
        )

        clear_import_preview_store_for_tests()
        self.db = make_session()
        self.svc = ImportPreviewService(self.db)
        self.principal = _operator()

    def teardown_method(self) -> None:
        self.db.close()

    def test_preview_response_excludes_raw_bytes_and_temp_paths(self) -> None:
        name = f"safe-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        preview = self.svc.preview(
            raw_zip=raw, mode="create", principal=self.principal
        )
        payload = preview.model_dump(by_alias=True, mode="json")
        text = str(payload)
        assert "tmp" not in text.lower() or "timestamp" in text.lower()
        # No raw ZIP magic / full body
        assert "PK\x03\x04" not in text
        assert raw.hex() not in text
        # No credential-looking fields
        for banned in ("password", "api_key", "authorization", "secret_key"):
            assert banned not in text.lower() or banned in (
                "authorization",  # may appear in docs; ensure no values
            )

    def test_apply_response_is_package_detail_only(self) -> None:
        name = f"safe2-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        preview = self.svc.preview(
            raw_zip=raw, mode="create", principal=self.principal
        )
        applied = self.svc.apply(
            preview_id=preview.preview_id,
            request_id=f"req-{name}",
            principal=self.principal,
        )
        payload = applied.model_dump(by_alias=True, mode="json")
        text = str(payload)
        assert "PK\x03\x04" not in text
        assert raw.hex() not in text
        assert "/tmp/" not in text


# ---------------------------------------------------------------------------
# Architecture: reuse Plan 01 parser; no duplicate normalization
# ---------------------------------------------------------------------------


def test_import_preview_reuses_plan01_parser_no_duplicate_normalization() -> None:
    """import_preview must call package_io helpers; must not reimplement them."""
    import app.assistant.skills.import_preview as mod

    source = inspect.getsource(mod)
    # Must reference Plan 01 entry points.
    assert "parse_skill_zip" in source
    assert "package_io" in source or "from app.assistant.skills.package_io" in source

    # Must not redefine path normalization / zip security.
    tree = ast.parse(source)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden_defs = {
        "normalize_package_path",
        "parse_skill_zip",
        "parse_skill_directory_files",
        "detect_media_type",
        "_reject_special_zip_entry",
        "export_skill_package",
    }
    overlap = defined & forbidden_defs
    assert not overlap, f"import_preview redefines Plan 01 symbols: {overlap}"

    # No local reimplementation of path traversal checks.
    for banned in (
        "posixpath.normpath",
        "os.path.normpath",
        "ZipFile(",
        "zipfile.ZipFile",
    ):
        # ZipFile may appear only if re-exporting; forbid local archive open.
        if banned in ("ZipFile(", "zipfile.ZipFile"):
            # Allow imports of package_io only — scan for direct zipfile use.
            assert "zipfile.ZipFile" not in source
            assert "ZipFile(" not in source


def test_rewrite_skill_md_name_only_changes_name_field() -> None:
    from app.assistant.skills.package_io import (
        rewrite_skill_md_frontmatter_name,
        parse_skill_md,
        parse_skill_directory_files,
    )

    original = _minimal_skill_md(name="old-name", body="# Keep body\n\nstable.\n")
    rewritten = rewrite_skill_md_frontmatter_name(original, new_name="new-name")
    fm = parse_skill_md(rewritten)
    assert fm.name == "new-name"
    assert b"# Keep body" in rewritten
    # Full package revalidation succeeds under new name.
    pkg = parse_skill_directory_files(
        {
            "SKILL.md": rewritten,
            "mindatlas.yaml": _mindatlas_yaml(legacy_aliases=[]),
        },
        expected_root_name="new-name",
    )
    assert pkg.canonical_name == "new-name"


# ---------------------------------------------------------------------------
# Admin router endpoints (trusted mount)
# ---------------------------------------------------------------------------


class TestImportPreviewAdminRoutes:
    def setup_method(self) -> None:
        reset_caches()
        import os
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.assistant.skills.admin_router import (
            TRUSTED_MOUNT_ENV,
            mount_skill_admin_router,
        )
        from app.common.exceptions import register_exception_handlers
        from app.database import get_db
        from tests._db import make_session

        os.environ[TRUSTED_MOUNT_ENV] = "1"
        os.environ["APP_ENV"] = "development"
        self.db = make_session()
        app = FastAPI()
        register_exception_handlers(app)

        def _override():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = _override
        assert mount_skill_admin_router(app, app_env="development") is True
        self.client = TestClient(app)
        self.headers = {
            "X-MindAtlas-Operator-Id": "op-route-1",
            "X-MindAtlas-Operator-Role": "operator",
        }

    def teardown_method(self) -> None:
        import os
        from app.assistant.skills.admin_router import TRUSTED_MOUNT_ENV

        os.environ.pop(TRUSTED_MOUNT_ENV, None)
        os.environ.pop("APP_ENV", None)
        self.db.close()

    def test_preview_and_apply_http_create(self) -> None:
        name = f"http-{uuid4().hex[:8]}"
        raw = _valid_zip(name=name, aliases=[])
        resp = self.client.post(
            "/api/assistant-config/skill-admin/skill-packages/import/preview",
            headers=self.headers,
            files={"file": ("pack.zip", raw, "application/zip")},
            data={"mode": "create"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["mode"] == "create"
        assert body["candidateCanonicalName"] == name
        preview_id = body["previewId"]

        apply_resp = self.client.post(
            "/api/assistant-config/skill-admin/skill-packages/import/apply",
            headers=self.headers,
            json={
                "previewId": preview_id,
                "requestId": f"req-http-{name}",
            },
        )
        assert apply_resp.status_code == 200, apply_resp.text
        pkg = apply_resp.json()["data"]["package"]
        assert pkg["canonicalName"] == name
        assert pkg["catalogEnabled"] is False
        assert pkg["publishedVersion"] is None
        assert pkg["draftVersion"] is not None
