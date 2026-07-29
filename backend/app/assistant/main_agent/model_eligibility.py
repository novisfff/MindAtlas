"""Model eligibility for Main Agent admission (Plan 04 Task 8 / §12).

Preflight uses the model's *current* probe pointer only. Decrypt / adapter
construction happens only after eligibility passes, and must recheck revision
and config digests immediately before construction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.assistant.domain.contracts import FrozenContract, ModelRef, ProviderRef
from app.assistant.provider_loop.probe import (
    PROBE_CONTRACT_VERSION,
    REQUIRED_CAPABILITY_KEYS,
    ModelCapabilityObservations,
    ModelCapabilityProbeEvidence,
    ProbeObservation,
    ProbeStatus,
)
from app.assistant.skills.schemas import ModelRequirementsV1

# ---------------------------------------------------------------------------
# Safe reason codes (stable; never include secrets / exception text)
# ---------------------------------------------------------------------------

MODEL_BINDING_MISSING = "model_binding_missing"
MODEL_TYPE_UNSUPPORTED = "model_type_unsupported"
PROBE_MISSING = "probe_missing"
PROBE_NOT_CURRENT = "probe_not_current"
PROBE_STALE = "probe_stale"
PROBE_STATUS_FAILED = "probe_status_failed"
PROBE_STATUS_PARTIAL = "probe_status_partial"
PROBE_CONTRACT_MISMATCH = "probe_contract_mismatch"
PROBE_ADAPTER_MISMATCH = "probe_adapter_mismatch"
PROBE_CONFIG_DIGEST_MISMATCH = "probe_config_digest_mismatch"
PROBE_DIGEST_MISMATCH = "probe_digest_mismatch"
REQUIRED_CAPABILITY_FAILED = "required_capability_failed"
REQUIRED_CAPABILITY_NOT_OBSERVED = "required_capability_not_observed"
MODEL_REVISION_DRIFT = "model_revision_drift"
CREDENTIAL_REVISION_DRIFT = "credential_revision_drift"
ADAPTER_UNAVAILABLE = "adapter_unavailable"
MODEL_INELIGIBLE = "model_ineligible"

# Profile requirement flag → probe capability key.
_REQUIREMENT_TO_CAPABILITY: dict[str, str] = {
    "streaming": "streaming",
    "tool_calling": "tool_calling",
    "json_schema": "json_schema_args",
    "multi_tool_calls": "multi_tool_calls",
}

# Always required for Main Agent even if Profile does not restate them.
_ALWAYS_REQUIRED_CAPS: tuple[str, ...] = (
    "streaming",
    "tool_calling",
    "json_schema_args",
    "stable_tool_call_ids",
    "tool_result_continuation",
    "tools_disabled_finalization",
)


class ModelEligibilityError(ValueError):
    """Admission/eligibility failure with a safe stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class FrozenModelIdentity(FrozenContract):
    """Exact model/credential identity frozen at admission (no secrets).

    Probe fields are optional diagnostics. Fresh bootstrap / deterministic
    readiness freezes both as ``None``; decrypt-time recheck only compares
    probe identity when the frozen identity carries them.
    """

    model_id: UUID
    model_name: str
    model_type: Literal["llm", "embedding"]
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    credential_config_digest: str
    model_config_digest: str
    provider_ref_digest: str | None = None
    capability_probe_id: UUID | None = None
    capability_probe_digest: str | None = None


class ModelEligibilityReport(FrozenContract):
    """Safe eligibility result used by admission and tests."""

    eligible: bool
    reason_code: str | None = None
    required_capabilities: tuple[str, ...] = ()
    failed_capabilities: tuple[str, ...] = ()
    not_observed_capabilities: tuple[str, ...] = ()
    probe_status: ProbeStatus | None = None
    probe_id: UUID | None = None
    probe_digest: str | None = None
    model_config_digest: str | None = None


@dataclass(frozen=True)
class CurrentProbeView:
    """Secret-free view of the current probe pointer for eligibility checks."""

    probe_id: UUID
    probe_contract_version: int
    adapter_key: str
    adapter_revision: str
    model_config_digest: str
    status: ProbeStatus
    capabilities: ModelCapabilityObservations | Mapping[str, Any]
    probe_digest: str
    is_current: bool
    is_stale_for_current_config: bool


