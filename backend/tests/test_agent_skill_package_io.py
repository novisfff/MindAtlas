from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.skills.contracts import (  # noqa: E402
    RESERVED_SKILL_LOOKUP_NAMES,
    normalize_skill_lookup_name,
)
from app.assistant.skills.package_io import (  # noqa: E402
    MAX_ENTRIES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    MAX_ZIP_UPLOAD_BYTES,
    detect_media_type,
    parse_skill_directory_files,
    parse_skill_zip,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "agent_skills" / "valid-weekly-review"
)


def _fixture_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(FIXTURE_ROOT.rglob("*")):
        if path.is_file():
            rel = path.relative_to(FIXTURE_ROOT).as_posix()
            files[rel] = path.read_bytes()
    return files


def _minimal_skill_md(
    *,
    name: str = "weekly-review",
    description: str = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    ),
    extra_frontmatter: str = "",
    body: str = "# Weekly review\n\nBody.\n",
) -> bytes:
    fm = f"name: {name}\ndescription: {description}\n{extra_frontmatter}".rstrip() + "\n"
    return f"---\n{fm}---\n\n{body}".encode("utf-8")


def _files(
    *,
    skill_md: bytes | None = None,
    mindatlas: bytes | None = None,
    resources: dict[str, bytes] | None = None,
    include_mindatlas: bool = True,
) -> dict[str, bytes]:
    out: dict[str, bytes] = {
        "SKILL.md": skill_md
        if skill_md is not None
        else _minimal_skill_md(body="# Weekly review\n\nBody.\n"),
    }
    if include_mindatlas and mindatlas is not None:
        out["mindatlas.yaml"] = mindatlas
    elif include_mindatlas and mindatlas is None:
        # omit unless provided; default minimal package has no mindatlas
        pass
    if resources:
        out.update(resources)
    return out


