from __future__ import annotations

import configparser
import hashlib
from itertools import pairwise
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable
import warnings

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

from app.schema.canonical import canonical_json_bytes, sha256_canonical_json
from app.schema.sql_objects import load_pre_squash_snapshot
from scripts import archive_pre_ga_lineage as archiver
from scripts.archive_pre_ga_lineage import (
    ARCHIVAL_REASON,
    ARCHIVE_ID,
    ARCHIVE_ROOT,
    DEVIATION_PATH,
    README_TEXT,
    REPO_ROOT,
    ArchivePaths,
    ArchiveLineageError,
    activate_archive,
    archive_manifest_bytes,
    build_archive_manifest,
    check_archive,
    load_archive_manifest,
    main,
    parse_revision_file,
    parse_revision_files,
    scan_python_imports,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
LIVE_VERSION_DIR = BACKEND_ROOT / "alembic" / "versions"
LIVE_CLEAN_ROOT = LIVE_VERSION_DIR / "pre_ga_v1_0001_clean_baseline.py"
STAGED_ROOT = (
    BACKEND_ROOT
    / "alembic"
    / "baseline_staging"
    / "pre_ga_v1_0001_clean_baseline.py"
)
ARCHIVE_DIR = BACKEND_ROOT / "alembic" / "archive" / "pre_ga_v1_superseded"
MANIFEST_ROOT = BACKEND_ROOT / "app" / "schema" / "manifests"


def _run_git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ("git", "-C", str(repo_root), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def _archive_paths(tmp_path: Path) -> ArchivePaths:
    repo_root = tmp_path / "repo"
    backend_root = repo_root / "backend"
    live_version_dir = backend_root / "alembic" / "versions"
    staged_root = (
        backend_root
        / "alembic"
        / "baseline_staging"
        / STAGED_ROOT.name
    )
    archive_root = (
        backend_root / "alembic" / "archive" / "pre_ga_v1_superseded"
    )
    deviation_path = repo_root / DEVIATION_PATH.relative_to(REPO_ROOT)
    manifest_root = backend_root / "app" / "schema" / "manifests"
    alembic_ini = backend_root / "alembic.ini"
    alembic_env = backend_root / "alembic" / "env.py"

    live_version_dir.mkdir(parents=True)
    for source in ARCHIVE_DIR.glob("*.py.archived"):
        original_name = source.name.removesuffix(".archived")
        shutil.copyfile(source, live_version_dir / original_name)
    staged_root.parent.mkdir(parents=True)
    shutil.copyfile(LIVE_CLEAN_ROOT, staged_root)
    deviation_path.parent.mkdir(parents=True)
    shutil.copyfile(DEVIATION_PATH, deviation_path)
    shutil.copytree(MANIFEST_ROOT, manifest_root)
    shutil.copyfile(BACKEND_ROOT / "alembic.ini", alembic_ini)
    alembic_env.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BACKEND_ROOT / "alembic" / "env.py", alembic_env)

    _run_git(repo_root, "init", "-q")
    _run_git(repo_root, "config", "user.name", "Schema Archive Test")
    _run_git(repo_root, "config", "user.email", "schema-archive@example.invalid")
    _run_git(repo_root, "add", ".")
    _run_git(repo_root, "commit", "-qm", "fixture")
    return ArchivePaths(
        repo_root=repo_root,
        backend_root=backend_root,
        live_version_dir=live_version_dir,
        staged_root=staged_root,
        archive_root=archive_root,
        deviation_path=deviation_path,
        manifest_root=manifest_root,
        alembic_ini=alembic_ini,
        alembic_env=alembic_env,
    )


def _live_bytes(paths: ArchivePaths) -> dict[str, bytes]:
    return _revision_directory_bytes(paths.live_version_dir)


def _revision_directory_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(directory.glob("*.py"))
    }


def _rollback_directories(paths: ArchivePaths) -> tuple[Path, ...]:
    return tuple(
        sorted(
            paths.live_version_dir.parent.glob(
                ".versions.archive-rollback-*"
            )
        )
    )


def _assert_no_transaction_residue(paths: ArchivePaths) -> None:
    residue = tuple(
        path
        for path in paths.live_version_dir.parent.rglob("*")
        if path.name.startswith(
            (
                ".versions.archive-new-",
                ".versions.archive-rollback-",
                ".pre_ga_v1_superseded.archive-new-",
            )
        )
    )
    assert residue == ()


