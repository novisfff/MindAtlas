"""Plan 09 evaluation HTTP — session principal, auth, and client-authored gate rejection."""

from __future__ import annotations

import base64
import json
import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.evaluation.router import (  # noqa: E402
    PLAN09_EVAL_PREFIX,
    skill_eval_router,
)
from app.assistant.skills.router import (  # noqa: E402
    main_agent_profile_router,
    skill_package_router,
)
from tests._db import make_session  # noqa: E402
from tests.operator_session_helpers import (  # noqa: E402
    build_authenticated_skill_client,
    csrf_headers,
    origin_headers,
)


def _digest(ch: str = "a") -> str:
    return (ch * 64)[:64]


class SkillEvalApiSessionTests(unittest.TestCase):
    def _client(self, *, extra_routers: list | None = None):
        session = make_session()
        routers = [skill_eval_router, skill_package_router]
        if extra_routers:
            routers.extend(extra_routers)
        client, headers, _settings = build_authenticated_skill_client(
            db=session,
            include_routers=routers,
        )
        from tests.operator_session_helpers import restore_operator_settings
        self.addCleanup(session.close)
        self.addCleanup(client.close)
        self.addCleanup(restore_operator_settings)
        return client, session, headers

    def test_openapi_includes_eval_routes(self) -> None:
        client, _session, _headers = self._client()
        paths = client.get("/openapi.json").json().get("paths") or {}
        eval_paths = [p for p in paths if PLAN09_EVAL_PREFIX in p or "/skill-eval" in p]
        self.assertGreaterEqual(len(eval_paths), 4)
        for path, methods in paths.items():
            if "/skill-eval" not in path:
                continue
            self.assertNotIn("delete", {m.lower() for m in methods.keys()})

    def test_eval_routes_require_session(self) -> None:
        client, _session, _headers = self._client()
        bare = TestClient(client.app)
        r = bare.get(f"{PLAN09_EVAL_PREFIX}/datasets")
        self.assertIn(r.status_code, {401, 403}, r.text)

        r2 = bare.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=origin_headers(),
            json={
                "requestId": f"auth-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "prompt": "hello",
                "locale": "en",
                "profileVersionId": str(uuid.uuid4()),
                "mode": "interactive_scripted",
                "datasetVersionIds": [],
            },
        )
        self.assertIn(r2.status_code, {401, 403}, r2.text)

    def test_client_authored_gate_decision_fields_rejected(self) -> None:
        client, _session, headers = self._client()
        body = {
            "requestId": str(uuid.uuid4()),
            "action": "skill_publish",
            "subjectAggregateId": str(uuid.uuid4()),
            "subjectVersionId": str(uuid.uuid4()),
            "qualifyingEvalRunIds": [str(uuid.uuid4())],
            "passed": True,
            "decision": "passed",
            "metrics": {"score": 1.0},
            "assertions": [{"code": "x", "passed": True}],
            "subject": {
                "catalogDigest": _digest("k"),
            },
        }
        r = client.post(f"{PLAN09_EVAL_PREFIX}/gates", json=body, headers=headers)
        self.assertIn(r.status_code, {422, 400}, r.text)
        self.assertNotIn('"decision":"passed"', r.text.replace(" ", ""))

    def test_gate_api_rejects_client_authored_subject(self) -> None:
        client, _session, headers = self._client()
        body = {
            "requestId": str(uuid.uuid4()),
            "action": "skill_publish",
            "subjectAggregateId": str(uuid.uuid4()),
            "subjectVersionId": str(uuid.uuid4()),
            "qualifyingEvalRunIds": [str(uuid.uuid4())],
            "subject": {"catalogDigest": "0" * 64},
        }
        r = client.post(f"{PLAN09_EVAL_PREFIX}/gates", json=body, headers=headers)
        self.assertEqual(r.status_code, 422, r.text)

    def _bootstrap_profile_version(self, client, headers: dict[str, str]) -> str:
        import copy

        from app.assistant.skills.schemas import default_main_agent_profile_snapshot

        profile = client.get(
            "/api/assistant-config/main-agent-profiles/default",
            headers=headers,
        )
        if profile.status_code != 200:
            return str(uuid.uuid4())
        data = profile.json()["data"]
        rev = int(data.get("aggregateRevision") or 0)
        snap = copy.deepcopy(default_main_agent_profile_snapshot().normalized_payload())
        snap["basePrompt"] = "eval admission profile"
        draft = client.put(
            "/api/assistant-config/main-agent-profiles/default/draft",
            headers=headers,
            json={
                "snapshot": snap,
                "requestId": f"prof-draft-{uuid.uuid4().hex[:8]}",
                "expectedAggregateRevision": rev,
            },
        )
        if draft.status_code != 200:
            return str(uuid.uuid4())
        return draft.json()["data"]["id"]

    def test_create_run_with_authenticated_session(self) -> None:
        client, session, headers = self._client(
            extra_routers=[main_agent_profile_router]
        )
        name = f"eval-run-{uuid.uuid4().hex[:8]}"
        skill_md = (
            f"---\nname: {name}\n"
            "description: Evaluation run admission regression package for Plan 09.\n"
            "---\n\n# Body\n"
        )
        mindatlas = (
            "version: 1\n"
            "display_name: Eval\n"
            "legacy_aliases: []\n\n"
            "routing:\n  include_examples: []\n  exclude_examples: []\n  conflict_rules: []\n\n"
            "capabilities: []\n\n"
            "policy:\n  allowed_side_effects:\n    - read\n    - compute\n"
            "  max_skill_calls: 16\n  max_same_read_calls: 3\n"
            "  requires_terminal_output: true\n  terminal_text_allowed: true\n\n"
            "provider_aliases: {}\n"
        )
        created = client.post(
            "/api/assistant-config/skill-packages",
            headers=headers,
            json={
                "skillMd": skill_md,
                "mindatlasYaml": mindatlas,
                "resources": [
                    {
                        "path": "references/a.md",
                        "contentBase64": base64.b64encode(b"a\n").decode("ascii"),
                    }
                ],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        pkg = created.json()["data"]
        draft = pkg["draftVersion"]
        profile_version_id = self._bootstrap_profile_version(client, headers)

        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": pkg["id"],
                "subjectVersionId": draft["id"],
                "prompt": "evaluate this skill draft",
                "locale": "en",
                "profileVersionId": profile_version_id,
                "mode": "interactive_scripted",
                "datasetVersionIds": [],
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["mode"], "interactive_scripted")
        run_id = data["id"]

        from app.assistant.evaluation.models import AssistantSkillEvalRun
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure
        from app.operator_auth.models import OperatorAccount

        session.expire_all()
        closure = resolve_skill_candidate_closure(
            session,
            package_id=uuid.UUID(pkg["id"]),
            version_id=uuid.UUID(draft["id"]),
            subject_kind="skill_draft",
        )
        run_row = session.get(AssistantSkillEvalRun, uuid.UUID(run_id))
        self.assertIsNotNone(run_row)
        assert run_row is not None
        self.assertEqual(run_row.subject_content_digest, closure.content_digest)
        self.assertEqual(run_row.subject_binding_digest, closure.binding_set_digest)
        account = session.query(OperatorAccount).one()
        self.assertEqual(run_row.actor_principal, str(account.id))
        self.assertNotEqual(run_row.subject_content_digest, _digest("3"))

        events = client.get(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/events",
            headers=headers,
            params={"afterSequence": 0},
        )
        self.assertEqual(events.status_code, 200, events.text)
        self.assertIn("items", events.json()["data"])

    def test_create_run_rejects_unknown_skill_version(self) -> None:
        client, _session, headers = self._client(
            extra_routers=[main_agent_profile_router]
        )
        profile_version_id = self._bootstrap_profile_version(client, headers)
        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "prompt": "missing skill",
                "locale": "en",
                "profileVersionId": profile_version_id,
                "mode": "interactive_scripted",
                "datasetVersionIds": [],
            },
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_create_run_rejects_client_digests(self) -> None:
        client, _session, headers = self._client()
        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "prompt": "x",
                "locale": "en",
                "profileVersionId": str(uuid.uuid4()),
                "mode": "interactive_scripted",
                "datasetVersionIds": [],
                "subjectContentDigest": _digest("3"),
                "actorPrincipal": "forged",
            },
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_dataset_scripted_requires_fixture_and_dataset(self) -> None:
        client, _session, headers = self._client()
        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "prompt": "x",
                "locale": "en",
                "profileVersionId": str(uuid.uuid4()),
                "mode": "dataset_scripted",
                "datasetVersionIds": [],
            },
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_unknown_fixture_revision_fails_closed(self) -> None:
        client, _session, headers = self._client()
        r = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs",
            headers=headers,
            json={
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "subjectKind": "skill_draft",
                "subjectAggregateId": str(uuid.uuid4()),
                "subjectVersionId": str(uuid.uuid4()),
                "prompt": "x",
                "locale": "en",
                "profileVersionId": str(uuid.uuid4()),
                "mode": "dataset_scripted",
                "datasetVersionIds": [str(uuid.uuid4())],
                "providerFixtureRevision": "opaque-unknown-fixture-rev",
            },
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("unknown providerFixtureRevision", r.text)

    def test_cancel_requires_request_id_and_expected_revision(self) -> None:
        client, session, headers = self._client()
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.evaluation.gates import current_build_revision
        from app.assistant.evaluation.orchestration import EVAL_POLICY_DIGEST
        from app.assistant.evaluation.repository import EvaluationRepository
        from app.operator_auth.models import OperatorAccount

        account = session.query(OperatorAccount).one()
        repo = EvaluationRepository(session)
        iso = uuid.uuid4()
        run = repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=_digest("1"),
            subject_binding_digest=_digest("2"),
            dataset_version_ids=[],
            threshold_policy_version="v1",
            mode="interactive_scripted",
            isolation_namespace_id=iso,
            runtime_contract_version=1,
            required_build_revision=current_build_revision(),
            isolation_digest=sha256_canonical_json({"iso": str(iso)}),
            policy_digest=EVAL_POLICY_DIGEST,
            evidence_provenance="structural_synthetic",
            actor_principal=str(account.id),
            request_id=f"create-{uuid.uuid4().hex[:8]}",
        )
        session.commit()
        run_id = str(run.id)
        rev = int(run.state_revision)

        missing = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/cancel",
            headers=headers,
        )
        self.assertEqual(missing.status_code, 422, missing.text)

        partial = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/cancel",
            headers=headers,
            json={"requestId": "cancel-only-req"},
        )
        self.assertEqual(partial.status_code, 422, partial.text)

        stale = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/cancel",
            headers=headers,
            json={
                "requestId": "cancel-stale",
                "expectedStateRevision": rev + 99,
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        ok = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/cancel",
            headers=csrf_headers(client),
            json={
                "requestId": "cancel-ok",
                "expectedStateRevision": rev,
            },
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["data"]["status"], "cancelling")

        retry = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/cancel",
            headers=csrf_headers(client),
            json={
                "requestId": "cancel-ok",
                "expectedStateRevision": rev,
            },
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["data"]["status"], "cancelling")
        self.assertEqual(
            retry.json()["data"]["stateRevision"],
            ok.json()["data"]["stateRevision"],
        )

        altered = client.post(
            f"{PLAN09_EVAL_PREFIX}/runs/{run_id}/cancel",
            headers=csrf_headers(client),
            json={
                "requestId": "cancel-ok",
                "expectedStateRevision": rev + 1,
            },
        )
        self.assertEqual(altered.status_code, 409, altered.text)

    def test_dataset_publish_requires_principal(self) -> None:
        client, session, _headers = self._client()
        from app.assistant.evaluation.repository import EvaluationRepository

        repo = EvaluationRepository(session)
        ds = repo.create_dataset(
            stable_key=f"api-ds-{uuid.uuid4().hex[:8]}",
            display_name="API DS",
            ownership="custom",
        )
        session.commit()
        bare = TestClient(client.app)
        r = bare.post(
            f"{PLAN09_EVAL_PREFIX}/datasets/{ds.id}/publish",
            headers=origin_headers(),
            json={"requestId": "ds-1", "expectedRevision": 0},
        )
        self.assertEqual(r.status_code, 401, r.text)