def _zip_bytes(
    members: dict[str, bytes],
    *,
    compress_type: int = zipfile.ZIP_STORED,
    external_attr: dict[str, int] | None = None,
    flag_bits: dict[str, int] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compress_type
            info.external_attr = (external_attr or {}).get(name, 0o100644 << 16)
            info.flag_bits = (flag_bits or {}).get(name, 0)
            zf.writestr(info, content)
    return buf.getvalue()


def _parse_zip(data: bytes):
    return parse_skill_zip(io.BytesIO(data), compressed_size=len(data))


# ---------------------------------------------------------------------------
# normalize_skill_lookup_name fixed vectors
# ---------------------------------------------------------------------------


def test_normalize_skill_lookup_name_fixed_vectors() -> None:
    # NFKC: fullwidth latin letters and compatibility forms
    assert normalize_skill_lookup_name("Ｗｅｅｋｌｙ") == "weekly"
    assert normalize_skill_lookup_name("ﬁle") == "file"  # fi ligature -> fi

    # casefold expansion (German sharp s)
    assert normalize_skill_lookup_name("STRASSE") == "strasse"
    assert normalize_skill_lookup_name("Straße") == "strasse"

    # preserve internal whitespace; trim ends
    assert normalize_skill_lookup_name("  weekly  review  ") == "weekly  review"
    assert normalize_skill_lookup_name("weekly\treview") == "weekly\treview"

    # controls / NUL / slash / backslash rejection
    for bad in ("a\x00b", "a\nb", "a/b", "a\\b", "a\x1fb", ""):
        with pytest.raises(ValueError):
            normalize_skill_lookup_name(bad)

    # reserved names
    assert "general_chat" in RESERVED_SKILL_LOOKUP_NAMES
    assert "general-chat" in RESERVED_SKILL_LOOKUP_NAMES
    assert normalize_skill_lookup_name("General_Chat") == "general_chat"
    assert normalize_skill_lookup_name("General-Chat") == "general-chat"

    # pre/post UTF-8/scalar limits: 128 scalars and 512 UTF-8 bytes
    ok_128 = "a" * 128
    assert normalize_skill_lookup_name(ok_128) == ok_128
    with pytest.raises(ValueError):
        normalize_skill_lookup_name("a" * 129)

    # Multibyte scalars count toward the 128-scalar cap (你 is one scalar / 3 bytes).
    assert normalize_skill_lookup_name("你" * 128) == "你" * 128
    with pytest.raises(ValueError):
        normalize_skill_lookup_name("你" * 129)

    # Casefold expansion can exceed the post-normalization scalar limit.
    # "ß" (1 scalar) casefolds to "ss" (2 scalars).
    with pytest.raises(ValueError):
        normalize_skill_lookup_name("ß" * 65)  # -> 130 scalars after casefold
    assert normalize_skill_lookup_name("ß" * 64) == "ss" * 64


# ---------------------------------------------------------------------------
# Happy path fixture
# ---------------------------------------------------------------------------


def test_valid_weekly_review_fixture_digests() -> None:
    files = _fixture_files()
    pkg = parse_skill_directory_files(files, expected_root_name=None)

    assert pkg.canonical_name == "weekly-review"
    assert (
        pkg.frontmatter.description
        == "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    )
    assert pkg.manifest is not None
    assert pkg.manifest.version == 1
    assert pkg.manifest.display_name == "周度回顾"
    assert pkg.manifest.legacy_aliases == ("weekly_review",)
    assert len(pkg.manifest.capabilities) == 2
    assert pkg.manifest.capabilities[0].type == "tool"
    assert pkg.manifest.capabilities[0].key == "search_entries"
    assert pkg.manifest.capabilities[1].type == "workflow"
    assert pkg.manifest.capabilities[1].key == "periodic_review__workflow"
    assert pkg.manifest.policy.allowed_side_effects == ("read", "compute")
    assert pkg.manifest.policy.max_skill_calls == 16
    assert pkg.manifest.policy.max_same_read_calls == 3
    assert pkg.manifest.policy.requires_terminal_output is True
    assert pkg.manifest.policy.terminal_text_allowed is True

    assert len(pkg.resources) == 1
    res = pkg.resources[0]
    assert res.path == "references/guide.md"
    assert res.resource_kind == "references"
    assert res.executable is False
    assert res.media_type == "text/markdown"
    assert res.byte_size == len(res.content)

    assert pkg.resource_index[0].path == "references/guide.md"
    assert pkg.resource_index[0].resource_kind == "references"
    assert pkg.resource_index[0].media_type == "text/markdown"
    assert pkg.resource_index[0].byte_size == res.byte_size
    assert pkg.resource_index[0].sha256 == res.sha256


# Hard-coded regression digests calculated once from exact fixture bytes.
HARDCODED_SKILL_MD_DIGEST = (
    "04cc5efcd0ed9be62586049c45f04c1f70c95f81dcb46dec0bf8528e9be7343c"
)
HARDCODED_MANIFEST_DIGEST = (
    "1b9a819752997a001c888cd6446a641db3e13645f291d99071828a899d73c3d8"
)
HARDCODED_RESOURCE_INDEX_DIGEST = (
    "bf9b9497ae22871c5d85fe878e8ba93febbca678ae051dad48aab90246496796"
)
HARDCODED_CONTENT_DIGEST = (
    "2854e0d98ff48cce887d95920c3f09c040ee6de1255fb404ea2f8b18bf211502"
)


def test_valid_weekly_review_fixture_hardcoded_digests() -> None:
    """Independent hard-coded digests for cross-version regression."""
    from app.assistant.domain.digests import sha256_bytes, sha256_canonical_json

    files = _fixture_files()
    pkg = parse_skill_directory_files(files)

    expected_skill = sha256_bytes(files["SKILL.md"])
    expected_manifest = sha256_bytes(files["mindatlas.yaml"])
    guide = files["references/guide.md"]
    expected_resource_sha = sha256_bytes(guide)
    expected_index = sha256_canonical_json(
        [
            {
                "path": "references/guide.md",
                "kind": "references",
                "mediaType": "text/markdown",
                "size": len(guide),
                "sha256": expected_resource_sha,
            }
        ]
    )
    expected_content = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "skillMdDigest": expected_skill,
            "manifestDigest": expected_manifest,
            "resourceIndexDigest": expected_index,
        }
    )

    assert expected_skill == HARDCODED_SKILL_MD_DIGEST
    assert expected_manifest == HARDCODED_MANIFEST_DIGEST
    assert expected_index == HARDCODED_RESOURCE_INDEX_DIGEST
    assert expected_content == HARDCODED_CONTENT_DIGEST

    assert pkg.skill_md_digest == HARDCODED_SKILL_MD_DIGEST
    assert pkg.manifest_digest == HARDCODED_MANIFEST_DIGEST
    assert pkg.resource_index_digest == HARDCODED_RESOURCE_INDEX_DIGEST
    assert pkg.content_digest == HARDCODED_CONTENT_DIGEST