def _failing_replace(failure_number: int) -> Callable[[Path, Path], None]:
    calls = 0

    def replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_number:
            raise OSError("injected rename failure")
        os.replace(source, destination)

    return replace


def test_old_lineage_is_one_exact_60_revision_chain() -> None:
    graph = parse_revision_files(ARCHIVE_DIR.glob("*.py.archived"))
    ordered = graph.require_linear_chain(final_head="b6e2d4f8a901")

    assert len(ordered) == 60
    assert ordered[0].revision == "a7d8f1424a99"
    assert ordered[0].parent is None
    assert ordered[-1].revision == "b6e2d4f8a901"
    for parent, child in pairwise(ordered):
        assert child.parent == parent.revision


def test_staged_root_is_not_connected_to_old_chain() -> None:
    revision = parse_revision_file(LIVE_CLEAN_ROOT)

    assert revision.revision == "pre_ga_v1_0001"
    assert revision.parent is None
    assert not STAGED_ROOT.exists()


def test_archive_manifest_locks_exact_order_paths_and_digests(
    tmp_path: Path,
) -> None:
    paths = _archive_paths(tmp_path)
    manifest = build_archive_manifest(paths)
    payload = manifest.to_payload()
    digest_payload = {
        key: value for key, value in payload.items() if key != "manifestDigest"
    }

    assert set(payload) == {
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
    assert payload["schemaVersion"] == 1
    assert payload["archiveId"] == ARCHIVE_ID == "pre_ga_v1_superseded"
    assert payload["revisionCount"] == 60
    assert payload["firstRevision"] == "a7d8f1424a99"
    assert payload["originalFinalHead"] == "b6e2d4f8a901"
    assert payload["archivalReason"] == ARCHIVAL_REASON
    assert payload["designDeviationEvidenceDigest"] == hashlib.sha256(
        paths.deviation_path.read_bytes()
    ).hexdigest()
    assert (
        payload["preSquashSnapshotDigest"]
        == load_pre_squash_snapshot().manifest_digest
    )
    assert payload["manifestDigest"] == sha256_canonical_json(digest_payload)

    graph = parse_revision_files(paths.live_version_dir.glob("*.py"))
    ordered = graph.require_linear_chain(final_head="b6e2d4f8a901")
    revisions = payload["revisions"]
    assert isinstance(revisions, list)
    assert len(revisions) == 60
    for index, (entry, source) in enumerate(zip(revisions, ordered), start=1):
        assert isinstance(entry, dict)
        assert set(entry) == {
            "order",
            "relativePath",
            "originalRelativePath",
            "revision",
            "parent",
            "sha256",
        }
        assert entry["order"] == index
        assert entry["revision"] == source.revision
        assert entry["parent"] == source.parent
        assert entry["originalRelativePath"] == source.path.relative_to(
            paths.repo_root
        ).as_posix()
        assert entry["relativePath"] == (
            paths.archive_root / f"{source.path.name}.archived"
        ).relative_to(paths.repo_root).as_posix()
        assert entry["relativePath"].endswith(".py.archived")
        assert not Path(entry["relativePath"]).is_absolute()
        assert not Path(entry["originalRelativePath"]).is_absolute()
        assert entry["sha256"] == hashlib.sha256(source.path.read_bytes()).hexdigest()

    assert archive_manifest_bytes(manifest) == canonical_json_bytes(payload) + b"\n"


def test_archive_manifest_loader_requires_exact_canonical_contract(
    tmp_path: Path,
) -> None:
    paths = _archive_paths(tmp_path)
    manifest = build_archive_manifest(paths)
    path = tmp_path / "manifest.v1.json"
    path.write_bytes(archive_manifest_bytes(manifest))

    assert load_archive_manifest(path, paths=paths) == manifest

    payload = manifest.to_payload()
    payload["unexpected"] = True
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ArchiveLineageError,
        match="^archive_manifest_invalid$",
    ):
        load_archive_manifest(path, paths=paths)


def test_archive_activation_refuses_dirty_source_revision(tmp_path: Path) -> None:
    paths = _archive_paths(tmp_path)
    source = next(paths.live_version_dir.glob("*.py"))
    source.write_bytes(source.read_bytes() + b"\n# dirty\n")

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_source_dirty$",
    ):
        activate_archive(paths=paths)

    assert len(tuple(paths.live_version_dir.glob("*.py"))) == 60
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


