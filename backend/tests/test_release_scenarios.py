from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_frozen_scenario_set_loads_and_covers_required_assertions() -> None:
    from app.release.scenarios import (
        REQUIRED_ASSERTION_SET,
        load_scenario_set,
    )

    scenario_set = load_scenario_set()
    assert scenario_set.contract_version == 1
    assert scenario_set.scenarios
    assert REQUIRED_ASSERTION_SET <= set(scenario_set.required_assertion_ids)
    assert len(scenario_set.digest) == 64


def test_scenario_loader_rejects_unknown_steps_and_skip(tmp_path: Path) -> None:
    from app.release.scenarios import ScenarioSetError, load_scenario_set

    payload = {
        "schemaVersion": 1,
        "contractVersion": 1,
        "scenarios": [
            {
                "scenarioId": "bad",
                "releaseCritical": True,
                "requiredServices": ["postgres", "minio", "api", "frontend", "scripted-provider"],
                "workerCount": 2,
                "timeoutSeconds": 10,
                "steps": [
                    {"stepId": "x", "kind": "skip", "expectedAssertionIds": ["teardown"]}
                ],
            }
        ],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScenarioSetError):
        load_scenario_set(path)


def test_scenario_digest_is_order_stable_but_duplicate_ids_fail(tmp_path: Path) -> None:
    from app.release.scenarios import ScenarioSetError, load_scenario_set

    source = Path("release/scenarios/pre_ga_launch.v1.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["scenarios"] = list(reversed(payload["scenarios"]))
    reversed_path = tmp_path / "reversed.json"
    reversed_path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_scenario_set(reversed_path).digest == load_scenario_set(source).digest

    payload["scenarios"].append(payload["scenarios"][0])
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScenarioSetError, match="duplicate"):
        load_scenario_set(duplicate_path)