# ---------------------------------------------------------------------------
# Frontmatter contracts
# ---------------------------------------------------------------------------


def test_minimal_standard_package_passes() -> None:
    pkg = parse_skill_directory_files(_files(include_mindatlas=False))
    assert pkg.canonical_name == "weekly-review"
    assert pkg.manifest is None
    assert pkg.mindatlas_yaml_bytes is None
    assert pkg.manifest_digest == sha256_empty()
    assert pkg.resources == ()


def sha256_empty() -> str:
    from app.assistant.domain.digests import sha256_bytes

    return sha256_bytes(b"")


def test_frontmatter_must_be_first_document() -> None:
    body = b"# Title\n\n---\nname: weekly-review\ndescription: x\n---\n"
    with pytest.raises(ValueError, match="frontmatter|SKILL"):
        parse_skill_directory_files(_files(skill_md=body, include_mindatlas=False))


def test_frontmatter_requires_single_yaml_mapping() -> None:
    body = b"---\n- not\n- a\n- mapping\n---\n\n# x\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(_files(skill_md=body, include_mindatlas=False))


def test_name_and_description_required_and_bounded() -> None:
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(skill_md=_minimal_skill_md(name=""), include_mindatlas=False)
        )
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(skill_md=_minimal_skill_md(description=""), include_mindatlas=False)
        )
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(description="x" * 1025),
                include_mindatlas=False,
            )
        )


@pytest.mark.parametrize(
    "bad_name",
    ["-leading", "trailing-", "double--hyphen", "Upper", "under_score", "has space", ""],
)
def test_name_rejects_invalid_hyphen_case(bad_name: str) -> None:
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(skill_md=_minimal_skill_md(name=bad_name), include_mindatlas=False)
        )


def test_name_must_match_expected_root() -> None:
    with pytest.raises(ValueError, match="root|name|directory"):
        parse_skill_directory_files(
            _files(include_mindatlas=False), expected_root_name="other-name"
        )


def test_optional_frontmatter_fields_survive() -> None:
    skill = _minimal_skill_md(
        extra_frontmatter=(
            "license: MIT\n"
            "compatibility: requires MindAtlas >= 1.0\n"
            "metadata:\n"
            "  author: alice\n"
            "allowed-tools: Bash(git:*) Read\n"
        )
    )
    pkg = parse_skill_directory_files(_files(skill_md=skill, include_mindatlas=False))
    assert pkg.frontmatter.license == "MIT"
    assert pkg.frontmatter.compatibility == "requires MindAtlas >= 1.0"
    assert pkg.frontmatter.metadata == {"author": "alice"}
    assert pkg.frontmatter.allowed_tools == "Bash(git:*) Read"
    # allowed-tools never creates bindings
    assert pkg.manifest is None
    assert pkg.resources == ()


def test_compatibility_bounds() -> None:
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(extra_frontmatter='compatibility: ""\n'),
                include_mindatlas=False,
            )
        )
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(
                    extra_frontmatter=f"compatibility: {'x' * 501}\n"
                ),
                include_mindatlas=False,
            )
        )


def test_allowed_tools_must_be_space_delimited_string_not_list() -> None:
    skill = _minimal_skill_md(
        extra_frontmatter="allowed-tools:\n  - Bash\n  - Read\n"
    )
    with pytest.raises(ValueError, match="allowed-tools|space"):
        parse_skill_directory_files(_files(skill_md=skill, include_mindatlas=False))


def test_unknown_frontmatter_fields_rejected() -> None:
    skill = _minimal_skill_md(extra_frontmatter="display_name: no\n")
    with pytest.raises(ValueError):
        parse_skill_directory_files(_files(skill_md=skill, include_mindatlas=False))
    skill2 = _minimal_skill_md(extra_frontmatter="capabilities: []\n")
    with pytest.raises(ValueError):
        parse_skill_directory_files(_files(skill_md=skill2, include_mindatlas=False))