def required_capability_keys_for_profile(
    requirements: ModelRequirementsV1 | None,
    *,
    require_multi_tool_when_profile_true: bool = True,
) -> tuple[str, ...]:
    """Return ordered capability keys that must be `passed` for admission."""
    required = list(_ALWAYS_REQUIRED_CAPS)
    if requirements is not None and bool(requirements.multi_tool_calls):
        if require_multi_tool_when_profile_true and "multi_tool_calls" not in required:
            required.append("multi_tool_calls")
    # Preserve deterministic order matching REQUIRED_CAPABILITY_KEYS then extras.
    ordered = [key for key in REQUIRED_CAPABILITY_KEYS if key in set(required)]
    for key in required:
        if key not in ordered:
            ordered.append(key)
    return tuple(ordered)


def _observation_of(
    capabilities: ModelCapabilityObservations | Mapping[str, Any],
    key: str,
) -> ProbeObservation | None:
    if isinstance(capabilities, ModelCapabilityObservations):
        item = getattr(capabilities, key, None)
        if item is None:
            return None
        return item.observation  # type: ignore[no-any-return]
    raw = capabilities.get(key)
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        obs = raw.get("observation")
        if isinstance(obs, str):
            return obs  # type: ignore[return-value]
        return None
    if hasattr(raw, "observation"):
        return getattr(raw, "observation")  # type: ignore[no-any-return]
    return None


