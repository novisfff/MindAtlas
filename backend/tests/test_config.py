"""Settings validation for Plan 04 main-agent feature mode and hard ceilings."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


@contextmanager
def _cleared_main_agent_env():
    """Temporarily remove ASSISTANT_MAIN_AGENT_* env so defaults are exercised."""
    keys = [
        k
        for k in os.environ
        if k.startswith("ASSISTANT_MAIN_AGENT_")
        or k.startswith("ASSISTANT_CAPABILITY_")
    ]
    saved = {k: os.environ.pop(k) for k in keys}
    try:
        yield
    finally:
        os.environ.update(saved)


def _settings(**overrides):
    from app.config import Settings

    # Avoid reading local .env values that may set main-agent knobs.
    with _cleared_main_agent_env():
        return Settings(**overrides)


def test_main_agent_defaults_are_production_safe() -> None:
    s = _settings()
    assert s.assistant_main_agent_mode == "off"
    assert s.assistant_main_agent_catalog_top_k == 8
    assert s.assistant_main_agent_max_active_skills == 4
    assert s.assistant_main_agent_resource_chunk_bytes == 16384
    assert s.assistant_main_agent_resource_max_bytes_per_call == 65536
    assert s.assistant_main_agent_artifact_max_bytes == 1048576
    assert s.assistant_main_agent_artifact_run_max_bytes == 5242880
    assert s.assistant_main_agent_inline_result_bytes == 16384
    assert s.assistant_capability_reconciliation_enabled is False
    assert s.assistant_capability_reconciliation_operator_id is None
    assert s.assistant_capability_reconciliation_evidence_secret == ""


def test_reconciliation_cli_requires_configured_operator_when_enabled() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True)
    assert "reconciliation_operator_id" in str(exc_info.value).lower()

    operator_id = uuid.uuid4()
    settings = _settings(
        ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
        ASSISTANT_CAPABILITY_RECONCILIATION_OPERATOR_ID=str(operator_id),
        ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET="e" * 32,
    )
    assert settings.assistant_capability_reconciliation_enabled is True
    assert settings.assistant_capability_reconciliation_operator_id == operator_id


def test_golden_write_requires_usable_reconciliation_operator_path() -> None:
    common = {
        "ASSISTANT_MAIN_AGENT_WRITE_MODE": "golden",
        "ASSISTANT_CAPABILITY_LEDGER_MODE": "enforced",
        "ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET": "i" * 32,
    }
    with pytest.raises(ValidationError, match="reconciliation"):
        _settings(**common)

    operator_id = uuid.uuid4()
    settings = _settings(
        **common,
        ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
        ASSISTANT_CAPABILITY_RECONCILIATION_OPERATOR_ID=str(operator_id),
        ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET="e" * 32,
    )
    assert settings.assistant_main_agent_write_mode == "golden"
    assert settings.assistant_capability_reconciliation_enabled is True


@pytest.mark.parametrize("mode", ["off", "shadow", "read_only"])
def test_main_agent_mode_accepts_literal_values(mode: str) -> None:
    s = _settings(ASSISTANT_MAIN_AGENT_MODE=mode)
    assert s.assistant_main_agent_mode == mode


@pytest.mark.parametrize("mode", ["write", "on", "READ_ONLY", "", "legacy", "true"])
def test_main_agent_mode_rejects_invalid(mode: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(ASSISTANT_MAIN_AGENT_MODE=mode)
    text = str(exc_info.value)
    assert "assistant_main_agent_mode" in text.lower() or "ASSISTANT_MAIN_AGENT_MODE" in text
    assert "sk-" not in text
    assert "FERNET" not in text


def test_main_agent_numeric_ranges_accept_bounds() -> None:
    s = _settings(
        ASSISTANT_MAIN_AGENT_CATALOG_TOP_K=1,
        ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS=1,
        ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES=1024,
        ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL=1024,
        ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES=1024,
        ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES=1024,
        ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES=256,
    )
    assert s.assistant_main_agent_catalog_top_k == 1
    assert s.assistant_main_agent_max_active_skills == 1
    assert s.assistant_main_agent_resource_chunk_bytes == 1024
    assert s.assistant_main_agent_resource_max_bytes_per_call == 1024
    assert s.assistant_main_agent_artifact_max_bytes == 1024
    assert s.assistant_main_agent_artifact_run_max_bytes == 1024
    assert s.assistant_main_agent_inline_result_bytes == 256

    s2 = _settings(
        ASSISTANT_MAIN_AGENT_CATALOG_TOP_K=32,
        ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS=8,
        ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES=65536,
        ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL=262144,
        ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES=1048576,
        ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES=10485760,
        ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES=65536,
    )
    assert s2.assistant_main_agent_catalog_top_k == 32
    assert s2.assistant_main_agent_max_active_skills == 8
    assert s2.assistant_main_agent_resource_chunk_bytes == 65536
    assert s2.assistant_main_agent_resource_max_bytes_per_call == 262144
    assert s2.assistant_main_agent_artifact_max_bytes == 1048576
    assert s2.assistant_main_agent_artifact_run_max_bytes == 10485760
    assert s2.assistant_main_agent_inline_result_bytes == 65536


@pytest.mark.parametrize(
    "field,value",
    [
        ("ASSISTANT_MAIN_AGENT_CATALOG_TOP_K", 0),
        ("ASSISTANT_MAIN_AGENT_CATALOG_TOP_K", 33),
        ("ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS", 0),
        ("ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS", 9),
        ("ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES", 1023),
        ("ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES", 65537),
        ("ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL", 1023),
        ("ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL", 262145),
        ("ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES", 1023),
        ("ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES", 1048577),
        ("ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES", 1023),
        ("ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES", 10485761),
        ("ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES", 255),
        ("ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES", 65537),
    ],
)
def test_main_agent_numeric_ranges_reject_out_of_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(**{field: value})
    text = str(exc_info.value)
    # Safe field name present; no secret material.
    assert field.lower().replace("assistant_main_agent_", "assistant_main_agent_") in text.lower() or any(
        part in text.lower()
        for part in (
            "catalog_top_k",
            "max_active_skills",
            "resource_chunk_bytes",
            "resource_max_bytes_per_call",
            "artifact_max_bytes",
            "artifact_run_max_bytes",
            "inline_result_bytes",
        )
    )
    assert "sk-" not in text
    assert "password" not in text.lower()


def test_main_agent_hard_ceilings_cannot_be_raised() -> None:
    """Settings may only lower ceilings; hard max constants are absolute."""
    from app import config as config_mod

    assert config_mod.ASSISTANT_MAIN_AGENT_CATALOG_TOP_K_HARD_MAX == 32
    assert config_mod.ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS_HARD_MAX == 8
    assert config_mod.ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES_HARD_MAX == 65536
    assert config_mod.ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL_HARD_MAX == 262144
    assert config_mod.ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES_HARD_MAX == 1 << 20
    assert config_mod.ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX == 10 << 20
    assert config_mod.ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES_HARD_MAX == 65536

    with pytest.raises(ValidationError) as exc_info:
        _settings(ASSISTANT_MAIN_AGENT_CATALOG_TOP_K=100)
    assert "catalog_top_k" in str(exc_info.value).lower()


def test_main_agent_cross_field_resource_and_artifact_bounds() -> None:
    # resource max must be >= chunk
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES=32768,
            ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL=16384,
        )
    text = str(exc_info.value).lower()
    assert "resource" in text

    # artifact run max must be >= artifact max
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES=2_000_000,
            ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES=1_000_000,
        )
    text = str(exc_info.value).lower()
    assert "artifact" in text


def test_main_agent_rejects_non_integer_numeric() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(ASSISTANT_MAIN_AGENT_CATALOG_TOP_K="eight")
    text = str(exc_info.value)
    assert "catalog_top_k" in text.lower() or "ASSISTANT_MAIN_AGENT_CATALOG_TOP_K" in text
    assert "sk-" not in text