# ---------------------------------------------------------------------------
# mindatlas.yaml
# ---------------------------------------------------------------------------


def test_missing_mindatlas_means_valid_defaults() -> None:
    pkg = parse_skill_directory_files(_files(include_mindatlas=False))
    assert pkg.manifest is None
    assert pkg.manifest_digest == sha256_empty()


def test_manifest_version_must_be_exactly_one() -> None:
    with pytest.raises(ValueError, match="version"):
        parse_skill_directory_files(
            _files(mindatlas=b"version: 2\n", include_mindatlas=True)
        )
    with pytest.raises(ValueError, match="version"):
        parse_skill_directory_files(
            _files(mindatlas=b"version: '1'\n", include_mindatlas=True)
        )


def test_extra_extension_keys_fail() -> None:
    yaml_text = "version: 1\nunknown_field: true\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
        )


def test_policy_budgets_reject_booleans() -> None:
    """Pydantic must not coerce true→1 / false→0 for skill budget fields."""
    true_budget = (
        "version: 1\n"
        "policy:\n"
        "  max_skill_calls: true\n"
    )
    with pytest.raises(ValueError, match="budget|integer|max_skill_calls"):
        parse_skill_directory_files(
            _files(mindatlas=true_budget.encode("utf-8"), include_mindatlas=True)
        )

    false_budget = (
        "version: 1\n"
        "policy:\n"
        "  max_same_read_calls: false\n"
    )
    with pytest.raises(ValueError, match="budget|integer|max_same_read_calls"):
        parse_skill_directory_files(
            _files(mindatlas=false_budget.encode("utf-8"), include_mindatlas=True)
        )


def test_capability_pair_uniqueness_and_limits() -> None:
    yaml_text = """
version: 1
capabilities:
  - type: tool
    key: search_entries
  - type: tool
    key: search_entries
"""
    with pytest.raises(ValueError, match="unique|duplicate"):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
        )


def test_routing_and_metadata_limits() -> None:
    examples = "\n".join(f'  - "{"x" * 10}"' for _ in range(101))
    yaml_text = f"version: 1\nrouting:\n  include_examples:\n{examples}\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
        )

    long_example = "x" * 1001
    yaml_text = (
        f"version: 1\nrouting:\n  include_examples:\n  - \"{long_example}\"\n"
    )
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
        )


def test_reserved_general_chat_names_rejected() -> None:
    # Canonical names use hyphen-case; general-chat is syntactically valid but reserved.
    with pytest.raises(ValueError, match="reserved|general"):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(name="general-chat"),
                include_mindatlas=False,
            )
        )

    # Underscore form is not valid hyphen-case for the canonical name field.
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(name="general_chat"),
                include_mindatlas=False,
            )
        )

    # Alias reservation covers both normalized reserved forms (any casing).
    for alias in ("general_chat", "general-chat", "General_Chat", "GENERAL-CHAT"):
        yaml_text = f"version: 1\nlegacy_aliases:\n  - {alias}\n"
        with pytest.raises(ValueError, match="reserved|general"):
            parse_skill_directory_files(
                _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
            )


def test_agent_requires_binding_schemas_tool_workflow_optional() -> None:
    missing = """
version: 1
capabilities:
  - type: agent
    key: research_assistant__agent
"""
    with pytest.raises(ValueError, match="agent|contract|schema"):
        parse_skill_directory_files(
            _files(mindatlas=missing.encode("utf-8"), include_mindatlas=True)
        )

    ok = """
version: 1
capabilities:
  - type: agent
    key: research_assistant__agent
    contract:
      input_schema:
        type: object
        properties:
          input: {}
        required: [input]
        additionalProperties: false
      output_schema:
        type: object
        properties:
          text:
            type: string
        required: [text]
        additionalProperties: false
  - type: tool
    key: search_entries
  - type: workflow
    key: periodic_review__workflow
"""
    pkg = parse_skill_directory_files(
        _files(mindatlas=ok.encode("utf-8"), include_mindatlas=True)
    )
    agent = pkg.manifest.capabilities[0]
    assert agent.contract is not None
    assert agent.contract.completion.terminal_output is False
    assert agent.contract.completion.needs_followup is True
    assert agent.contract.completion.followup_hint is None
    assert agent.contract.input_schema["type"] == "object"
    assert pkg.manifest.capabilities[1].contract is None
    assert pkg.manifest.capabilities[2].contract is None


