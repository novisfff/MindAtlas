"""Frozen release scenario set and assertion coverage rules."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from app.assistant.domain.digests import sha256_canonical_json
from app.release.contracts import ReleaseContract


DEFAULT_SCENARIO_PATH = Path(__file__).resolve().parents[2] / "release" / "scenarios" / "pre_ga_launch.v1.json"
SCENARIO_SET_DOMAIN = "mindatlas:release-scenario-set:v1"
REQUIRED_ASSERTION_DOMAIN = "mindatlas:release-required-assertions:v1"

SCENARIO_STEP_KINDS = frozenset(
    {
        "setup",
        "login_csrf",
        "rollout_activation",
        "worker_claim_lease",
        "interrupt_sse",
        "local_create",
        "recovery",
        "reconciliation",
        "artifact_gc",
        "l2_memory",
        "readiness",
        "launch_control",
        "teardown",
    }
)
FAULT_POINTS = frozenset(
    {
        "none",
        "before_call_insert",
        "after_call_insert",
        "before_entry_insert",
        "after_entry_commit_before_ack",
        "connection_drop_during_commit",
        "stream_disconnect",
    }
)
REQUIRED_ASSERTION_SET = frozenset(
    {
        "qualification-target",
        "schema-identity",
        "operator-auth",
        "bootstrap-readiness",
        "two-workers-ready",
        "worker-fault-matrix",
        "interrupt-idempotency",
        "streaming",
        "create-entry",
        "recovery-reconciliation",
        "artifact-gc",
        "l2-memory",
        "readiness",
        "launch-control",
        "secret-scan",
        "isolation",
        "teardown",
    }
)


class ScenarioSetError(ValueError):
    safe_code = "release_scenario_set_invalid"


class ScenarioStepV1(ReleaseContract):
    schema_version: Literal[1] = 1
    step_id: str
    kind: str
    fault_point: str = "none"
    expected_assertion_ids: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    timeout_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def validate_step(self) -> "ScenarioStepV1":
        if self.kind not in SCENARIO_STEP_KINDS:
            raise ScenarioSetError("unknown scenario step")
        if self.fault_point not in FAULT_POINTS:
            raise ScenarioSetError("unknown scenario fault point")
        if len(self.expected_assertion_ids) != len(set(self.expected_assertion_ids)):
            raise ScenarioSetError("duplicate assertion id in scenario step")
        return self


class ReleaseScenarioV1(ReleaseContract):
    schema_version: Literal[1] = 1
    scenario_id: str
    release_critical: bool
    required_services: tuple[
        Literal["postgres", "minio", "api", "frontend", "scripted-provider"], ...
    ]
    worker_count: int = Field(ge=2, le=2)
    timeout_seconds: int = Field(ge=1, le=3600)
    steps: tuple[ScenarioStepV1, ...]

    @model_validator(mode="after")
    def validate_scenario(self) -> "ReleaseScenarioV1":
        if self.release_critical:
            required = {"postgres", "minio", "api", "frontend", "scripted-provider"}
            if set(self.required_services) != required:
                raise ScenarioSetError("release-critical scenario services are incomplete")
            if "teardown" not in {item.kind for item in self.steps}:
                raise ScenarioSetError("release-critical scenario is missing teardown")
        ids = [item.step_id for item in self.steps]
        if len(ids) != len(set(ids)):
            raise ScenarioSetError("duplicate scenario step id")
        known = set(ids)
        for step in self.steps:
            if not set(step.depends_on) <= known:
                raise ScenarioSetError("scenario dependency is missing")
        graph = {step.step_id: set(step.depends_on) for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ScenarioSetError("scenario dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        return self


class ReleaseScenarioSetV1(ReleaseContract):
    schema_version: Literal[1] = 1
    contract_version: Literal[1] = 1
    scenarios: tuple[ReleaseScenarioV1, ...]
    digest: str
    required_assertion_ids: tuple[str, ...]
    required_assertion_set_digest: str

    @classmethod
    def build(cls, scenarios: tuple[ReleaseScenarioV1, ...]) -> "ReleaseScenarioSetV1":
        scenarios = tuple(sorted(scenarios, key=lambda item: item.scenario_id))
        ids = sorted(
            {
                assertion_id
                for scenario in scenarios
                if scenario.release_critical
                for step in scenario.steps
                for assertion_id in step.expected_assertion_ids
            }
        )
        required_digest = sha256_canonical_json(
            {"domain": REQUIRED_ASSERTION_DOMAIN, "assertionIds": ids}
        )
        scenario_payload = [
            scenario.model_dump(mode="json", by_alias=True, exclude_none=False)
            for scenario in scenarios
        ]
        digest = sha256_canonical_json(
            {
                "domain": SCENARIO_SET_DOMAIN,
                "contractVersion": 1,
                "scenarios": scenario_payload,
            }
        )
        return cls.model_validate(
            {
                "scenarios": scenarios,
                "digest": digest,
                "required_assertion_ids": tuple(ids),
                "required_assertion_set_digest": required_digest,
            }
        )

    @model_validator(mode="after")
    def validate_set(self) -> "ReleaseScenarioSetV1":
        ids = [item.scenario_id for item in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ScenarioSetError("duplicate scenario id")
        if not REQUIRED_ASSERTION_SET <= set(self.required_assertion_ids):
            raise ScenarioSetError("required assertion coverage is incomplete")
        referenced = sorted(
            {
                assertion_id
                for scenario in self.scenarios
                if scenario.release_critical
                for step in scenario.steps
                for assertion_id in step.expected_assertion_ids
            }
        )
        if referenced != list(self.required_assertion_ids):
            raise ScenarioSetError("required assertion inventory is not scenario-derived")
        expected_required_digest = sha256_canonical_json(
            {"domain": REQUIRED_ASSERTION_DOMAIN, "assertionIds": referenced}
        )
        if self.required_assertion_set_digest != expected_required_digest:
            raise ScenarioSetError("required assertion digest mismatch")
        expected_scenario_digest = sha256_canonical_json(
            {
                "domain": SCENARIO_SET_DOMAIN,
                "contractVersion": self.contract_version,
                "scenarios": [
                    scenario.model_dump(mode="json", by_alias=True, exclude_none=False)
                    for scenario in sorted(self.scenarios, key=lambda item: item.scenario_id)
                ],
            }
        )
        if self.digest != expected_scenario_digest:
            raise ScenarioSetError("scenario set digest mismatch")
        return self


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ScenarioSetError("scenario set is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ScenarioSetError("scenario set root must be an object")
    if set(payload) != {"schemaVersion", "contractVersion", "scenarios"}:
        raise ScenarioSetError("scenario set fields are not exact")
    if payload["schemaVersion"] != 1 or payload["contractVersion"] != 1:
        raise ScenarioSetError("scenario set version is unsupported")
    return payload


def load_scenario_set(path: Path = DEFAULT_SCENARIO_PATH) -> ReleaseScenarioSetV1:
    payload = _load_json(Path(path))
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ScenarioSetError("scenario set must contain scenarios")
    try:
        scenarios: list[ReleaseScenarioV1] = []
        for raw in raw_scenarios:
            if not isinstance(raw, dict):
                raise ValueError
            if "skip" in json.dumps(raw).lower() or "xfail" in json.dumps(raw).lower():
                raise ScenarioSetError("skip/xfail is not allowed in release scenarios")
            scenarios.append(ReleaseScenarioV1.model_validate(raw))
        return ReleaseScenarioSetV1.build(tuple(scenarios))
    except ScenarioSetError:
        raise
    except (TypeError, ValueError) as exc:
        raise ScenarioSetError(str(exc) or "scenario set is invalid") from None


_DEFAULT_SCENARIO_SET = load_scenario_set()
SCENARIO_SET_DIGEST = _DEFAULT_SCENARIO_SET.digest
REQUIRED_ASSERTION_SET_DIGEST = _DEFAULT_SCENARIO_SET.required_assertion_set_digest


__all__ = [
    "DEFAULT_SCENARIO_PATH",
    "FAULT_POINTS",
    "REQUIRED_ASSERTION_SET",
    "REQUIRED_ASSERTION_SET_DIGEST",
    "SCENARIO_SET_DIGEST",
    "SCENARIO_STEP_KINDS",
    "ReleaseScenarioSetV1",
    "ScenarioSetError",
    "load_scenario_set",
]
