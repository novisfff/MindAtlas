from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.durable.codec import (  # noqa: E402
    CURRENT_CHECKPOINT_CODEC_VERSION,
    SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS,
)
from app.assistant.runtime.seed import (  # noqa: E402
    MANIFEST_PATH,
    SEED_CONTRACT_DIGEST,
    SEED_MANIFEST_DIGEST,
    load_verified_assistant_system_seed,
)


def test_current_checkpoint_codec_version_is_release_locked() -> None:
    assert CURRENT_CHECKPOINT_CODEC_VERSION == 3
    assert CURRENT_CHECKPOINT_CODEC_VERSION in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS


def test_embedded_seed_verifies_every_artifact() -> None:
    seed = load_verified_assistant_system_seed()
    assert seed.manifest.schema_version == 1
    assert seed.profile.schema_version == 2
    assert seed.profile.control_capability_keys == (
        "skill.search",
        "skill.inject",
        "skill.read_resource",
        "artifact.read",
    )
    assert tuple(binding.key for binding in seed.capability_bindings) == (
        "create_entry",
        "get_entry_detail",
        "search_entries",
    )
    assert seed.manifest.manifest_digest == SEED_MANIFEST_DIGEST
    assert seed.manifest.seed_contract_digest == SEED_CONTRACT_DIGEST
    assert seed.parsed_skill.canonical_name == "mindatlas-universal"
    assert len(SEED_MANIFEST_DIGEST) == 64
    assert len(SEED_CONTRACT_DIGEST) == 64
    assert SEED_MANIFEST_DIGEST != "0" * 64
    assert SEED_CONTRACT_DIGEST != "0" * 64
    assert SEED_MANIFEST_DIGEST == SEED_MANIFEST_DIGEST.lower()
    assert SEED_CONTRACT_DIGEST == SEED_CONTRACT_DIGEST.lower()


def test_manifest_digest_excludes_only_its_own_field() -> None:
    payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    claimed = payload.pop("manifestDigest")
    assert sha256_canonical_json(payload) == claimed


def test_seed_contains_no_unsupported_write() -> None:
    seed = load_verified_assistant_system_seed()
    keys = {item.key for item in seed.capability_bindings}
    assert "update_entry" not in keys
    assert "merge_entry" not in keys
    assert "create_relation" not in keys
    assert "relation_followup" not in keys
    skill_keys = {cap.key for cap in seed.parsed_skill.manifest.capabilities}
    assert "update_entry" not in skill_keys
    assert "merge_entry" not in skill_keys
    assert "create_relation" not in skill_keys


def test_loader_has_no_path_url_or_env_override() -> None:
    signature = inspect.signature(load_verified_assistant_system_seed)
    assert list(signature.parameters) == []
    source = inspect.getsource(load_verified_assistant_system_seed)
    # Fail closed: loader must not accept runtime override knobs.
    for forbidden in (
        "os.getenv",
        "os.environ",
        "seed_root=",
        "seed_path=",
        "http://",
        "https://",
        "Path(os",
        "getenv(",
    ):
        assert forbidden not in source


def test_expected_module_digests_are_real_lowercase_hex() -> None:
    from app.assistant.runtime.system_seed.expected import (
        SEED_CONTRACT_DIGEST as expected_contract,
        SEED_MANIFEST_DIGEST as expected_manifest,
    )

    assert expected_manifest == SEED_MANIFEST_DIGEST
    assert expected_contract == SEED_CONTRACT_DIGEST
    for value in (expected_manifest, expected_contract):
        assert isinstance(value, str)
        assert len(value) == 64
        assert value == value.lower()
        int(value, 16)
        assert value != "0" * 64
        assert len(set(value)) > 1
