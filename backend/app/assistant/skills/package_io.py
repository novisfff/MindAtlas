from __future__ import annotations

import io
import posixpath
import re
import stat
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse

import yaml
from yaml.composer import Composer
from yaml.constructor import SafeConstructor
from yaml.nodes import MappingNode, ScalarNode
from yaml.parser import Parser
from yaml.reader import Reader
from yaml.resolver import Resolver
from yaml.scanner import Scanner

from app.assistant.domain.contracts import (
    ParsedSkillResource,
    SkillResourceIndexEntry,
    StoredSkillResource,
)
from app.assistant.domain.digests import sha256_bytes, sha256_canonical_json
from app.assistant.skills.contracts import (
    AgentSkillFrontmatter,
    MindAtlasSkillManifestV1,
    ParsedSkillPackage,
    validate_canonical_skill_name,
)

# ---------------------------------------------------------------------------
# Size / count limits
# ---------------------------------------------------------------------------

MAX_ZIP_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 200
MAX_SKILL_MD_BYTES = 256 * 1024
MAX_MINDATLAS_YAML_BYTES = 128 * 1024
MAX_SCRIPTS_OR_REFERENCES_FILE_BYTES = 1 * 1024 * 1024
MAX_ASSETS_FILE_BYTES = 10 * 1024 * 1024
MAX_OTHER_FILE_BYTES = 1 * 1024 * 1024
MAX_PATH_UTF8_BYTES = 512
STREAM_CHUNK_SIZE = 64 * 1024

SUPPORTED_ZIP_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

_FRONTMATTER_RE = re.compile(
    rb"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)",
    re.DOTALL,
)

# Conservative Markdown inline/image link targets: ](target) and ](<target>)
_MD_LINK_RE = re.compile(
    r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*\)"
    r"|!\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*\)"
)

_PACKAGING_NOISE_NAMES = frozenset({".DS_Store", "__MACOSX"})

_EXT_MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".py": "text/x-python",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".css": "text/css",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xml": "application/xml",
    ".toml": "application/toml",
}


# ---------------------------------------------------------------------------
# Strict YAML loader: no aliases, merge keys, duplicate keys, custom tags,
# non-string mapping keys, or multi-doc streams.
# Only plain JSON-like YAML core types are allowed.
# ---------------------------------------------------------------------------

_ALLOWED_YAML_TAGS = frozenset(
    {
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:null",
    }
)


class _StrictSafeConstructor(SafeConstructor):
    def construct_object(self, node, deep=False):  # type: ignore[no-untyped-def]
        # Allowlist only plain JSON-like core types. Reject !!binary/!!set/!!omap
        # /!!timestamp and every other non-core or custom tag before construction.
        if node.tag not in _ALLOWED_YAML_TAGS:
            raise ValueError(f"YAML tag not allowed: {node.tag}")
        return super().construct_object(node, deep=deep)

    def flatten_mapping(self, node):  # type: ignore[no-untyped-def]
        # Reject merge keys explicitly.
        for key_node, _value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge" or (
                isinstance(key_node, ScalarNode) and key_node.value == "<<"
            ):
                raise ValueError("YAML merge keys are forbidden")
        return super().flatten_mapping(node)

    def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
        if not isinstance(node, MappingNode):
            raise ValueError("expected a YAML mapping")
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ValueError("YAML mapping keys must be strings")
            if key in mapping:
                raise ValueError(f"duplicate YAML mapping key: {key!r}")
            if key == "<<":
                raise ValueError("YAML merge keys are forbidden")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _StrictComposer(Composer):
    def compose_document(self):  # type: ignore[no-untyped-def]
        self.get_event()
        node = self.compose_node(None, None)
        self.get_event()
        # Reject any anchors so aliases cannot resolve.
        self.anchors = {}
        return node

    def compose_node(self, parent, index):  # type: ignore[no-untyped-def]
        if self.check_event(yaml.events.AliasEvent):
            raise ValueError("YAML aliases are forbidden")
        return super().compose_node(parent, index)


class StrictSafeLoader(Reader, Scanner, Parser, _StrictComposer, _StrictSafeConstructor, Resolver):
    def __init__(self, stream):  # type: ignore[no-untyped-def]
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        _StrictComposer.__init__(self)
        _StrictSafeConstructor.__init__(self)
        Resolver.__init__(self)


