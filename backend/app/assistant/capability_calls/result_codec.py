"""Bounded canonical codec for durable CapabilityCall replay results."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.assistant.domain.digests import canonical_json_bytes, sha256_bytes
from app.assistant.provider_loop.contracts import ProviderDispatchResult

MAX_INLINE_RESULT_BYTES = 262_144


class CapabilityResultCodecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EncodedCapabilityResult:
    payload: bytes
    digest: str


def encode_capability_result(
    *,
    call_id: str,
    binding_contract_digest: str,
    descriptor_digest: str,
    result: ProviderDispatchResult,
) -> EncodedCapabilityResult:
    envelope = {
        "contractVersion": 1,
        "callId": call_id,
        "bindingContractDigest": binding_contract_digest,
        "descriptorDigest": descriptor_digest,
        "result": result.model_dump(mode="json"),
    }
    payload = canonical_json_bytes(envelope)  # type: ignore[arg-type]
    if len(payload) > MAX_INLINE_RESULT_BYTES:
        raise CapabilityResultCodecError("capability result exceeds inline limit")
    return EncodedCapabilityResult(payload=payload, digest=sha256_bytes(payload))


def decode_capability_result(
    payload: bytes,
    *,
    expected_digest: str,
    expected_call_id: str,
    expected_binding_contract_digest: str,
    expected_descriptor_digest: str,
) -> ProviderDispatchResult:
    if len(payload) > MAX_INLINE_RESULT_BYTES:
        raise CapabilityResultCodecError("capability result exceeds inline limit")
    if sha256_bytes(payload) != expected_digest:
        raise CapabilityResultCodecError("capability result digest mismatch")
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityResultCodecError("capability result is not canonical JSON") from exc
    if envelope.get("contractVersion") != 1:
        raise CapabilityResultCodecError("unsupported capability result version")
    if envelope.get("callId") != expected_call_id:
        raise CapabilityResultCodecError("capability result call mismatch")
    if envelope.get("bindingContractDigest") != expected_binding_contract_digest:
        raise CapabilityResultCodecError("capability result binding mismatch")
    if envelope.get("descriptorDigest") != expected_descriptor_digest:
        raise CapabilityResultCodecError("capability result descriptor mismatch")
    try:
        return ProviderDispatchResult.model_validate(envelope["result"])
    except Exception as exc:
        raise CapabilityResultCodecError("capability result contract invalid") from exc


__all__ = [
    "CapabilityResultCodecError",
    "EncodedCapabilityResult",
    "MAX_INLINE_RESULT_BYTES",
    "decode_capability_result",
    "encode_capability_result",
]
