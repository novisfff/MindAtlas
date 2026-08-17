"""Compile and verify the release Python locks in an immutable amd64 container.

The host process deliberately does not run a resolver.  It validates the
compiler contract, asks Docker to run the pinned image, and only then compares
or replaces lock bytes.  This prevents a developer's local architecture or
configured package index from silently becoming release input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Final, Iterable


BACKEND_ROOT: Final = Path(__file__).resolve().parents[1]
REQUIREMENTS_ROOT: Final = BACKEND_ROOT / "requirements"
SUPPORTED_PYTHON: Final = "3.11"
RELEASE_PLATFORM: Final = "linux/amd64"
PYTORCH_CPU_INDEX: Final = "https://download.pytorch.org/whl/cpu"
COMPILER_PIP_TOOLS_VERSION: Final = "7.4.1"
COMPILER_IMAGE_FILE: Final = REQUIREMENTS_ROOT / "compiler-image.txt"
BOOTSTRAP_LOCK: Final = REQUIREMENTS_ROOT / "compiler-bootstrap.lock"
TARGETS: Final = {
    "api-worker": (REQUIREMENTS_ROOT / "api-worker.in", REQUIREMENTS_ROOT / "api-worker.lock"),
    "parse-worker": (REQUIREMENTS_ROOT / "parse-worker.in", REQUIREMENTS_ROOT / "parse-worker.lock"),
}
COMMON_ARGS: Final = (
    "--quiet",
    "--resolver=backtracking",
    "--generate-hashes",
    "--allow-unsafe",
    "--strip-extras",
    "--no-emit-index-url",
    "--no-emit-trusted-host",
    "--no-emit-options",
    "--newline=lf",
    "--no-config",
)
_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}\b")
_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^;\\\s]+)(?:\\)?$")
_FORBIDDEN_MARKER_RE = re.compile(r"(?i)(?:python_version|sys_platform|platform_machine)")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:https?|postgres(?:ql)?|s3)://[^\s/@]+:[^\s/@]+@"
    r"|\b(?:password|token|secret|api[_-]?key)\b\s*[:=]"
)


class LockCompileError(RuntimeError):
    """Safe, bounded compiler failure."""


def load_compiler_image_reference(path: Path = COMPILER_IMAGE_FILE) -> str:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    values = [line for line in lines if line and not line.startswith("#")]
    if len(values) != 1 or _IMAGE_RE.fullmatch(values[0]) is None:
        raise LockCompileError("compiler image must be one immutable sha256 reference")
    return values[0]


def assert_compiler_environment() -> None:
    if sys.version_info[:2] != (3, 11):
        raise LockCompileError("compiler requires Python 3.11")
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise LockCompileError("compiler requires linux/amd64")
    try:
        from importlib.metadata import version

        observed = version("pip-tools")
    except Exception as exc:  # pragma: no cover - defensive environment guard
        raise LockCompileError("pip-tools is not installed in compiler environment") from exc
    if observed != COMPILER_PIP_TOOLS_VERSION:
        raise LockCompileError("pip-tools compiler version mismatch")


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise LockCompileError(f"required executable unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise LockCompileError(f"compiler command failed with exit {exc.returncode}") from exc


def _docker_available() -> None:
    if shutil.which("docker") is None:
        raise LockCompileError("Docker is required for pinned lock compilation")
    _run(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"])


def _container_compile(image: str, input_path: Path, output_path: Path) -> None:
    relative_input = input_path.relative_to(BACKEND_ROOT)
    relative_output = output_path.relative_to(BACKEND_ROOT)
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        RELEASE_PLATFORM,
        "--network",
        "default",
        "-e",
        "LC_ALL=C.UTF-8",
        "-e",
        "TZ=UTC",
        "-e",
        "PIP_CONFIG_FILE=/dev/null",
        "-e",
        "PIP_DISABLE_PIP_VERSION_CHECK=1",
        "-e",
        "PIP_NO_INPUT=1",
        "-v",
        f"{BACKEND_ROOT}:/workspace:rw",
        "-w",
        "/workspace",
        image,
        "sh",
        "-eu",
        "-c",
        (
            "python -m venv /tmp/compiler-venv && "
            + "/tmp/compiler-venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir "
            + "--require-hashes -r requirements/compiler-bootstrap.lock && "
            + shlex.join(
                [
                    "/tmp/compiler-venv/bin/python",
                    "-c",
                    "import platform, sys; "
                    "from importlib.metadata import version; "
                    "assert sys.version_info[:2] == (3, 11); "
                    "assert sys.platform == 'linux'; "
                    "assert platform.machine() in {'x86_64', 'amd64'}; "
                    "assert version('pip-tools') == '7.4.1'",
                ]
            )
            + " && /tmp/compiler-venv/bin/python -m piptools compile "
            + " ".join(COMMON_ARGS)
            + f" --output-file={relative_output} {relative_input}"
        ),
    ]
    _run(command, cwd=BACKEND_ROOT)


def _validate_generated_bytes(path: Path) -> None:
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LockCompileError(f"generated lock is not valid UTF-8: {path.name}") from exc
    if b"\r" in data or not data.endswith(b"\n"):
        raise LockCompileError(f"generated lock must use LF and end with a newline: {path.name}")
    if _HASH_RE.search(text) is None:
        raise LockCompileError(f"generated lock has no sha256 hashes: {path.name}")
    if any(
        line.strip().startswith(("--index-url", "--extra-index-url", "--find-links", "--trusted-host"))
        for line in text.splitlines()
    ):
        raise LockCompileError(f"generated lock contains an index directive: {path.name}")
    if _CREDENTIAL_RE.search(text):
        raise LockCompileError(f"generated lock contains credential-like text: {path.name}")
    names: set[str] = set()
    current_name: str | None = None
    current_has_hash = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            if _HASH_RE.search(line):
                current_has_hash = True
            continue
        if line.startswith(("-e ", "git+", "hg+", "svn+", "bzr+", "file:", "/", "./", "../")):
            raise LockCompileError(f"generated lock contains an editable/VCS/path dependency: {path.name}")
        requirement_text = line[:-1].rstrip() if line.endswith("\\") else line
        requirement_text = requirement_text.split(" --hash=", 1)[0].strip()
        match = _PIN_RE.fullmatch(requirement_text)
        if match is None:
            if line.startswith("--hash="):
                current_has_hash = True
                continue
            raise LockCompileError(f"generated lock contains an unparseable requirement: {path.name}")
        if current_name is not None and not current_has_hash:
            raise LockCompileError(f"generated lock requirement is missing a hash: {path.name}")
        current_name = match.group(1).lower().replace("_", "-")
        if current_name in names:
            raise LockCompileError(f"generated lock contains duplicate project names: {path.name}")
        names.add(current_name)
        current_has_hash = _HASH_RE.search(raw_line) is not None
        if _FORBIDDEN_MARKER_RE.search(raw_line):
            raise LockCompileError(f"generated lock contains an unexpected marker: {path.name}")
    if current_name is not None and not current_has_hash:
        raise LockCompileError(f"generated lock requirement is missing a hash: {path.name}")


def _normalize_header(path: Path, input_path: Path, image: str) -> None:
    """Replace pip-tools' host/temp-path header with stable release metadata."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    first_requirement = next(
        (index for index, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")),
        None,
    )
    if first_requirement is None:
        raise LockCompileError(f"compiler produced an empty lock: {path.name}")
    relative_input = input_path.relative_to(BACKEND_ROOT).as_posix()
    stable_header = (
        "# This file is generated by the MindAtlas reproducible lock compiler.\n"
        f"# input: {relative_input}\n"
        "# constraints: requirements/constraints-python311.txt\n"
        f"# compiler: pip-tools=={COMPILER_PIP_TOOLS_VERSION}\n"
        f"# python: {SUPPORTED_PYTHON}\n"
        f"# compiler-image: {image}\n"
        "# install with: python -m pip install --require-hashes -r this-file\n"
        "\n"
    )
    path.write_text(stable_header + "".join(lines[first_requirement:]), encoding="utf-8", newline="\n")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _lock_set_digest(api_digest: str, parse_digest: str) -> str:
    payload = {
        "domain": "mindatlas:python-lock-set:v1",
        "python": SUPPORTED_PYTHON,
        "platform": RELEASE_PLATFORM,
        "locks": [["api-worker.lock", api_digest], ["parse-worker.lock", parse_digest]],
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def write_generated_lock_digests() -> None:
    """Rewrite the literal digest module from the committed lock bytes."""

    api = _sha256_bytes(TARGETS["api-worker"][1].read_bytes())
    parse = _sha256_bytes(TARGETS["parse-worker"][1].read_bytes())
    bootstrap = _sha256_bytes(BOOTSTRAP_LOCK.read_bytes())
    combined = _lock_set_digest(api, parse)
    destination = BACKEND_ROOT / "app" / "release" / "generated_lock_digests.py"
    destination.write_text(
        f'''"""Generated SHA-256 identities for the reviewed Python lock set."""\n\n'''
        "from __future__ import annotations\n\n"
        "import hashlib\nimport json\nfrom pathlib import Path\nfrom typing import Final\n\n"
        'LOCK_SET_DOMAIN: Final = "mindatlas:python-lock-set:v1"\n'
        f'PYTHON_VERSION: Final = "{SUPPORTED_PYTHON}"\n'
        f'RELEASE_PLATFORM: Final = "{RELEASE_PLATFORM}"\n\n'
        f'API_WORKER_LOCK_SHA256: Final = "{api}"\n'
        f'PARSE_WORKER_LOCK_SHA256: Final = "{parse}"\n'
        f'COMPILER_BOOTSTRAP_LOCK_SHA256: Final = "{bootstrap}"\n'
        f'DEPENDENCY_LOCK_SET_SHA256: Final = "{combined}"\n\n'
        "def _canonical_json_bytes(value: object) -> bytes:\n"
        "    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(\",\", \":\"), allow_nan=False).encode(\"utf-8\")\n\n"
        "def _sha256_bytes(value: bytes) -> str:\n    return hashlib.sha256(value).hexdigest()\n\n"
        "def _lock_set_digest(api_digest: str, parse_digest: str) -> str:\n"
        "    return _sha256_bytes(_canonical_json_bytes({\"domain\": LOCK_SET_DOMAIN, \"python\": PYTHON_VERSION, \"platform\": RELEASE_PLATFORM, \"locks\": [[\"api-worker.lock\", api_digest], [\"parse-worker.lock\", parse_digest]]}))\n\n"
        "def verify_generated_lock_digests(requirements_dir: Path | str) -> None:\n"
        "    root = Path(requirements_dir)\n"
        "    expected = {\"api-worker.lock\": API_WORKER_LOCK_SHA256, \"parse-worker.lock\": PARSE_WORKER_LOCK_SHA256, \"compiler-bootstrap.lock\": COMPILER_BOOTSTRAP_LOCK_SHA256}\n"
        "    actual = {}\n"
        "    for name, digest in expected.items():\n"
        "        path = root / name\n"
        "        if not path.is_file():\n            raise RuntimeError(f\"missing dependency lock: {name}\")\n"
        "        actual[name] = _sha256_bytes(path.read_bytes())\n"
        "        if actual[name] != digest:\n            raise RuntimeError(f\"dependency lock digest mismatch: {name}\")\n"
        "    if _lock_set_digest(actual[\"api-worker.lock\"], actual[\"parse-worker.lock\"]) != DEPENDENCY_LOCK_SET_SHA256:\n"
        "        raise RuntimeError(\"dependency lock-set digest mismatch\")\n\n"
        "__all__ = (\"API_WORKER_LOCK_SHA256\", \"PARSE_WORKER_LOCK_SHA256\", \"COMPILER_BOOTSTRAP_LOCK_SHA256\", \"DEPENDENCY_LOCK_SET_SHA256\", \"verify_generated_lock_digests\")\n",
        encoding="utf-8",
    )


def compile_locks(*, write: bool) -> None:
    image = load_compiler_image_reference()
    _docker_available()
    with tempfile.TemporaryDirectory(prefix="mindatlas-lock-", dir=BACKEND_ROOT) as temp:
        temp_root = Path(temp)
        generated: dict[str, Path] = {}
        for name, (input_path, committed_path) in TARGETS.items():
            output = temp_root / committed_path.name
            generated[name] = output
            _container_compile(image, input_path, output)
            _normalize_header(output, input_path, image)
            _validate_generated_bytes(output)
        for name, output in generated.items():
            committed = TARGETS[name][1]
            if not write:
                _validate_generated_bytes(committed)
                if output.read_bytes() != committed.read_bytes():
                    raise LockCompileError(f"{committed.name}: differs from pinned compiler output")
                print(f"{committed.name}: byte-identical")
        if write:
            for name, output in generated.items():
                committed = TARGETS[name][1]
                replacement = committed.with_suffix(committed.suffix + ".tmp")
                replacement.write_bytes(output.read_bytes())
                os.replace(replacement, committed)
                print(f"{committed.name}: replaced")
            write_generated_lock_digests()


def clean_install(target: str, requested_platform: str) -> None:
    if requested_platform != RELEASE_PLATFORM:
        raise LockCompileError("clean-install requires --platform linux/amd64")
    image = load_compiler_image_reference()
    _docker_available()
    if target not in TARGETS:
        raise LockCompileError(f"unknown clean-install target: {target}")
    _, lock = TARGETS[target]
    relative_lock = lock.relative_to(BACKEND_ROOT)
    install_args = [
        "python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--require-hashes",
    ]
    if target == "parse-worker":
        install_args.extend(("--extra-index-url", PYTORCH_CPU_INDEX))
    install_args.extend(("-r", str(relative_lock)))
    install_command = shlex.join(install_args)
    smoke_import = {
        "api-worker": (
            "import fastapi, sqlalchemy, alembic, cryptography, langgraph, openai; "
            "import app.main, app.assistant.worker; "
            "from importlib.metadata import version; "
            "assert version('langgraph') == '0.3.34'; "
            "assert version('pydantic').split('.')[0] == '2'; "
            "print('api-worker-import-smoke: ok')"
        ),
        "parse-worker": (
            "import docling, transformers, huggingface_hub, torch, torchvision, "
            "rapidocr_onnxruntime, app.attachment.worker; "
            "from importlib.metadata import distributions; "
            "assert not torch.cuda.is_available(); "
            "assert not any(d.metadata['Name'].lower().startswith('nvidia-') for d in distributions()); "
            "print('parse-worker-import-smoke: ok')"
        ),
    }[target]
    smoke = (
        "python -m venv /tmp/clean-venv && "
        + install_command
        + " && "
        "python -m pip check && "
        "python -c " + shlex.quote(smoke_import) + " && "
        "python -c 'from importlib.metadata import distributions; "
        "names=sorted((d.metadata.get(\"Name\") or \"\").lower().replace(\"_\",\"-\") for d in distributions()); "
        "assert names, \"empty distribution set\"; print(\"clean-install-distributions: ok\")'"
    )
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            RELEASE_PLATFORM,
            "-v",
            f"{BACKEND_ROOT}:/workspace:ro",
            "-w",
            "/workspace",
            image,
            "sh",
            "-eu",
            "-c",
            smoke,
        ],
        cwd=BACKEND_ROOT,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=("clean-install",), default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--target", choices=tuple(TARGETS))
    parser.add_argument("--platform", default=RELEASE_PLATFORM)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.write and args.check:
            raise LockCompileError("--write and --check are mutually exclusive")
        if args.mode == "clean-install":
            if not args.target:
                raise LockCompileError("clean-install requires --target")
            clean_install(args.target, args.platform)
        elif args.write or args.check:
            compile_locks(write=args.write)
        else:
            parser.error("choose --write, --check, or clean-install")
    except LockCompileError as exc:
        print(f"lock_compile_error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