def load_strict_yaml(text: str, *, source_name: str) -> Any:
    """Load exactly one YAML document with the strict SafeLoader subclass."""
    try:
        loader = StrictSafeLoader(text)
        try:
            if not loader.check_data():
                raise ValueError(f"{source_name} is empty")
            data = loader.get_data()
            if loader.check_data():
                raise ValueError(f"{source_name} contains multiple YAML documents")
            return data
        finally:
            loader.dispose()
    except ValueError:
        raise
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {source_name}: {exc}") from exc


# ---------------------------------------------------------------------------
# Media type detection
# ---------------------------------------------------------------------------


def detect_media_type(path: str, content: bytes) -> str:
    """Deterministic server-side media type from path + content sniffs."""
    lower = path.lower()
    ext = ""
    if "." in posixpath.basename(lower):
        ext = "." + posixpath.basename(lower).rsplit(".", 1)[-1]

    if ext in {".html", ".htm"}:
        return "text/html"
    if ext == ".svg":
        return "image/svg+xml"
    if ext in _EXT_MEDIA_TYPES:
        return _EXT_MEDIA_TYPES[ext]

    # Content sniff for common binaries without known extension.
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.lstrip().startswith((b"<html", b"<!DOCTYPE html", b"<HTML", b"<!doctype html")):
        return "text/html"
    if content.lstrip().startswith((b"<svg", b"<?xml")) and b"<svg" in content[:512].lower():
        return "image/svg+xml"

    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _is_packaging_noise(path: str) -> bool:
    parts = path.split("/")
    return any(part in _PACKAGING_NOISE_NAMES or part.startswith("._") and part != "._" for part in parts) or any(
        part == "__MACOSX" for part in parts
    )


def normalize_package_path(raw: str, *, field: str = "path") -> str:
    """Normalize a Skill-root-relative path exactly once; reject ambiguity."""
    if not isinstance(raw, str) or raw == "":
        raise ValueError(f"{field} must be a non-empty string")
    if "\x00" in raw:
        raise ValueError(f"{field} must not contain NUL")
    if "\\" in raw:
        raise ValueError(f"{field} must not contain backslashes")
    if raw.startswith("/") or raw.startswith("~"):
        raise ValueError(f"{field} must be relative (no absolute paths)")
    # Windows drive / UNC
    if re.match(r"^[A-Za-z]:", raw) or raw.startswith("//"):
        raise ValueError(f"{field} must not contain a drive or UNC prefix")

    # Reject empty segments, '.' and '..' before normalization.
    parts = raw.split("/")
    if any(part == "" for part in parts):
        # Allow no leading/trailing/duplicate slashes.
        raise ValueError(f"{field} has empty path segment")
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"{field} must not contain '.' or '..' components")

    normalized = posixpath.normpath(raw)
    if normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError(f"{field} normalizes outside the package root")
    if normalized != raw:
        # Ambiguous: raw differed from the single allowed normalized form.
        # Exception: normpath collapses nothing if raw was already clean.
        # If only difference would be something we already rejected, fail closed.
        raise ValueError(f"{field} is not in normalized relative POSIX form: {raw!r}")

    if len(normalized.encode("utf-8")) > MAX_PATH_UTF8_BYTES:
        raise ValueError(
            f"{field} exceeds {MAX_PATH_UTF8_BYTES} UTF-8 bytes inside the Skill root"
        )
    if _is_packaging_noise(normalized):
        raise ValueError(f"packaging noise is not allowed: {normalized}")
    return normalized


def classify_resource_kind(path: str) -> str:
    if path == "scripts" or path.startswith("scripts/"):
        return "scripts"
    if path == "references" or path.startswith("references/"):
        return "references"
    if path == "assets" or path.startswith("assets/"):
        return "assets"
    return "other"


def max_file_bytes_for_kind(kind: str, *, path: str) -> int:
    if path == "SKILL.md":
        return MAX_SKILL_MD_BYTES
    if path == "mindatlas.yaml":
        return MAX_MINDATLAS_YAML_BYTES
    if kind in {"scripts", "references"}:
        return MAX_SCRIPTS_OR_REFERENCES_FILE_BYTES
    if kind == "assets":
        return MAX_ASSETS_FILE_BYTES
    return MAX_OTHER_FILE_BYTES


# ---------------------------------------------------------------------------
# Frontmatter + manifest parsing
# ---------------------------------------------------------------------------