def test_contract_schema_uses_task1_normalization() -> None:
    yaml_text = """
version: 1
capabilities:
  - type: agent
    key: research_assistant__agent
    contract:
      input_schema:
        type: object
        properties:
          b: {type: number}
          a: {type: string, description: alpha}
        required: [b, a, b]
      output_schema:
        type: string
"""
    pkg = parse_skill_directory_files(
        _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
    )
    schema = pkg.manifest.capabilities[0].contract.input_schema
    assert schema["required"] == ["a", "b"]
    assert list(schema["properties"].keys()) == ["a", "b"]


def test_provider_aliases_bounded_and_refer_to_declared_keys() -> None:
    yaml_text = """
version: 1
capabilities:
  - type: tool
    key: search_entries
provider_aliases:
  missing_key: hint
"""
    with pytest.raises(ValueError, match="provider_aliases|key"):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
        )

    yaml_text2 = """
version: 1
capabilities:
  - type: tool
    key: search_entries
provider_aliases:
  search_entries: ""
"""
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text2.encode("utf-8"), include_mindatlas=True)
        )

    yaml_text3 = """
version: 1
capabilities:
  - type: tool
    key: search_entries
provider_aliases:
  search_entries: ok-hint
"""
    pkg = parse_skill_directory_files(
        _files(mindatlas=yaml_text3.encode("utf-8"), include_mindatlas=True)
    )
    assert pkg.manifest.provider_aliases == {"search_entries": "ok-hint"}


def test_yaml_aliases_merge_keys_duplicates_custom_tags_non_string_keys_multi_doc() -> None:
    # YAML alias
    alias_yaml = b"version: 1\nmetadata: &a {k: v}\nlegacy_aliases: *a\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=alias_yaml, include_mindatlas=True)
        )

    # merge key
    merge_yaml = b"version: 1\nmetadata:\n  <<: {a: 1}\n  b: 2\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=merge_yaml, include_mindatlas=True)
        )

    # duplicate mapping keys
    dup_yaml = b"version: 1\nmetadata:\n  a: 1\n  a: 2\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=dup_yaml, include_mindatlas=True)
        )

    # custom tags
    tag_yaml = b"version: 1\nmetadata: !!python/object/apply:os.system ['echo hi']\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=tag_yaml, include_mindatlas=True)
        )

    # non-plain YAML core types rejected even though SafeLoader accepts them
    binary_yaml = b"version: 1\nmetadata:\n  blob: !!binary aGVsbG8=\n"
    with pytest.raises(ValueError, match="tag|binary|YAML"):
        parse_skill_directory_files(
            _files(mindatlas=binary_yaml, include_mindatlas=True)
        )

    set_yaml = b"version: 1\nmetadata:\n  tags: !!set\n    ? a\n    ? b\n"
    with pytest.raises(ValueError, match="tag|set|YAML"):
        parse_skill_directory_files(
            _files(mindatlas=set_yaml, include_mindatlas=True)
        )

    omap_yaml = b"version: 1\nmetadata:\n  ordered: !!omap\n    - k: v\n"
    with pytest.raises(ValueError, match="tag|omap|YAML"):
        parse_skill_directory_files(
            _files(mindatlas=omap_yaml, include_mindatlas=True)
        )

    binary_fm = (
        b"---\nname: weekly-review\ndescription: desc\n"
        b"license: !!binary aGVsbG8=\n---\n\n# b\n"
    )
    with pytest.raises(ValueError, match="tag|binary|YAML|frontmatter"):
        parse_skill_directory_files(
            _files(skill_md=binary_fm, include_mindatlas=False)
        )

    # non-string keys
    non_string_key = b"version: 1\nmetadata:\n  1: one\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=non_string_key, include_mindatlas=True)
        )

    # multiple documents
    multi = b"---\nversion: 1\n---\nversion: 1\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=multi, include_mindatlas=True)
        )

    # same protections for SKILL.md frontmatter
    multi_fm = b"---\nname: weekly-review\ndescription: desc\n---\n---\nname: x\n---\n"
    # body after first closed frontmatter is ok if no second frontmatter required —
    # multiple YAML docs inside frontmatter block:
    multi_fm2 = (
        b"---\nname: weekly-review\ndescription: desc\n---\n\n# body\n"
    )
    parse_skill_directory_files(_files(skill_md=multi_fm2, include_mindatlas=False))

    alias_fm = b"---\nname: weekly-review\ndescription: &d desc\nlicense: *d\n---\n\n# b\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(skill_md=alias_fm, include_mindatlas=False)
        )


