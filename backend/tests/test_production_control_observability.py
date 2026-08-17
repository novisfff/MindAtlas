from __future__ import annotations

import logging

import pytest


def test_pre_ga_metrics_use_only_closed_labels_and_redact_sentinels(caplog) -> None:
    from app.pre_ga_launch.observability import (
        clear_pre_ga_metrics_for_tests,
        record_pre_ga_metric,
        snapshot_pre_ga_metrics,
    )

    clear_pre_ga_metrics_for_tests()
    caplog.set_level(logging.INFO)
    record_pre_ga_metric(
        "mindatlas_pre_ga_launch_state",
        {"state": "not-a-password-or-token"},
    )
    snapshot = snapshot_pre_ga_metrics()
    assert all("not-a-password" not in repr(item) for item in snapshot)
    assert any(("state", "other") in labels for _, labels in snapshot)
    with pytest.raises(ValueError, match="labels"):
        record_pre_ga_metric(
            "mindatlas_pre_ga_launch_state",
            {"state": "current", "rawEntryBody": "sentinel"},
        )
    assert "sentinel" not in caplog.text


def test_capability_observability_maps_unknown_labels_without_content() -> None:
    from app.assistant.capability_calls.observability import (
        clear_capability_metrics_for_tests,
        record_capability_metric,
        snapshot_capability_metrics,
    )

    clear_capability_metrics_for_tests()
    record_capability_metric(
        "mindatlas_agent_unsupported_write_total",
        {"branch": "update_entry", "entrypoint": "not-a-session-token"},
    )
    snapshot = snapshot_capability_metrics()
    assert any(("entrypoint", "other") in labels for _, labels in snapshot)
    assert all("not-a-session-token" not in repr(item) for item in snapshot)
    with pytest.raises(ValueError):
        record_capability_metric(
            "mindatlas_agent_unsupported_write_total",
            {"branch": "update_entry"},
        )
