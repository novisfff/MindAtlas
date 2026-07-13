"""Plan 03 clean-environment gate helper for dependency resolution evidence.

This module records that the OpenAI SDK is a direct requirement and that the
adapter package imports cleanly under the project dependency set. Full clean
Python 3.11 recreation is performed by the Task 6 verification command, not by
CI against paid providers.
"""

from __future__ import annotations

from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def test_openai_is_direct_requirement() -> None:
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    text = requirements.read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    openai_lines = [line for line in lines if line.lower().startswith("openai")]
    assert openai_lines, "openai must be declared as a direct dependency"
    assert any("1.104.2" in line and "<3.0.0" in line for line in openai_lines)


def test_adapter_imports_without_core_loop_openai_dependency() -> None:
    # Adapter package may import openai; core loop modules must not.
    import app.assistant.provider_loop.adapters.openai_chat as adapter_mod
    import app.assistant.provider_loop.loop as loop_mod
    import app.assistant.provider_loop.contracts as contracts_mod
    import app.assistant.provider_loop.streaming as streaming_mod

    assert adapter_mod.ADAPTER_KEY == "openai_chat_completions"
    assert "openai" not in getattr(loop_mod, "__dict__", {})
    source_loop = Path(loop_mod.__file__).read_text(encoding="utf-8")
    source_contracts = Path(contracts_mod.__file__).read_text(encoding="utf-8")
    source_streaming = Path(streaming_mod.__file__).read_text(encoding="utf-8")
    for source in (source_loop, source_contracts, source_streaming):
        assert "import openai" not in source
        assert "from openai" not in source


def test_openai_sdk_importable_with_expected_surface() -> None:
    import openai
    from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError

    assert hasattr(openai, "OpenAI")
    assert OpenAI is not None
    assert issubclass(APIStatusError, Exception)
    assert issubclass(APITimeoutError, Exception)
    assert issubclass(APIConnectionError, Exception)
