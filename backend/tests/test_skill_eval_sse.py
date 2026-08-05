"""Plan 09 Task 8 — bounded SSE replay + dataset for eval run events."""

from __future__ import annotations

import json
import unittest
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.evaluation.repository import EvaluationRepository  # noqa: E402
from app.assistant.evaluation.router import PLAN09_EVAL_PREFIX, skill_eval_router  # noqa: E402
from tests._db import make_session  # noqa: E402
from tests.operator_session_helpers import (  # noqa: E402
    build_authenticated_skill_client,
    restore_operator_settings,
    origin_headers,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


@dataclass(frozen=True)
class SseFrame:
    id: str | None
    event: str | None
    data: dict[str, Any]


def _parse_sse_frames(raw: str) -> list[SseFrame]:
    frames: list[SseFrame] = []
    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block.strip():
            continue
        event_name: str | None = None
        event_id: str | None = None
        data_json = "{}"
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("id:"):
                event_id = line[len("id:") :].strip()
            elif line.startswith("data:"):
                data_json = line[len("data:") :].strip()
        try:
            payload = json.loads(data_json) if data_json else {}
        except json.JSONDecodeError:
            payload = {"raw": data_json}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        frames.append(SseFrame(id=event_id, event=event_name, data=payload))
    return frames


def read_sse(client: TestClient, url: str, *, frame_count: int, headers: dict[str, str]) -> list[SseFrame]:
    """Read a bounded number of SSE frames from the streaming endpoint."""
    with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200, response.text
        assert "text/event-stream" in (response.headers.get("content-type") or "")
        buf = ""
        frames: list[SseFrame] = []
        for chunk in response.iter_text():
            buf += chunk
            frames = _parse_sse_frames(buf)
            if len(frames) >= frame_count:
                return frames[:frame_count]
    return frames


class SkillEvalSseTests(unittest.TestCase):
    def _client(self) -> tuple[TestClient, Any, dict[str, str]]:
        session = make_session()
        client, headers, _settings = build_authenticated_skill_client(
            db=session,
            include_routers=[skill_eval_router],
        )
        self.addCleanup(session.close)
        self.addCleanup(client.close)
        self.addCleanup(restore_operator_settings)
        return client, session, headers

    def _seed_run_with_events(self, session: Any, *, count: int = 3) -> UUID:
        repo = EvaluationRepository(session)
        dataset = repo.create_dataset(
            stable_key=f"sse-ds-{uuid.uuid4().hex[:8]}",
            display_name="SSE Dataset",
            ownership="custom",
            actor="tester",
        )
        cases_snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "fixture_refs": [],
                "expected_mode": "unknown",
                "acceptable_skill_keys": [],
                "forbidden_skill_keys": [],
                "acceptable_capability_paths": [],
                "forbidden_side_effect_classes": [],
                "expect_completion": True,
                "assertion_json": {},
                "ceilings_json": {},
                "tags": [],
                "notes": "",
                "case_digest": DIGEST_A,
            }
        ]
        repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=cases_snapshot, actor="tester")
        published = repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
            actor="tester",
        )
        run = repo.create_run(
            subject_kind="skill_version",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_B,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="plan09-policy-v1",
            mode="dataset_scripted",
            isolation_namespace_id=uuid.uuid4(),
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest=DIGEST_C,
            evidence_provenance="structural_synthetic",
            actor_principal="operator-sse",
            request_id=f"sse-{uuid.uuid4().hex[:8]}",
        )
        session.flush()
        for i in range(count):
            run = repo.get_run(run.id)
            assert run is not None
            repo.append_event(
                eval_run_id=run.id,
                expected_run_revision=int(run.state_revision),
                event_type=f"step_{i + 1}",
                payload={"n": i + 1, "note": "safe"},
            )
        # Terminal so the stream can end after replay + heartbeat.
        # Allowed path: queued -> running -> completed.
        run = repo.get_run(run.id)
        assert run is not None
        run = repo.transition_run(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            to_status="running",
            lease_owner="sse-test-worker",
            lease_generation=1,
        )
        run = repo.transition_run(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            to_status="completed",
            gate_eligible=False,
        )
        session.commit()
        return run.id

    def test_sse_replays_after_sequence_and_heartbeats(self) -> None:
        client, session, headers = self._client()
        run_id = self._seed_run_with_events(session, count=3)
        frames = read_sse(
            client,
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/events/stream?afterSequence=1",
            frame_count=3,
            headers=headers,
        )
        self.assertEqual([frame.id for frame in frames[:2]], ["2", "3"])
        self.assertEqual(frames[2].event, "heartbeat")

    def test_sse_requires_principal(self) -> None:
        client, session, _headers = self._client()
        run_id = self._seed_run_with_events(session, count=1)
        bare = TestClient(client.app)
        r = bare.get(f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/events/stream?afterSequence=0")
        self.assertIn(r.status_code, {401, 403}, r.text)

    def test_dataset_publish_requires_revision_and_principal(self) -> None:
        client, session, _headers = self._client()
        repo = EvaluationRepository(session)
        dataset = repo.create_dataset(
            stable_key=f"pub-ds-{uuid.uuid4().hex[:8]}",
            display_name="Publish Auth Dataset",
            ownership="custom",
        )
        session.commit()
        bare = TestClient(client.app)
        response = bare.post(
            f"{PLAN09_EVAL_PREFIX}/datasets/{dataset.id}/publish",
            headers=origin_headers(),
            json={"requestId": "ds-1", "expectedRevision": 0},
        )
        self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()