def test_archive_activation_is_byte_exact_and_installs_only_clean_root(
    tmp_path: Path,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    staged_bytes = paths.staged_root.read_bytes()

    activate_archive(paths=paths)
    manifest = check_archive(paths=paths)

    live = tuple(paths.live_version_dir.glob("*.py"))
    assert tuple(path.name for path in live) == (STAGED_ROOT.name,)
    assert live[0].read_bytes() == staged_bytes
    assert not paths.staged_root.exists()
    assert manifest.revision_count == 60
    assert len(tuple(paths.archive_root.glob("*.py.archived"))) == 60
    for entry in manifest.revisions:
        archived = paths.repo_root / entry.relative_path
        source_name = Path(entry.original_relative_path).name
        assert archived.read_bytes() == original[source_name]
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == entry.sha256
    assert (paths.archive_root / "README.md").read_text("utf-8") == README_TEXT
    assert "preserve unpublished development history only" in README_TEXT
    assert "`.py.archived` suffix is intentional" in README_TEXT
    assert "intentionally has no `__init__.py`" in README_TEXT
    assert "excluded from Alembic `version_locations`" in README_TEXT
    assert "not a supported upgrade or restore source" in README_TEXT
    assert "without importing or executing files" in README_TEXT
    assert not (paths.archive_root / "__init__.py").exists()
    assert not tuple(paths.archive_root.rglob("*.py"))
    assert not tuple(paths.archive_root.rglob("*.pyc"))
    assert not tuple(paths.archive_root.rglob("__pycache__"))
    _assert_no_transaction_residue(paths)

    config = Config(str(paths.alembic_ini))
    config.set_main_option(
        "script_location",
        str(paths.backend_root / "alembic"),
    )
    config.set_main_option(
        "version_locations",
        str(paths.live_version_dir),
    )
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        revisions = tuple(
            item.revision
            for item in ScriptDirectory.from_config(config).walk_revisions()
        )
    assert {str(item.message) for item in emitted} == {
        "No path_separator found in configuration; falling back to legacy "
        "splitting on spaces, commas, and colons for prepend_sys_path.  "
        "Consider adding path_separator=os to Alembic config.",
        "The version_path_separator configuration parameter is deprecated; "
        "please use path_separator",
    }
    assert revisions == ("pre_ga_v1_0001",)
    assert "b6e2d4f8a901" not in revisions


@pytest.mark.parametrize("failure_number", [1, 2, 3])
def test_archive_activation_rolls_back_every_rename_failure(
    tmp_path: Path,
    failure_number: int,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_activation_failed$",
    ):
        activate_archive(
            paths=paths,
            replace=_failing_replace(failure_number),
        )

    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


def test_archive_activation_rolls_back_postcondition_failure(
    tmp_path: Path,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)

    def fail_postcondition(*, paths: ArchivePaths):
        raise ArchiveLineageError("injected_postcondition_failure")

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_activation_failed$",
    ):
        activate_archive(paths=paths, postcondition=fail_postcondition)

    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


def test_temporary_alembic_validation_precedes_first_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    rename_calls: list[tuple[Path, Path]] = []

    def record_replace(source: Path, destination: Path) -> None:
        rename_calls.append((source, destination))
        os.replace(source, destination)

    def reject_revision_map(cls, config):  # noqa: ANN001
        raise RuntimeError("injected temporary Alembic failure")

    monkeypatch.setattr(
        ScriptDirectory,
        "from_config",
        classmethod(reject_revision_map),
    )

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_temporary_live_invalid$",
    ):
        activate_archive(paths=paths, replace=record_replace)

    assert rename_calls == []
    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


@pytest.mark.parametrize("failure_number", [1, 2])
def test_temporary_directory_creation_failure_is_bounded_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_number: int,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    original_mkdtemp = archiver.tempfile.mkdtemp
    calls = 0

    def fail_mkdtemp(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls == failure_number:
            raise OSError("injected temporary directory failure")
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(archiver.tempfile, "mkdtemp", fail_mkdtemp)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_preparation_failed$",
    ):
        activate_archive(paths=paths)

    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


def test_preparation_fsync_failure_is_bounded_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)

    def fail_fsync(path: Path) -> None:
        raise OSError("injected preparation fsync failure")

    monkeypatch.setattr(archiver, "_fsync_directory", fail_fsync)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_preparation_failed$",
    ):
        activate_archive(paths=paths)

    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


def test_final_durability_failure_rolls_back_before_deleting_old_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    original_fsync = archiver._fsync_directory
    calls = 0

    def fail_third_fsync(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected final durability failure")
        original_fsync(path)

    monkeypatch.setattr(archiver, "_fsync_directory", fail_third_fsync)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_activation_failed$",
    ):
        activate_archive(paths=paths)

    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


