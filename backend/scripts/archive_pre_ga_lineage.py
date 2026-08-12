"""Verify and atomically archive the unpublished pre-GA Alembic lineage."""

from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
import warnings

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schema.canonical import (  # noqa: E402
    canonical_json_bytes,
    sha256_canonical_json,
)
from app.schema.application_contract import (  # noqa: E402
    LogicalApplicationContractError,
    load_logical_application_contract,
)
from app.schema.identity import (  # noqa: E402
    SchemaIdentityError,
    load_expected_schema_contract,
)
from app.schema.sql_objects import (  # noqa: E402
    SchemaManifestError,
    load_pre_squash_snapshot,
    validate_manifest_set,
)


LIVE_VERSION_DIR = BACKEND_ROOT / "alembic" / "versions"
STAGED_ROOT = (
    BACKEND_ROOT
    / "alembic"
    / "baseline_staging"
    / "pre_ga_v1_0001_clean_baseline.py"
)
ARCHIVE_ID = "pre_ga_v1_superseded"
ARCHIVE_ROOT = BACKEND_ROOT / "alembic" / "archive" / ARCHIVE_ID
DEVIATION_PATH = (
    REPO_ROOT
    / "docs"
    / "superpowers"
    / "evidence"
    / "2026-07-28-pre-ga-clean-baseline-deviation.md"
)
ARCHIVAL_REASON = (
    "unpublished_lineage_replaced_by_first_supported_pre_ga_baseline"
)
ORIGINAL_FINAL_HEAD = "b6e2d4f8a901"
FIRST_REVISION = "a7d8f1424a99"
REVISION_COUNT = 60
_LANGCHAIN_ALLOWED_OBJECTS_WARNING = (
    "The default value of `allowed_objects` will change in a future version. "
    "Pass an explicit value (e.g., allowed_objects='messages' or "
    "allowed_objects='core') to suppress this warning."
)
_ALEMBIC_PATH_SEPARATOR_WARNINGS = frozenset(
    {
        "No path_separator found in configuration; falling back to legacy "
        "splitting on spaces, commas, and colons for prepend_sys_path.  "
        "Consider adding path_separator=os to Alembic config.",
        "The version_path_separator configuration parameter is deprecated; "
        "please use path_separator",
    }
)
README_TEXT = """# Superseded pre-GA Alembic lineage

These artifacts preserve unpublished development history only.

- The `.py.archived` suffix is intentional so Python cannot import them.
- This directory intentionally has no `__init__.py`.
- This directory is excluded from Alembic `version_locations`.
- These artifacts are not a supported upgrade or restore source.
- Verification parses metadata and hashes without importing or executing files.
"""

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "archiveId",
        "revisionCount",
        "firstRevision",
        "originalFinalHead",
        "archivalReason",
        "designDeviationEvidenceDigest",
        "preSquashSnapshotDigest",
        "revisions",
        "manifestDigest",
    }
)
_REVISION_ENTRY_KEYS = frozenset(
    {
        "order",
        "relativePath",
        "originalRelativePath",
        "revision",
        "parent",
        "sha256",
    }
)
_IMPORT_SCAN_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)


