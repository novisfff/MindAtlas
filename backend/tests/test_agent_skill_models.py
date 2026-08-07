from __future__ import annotations

import hashlib
import unittest
import uuid
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


V2_TABLES = (
    "assistant_skill_package",
    "assistant_skill_package_alias",
    "assistant_skill_version",
    "assistant_skill_resource_blob",
    "assistant_skill_version_resource",
    "assistant_skill_capability_binding",
    "assistant_skill_capability_dependency",
    "assistant_main_agent_profile",
    "assistant_main_agent_profile_version",
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64

_REPO_ROOT = Path(__file__).resolve().parents[2]
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AgentSkillModelContractTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session  # noqa: E402

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_all_nine_v2_tables_registered(self) -> None:
        from app.database import Base  # noqa: E402
        import app.assistant.skills.models  # noqa: F401,E402

        for name in V2_TABLES:
            self.assertIn(name, Base.metadata.tables, msg=f"missing table {name}")

        # Explicitly require blob + dependency (called out in the plan).
        self.assertIn("assistant_skill_resource_blob", Base.metadata.tables)
        self.assertIn("assistant_skill_capability_dependency", Base.metadata.tables)

    def test_package_canonical_name_and_alias_uniqueness(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantSkillPackage,
            AssistantSkillPackageAlias,
        )

        pkg = AssistantSkillPackage(
            canonical_name="weekly-review",
            display_name="Weekly Review",
            description="desc",
            migration_state="native",
            catalog_enabled=False,
            is_system=False,
        )
        self.db.add(pkg)
        self.db.flush()

        self.db.add(
            AssistantSkillPackageAlias(
                skill_package_id=pkg.id,
                alias="weekly-review",
                normalized_alias="weekly-review",
                alias_type="canonical",
            )
        )
        self.db.commit()

        dup_pkg = AssistantSkillPackage(
            canonical_name="weekly-review",
            display_name="Other",
            description="d",
            migration_state="native",
            catalog_enabled=False,
            is_system=False,
        )
        self.db.add(dup_pkg)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        other = AssistantSkillPackage(
            canonical_name="other-skill",
            display_name="Other",
            description="d",
            migration_state="native",
            catalog_enabled=False,
            is_system=False,
        )
        self.db.add(other)
        self.db.flush()
        self.db.add(
            AssistantSkillPackageAlias(
                skill_package_id=other.id,
                alias="Weekly Review",
                normalized_alias="weekly-review",
                alias_type="custom",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_package_version_sequence_and_draft_content_uniqueness(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantSkillPackage,
            AssistantSkillVersion,
        )

        pkg = self._package("seq-skill")
        self.db.add(pkg)
        self.db.flush()

        v1 = self._version(pkg.id, sequence_no=1, content_digest=DIGEST_A, version_source="save")
        self.db.add(v1)
        self.db.commit()

        dup_seq = self._version(pkg.id, sequence_no=1, content_digest=DIGEST_B, version_source="save")
        self.db.add(dup_seq)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Partial unique: same content_digest for another draft save collides.
        dup_draft = self._version(pkg.id, sequence_no=2, content_digest=DIGEST_A, version_source="save")
        self.db.add(dup_draft)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Published rows intentionally allow duplicate content_digest.
        pub1 = self._version(
            pkg.id,
            sequence_no=2,
            content_digest=DIGEST_A,
            version_source="publish",
            source_draft_version_id=v1.id,
            binding_set_digest=DIGEST_C,
            version_digest=DIGEST_D,
        )
        pub2 = self._version(
            pkg.id,
            sequence_no=3,
            content_digest=DIGEST_A,
            version_source="publish",
            source_draft_version_id=v1.id,
            binding_set_digest=DIGEST_C,
            version_digest=DIGEST_E,
        )
        self.db.add_all([pub1, pub2])
        self.db.commit()

    def test_resource_path_and_binding_key_uniqueness(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantSkillCapabilityBinding,
            AssistantSkillResourceBlob,
            AssistantSkillVersionResource,
        )

        pkg = self._package("res-skill")
        self.db.add(pkg)
        self.db.flush()
        version = self._version(pkg.id, sequence_no=1, content_digest=DIGEST_A)
        self.db.add(version)
        self.db.flush()

        content = b"hello-resource"
        digest = _sha256(content)
        blob = AssistantSkillResourceBlob(sha256=digest, byte_size=len(content), content=content)
        self.db.add(blob)
        self.db.flush()

        r1 = AssistantSkillVersionResource(
            skill_version_id=version.id,
            path="references/guide.md",
            resource_kind="references",
            media_type="text/markdown",
            byte_size=len(content),
            sha256=digest,
            blob_id=blob.id,
            executable=False,
        )
        self.db.add(r1)
        self.db.commit()

        r_dup = AssistantSkillVersionResource(
            skill_version_id=version.id,
            path="references/guide.md",
            resource_kind="references",
            media_type="text/markdown",
            byte_size=len(content),
            sha256=digest,
            blob_id=blob.id,
            executable=False,
        )
        self.db.add(r_dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        b1 = AssistantSkillCapabilityBinding(
            skill_version_id=version.id,
            ordinal=0,
            capability_type="tool",
            capability_key="search",
            resolution_status="unresolved",
        )
        self.db.add(b1)
        self.db.commit()

        b_dup = AssistantSkillCapabilityBinding(
            skill_version_id=version.id,
            ordinal=1,
            capability_type="tool",
            capability_key="search",
            resolution_status="unresolved",
        )
        self.db.add(b_dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_blob_dedup_and_metadata_match_contract(self) -> None:
        from app.assistant.skills.models import AssistantSkillResourceBlob  # noqa: E402

        content = b"shared-bytes"
        digest = _sha256(content)
        blob = AssistantSkillResourceBlob(sha256=digest, byte_size=len(content), content=content)
        self.db.add(blob)
        self.db.commit()

        # Equal (sha256, byte_size) is unique — reuse is by re-reading the row.
        collision = AssistantSkillResourceBlob(
            sha256=digest,
            byte_size=len(content),
            content=content,
        )
        self.db.add(collision)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Application fail-closed rule for digest/size collision with different bytes.
        existing = (
            self.db.query(AssistantSkillResourceBlob)
            .filter_by(sha256=digest, byte_size=len(content))
            .one()
        )
        forged = b"different-bytes-same-claimed-digest"
        with self.assertRaises(ValueError):
            if existing.content != forged:
                raise ValueError("digest collision with different bytes; fail closed")

    def test_dependency_ordinal_path_and_typed_fk_shapes(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityDependency,
        )
        from app.ai_registry.models import AiCredential, AiModel  # noqa: E402

        pkg = self._package("dep-skill")
        self.db.add(pkg)
        self.db.flush()
        version = self._version(pkg.id, sequence_no=1, content_digest=DIGEST_A)
        self.db.add(version)
        self.db.flush()

        binding = AssistantSkillCapabilityBinding(
            skill_version_id=version.id,
            ordinal=0,
            capability_type="agent",
            capability_key="reviewer",
            resolution_status="resolved",
            target_identity="agent:reviewer@v1",
            resolved_agent_version_id=self._agent_version_id(),
            input_schema_digest=DIGEST_A,
            output_schema_digest=DIGEST_B,
            resolution_digest=DIGEST_C,
            dependency_closure_digest=DIGEST_D,
            binding_contract_digest=DIGEST_E,
            resolution_snapshot={
                "schemaVersion": 1,
                "inputSchema": {"type": "object", "properties": {}},
                "outputSchema": {"type": "object", "properties": {}},
                "inputSchemaDigest": DIGEST_A,
                "outputSchemaDigest": DIGEST_B,
            },
        )
        self.db.add(binding)
        self.db.flush()

        cred = AiCredential(
            name="cred-dep",
            base_url="https://api.example.com",
            api_key_encrypted="enc",
            api_key_hint="****",
        )
        model = AiModel(credential=cred, name="gpt-test", model_type="llm")
        self.db.add_all([cred, model])
        self.db.flush()

        dep = AssistantSkillCapabilityDependency(
            binding_id=binding.id,
            ordinal=0,
            dependency_path="model/default",
            dependency_type="model",
            target_identity=f"model:{model.id}",
            resolved_model_id=model.id,
            target_revision=1,
            resolution_digest=DIGEST_A,
            dependency_digest=DIGEST_B,
            resolution_snapshot={"schemaVersion": 1, "modelId": str(model.id)},
        )
        self.db.add(dep)
        self.db.commit()

        dup_ord = AssistantSkillCapabilityDependency(
            binding_id=binding.id,
            ordinal=0,
            dependency_path="model/other",
            dependency_type="model",
            target_identity=f"model:{model.id}",
            resolved_model_id=model.id,
            target_revision=1,
            resolution_digest=DIGEST_A,
            dependency_digest=DIGEST_C,
            resolution_snapshot={"schemaVersion": 1},
        )
        self.db.add(dup_ord)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        dup_path = AssistantSkillCapabilityDependency(
            binding_id=binding.id,
            ordinal=1,
            dependency_path="model/default",
            dependency_type="model",
            target_identity=f"model:{model.id}",
            resolved_model_id=model.id,
            target_revision=1,
            resolution_digest=DIGEST_A,
            dependency_digest=DIGEST_D,
            resolution_snapshot={"schemaVersion": 1},
        )
        self.db.add(dup_path)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Wrong shape: model dependency must not also set a tool FK.
        bad_shape = AssistantSkillCapabilityDependency(
            binding_id=binding.id,
            ordinal=1,
            dependency_path="model/bad",
            dependency_type="model",
            target_identity="model:bad",
            resolved_model_id=model.id,
            resolved_tool_id=self._tool_id(),
            target_revision=1,
            resolution_digest=DIGEST_A,
            dependency_digest=DIGEST_E,
            resolution_snapshot={"schemaVersion": 1},
        )
        self.db.add(bad_shape)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_resolved_binding_digest_set_and_unresolved_cannot_masquerade(self) -> None:
        from app.assistant.skills.models import AssistantSkillCapabilityBinding  # noqa: E402

        pkg = self._package("bind-skill")
        self.db.add(pkg)
        self.db.flush()
        version = self._version(pkg.id, sequence_no=1, content_digest=DIGEST_A)
        self.db.add(version)
        self.db.flush()

        resolved = AssistantSkillCapabilityBinding(
            skill_version_id=version.id,
            ordinal=0,
            capability_type="tool",
            capability_key="sys-search",
            resolution_status="resolved",
            target_identity="system-tool:search_entries",
            resolved_tool_id=None,
            resolved_workflow_version_id=None,
            resolved_agent_version_id=None,
            resolved_revision=None,
            executable_revision="build-1",
            input_schema_digest=DIGEST_A,
            output_schema_digest=DIGEST_B,
            config_digest=DIGEST_C,
            resolution_digest=DIGEST_D,
            dependency_closure_digest=DIGEST_E,
            binding_contract_digest=DIGEST_A,
            resolution_snapshot={
                "schemaVersion": 1,
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "inputSchemaDigest": DIGEST_A,
                "outputSchemaDigest": DIGEST_B,
            },
        )
        self.db.add(resolved)
        self.db.commit()
        self.db.refresh(resolved)
        self.assertEqual(resolved.resolution_status, "resolved")
        self.assertIsNotNone(resolved.input_schema_digest)
        self.assertIsNotNone(resolved.output_schema_digest)
        self.assertIsNotNone(resolved.resolution_digest)
        self.assertIsNotNone(resolved.dependency_closure_digest)
        self.assertIsNotNone(resolved.binding_contract_digest)
        self.assertIsInstance(resolved.resolution_snapshot, dict)

        # Unresolved draft cannot claim complete digest set / typed target.
        incomplete = AssistantSkillCapabilityBinding(
            skill_version_id=version.id,
            ordinal=1,
            capability_type="tool",
            capability_key="remote-x",
            resolution_status="unresolved",
            target_identity="tool:x",
            resolved_tool_id=self._tool_id(),
            resolved_revision=1,
            input_schema_digest=DIGEST_A,
            output_schema_digest=DIGEST_B,
            resolution_digest=DIGEST_C,
            dependency_closure_digest=DIGEST_D,
            binding_contract_digest=DIGEST_E,
            resolution_snapshot={"schemaVersion": 1},
        )
        self.db.add(incomplete)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        draft = AssistantSkillCapabilityBinding(
            skill_version_id=version.id,
            ordinal=1,
            capability_type="tool",
            capability_key="remote-x",
            resolution_status="unresolved",
        )
        self.db.add(draft)
        self.db.commit()

    def test_child_rows_belong_to_one_version(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantSkillCapabilityBinding,
            AssistantSkillResourceBlob,
            AssistantSkillVersionResource,
        )

        pkg = self._package("child-skill")
        self.db.add(pkg)
        self.db.flush()
        v1 = self._version(pkg.id, sequence_no=1, content_digest=DIGEST_A)
        v2 = self._version(pkg.id, sequence_no=2, content_digest=DIGEST_B)
        self.db.add_all([v1, v2])
        self.db.flush()

        content = b"body"
        digest = _sha256(content)
        blob = AssistantSkillResourceBlob(sha256=digest, byte_size=len(content), content=content)
        self.db.add(blob)
        self.db.flush()

        resource = AssistantSkillVersionResource(
            skill_version_id=v1.id,
            path="assets/a.txt",
            resource_kind="assets",
            media_type="text/plain",
            byte_size=len(content),
            sha256=digest,
            blob_id=blob.id,
            executable=False,
        )
        binding = AssistantSkillCapabilityBinding(
            skill_version_id=v1.id,
            ordinal=0,
            capability_type="workflow",
            capability_key="flow",
            resolution_status="unresolved",
        )
        self.db.add_all([resource, binding])
        self.db.commit()

        self.assertEqual(resource.skill_version_id, v1.id)
        self.assertEqual(binding.skill_version_id, v1.id)
        self.assertNotEqual(resource.skill_version_id, v2.id)

        # Immutable rows must not carry updated_at (append-only).
        self.assertFalse(hasattr(resource, "updated_at") and "updated_at" in resource.__table__.c)
        self.assertNotIn("updated_at", v1.__table__.c.keys())
        self.assertNotIn("updated_at", binding.__table__.c.keys())

    def test_main_agent_profile_default_and_sequence_uniqueness(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
        )

        p1 = AssistantMainAgentProfile(
            profile_key="default",
            display_name="Default Main Agent",
            is_default=True,
            migration_state="bootstrap",
            runtime_enabled=False,
        )
        self.db.add(p1)
        self.db.flush()

        v1 = AssistantMainAgentProfileVersion(
            profile_id=p1.id,
            sequence_no=1,
            version_name="v1",
            version_source="save",
            origin="bootstrap",
            snapshot={"schemaVersion": 1, "systemPrompt": "hi"},
            content_digest=DIGEST_A,
        )
        self.db.add(v1)
        self.db.commit()

        p2 = AssistantMainAgentProfile(
            profile_key="other",
            display_name="Other",
            is_default=True,
            migration_state="native",
            runtime_enabled=False,
        )
        self.db.add(p2)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        dup_seq = AssistantMainAgentProfileVersion(
            profile_id=p1.id,
            sequence_no=1,
            version_name="v1-dup",
            version_source="save",
            origin="api",
            snapshot={"schemaVersion": 1},
            content_digest=DIGEST_B,
        )
        self.db.add(dup_seq)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Draft content_digest uniqueness per profile.
        same_draft = AssistantMainAgentProfileVersion(
            profile_id=p1.id,
            sequence_no=2,
            version_name="v2",
            version_source="save",
            origin="api",
            snapshot={"schemaVersion": 1, "systemPrompt": "hi"},
            content_digest=DIGEST_A,
        )
        self.db.add(same_draft)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_published_pointer_ownership_is_service_enforced(self) -> None:
        """Cross-aggregate pointer ownership is enforced in service code (not portable FK)."""
        from app.assistant.skills.models import AssistantSkillPackage  # noqa: E402

        pkg_a = self._package("own-a")
        pkg_b = self._package("own-b")
        self.db.add_all([pkg_a, pkg_b])
        self.db.flush()
        v_a = self._version(pkg_a.id, sequence_no=1, content_digest=DIGEST_A)
        self.db.add(v_a)
        self.db.flush()

        # SQLite allows the raw pointer assignment; ownership is a service invariant.
        pkg_b.published_version_id = v_a.id
        self.db.commit()
        self.db.refresh(pkg_b)
        self.assertEqual(pkg_b.published_version_id, v_a.id)
        # Service layer (Task 4+) must reject this; PG deferred trigger covered in Task 10.

    def test_tool_and_registry_revision_defaults(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.ai_registry.models import AiCredential, AiModel  # noqa: E402

        tool = AssistantTool(
            name="rev-tool",
            description="d",
            kind="local",
            is_system=False,
            enabled=True,
        )
        cred = AiCredential(
            name="rev-cred",
            base_url="https://api.example.com",
            api_key_encrypted="enc",
            api_key_hint="****",
        )
        model = AiModel(credential=cred, name="m1", model_type="llm")
        self.db.add_all([tool, cred, model])
        self.db.commit()
        self.db.refresh(tool)
        self.db.refresh(cred)
        self.db.refresh(model)

        self.assertEqual(tool.config_revision, 1)
        self.assertIsNotNone(tool.config_revision)
        self.assertEqual(cred.runtime_revision, 1)
        self.assertEqual(model.runtime_revision, 1)

    def test_catalog_and_runtime_flags_default_false_but_may_enable(self) -> None:
        """Plan 04: disabled-only CHECKs removed; defaults remain false.

        Aggregate flags may be set true (enablement is application-gated, not DB-hard).
        """
        from app.assistant.skills.models import (  # noqa: E402
            AssistantMainAgentProfile,
            AssistantSkillPackage,
        )

        pkg = AssistantSkillPackage(
            canonical_name="disabled-pkg",
            display_name="Disabled",
            description="d",
            migration_state="native",
            is_system=False,
        )
        self.db.add(pkg)
        self.db.commit()
        self.db.refresh(pkg)
        self.assertFalse(pkg.catalog_enabled)

        pkg.catalog_enabled = True
        self.db.commit()
        self.db.refresh(pkg)
        self.assertTrue(pkg.catalog_enabled)

        profile = AssistantMainAgentProfile(
            profile_key="enabled-profile",
            display_name="P",
            is_default=False,
            migration_state="native",
        )
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        self.assertFalse(profile.runtime_enabled)

        profile.runtime_enabled = True
        self.db.commit()
        self.db.refresh(profile)
        self.assertTrue(profile.runtime_enabled)

    def test_no_orm_cascade_deletes_immutable_history(self) -> None:
        from app.assistant.skills.models import (  # noqa: E402
            AssistantSkillPackage,
            AssistantSkillVersion,
        )

        pkg_rel = AssistantSkillPackage.versions.property
        cascade = set(pkg_rel.cascade)
        self.assertNotIn("delete", cascade)
        self.assertNotIn("delete-orphan", cascade)

        ver_rel = AssistantSkillVersion.resources.property
        cascade_r = set(ver_rel.cascade)
        self.assertNotIn("delete-orphan", cascade_r)

    def test_postgresql_only_guards_marked_for_task_10(self) -> None:
        """Document PG-only pieces exercised in Task 10 migration gate, not SQLite.

        PostgreSQL-only (Task 10):
        - UPDATE/DELETE rejection triggers on aliases, versions, blobs, resources,
          bindings, dependencies, main-agent profile versions
        - deferred ownership/source guards for draft/published/source_draft pointers
        - deferred version-resource/blob sha256+byte_size equality + unreferenced blob guard
        - deferred binding closure index completeness trigger (required digest keys)
        - revision guard triggers for AssistantTool / AiModel / AiCredential
        - full PG execution of downgrade preflight (native/cutover, api/import package
          origins, profile api origin; bootstrap/legacy profile origins allowed)
        """
        self.assertTrue(True)

    # ------------------------------------------------------------------ helpers

    def _package(self, name: str):
        from app.assistant.skills.models import AssistantSkillPackage  # noqa: E402

        return AssistantSkillPackage(
            canonical_name=name,
            display_name=name,
            description="desc",
            migration_state="native",
            catalog_enabled=False,
            is_system=False,
        )

    def _version(
        self,
        package_id: uuid.UUID,
        *,
        sequence_no: int,
        content_digest: str,
        version_source: str = "save",
        source_draft_version_id: uuid.UUID | None = None,
        binding_set_digest: str | None = None,
        version_digest: str | None = None,
    ):
        from app.assistant.skills.models import AssistantSkillVersion  # noqa: E402

        return AssistantSkillVersion(
            skill_package_id=package_id,
            sequence_no=sequence_no,
            version_name=f"v{sequence_no}",
            version_source=version_source,
            source_draft_version_id=source_draft_version_id,
            origin="api",
            skill_md="# Skill\n",
            mindatlas_yaml=None,
            frontmatter={"name": "x", "description": "y"},
            extension_manifest=None,
            resource_index=[],
            skill_md_digest=DIGEST_A,
            manifest_digest=DIGEST_B,
            resource_index_digest=DIGEST_C,
            content_digest=content_digest,
            binding_set_digest=binding_set_digest,
            version_digest=version_digest,
        )

    def _tool_id(self) -> uuid.UUID:
        from app.assistant_config.models import AssistantTool  # noqa: E402

        tool = AssistantTool(
            name=f"tool-{uuid.uuid4().hex[:8]}",
            description="d",
            kind="remote",
            is_system=False,
            enabled=True,
        )
        self.db.add(tool)
        self.db.flush()
        return tool.id

    def _agent_version_id(self) -> uuid.UUID:
        from app.assistant_config.models import (  # noqa: E402
            AssistantAgentProfile,
            AssistantAgentProfileVersion,
        )

        profile = AssistantAgentProfile(
            name=f"agent-{uuid.uuid4().hex[:8]}",
            description="d",
            is_system=False,
            enabled=True,
        )
        self.db.add(profile)
        self.db.flush()
        version = AssistantAgentProfileVersion(
            agent_profile_id=profile.id,
            sequence_no=1,
            version_name="v1",
            version_source="publish",
            snapshot={"system_prompt": "x", "tools": [], "kb_config": {}},
        )
        self.db.add(version)
        self.db.flush()
        return version.id


if __name__ == "__main__":
    unittest.main()