# ---------------------------------------------------------------------------
# Media type detector fixed vectors
# ---------------------------------------------------------------------------


def test_media_type_detector_fixed_vectors() -> None:
    assert detect_media_type("references/guide.md", b"# hi") == "text/markdown"
    assert detect_media_type("notes.txt", b"plain") == "text/plain"
    assert detect_media_type("data.json", b"{}") == "application/json"
    assert detect_media_type("cfg.yaml", b"a: 1") == "application/yaml"
    assert detect_media_type("cfg.yml", b"a: 1") == "application/yaml"
    assert detect_media_type("script.py", b"print(1)") == "text/x-python"
    assert detect_media_type("script.sh", b"echo hi") == "text/x-shellscript"
    assert detect_media_type("img.png", b"\x89PNG\r\n\x1a\n") == "image/png"
    assert detect_media_type("img.jpg", b"\xff\xd8\xff") == "image/jpeg"
    assert detect_media_type("img.gif", b"GIF89a") == "image/gif"
    assert detect_media_type("img.webp", b"RIFF....WEBP") == "image/webp"
    # active content / spoofing: extension wins only when safe; HTML/SVG forced
    assert detect_media_type("page.html", b"<html>") == "text/html"
    assert detect_media_type("icon.svg", b"<svg>") == "image/svg+xml"
    # extension spoof: .md with binary still reports text/markdown by path policy,
    # but unknown extensions with binary become octet-stream
    assert detect_media_type("blob.bin", b"\x00\x01\x02") == "application/octet-stream"
    assert detect_media_type("noext", b"") == "application/octet-stream"
    assert detect_media_type("empty.md", b"") == "text/markdown"
    # unknown extension
    assert detect_media_type("x.unknown", b"abc") == "application/octet-stream"


# ---------------------------------------------------------------------------
# ZIP safety
# ---------------------------------------------------------------------------


def _valid_zip_members(
    *,
    root: str = "weekly-review",
    skill_md: bytes | None = None,
    resources: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    skill = skill_md or _minimal_skill_md(name=root, body="# Weekly review\n\nBody.\n")
    members = {f"{root}/SKILL.md": skill}
    if resources:
        for path, content in resources.items():
            members[f"{root}/{path}"] = content
    return members


def test_zip_happy_path_and_root_name() -> None:
    data = _zip_bytes(_valid_zip_members())
    pkg = _parse_zip(data)
    assert pkg.canonical_name == "weekly-review"


def test_zip_rejects_multiple_top_level_directories() -> None:
    members = {
        "a/SKILL.md": _minimal_skill_md(name="a"),
        "b/SKILL.md": _minimal_skill_md(name="b"),
    }
    with pytest.raises(ValueError, match="top-level|root"):
        _parse_zip(_zip_bytes(members))


def test_zip_rejects_root_name_mismatch() -> None:
    members = {
        "other-name/SKILL.md": _minimal_skill_md(name="weekly-review"),
    }
    with pytest.raises(ValueError, match="root|name"):
        _parse_zip(_zip_bytes(members))


def test_zip_rejects_absolute_traversal_backslash_drive_nul_overlong_dupes() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)

    # absolute
    with pytest.raises(ValueError):
        _parse_zip(
            _zip_bytes({f"/{root}/SKILL.md": skill})
        )

    # traversal
    with pytest.raises(ValueError):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/../evil.txt": b"x",
                }
            )
        )

    # backslash
    with pytest.raises(ValueError):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/refs\\x.txt": b"x",
                }
            )
        )

    # Windows drive
    with pytest.raises(ValueError):
        _parse_zip(_zip_bytes({f"C:/{root}/SKILL.md": skill}))

    # NUL — Python's ZipFile truncates at NUL when writing, so exercise the path
    # guard through the root-relative file map (same normalizer ZIP uses).
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            {
                "SKILL.md": skill,
                "a\x00b.txt": b"x",
            }
        )

    # overlong path (>512 UTF-8 bytes inside skill root)
    long_name = "a" * 509 + ".txt"  # 513 UTF-8 bytes
    with pytest.raises(ValueError):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/{long_name}": b"x",
                }
            )
        )

    # normalized duplicates (./foo and foo)
    with pytest.raises(ValueError):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/foo.txt": b"1",
                    f"{root}/./foo.txt": b"2",
                }
            )
        )


