"""Dependency-closure focused cases for skill publication (Plan 01 Task 5)."""

from __future__ import annotations

import os
import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-c25d03f")
os.environ.setdefault("APP_ENV", "test")


class DependencyClosureFocusedTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from app.assistant_config.models import AssistantWorkflow

        self.db = make_session()

        def _boom(self):  # noqa: ANN001
            raise AssertionError("AssistantWorkflow.graph_snapshot must not be accessed")

        self._orig = AssistantWorkflow.graph_snapshot
        AssistantWorkflow.graph_snapshot = property(_boom)  # type: ignore[assignment]

    def tearDown(self) -> None:
        from app.assistant_config.models import AssistantWorkflow

        AssistantWorkflow.graph_snapshot = self._orig  # type: ignore[assignment]
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()

    def test_custom_model_path_and_stable_ordering(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_agent,
        )
        from app.ai_registry.models import AiModel
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver

        cred, _default_model, _binding = create_default_model_binding(self.db)
        custom = AiModel(
            credential_id=cred.id,
            name="custom-llm",
            model_type="llm",
            runtime_revision=1,
        )
        self.db.add(custom)
        self.db.flush()
        create_published_agent(
            self.db,
            name="custom_model_agent",
            tools=["search_entries", "list_tags"],
            model_source="custom",
            model_id=custom.id,
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        binding = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key="custom_model_agent",
                    contract=CapabilityBindingContract(
                        input_schema={"type": "object", "properties": {}},
                        output_schema={"type": "string"},
                    ),
                ),
            )
        )[0]
        model_dep = next(d for d in binding.dependencies if d.dependency_type == "model")
        self.assertEqual(model_dep.resolved_model_id, custom.id)
        paths = [d.dependency_path for d in binding.dependencies]
        self.assertEqual(paths, sorted(paths))
        # Re-resolve is deterministic.
        again = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key="custom_model_agent",
                    contract=CapabilityBindingContract(
                        input_schema={"type": "object", "properties": {}},
                        output_schema={"type": "string"},
                    ),
                ),
            )
        )[0]
        self.assertEqual(binding.dependency_closure_digest, again.dependency_closure_digest)
        self.assertEqual(binding.binding_contract_digest, again.binding_contract_digest)

    def test_invalid_custom_model_type_fails(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_agent,
        )
        from app.ai_registry.models import AiModel
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        cred, _, _ = create_default_model_binding(self.db)
        embedding = AiModel(
            credential_id=cred.id,
            name="embed-1",
            model_type="embedding",
            runtime_revision=1,
        )
        self.db.add(embedding)
        self.db.flush()
        create_published_agent(
            self.db,
            name="bad_custom_agent",
            tools=["search_entries"],
            model_source="custom",
            model_id=embedding.id,
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many(
                (
                    CapabilityDeclaration(
                        type="agent",
                        key="bad_custom_agent",
                        contract=CapabilityBindingContract(
                            input_schema={"type": "object", "properties": {}},
                            output_schema={"type": "string"},
                        ),
                    ),
                )
            )
        self.assertEqual(ctx.exception.code, 42293)

    def test_workflow_kb_paths_freeze_lightrag_embedding(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        create_default_model_binding(self.db)
        create_published_workflow(
            self.db,
            name="kb_wf",
            knowledge_retrieval=True,
            agent_knowledge_enabled=True,
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many((CapabilityDeclaration(type="workflow", key="kb_wf"),))
        self.assertEqual(ctx.exception.code, 42293)
        self.assertEqual(ctx.exception.details.get("reason"), "default_model_unbound")
        self.assertEqual(ctx.exception.details.get("component"), "lightrag")

        _, embed_model, _ = create_default_model_binding(
            self.db,
            component="lightrag",
            model_name="embed-test",
            model_type="embedding",
        )
        self.db.commit()
        binding = resolver.resolve_many(
            (CapabilityDeclaration(type="workflow", key="kb_wf"),)
        )[0]
        kb_model_deps = [
            d
            for d in binding.dependencies
            if d.dependency_type == "model" and d.dependency_path.endswith("/kb/model")
        ]
        self.assertGreaterEqual(len(kb_model_deps), 2)
        for dep in kb_model_deps:
            self.assertEqual(dep.resolved_model_id, embed_model.id)
            self.assertEqual(dep.resolution_snapshot.get("modelType"), "embedding")

    def test_nested_workflow_dependency_uses_derived_contract_schemas(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.assistant_config.schemas import WorkflowInput
        from app.assistant_config.workflow_contracts import workflow_contract_from_input

        create_default_model_binding(self.db)
        nested, nested_version = create_published_workflow(
            self.db,
            name="nested_contract_wf",
            tool_names=["search_entries"],
        )
        create_published_workflow(
            self.db,
            name="parent_contract_wf",
            nested_calls=[
                {
                    "target_workflow_id": str(nested.id),
                    "binding_mode": "pinned",
                    "target_published_version_id": str(nested_version.id),
                }
            ],
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        binding = resolver.resolve_many(
            (CapabilityDeclaration(type="workflow", key="parent_contract_wf"),)
        )[0]
        nested_dep = next(d for d in binding.dependencies if d.dependency_type == "workflow")
        nested_input = WorkflowInput.model_validate(
            {
                "nodes": nested_version.snapshot.get("nodes") or [],
                "edges": nested_version.snapshot.get("edges") or [],
                "viewport": nested_version.snapshot.get("viewport"),
            }
        )
        contract = workflow_contract_from_input(nested_input)
        expected_input = normalize_binding_schema(contract.input_schema, require_object_root=True)
        expected_output = normalize_binding_schema(contract.output_schema, require_object_root=False)
        dummy_digest = sha256_canonical_json({"type": "object"})
        self.assertEqual(nested_dep.input_schema_digest, binding_schema_digest(expected_input))
        self.assertEqual(nested_dep.output_schema_digest, binding_schema_digest(expected_output))
        self.assertNotEqual(nested_dep.input_schema_digest, dummy_digest)
        self.assertEqual(nested_dep.input_schema, expected_input)
        self.assertEqual(nested_dep.output_schema, expected_output)
        self.assertEqual(
            nested_dep.resolution_snapshot.get("inputSchemaDigest"),
            nested_dep.input_schema_digest,
        )


if __name__ == "__main__":
    unittest.main()