@pytest.mark.parametrize("recovery_failure", [1, 2, 3, 4])
def test_recovery_failure_preserves_original_lineage_and_is_bounded(
    tmp_path: Path,
    recovery_failure: int,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    replace_calls = 0

    def fail_postcondition(*, paths: ArchivePaths):  # noqa: ANN202
        raise ArchiveLineageError("injected_postcondition_failure")

    def recovery_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == recovery_failure:
            raise OSError("injected recovery rename failure")
        os.replace(source, destination)

    def recovery_copy(source: Path, destination: Path) -> None:
        if recovery_failure == 4:
            raise OSError("injected staged-root recovery failure")
        shutil.copyfile(source, destination)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_recovery_required$",
    ):
        activate_archive(
            paths=paths,
            postcondition=fail_postcondition,
            recovery_replace=recovery_replace,
            recovery_copy=recovery_copy,
        )

    candidates = (
        (paths.live_version_dir,) if paths.live_version_dir.exists() else ()
    ) + _rollback_directories(paths)
    assert any(
        _revision_directory_bytes(directory) == original
        for directory in candidates
    )


def test_rollback_cleanup_failure_is_bounded_and_detectable(
    tmp_path: Path,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)

    def fail_remove_tree(path: Path) -> None:
        raise OSError("injected rollback cleanup failure")

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_cleanup_required$",
    ):
        activate_archive(paths=paths, remove_tree=fail_remove_tree)

    assert len(tuple(paths.live_version_dir.glob("*.py"))) == 1
    rollback = _rollback_directories(paths)
    assert len(rollback) == 1
    assert _revision_directory_bytes(rollback[0]) == original
    with pytest.raises(
        ArchiveLineageError,
        match="^archive_transaction_residue$",
    ):
        check_archive(paths=paths)


@pytest.mark.parametrize(
    "relative_residue",
    [
        "alembic/.versions.archive-new-interrupted",
        "alembic/.versions.archive-rollback-interrupted",
        "alembic/archive/.pre_ga_v1_superseded.archive-new-interrupted",
    ],
)
def test_archive_activation_refuses_existing_residue_before_first_rename(
    tmp_path: Path,
    relative_residue: str,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    residue = paths.backend_root / relative_residue
    residue.mkdir(parents=True)
    (residue / "recovery-marker").write_text("preserve\n", encoding="utf-8")
    rename_calls: list[tuple[Path, Path]] = []

    def record_replace(source: Path, destination: Path) -> None:
        rename_calls.append((source, destination))
        os.replace(source, destination)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_transaction_residue$",
    ):
        activate_archive(paths=paths, replace=record_replace)

    assert rename_calls == []
    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    assert (residue / "recovery-marker").read_text("utf-8") == "preserve\n"


def test_archive_postcondition_allows_only_current_rollback_residue(
    tmp_path: Path,
) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    stale = paths.live_version_dir.parent / ".versions.archive-new-concurrent"
    rename_calls = 0

    def introduce_residue_after_forward_renames(
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal rename_calls
        rename_calls += 1
        os.replace(source, destination)
        if rename_calls == 3:
            stale.mkdir()

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_activation_failed$",
    ):
        activate_archive(paths=paths, replace=introduce_residue_after_forward_renames)

    assert _live_bytes(paths) == original
    assert not paths.archive_root.exists()
    assert paths.staged_root.is_file()
    assert stale.is_dir()


def test_partial_recovery_failure_still_fsyncs_recovered_parent_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _archive_paths(tmp_path)
    recovery_replace_calls = 0
    recovery_fsyncs: list[Path] = []

    def fail_postcondition(*, paths: ArchivePaths):  # noqa: ANN202
        recovery_fsyncs.clear()
        raise ArchiveLineageError("injected_postcondition_failure")

    def fail_second_recovery_replace(source: Path, destination: Path) -> None:
        nonlocal recovery_replace_calls
        recovery_replace_calls += 1
        if recovery_replace_calls == 2:
            raise OSError("injected recovery rename failure")
        os.replace(source, destination)

    def record_fsync(path: Path) -> None:
        recovery_fsyncs.append(path)

    monkeypatch.setattr(archiver, "_fsync_directory", record_fsync)

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_recovery_required$",
    ):
        activate_archive(
            paths=paths,
            postcondition=fail_postcondition,
            recovery_replace=fail_second_recovery_replace,
        )

    assert paths.live_version_dir.parent in recovery_fsyncs
    assert paths.archive_root.parent in recovery_fsyncs
    assert paths.staged_root.parent in recovery_fsyncs