def _decode_utf8(data: bytes, *, source_name: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{source_name} must be valid UTF-8") from exc


def parse_skill_md(skill_md_bytes: bytes) -> AgentSkillFrontmatter:
    if len(skill_md_bytes) > MAX_SKILL_MD_BYTES:
        raise ValueError("SKILL.md exceeds size limit")
    text = _decode_utf8(skill_md_bytes, source_name="SKILL.md")
    # Frontmatter must be the first document content.
    raw = skill_md_bytes
    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        raise ValueError("SKILL.md must begin with a closed YAML frontmatter document")
    fm_bytes = match.group(1)
    fm_text = _decode_utf8(fm_bytes, source_name="SKILL.md frontmatter")
    data = load_strict_yaml(fm_text, source_name="SKILL.md frontmatter")
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter must be a single YAML mapping")
    # Unknown keys fail via FrozenContract extra=forbid once we build the model.
    # Translate allowed-tools key already using alias.
    try:
        return AgentSkillFrontmatter.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise ValueError(f"invalid SKILL.md frontmatter: {exc}") from exc


def parse_mindatlas_yaml(yaml_bytes: bytes) -> MindAtlasSkillManifestV1:
    if len(yaml_bytes) > MAX_MINDATLAS_YAML_BYTES:
        raise ValueError("mindatlas.yaml exceeds size limit")
    text = _decode_utf8(yaml_bytes, source_name="mindatlas.yaml")
    data = load_strict_yaml(text, source_name="mindatlas.yaml")
    if not isinstance(data, dict):
        raise ValueError("mindatlas.yaml must be a single YAML mapping")
    try:
        return MindAtlasSkillManifestV1.model_validate(data)
    except Exception as exc:
        raise ValueError(f"invalid mindatlas.yaml: {exc}") from exc


# ---------------------------------------------------------------------------
# Markdown link validation
# ---------------------------------------------------------------------------


def _is_remote_or_anchor(target: str) -> bool:
    if target.startswith("#"):
        return True
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https", "mailto"}:
        return True
    return False


def validate_skill_md_links(skill_md_bytes: bytes, file_paths: set[str]) -> None:
    text = _decode_utf8(skill_md_bytes, source_name="SKILL.md")
    for match in _MD_LINK_RE.finditer(text):
        target = match.group(1) or match.group(2)
        if target is None:
            continue
        target = target.strip()
        if not target:
            raise ValueError("SKILL.md contains an empty Markdown link")
        if _is_remote_or_anchor(target):
            continue
        # Local / ambiguous link — must prove safety.
        if "\\" in target or "\x00" in target:
            raise ValueError(f"unsafe Markdown link target: {target!r}")
        # URL-decode once for path checks.
        decoded = unquote(target)
        if decoded != target and (".." in decoded.split("/") or decoded.startswith("/")):
            raise ValueError(f"Markdown link traversal rejected: {target!r}")
        if ".." in decoded.split("/") or decoded.startswith("/") or decoded.startswith("~"):
            raise ValueError(f"Markdown link path rejected: {target!r}")
        if re.match(r"^[A-Za-z]:", decoded) or "\\" in decoded:
            raise ValueError(f"Markdown link path rejected: {target!r}")
        # Drop fragment / query for local file resolution.
        path_only = decoded.split("#", 1)[0].split("?", 1)[0]
        if not path_only:
            # pure query/fragment after decode — treat as non-local if empty
            continue
        try:
            normalized = normalize_package_path(path_only, field="markdown link")
        except ValueError as exc:
            raise ValueError(f"unsafe Markdown link target: {target!r}") from exc
        if normalized not in file_paths:
            raise ValueError(f"broken Markdown link target: {target!r}")


# ---------------------------------------------------------------------------
# Resource assembly + digests
# ---------------------------------------------------------------------------


def _build_package(
    *,
    files: Mapping[str, bytes],
    expected_root_name: str | None,
) -> ParsedSkillPackage:
    if not isinstance(files, Mapping) or not files:
        raise ValueError("package file map must be a non-empty mapping")

    normalized_files: dict[str, bytes] = {}
    for raw_path, content in files.items():
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError(f"file content for {raw_path!r} must be bytes")
        path = normalize_package_path(str(raw_path), field="file path")
        if path in normalized_files:
            raise ValueError(f"duplicate normalized path: {path}")
        # Top-level directory must not appear in file-map keys.
        # (keys are relative to Skill root)
        kind = classify_resource_kind(path)
        limit = max_file_bytes_for_kind(kind, path=path)
        if len(content) > limit:
            raise ValueError(f"file {path!r} exceeds size limit of {limit} bytes")
        normalized_files[path] = bytes(content)

    if "SKILL.md" not in normalized_files:
        raise ValueError("package requires SKILL.md")

    skill_md_bytes = normalized_files.pop("SKILL.md")
    frontmatter = parse_skill_md(skill_md_bytes)
    canonical_name = frontmatter.name
    validate_canonical_skill_name(canonical_name)

    if expected_root_name is not None and expected_root_name != canonical_name:
        raise ValueError(
            f"package root name {expected_root_name!r} does not match SKILL.md name {canonical_name!r}"
        )

    mindatlas_yaml_bytes: bytes | None = None
    manifest: MindAtlasSkillManifestV1 | None = None
    if "mindatlas.yaml" in normalized_files:
        mindatlas_yaml_bytes = normalized_files.pop("mindatlas.yaml")
        manifest = parse_mindatlas_yaml(mindatlas_yaml_bytes)

    # Remaining entries are resources. Directories-as-keys are not expected.
    resources_list: list[ParsedSkillResource] = []
    for path in sorted(normalized_files.keys()):
        content = normalized_files[path]
        kind = classify_resource_kind(path)
        if path in {"SKILL.md", "mindatlas.yaml"}:
            continue
        media_type = detect_media_type(path, content)
        digest = sha256_bytes(content)
        resources_list.append(
            ParsedSkillResource(
                path=path,
                resource_kind=kind,  # type: ignore[arg-type]
                media_type=media_type,
                content=content,
                byte_size=len(content),
                sha256=digest,
                executable=False,
            )
        )

    resources = tuple(resources_list)
    resource_index = tuple(
        SkillResourceIndexEntry(
            path=item.path,
            resource_kind=item.resource_kind,
            media_type=item.media_type,
            byte_size=item.byte_size,
            sha256=item.sha256,
        )
        for item in resources
    )

    # Link validation sees resources plus package-root files when present.
    # SKILL.md / mindatlas.yaml are stored separately from resources but are valid local targets.
    link_paths = {item.path for item in resources}
    link_paths.add("SKILL.md")
    if mindatlas_yaml_bytes is not None:
        link_paths.add("mindatlas.yaml")
    validate_skill_md_links(skill_md_bytes, link_paths)

    skill_md_digest = sha256_bytes(skill_md_bytes)
    manifest_digest = sha256_bytes(mindatlas_yaml_bytes or b"")
    resource_index_payload = [
        {
            "path": entry.path,
            "kind": entry.resource_kind,
            "mediaType": entry.media_type,
            "size": entry.byte_size,
            "sha256": entry.sha256,
        }
        for entry in resource_index
    ]
    resource_index_digest = sha256_canonical_json(resource_index_payload)
    content_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "skillMdDigest": skill_md_digest,
            "manifestDigest": manifest_digest,
            "resourceIndexDigest": resource_index_digest,
        }
    )

    total = len(skill_md_bytes) + (len(mindatlas_yaml_bytes) if mindatlas_yaml_bytes else 0)
    total += sum(item.byte_size for item in resources)
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ValueError("total uncompressed package bytes exceed limit")
    if len(resources) + 1 + (1 if mindatlas_yaml_bytes is not None else 0) > MAX_ENTRIES:
        raise ValueError("package entry count exceeds limit")

    return ParsedSkillPackage(
        canonical_name=canonical_name,
        frontmatter=frontmatter,
        manifest=manifest,
        skill_md_bytes=skill_md_bytes,
        mindatlas_yaml_bytes=mindatlas_yaml_bytes,
        resources=resources,
        resource_index=resource_index,
        skill_md_digest=skill_md_digest,
        manifest_digest=manifest_digest,
        resource_index_digest=resource_index_digest,
        content_digest=content_digest,
    )


