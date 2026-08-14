from __future__ import annotations

import pytest


def _script():
    from app.release.scripted_provider import ScriptedProviderStep, ScriptedProviderScript

    return ScriptedProviderScript(
        scenario_id="smoke",
        steps=(
            ScriptedProviderStep(
                scenario_id="smoke",
                request_ordinal=1,
                expected_tool_names=("create_entry",),
                response_kind="tool_call",
                tool_name="create_entry",
                fault_code=None,
            ),
            ScriptedProviderStep(
                scenario_id="smoke",
                request_ordinal=2,
                expected_tool_names=(),
                response_kind="content",
                tool_name=None,
                fault_code=None,
            ),
        ),
    )


def test_scripted_provider_is_structural_and_deterministic() -> None:
    from app.release.scripted_provider import ScriptedProvider

    provider = ScriptedProvider(_script())
    request = {"model": "ignored", "messages": [{"role": "user", "content": "sentinel"}], "tools": [{"type": "function", "function": {"name": "create_entry"}}]}
    first = provider.complete(request, scenario_id="smoke", request_ordinal=1)
    second = ScriptedProvider(_script()).complete(
        {**request, "messages": [{"role": "user", "content": "different"}]},
        scenario_id="smoke",
        request_ordinal=1,
    )
    assert first == second
    assert "sentinel" not in repr(first)


def test_scripted_provider_rejects_ordinal_reuse_tool_drift_and_paid_endpoint() -> None:
    from app.release.scripted_provider import ScriptedProvider, ScriptedProviderError

    provider = ScriptedProvider(_script())
    request = {"messages": [], "tools": [{"type": "function", "function": {"name": "create_entry"}}]}
    provider.complete(request, scenario_id="smoke", request_ordinal=1)
    with pytest.raises(ScriptedProviderError, match="ordinal"):
        provider.complete(request, scenario_id="smoke", request_ordinal=1)
    with pytest.raises(ScriptedProviderError, match="tool"):
        provider.complete(
            {"messages": [], "tools": [{"type": "function", "function": {"name": "update_entry"}}]},
            scenario_id="smoke",
            request_ordinal=2,
        )
    with pytest.raises(ScriptedProviderError, match="endpoint"):
        provider.complete(request, scenario_id="smoke", request_ordinal=2, endpoint="https://api.openai.com/v1")
