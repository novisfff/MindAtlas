"""Profile schema V2 is the exclusive production Main Agent Profile shape."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _v1_payload(**overrides: Any) -> dict[str, Any]:
    from app.assistant.skills.schemas import default_main_agent_profile_snapshot

    payload = copy.deepcopy(default_main_agent_profile_snapshot().normalized_payload())
    for key, value in overrides.items():
        payload[key] = value
    return payload


def _v2_payload(**overrides: Any) -> dict[str, Any]:
    from app.assistant.skills.schemas import default_main_agent_profile_snapshot_v2

    payload = copy.deepcopy(
        default_main_agent_profile_snapshot_v2().normalized_payload()
    )
    for key, value in overrides.items():
        payload[key] = value
    return payload


@pytest.fixture
def profile_v1_payload() -> dict[str, Any]:
    return _v1_payload()


@pytest.fixture
def profile_v2_payload() -> dict[str, Any]:
    return _v2_payload()


def test_profile_v2_has_main_agent_only_runtime_policy():
    from app.assistant.skills.schemas import default_main_agent_profile_snapshot_v2

    snapshot = default_main_agent_profile_snapshot_v2()
    assert snapshot.schema_version == 2
    assert snapshot.runtime_policy.runtime_kind == "main_agent"
    assert snapshot.runtime_policy.recovery_scope == "same_run_only"
    assert "fallbackPolicy" not in snapshot.normalized_payload()


def test_profile_v1_is_readable_but_not_publishable(profile_v1_payload):
    from app.assistant.skills.schemas import (
        ProfileSchemaNotPublishable,
        parse_main_agent_profile_snapshot_for_read,
        require_production_profile_v2,
    )

    historical = parse_main_agent_profile_snapshot_for_read(profile_v1_payload)
    assert historical.schema_version == 1
    with pytest.raises(ProfileSchemaNotPublishable) as exc:
        require_production_profile_v2(historical)
    assert exc.value.reason_code == "profile_schema_unsupported"


def test_profile_v2_rejects_legacy_runtime_policy(profile_v2_payload):
    from app.assistant.skills.schemas import MainAgentProfileSnapshotV2

    profile_v2_payload["runtimePolicy"]["runtimeKind"] = "legacy"
    with pytest.raises(ValidationError):
        MainAgentProfileSnapshotV2.model_validate(profile_v2_payload)