def test_zip_rejects_symlink_device_fifo_mode_bits() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    members = {f"{root}/SKILL.md": skill, f"{root}/link": b"target"}
    # symlink: S_IFLNK = 0o120000
    with pytest.raises(ValueError, match="symlink|mode|type"):
        _parse_zip(
            _zip_bytes(members, external_attr={f"{root}/link": 0o120777 << 16})
        )
    # FIFO: S_IFIFO = 0o010000
    with pytest.raises(ValueError, match="fifo|mode|type|device"):
        _parse_zip(
            _zip_bytes(members, external_attr={f"{root}/link": 0o010666 << 16})
        )
    # device: S_IFCHR = 0o020000
    with pytest.raises(ValueError, match="device|mode|type"):
        _parse_zip(
            _zip_bytes(members, external_attr={f"{root}/link": 0o020666 << 16})
        )


def test_zip_rejects_encrypted_and_unsupported_compression() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    members = {f"{root}/SKILL.md": skill}

    # encrypted flag bit 0 — ZipFile.writestr may clear the bit, so force it on close.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(f"{root}/SKILL.md")
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o100644 << 16
        zf.writestr(info, skill)
        for item in zf.filelist:
            item.flag_bits |= 0x1
    with pytest.raises(ValueError, match="encrypt"):
        _parse_zip(buf.getvalue())

    # unsupported compression (BZIP2 = 12) — craft via ZipInfo
    with pytest.raises(ValueError, match="compress"):
        _parse_zip(
            _zip_bytes(members, compress_type=12)  # type: ignore[arg-type]
        )


def test_zip_entry_count_and_size_limits() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    # entry count
    members = {f"{root}/SKILL.md": skill}
    for i in range(MAX_ENTRIES):
        members[f"{root}/f{i}.txt"] = b"x"
    with pytest.raises(ValueError, match="entr"):
        _parse_zip(_zip_bytes(members))

    # compressed upload limit
    tiny = _zip_bytes({f"{root}/SKILL.md": skill})
    with pytest.raises(ValueError, match="upload|compress|size|413"):
        parse_skill_zip(io.BytesIO(tiny), compressed_size=MAX_ZIP_UPLOAD_BYTES + 1)


def test_zip_total_uncompressed_and_per_file_limits() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    # per-file scripts limit 1 MiB
    big = b"x" * (1 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="size|limit"):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/scripts/big.py": big,
                }
            )
        )

    # assets limit 10 MiB
    big_asset = b"y" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="size|limit"):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/assets/big.bin": big_asset,
                }
            )
        )