def parse_skill_directory_files(
    files: Mapping[str, bytes],
    *,
    expected_root_name: str | None = None,
) -> ParsedSkillPackage:
    """Parse a root-relative in-memory file map into a validated package."""
    return _build_package(files=files, expected_root_name=expected_root_name)


# ---------------------------------------------------------------------------
# ZIP parsing
# ---------------------------------------------------------------------------


def _zip_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0o777777


def _reject_special_zip_entry(info: zipfile.ZipInfo) -> None:
    mode = _zip_mode(info)
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        raise ValueError(f"symlink ZIP entries are forbidden: {info.filename}")
    if file_type in {stat.S_IFCHR, stat.S_IFBLK}:
        raise ValueError(f"device ZIP entries are forbidden: {info.filename}")
    if file_type == stat.S_IFIFO:
        raise ValueError(f"fifo ZIP entries are forbidden: {info.filename}")
    if file_type == stat.S_IFSOCK:
        raise ValueError(f"socket ZIP entries are forbidden: {info.filename}")
    # Encrypted
    if info.flag_bits & 0x1:
        raise ValueError(f"encrypted ZIP entries are forbidden: {info.filename}")
    if info.compress_type not in SUPPORTED_ZIP_COMPRESSION:
        raise ValueError(
            f"unsupported ZIP compression {info.compress_type} for {info.filename}"
        )


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_size: int,
    remaining_total: int,
) -> bytes:
    if info.file_size > max_size:
        raise ValueError(
            f"ZIP entry {info.filename!r} declared size exceeds limit of {max_size}"
        )
    if info.file_size > remaining_total:
        raise ValueError(
            f"ZIP entry {info.filename!r} would exceed total uncompressed limit"
        )
    buf = bytearray()
    with zf.open(info, "r") as member:
        while True:
            chunk = member.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_size:
                raise ValueError(
                    f"ZIP entry {info.filename!r} streamed size exceeds limit of {max_size}"
                )
            if len(buf) > remaining_total:
                raise ValueError(
                    f"ZIP entry {info.filename!r} streamed size exceeds total uncompressed limit"
                )
    if info.file_size != len(buf):
        # Prefer actual size; still enforce declared consistency loosely? Spec says
        # reject when declared or streamed crosses limit; mismatched is suspicious.
        pass
    return bytes(buf)