def evaluate_probe_eligibility(
    *,
    probe: CurrentProbeView | None,
    expected_adapter_key: str,
    expected_adapter_revision: str,
    expected_model_config_digest: str,
    expected_probe_contract_version: int = PROBE_CONTRACT_VERSION,
    required_capabilities: Sequence[str],
) -> ModelEligibilityReport:
    """Evaluate current probe evidence without decrypting credentials."""
    required = tuple(required_capabilities)
    if probe is None:
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_MISSING,
            required_capabilities=required,
        )
    if not probe.is_current:
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_NOT_CURRENT,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if probe.is_stale_for_current_config:
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_STALE,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if int(probe.probe_contract_version) != int(expected_probe_contract_version):
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_CONTRACT_MISMATCH,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if (
        str(probe.adapter_key) != str(expected_adapter_key)
        or str(probe.adapter_revision) != str(expected_adapter_revision)
    ):
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_ADAPTER_MISMATCH,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if str(probe.model_config_digest) != str(expected_model_config_digest):
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_CONFIG_DIGEST_MISMATCH,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if probe.status == "failed":
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_STATUS_FAILED,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if probe.status == "partial":
        # Partial may still pass if every *required* capability is passed; Plan §12
        # says partial/failed/not_observed for required features are ineligible.
        # Keep status partial as a soft signal but still evaluate required caps.
        pass
    if probe.status not in {"passed", "partial"}:
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_STATUS_FAILED,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )

    failed: list[str] = []
    not_observed: list[str] = []
    for key in required:
        obs = _observation_of(probe.capabilities, key)
        if obs == "passed":
            continue
        if obs == "failed":
            failed.append(key)
        else:
            # missing or not_observed
            not_observed.append(key)

    if failed:
        return ModelEligibilityReport(
            eligible=False,
            reason_code=REQUIRED_CAPABILITY_FAILED,
            required_capabilities=required,
            failed_capabilities=tuple(failed),
            not_observed_capabilities=tuple(not_observed),
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    if not_observed:
        return ModelEligibilityReport(
            eligible=False,
            reason_code=REQUIRED_CAPABILITY_NOT_OBSERVED,
            required_capabilities=required,
            failed_capabilities=(),
            not_observed_capabilities=tuple(not_observed),
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    # Require overall status passed when all required caps pass (partial with all
    # required passed is still fail-closed for production Main Agent admission).
    if probe.status != "passed":
        return ModelEligibilityReport(
            eligible=False,
            reason_code=PROBE_STATUS_PARTIAL,
            required_capabilities=required,
            probe_status=probe.status,
            probe_id=probe.probe_id,
            probe_digest=probe.probe_digest,
            model_config_digest=probe.model_config_digest,
        )
    return ModelEligibilityReport(
        eligible=True,
        reason_code=None,
        required_capabilities=required,
        probe_status=probe.status,
        probe_id=probe.probe_id,
        probe_digest=probe.probe_digest,
        model_config_digest=probe.model_config_digest,
    )


def recheck_identity_before_decrypt(
    *,
    frozen: FrozenModelIdentity,
    live_model_runtime_revision: int,
    live_credential_runtime_revision: int,
    live_model_config_digest: str,
    live_credential_config_digest: str,
    live_probe_id: UUID | None,
    live_probe_digest: str | None,
) -> None:
    """Fail closed if model/credential (and optional probe) drifted after freeze.

    Always compares model revision, credential revision, model config digest,
    and credential config digest. Probe identity is compared only when the
    frozen identity contains an optional diagnostic probe.
    """
    if int(live_model_runtime_revision) != int(frozen.model_runtime_revision):
        raise ModelEligibilityError(MODEL_REVISION_DRIFT)
    if int(live_credential_runtime_revision) != int(frozen.credential_runtime_revision):
        raise ModelEligibilityError(CREDENTIAL_REVISION_DRIFT)
    if str(live_model_config_digest) != str(frozen.model_config_digest):
        raise ModelEligibilityError(PROBE_CONFIG_DIGEST_MISMATCH)
    if str(live_credential_config_digest) != str(frozen.credential_config_digest):
        raise ModelEligibilityError(CREDENTIAL_REVISION_DRIFT)
    if frozen.capability_probe_id is None and frozen.capability_probe_digest is None:
        return
    if live_probe_id is None or live_probe_id != frozen.capability_probe_id:
        raise ModelEligibilityError(PROBE_NOT_CURRENT)
    if live_probe_digest is None or str(live_probe_digest) != str(
        frozen.capability_probe_digest
    ):
        raise ModelEligibilityError(PROBE_DIGEST_MISMATCH)


def build_model_and_provider_refs(
    *,
    frozen: FrozenModelIdentity,
    provider: ProviderRef,
) -> tuple[ProviderRef, ModelRef]:
    """Construct exact ProviderRef/ModelRef for the base Manifest."""
    from app.assistant.domain.contracts import create_model_ref

    model_ref = create_model_ref(
        model_id=frozen.model_id,
        model_name=frozen.model_name,
        model_type=frozen.model_type,
        model_runtime_revision=frozen.model_runtime_revision,
        credential_id=frozen.credential_id,
        credential_runtime_revision=frozen.credential_runtime_revision,
        credential_config_digest=frozen.credential_config_digest,
        model_config_digest=frozen.model_config_digest,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=frozen.capability_probe_id,
        capability_probe_digest=frozen.capability_probe_digest,
    )
    return provider, model_ref


def probe_view_from_evidence(
    *,
    probe_id: UUID,
    evidence: ModelCapabilityProbeEvidence,
    is_current: bool,
    is_stale_for_current_config: bool,
) -> CurrentProbeView:
    return CurrentProbeView(
        probe_id=probe_id,
        probe_contract_version=int(evidence.probe_contract_version),
        adapter_key=evidence.adapter_key,
        adapter_revision=evidence.adapter_revision,
        model_config_digest=evidence.model_config_digest,
        status=evidence.status,
        capabilities=evidence.capabilities,
        probe_digest=evidence.probe_digest,
        is_current=is_current,
        is_stale_for_current_config=is_stale_for_current_config,
    )


def probe_view_from_row(
    *,
    probe_id: UUID,
    probe_contract_version: int,
    adapter_key: str,
    adapter_revision: str,
    model_config_digest: str,
    status: str,
    capabilities: Mapping[str, Any] | ModelCapabilityObservations,
    probe_digest: str,
    is_current: bool,
    is_stale_for_current_config: bool,
) -> CurrentProbeView:
    return CurrentProbeView(
        probe_id=probe_id,
        probe_contract_version=int(probe_contract_version),
        adapter_key=str(adapter_key),
        adapter_revision=str(adapter_revision),
        model_config_digest=str(model_config_digest),
        status=status,  # type: ignore[arg-type]
        capabilities=capabilities,
        probe_digest=str(probe_digest),
        is_current=bool(is_current),
        is_stale_for_current_config=bool(is_stale_for_current_config),
    )


__all__ = [
    "ADAPTER_UNAVAILABLE",
    "CREDENTIAL_REVISION_DRIFT",
    "CurrentProbeView",
    "FrozenModelIdentity",
    "MODEL_BINDING_MISSING",
    "MODEL_INELIGIBLE",
    "MODEL_REVISION_DRIFT",
    "MODEL_TYPE_UNSUPPORTED",
    "ModelEligibilityError",
    "ModelEligibilityReport",
    "PROBE_ADAPTER_MISMATCH",
    "PROBE_CONFIG_DIGEST_MISMATCH",
    "PROBE_CONTRACT_MISMATCH",
    "PROBE_DIGEST_MISMATCH",
    "PROBE_MISSING",
    "PROBE_NOT_CURRENT",
    "PROBE_STALE",
    "PROBE_STATUS_FAILED",
    "PROBE_STATUS_PARTIAL",
    "REQUIRED_CAPABILITY_FAILED",
    "REQUIRED_CAPABILITY_NOT_OBSERVED",
    "build_model_and_provider_refs",
    "evaluate_probe_eligibility",
    "probe_view_from_evidence",
    "probe_view_from_row",
    "recheck_identity_before_decrypt",
    "required_capability_keys_for_profile",
]
