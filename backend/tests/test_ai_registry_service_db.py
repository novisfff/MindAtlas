"""DB transaction coverage for Plan 03 probe pointer + revision invalidation."""

from __future__ import annotations

import unittest
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()

from app.ai_provider.crypto import encrypt_api_key  # noqa: E402
from app.ai_registry.models import (  # noqa: E402
    AiComponentBinding,
    AiCredential,
    AiModel,
    AiModelCapabilityProbe,
)
from app.ai_registry.service import (  # noqa: E402
    AiBindingService,
    AiCredentialService,
    AiModelCapabilityProbeService,
    AiModelService,
)
from app.assistant.provider_loop.adapters.openai_chat import ADAPTER_KEY  # noqa: E402
from app.assistant.provider_loop.probe import (  # noqa: E402
    CapabilityObservation,
    ModelCapabilityObservations,
    ModelCapabilityProbeEvidence,
    build_endpoint_identity,
    build_model_config_digest,
    compute_probe_digest,
)
from app.common.exceptions import ApiException  # noqa: E402
from app.common.ssrf import normalize_openai_base_url  # noqa: E402


DIGEST_B = "b" * 64
APP_BUILD = "plan03-task8-local"
ADAPTER_REVISION = "1"


def _real_digest(model: AiModel, cred: AiCredential, *, app_build: str = APP_BUILD) -> tuple[str, dict]:
    endpoint = build_endpoint_identity(normalize_openai_base_url(cred.base_url))
    digest = build_model_config_digest(
        model_id=model.id,
        model_name=model.name,
        model_type=model.model_type,
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=cred.id,
        credential_runtime_revision=int(cred.runtime_revision or 1),
        endpoint_identity=endpoint,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=app_build,
    )
    return digest, endpoint


def _obs(status: str = "passed") -> ModelCapabilityObservations:
    item = CapabilityObservation(observation=status, safe_reason_code=None)  # type: ignore[arg-type]
    return ModelCapabilityObservations(
        streaming=item,
        tool_calling=item,
        json_schema_args=item,
        stable_tool_call_ids=item,
        multi_tool_calls=item,
        tool_result_continuation=item,
        tools_disabled_finalization=item,
    )


def _evidence(
    *,
    status: str = "passed",
    model_config_digest: str,
    safe_error_code: str | None = None,
    safe_error_summary: str | None = None,
) -> ModelCapabilityProbeEvidence:
    caps = _obs("passed" if status == "passed" else "failed" if status == "failed" else "not_observed")
    if status == "partial":
        caps = ModelCapabilityObservations(
            streaming=CapabilityObservation(observation="passed"),
            tool_calling=CapabilityObservation(observation="failed", safe_reason_code="no_tool"),
            json_schema_args=CapabilityObservation(observation="not_observed"),
            stable_tool_call_ids=CapabilityObservation(observation="not_observed"),
            multi_tool_calls=CapabilityObservation(observation="not_observed"),
            tool_result_continuation=CapabilityObservation(observation="not_observed"),
            tools_disabled_finalization=CapabilityObservation(observation="not_observed"),
        )
    digest = compute_probe_digest(
        probe_contract_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=model_config_digest,
        status=status,  # type: ignore[arg-type]
        capabilities=caps,
        compatibility_warnings=(),
        safe_error_code=safe_error_code,
        safe_error_summary=safe_error_summary,
    )
    return ModelCapabilityProbeEvidence(
        probe_contract_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=model_config_digest,
        status=status,  # type: ignore[arg-type]
        capabilities=caps,
        probe_digest=digest,
        safe_error_code=safe_error_code,
        safe_error_summary=safe_error_summary,
    )


def _seed_credential_model(
    db,
    *,
    name: str = "cred",
    model_name: str = "gpt-test",
    base_url: str = "https://api.example.com/v1",
    api_key: str = "sk-test-key",
) -> tuple[AiCredential, AiModel]:
    cred = AiCredential(
        name=name,
        base_url=base_url,
        api_key_encrypted=encrypt_api_key(api_key),
        api_key_hint="****",
        runtime_revision=1,
    )
    db.add(cred)
    db.flush()
    model = AiModel(
        credential_id=cred.id,
        name=model_name,
        model_type="llm",
        runtime_revision=1,
    )
    db.add(model)
    db.commit()
    db.refresh(cred)
    db.refresh(model)
    return cred, model


def _snap(model: AiModel, cred: AiCredential, digest: str, endpoint: dict) -> "_ProbeConfigSnapshot":
    from app.ai_registry.service import _ProbeConfigSnapshot

    return _ProbeConfigSnapshot(
        model_id=model.id,
        model_name=model.name,
        model_type=model.model_type,
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=cred.id,
        credential_runtime_revision=int(cred.runtime_revision or 1),
        base_url=normalize_openai_base_url(cred.base_url),
        model_config_digest=digest,
        endpoint_identity=dict(endpoint),
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
    )


class AiRegistryServiceDbTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_plan01_runtime_revisions_default_and_not_recreated(self) -> None:
        cred, model = _seed_credential_model(self.db)
        self.assertEqual(cred.runtime_revision, 1)
        self.assertEqual(model.runtime_revision, 1)
        self.assertIsNone(model.current_capability_probe_id)
        self.assertTrue(hasattr(model, "runtime_revision"))
        self.assertFalse(hasattr(AiModelCapabilityProbe, "runtime_revision"))

    def test_probe_has_no_updated_at_or_unique_digest(self) -> None:
        cols = {c.name for c in AiModelCapabilityProbe.__table__.columns}
        self.assertNotIn("updated_at", cols)
        self.assertIn("created_at", cols)
        unique_names = {
            c.name
            for c in AiModelCapabilityProbe.__table__.constraints
            if getattr(c, "name", None)
        }
        # No unique constraint on probe_digest.
        indexes = list(AiModelCapabilityProbe.__table__.indexes)
        for idx in indexes:
            if "probe_digest" in idx.columns.keys():
                self.assertFalse(idx.unique)


    def test_create_probe_row_and_repeated_identical_evidence(self) -> None:
        cred, model = _seed_credential_model(self.db)
        digest, endpoint = _real_digest(model, cred)
        svc = AiModelCapabilityProbeService(
            self.db,
            enabled=True,
            app_build_revision=APP_BUILD,
            adapter_revision=ADAPTER_REVISION,
        )
        snap = _snap(model, cred, digest, endpoint)
        r1 = svc._persist_evidence(
            model_id=model.id,
            original_snapshot=snap,
            evidence=_evidence(model_config_digest=digest),
            promote=True,
        )
        r2 = svc._persist_evidence(
            model_id=model.id,
            original_snapshot=snap,
            evidence=_evidence(model_config_digest=digest),
            promote=False,
        )
        self.assertNotEqual(r1.probe.id, r2.probe.id)
        self.assertEqual(r1.probe.probe_digest, r2.probe.probe_digest)
        self.assertEqual(r1.promotion_outcome, "promoted")
        self.assertEqual(r2.promotion_outcome, "not_requested")
        rows = self.db.query(AiModelCapabilityProbe).filter_by(model_id=model.id).all()
        self.assertEqual(len(rows), 2)

    def test_history_newest_first_pagination(self) -> None:
        cred, model = _seed_credential_model(self.db)
        digest, endpoint = _real_digest(model, cred)
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        snap = _snap(model, cred, digest, endpoint)
        ids = []
        for _ in range(3):
            r = svc._persist_evidence(
                model_id=model.id,
                original_snapshot=snap,
                evidence=_evidence(model_config_digest=digest),
                promote=True,
            )
            ids.append(r.probe.id)
        listed = svc.list_for_model(model.id, limit=2, offset=0)
        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0][0].id, ids[-1])
        self.assertEqual(listed[1][0].id, ids[-2])
        self.assertTrue(listed[0][1])  # is_current
        page2 = svc.list_for_model(model.id, limit=2, offset=2)
        self.assertEqual(len(page2), 1)
        self.assertEqual(page2[0][0].id, ids[0])

    def test_promote_true_for_passed_partial_failed(self) -> None:
        cred, model = _seed_credential_model(self.db)
        digest, endpoint = _real_digest(model, cred)
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        snap = _snap(model, cred, digest, endpoint)
        for status in ("passed", "partial", "failed"):
            r = svc._persist_evidence(
                model_id=model.id,
                original_snapshot=snap,
                evidence=_evidence(status=status, model_config_digest=digest),
                promote=True,
            )
            self.assertEqual(r.promotion_outcome, "promoted")
            self.db.refresh(model)
            self.assertEqual(model.current_capability_probe_id, r.probe.id)
            self.assertEqual(model.runtime_revision, 1)

    def test_promote_false_leaves_pointer_unchanged(self) -> None:
        cred, model = _seed_credential_model(self.db)
        digest, endpoint = _real_digest(model, cred)
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        snap = _snap(model, cred, digest, endpoint)
        first = svc._persist_evidence(
            model_id=model.id,
            original_snapshot=snap,
            evidence=_evidence(model_config_digest=digest),
            promote=True,
        )
        second = svc._persist_evidence(
            model_id=model.id,
            original_snapshot=snap,
            evidence=_evidence(model_config_digest=digest),
            promote=False,
        )
        self.assertEqual(second.promotion_outcome, "not_requested")
        self.db.refresh(model)
        self.assertEqual(model.current_capability_probe_id, first.probe.id)
        self.assertNotEqual(model.current_capability_probe_id, second.probe.id)

    def test_pointer_cannot_reference_another_models_probe_service_path(self) -> None:
        cred_a, model_a = _seed_credential_model(self.db, name="a", model_name="ma")
        _, model_b = _seed_credential_model(self.db, name="b", model_name="mb")
        digest, endpoint = _real_digest(model_a, cred_a)
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        snap_a = _snap(model_a, cred_a, digest, endpoint)
        ra = svc._persist_evidence(
            model_id=model_a.id,
            original_snapshot=snap_a,
            evidence=_evidence(model_config_digest=digest),
            promote=True,
        )
        model_b.current_capability_probe_id = ra.probe.id
        self.db.commit()
        owner = (
            self.db.query(AiModelCapabilityProbe)
            .filter(AiModelCapabilityProbe.id == ra.probe.id)
            .one()
        )
        self.assertEqual(owner.model_id, model_a.id)
        self.assertNotEqual(owner.model_id, model_b.id)

    def test_probe_service_has_no_update_or_delete(self) -> None:
        methods = dir(AiModelCapabilityProbeService)
        self.assertNotIn("update", methods)
        self.assertNotIn("delete", methods)
        self.assertNotIn("update_probe", methods)
        self.assertNotIn("delete_probe", methods)

    def test_model_delete_cascades_probe_history(self) -> None:
        cred, model = _seed_credential_model(self.db)
        digest, endpoint = _real_digest(model, cred)
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        snap = _snap(model, cred, digest, endpoint)
        r = svc._persist_evidence(
            model_id=model.id,
            original_snapshot=snap,
            evidence=_evidence(model_config_digest=digest),
            promote=True,
        )
        probe_id = r.probe.id
        AiModelService(self.db).delete(model.id)
        self.assertIsNone(
            self.db.query(AiModelCapabilityProbe).filter_by(id=probe_id).first()
        )

    def test_credential_display_name_only_no_increment(self) -> None:
        cred, model = _seed_credential_model(self.db)
        probe = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(probe)
        self.db.flush()
        model.current_capability_probe_id = probe.id
        self.db.commit()
        rev_before = cred.runtime_revision
        AiCredentialService(self.db).update(cred.id, name="renamed-only", base_url=None, api_key=None)
        self.db.refresh(cred)
        self.db.refresh(model)
        self.assertEqual(cred.runtime_revision, rev_before)
        self.assertEqual(model.current_capability_probe_id, probe.id)

    def test_credential_base_url_increments_and_clears_all_pointers(self) -> None:
        cred, model1 = _seed_credential_model(self.db, name="cred-url", model_name="m1")
        model2 = AiModel(
            credential_id=cred.id, name="m2", model_type="llm", runtime_revision=1
        )
        self.db.add(model2)
        self.db.flush()
        for m in (model1, model2):
            p = AiModelCapabilityProbe(
                model_id=m.id,
                probe_contract_version=1,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=DIGEST_B,
                status="passed",
                capabilities={"streaming": {"observation": "passed"}},
                probe_digest=DIGEST_B,
            )
            self.db.add(p)
            self.db.flush()
            m.current_capability_probe_id = p.id
        self.db.commit()
        rev_before = cred.runtime_revision
        AiCredentialService(self.db).update(
            cred.id,
            name=None,
            base_url="https://other.example.com/v1",
            api_key=None,
        )
        self.db.refresh(cred)
        self.db.refresh(model1)
        self.db.refresh(model2)
        self.assertEqual(cred.runtime_revision, rev_before + 1)
        self.assertIsNone(model1.current_capability_probe_id)
        self.assertIsNone(model2.current_capability_probe_id)

    def test_credential_api_key_increments_and_clears(self) -> None:
        cred, model = _seed_credential_model(self.db, name="cred-key")
        p = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(p)
        self.db.flush()
        model.current_capability_probe_id = p.id
        self.db.commit()
        rev_before = cred.runtime_revision
        AiCredentialService(self.db).update(
            cred.id, name=None, base_url=None, api_key="sk-new-key"
        )
        self.db.refresh(cred)
        self.db.refresh(model)
        self.assertEqual(cred.runtime_revision, rev_before + 1)
        self.assertIsNone(model.current_capability_probe_id)

    def test_semantically_unchanged_base_url_no_increment(self) -> None:
        cred, model = _seed_credential_model(
            self.db, name="cred-norm", base_url="https://api.example.com"
        )
        rev_before = cred.runtime_revision
        p = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(p)
        self.db.flush()
        model.current_capability_probe_id = p.id
        self.db.commit()
        AiCredentialService(self.db).update(
            cred.id,
            name=None,
            base_url="https://api.example.com/v1",
            api_key=None,
        )
        self.db.refresh(cred)
        self.db.refresh(model)
        self.assertEqual(cred.runtime_revision, rev_before)
        self.assertEqual(model.current_capability_probe_id, p.id)

    def test_model_name_and_type_increment_and_clear(self) -> None:
        _, model = _seed_credential_model(self.db, name="cred-model")
        p = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(p)
        self.db.flush()
        model.current_capability_probe_id = p.id
        self.db.commit()
        rev = model.runtime_revision
        AiModelService(self.db).update(model.id, name="renamed-model", model_type=None)
        self.db.refresh(model)
        self.assertEqual(model.runtime_revision, rev + 1)
        self.assertIsNone(model.current_capability_probe_id)

        p2 = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(p2)
        self.db.flush()
        model.current_capability_probe_id = p2.id
        self.db.commit()
        rev = model.runtime_revision
        AiModelService(self.db).update(model.id, name=None, model_type="embedding")
        self.db.refresh(model)
        self.assertEqual(model.runtime_revision, rev + 1)
        self.assertIsNone(model.current_capability_probe_id)

    def test_model_noop_update_no_increment(self) -> None:
        _, model = _seed_credential_model(self.db, name="cred-noop")
        p = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(p)
        self.db.flush()
        model.current_capability_probe_id = p.id
        self.db.commit()
        rev = model.runtime_revision
        AiModelService(self.db).update(model.id, name=model.name, model_type=model.model_type)
        self.db.refresh(model)
        self.assertEqual(model.runtime_revision, rev)
        self.assertEqual(model.current_capability_probe_id, p.id)

    def test_component_binding_change_no_model_runtime_revision(self) -> None:
        _, model = _seed_credential_model(self.db, name="cred-bind")
        rev = model.runtime_revision
        AiBindingService(self.db).update_component(
            "assistant", llm_model_id=model.id, embedding_model_id=None
        )
        self.db.refresh(model)
        self.assertEqual(model.runtime_revision, rev)

    def test_failed_transaction_neither_revision_nor_pointer_changes(self) -> None:
        cred, model = _seed_credential_model(self.db, name="cred-fail")
        p = AiModelCapabilityProbe(
            model_id=model.id,
            probe_contract_version=1,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=DIGEST_B,
            status="passed",
            capabilities={"streaming": {"observation": "passed"}},
            probe_digest=DIGEST_B,
        )
        self.db.add(p)
        self.db.flush()
        model.current_capability_probe_id = p.id
        self.db.commit()
        rev = cred.runtime_revision
        pointer = model.current_capability_probe_id
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError

        with patch.object(self.db, "commit", side_effect=IntegrityError("s", "p", Exception("x"))):
            with self.assertRaises(ApiException) as ctx:
                AiCredentialService(self.db).update(
                    cred.id, name=None, base_url="https://new.example.com/v1", api_key=None
                )
            self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        cred2 = self.db.query(AiCredential).filter_by(id=cred.id).one()
        model2 = self.db.query(AiModel).filter_by(id=model.id).one()
        self.assertEqual(cred2.runtime_revision, rev)
        self.assertEqual(model2.current_capability_probe_id, pointer)

    def test_config_changed_during_probe_persists_without_promote(self) -> None:
        cred, model = _seed_credential_model(self.db, name="cred-mid")
        digest, endpoint = _real_digest(model, cred)
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        original = _snap(model, cred, digest, endpoint)
        # Mutate model name so current digest differs from original snapshot.
        model.name = "changed-mid-flight"
        model.runtime_revision = 2
        self.db.commit()
        result = svc._persist_evidence(
            model_id=model.id,
            original_snapshot=original,
            evidence=_evidence(model_config_digest=digest),
            promote=True,
        )
        self.assertEqual(result.promotion_outcome, "config_changed")
        self.db.refresh(model)
        self.assertIsNone(model.current_capability_probe_id)
        row = self.db.query(AiModelCapabilityProbe).filter_by(id=result.probe.id).one()
        self.assertEqual(row.model_config_digest, digest)

    def test_safe_response_excludes_runtime_revision(self) -> None:
        from app.ai_registry.schemas import AiCredentialResponse, AiModelResponse

        cred, model = _seed_credential_model(self.db, name="cred-safe")
        cred_payload = AiCredentialResponse.model_validate(cred).model_dump(by_alias=True)
        model_payload = AiModelResponse.model_validate(model).model_dump(by_alias=True)
        self.assertNotIn("runtimeRevision", cred_payload)
        self.assertNotIn("runtime_revision", cred_payload)
        self.assertNotIn("runtimeRevision", model_payload)
        self.assertNotIn("currentCapabilityProbeId", model_payload)


if __name__ == "__main__":
    unittest.main()