class ArchiveLineageError(RuntimeError):
    """Bounded archive failure safe for automation and logs."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


@dataclass(frozen=True)
class ArchivePaths:
    repo_root: Path
    backend_root: Path
    live_version_dir: Path
    staged_root: Path
    archive_root: Path
    deviation_path: Path
    manifest_root: Path
    alembic_ini: Path
    alembic_env: Path


DEFAULT_ARCHIVE_PATHS = ArchivePaths(
    repo_root=REPO_ROOT,
    backend_root=BACKEND_ROOT,
    live_version_dir=LIVE_VERSION_DIR,
    staged_root=STAGED_ROOT,
    archive_root=ARCHIVE_ROOT,
    deviation_path=DEVIATION_PATH,
    manifest_root=BACKEND_ROOT / "app" / "schema" / "manifests",
    alembic_ini=BACKEND_ROOT / "alembic.ini",
    alembic_env=BACKEND_ROOT / "alembic" / "env.py",
)


@dataclass(frozen=True)
class ParsedRevision:
    path: Path
    revision: str
    parent: str | None


@dataclass(frozen=True)
class ArchiveRevisionEntry:
    order: int
    relative_path: str
    original_relative_path: str
    revision: str
    parent: str | None
    sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "order": self.order,
            "relativePath": self.relative_path,
            "originalRelativePath": self.original_relative_path,
            "revision": self.revision,
            "parent": self.parent,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ArchiveManifest:
    schema_version: int
    archive_id: str
    revision_count: int
    first_revision: str
    original_final_head: str
    archival_reason: str
    design_deviation_evidence_digest: str
    pre_squash_snapshot_digest: str
    revisions: tuple[ArchiveRevisionEntry, ...]
    manifest_digest: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "archiveId": self.archive_id,
            "revisionCount": self.revision_count,
            "firstRevision": self.first_revision,
            "originalFinalHead": self.original_final_head,
            "archivalReason": self.archival_reason,
            "designDeviationEvidenceDigest": (
                self.design_deviation_evidence_digest
            ),
            "preSquashSnapshotDigest": self.pre_squash_snapshot_digest,
            "revisions": [item.to_payload() for item in self.revisions],
            "manifestDigest": self.manifest_digest,
        }


@dataclass(frozen=True)
class RevisionGraph:
    revisions: tuple[ParsedRevision, ...]

    def require_linear_chain(self, *, final_head: str) -> tuple[ParsedRevision, ...]:
        by_revision = {item.revision: item for item in self.revisions}
        if len(by_revision) != len(self.revisions) or final_head not in by_revision:
            raise ArchiveLineageError("archive_revision_graph_invalid")
        roots = tuple(item for item in self.revisions if item.parent is None)
        if len(roots) != 1:
            raise ArchiveLineageError("archive_revision_graph_invalid")

        children: dict[str, list[ParsedRevision]] = {}
        for item in self.revisions:
            if item.parent is None:
                continue
            if item.parent not in by_revision:
                raise ArchiveLineageError("archive_revision_graph_invalid")
            children.setdefault(item.parent, []).append(item)
        if any(len(items) != 1 for items in children.values()):
            raise ArchiveLineageError("archive_revision_graph_invalid")

        ordered: list[ParsedRevision] = []
        current = roots[0]
        while True:
            if current in ordered:
                raise ArchiveLineageError("archive_revision_graph_invalid")
            ordered.append(current)
            next_items = children.get(current.revision, [])
            if not next_items:
                break
            current = next_items[0]
        if (
            len(ordered) != len(self.revisions)
            or ordered[-1].revision != final_head
        ):
            raise ArchiveLineageError("archive_revision_graph_invalid")
        return tuple(ordered)


def _literal_assignment(tree: ast.Module, name: str):  # noqa: ANN202
    values: list[object] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            values.append(ast.literal_eval(node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            values.append(ast.literal_eval(node.value))
    if len(values) != 1:
        raise ArchiveLineageError("archive_revision_metadata_invalid")
    return values[0]


def parse_revision_file(path: Path) -> ParsedRevision:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        revision = _literal_assignment(tree, "revision")
        parent = _literal_assignment(tree, "down_revision")
    except ArchiveLineageError:
        raise
    except (OSError, UnicodeError, SyntaxError, ValueError):
        raise ArchiveLineageError("archive_revision_metadata_invalid") from None
    if not isinstance(revision, str) or not revision:
        raise ArchiveLineageError("archive_revision_metadata_invalid")
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise ArchiveLineageError("archive_revision_metadata_invalid")
    return ParsedRevision(path=path, revision=revision, parent=parent)


def parse_revision_files(paths: Iterable[Path]) -> RevisionGraph:
    revisions = tuple(parse_revision_file(path) for path in sorted(paths))
    if not revisions:
        raise ArchiveLineageError("archive_revision_graph_invalid")
    return RevisionGraph(revisions)


def scan_python_imports(root: Path, target: str) -> tuple[str, ...]:
    hits: list[str] = []
    walk_errors: list[OSError] = []
    python_paths: list[Path] = []
    for current, directory_names, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=walk_errors.append,
    ):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _IMPORT_SCAN_IGNORED_DIRECTORIES
        )
        python_paths.extend(
            Path(current) / name
            for name in sorted(filenames)
            if name.endswith(".py")
        )
    if walk_errors:
        raise ArchiveLineageError("archive_import_scan_failed") from None
    for path in sorted(python_paths):
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
        except (OSError, UnicodeError, SyntaxError):
            raise ArchiveLineageError("archive_import_scan_failed") from None
        for node in ast.walk(tree):
            import_targets: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                import_targets = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                import_targets = tuple(
                    f"{module}.{alias.name}" if module else alias.name
                    for alias in node.names
                )
            for import_target in import_targets:
                if import_target == target or import_target.startswith(
                    f"{target}."
                ):
                    hits.append(
                        f"{path.relative_to(root).as_posix()}:"
                        f"{node.lineno}:{import_target}"
                    )
    return tuple(sorted(hits))


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise ArchiveLineageError("archive_input_unavailable") from None


def _manifest_without_digest(
    *,
    revisions: tuple[ArchiveRevisionEntry, ...],
    paths: ArchivePaths,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "archiveId": ARCHIVE_ID,
        "revisionCount": REVISION_COUNT,
        "firstRevision": FIRST_REVISION,
        "originalFinalHead": ORIGINAL_FINAL_HEAD,
        "archivalReason": ARCHIVAL_REASON,
        "designDeviationEvidenceDigest": sha256_file(paths.deviation_path),
        "preSquashSnapshotDigest": load_pre_squash_snapshot(
            paths.manifest_root / "pre_ga_v1-pre-squash-schema.json"
        ).manifest_digest,
        "revisions": [item.to_payload() for item in revisions],
    }


def build_archive_manifest(
    paths: ArchivePaths = DEFAULT_ARCHIVE_PATHS,
) -> ArchiveManifest:
    ordered = parse_revision_files(
        paths.live_version_dir.glob("*.py")
    ).require_linear_chain(final_head=ORIGINAL_FINAL_HEAD)
    if len(ordered) != REVISION_COUNT or ordered[0].revision != FIRST_REVISION:
        raise ArchiveLineageError("archive_revision_graph_invalid")
    revisions = tuple(
        ArchiveRevisionEntry(
            order=index,
            relative_path=(
                paths.archive_root / f"{item.path.name}.archived"
            ).relative_to(paths.repo_root).as_posix(),
            original_relative_path=item.path.relative_to(
                paths.repo_root
            ).as_posix(),
            revision=item.revision,
            parent=item.parent,
            sha256=sha256_file(item.path),
        )
        for index, item in enumerate(ordered, start=1)
    )
    payload = _manifest_without_digest(revisions=revisions, paths=paths)
    return ArchiveManifest(
        schema_version=1,
        archive_id=ARCHIVE_ID,
        revision_count=REVISION_COUNT,
        first_revision=FIRST_REVISION,
        original_final_head=ORIGINAL_FINAL_HEAD,
        archival_reason=ARCHIVAL_REASON,
        design_deviation_evidence_digest=payload[
            "designDeviationEvidenceDigest"
        ],
        pre_squash_snapshot_digest=payload["preSquashSnapshotDigest"],
        revisions=revisions,
        manifest_digest=sha256_canonical_json(payload),
    )


def archive_manifest_bytes(manifest: ArchiveManifest) -> bytes:
    try:
        return canonical_json_bytes(manifest.to_payload()) + b"\n"
    except (TypeError, ValueError):
        raise ArchiveLineageError("archive_manifest_invalid") from None


class _DuplicateJsonMember(ValueError):
    pass


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateJsonMember
        payload[key] = value
    return payload


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _require_relative_path(value: object, *, prefix: str, suffix: str) -> str:
    if not isinstance(value, str):
        raise ArchiveLineageError("archive_manifest_invalid")
    path = Path(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
        or not value.startswith(prefix)
        or not value.endswith(suffix)
    ):
        raise ArchiveLineageError("archive_manifest_invalid")
    return value


def _parse_manifest_revision(raw: object, *, expected_order: int) -> ArchiveRevisionEntry:
    if not isinstance(raw, dict) or set(raw) != _REVISION_ENTRY_KEYS:
        raise ArchiveLineageError("archive_manifest_invalid")
    if type(raw.get("order")) is not int or raw["order"] != expected_order:
        raise ArchiveLineageError("archive_manifest_invalid")
    revision = raw.get("revision")
    parent = raw.get("parent")
    if not isinstance(revision, str) or not revision:
        raise ArchiveLineageError("archive_manifest_invalid")
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise ArchiveLineageError("archive_manifest_invalid")
    relative_path = _require_relative_path(
        raw.get("relativePath"),
        prefix=f"backend/alembic/archive/{ARCHIVE_ID}/",
        suffix=".py.archived",
    )
    original_relative_path = _require_relative_path(
        raw.get("originalRelativePath"),
        prefix="backend/alembic/versions/",
        suffix=".py",
    )
    if Path(relative_path).name != f"{Path(original_relative_path).name}.archived":
        raise ArchiveLineageError("archive_manifest_invalid")
    digest = raw.get("sha256")
    if not _valid_sha256(digest):
        raise ArchiveLineageError("archive_manifest_invalid")
    return ArchiveRevisionEntry(
        order=expected_order,
        relative_path=relative_path,
        original_relative_path=original_relative_path,
        revision=revision,
        parent=parent,
        sha256=digest,
    )


def load_archive_manifest(
    path: Path,
    *,
    paths: ArchivePaths = DEFAULT_ARCHIVE_PATHS,
) -> ArchiveManifest:
    try:
        encoded = path.read_bytes()
        raw = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ArchiveLineageError("archive_manifest_invalid") from None
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_KEYS:
        raise ArchiveLineageError("archive_manifest_invalid")
    raw_revisions = raw.get("revisions")
    if not isinstance(raw_revisions, list) or len(raw_revisions) != REVISION_COUNT:
        raise ArchiveLineageError("archive_manifest_invalid")
    revisions = tuple(
        _parse_manifest_revision(item, expected_order=index)
        for index, item in enumerate(raw_revisions, start=1)
    )
    if (
        type(raw.get("schemaVersion")) is not int
        or raw["schemaVersion"] != 1
        or raw.get("archiveId") != ARCHIVE_ID
        or type(raw.get("revisionCount")) is not int
        or raw["revisionCount"] != REVISION_COUNT
        or raw.get("firstRevision") != FIRST_REVISION
        or raw.get("originalFinalHead") != ORIGINAL_FINAL_HEAD
        or raw.get("archivalReason") != ARCHIVAL_REASON
        or raw.get("designDeviationEvidenceDigest")
        != sha256_file(paths.deviation_path)
        or raw.get("preSquashSnapshotDigest")
        != load_pre_squash_snapshot(
            paths.manifest_root / "pre_ga_v1-pre-squash-schema.json"
        ).manifest_digest
        or not _valid_sha256(raw.get("manifestDigest"))
    ):
        raise ArchiveLineageError("archive_manifest_invalid")
    for index, item in enumerate(revisions):
        expected_parent = None if index == 0 else revisions[index - 1].revision
        if item.parent != expected_parent:
            raise ArchiveLineageError("archive_manifest_invalid")
    if revisions[0].revision != FIRST_REVISION or (
        revisions[-1].revision != ORIGINAL_FINAL_HEAD
    ):
        raise ArchiveLineageError("archive_manifest_invalid")
    if len({item.revision for item in revisions}) != REVISION_COUNT or len(
        {item.relative_path for item in revisions}
    ) != REVISION_COUNT:
        raise ArchiveLineageError("archive_manifest_invalid")
    digest_payload = {key: value for key, value in raw.items() if key != "manifestDigest"}
    if sha256_canonical_json(digest_payload) != raw["manifestDigest"]:
        raise ArchiveLineageError("archive_manifest_digest_mismatch")
    manifest = ArchiveManifest(
        schema_version=1,
        archive_id=ARCHIVE_ID,
        revision_count=REVISION_COUNT,
        first_revision=FIRST_REVISION,
        original_final_head=ORIGINAL_FINAL_HEAD,
        archival_reason=ARCHIVAL_REASON,
        design_deviation_evidence_digest=raw[
            "designDeviationEvidenceDigest"
        ],
        pre_squash_snapshot_digest=raw["preSquashSnapshotDigest"],
        revisions=revisions,
        manifest_digest=raw["manifestDigest"],
    )
    if encoded != archive_manifest_bytes(manifest):
        raise ArchiveLineageError("archive_manifest_invalid")
    return manifest


def _relative_input_paths(paths: ArchivePaths) -> tuple[str, ...]:
    try:
        return tuple(
            path.relative_to(paths.repo_root).as_posix()
            for path in (
                paths.live_version_dir,
                paths.staged_root,
                paths.manifest_root,
                paths.deviation_path,
            )
        )
    except ValueError:
        raise ArchiveLineageError("archive_path_layout_invalid") from None


def _require_sources_clean(paths: ArchivePaths) -> None:
    try:
        result = subprocess.run(
            (
                "git",
                "-C",
                str(paths.repo_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *_relative_input_paths(paths),
            ),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise ArchiveLineageError("archive_source_status_unavailable") from None
    if result.stdout:
        raise ArchiveLineageError("archive_source_dirty")


def _manifest_paths(paths: ArchivePaths) -> dict[str, Path]:
    return {
        name: paths.manifest_root / name
        for name in (
            "pre_ga_v1-exclusions.json",
            "pre_ga_v1-pre-squash-schema.json",
            "pre_ga_v1-sql-objects.json",
            "pre_ga_v1-clean-application-contract.json",
            "pre_ga_v1-expected.json",
        )
    }


def _require_alembic_configuration(paths: ArchivePaths) -> None:
    parser = configparser.RawConfigParser()
    try:
        with paths.alembic_ini.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        version_locations = parser.get("alembic", "version_locations")
        env_source = paths.alembic_env.read_text(encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error):
        raise ArchiveLineageError("archive_alembic_configuration_invalid") from None
    if (
        version_locations != "%(here)s/alembic/versions"
        or "archive" in version_locations
        or "archive" in env_source
    ):
        raise ArchiveLineageError("archive_alembic_configuration_invalid")


def _validate_archive_inputs(paths: ArchivePaths) -> ArchiveManifest:
    _require_sources_clean(paths)
    manifest_paths = _manifest_paths(paths)
    try:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            validate_manifest_set(
                manifest_paths["pre_ga_v1-exclusions.json"],
                manifest_paths["pre_ga_v1-pre-squash-schema.json"],
                manifest_paths["pre_ga_v1-sql-objects.json"],
            )
            load_logical_application_contract(
                manifest_paths["pre_ga_v1-clean-application-contract.json"],
                snapshot_path=manifest_paths[
                    "pre_ga_v1-pre-squash-schema.json"
                ],
                exclusion_path=manifest_paths["pre_ga_v1-exclusions.json"],
            )
            load_expected_schema_contract(
                manifest_paths["pre_ga_v1-expected.json"]
            )
    except (
        SchemaManifestError,
        LogicalApplicationContractError,
        SchemaIdentityError,
    ):
        raise ArchiveLineageError("archive_input_manifest_invalid") from None
    unexpected = tuple(
        item
        for item in emitted
        if str(item.message) != _LANGCHAIN_ALLOWED_OBJECTS_WARNING
    )
    if unexpected:
        raise ArchiveLineageError("archive_warning_unexpected")
    staged = parse_revision_file(paths.staged_root)
    if staged.revision != "pre_ga_v1_0001" or staged.parent is not None:
        raise ArchiveLineageError("archive_staged_root_invalid")
    _require_alembic_configuration(paths)
    return build_archive_manifest(paths)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_file_exact(source: Path, destination: Path) -> None:
    try:
        shutil.copyfile(source, destination)
        with destination.open("rb") as stream:
            os.fsync(stream.fileno())
    except OSError:
        raise ArchiveLineageError("archive_copy_failed") from None
    if sha256_file(source) != sha256_file(destination):
        raise ArchiveLineageError("archive_copy_digest_mismatch")


def _write_fsynced(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise ArchiveLineageError("archive_copy_failed") from None


def _prepared_archive_path(
    temporary_archive: Path,
    entry: ArchiveRevisionEntry,
) -> Path:
    return temporary_archive / Path(entry.relative_path).name


def _alembic_revision_ids(
    *,
    paths: ArchivePaths,
    live_version_dir: Path,
    safe_code: str,
) -> tuple[str, ...]:
    try:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            config = Config(str(paths.alembic_ini))
            config.set_main_option(
                "script_location",
                str(paths.backend_root / "alembic"),
            )
            config.set_main_option(
                "version_locations",
                str(live_version_dir),
            )
            script = ScriptDirectory.from_config(config)
            revision_ids = tuple(
                item.revision for item in script.walk_revisions()
            )
    except Exception:
        raise ArchiveLineageError(safe_code) from None
    if any(
        str(item.message) not in _ALEMBIC_PATH_SEPARATOR_WARNINGS
        for item in emitted
    ):
        raise ArchiveLineageError("archive_warning_unexpected")
    return revision_ids


def _verify_prepared_directories(
    *,
    paths: ArchivePaths,
    manifest: ArchiveManifest,
    temporary_archive: Path,
    temporary_live: Path,
) -> None:
    clean_roots = tuple(temporary_live.glob("*.py"))
    if len(clean_roots) != 1:
        raise ArchiveLineageError("archive_temporary_live_invalid")
    clean = parse_revision_file(clean_roots[0])
    if clean.revision != "pre_ga_v1_0001" or clean.parent is not None:
        raise ArchiveLineageError("archive_temporary_live_invalid")
    if _alembic_revision_ids(
        paths=paths,
        live_version_dir=temporary_live,
        safe_code="archive_temporary_live_invalid",
    ) != ("pre_ga_v1_0001",):
        raise ArchiveLineageError("archive_temporary_live_invalid")
    loaded = load_archive_manifest(
        temporary_archive / "manifest.v1.json",
        paths=paths,
    )
    if loaded != manifest or (
        temporary_archive / "README.md"
    ).read_text(encoding="utf-8") != README_TEXT:
        raise ArchiveLineageError("archive_temporary_manifest_invalid")
    expected_names = {
        "README.md",
        "manifest.v1.json",
        *(Path(item.relative_path).name for item in manifest.revisions),
    }
    if {item.name for item in temporary_archive.iterdir()} != expected_names:
        raise ArchiveLineageError("archive_temporary_manifest_invalid")
    for entry in manifest.revisions:
        archived = _prepared_archive_path(temporary_archive, entry)
        if sha256_file(archived) != entry.sha256:
            raise ArchiveLineageError("archive_copy_digest_mismatch")
        parsed = parse_revision_file(archived)
        if parsed.revision != entry.revision or parsed.parent != entry.parent:
            raise ArchiveLineageError("archive_revision_metadata_invalid")


def _prepare_directories(
    *,
    paths: ArchivePaths,
    manifest: ArchiveManifest,
) -> tuple[Path, Path]:
    temporary_live: Path | None = None
    temporary_archive: Path | None = None
    try:
        paths.archive_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_live = Path(
            tempfile.mkdtemp(
                prefix=".versions.archive-new-",
                dir=paths.live_version_dir.parent,
            )
        )
        temporary_archive = Path(
            tempfile.mkdtemp(
                prefix=f".{ARCHIVE_ID}.archive-new-",
                dir=paths.archive_root.parent,
            )
        )
        for entry in manifest.revisions:
            _copy_file_exact(
                paths.repo_root / entry.original_relative_path,
                _prepared_archive_path(temporary_archive, entry),
            )
        _write_fsynced(
            temporary_archive / "manifest.v1.json",
            archive_manifest_bytes(manifest),
        )
        _write_fsynced(
            temporary_archive / "README.md",
            README_TEXT.encode("utf-8"),
        )
        _copy_file_exact(
            paths.staged_root,
            temporary_live / paths.staged_root.name,
        )
        _fsync_directory(temporary_archive)
        _fsync_directory(temporary_live)
        _verify_prepared_directories(
            paths=paths,
            manifest=manifest,
            temporary_archive=temporary_archive,
            temporary_live=temporary_live,
        )
    except (ArchiveLineageError, OSError) as exc:
        cleanup_failed = False
        for candidate in (temporary_live, temporary_archive):
            if candidate is None or not candidate.exists():
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise ArchiveLineageError(
                "archive_preparation_cleanup_required"
            ) from None
        if isinstance(exc, ArchiveLineageError):
            raise
        raise ArchiveLineageError("archive_preparation_failed") from None
    assert temporary_live is not None
    assert temporary_archive is not None
    return temporary_live, temporary_archive


def _transaction_residue(paths: ArchivePaths) -> tuple[Path, ...]:
    candidates = (
        *paths.live_version_dir.parent.glob(".versions.archive-new-*"),
        *paths.live_version_dir.parent.glob(".versions.archive-rollback-*"),
        *paths.archive_root.parent.glob(
            f".{ARCHIVE_ID}.archive-new-*"
        ),
    )
    return tuple(sorted(path for path in candidates if path.exists()))


def check_archive(
    *,
    paths: ArchivePaths = DEFAULT_ARCHIVE_PATHS,
    allowed_transaction_residue: tuple[Path, ...] = (),
) -> ArchiveManifest:
    allowed_residue = set(allowed_transaction_residue)
    if any(
        candidate not in allowed_residue
        for candidate in _transaction_residue(paths)
    ):
        raise ArchiveLineageError("archive_transaction_residue")
    _require_alembic_configuration(paths)
    manifest = load_archive_manifest(
        paths.archive_root / "manifest.v1.json",
        paths=paths,
    )
    try:
        archive_items = tuple(paths.archive_root.iterdir())
        readme = (paths.archive_root / "README.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ArchiveLineageError("archive_postcondition_invalid") from None
    expected_names = {
        "README.md",
        "manifest.v1.json",
        *(Path(entry.relative_path).name for entry in manifest.revisions),
    }
    if (
        {item.name for item in archive_items} != expected_names
        or any(item.is_dir() for item in archive_items)
        or readme != README_TEXT
    ):
        raise ArchiveLineageError("archive_postcondition_invalid")
    for entry in manifest.revisions:
        archived = paths.repo_root / entry.relative_path
        if sha256_file(archived) != entry.sha256:
            raise ArchiveLineageError("archive_file_digest_mismatch")
        parsed = parse_revision_file(archived)
        if parsed.revision != entry.revision or parsed.parent != entry.parent:
            raise ArchiveLineageError("archive_revision_metadata_invalid")
    live_paths = tuple(sorted(paths.live_version_dir.glob("*.py")))
    if len(live_paths) != 1 or live_paths[0].name != paths.staged_root.name:
        raise ArchiveLineageError("archive_live_root_invalid")
    live = parse_revision_file(live_paths[0])
    if live.revision != "pre_ga_v1_0001" or live.parent is not None:
        raise ArchiveLineageError("archive_live_root_invalid")
    revision_ids = _alembic_revision_ids(
        paths=paths,
        live_version_dir=paths.live_version_dir,
        safe_code="archive_alembic_revision_map_invalid",
    )
    if revision_ids != ("pre_ga_v1_0001",) or ORIGINAL_FINAL_HEAD in revision_ids:
        raise ArchiveLineageError("archive_alembic_revision_map_invalid")
    if paths.staged_root.exists():
        raise ArchiveLineageError("archive_staged_root_not_removed")
    if scan_python_imports(paths.backend_root, "alembic.archive"):
        raise ArchiveLineageError("archive_live_import_detected")
    return manifest


def _recover_activation(
    *,
    paths: ArchivePaths,
    temporary_live: Path,
    temporary_archive: Path,
    rollback: Path,
    old_moved: bool,
    new_live_installed: bool,
    archive_installed: bool,
    staged_removed: bool,
    recovery_replace: Callable[[Path, Path], None],
    recovery_copy: Callable[[Path, Path], None],
) -> bool:
    recovery_failed = False
    if archive_installed and paths.archive_root.exists():
        try:
            recovery_replace(paths.archive_root, temporary_archive)
        except Exception:
            recovery_failed = True
    new_live_recovered = not new_live_installed
    if new_live_installed and paths.live_version_dir.exists():
        try:
            recovery_replace(paths.live_version_dir, temporary_live)
            new_live_recovered = True
        except Exception:
            recovery_failed = True
    if old_moved and rollback.exists() and new_live_recovered:
        try:
            recovery_replace(rollback, paths.live_version_dir)
        except Exception:
            recovery_failed = True
    elif old_moved and rollback.exists():
        recovery_failed = True

    if staged_removed and not paths.staged_root.exists():
        staged_source = temporary_live / paths.staged_root.name
        if not staged_source.exists():
            staged_source = paths.live_version_dir / paths.staged_root.name
        try:
            paths.staged_root.parent.mkdir(parents=True, exist_ok=True)
            recovery_copy(staged_source, paths.staged_root)
            _fsync_directory(paths.staged_root.parent)
        except Exception:
            recovery_failed = True

    cleanup_failed = False
    if not recovery_failed:
        for candidate in (temporary_live, temporary_archive):
            if not candidate.exists():
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                cleanup_failed = True
    for parent in (
        paths.live_version_dir.parent,
        paths.archive_root.parent,
        paths.staged_root.parent,
    ):
        try:
            _fsync_directory(parent)
        except OSError:
            cleanup_failed = True
    return not recovery_failed and not cleanup_failed


def activate_archive(
    *,
    paths: ArchivePaths = DEFAULT_ARCHIVE_PATHS,
    replace: Callable[[Path, Path], None] = os.replace,
    postcondition: Callable[..., ArchiveManifest] | None = None,
    recovery_replace: Callable[[Path, Path], None] = os.replace,
    recovery_copy: Callable[[Path, Path], None] = _copy_file_exact,
    remove_tree: Callable[[Path], None] = shutil.rmtree,
) -> ArchiveManifest:
    if _transaction_residue(paths):
        raise ArchiveLineageError("archive_transaction_residue")
    if paths.archive_root.exists():
        if not paths.staged_root.exists():
            return check_archive(paths=paths)
        raise ArchiveLineageError("archive_destination_collision")
    manifest = _validate_archive_inputs(paths)
    temporary_live, temporary_archive = _prepare_directories(
        paths=paths,
        manifest=manifest,
    )
    rollback = paths.live_version_dir.with_name(
        f".versions.archive-rollback-{uuid.uuid4().hex}"
    )
    old_moved = False
    new_live_installed = False
    archive_installed = False
    staged_removed = False
    try:
        replace(paths.live_version_dir, rollback)
        old_moved = True
        replace(temporary_live, paths.live_version_dir)
        new_live_installed = True
        replace(temporary_archive, paths.archive_root)
        archive_installed = True
        paths.staged_root.unlink()
        staged_removed = True
        result = (
            check_archive(
                paths=paths,
                allowed_transaction_residue=(rollback,),
            )
            if postcondition is None
            else postcondition(paths=paths)
        )
        _fsync_directory(paths.live_version_dir.parent)
        _fsync_directory(paths.archive_root.parent)
        _fsync_directory(paths.staged_root.parent)
    except Exception:
        recovered = _recover_activation(
            paths=paths,
            temporary_live=temporary_live,
            temporary_archive=temporary_archive,
            rollback=rollback,
            old_moved=old_moved,
            new_live_installed=new_live_installed,
            archive_installed=archive_installed,
            staged_removed=staged_removed,
            recovery_replace=recovery_replace,
            recovery_copy=recovery_copy,
        )
        if not recovered:
            raise ArchiveLineageError("archive_recovery_required") from None
        raise ArchiveLineageError("archive_activation_failed") from None
    try:
        remove_tree(rollback)
    except OSError:
        raise ArchiveLineageError("archive_cleanup_required") from None
    if rollback.exists():
        raise ArchiveLineageError("archive_cleanup_required")
    try:
        _fsync_directory(paths.live_version_dir.parent)
    except OSError:
        raise ArchiveLineageError("archive_cleanup_required") from None
    if _transaction_residue(paths):
        raise ArchiveLineageError("archive_transaction_residue")
    return result


def _success_line(action: str, manifest: ArchiveManifest) -> str:
    return (
        f"archive_{action}_ok revision_count={manifest.revision_count} "
        f"original_final_head={manifest.original_final_head} "
        "live_head=pre_ga_v1_0001"
    )


def main(
    argv: list[str] | None = None,
    *,
    paths: ArchivePaths = DEFAULT_ARCHIVE_PATHS,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Archive the unpublished pre-GA lineage and activate the clean root."
        )
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    action = "write" if args.write else "check"
    try:
        manifest = (
            activate_archive(paths=paths)
            if args.write
            else check_archive(paths=paths)
        )
    except ArchiveLineageError as exc:
        print(exc.safe_code, file=sys.stderr)
        return 2
    print(_success_line(action, manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
