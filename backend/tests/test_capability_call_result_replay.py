"""Durable CapabilityCall result codec and replay guards."""

from __future__ import annotations

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _provider_result():
    from tests.test_agent_policy_runtime import _base_manifest
    from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result
    from app.assistant.provider_loop.contracts import ProviderDispatchResult

    manifest, _ = _base_manifest()
    return ProviderDispatchResult(
        capability_result=completed_result(
            structured_output={"value": 7},
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=2, output_bytes=3),
        ),
        next_manifest=manifest,
    )


def test_result_round_trip_and_identity_guards() -> None:
    from app.assistant.capability_calls.result_codec import (
        CapabilityResultCodecError,
        decode_capability_result,
        encode_capability_result,
    )

    encoded = encode_capability_result(
        call_id="call-1",
        binding_contract_digest="a" * 64,
        descriptor_digest="b" * 64,
        result=_provider_result(),
    )
    replay = decode_capability_result(
        encoded.payload,
        expected_digest=encoded.digest,
        expected_call_id="call-1",
        expected_binding_contract_digest="a" * 64,
        expected_descriptor_digest="b" * 64,
    )
    assert replay == _provider_result()
    with pytest.raises(CapabilityResultCodecError, match="call mismatch"):
        decode_capability_result(
            encoded.payload,
            expected_digest=encoded.digest,
            expected_call_id="call-2",
            expected_binding_contract_digest="a" * 64,
            expected_descriptor_digest="b" * 64,
        )


def test_result_corruption_fails_closed() -> None:
    from app.assistant.capability_calls.result_codec import (
        CapabilityResultCodecError,
        decode_capability_result,
        encode_capability_result,
    )

    encoded = encode_capability_result(
        call_id="call-1",
        binding_contract_digest="a" * 64,
        descriptor_digest="b" * 64,
        result=_provider_result(),
    )
    with pytest.raises(CapabilityResultCodecError, match="digest mismatch"):
        decode_capability_result(
            encoded.payload + b"x",
            expected_digest=encoded.digest,
            expected_call_id="call-1",
            expected_binding_contract_digest="a" * 64,
            expected_descriptor_digest="b" * 64,
        )


def test_durable_aggregate_replays_success_without_redispatch() -> None:
    from types import SimpleNamespace
    import uuid

    from tests._db import make_session
    from tests.test_capability_call_repository import _make_main_agent_run
    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.durable.models import AssistantRunManifestRevision
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.policy.contracts import build_authorization_decision_v2

    db = make_session()
    try:
        run_id = uuid.uuid4()
        run = _make_main_agent_run(
            db,
            id=run_id,
            status="running",
            state_revision=3,
            capability_ledger_mode="enforced",
            lease_owner="worker-1",
            lease_generation=1,
        )
        db.add(
            AssistantRunManifestRevision(
                run_id=run.id,
                revision=1,
                manifest_digest="a" * 64,
                schema_version=1,
                payload={},
            )
        )
        db.commit()
        decision = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="dispatch",
            reason_code="allowed",
            principal_digest="a" * 64,
            entrypoint_policy_digest="a" * 64,
            global_policy_digest="a" * 64,
            owner_policy_digest="a" * 64,
            allowed_side_effects=("none", "compute", "read"),
            grant_source_digest="b" * 64,
            exposure_digest="a" * 64,
            effective_policy_digest="a" * 64,
        )
        factory = SimpleNamespace(
            decision_for_call=lambda **_kwargs: decision,
        )
        request = SimpleNamespace(
            execution_scope=SimpleNamespace(run_id=run.id),
            call=SimpleNamespace(
                call_id="durable-read-1",
                domain_key="search_entries",
                arguments={"q": "x"},
            ),
            current_manifest=SimpleNamespace(manifest_digest="a" * 64),
            binding=SimpleNamespace(
                ref=SimpleNamespace(
                    binding_contract_digest="c" * 64,
                    resolution_digest="d" * 64,
                )
            ),
            descriptor=SimpleNamespace(
                behavior=SimpleNamespace(side_effect="read"),
                capability_type="tool",
                target_id=None,
                target_version_id=None,
                descriptor_digest="e" * 64,
            ),
            authorization=SimpleNamespace(
                owner=SimpleNamespace(
                    owner_kind="main_agent",
                    owner_id="general_chat",
                    owner_version_id=None,
                )
            ),
        )
        aggregate = DurableCapabilityLedgerAggregate(
            db=db,
            authorization_factory=factory,
            idempotency_secret="s" * 32,
            lease=LeaseToken(
                run_id=run.id, worker_id="worker-1", lease_generation=1
            ),
        )
        prepared = aggregate.prepare(request)
        assert prepared.kind == "dispatch"
        result = _provider_result()
        aggregate.commit_result(prepared, result)
        replay = aggregate.prepare(request)
        assert replay.kind == "replay"
        assert replay.provider_result == result
    finally:
        db.close()
