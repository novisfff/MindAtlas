"""Publish-time capability resolution and dependency-closure tests (Plan 01 Task 5)."""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

# Fixed revision for code-native tool publication tests.
os.environ.setdefault("APP_BUILD_REVISION", "test-build-c25d03f")
os.environ.setdefault("APP_ENV", "test")


def _minimal_skill_md(name: str = "weekly-review") -> bytes:
    description = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    )
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n"
    ).encode("utf-8")


def _mindatlas_yaml(capabilities: str) -> bytes:
    return (
        "version: 1\n"
        "display_name: Test Skill\n"
        "legacy_aliases: []\n"
        "routing:\n"
        "  include_examples: []\n"
        "  exclude_examples: []\n"
        "  conflict_rules: []\n"
        "capabilities:\n"
        f"{capabilities}"
        "policy:\n"
        "  allowed_side_effects:\n"
        "    - read\n"
        "  max_skill_calls: 8\n"
        "  max_same_read_calls: 2\n"
        "  requires_terminal_output: false\n"
        "  terminal_text_allowed: true\n"
        "provider_aliases: {}\n"
        "metadata: {}\n"
    ).encode("utf-8")


def _parse(name: str, capabilities: str):
    from app.assistant.skills.package_io import parse_skill_directory_files

    return parse_skill_directory_files(
        {
            "SKILL.md": _minimal_skill_md(name=name),
            "mindatlas.yaml": _mindatlas_yaml(capabilities),
        },
        expected_root_name=None,
    )


class CapabilityResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session

        self.db = make_session()
        # Draft leakage tripwire.
        from app.assistant_config.models import AssistantWorkflow

        def _boom(self):  # noqa: ANN001
            raise AssertionError("AssistantWorkflow.graph_snapshot must not be accessed")

        self._orig_graph_snapshot = AssistantWorkflow.graph_snapshot
        AssistantWorkflow.graph_snapshot = property(_boom)  # type: ignore[assignment]

    def tearDown(self) -> None:
        from app.assistant_config.models import AssistantWorkflow

        AssistantWorkflow.graph_snapshot = self._orig_graph_snapshot  # type: ignore[assignment]
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()

    def test_system_tool_resolution_freezes_registry_schemas_and_build_revision(self) -> None:
        from app.assistant.domain.json_schema import binding_schema_digest
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import (
            CapabilityReferenceResolver,
            compute_system_tool_contract_set_digest,
            system_tool_schemas,
        )

        resolver = CapabilityReferenceResolver(self.db)
        resolved = resolver.resolve_many(
            (CapabilityDeclaration(type="tool", key="search_entries"),)
        )
        self.assertEqual(len(resolved), 1)
        binding = resolved[0]
        self.assertEqual(binding.target_identity, "system-tool:search_entries")
        self.assertIsNone(binding.target_id)
        self.assertIsNone(binding.resolved_tool_id)
        self.assertEqual(binding.executable_revision, "test-build-c25d03f")
        input_schema, output_schema = system_tool_schemas("search_entries")
        self.assertEqual(binding.input_schema_digest, binding_schema_digest(input_schema))
        self.assertEqual(binding.output_schema_digest, binding_schema_digest(output_schema))
        self.assertEqual(
            binding.config_digest,
            compute_system_tool_contract_set_digest(),
        )
        # No secrets.
        blob = str(binding.resolution_snapshot)
        self.assertNotIn("api_key", blob.lower())
        self.assertNotIn("authorization", blob.lower())

    def test_missing_tool_returns_42293(self) -> None:
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many(
                (CapabilityDeclaration(type="tool", key="definitely_missing_tool"),)
            )
        self.assertEqual(ctx.exception.code, 42293)

    def test_workflow_without_published_version_fails(self) -> None:
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.assistant_config.models import AssistantWorkflow
        from app.common.exceptions import ApiException

        workflow = AssistantWorkflow(
            name="unpublished_wf",
            description="",
            enabled=True,
            is_system=False,
        )
        self.db.add(workflow)
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many(
                (CapabilityDeclaration(type="workflow", key="unpublished_wf"),)
            )
        self.assertEqual(ctx.exception.code, 42293)

    def test_workflow_resolution_uses_published_snapshot_not_draft(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver

        create_default_model_binding(self.db)
        workflow, version = create_published_workflow(
            self.db,
            name="published_wf",
            tool_names=["search_entries"],
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        resolved = resolver.resolve_many(
            (CapabilityDeclaration(type="workflow", key="published_wf"),)
        )
        binding = resolved[0]
        self.assertEqual(binding.resolved_workflow_version_id, version.id)
        self.assertEqual(binding.target_id, workflow.id)
        self.assertTrue(
            any(d.dependency_type == "system_tool" for d in binding.dependencies)
        )
        self.assertTrue(any(d.dependency_type == "model" for d in binding.dependencies))

    def test_agent_resolution_requires_contract_and_queries_owned_version(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_agent,
        )
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        create_default_model_binding(self.db)
        agent, version = create_published_agent(
            self.db,
            name="published_agent",
            tools=["search_entries"],
            model_source="default",
        )
        self.db.commit()

        # Missing contract is rejected by CapabilityDeclaration itself (publish parse path).
        with self.assertRaises(Exception):
            CapabilityDeclaration(type="agent", key="published_agent")

        resolver = CapabilityReferenceResolver(self.db)
        resolved = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key="published_agent",
                    contract=CapabilityBindingContract(
                        input_schema={"type": "object", "properties": {}},
                        output_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                    ),
                ),
            )
        )
        binding = resolved[0]
        self.assertEqual(binding.resolved_agent_version_id, version.id)
        self.assertEqual(binding.target_id, agent.id)
        self.assertTrue(any(d.dependency_type == "system_tool" for d in binding.dependencies))
        self.assertTrue(any(d.dependency_type == "model" for d in binding.dependencies))

    def test_remote_tool_requires_output_schema_and_omits_secrets(self) -> None:
        from tests.agent_skill_test_support import create_remote_tool
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        tool = create_remote_tool(self.db, name="remote_search")
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many(
                (CapabilityDeclaration(type="tool", key="remote_search"),)
            )
        self.assertEqual(ctx.exception.code, 42293)

        resolved = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="tool",
                    key="remote_search",
                    contract=CapabilityBindingContract(
                        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
                    ),
                ),
            )
        )
        binding = resolved[0]
        self.assertEqual(binding.resolved_tool_id, tool.id)
        self.assertEqual(binding.resolved_revision, 1)
        blob = str(binding.resolution_snapshot)
        self.assertNotIn("super-secret-header-value", blob)
        self.assertNotIn("secret-query", blob)
        self.assertNotIn("enc-remote-key", blob)
        self.assertNotIn("{{query}}", blob)

    def test_binding_resolution_sorted_and_deterministic(self) -> None:
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver

        resolver = CapabilityReferenceResolver(self.db)
        first = resolver.resolve_many(
            (
                CapabilityDeclaration(type="tool", key="list_tags"),
                CapabilityDeclaration(type="tool", key="search_entries"),
            )
        )
        second = resolver.resolve_many(
            (
                CapabilityDeclaration(type="tool", key="search_entries"),
                CapabilityDeclaration(type="tool", key="list_tags"),
            )
        )
        self.assertEqual(
            [(b.capability_type, b.capability_key) for b in first],
            [(b.capability_type, b.capability_key) for b in second],
        )
        self.assertEqual(first[0].binding_contract_digest, second[0].binding_contract_digest)

    def test_non_immutable_build_revision_fails_outside_dev(self) -> None:
        from app.assistant.skills.resolution import require_immutable_app_build_revision
        from app.common.exceptions import ApiException

        with self.assertRaises(ApiException) as ctx:
            require_immutable_app_build_revision(
                app_env="production",
                app_build_revision="development",
            )
        self.assertEqual(ctx.exception.code, 42293)
        self.assertEqual(ctx.exception.details.get("reason"), "non_immutable_build_revision")


class PublishLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService

        self.db = make_session()
        self.svc = AgentSkillService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()

    def _create_package(self, name: str, capabilities: str):
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        parsed = _parse(name, capabilities)
        return self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )

    def test_publish_creates_new_row_and_keeps_draft_and_catalog_disabled(self) -> None:
        from app.assistant.skills.models import AssistantSkillVersion
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        package = self._create_package(
            "pub-skill-a",
            "  - type: tool\n    key: search_entries\n",
        )
        draft_id = package.draft_version.id if package.draft_version else None
        self.assertIsNotNone(draft_id)
        draft_before = self.db.get(AssistantSkillVersion, draft_id)
        assert draft_before is not None
        draft_digest = draft_before.content_digest
        draft_seq = draft_before.sequence_no

        published = self.svc.publish(
            package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-1"),  # type: ignore[arg-type]
        )
        self.assertEqual(published.version_source, "publish")
        self.assertEqual(published.source_draft_version_id, draft_id)
        self.assertIsNotNone(published.binding_set_digest)
        self.assertIsNotNone(published.version_digest)
        self.assertEqual(published.content_digest, draft_digest)

        draft_after = self.db.get(AssistantSkillVersion, draft_id)
        assert draft_after is not None
        self.assertEqual(draft_after.sequence_no, draft_seq)
        self.assertEqual(draft_after.content_digest, draft_digest)
        self.assertIsNone(draft_after.binding_set_digest)
        self.assertIsNone(draft_after.version_digest)

        detail = self.svc.get_package(package.id)
        self.assertFalse(detail.catalog_enabled)
        self.assertEqual(detail.published_version.id if detail.published_version else None, published.id)

    def test_publish_twice_creates_two_auditable_sequences(self) -> None:
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        package = self._create_package(
            "pub-skill-b",
            "  - type: tool\n    key: list_tags\n",
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        first = self.svc.publish(
            package.id, PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-2")
        )
        second = self.svc.publish(
            package.id, PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-3")
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertEqual(first.binding_set_digest, second.binding_set_digest)
        self.assertLess(first.sequence_no, second.sequence_no)

    def test_changed_tool_revision_changes_version_digest_not_content(self) -> None:
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.assistant_config.schemas import AssistantToolUpdateRequest
        from app.assistant_config.service import AssistantConfigService
        from tests.agent_skill_test_support import create_remote_tool

        tool = create_remote_tool(self.db, name="remote_rev")
        self.db.commit()
        package = self._create_package(
            "pub-skill-c",
            (
                "  - type: tool\n"
                "    key: remote_rev\n"
                "    contract:\n"
                "      output_schema:\n"
                "        type: object\n"
                "        properties:\n"
                "          ok:\n"
                "            type: boolean\n"
            ),
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        first = self.svc.publish(
            package.id, PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-4")
        )
        cfg = AssistantConfigService(self.db)
        cfg.update_tool(
            tool.id,
            AssistantToolUpdateRequest(timeout_seconds=30),
        )
        second = self.svc.publish(
            package.id, PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-5")
        )
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.binding_set_digest, second.binding_set_digest)
        self.assertNotEqual(first.version_digest, second.version_digest)

    def test_foreign_draft_rejected(self) -> None:
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.common.exceptions import ApiException
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        a = self._create_package(
            "pub-skill-d",
            "  - type: tool\n    key: search_entries\n",
        )
        b = self._create_package(
            "pub-skill-e",
            "  - type: tool\n    key: list_tags\n",
        )
        with self.assertRaises(ApiException) as ctx:
            self.svc.publish(
                a.id,
                PublishSkillVersionCommand(draft_version_id=b.draft_version.id, request_id="pub-req-6"),  # type: ignore[union-attr]
            )
        self.assertEqual(ctx.exception.code, 40491)

    def test_snapshot_reconstructs_byte_for_byte(self) -> None:
        from app.assistant.skills.models import (
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityDependency,
        )
        from app.assistant.skills.resolution import reconstruct_binding_snapshot
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        package = self._create_package(
            "pub-skill-f",
            "  - type: tool\n    key: search_entries\n",
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        published = self.svc.publish(
            package.id, PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-7")
        )
        bindings = (
            self.db.query(AssistantSkillCapabilityBinding)
            .filter(AssistantSkillCapabilityBinding.skill_version_id == published.id)
            .all()
        )
        self.assertEqual(len(bindings), 1)
        deps = (
            self.db.query(AssistantSkillCapabilityDependency)
            .filter(AssistantSkillCapabilityDependency.binding_id == bindings[0].id)
            .all()
        )
        reconstructed = reconstruct_binding_snapshot(bindings[0], deps)
        self.assertEqual(
            reconstructed["bindingContractDigest"],
            bindings[0].binding_contract_digest,
        )


class DependencyClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session

        self.db = make_session()
        from app.assistant_config.models import AssistantWorkflow

        def _boom(self):  # noqa: ANN001
            raise AssertionError("AssistantWorkflow.graph_snapshot must not be accessed")

        self._orig_graph_snapshot = AssistantWorkflow.graph_snapshot
        AssistantWorkflow.graph_snapshot = property(_boom)  # type: ignore[assignment]

    def tearDown(self) -> None:
        from app.assistant_config.models import AssistantWorkflow

        AssistantWorkflow.graph_snapshot = self._orig_graph_snapshot  # type: ignore[assignment]
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()

    def test_agent_and_workflow_closures_freeze_tools_and_models(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_agent,
            create_published_workflow,
            create_remote_tool,
        )
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver

        create_default_model_binding(self.db)
        remote = create_remote_tool(self.db, name="remote_dep")
        nested, nested_version = create_published_workflow(
            self.db,
            name="nested_wf",
            tool_names=["search_entries"],
        )
        parent, _ = create_published_workflow(
            self.db,
            name="parent_wf",
            tool_names=["remote_dep"],
            nested_calls=[
                {
                    "target_workflow_id": str(nested.id),
                    "binding_mode": "pinned",
                    "target_published_version_id": str(nested_version.id),
                }
            ],
        )
        agent, _ = create_published_agent(
            self.db,
            name="closure_agent",
            tools=["search_entries", "remote_dep"],
            model_source="default",
        )
        self.db.commit()

        resolver = CapabilityReferenceResolver(self.db)
        agent_binding = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key="closure_agent",
                    contract=CapabilityBindingContract(
                        input_schema={"type": "object", "properties": {}},
                        output_schema={"type": "string"},
                    ),
                ),
            )
        )[0]
        tool_types = {d.dependency_type for d in agent_binding.dependencies}
        self.assertIn("system_tool", tool_types)
        self.assertIn("remote_tool", tool_types)
        self.assertIn("model", tool_types)
        remote_dep = next(
            d for d in agent_binding.dependencies if d.dependency_type == "remote_tool"
        )
        self.assertEqual(remote_dep.resolved_tool_id, remote.id)
        self.assertEqual(remote_dep.output_schema, {"type": "string"})

        wf_binding = resolver.resolve_many(
            (CapabilityDeclaration(type="workflow", key="parent_wf"),)
        )[0]
        paths = [d.dependency_path for d in wf_binding.dependencies]
        self.assertTrue(any("workflow_call" in p for p in paths))
        self.assertTrue(any(d.dependency_type == "workflow" for d in wf_binding.dependencies))
        self.assertEqual(paths, sorted(paths))

    def test_unbound_default_model_fails_without_hidden_seed(self) -> None:
        from tests.agent_skill_test_support import create_published_agent
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        create_published_agent(
            self.db,
            name="unbound_agent",
            tools=["search_entries"],
            model_source="default",
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many(
                (
                    CapabilityDeclaration(
                        type="agent",
                        key="unbound_agent",
                        contract=CapabilityBindingContract(
                            input_schema={"type": "object", "properties": {}},
                            output_schema={"type": "string"},
                        ),
                    ),
                )
            )
        self.assertEqual(ctx.exception.code, 42293)
        self.assertEqual(ctx.exception.details.get("reason"), "default_model_unbound")

    def test_unpinned_workflow_call_fails(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.skills.contracts import CapabilityDeclaration
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.common.exceptions import ApiException

        create_default_model_binding(self.db)
        nested, nested_version = create_published_workflow(
            self.db, name="nested_latest", tool_names=["search_entries"]
        )
        create_published_workflow(
            self.db,
            name="parent_latest",
            nested_calls=[
                {
                    "target_workflow_id": str(nested.id),
                    "binding_mode": "latest",
                    "target_published_version_id": str(nested_version.id),
                }
            ],
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        with self.assertRaises(ApiException) as ctx:
            resolver.resolve_many(
                (CapabilityDeclaration(type="workflow", key="parent_latest"),)
            )
        self.assertEqual(ctx.exception.code, 42293)
        self.assertEqual(ctx.exception.details.get("reason"), "unpinned_workflow_call")

    def test_component_binding_change_after_publish_does_not_alter_frozen_closure(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_agent,
        )
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import CapabilityReferenceResolver
        from app.ai_registry.models import AiModel

        cred, model, binding = create_default_model_binding(self.db)
        create_published_agent(
            self.db,
            name="frozen_agent",
            tools=["search_entries"],
            model_source="default",
        )
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        before = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key="frozen_agent",
                    contract=CapabilityBindingContract(
                        input_schema={"type": "object", "properties": {}},
                        output_schema={"type": "string"},
                    ),
                ),
            )
        )[0]
        frozen_model = next(d for d in before.dependencies if d.dependency_type == "model")
        other = AiModel(
            credential_id=cred.id,
            name="gpt-other",
            model_type="llm",
            runtime_revision=1,
        )
        self.db.add(other)
        self.db.flush()
        binding.llm_model_id = other.id
        self.db.commit()
        # Re-resolve would see the new model; frozen digest from before stays unchanged.
        after = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key="frozen_agent",
                    contract=CapabilityBindingContract(
                        input_schema={"type": "object", "properties": {}},
                        output_schema={"type": "string"},
                    ),
                ),
            )
        )[0]
        new_model = next(d for d in after.dependencies if d.dependency_type == "model")
        self.assertEqual(frozen_model.resolved_model_id, model.id)
        self.assertEqual(new_model.resolved_model_id, other.id)
        self.assertNotEqual(frozen_model.dependency_digest, new_model.dependency_digest)

    def test_verify_resolved_binding_detects_revision_drift(self) -> None:
        from tests.agent_skill_test_support import create_remote_tool
        from app.assistant.domain.contracts import CurrentCapabilityReference
        from app.assistant.skills.contracts import (
            CapabilityBindingContract,
            CapabilityDeclaration,
        )
        from app.assistant.skills.resolution import (
            CapabilityReferenceResolver,
            verify_resolved_binding_is_current,
        )
        from app.assistant.skills.models import AssistantSkillCapabilityBinding
        from app.common.exceptions import ApiException

        tool = create_remote_tool(self.db, name="drift_tool")
        self.db.commit()
        resolver = CapabilityReferenceResolver(self.db)
        resolved = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="tool",
                    key="drift_tool",
                    contract=CapabilityBindingContract(
                        output_schema={"type": "string"},
                    ),
                ),
            )
        )[0]
        row = AssistantSkillCapabilityBinding(
            skill_version_id=uuid4(),  # not persisted; synthetic
            ordinal=0,
            capability_type=resolved.capability_type,
            capability_key=resolved.capability_key,
            resolution_status="resolved",
            target_identity=resolved.target_identity,
            resolved_tool_id=resolved.resolved_tool_id,
            resolved_revision=resolved.resolved_revision,
            input_schema_digest=resolved.input_schema_digest,
            output_schema_digest=resolved.output_schema_digest,
            config_digest=resolved.config_digest,
            executable_revision=resolved.executable_revision,
            resolution_digest=resolved.resolution_digest,
            dependency_closure_digest=resolved.dependency_closure_digest,
            binding_contract_digest=resolved.binding_contract_digest,
            resolution_snapshot=resolved.resolution_snapshot,
        )
        current = CurrentCapabilityReference(
            target_identity=resolved.target_identity,
            target_id=resolved.target_id,
            target_version_id=None,
            target_revision=2,
            executable_revision="2",
            system_tool_contract_set_digest=None,
            input_schema_digest=resolved.input_schema_digest,
            output_schema_digest=resolved.output_schema_digest,
            resolution_digest=resolved.resolution_digest,
            dependency_closure_digest=resolved.dependency_closure_digest,
            dependencies=(),
        )
        with self.assertRaises(ApiException) as ctx:
            verify_resolved_binding_is_current(row, current)
        self.assertEqual(ctx.exception.code, 42295)


class RevisionRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_tool_description_only_does_not_advance_revision(self) -> None:
        from tests.agent_skill_test_support import create_remote_tool
        from app.assistant_config.schemas import AssistantToolUpdateRequest
        from app.assistant_config.service import AssistantConfigService

        tool = create_remote_tool(self.db, name="rev_tool")
        self.db.commit()
        svc = AssistantConfigService(self.db)
        updated = svc.update_tool(
            tool.id,
            AssistantToolUpdateRequest(description="new description only"),
        )
        self.assertEqual(updated.config_revision, 1)

    def test_tool_endpoint_change_advances_revision_once(self) -> None:
        from tests.agent_skill_test_support import create_remote_tool
        from app.assistant_config.schemas import AssistantToolUpdateRequest
        from app.assistant_config.service import AssistantConfigService

        tool = create_remote_tool(self.db, name="rev_tool2")
        self.db.commit()
        svc = AssistantConfigService(self.db)
        updated = svc.update_tool(
            tool.id,
            AssistantToolUpdateRequest(endpoint_url="https://hooks.example.com/v2"),
        )
        self.assertEqual(updated.config_revision, 2)

    def test_model_and_credential_revision_rules(self) -> None:
        from app.ai_registry.service import AiCredentialService, AiModelService
        from tests.agent_skill_test_support import create_default_model_binding
        from unittest.mock import patch

        with patch("app.ai_registry.service.encrypt_api_key", return_value="enc2"), patch(
            "app.ai_registry.service.api_key_hint", return_value="****2"
        ), patch("app.ai_registry.service.validate_url_ssrf"):
            cred, model, _ = create_default_model_binding(self.db)
            self.db.commit()
            cred_svc = AiCredentialService(self.db)
            # Name-only change does not advance credential revision.
            updated_cred = cred_svc.update(cred.id, name="renamed-cred", base_url=None, api_key=None)
            self.assertEqual(updated_cred.runtime_revision, 1)
            # Base URL advances revision.
            updated_cred = cred_svc.update(
                cred.id, name=None, base_url="https://api.example.com/v2", api_key=None
            )
            self.assertEqual(updated_cred.runtime_revision, 2)

            model_svc = AiModelService(self.db)
            updated_model = model_svc.update(model.id, name="gpt-test-2", model_type=None)
            self.assertEqual(updated_model.runtime_revision, 2)


class ProtectedHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService

        self.db = make_session()
        self.svc = AgentSkillService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()

    def test_keep_only_preserves_skill_referenced_versions(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant_config.models import AssistantWorkflowVersion
        from app.assistant_config.service import AssistantConfigService

        create_default_model_binding(self.db)
        workflow, v1 = create_published_workflow(
            self.db, name="protect_wf", tool_names=["search_entries"]
        )
        # Add a second publish version that becomes current.
        v2 = AssistantWorkflowVersion(
            workflow_id=workflow.id,
            sequence_no=2,
            version_name="v2",
            version_source="publish",
            snapshot=v1.snapshot,
        )
        self.db.add(v2)
        self.db.flush()
        workflow.published_version_id = v1.id
        self.db.commit()

        caps = f"  - type: workflow\n    key: protect_wf\n"
        parsed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md("protect-skill"),
                "mindatlas.yaml": _mindatlas_yaml(caps),
            },
            expected_root_name=None,
        )
        package = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )
        self.svc.publish(
            package.id,
            PublishSkillVersionCommand(draft_version_id=package.draft_version.id, request_id="pub-req-8"),  # type: ignore[union-attr]
        )
        # Advance aggregate to V2 after skill freeze of V1.
        workflow.published_version_id = v2.id
        self.db.commit()

        cfg = AssistantConfigService(self.db)
        cfg._keep_only_workflow_version(workflow, v2.id)
        self.db.commit()
        remaining = {
            row.id
            for row in self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .all()
        }
        self.assertIn(v1.id, remaining)
        self.assertIn(v2.id, remaining)

    def test_delete_skill_referenced_workflow_version_returns_40994(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant_config.models import AssistantWorkflowVersion
        from app.assistant_config.service import AssistantConfigService
        from app.common.exceptions import ApiException

        create_default_model_binding(self.db)
        workflow, v1 = create_published_workflow(
            self.db, name="delete_protect_wf", tool_names=["search_entries"]
        )
        v2 = AssistantWorkflowVersion(
            workflow_id=workflow.id,
            sequence_no=2,
            version_name="v2",
            version_source="publish",
            snapshot=v1.snapshot,
        )
        self.db.add(v2)
        self.db.flush()
        workflow.published_version_id = v1.id
        self.db.commit()

        caps = "  - type: workflow\n    key: delete_protect_wf\n"
        parsed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md("delete-protect-skill"),
                "mindatlas.yaml": _mindatlas_yaml(caps),
            },
            expected_root_name=None,
        )
        package = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )
        published = self.svc.publish(
            package.id,
            PublishSkillVersionCommand(draft_version_id=package.draft_version.id, request_id="pub-req-9"),  # type: ignore[union-attr]
        )
        workflow.published_version_id = v2.id
        self.db.commit()

        cfg = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            cfg.delete_workflow_version(workflow.id, v1.id)
        self.assertEqual(ctx.exception.code, 40994)
        details = ctx.exception.details or {}
        self.assertEqual(details.get("skillPackageId"), str(package.id))
        self.assertEqual(details.get("skillVersionId"), str(published.id))

        with self.assertRaises(ApiException) as ctx2:
            cfg.delete_workflow(workflow.id)
        self.assertEqual(ctx2.exception.code, 40994)
        self.assertEqual((ctx2.exception.details or {}).get("skillPackageId"), str(package.id))

    def test_dependency_only_v1_survives_keep_only_after_v2_advance(self) -> None:
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant.skills.models import AssistantSkillCapabilityDependency
        from app.assistant_config.models import AssistantWorkflowVersion
        from app.assistant_config.service import AssistantConfigService

        create_default_model_binding(self.db)
        nested, nested_v1 = create_published_workflow(
            self.db,
            name="dep_only_nested",
            tool_names=["search_entries"],
        )
        nested_v2 = AssistantWorkflowVersion(
            workflow_id=nested.id,
            sequence_no=2,
            version_name="v2",
            version_source="publish",
            snapshot=nested_v1.snapshot,
        )
        self.db.add(nested_v2)
        self.db.flush()
        nested.published_version_id = nested_v1.id
        parent, _ = create_published_workflow(
            self.db,
            name="dep_only_parent",
            nested_calls=[
                {
                    "target_workflow_id": str(nested.id),
                    "binding_mode": "pinned",
                    "target_published_version_id": str(nested_v1.id),
                }
            ],
        )
        self.db.commit()

        caps = "  - type: workflow\n    key: dep_only_parent\n"
        parsed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md("dep-only-skill"),
                "mindatlas.yaml": _mindatlas_yaml(caps),
            },
            expected_root_name=None,
        )
        package = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )
        published = self.svc.publish(
            package.id,
            PublishSkillVersionCommand(draft_version_id=package.draft_version.id, request_id="pub-req-10"),  # type: ignore[union-attr]
        )
        nested_dep = (
            self.db.query(AssistantSkillCapabilityDependency)
            .filter(
                AssistantSkillCapabilityDependency.resolved_workflow_version_id
                == nested_v1.id
            )
            .one()
        )
        frozen_digest = nested_dep.dependency_digest
        nested.published_version_id = nested_v2.id
        self.db.commit()

        cfg = AssistantConfigService(self.db)
        cfg._keep_only_workflow_version(nested, nested_v2.id)
        self.db.commit()

        remaining = {
            row.id
            for row in self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == nested.id)
            .all()
        }
        self.assertEqual(nested.published_version_id, nested_v2.id)
        self.assertIn(nested_v1.id, remaining)
        self.assertIn(nested_v2.id, remaining)
        self.db.refresh(nested_dep)
        self.assertEqual(nested_dep.dependency_digest, frozen_digest)
        self.assertEqual(
            nested_dep.resolution_snapshot.get("snapshotDigest"),
            sha256_canonical_json(nested_v1.snapshot),
        )
        self.assertEqual(published.skill_package_id, package.id)

    def test_delete_referenced_tool_returns_40994(self) -> None:
        from tests.agent_skill_test_support import create_remote_tool
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant_config.service import AssistantConfigService
        from app.common.exceptions import ApiException

        tool = create_remote_tool(self.db, name="protected_remote")
        self.db.commit()
        caps = (
            "  - type: tool\n"
            "    key: protected_remote\n"
            "    contract:\n"
            "      output_schema:\n"
            "        type: string\n"
        )
        parsed = parse_skill_directory_files(
            {
                "SKILL.md": _minimal_skill_md("protect-tool-skill"),
                "mindatlas.yaml": _mindatlas_yaml(caps),
            },
            expected_root_name=None,
        )
        package = self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )
        self.svc.publish(
            package.id,
            PublishSkillVersionCommand(draft_version_id=package.draft_version.id, request_id="pub-req-11"),  # type: ignore[union-attr]
        )
        cfg = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            cfg.delete_tool(tool.id)
        self.assertEqual(ctx.exception.code, 40994)
        self.assertIn("skillPackageId", ctx.exception.details or {})


