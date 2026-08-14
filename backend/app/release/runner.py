"""Server-owned release evidence finalization primitives.

The runner owns observation and assertion derivation.  Callers can provide
typed observations to this module, but cannot submit a passed flag or an
arbitrary assertion result through the public CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from app.assistant.domain.digests import sha256_canonical_json
from app.release.contracts import (
    ReleaseArtifactRefV1,
    ReleaseAssertionResultV1,
    ReleaseEvidenceManifestV1,
)
from app.release.evidence import ContentAddressedEvidenceStore
from app.release.trust import ReleaseEvidenceSigner


class ReleaseRunnerError(RuntimeError):
    safe_code = "release_runner_invalid"


@dataclass(frozen=True)
class ReleaseObservation:
    assertion_id: str
    passed: bool
    safe_failure_code: str | None
    payload: dict[str, Any]
    artifact_digests: tuple[str, ...] = ()
    duration_ms: int = 0


class ReleaseRunner:
    def __init__(
        self,
        *,
        store: ContentAddressedEvidenceStore,
        signer: ReleaseEvidenceSigner,
    ) -> None:
        self.store = store
        self.signer = signer

    def put_json_artifact(self, *, artifact_id: str, artifact_kind: str, payload: dict[str, Any]) -> ReleaseArtifactRefV1:
        from app.schema.canonical import canonical_json_bytes

        data = canonical_json_bytes(payload)
        digest = self.store.put_artifact(data, media_type="application/json")
        return ReleaseArtifactRefV1(
            artifact_id=artifact_id,
            artifact_kind=artifact_kind,
            media_type="application/json",
            sha256_digest=digest,
            byte_size=len(data),
        )

    @staticmethod
    def observation_result(observation: ReleaseObservation) -> ReleaseAssertionResultV1:
        observation_digest = sha256_canonical_json(
            {
                "domain": "mindatlas:release-observation:v1",
                "assertionId": observation.assertion_id,
                "passed": observation.passed,
                "safeFailureCode": observation.safe_failure_code,
                "payload": observation.payload,
            }
        )
        return ReleaseAssertionResultV1(
            assertion_id=observation.assertion_id,
            passed=observation.passed,
            safe_failure_code=observation.safe_failure_code,
            observation_digest=observation_digest,
            artifact_digests=observation.artifact_digests,
            duration_ms=observation.duration_ms,
        )

    def finalize(
        self,
        *,
        manifest_values: dict[str, Any],
        observations: tuple[ReleaseObservation, ...],
        artifacts: tuple[ReleaseArtifactRefV1, ...],
    ) -> tuple[ReleaseEvidenceManifestV1, Any]:
        if len({item.assertion_id for item in observations}) != len(observations):
            raise ReleaseRunnerError("duplicate assertion id")
        results = tuple(self.observation_result(item) for item in observations)
        manifest = ReleaseEvidenceManifestV1.build(
            **manifest_values,
            assertion_results=results,
            artifact_refs=artifacts,
        )
        attestation = self.signer.sign(manifest)
        self.store.put_evidence_object(manifest, attestation)
        return manifest, attestation


__all__ = ["ReleaseObservation", "ReleaseRunner", "ReleaseRunnerError"]
