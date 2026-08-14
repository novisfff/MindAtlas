"""Policy gates for the reproducible Python 3.11 dependency locks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pytest
from packaging.requirements import InvalidRequirement, Requirement


REQUIREMENTS = Path(__file__).parents[1] / "requirements"
LOCKS = ("api-worker.lock", "parse-worker.lock")
_HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}\b")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:https?|postgres(?:ql)?|s3)://[^\s/@]+:[^\s/@]+@"
    r"|\b(?:password|token|secret|api[_-]?key)\b\s*[:=]"
)
_FORBIDDEN_MARKER_RE = re.compile(r"(?i)(?:python_version|sys_platform|platform_machine)")


@dataclass(frozen=True)
class LockParseResult:
    require_hashes: bool
    unpinned_requirements: tuple[str, ...]
    missing_hashes: tuple[str, ...]
    index_directives: tuple[str, ...]
    credential_like_text: tuple[str, ...]
    editable_or_vcs: tuple[str, ...]
    path_dependencies: tuple[str, ...]
    duplicate_normalized_names: tuple[str, ...]
    unexpected_markers: tuple[str, ...]


def parse_requirements_lock(path: Path) -> LockParseResult:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    require_hashes = False
    unpinned: list[str] = []
    missing_hashes: list[str] = []
    index_directives: list[str] = []
    credential_like: list[str] = []
    editable_or_vcs: list[str] = []
    path_dependencies: list[str] = []
    unexpected_markers: list[str] = []
    names: list[str] = []

    current: dict[str, object] | None = None

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        requirement = current["requirement"]
        assert isinstance(requirement, Requirement)
        line_no = current["line_no"]
        assert isinstance(line_no, int)
        names.append(requirement.name.lower().replace("_", "-"))
        specifier = str(requirement.specifier)
        exact = re.fullmatch(r"==\s*([0-9][^,;\\\s]*)", specifier)
        if exact is None:
            unpinned.append(f"{line_no}:{current['raw']}")
        if not current["has_hash"]:
            missing_hashes.append(f"{line_no}:{requirement.name}")
        if current["has_forbidden_marker"]:
            unexpected_markers.append(f"{line_no}:{current['raw']}")
        current = None

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            if line.startswith(
                ("--index-url", "--extra-index-url", "--find-links", "--trusted-host")
            ):
                index_directives.append(f"{line_no}:{line}")
            if "--hash=sha256:" in line:
                require_hashes = True
                if current is not None:
                    current["has_hash"] = True
            continue
        if _CREDENTIAL_RE.search(line):
            credential_like.append(f"{line_no}:{line}")
        if line.startswith(
            ("-e ", "git+", "hg+", "svn+", "bzr+", "file:", "/", "./", "../")
        ):
            finish_current()
            editable_or_vcs.append(f"{line_no}:{line}")
            continue
        requirement_text = line[:-1].rstrip() if line.endswith("\\") else line
        inline_hash = _HASH_RE.search(requirement_text)
        requirement_text = requirement_text.split(" --hash=", 1)[0].strip()
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            # Continuation hash lines have no requirement of their own.
            if line.startswith("--hash="):
                require_hashes = True
                if current is not None:
                    current["has_hash"] = True
                continue
            path_dependencies.append(f"{line_no}:{line}")
            continue
        finish_current()
        has_hash = inline_hash is not None
        if has_hash:
            require_hashes = True
        current = {
            "requirement": requirement,
            "line_no": line_no,
            "raw": line,
            "has_hash": has_hash,
            "has_forbidden_marker": _FORBIDDEN_MARKER_RE.search(line) is not None,
        }

    finish_current()

    duplicates = sorted({name for name in names if names.count(name) > 1})
    return LockParseResult(
        require_hashes=require_hashes,
        unpinned_requirements=tuple(unpinned),
        missing_hashes=tuple(missing_hashes),
        index_directives=tuple(index_directives),
        credential_like_text=tuple(credential_like),
        editable_or_vcs=tuple(editable_or_vcs),
        path_dependencies=tuple(path_dependencies),
        duplicate_normalized_names=tuple(duplicates),
        unexpected_markers=tuple(unexpected_markers),
    )


def _locked_version(lock_name: str, project: str) -> str:
    target = project.lower().replace("_", "-")
    for raw_line in (REQUIREMENTS / lock_name).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(
                line.split(" --hash=", 1)[0].rstrip("\\").strip()
            )
        except InvalidRequirement:
            continue
        if requirement.name.lower().replace("_", "-") == target:
            match = re.search(r"==\s*([^;\s\\]+)", str(requirement.specifier))
            if match:
                return match.group(1)
    raise AssertionError(f"{project} is absent from {lock_name}")


def _locked_names(lock_name: str) -> set[str]:
    names: set[str] = set()
    current_name: str | None = None
    for raw_line in (REQUIREMENTS / lock_name).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        requirement_text = line[:-1].rstrip() if line.endswith("\\") else line
        requirement_text = requirement_text.split(" --hash=", 1)[0].strip()
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            continue
        current_name = requirement.name.lower().replace("_", "-")
        names.add(current_name)
    return names


@pytest.mark.parametrize("name", LOCKS)
def test_lock_is_hashed_pinned_and_contains_no_index_or_credentials(name: str) -> None:
    parsed = parse_requirements_lock(REQUIREMENTS / name)
    assert parsed.require_hashes
    assert parsed.unpinned_requirements == ()
    assert parsed.missing_hashes == ()
    assert parsed.index_directives == ()
    assert parsed.credential_like_text == ()
    assert parsed.editable_or_vcs == ()
    assert parsed.path_dependencies == ()
    assert parsed.duplicate_normalized_names == ()
    assert parsed.unexpected_markers == ()


def test_langgraph_line_is_frozen() -> None:
    assert _locked_version("api-worker.lock", "langgraph") == "0.3.34"


def test_python_311_constraints_and_direct_split_are_declared() -> None:
    constraints = (REQUIREMENTS / "constraints-python311.txt").read_text(encoding="utf-8")
    api_input = (REQUIREMENTS / "api-worker.in").read_text(encoding="utf-8")
    parse_input = (REQUIREMENTS / "parse-worker.in").read_text(encoding="utf-8")
    assert "python_version == '3.11'" in constraints or 'python_version == "3.11"' in constraints
    assert "langgraph==0.3.34" in api_input
    assert "docling" not in api_input.lower()
    assert "lightrag-hku" not in parse_input.lower()
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in parse_input


@pytest.mark.parametrize("name", LOCKS)
def test_lock_header_is_stable_and_binds_the_compiler(name: str) -> None:
    lines = (REQUIREMENTS / name).read_text(encoding="utf-8").splitlines()
    assert lines[:7] == [
        "# This file is generated by the MindAtlas reproducible lock compiler.",
        f"# input: requirements/{name.replace('.lock', '.in')}",
        "# constraints: requirements/constraints-python311.txt",
        "# compiler: pip-tools==7.4.1",
        "# python: 3.11",
        "# compiler-image: python:3.11-slim-bookworm@sha256:77923445c077d8eb971b14b2b114a1d9cd4a87edb4c75654820ca4832ee8cb15",
        "# install with: python -m pip install --require-hashes -r this-file",
    ]
    assert lines[7] == ""
    assert all("/tmp/" not in line for line in lines[:8])
    assert all("--index-url" not in line for line in lines[:8])


def test_runtime_lock_split_has_no_cross_worker_packages() -> None:
    assert "docling" not in _locked_names("api-worker.lock")
    assert "lightrag-hku" not in _locked_names("parse-worker.lock")
    assert "langgraph" not in _locked_names("parse-worker.lock")
    assert "torch" in _locked_names("parse-worker.lock")
    assert "torchvision" in _locked_names("parse-worker.lock")


def test_parse_lock_has_cpu_torch_and_no_cuda_distribution_family() -> None:
    names = _locked_names("parse-worker.lock")
    assert not any(name.startswith("nvidia-") for name in names)
    assert _locked_version("parse-worker.lock", "torch") == "2.7.0+cpu"
    assert _locked_version("parse-worker.lock", "torchvision") == "0.22.0+cpu"


def test_install_sources_are_hash_locked_and_deleted_docling_input_is_unreferenced() -> None:
    root = Path(__file__).parents[2]
    source_paths = [
        root / "backend" / "Dockerfile",
        root / ".github" / "workflows" / "ci.yml",
        *sorted((root / "deploy").rglob("*")),
        *sorted((root / "backend" / "scripts").rglob("*.py")),
    ]
    for path in source_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "requirements-docling.txt" not in text, path
        for line in text.splitlines():
            if "pip install" in line and not line.lstrip().startswith(("#", "//")):
                assert "--require-hashes" in text, path


def test_compiler_script_owns_platform_and_clean_install_smokes() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "compile_requirements.py").read_text(
        encoding="utf-8"
    )
    assert '"--platform",' in source
    assert "--require-hashes" in source
    assert "api-worker-import-smoke: ok" in source
    assert "parse-worker-import-smoke: ok" in source
    assert "PYTORCH_CPU_INDEX" in source
    assert "platform.machine()" in source
    assert "--write and --check are mutually exclusive" in source


def test_supported_compiler_tuple_and_immutable_image_are_explicit() -> None:
    from scripts.compile_requirements import (
        COMPILER_PIP_TOOLS_VERSION,
        RELEASE_PLATFORM,
        SUPPORTED_PYTHON,
        load_compiler_image_reference,
    )

    assert SUPPORTED_PYTHON == "3.11"
    assert RELEASE_PLATFORM == "linux/amd64"
    assert COMPILER_PIP_TOOLS_VERSION == "7.4.1"
    reference = load_compiler_image_reference(
        REQUIREMENTS / "compiler-image.txt"
    )
    assert reference == (
        "python:3.11-slim-bookworm@"
        "sha256:77923445c077d8eb971b14b2b114a1d9cd4a87edb4c75654820ca4832ee8cb15"
    )


def test_compiler_bootstrap_lock_is_fully_hashed() -> None:
    parsed = parse_requirements_lock(REQUIREMENTS / "compiler-bootstrap.lock")
    assert parsed.require_hashes
    assert parsed.unpinned_requirements == ()
    assert parsed.missing_hashes == ()
    assert parsed.index_directives == ()
    assert parsed.credential_like_text == ()
    assert parsed.editable_or_vcs == ()
    assert parsed.path_dependencies == ()
    assert parsed.duplicate_normalized_names == ()
    assert parsed.unexpected_markers == ()


def test_compiler_bootstrap_header_has_no_host_or_temp_path() -> None:
    lines = (REQUIREMENTS / "compiler-bootstrap.lock").read_text(encoding="utf-8").splitlines()
    assert lines[:7] == [
        "# This file is generated by the MindAtlas reproducible lock compiler.",
        "# input: requirements/compiler-bootstrap.in",
        "# constraints: none (bootstrap is compiler-owned)",
        "# compiler: pip-tools==7.4.1",
        "# python: 3.11",
        "# compiler-image: python:3.11-slim-bookworm@sha256:77923445c077d8eb971b14b2b114a1d9cd4a87edb4c75654820ca4832ee8cb15",
        "# install with: python -m pip install --require-hashes -r this-file",
    ]
    assert lines[7] == ""
    assert all("/tmp/" not in line and "--index-url" not in line for line in lines[:8])


def test_legacy_requirement_shims_are_lock_only() -> None:
    assert (Path(__file__).parents[1] / "requirements.txt").read_text(
        encoding="utf-8"
    ).strip() == "-r requirements/api-worker.lock"
    assert (Path(__file__).parents[1] / "requirements-parse-worker.txt").read_text(
        encoding="utf-8"
    ).strip() == "-r requirements/parse-worker.lock"


def test_generated_lock_digests_match_committed_bytes() -> None:
    from app.release.generated_lock_digests import verify_generated_lock_digests

    verify_generated_lock_digests(REQUIREMENTS)
