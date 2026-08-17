"""Server-owned release evidence finalization primitives.

The runner owns observation and assertion derivation.  Callers can provide
typed observations to this module, but cannot submit a passed flag or an
arbitrary assertion result through the public CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.assistant.domain.digests import sha256_canonical_json
from app.release.contracts import (
    ReleaseArtifactRefV1,
    ReleaseAssertionResultV1,
    ReleaseEvidenceManifestV1,
)
from app.release.evidence import ContentAddressedEvidenceStore, assert_safe_evidence_payload
from app.release.scenarios import ReleaseScenarioSetV1, ReleaseScenarioV1, ScenarioStepV1
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


class ReleaseScenarioExecutionPort(Protocol):
    """Protected-profile adapter for one real HTTP/DB/Worker scenario step."""

    def execute_step(
        self,
        *,
        scenario_id: str,
        step: ScenarioStepV1,
    ) -> tuple[ReleaseObservation, ...]: ...


class ReleaseScenarioExecutor:
    """Run the code-owned scenario graph without accepting caller outcomes."""

    def __init__(self, scenario_set: ReleaseScenarioSetV1, port: ReleaseScenarioExecutionPort) -> None:
        self.scenario_set = scenario_set
        self.port = port

    @staticmethod
    def _ordered_steps(scenario: ReleaseScenarioV1) -> tuple[ScenarioStepV1, ...]:
        remaining = {step.step_id: step for step in scenario.steps}
        completed: set[str] = set()
        ordered: list[ScenarioStepV1] = []
        while remaining:
            ready = sorted(
                (
                    step
                    for step in remaining.values()
                    if set(step.depends_on) <= completed
                ),
                key=lambda step: step.step_id,
            )
            if not ready:
                raise ReleaseRunnerError("scenario dependency order cannot be resolved")
            for step in ready:
                ordered.append(step)
                completed.add(step.step_id)
                remaining.pop(step.step_id)
        return tuple(ordered)

    def execute(self) -> tuple[ReleaseObservation, ...]:
        by_assertion: dict[str, list[ReleaseObservation]] = {}
        for scenario in sorted(self.scenario_set.scenarios, key=lambda item: item.scenario_id):
            if not scenario.release_critical:
                continue
            for step in self._ordered_steps(scenario):
                expected = set(step.expected_assertion_ids)
                try:
                    observations = tuple(
                        self.port.execute_step(scenario_id=scenario.scenario_id, step=step)
                    )
                except ReleaseRunnerError:
                    raise
                except Exception as exc:  # noqa: BLE001 - convert adapter failures to a safe code
                    raise ReleaseRunnerError("release_scenario_step_failed") from exc
                observed = {item.assertion_id for item in observations}
                if len(observations) != len(expected) or observed != expected or any(
                    item.assertion_id not in expected for item in observations
                ):
                    raise ReleaseRunnerError("release_scenario_assertion_inventory_mismatch")
                for observation in observations:
                    # Validate payload safety and derive a digest now, before
                    # aggregation can discard the step-level raw object.
                    result = ReleaseRunner.observation_result(observation)
                    by_assertion.setdefault(observation.assertion_id, []).append(
                        ReleaseObservation(
                            assertion_id=observation.assertion_id,
                            passed=observation.passed,
                            safe_failure_code=observation.safe_failure_code,
                            payload={
                                "stepId": step.step_id,
                                "observationDigest": result.observation_digest,
                            },
                            artifact_digests=observation.artifact_digests,
                            duration_ms=observation.duration_ms,
                        )
                    )

        required = set(self.scenario_set.required_assertion_ids)
        if set(by_assertion) != required:
            raise ReleaseRunnerError("release_scenario_required_assertion_missing")
        aggregate: list[ReleaseObservation] = []
        for assertion_id in sorted(required):
            entries = by_assertion[assertion_id]
            failed = next((item for item in entries if not item.passed), None)
            artifacts = tuple(sorted({digest for item in entries for digest in item.artifact_digests}))
            aggregate.append(
                ReleaseObservation(
                    assertion_id=assertion_id,
                    passed=failed is None,
                    safe_failure_code=failed.safe_failure_code if failed is not None else None,
                    payload={
                        "stepObservationDigests": sorted(
                            item.payload["observationDigest"] for item in entries
                        )
                    },
                    artifact_digests=artifacts,
                    duration_ms=sum(item.duration_ms for item in entries),
                )
            )
        return tuple(aggregate)


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
        assert_safe_evidence_payload(observation.payload)
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


__all__ = [
    "ReleaseObservation",
    "ReleaseRunner",
    "ReleaseRunnerError",
    "ReleaseScenarioExecutionPort",
    "ReleaseScenarioExecutor",
]