class SkillEvalRouterContractTests(unittest.TestCase):
    def test_router_prefix_and_route_count(self) -> None:
        self.assertEqual(PLAN09_EVAL_PREFIX, "/api/assistant-config/skill-eval")
        self.assertGreaterEqual(len(skill_eval_router.routes), 12)

    def test_create_eval_run_body_forbids_client_digests(self) -> None:
        from app.assistant.evaluation.schemas import CreateEvalRunBody
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            CreateEvalRunBody.model_validate(
                {
                    "requestId": "r1",
                    "subjectKind": "skill_draft",
                    "subjectAggregateId": str(uuid.uuid4()),
                    "subjectVersionId": str(uuid.uuid4()),
                    "prompt": "p",
                    "locale": "en",
                    "profileVersionId": str(uuid.uuid4()),
                    "mode": "interactive_scripted",
                    "datasetVersionIds": [],
                    "subjectContentDigest": _digest("x"),
                }
            )

    def test_cancel_body_requires_cas_fields(self) -> None:
        from app.assistant.evaluation.schemas import CancelEvalRunBody
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            CancelEvalRunBody.model_validate({})
        with self.assertRaises(ValidationError):
            CancelEvalRunBody.model_validate({"requestId": "only"})
        with self.assertRaises(ValidationError):
            CancelEvalRunBody.model_validate({"expectedStateRevision": 0})
        body = CancelEvalRunBody.model_validate(
            {"requestId": "c1", "expectedStateRevision": 0}
        )
        self.assertEqual(body.request_id, "c1")
        self.assertEqual(body.expected_state_revision, 0)


if __name__ == "__main__":
    unittest.main()
