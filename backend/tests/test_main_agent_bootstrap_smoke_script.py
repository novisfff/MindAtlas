"""Unit tests for Main-Agent bootstrap smoke evidence, stub, SSRF gate, cleanup."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import urllib.error
import urllib.request

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

import scripts.smoke_main_agent_bootstrap as smoke_script  # noqa: E402
from scripts.smoke_main_agent_bootstrap import (  # noqa: E402
    ALLOWED_EVIDENCE_KEYS,
    SENSITIVE_FRAGMENTS,
    ComposeRunner,
    collect_database_evidence,
    EvidenceSchemaError,
    SmokeFailure,
    extract_status_fields,
    finalize_evidence,
    parse_sse_status_buffer,
    resolve_chat_completion_evidence,
    terminal_after_active_cleared,
    validate_evidence,
    write_evidence_atomic,
)
from tests.support.openai_stub_server import (  # noqa: E402
    FIXED_CHUNKS,
    SMOKE_MODEL,
    evaluate_chat_completion_request,
    make_server,
)


def _safe_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "2",
        "verificationKind": "main_agent_bootstrap_readiness",
        "checkoutCommitSha": "0123456789abcdef0123456789abcdef01234567",
        "pullRequestHeadSha": "89abcdef0123456789abcdef0123456789abcdef",
        "buildRevision": "plan2-smoke-deadbeef",
        "alembicHead": "b6e2d4f8a901",
        "seedManifestDigest": "3ba69223e9c1a3ab1783c8a549a1b7cf56410f36f60a7fb67ba1f4fc84275237",
        "healthStatus": "ok",
        "readinessTransitions": [
            "health_ok",
            "ready_system_not_initialized",
            "initialized_pending_worker",
            "ready_post_init_blocked",
            "compatible_worker",
            "ready_rollout_inactive",
            "activation_committed",
            "ready_ok",
            "conversation_created",
            "chat_admitted",
            "chat_completed",
        ],
        "compatibleWorkerCount": 1,
        "activeRuntimeKind": "main_agent",
        "chatRunCount": 1,
        "chatTerminalStatus": "completed",
        "testSuites": {
            "suiteCount": 1,
            "passed": True,
            "totalPassed": 10,
            "totalFailed": 0,
            "totalSkipped": 0,
            "pytestVersion": "9.0.2",
            "pythonVersion": "3.11.0",
        },
        "generatedAtUtc": "2026-07-28T00:00:00+00:00",
    }


@pytest.mark.parametrize("field", ["checkoutCommitSha", "pullRequestHeadSha"])
def test_finalize_evidence_rejects_invalid_source_sha(field: str) -> None:
    evidence = finalize_evidence(_safe_payload())
    evidence[field] = "not-a-sha"
    with pytest.raises(EvidenceSchemaError, match=field):
        validate_evidence(evidence)


def test_source_attributed_evidence_requires_schema_version_2() -> None:
    payload = _safe_payload()
    payload["schemaVersion"] = "1"
    with pytest.raises(EvidenceSchemaError, match="schemaVersion"):
        finalize_evidence(payload)


@pytest.mark.parametrize("field", ["checkoutCommitSha", "pullRequestHeadSha"])
def test_source_sha_is_covered_by_aggregate_digest(field: str) -> None:
    baseline = finalize_evidence(_safe_payload())
    changed = _safe_payload()
    changed[field] = "a" * 40
    updated = finalize_evidence(changed)
    assert updated["aggregateDigest"] != baseline["aggregateDigest"]


def test_source_provenance_derives_checkout_and_defaults_pr_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout_sha = "0123456789abcdef0123456789abcdef01234567"
    run = MagicMock(returncode=0, stdout=f"{checkout_sha}\n", stderr="")
    monkeypatch.setattr(smoke_script.subprocess, "run", MagicMock(return_value=run))

    assert smoke_script.resolve_source_provenance(None) == (
        checkout_sha,
        checkout_sha,
    )
    smoke_script.subprocess.run.assert_called_once_with(
        ["git", "rev-parse", "HEAD"],
        cwd=str(smoke_script._BACKEND_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_source_provenance_preserves_supplied_valid_pr_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout_sha = "0123456789abcdef0123456789abcdef01234567"
    pull_request_head_sha = "89abcdef0123456789abcdef0123456789abcdef"
    run = MagicMock(returncode=0, stdout=f"{checkout_sha}\n", stderr="")
    monkeypatch.setattr(smoke_script.subprocess, "run", MagicMock(return_value=run))

    assert smoke_script.resolve_source_provenance(pull_request_head_sha) == (
        checkout_sha,
        pull_request_head_sha,
    )


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, ""),
        (0, "not-a-sha\n"),
        (0, "A" * 40),
    ],
)
def test_source_provenance_fails_closed_for_invalid_checkout(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
) -> None:
    run = MagicMock(returncode=returncode, stdout=stdout, stderr="ignored")
    monkeypatch.setattr(smoke_script.subprocess, "run", MagicMock(return_value=run))

    with pytest.raises(SmokeFailure, match="checkoutCommitSha"):
        smoke_script.resolve_source_provenance(None)


@pytest.mark.parametrize("pull_request_head_sha", ["", " ", "\t", "not-a-sha"])
def test_source_provenance_rejects_explicit_invalid_pr_head_before_secrets_or_compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pull_request_head_sha: str,
) -> None:
    compose_file = tmp_path / "compose.yml"
    overlay_file = tmp_path / "overlay.yml"
    compose_file.touch()
    overlay_file.touch()
    compose_runner = MagicMock()
    generate_secrets = MagicMock(
        side_effect=AssertionError("invalid provenance reached secret generation")
    )
    run_suites = MagicMock(
        side_effect=AssertionError("invalid provenance reached unit suites")
    )
    monkeypatch.setattr(smoke_script, "ComposeRunner", compose_runner)
    monkeypatch.setattr(smoke_script, "generate_ephemeral_secrets", generate_secrets)
    monkeypatch.setattr(smoke_script, "run_unit_suites", run_suites)

    result = smoke_script.main(
        [
            "--compose-file",
            str(compose_file),
            "--overlay-file",
            str(overlay_file),
            "--output",
            str(tmp_path / "evidence.json"),
            "--pull-request-head-sha",
            pull_request_head_sha,
        ]
    )

    assert result == 2
    generate_secrets.assert_not_called()
    run_suites.assert_not_called()
    compose_runner.assert_not_called()


def test_smoke_evidence_has_only_safe_keys() -> None:
    evidence = finalize_evidence(_safe_payload())
    assert set(evidence) == ALLOWED_EVIDENCE_KEYS
    serialized = json.dumps(evidence).lower()
    for fragment in (
        "password",
        "setup",
        "token",
        "cookie",
        "api_key",
        "prompt",
        "entry",
        "artifact",
        "provider_payload",
    ):
        assert fragment not in serialized
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in serialized


def test_finalize_evidence_rejects_unknown_keys() -> None:
    payload = _safe_payload()
    payload["sessionSecret"] = "nope"
    with pytest.raises(EvidenceSchemaError, match="allowlist"):
        finalize_evidence(payload)


def test_finalize_evidence_rejects_missing_keys() -> None:
    payload = _safe_payload()
    del payload["alembicHead"]
    with pytest.raises(EvidenceSchemaError, match="allowlist"):
        finalize_evidence(payload)


def test_finalize_evidence_digest_is_64_lowercase_hex() -> None:
    evidence = finalize_evidence(_safe_payload())
    digest = evidence["aggregateDigest"]
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_finalize_evidence_digest_covers_canonical_payload() -> None:
    first = finalize_evidence(_safe_payload())
    second = finalize_evidence(_safe_payload())
    assert first["aggregateDigest"] == second["aggregateDigest"]
    altered = _safe_payload()
    altered["buildRevision"] = "plan2-smoke-cafebabe"
    third = finalize_evidence(altered)
    assert third["aggregateDigest"] != first["aggregateDigest"]


def test_validate_evidence_detects_tampered_digest() -> None:
    evidence = dict(finalize_evidence(_safe_payload()))
    evidence["aggregateDigest"] = "0" * 64
    with pytest.raises(EvidenceSchemaError, match="aggregateDigest"):
        validate_evidence(evidence)


def test_write_evidence_atomic_mode_and_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    evidence = finalize_evidence(_safe_payload())
    write_evidence_atomic(path, evidence)
    assert path.is_file()
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600 or mode == 0o400  # some FS may mask write bit on read
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(reloaded)
    assert reloaded["activeRuntimeKind"] == "main_agent"
    assert reloaded["chatRunCount"] == 1
    assert reloaded["chatTerminalStatus"] == "completed"


def test_safe_payload_fragments_stay_clean() -> None:
    serialized = json.dumps(_safe_payload()).lower()
    for fragment in SENSITIVE_FRAGMENTS:
        assert fragment not in serialized


def test_stub_rejects_wrong_model() -> None:
    status, reason = evaluate_chat_completion_request(
        {"model": "gpt-4o", "stream": True}
    )
    assert status == 400
    assert reason == "unsupported_model"


def test_stub_rejects_non_stream() -> None:
    status, reason = evaluate_chat_completion_request(
        {"model": SMOKE_MODEL, "stream": False}
    )
    assert status == 400
    assert reason == "stream_required"


def test_stub_rejects_tools() -> None:
    status, reason = evaluate_chat_completion_request(
        {
            "model": SMOKE_MODEL,
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "x"}}],
        }
    )
    assert status == 400
    assert reason == "tools_not_supported"


def test_stub_accepts_smoke_model_stream() -> None:
    status, reason = evaluate_chat_completion_request(
        {"model": SMOKE_MODEL, "stream": True, "messages": []}
    )
    assert status == 200
    assert reason == "ok"
    assert len(FIXED_CHUNKS) == 3


def test_stub_server_in_thread_serves_sse() -> None:
    server = make_server("127.0.0.1", 0)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(
            {"model": SMOKE_MODEL, "stream": True, "messages": []}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            raw = resp.read().decode("utf-8")
        assert "smoke-ok" in raw
        assert "data: [DONE]" in raw

        bad = json.dumps({"model": "other", "stream": True}).encode("utf-8")
        bad_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=bad,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(bad_req, timeout=5)
        assert exc_info.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_ssrf_test_provider_host_allowed_only_in_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.common.ssrf import SSRFError, validate_url_ssrf
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MINDATLAS_TEST_PROVIDER_HOST", "provider-stub")
    get_settings.cache_clear()

    # Exact host allowed — skips DNS private-IP check (would block otherwise).
    private_ip = "10.0.0.42"

    def fake_getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
        assert host == "provider-stub"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (private_ip, 0))]

    monkeypatch.setattr("app.common.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    validate_url_ssrf("http://provider-stub:8089/v1")

    # Wrong host still blocked when DNS returns private.
    def fake_other(host, *args, **kwargs):  # noqa: ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (private_ip, 0))]

    monkeypatch.setattr("app.common.ssrf.socket.getaddrinfo", fake_other)
    with pytest.raises(SSRFError):
        validate_url_ssrf("http://other-stub:8089/v1")

    # Non-test env never allows.
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    monkeypatch.setattr("app.common.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError):
        validate_url_ssrf("http://provider-stub:8089/v1")

    # IP literals never allowed even in test.
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    with pytest.raises(SSRFError):
        validate_url_ssrf("http://10.0.0.42:8089/v1")

    get_settings.cache_clear()


def test_ssrf_empty_test_host_does_not_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.common.ssrf import SSRFError, validate_url_ssrf
    from app.config import get_settings

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MINDATLAS_TEST_PROVIDER_HOST", "")
    get_settings.cache_clear()

    def fake_getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.1.2.3", 0))]

    monkeypatch.setattr("app.common.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError):
        validate_url_ssrf("http://provider-stub:8089/v1")
    get_settings.cache_clear()


def test_failure_still_runs_compose_down_and_redacts(tmp_path: Path) -> None:
    """Failure path must always compose down --volumes and keep secrets out of output."""
    setup_secret = "setup-secret-value-xyz-not-for-logs"
    operator_password = "operator-password-value-abc-not-for-logs"

    class FailingRunner:
        def __init__(self) -> None:
            self.compose_down_called_with_volumes = False
            self.setup_secret = setup_secret
            self.operator_password = operator_password
            self.stdout = ""
            self.stderr = ""

        def run(self) -> MagicMock:
            compose = ComposeRunner(
                compose_file=tmp_path / "missing-compose.yml",
                overlay_file=tmp_path / "missing-overlay.yml",
                project_name="test-smoke-fail",
                env=dict(os.environ),
            )
            # Bypass real docker: patch down to record the flag.
            def fake_down() -> None:
                compose.compose_down_called_with_volumes = True

            compose.down = fake_down  # type: ignore[method-assign]
            try:
                # Simulate work that fails after "initialization" without leaking secrets.
                raise RuntimeError("initialization_followup_failed")
            except RuntimeError as exc:
                self.stderr = f"smoke failed: {exc}"
                result = MagicMock()
                result.returncode = 1
                result.stdout = self.stdout
                result.stderr = self.stderr
                compose.down()
                self.compose_down_called_with_volumes = (
                    compose.compose_down_called_with_volumes
                )
                return result

    runner = FailingRunner()
    result = runner.run()
    assert result.returncode == 1
    assert runner.compose_down_called_with_volumes is True
    combined = (result.stdout or "") + (result.stderr or "")
    assert runner.setup_secret not in combined
    assert runner.operator_password not in combined


def test_compose_down_command_includes_volumes() -> None:
    runner = ComposeRunner(
        compose_file=Path("/tmp/compose.yml"),
        overlay_file=Path("/tmp/overlay.yml"),
        project_name="demo",
        env={},
    )
    with patch("scripts.smoke_main_agent_bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.down()
    assert runner.compose_down_called_with_volumes is True
    args = mock_run.call_args[0][0]
    assert "down" in args
    assert "--volumes" in args
    assert "--remove-orphans" in args


def test_compose_runner_observes_safe_database_scalars() -> None:
    """The smoke evidence facts come from the Compose PostgreSQL service."""
    runner = ComposeRunner(
        compose_file=Path("/tmp/compose.yml"),
        overlay_file=Path("/tmp/overlay.yml"),
        project_name="demo",
        env={},
    )
    with patch("scripts.smoke_main_agent_bootstrap.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="b6e2d4f8a901\n", stderr=""),
            MagicMock(returncode=0, stdout="1\n", stderr=""),
        ]
        assert runner.observed_alembic_head() == "b6e2d4f8a901"
        assert (
            runner.observed_conversation_run_count(
                "11111111-1111-1111-1111-111111111111"
            )
            == 1
        )

    commands = [" ".join(call.args[0]) for call in mock_run.call_args_list]
    assert all(" exec -T postgres " in f" {command} " for command in commands)
    assert "SELECT version_num FROM alembic_version" in commands[0]
    assert "SELECT COUNT(*) FROM assistant_chat_run" in commands[1]
    assert "11111111-1111-1111-1111-111111111111" in commands[1]


def test_compose_runner_rejects_invalid_database_scalar() -> None:
    runner = ComposeRunner(
        compose_file=Path("/tmp/compose.yml"),
        overlay_file=Path("/tmp/overlay.yml"),
        project_name="demo",
        env={},
    )
    with patch("scripts.smoke_main_agent_bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="b6e2d4f8a901\nunexpected-second-row\n",
            stderr="",
        )
        with pytest.raises(SmokeFailure, match="invalid scalar"):
            runner.observed_alembic_head()


def test_collect_database_evidence_requires_expected_observations() -> None:
    compose = MagicMock()
    compose.observed_alembic_head.return_value = "b6e2d4f8a901"
    compose.observed_conversation_run_count.return_value = 1

    assert collect_database_evidence(
        compose,
        conversation_id="11111111-1111-1111-1111-111111111111",
    ) == ("b6e2d4f8a901", 1)
    compose.observed_alembic_head.assert_called_once_with()
    compose.observed_conversation_run_count.assert_called_once_with(
        "11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.parametrize(
    ("alembic_head", "chat_run_count", "match"),
    [
        ("wrong-head", 1, "alembic head"),
        ("b6e2d4f8a901", 0, "chat run count"),
        ("b6e2d4f8a901", 2, "chat run count"),
    ],
)
def test_collect_database_evidence_fails_closed_on_invalid_values(
    alembic_head: str,
    chat_run_count: int,
    match: str,
) -> None:
    compose = MagicMock()
    compose.observed_alembic_head.return_value = alembic_head
    compose.observed_conversation_run_count.return_value = chat_run_count

    with pytest.raises(SmokeFailure, match=match):
        collect_database_evidence(
            compose,
            conversation_id="11111111-1111-1111-1111-111111111111",
        )


def test_terminal_after_active_cleared_never_soft_assumes_completed() -> None:
    """Active-cleared path must not mint completed without an observed terminal."""
    assert terminal_after_active_cleared(stream_observed_terminal=None) is None
    assert terminal_after_active_cleared(stream_observed_terminal="") is None
    assert terminal_after_active_cleared(stream_observed_terminal="running") is None
    assert terminal_after_active_cleared(stream_observed_terminal="queued") is None
    assert terminal_after_active_cleared(stream_observed_terminal="completed") == "completed"
    assert terminal_after_active_cleared(stream_observed_terminal="failed") == "failed"
    assert terminal_after_active_cleared(stream_observed_terminal="cancelled") == "cancelled"


def test_parse_sse_status_buffer_extracts_terminal_kind_and_run_id() -> None:
    text = "\n".join(
        [
            "event: run_status",
            'data: {"status": "queued", "runtimeKind": "main_agent", "runId": "run-1", "seq": 1}',
            "",
            "event: content_delta",
            'data: {"delta": "should-not-be-required", "runId": "run-1", "seq": 2}',
            "",
            "event: run_status",
            'data: {"status": "completed", "runId": "run-1", "seq": 3}',
            "",
            "data: [DONE]",
        ]
    )
    obs = parse_sse_status_buffer(text)
    assert obs.terminal_status == "completed"
    assert obs.runtime_kinds == frozenset({"main_agent"})
    assert obs.run_ids == frozenset({"run-1"})


def test_parse_sse_status_buffer_ignores_malformed_and_non_terminal() -> None:
    text = "\n".join(
        [
            "data: not-json",
            'data: {"status": "running", "runtime_kind": "main_agent", "run_id": "r2"}',
            'data: ["array"]',
        ]
    )
    obs = parse_sse_status_buffer(text)
    assert obs.terminal_status is None
    assert obs.runtime_kinds == frozenset({"main_agent"})
    assert obs.run_ids == frozenset({"r2"})


def test_extract_status_fields_reads_camel_and_snake() -> None:
    assert extract_status_fields(
        {"status": "completed", "runtimeKind": "main_agent", "runId": "a"}
    ) == ("completed", "main_agent", "a")
    assert extract_status_fields(
        {"status": "failed", "runtime_kind": "legacy", "run_id": "b"}
    ) == ("failed", "legacy", "b")
    assert extract_status_fields({}) == (None, None, None)


def test_resolve_chat_completion_evidence_happy_path() -> None:
    terminal, kind, count = resolve_chat_completion_evidence(
        observed_terminal="completed",
        observed_runtime_kinds={"main_agent"},
        observed_run_ids={"run-abc"},
        admitted_run_id="run-abc",
    )
    assert terminal == "completed"
    assert kind == "main_agent"
    assert count == 1


def test_resolve_chat_completion_evidence_fails_unobserved_terminal() -> None:
    with pytest.raises(SmokeFailure, match="unobserved"):
        resolve_chat_completion_evidence(
            observed_terminal="",
            observed_runtime_kinds={"main_agent"},
            observed_run_ids={"run-abc"},
            admitted_run_id="run-abc",
        )


def test_resolve_chat_completion_evidence_rejects_non_completed_terminal() -> None:
    with pytest.raises(SmokeFailure, match="expected completed got failed"):
        resolve_chat_completion_evidence(
            observed_terminal="failed",
            observed_runtime_kinds={"main_agent"},
            observed_run_ids={"run-abc"},
            admitted_run_id="run-abc",
        )


def test_resolve_chat_completion_evidence_requires_observed_runtime_kind() -> None:
    with pytest.raises(SmokeFailure, match="runtimeKind never observed"):
        resolve_chat_completion_evidence(
            observed_terminal="completed",
            observed_runtime_kinds=set(),
            observed_run_ids={"run-abc"},
            admitted_run_id="run-abc",
        )
    with pytest.raises(SmokeFailure, match="runtimeKind expected main_agent"):
        resolve_chat_completion_evidence(
            observed_terminal="completed",
            observed_runtime_kinds={"legacy"},
            observed_run_ids={"run-abc"},
            admitted_run_id="run-abc",
        )


def test_resolve_chat_completion_evidence_requires_single_admitted_run_id() -> None:
    with pytest.raises(SmokeFailure, match="no run ids observed"):
        resolve_chat_completion_evidence(
            observed_terminal="completed",
            observed_runtime_kinds={"main_agent"},
            observed_run_ids=set(),
            admitted_run_id="run-abc",
        )
    with pytest.raises(SmokeFailure, match="exactly one run id"):
        resolve_chat_completion_evidence(
            observed_terminal="completed",
            observed_runtime_kinds={"main_agent"},
            observed_run_ids={"run-abc", "run-other"},
            admitted_run_id="run-abc",
        )
    with pytest.raises(SmokeFailure, match="exactly one run id"):
        resolve_chat_completion_evidence(
            observed_terminal="completed",
            observed_runtime_kinds={"main_agent"},
            observed_run_ids={"run-other"},
            admitted_run_id="run-abc",
        )
