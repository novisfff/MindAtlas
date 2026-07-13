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


if __name__ == "__main__":
    unittest.main()