def test_zip_total_declared_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declared file_size sum crossing total limit is rejected before streaming."""
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    # Craft a zip where declared sizes sum over the total without needing huge content.
    # We monkeypatch the max down for a fast test when possible; otherwise use metadata.
    from app.assistant.skills import package_io as pio

    monkeypatch.setattr(pio, "MAX_TOTAL_UNCOMPRESSED_BYTES", 100)
    members = {
        f"{root}/SKILL.md": skill,
        f"{root}/a.txt": b"x" * 60,
        f"{root}/b.txt": b"y" * 60,
    }
    with pytest.raises(ValueError, match="total|size|limit"):
        _parse_zip(_zip_bytes(members))


def test_invalid_utf8_skill_and_manifest() -> None:
    root = "weekly-review"
    with pytest.raises(ValueError, match="UTF-8|utf"):
        parse_skill_directory_files(
            {"SKILL.md": b"---\nname: weekly-review\ndescription: \xff\n---\n"}
        )
    with pytest.raises(ValueError, match="UTF-8|utf"):
        parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md(),
                "mindatlas.yaml": b"version: 1\n# \xff\n",
            }
        )


def test_packaging_noise_rejected() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    with pytest.raises(ValueError, match="MACOSX|DS_Store|packaging|noise"):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    f"{root}/.DS_Store": b"noise",
                }
            )
        )
    with pytest.raises(ValueError, match="MACOSX|DS_Store|packaging|noise"):
        _parse_zip(
            _zip_bytes(
                {
                    f"{root}/SKILL.md": skill,
                    "__MACOSX/._SKILL.md": b"noise",
                }
            )
        )


def test_markdown_links_local_and_remote() -> None:
    # broken local link
    skill = _minimal_skill_md(
        body="# t\n\nSee [missing](references/nope.md).\n"
    )
    with pytest.raises(ValueError, match="link"):
        parse_skill_directory_files(_files(skill_md=skill, include_mindatlas=False))

    # URL-decoded traversal
    skill2 = _minimal_skill_md(
        body="# t\n\nSee [x](references/%2e%2e/secret.md).\n"
    )
    with pytest.raises(ValueError, match="link|traversal|path"):
        parse_skill_directory_files(
            _files(
                skill_md=skill2,
                include_mindatlas=False,
                resources={"secret.md": b"no"},
            )
        )

    # remote + same-document anchors allowed
    skill3 = _minimal_skill_md(
        body=(
            "# t\n\n"
            "See [remote](https://example.com/a) and [anchor](#section).\n"
            "Also [guide](references/guide.md).\n"
        )
    )
    pkg = parse_skill_directory_files(
        _files(
            skill_md=skill3,
            include_mindatlas=False,
            resources={"references/guide.md": b"# g\n"},
        )
    )
    assert pkg.canonical_name == "weekly-review"


def test_markdown_links_to_package_root_files() -> None:
    """SKILL.md may link to package-root files when those files are present."""
    skill = _minimal_skill_md(
        body=(
            "# t\n\n"
            "See [manifest](mindatlas.yaml) and [self](SKILL.md).\n"
        )
    )
    pkg = parse_skill_directory_files(
        _files(
            skill_md=skill,
            mindatlas=b"version: 1\n",
            include_mindatlas=True,
        )
    )
    assert pkg.canonical_name == "weekly-review"
    assert pkg.mindatlas_yaml_bytes == b"version: 1\n"

    # Missing root target still fails (no mindatlas.yaml in package).
    skill_missing = _minimal_skill_md(
        body="# t\n\nSee [manifest](mindatlas.yaml).\n"
    )
    with pytest.raises(ValueError, match="link"):
        parse_skill_directory_files(
            _files(skill_md=skill_missing, include_mindatlas=False)
        )


def test_script_mode_bits_discarded_executable_false() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    members = {
        f"{root}/SKILL.md": skill,
        f"{root}/scripts/run.sh": b"#!/bin/sh\necho hi\n",
    }
    data = _zip_bytes(
        members,
        external_attr={
            f"{root}/scripts/run.sh": 0o100755 << 16,
        },
    )
    pkg = _parse_zip(data)
    assert len(pkg.resources) == 1
    assert pkg.resources[0].path == "scripts/run.sh"
    assert pkg.resources[0].resource_kind == "scripts"
    assert pkg.resources[0].executable is False


def test_directory_files_rejects_nested_root_prefix() -> None:
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            {
                "weekly-review/SKILL.md": _minimal_skill_md(),
            }
        )


def test_conflict_rules_structure_and_limit() -> None:
    rules = "\n".join(
        f"  - kind: excludes\n    target_skill: other-skill-{i}" for i in range(51)
    )
    yaml_text = f"version: 1\nrouting:\n  conflict_rules:\n{rules}\n"
    with pytest.raises(ValueError):
        parse_skill_directory_files(
            _files(mindatlas=yaml_text.encode("utf-8"), include_mindatlas=True)
        )

    ok = """
version: 1
routing:
  conflict_rules:
    - kind: excludes
      target_skill: other-skill
    - kind: requires
      target_skill: base-skill
    - kind: exclusive_group
      group: review-family
"""
    pkg = parse_skill_directory_files(
        _files(mindatlas=ok.encode("utf-8"), include_mindatlas=True)
    )
    assert len(pkg.manifest.routing.conflict_rules) == 3
    assert pkg.manifest.routing.conflict_rules[0].kind == "excludes"
    assert pkg.manifest.routing.conflict_rules[2].group == "review-family"
