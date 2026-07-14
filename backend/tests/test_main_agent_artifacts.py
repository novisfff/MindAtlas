"""Transient Artifact store and result projection tests (Plan 04 Task 7)."""

from __future__ import annotations

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def test_put_and_read_chunk_utf8() -> None:
    from app.assistant.main_agent.artifacts import TransientArtifactStore

    store = TransientArtifactStore(token_factory=lambda: "art-1")
    ref = store.put_text("hello artifact world")
    assert ref.artifact_id == "art-1"
    assert len(ref.content_digest) == 64
    chunk = store.read_chunk("art-1", offset=0, limit=5)
    assert chunk["encoding"] == "utf-8"
    assert chunk["content"] == "hello"
    assert chunk["eof"] is False


def test_oversized_single_artifact_rejected() -> None:
    from app.assistant.main_agent.artifacts import RESULT_TOO_LARGE, TransientArtifactStore

    store = TransientArtifactStore(max_artifact_bytes=16, token_factory=lambda: "a")
    with pytest.raises(ValueError) as exc:
        store.put_bytes(b"x" * 32)
    assert str(exc.value) == RESULT_TOO_LARGE


def test_run_budget_and_lru_skips_referenced() -> None:
    from app.assistant.main_agent.artifacts import TransientArtifactStore

    tokens = iter(["a1", "a2", "a3"])
    store = TransientArtifactStore(
        max_artifact_bytes=100,
        run_max_bytes=30,
        max_artifacts=10,
        token_factory=lambda: next(tokens),
    )
    r1 = store.put_bytes(b"x" * 10)
    store.mark_referenced(r1.artifact_id)
    r2 = store.put_bytes(b"y" * 10)
    # Need room for 15 more bytes; a2 (unreferenced) should be evicted, a1 kept.
    r3 = store.put_bytes(b"z" * 15)
    assert store.get(r1.artifact_id) is not None
    assert store.get(r2.artifact_id) is None
    assert store.get(r3.artifact_id) is not None


def test_project_oversized_structured_to_artifact() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityMetrics,
        completed_result,
    )
    from app.assistant.main_agent.artifacts import (
        TransientArtifactStore,
        project_capability_result,
    )

    store = TransientArtifactStore(token_factory=lambda: "proj-1")
    big = {"data": "x" * 1000}
    result = completed_result(
        user_text=None,
        structured_output=big,
        metrics=CapabilityMetrics(
            duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
        ),
    )
    projected = project_capability_result(
        result, store=store, inline_threshold_bytes=64
    )
    assert projected.status == "completed"
    assert projected.artifact_refs
    assert projected.structured_output["summary"] == "structured_output_artifactized"
    # Raw big payload not retained inline.
    assert "xxxx" not in str(projected.structured_output)


def test_artifact_read_control_handler() -> None:
    from app.assistant.main_agent.artifacts import (
        TransientArtifactStore,
        handle_artifact_read,
    )

    store = TransientArtifactStore(token_factory=lambda: "read-1")
    store.put_text("abcdef")
    result = handle_artifact_read(
        call_id="c1",
        validated_input={"artifactId": "read-1", "offset": 2, "limit": 2},
        store=store,
    )
    assert result.status == "completed"
    assert result.structured_output["content"] == "cd"


def test_artifact_not_found_and_cleared_state() -> None:
    from app.assistant.main_agent.artifacts import (
        ARTIFACT_NOT_FOUND,
        NON_DURABLE_STATE_LOST,
        TransientArtifactStore,
        handle_artifact_read,
    )

    store = TransientArtifactStore(token_factory=lambda: "x")
    missing = handle_artifact_read(
        call_id="c2",
        validated_input={"artifactId": "missing"},
        store=store,
    )
    assert missing.status == "failed"
    assert missing.error.safe_code == ARTIFACT_NOT_FOUND

    store.put_text("data")
    store.clear()
    lost = handle_artifact_read(
        call_id="c3",
        validated_input={"artifactId": "x"},
        store=store,
    )
    assert lost.status == "failed"
    assert lost.error.safe_code in {ARTIFACT_NOT_FOUND, NON_DURABLE_STATE_LOST}


def test_result_too_large_when_run_cap_exceeded() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityMetrics,
        completed_result,
    )
    from app.assistant.main_agent.artifacts import (
        RESULT_TOO_LARGE,
        TransientArtifactStore,
        project_capability_result,
    )

    store = TransientArtifactStore(
        max_artifact_bytes=100,
        run_max_bytes=50,
        token_factory=lambda: "only",
    )
    result = completed_result(
        user_text="y" * 80,
        structured_output=None,
        metrics=CapabilityMetrics(
            duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
        ),
    )
    projected = project_capability_result(
        result, store=store, inline_threshold_bytes=10
    )
    assert projected.status == "failed"
    assert projected.error.safe_code == RESULT_TOO_LARGE


def test_artifact_content_absent_from_safe_repr() -> None:
    from app.assistant.main_agent.artifacts import TransientArtifactStore

    store = TransientArtifactStore(token_factory=lambda: "secret-art")
    store.put_text("TOP-SECRET-PAYLOAD-VALUE")
    # Store repr / public methods must not dump content.
    blob = repr(store)
    assert "TOP-SECRET" not in blob
    item = store.get("secret-art")
    assert item is not None
    # Explicit access returns bytes; that is intentional for control path only.
    assert b"TOP-SECRET" in item.content