class WorkflowReferenceParityTests(unittest.TestCase):
    def test_pure_walkers_match_service_wrappers(self) -> None:
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_references import (
            collect_workflow_custom_model_ids,
            collect_workflow_tool_names,
            collect_workflow_call_references,
        )
        from uuid import uuid4

        nested_id = uuid4()
        version_id = uuid4()
        nodes = [
            {
                "node_id": "start",
                "node_type": "start",
                "config": {},
            },
            {
                "node_id": "t1",
                "node_type": "tool",
                "config": {"toolName": "search_entries"},
            },
            {
                "node_id": "a1",
                "node_type": "agent",
                "config": {
                    "tool_names": ["list_tags"],
                    "knowledge_enabled": True,
                    "model_source": "custom",
                    "model_id": str(uuid4()),
                },
            },
            {
                "node_id": "loop1",
                "node_type": "loop",
                "config": {
                    "body_nodes": [
                        {
                            "node_id": "inner_call",
                            "node_type": "workflow_call",
                            "config": {
                                "target_workflow_id": str(nested_id),
                                "binding_mode": "pinned",
                                "target_published_version_id": str(version_id),
                            },
                        }
                    ]
                },
            },
        ]
        pure_tools = collect_workflow_tool_names(nodes)
        svc_tools = AssistantConfigService._collect_workflow_tool_names(nodes)
        self.assertEqual(pure_tools, svc_tools)
        pure_models = collect_workflow_custom_model_ids(nodes)
        svc_models = AssistantConfigService._collect_workflow_custom_model_ids(nodes)
        self.assertEqual(pure_models, svc_models)
        pure_calls = collect_workflow_call_references(nodes)
        self.assertEqual(len(pure_calls), 1)
        self.assertEqual(pure_calls[0].target_workflow_id, nested_id)
        self.assertEqual(pure_calls[0].target_published_version_id, version_id)


if __name__ == "__main__":
    unittest.main()
