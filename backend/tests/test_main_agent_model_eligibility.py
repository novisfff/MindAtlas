"""Model eligibility preflight tests (Plan 04 Task 8 / §12)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.main_agent.model_eligibility import (  # noqa: E402
    CREDENTIAL_REVISION_DRIFT,
    MODEL_REVISION_DRIFT,
    PROBE_ADAPTER_MISMATCH,
    PROBE_CONFIG_DIGEST_MISMATCH,
    PROBE_CONTRACT_MISMATCH,
    PROBE_DIGEST_MISMATCH,
    PROBE_MISSING,
    PROBE_NOT_CURRENT,
    PROBE_STALE,
    PROBE_STATUS_FAILED,
    PROBE_STATUS_PARTIAL,
    REQUIRED_CAPABILITY_FAILED,
    REQUIRED_CAPABILITY_NOT_OBSERVED,
    CurrentProbeView,
    FrozenModelIdentity,
    ModelEligibilityError,
    evaluate_probe_eligibility,
    recheck_identity_before_decrypt,
    required_capability_keys_for_profile,
)
from app.assistant.provider_loop.probe import (  # noqa: E402
    CapabilityObservation,
    ModelCapabilityObservations,
)
from app.assistant.skills.schemas import ModelRequirementsV1  # noqa: E402


def _obs(status: str = "passed") -> CapabilityObservation:
    return CapabilityObservation(observation=status)  # type: ignore[arg-type]


def _all_passed() -> ModelCapabilityObservations:
    return ModelCapabilityObservations(
        streaming=_obs("passed"),
        tool_calling=_obs("passed"),
        json_schema_args=_obs("passed"),
        stable_tool_call_ids=_obs("passed"),
        multi_tool_calls=_obs("passed"),
        tool_result_continuation=_obs("passed"),
        tools_disabled_finalization=_obs("passed"),
    )


def _view(**overrides) -> CurrentProbeView:
    payload = dict(
        probe_id=UUID("00000000-0000-4000-8000-000000000901"),
        probe_contract_version=1,
        adapter_key="openai_chat_completions",
        adapter_revision="1",
        model_config_digest="a" * 64,
        status="passed",
        capabilities=_all_passed(),
        probe_digest="b" * 64,
        is_current=True,
        is_stale_for_current_config=False,
    )
    payload.update(overrides)
    return CurrentProbeView(**payload)


def test_required_capabilities_include_always_and_multi_when_profile_true() -> None:
    req = ModelRequirementsV1(
        tool_calling=True,
        streaming=True,
        multi_tool_calls=True,
        json_schema=True,
    )
    keys = required_capability_keys_for_profile(req)
    assert "streaming" in keys
    assert "tool_calling" in keys
    assert "json_schema_args" in keys
    assert "stable_tool_call_ids" in keys
    assert "tool_result_continuation" in keys
    assert "tools_disabled_finalization" in keys
    assert "multi_tool_calls" in keys


def test_required_capabilities_omit_multi_when_profile_false() -> None:
    req = ModelRequirementsV1(
        tool_calling=True,
        streaming=True,
        multi_tool_calls=False,
        json_schema=True,
    )
    keys = required_capability_keys_for_profile(req)
    assert "multi_tool_calls" not in keys


def test_missing_probe_ineligible() -> None:
    report = evaluate_probe_eligibility(
        probe=None,
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming", "tool_calling"),
    )
    assert report.eligible is False
    assert report.reason_code == PROBE_MISSING


def test_stale_and_not_current_probe_ineligible() -> None:
    report = evaluate_probe_eligibility(
        probe=_view(is_current=False),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming",),
    )
    assert report.reason_code == PROBE_NOT_CURRENT

    report = evaluate_probe_eligibility(
        probe=_view(is_stale_for_current_config=True),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming",),
    )
    assert report.reason_code == PROBE_STALE


def test_probe_status_failed_and_partial() -> None:
    report = evaluate_probe_eligibility(
        probe=_view(status="failed"),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming",),
    )
    assert report.reason_code == PROBE_STATUS_FAILED

    # All required passed but overall status partial → still fail-closed.
    report = evaluate_probe_eligibility(
        probe=_view(status="partial"),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming", "tool_calling"),
    )
    assert report.reason_code == PROBE_STATUS_PARTIAL


def test_adapter_and_contract_and_config_mismatch() -> None:
    report = evaluate_probe_eligibility(
        probe=_view(adapter_key="other"),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming",),
    )
    assert report.reason_code == PROBE_ADAPTER_MISMATCH

    report = evaluate_probe_eligibility(
        probe=_view(probe_contract_version=99),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming",),
    )
    assert report.reason_code == PROBE_CONTRACT_MISMATCH

    report = evaluate_probe_eligibility(
        probe=_view(model_config_digest="c" * 64),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming",),
    )
    assert report.reason_code == PROBE_CONFIG_DIGEST_MISMATCH


def test_required_capability_failed_and_not_observed() -> None:
    caps = _all_passed().model_copy(
        update={"tool_calling": _obs("failed")}
    )
    report = evaluate_probe_eligibility(
        probe=_view(capabilities=caps),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming", "tool_calling"),
    )
    assert report.reason_code == REQUIRED_CAPABILITY_FAILED
    assert "tool_calling" in report.failed_capabilities

    caps = _all_passed().model_copy(
        update={"stable_tool_call_ids": _obs("not_observed")}
    )
    report = evaluate_probe_eligibility(
        probe=_view(capabilities=caps),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming", "stable_tool_call_ids"),
    )
    assert report.reason_code == REQUIRED_CAPABILITY_NOT_OBSERVED


def test_passed_probe_eligible() -> None:
    report = evaluate_probe_eligibility(
        probe=_view(),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=required_capability_keys_for_profile(
            ModelRequirementsV1(
                tool_calling=True,
                streaming=True,
                multi_tool_calls=True,
                json_schema=True,
            )
        ),
    )
    assert report.eligible is True
    assert report.reason_code is None
    assert report.probe_id is not None


def test_recheck_before_decrypt_detects_drift() -> None:
    frozen = FrozenModelIdentity(
        model_id=uuid4(),
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=uuid4(),
        credential_runtime_revision=1,
        credential_config_digest="d" * 64,
        model_config_digest="a" * 64,
        capability_probe_id=UUID("00000000-0000-4000-8000-000000000901"),
        capability_probe_digest="b" * 64,
    )
    # Happy path
    recheck_identity_before_decrypt(
        frozen=frozen,
        live_model_runtime_revision=1,
        live_credential_runtime_revision=1,
        live_model_config_digest="a" * 64,
        live_credential_config_digest="d" * 64,
        live_probe_id=frozen.capability_probe_id,
        live_probe_digest="b" * 64,
    )
    with pytest.raises(ModelEligibilityError) as exc:
        recheck_identity_before_decrypt(
            frozen=frozen,
            live_model_runtime_revision=2,
            live_credential_runtime_revision=1,
            live_model_config_digest="a" * 64,
            live_credential_config_digest="d" * 64,
            live_probe_id=frozen.capability_probe_id,
            live_probe_digest="b" * 64,
        )
    assert exc.value.reason_code == MODEL_REVISION_DRIFT

    with pytest.raises(ModelEligibilityError) as exc:
        recheck_identity_before_decrypt(
            frozen=frozen,
            live_model_runtime_revision=1,
            live_credential_runtime_revision=9,
            live_model_config_digest="a" * 64,
            live_credential_config_digest="d" * 64,
            live_probe_id=frozen.capability_probe_id,
            live_probe_digest="b" * 64,
        )
    assert exc.value.reason_code == CREDENTIAL_REVISION_DRIFT

    with pytest.raises(ModelEligibilityError) as exc:
        recheck_identity_before_decrypt(
            frozen=frozen,
            live_model_runtime_revision=1,
            live_credential_runtime_revision=1,
            live_model_config_digest="a" * 64,
            live_credential_config_digest="d" * 64,
            live_probe_id=frozen.capability_probe_id,
            live_probe_digest="e" * 64,
        )
    assert exc.value.reason_code == PROBE_DIGEST_MISMATCH


def test_mapping_capabilities_shape_supported() -> None:
    caps = {
        "streaming": {"observation": "passed"},
        "tool_calling": {"observation": "passed"},
        "json_schema_args": {"observation": "passed"},
        "stable_tool_call_ids": {"observation": "passed"},
        "multi_tool_calls": {"observation": "passed"},
        "tool_result_continuation": {"observation": "passed"},
        "tools_disabled_finalization": {"observation": "passed"},
    }
    report = evaluate_probe_eligibility(
        probe=_view(capabilities=caps),
        expected_adapter_key="openai_chat_completions",
        expected_adapter_revision="1",
        expected_model_config_digest="a" * 64,
        required_capabilities=("streaming", "tool_calling"),
    )
    assert report.eligible is True
