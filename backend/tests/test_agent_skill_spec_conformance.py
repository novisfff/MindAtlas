"""Project-owned portable Agent Skills conformance vectors (Plan 01 Task 10).

These tests are authoritative for MindAtlas v1. They do not import or fetch
skills-ref; that optional external smoke is non-authoritative and not part of
the default unit path.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.contracts import StoredSkillResource  # noqa: E402
from app.assistant.skills.contracts import (  # noqa: E402
    MAX_COMPATIBILITY_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_FRONTMATTER_METADATA_ENTRIES,
    MAX_SKILL_NAME_LEN,
    AgentSkillFrontmatter,
    validate_canonical_skill_name,
)
from app.assistant.skills.package_io import (  # noqa: E402
    StrictSafeLoader,
    export_skill_package,
    load_strict_yaml,
    parse_skill_directory_files,
    parse_skill_md,
    parse_skill_zip,
)


FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "agent_skills" / "valid-weekly-review"
)


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
    include_mindatlas: bool = False,
) -> dict[str, bytes]:
    out: dict[str, bytes] = {
        "SKILL.md": skill_md if skill_md is not None else _minimal_skill_md(),
    }
    if include_mindatlas:
        out["mindatlas.yaml"] = (
            mindatlas
            if mindatlas is not None
            else b"version: 1\n"
        )
    if resources:
        out.update(resources)
    return out


def _fixture_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(FIXTURE_ROOT.rglob("*")):
        if path.is_file():
            rel = path.relative_to(FIXTURE_ROOT).as_posix()
            files[rel] = path.read_bytes()
    return files


# ---------------------------------------------------------------------------
# Standard frontmatter fields
# ---------------------------------------------------------------------------


def test_standard_frontmatter_fields_round_trip() -> None:
    skill = _minimal_skill_md(
        extra_frontmatter=(
            "license: MIT\n"
            "compatibility: Requires MindAtlas >=1\n"
            "metadata:\n"
            "  author: mindatlas\n"
            "  version: '1'\n"
            "allowed-tools: search_entries get_entry_detail\n"
        )
    )
    fm = parse_skill_md(skill)
    assert isinstance(fm, AgentSkillFrontmatter)
    assert fm.name == "weekly-review"
    assert "weekly summaries" in fm.description
    assert fm.license == "MIT"
    assert fm.compatibility == "Requires MindAtlas >=1"
    assert fm.metadata == {"author": "mindatlas", "version": "1"}
    assert fm.allowed_tools == "search_entries get_entry_detail"

    pkg = parse_skill_directory_files(_files(skill_md=skill))
    assert pkg.frontmatter.license == "MIT"
    assert pkg.frontmatter.allowed_tools == "search_entries get_entry_detail"


def test_unknown_frontmatter_keys_rejected() -> None:
    skill = _minimal_skill_md(extra_frontmatter="routing_priority: high\n")
    with pytest.raises(ValueError, match="frontmatter|extra|unknown|forbidden"):
        parse_skill_md(skill)


def test_allowed_tools_must_be_space_delimited_string_not_list() -> None:
    skill = _minimal_skill_md(
        extra_frontmatter="allowed-tools:\n  - search_entries\n  - get_entry_detail\n"
    )
    with pytest.raises(ValueError, match="allowed-tools|list|string"):
        parse_skill_md(skill)


# ---------------------------------------------------------------------------
# Name / description constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "a",
        "weekly-review",
        "a" + ("b" * (MAX_SKILL_NAME_LEN - 1)),
        "tool42",
        "a-b-c-d",
    ],
)
def test_canonical_name_accepts_standard_shape(name: str) -> None:
    assert validate_canonical_skill_name(name) == name
    parse_skill_md(_minimal_skill_md(name=name))


@pytest.mark.parametrize(
    "name",
    [
        "",
        "-leading",
        "trailing-",
        "double--hyphen",
        "Upper",
        "has_underscore",
        "has space",
        "a" * (MAX_SKILL_NAME_LEN + 1),
        "general_chat",
        "general-chat",
    ],
)
def test_canonical_name_rejects_invalid_or_reserved(name: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        validate_canonical_skill_name(name)


def test_description_bounds() -> None:
    parse_skill_md(_minimal_skill_md(description="x"))
    parse_skill_md(_minimal_skill_md(description="d" * MAX_DESCRIPTION_LEN))
    with pytest.raises(ValueError, match="description"):
        parse_skill_md(_minimal_skill_md(description=""))
    with pytest.raises(ValueError, match="description"):
        parse_skill_md(_minimal_skill_md(description="d" * (MAX_DESCRIPTION_LEN + 1)))


def test_compatibility_and_metadata_bounds() -> None:
    parse_skill_md(
        _minimal_skill_md(
            extra_frontmatter=f"compatibility: {'c' * MAX_COMPATIBILITY_LEN}\n"
        )
    )
    with pytest.raises(ValueError, match="compatibility"):
        parse_skill_md(
            _minimal_skill_md(
                extra_frontmatter=f"compatibility: {'c' * (MAX_COMPATIBILITY_LEN + 1)}\n"
            )
        )

    meta_lines = "\n".join(f"  k{i}: v{i}" for i in range(MAX_FRONTMATTER_METADATA_ENTRIES))
    parse_skill_md(_minimal_skill_md(extra_frontmatter=f"metadata:\n{meta_lines}\n"))
    too_many = "\n".join(
        f"  k{i}: v{i}" for i in range(MAX_FRONTMATTER_METADATA_ENTRIES + 1)
    )
    with pytest.raises(ValueError, match="metadata"):
        parse_skill_md(_minimal_skill_md(extra_frontmatter=f"metadata:\n{too_many}\n"))


# ---------------------------------------------------------------------------
# Optional directories and safe extra files
# ---------------------------------------------------------------------------


def test_optional_standard_directories_and_safe_extra_files() -> None:
    pkg = parse_skill_directory_files(
        _files(
            resources={
                "scripts/run.sh": b"#!/bin/sh\necho hi\n",
                "references/guide.md": b"# guide\n",
                "assets/logo.svg": b"<svg/>\n",
                "notes.txt": b"safe extra\n",
            }
        )
    )
    paths = {r.path for r in pkg.resources}
    assert "scripts/run.sh" in paths
    assert "references/guide.md" in paths
    assert "assets/logo.svg" in paths
    assert "notes.txt" in paths
    # Imported scripts are never executable in MindAtlas v1.
    script = next(r for r in pkg.resources if r.path == "scripts/run.sh")
    assert script.executable is False


def test_name_must_match_package_directory_root() -> None:
    root = "weekly-review"
    skill = _minimal_skill_md(name=root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{root}/SKILL.md", skill)
    raw = buf.getvalue()
    parse_skill_zip(io.BytesIO(raw), compressed_size=len(raw))

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("other-name/SKILL.md", skill)
    bad_raw = bad.getvalue()
    with pytest.raises(ValueError, match="name|root|directory"):
        parse_skill_zip(io.BytesIO(bad_raw), compressed_size=len(bad_raw))


# ---------------------------------------------------------------------------
# Local-link validation
# ---------------------------------------------------------------------------


def test_local_links_require_existing_targets_remote_allowed() -> None:
    with pytest.raises(ValueError, match="link"):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(
                    body="# t\n\nSee [missing](references/nope.md).\n"
                )
            )
        )

    pkg = parse_skill_directory_files(
        _files(
            skill_md=_minimal_skill_md(
                body=(
                    "# t\n\n"
                    "See [remote](https://example.com/doc) and [anchor](#sec).\n"
                    "Also [guide](references/guide.md).\n"
                )
            ),
            resources={"references/guide.md": b"# g\n"},
        )
    )
    assert pkg.canonical_name == "weekly-review"


def test_local_link_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="link|traversal|path"):
        parse_skill_directory_files(
            _files(
                skill_md=_minimal_skill_md(
                    body="# t\n\nSee [x](references/%2e%2e/secret.md).\n"
                ),
                resources={"secret.md": b"no"},
            )
        )


# ---------------------------------------------------------------------------
# Portable round-trip
# ---------------------------------------------------------------------------


def test_fixture_package_round_trip_export_is_deterministic() -> None:
    files = _fixture_files()
    assert "SKILL.md" in files
    parsed = parse_skill_directory_files(files, expected_root_name=None)
    assert parsed.canonical_name == "weekly-review"

    resources = [
        StoredSkillResource(
            path=r.path,
            resource_kind=r.resource_kind,
            media_type=r.media_type,
            byte_size=r.byte_size,
            sha256=r.sha256,
            content=r.content,
        )
        for r in parsed.resources
    ]
    zip_a = export_skill_package(
        parsed.canonical_name,
        skill_md=parsed.skill_md_bytes,
        mindatlas_yaml=parsed.mindatlas_yaml_bytes,
        resources=resources,
    )
    zip_b = export_skill_package(
        parsed.canonical_name,
        skill_md=parsed.skill_md_bytes,
        mindatlas_yaml=parsed.mindatlas_yaml_bytes,
        resources=list(reversed(resources)),
    )
    assert zip_a == zip_b

    reparsed = parse_skill_zip(io.BytesIO(zip_a), compressed_size=len(zip_a))
    assert reparsed.canonical_name == parsed.canonical_name
    assert reparsed.skill_md_bytes == parsed.skill_md_bytes
    assert reparsed.mindatlas_yaml_bytes == parsed.mindatlas_yaml_bytes
    assert {r.path for r in reparsed.resources} == {r.path for r in parsed.resources}


# ---------------------------------------------------------------------------
# Agent binding contract + remote Tool output-contract requirements
# ---------------------------------------------------------------------------


def test_agent_binding_requires_explicit_input_and_output_schemas() -> None:
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

    partial = """
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
"""
    with pytest.raises(ValueError, match="agent|contract|schema|output"):
        parse_skill_directory_files(
            _files(mindatlas=partial.encode("utf-8"), include_mindatlas=True)
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
"""
    pkg = parse_skill_directory_files(
        _files(mindatlas=ok.encode("utf-8"), include_mindatlas=True)
    )
    agent = pkg.manifest.capabilities[0]
    assert agent.type == "agent"
    assert agent.contract is not None
    assert agent.contract.input_schema["type"] == "object"
    assert agent.contract.output_schema["type"] == "object"