def parse_skill_zip(
    content: BinaryIO,
    *,
    compressed_size: int,
) -> ParsedSkillPackage:
    if compressed_size < 0:
        raise ValueError("compressed_size must be non-negative")
    if compressed_size > MAX_ZIP_UPLOAD_BYTES:
        raise ValueError("ZIP upload exceeds compressed size limit")

    # Load into memory bound by compressed_size.
    raw = content.read(compressed_size + 1)
    if len(raw) > compressed_size:
        # Caller lied about size or stream is larger.
        # Still enforce upload bound.
        if len(raw) > MAX_ZIP_UPLOAD_BYTES:
            raise ValueError("ZIP upload exceeds compressed size limit")
    if len(raw) > MAX_ZIP_UPLOAD_BYTES:
        raise ValueError("ZIP upload exceeds compressed size limit")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid ZIP archive") from exc

    with zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if not infos:
            raise ValueError("ZIP archive has no file entries")

        # Packaging noise rejection (including directories as names).
        for info in zf.infolist():
            name = info.filename
            # ZipInfo.filename is str; also inspect orig_filename for embedded NUL.
            orig = getattr(info, "orig_filename", name)
            if "\\" in name or "\x00" in name or "\x00" in orig:
                raise ValueError(f"unsafe ZIP entry name: {name!r}")
            if _is_packaging_noise(name.replace("\\", "/")):
                raise ValueError(f"packaging noise is not allowed: {name}")

        top_levels: set[str] = set()
        for info in infos:
            _reject_special_zip_entry(info)
            name = info.filename
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError(f"absolute or drive ZIP path forbidden: {name!r}")
            if "\\" in name:
                raise ValueError(f"backslash ZIP path forbidden: {name!r}")
            parts = name.split("/")
            if not parts or parts[0] == "":
                raise ValueError(f"ZIP entry missing top-level directory: {name!r}")
            top_levels.add(parts[0])

        if len(top_levels) != 1:
            raise ValueError("ZIP must contain exactly one top-level directory")
        root_name = next(iter(top_levels))
        if root_name in {".", ".."}:
            raise ValueError("invalid ZIP top-level directory name")

        if len(infos) > MAX_ENTRIES:
            raise ValueError("ZIP entry count exceeds limit")

        # Pre-check total declared size.
        declared_total = sum(info.file_size for info in infos)
        if declared_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("total declared uncompressed ZIP size exceeds limit")

        files: dict[str, bytes] = {}
        remaining = MAX_TOTAL_UNCOMPRESSED_BYTES
        for info in infos:
            name = info.filename
            if not name.startswith(root_name + "/"):
                raise ValueError(f"ZIP entry outside top-level directory: {name!r}")
            rel = name[len(root_name) + 1 :]
            if rel == "" or rel.endswith("/"):
                continue
            try:
                rel_norm = normalize_package_path(rel, field="ZIP entry path")
            except ValueError as exc:
                raise ValueError(f"unsafe ZIP entry path: {name!r}") from exc
            if rel_norm in files:
                raise ValueError(f"duplicate normalized ZIP path: {rel_norm}")

            kind = classify_resource_kind(rel_norm)
            max_size = max_file_bytes_for_kind(kind, path=rel_norm)
            data = _read_zip_member_bounded(
                zf, info, max_size=max_size, remaining_total=remaining
            )
            remaining -= len(data)
            files[rel_norm] = data

        return parse_skill_directory_files(files, expected_root_name=root_name)