def test_successful_rollback_cleanup_is_followed_by_parent_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _archive_paths(tmp_path)
    original_fsync = archiver._fsync_directory
    events: list[tuple[str, Path]] = []

    def record_fsync(path: Path) -> None:
        events.append(("fsync", path))
        original_fsync(path)

    def record_remove_tree(path: Path) -> None:
        events.append(("remove", path))
        shutil.rmtree(path)

    monkeypatch.setattr(archiver, "_fsync_directory", record_fsync)

    activate_archive(paths=paths, remove_tree=record_remove_tree)

    cleanup_index = next(
        index for index, event in enumerate(events) if event[0] == "remove"
    )
    assert ("fsync", paths.live_version_dir.parent) in events[
        cleanup_index + 1 :
    ]
    _assert_no_transaction_residue(paths)


def test_archive_activation_refuses_destination_collision(tmp_path: Path) -> None:
    paths = _archive_paths(tmp_path)
    original = _live_bytes(paths)
    paths.archive_root.mkdir(parents=True)
    (paths.archive_root / "README.md").write_text("different\n", encoding="utf-8")

    with pytest.raises(
        ArchiveLineageError,
        match="^archive_destination_collision$",
    ):
        activate_archive(paths=paths)

    assert _live_bytes(paths) == original
    assert (paths.archive_root / "README.md").read_text("utf-8") == "different\n"
    assert paths.staged_root.is_file()
    _assert_no_transaction_residue(paths)


def test_alembic_configuration_reaches_only_live_versions() -> None:
    config = configparser.RawConfigParser()
    config.read(BACKEND_ROOT / "alembic.ini", encoding="utf-8")

    assert config.get("alembic", "version_locations") == (
        "%(here)s/alembic/versions"
    )
    assert "archive" not in config.get("alembic", "version_locations")
    assert "archive" not in (BACKEND_ROOT / "alembic" / "env.py").read_text(
        "utf-8"
    )
    assert scan_python_imports(BACKEND_ROOT, "alembic.archive") == ()


def test_ordinary_alembic_cannot_resolve_archived_old_head() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        script = ScriptDirectory.from_config(
            Config(str(BACKEND_ROOT / "alembic.ini"))
        )

    assert script.get_revision("pre_ga_v1_0001") is not None
    with pytest.raises(CommandError, match="b6e2d4f8a901"):
        script.get_revision("b6e2d4f8a901")


def test_python_import_scan_prunes_dependency_and_cache_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "invalid.py").write_text("not valid !!!", "utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "invalid.py").write_text(
        "also not valid !!!",
        "utf-8",
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "safe.py").write_text("import pathlib\n", "utf-8")

    assert scan_python_imports(tmp_path, "alembic.archive") == ()


def test_post_archive_test_support_never_executes_archived_revisions() -> None:
    support_source = (
        BACKEND_ROOT / "tests" / "schema_baseline_support.py"
    ).read_text(encoding="utf-8")
    equivalence_source = (
        BACKEND_ROOT / "tests" / "test_schema_equivalence_postgres.py"
    ).read_text(encoding="utf-8")

    assert ".py.archived" not in support_source
    assert "upgrade_archived_old_chain" not in support_source
    assert "upgrade_archived_old_chain" not in equivalence_source


def test_archive_cli_write_then_check_is_bounded_and_quiet(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _archive_paths(tmp_path)

    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert main(["--write"], paths=paths) == 0
    written = capsys.readouterr()
    assert written.out == (
        "archive_write_ok revision_count=60 "
        "original_final_head=b6e2d4f8a901 live_head=pre_ga_v1_0001\n"
    )
    assert written.err == ""
    assert [str(item.message) for item in emitted] == []

    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert main(["--check"], paths=paths) == 0
    checked = capsys.readouterr()
    assert checked.out == (
        "archive_check_ok revision_count=60 "
        "original_final_head=b6e2d4f8a901 live_head=pre_ga_v1_0001\n"
    )
    assert checked.err == ""
    assert [str(item.message) for item in emitted] == []


def test_archive_cli_reports_only_bounded_safe_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _archive_paths(tmp_path)
    paths.archive_root.mkdir(parents=True)
    (paths.archive_root / "unexpected").write_text("collision", encoding="utf-8")

    assert main(["--write"], paths=paths) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "archive_destination_collision\n"