def test_remote_tool_output_contract_is_required_at_publish_resolution() -> None:
    """Package parse allows tool without contract; publish-time resolution requires output.

    This vector freezes the Plan 01 contract surface: remote top-level Tool bindings
    must carry an explicit binding-owned output contract before publish.
    """
    import inspect

    from app.assistant.skills import resolution as resolution_mod
    from app.common.exceptions import ApiException

    source = inspect.getsource(resolution_mod)
    assert "remote tool binding requires explicit output_schema" in source
    # Publish-time failures use the reserved 42293 block.
    err = resolution_mod._publish_error("remote tool binding requires explicit output_schema: x")
    assert isinstance(err, ApiException)
    assert err.code == 42293


def test_strict_yaml_loader_never_constructs_custom_tags() -> None:
    with pytest.raises(ValueError, match="tag|YAML"):
        load_strict_yaml(
            "!!python/object/apply:os.system ['echo pwned']\n",
            source_name="strict-yaml-test",
        )
    with pytest.raises(ValueError, match="tag|YAML|binary"):
        load_strict_yaml("!!binary aGVsbG8=\n", source_name="strict-yaml-test")
    assert issubclass(StrictSafeLoader, object)
    # Happy core types still load.
    assert load_strict_yaml("a: 1\nb: true\nc: null\n", source_name="strict-yaml-test") == {
        "a": 1,
        "b": True,
        "c": None,
    }


def test_allowed_tools_never_becomes_capability_authorization() -> None:
    skill = _minimal_skill_md(
        extra_frontmatter="allowed-tools: search_entries create_entry\n"
    )
    mindatlas = b"""
version: 1
capabilities:
  - type: tool
    key: search_entries
"""
    pkg = parse_skill_directory_files(
        _files(skill_md=skill, mindatlas=mindatlas, include_mindatlas=True)
    )
    # Frontmatter preserves interoperability text only.
    assert pkg.frontmatter.allowed_tools == "search_entries create_entry"
    # Capability set comes solely from mindatlas.yaml.
    keys = [c.key for c in pkg.manifest.capabilities]
    assert keys == ["search_entries"]
    assert "create_entry" not in keys