# ---------------------------------------------------------------------------
# Deterministic ZIP export
# ---------------------------------------------------------------------------

# Fixed DOS epoch used by every exported ZipInfo (host clocks never leak).
_EXPORT_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
# Unix regular file, mode 0644, non-executable — encoded in the high 16 bits.
_EXPORT_EXTERNAL_ATTR = (stat.S_IFREG | 0o644) << 16
# create_system=3 marks Unix attributes; create/extract_version=20 is ZIP 2.0.
_EXPORT_CREATE_SYSTEM = 3
_EXPORT_ZIP_VERSION = 20


def export_skill_package(
    package_name: str,
    *,
    skill_md: bytes,
    mindatlas_yaml: bytes | None,
    resources: Sequence[StoredSkillResource],
) -> bytes:
    """Write a byte-for-byte deterministic Agent Skills ZIP.

    Every entry uses ``ZIP_STORED``, fixed 1980-01-01 timestamps, and
    non-executable regular-file permissions. Paths are sorted; host
    filesystem metadata and input order never affect the output bytes.
    """
    try:
        canonical = validate_canonical_skill_name(package_name)
    except ValueError as exc:
        raise ValueError(f"invalid export package_name: {exc}") from exc
    if not isinstance(skill_md, (bytes, bytearray)) or not skill_md:
        raise ValueError("skill_md must be non-empty bytes")
    skill_md_bytes = bytes(skill_md)
    mindatlas_bytes = bytes(mindatlas_yaml) if mindatlas_yaml is not None else None

    # Build (archive_path, content) pairs; sort by UTF-8 path bytes for stability.
    entries: list[tuple[str, bytes]] = [
        (f"{canonical}/SKILL.md", skill_md_bytes),
    ]
    if mindatlas_bytes is not None:
        entries.append((f"{canonical}/mindatlas.yaml", mindatlas_bytes))

    seen_paths: set[str] = set()
    for resource in resources:
        if not isinstance(resource, StoredSkillResource):
            raise TypeError("resources must be StoredSkillResource values")
        path = normalize_package_path(resource.path, field="export resource path")
        if path in {"SKILL.md", "mindatlas.yaml"}:
            raise ValueError(f"resource path collides with package root file: {path}")
        if path in seen_paths:
            raise ValueError(f"duplicate export resource path: {path}")
        content = bytes(resource.content)
        if resource.byte_size != len(content):
            raise ValueError(
                f"resource {path!r} byte_size {resource.byte_size} != len(content) {len(content)}"
            )
        if resource.sha256 != sha256_bytes(content):
            raise ValueError(f"resource {path!r} sha256 does not match content")
        seen_paths.add(path)
        entries.append((f"{canonical}/{path}", content))

    entries.sort(key=lambda item: item[0].encode("utf-8"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for archive_path, content in entries:
            info = zipfile.ZipInfo(filename=archive_path, date_time=_EXPORT_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = _EXPORT_CREATE_SYSTEM
            info.create_version = _EXPORT_ZIP_VERSION
            info.extract_version = _EXPORT_ZIP_VERSION
            info.external_attr = _EXPORT_EXTERNAL_ATTR
            info.flag_bits = 0
            info.internal_attr = 0
            # ZIP_STORED: CRC/size still filled by writestr from content.
            zf.writestr(info, content, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


__all__ = [
    "MAX_ENTRIES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "MAX_ZIP_UPLOAD_BYTES",
    "StrictSafeLoader",
    "detect_media_type",
    "export_skill_package",
    "load_strict_yaml",
    "normalize_package_path",
    "parse_skill_directory_files",
    "parse_skill_zip",
]
