"""Tests for Main Agent Profile v1 snapshot validation and lifecycle service."""

from __future__ import annotations

import copy
import unittest
import uuid
from typing import Any

from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _current_profile_rev(db, profile_id) -> int:
    from app.assistant.skills.models import AssistantMainAgentProfile
    row = db.get(AssistantMainAgentProfile, profile_id)
    return int(getattr(row, "aggregate_revision", 0) or 0) if row is not None else 0



def _valid_snapshot_dict(**overrides: Any) -> dict[str, Any]:
    from app.assistant.skills.schemas import default_main_agent_profile_snapshot

    payload = default_main_agent_profile_snapshot().normalized_payload()
    payload = copy.deepcopy(payload)
    for key, value in overrides.items():
        payload[key] = value
    return payload


class MainAgentProfileSnapshotValidationTests(unittest.TestCase):
    def test_default_snapshot_is_valid_and_digest_stable(self) -> None:
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            default_main_agent_profile_snapshot,
        )

        snap = default_main_agent_profile_snapshot()
        self.assertEqual(snap.schema_version, 1)
        self.assertTrue(snap.base_prompt.strip())
        self.assertEqual(snap.supported_entrypoints, ("assistant_chat",))
        self.assertTrue(snap.model_requirements.tool_calling)
        self.assertTrue(snap.model_requirements.streaming)
        self.assertEqual(snap.control_capability_keys, ())
        self.assertEqual(snap.skill_catalog_scope.mode, "all_published")
        self.assertEqual(snap.skill_catalog_scope.package_ids, ())
        self.assertTrue(snap.global_safety_policy.deny_by_default)
        self.assertTrue(snap.fallback_policy.legacy_runtime_allowed)
        self.assertTrue(snap.fallback_policy.before_side_effects_only)

        digest1 = snap.content_digest()
        again = MainAgentProfileSnapshotV1.model_validate(snap.normalized_payload())
        self.assertEqual(again.content_digest(), digest1)
        self.assertEqual(len(digest1), 64)

    def test_schema_version_must_be_integer_one(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        for bad in (2, "1", 1.0, True, None):
            with self.subTest(bad=bad):
                payload = _valid_snapshot_dict(schemaVersion=bad)
                with self.assertRaises((ValidationError, ValueError)):
                    MainAgentProfileSnapshotV1.model_validate(payload)

    def test_base_prompt_non_empty_and_bounded(self) -> None:
        from app.assistant.skills.schemas import (
            MAX_BASE_PROMPT_LEN,
            MainAgentProfileSnapshotV1,
        )

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(basePrompt="")
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(basePrompt="   ")
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(basePrompt="x" * (MAX_BASE_PROMPT_LEN + 1))
            )
        ok = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(basePrompt="x" * MAX_BASE_PROMPT_LEN)
        )
        self.assertEqual(len(ok.base_prompt), MAX_BASE_PROMPT_LEN)

    def test_entrypoints_known_unique_ordered(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(supportedEntrypoints=[])
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(supportedEntrypoints=["unknown_entry"])
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    supportedEntrypoints=["assistant_chat", "assistant_chat"]
                )
            )

        ok = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(supportedEntrypoints=["assistant_chat"])
        )
        self.assertEqual(ok.supported_entrypoints, ("assistant_chat",))

    def test_model_requirements_booleans_no_unknown_keys(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    modelRequirements={
                        "toolCalling": True,
                        "streaming": True,
                        "multiToolCalls": True,
                        "jsonSchema": True,
                        "extraFlag": True,
                    }
                )
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    modelRequirements={
                        "toolCalling": "yes",
                        "streaming": True,
                        "multiToolCalls": True,
                        "jsonSchema": True,
                    }
                )
            )

    def test_control_capability_keys_unique_domain_keys(self) -> None:
        from app.assistant.skills.contracts import MAX_CAPABILITY_KEY_LEN
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        ok = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(
                controlCapabilityKeys=["skill.inject", "skill.search"]
            )
        )
        self.assertEqual(ok.control_capability_keys, ("skill.inject", "skill.search"))

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    controlCapabilityKeys=["skill.inject", "skill.inject"]
                )
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(controlCapabilityKeys=[""])
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    controlCapabilityKeys=["x" * (MAX_CAPABILITY_KEY_LEN + 1)]
                )
            )

    def test_skill_catalog_scope_modes_and_package_ids(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        # default
        ok = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(
                skillCatalogScope={"mode": "all_published", "packageIds": []}
            )
        )
        self.assertEqual(ok.skill_catalog_scope.mode, "all_published")

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    skillCatalogScope={
                        "mode": "all_published",
                        "packageIds": [str(uuid.uuid4())],
                    }
                )
            )

        id_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        id_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        # unsorted rejected
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    skillCatalogScope={
                        "mode": "allowlist",
                        "packageIds": [str(id_b), str(id_a)],
                    }
                )
            )
        # empty allowlist rejected
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    skillCatalogScope={"mode": "allowlist", "packageIds": []}
                )
            )
        # duplicates rejected
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    skillCatalogScope={
                        "mode": "allowlist",
                        "packageIds": [str(id_a), str(id_a)],
                    }
                )
            )
        # unknown mode
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    skillCatalogScope={"mode": "latest", "packageIds": []}
                )
            )

        allow = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(
                skillCatalogScope={
                    "mode": "allowlist",
                    "packageIds": [str(id_a), str(id_b)],
                }
            )
        )
        self.assertEqual(allow.skill_catalog_scope.package_ids, (id_a, id_b))

    def test_context_and_output_budget_coherence(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        # exceed ceiling
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    contextBudget={
                        **_valid_snapshot_dict()["contextBudget"],
                        "maxPromptCharacters": 72_001,
                    }
                )
            )

        # single skill > total skill instruction
        bad_ctx = _valid_snapshot_dict()["contextBudget"]
        bad_ctx = {
            **bad_ctx,
            "maxSkillInstructionCharacters": 1000,
            "maxSingleSkillInstructionCharacters": 2000,
            "maxHistoryCharacters": 1000,
            "maxToolSummaryCharacters": 1000,
            "maxPromptCharacters": 10_000,
        }
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(contextBudget=bad_ctx)
            )

        # sum exceeds prompt
        bad_sum = {
            **_valid_snapshot_dict()["contextBudget"],
            "maxPromptCharacters": 1000,
            "maxSkillInstructionCharacters": 500,
            "maxSingleSkillInstructionCharacters": 100,
            "maxHistoryCharacters": 500,
            "maxToolSummaryCharacters": 500,
        }
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(contextBudget=bad_sum)
            )

        # parallel > total
        bad_out = {
            **_valid_snapshot_dict()["outputBudget"],
            "maxParallelCalls": 8,
            "maxTotalCapabilityCalls": 4,
        }
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(outputBudget=bad_out)
            )

        # same-read > total
        bad_read = {
            **_valid_snapshot_dict()["outputBudget"],
            "maxSameReadSignature": 8,
            "maxTotalCapabilityCalls": 4,
        }
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(outputBudget=bad_read)
            )

        # followup >= provider rounds
        bad_follow = {
            **_valid_snapshot_dict()["outputBudget"],
            "maxCompletionFollowupRounds": 8,
            "maxProviderRounds": 8,
        }
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(outputBudget=bad_follow)
            )

        # zero / negative rejected
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    contextBudget={
                        **_valid_snapshot_dict()["contextBudget"],
                        "maxActiveSkills": 0,
                    }
                )
            )

    def test_global_safety_deny_by_default_required(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(globalSafetyPolicy={"denyByDefault": False})
            )

    def test_legacy_fallback_cannot_enable_after_side_effects(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    fallbackPolicy={
                        "legacyRuntimeAllowed": True,
                        "beforeSideEffectsOnly": False,
                    }
                )
            )
        ok = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(
                fallbackPolicy={
                    "legacyRuntimeAllowed": False,
                    "beforeSideEffectsOnly": False,
                }
            )
        )
        self.assertFalse(ok.fallback_policy.legacy_runtime_allowed)

    def test_unknown_fields_fail(self) -> None:
        from app.assistant.skills.schemas import MainAgentProfileSnapshotV1

        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(unknownField="nope")
            )
        with self.assertRaises((ValidationError, ValueError)):
            MainAgentProfileSnapshotV1.model_validate(
                _valid_snapshot_dict(
                    contextBudget={
                        **_valid_snapshot_dict()["contextBudget"],
                        "extraBudget": 1,
                    }
                )
            )


def _profile_draft_command(
    *,
    base_prompt: str = "admin draft prompt",
    request_id: str | None = None,
    expected_aggregate_revision: int = 0,
    origin: str = "api",
    version_name: str | None = None,
):
    from app.assistant.skills.schemas import (
        MainAgentProfileSnapshotV1,
        SaveMainAgentProfileDraftCommand,
    )

    snap = MainAgentProfileSnapshotV1.model_validate(
        _valid_snapshot_dict(basePrompt=base_prompt)
    )
    return SaveMainAgentProfileDraftCommand(
        snapshot=snap,
        version_name=version_name,
        origin=origin,  # type: ignore[arg-type]
        expected_aggregate_revision=expected_aggregate_revision,
        request_id=request_id or f"profile-draft-{uuid.uuid4().hex[:10]}",
    )


class MainAgentProfileServiceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.skills.service import MainAgentProfileService

        self.db = make_session()
        self.svc = MainAgentProfileService(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_profile_draft_save_does_not_disable_runtime(self) -> None:
        """Draft save must preserve runtime_enabled and published pointer."""
        from app.assistant.skills.models import AssistantMainAgentProfile
        from app.assistant.skills.schemas import PublishMainAgentProfileCommand

        profile = self.svc.ensure_default()
        draft = self.svc.save_draft(
            profile.id,
            _profile_draft_command(
                base_prompt="publishable draft before enable",
                expected_aggregate_revision=0,
            ),
        )
        published = self.svc.publish(
            profile.id,
            PublishMainAgentProfileCommand(
                draft_version_id=draft.id,
                request_id=f"pub-{uuid.uuid4().hex[:8]}",
                expected_aggregate_revision=_current_profile_rev(self.db, profile.id),
            ),
        )
        row = self.db.get(AssistantMainAgentProfile, profile.id)
        assert row is not None
        row.runtime_enabled = True
        self.db.commit()

        enabled = self.svc.get_default()
        self.assertTrue(enabled.runtime_enabled)
        self.assertEqual(enabled.published_version.id, published.id)  # type: ignore[union-attr]

        rev = int(getattr(enabled, "aggregate_revision", 0) or 0)
        # Prefer model field if summary exposes it later; fall back to ORM.
        orm = self.db.get(AssistantMainAgentProfile, profile.id)
        assert orm is not None
        rev = int(orm.aggregate_revision or 0)

        self.svc.save_draft(
            enabled.id,
            _profile_draft_command(
                base_prompt="draft while runtime stays live",
                expected_aggregate_revision=rev,
            ),
        )
        after = self.svc.get_default()
        self.assertTrue(after.runtime_enabled is True)
        self.assertEqual(
            after.published_version_id
            if hasattr(after, "published_version_id")
            else after.published_version.id,  # type: ignore[union-attr]
            enabled.published_version.id,  # type: ignore[union-attr]
        )

    def test_ensure_default_creates_once_and_is_idempotent(self) -> None:
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
        )

        first = self.svc.ensure_default()
        self.assertEqual(first.profile_key, "default")
        self.assertTrue(first.is_default)
        self.assertEqual(first.migration_state, "bootstrap")
        self.assertFalse(first.runtime_enabled)
        self.assertIsNotNone(first.draft_version)
        assert first.draft_version is not None
        self.assertEqual(first.draft_version.sequence_no, 1)
        self.assertEqual(first.draft_version.version_source, "save")
        self.assertEqual(first.draft_version.origin, "bootstrap")
        self.assertIsNone(first.published_version)

        second = self.svc.ensure_default()
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            first.draft_version.id if first.draft_version else None,
            second.draft_version.id if second.draft_version else None,
        )

        count = self.db.query(AssistantMainAgentProfile).count()
        self.assertEqual(count, 1)
        versions = self.db.query(AssistantMainAgentProfileVersion).count()
        self.assertEqual(versions, 1)

        got = self.svc.get_default()
        self.assertEqual(got.id, first.id)

    def test_save_identical_draft_reuses_row_and_moves_pointer(self) -> None:
        from app.assistant.skills.models import AssistantMainAgentProfileVersion
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            SaveMainAgentProfileDraftCommand,
            default_main_agent_profile_snapshot,
        )

        profile = self.svc.ensure_default()
        bootstrap_draft_id = profile.draft_version.id  # type: ignore[union-attr]
        snap = default_main_agent_profile_snapshot()

        # First admin save of a *changed* snapshot advances migration_state.
        changed = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(basePrompt="admin authored prompt v1")
        )
        v2 = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(snapshot=changed, version_name="admin-1", origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-1"),
        )
        self.assertEqual(v2.sequence_no, 2)
        self.assertEqual(v2.version_source, "save")
        refreshed = self.svc.get_default()
        self.assertEqual(refreshed.migration_state, "native")
        self.assertEqual(refreshed.draft_version.id, v2.id)  # type: ignore[union-attr]

        # Identical save reuses the existing draft and re-points pointer.
        again = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(snapshot=changed, version_name="admin-1-again", origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-2"),
        )
        self.assertEqual(again.id, v2.id)
        self.assertEqual(
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(AssistantMainAgentProfileVersion.version_source == "save")
            .count(),
            2,  # bootstrap + one admin
        )

        # Re-saving the original bootstrap content reuses bootstrap draft and
        # moves the draft pointer back (never infer from "latest sequence").
        reused_bootstrap = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(snapshot=snap, origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-3"),
        )
        self.assertEqual(reused_bootstrap.id, bootstrap_draft_id)
        refreshed2 = self.svc.get_default()
        self.assertEqual(
            refreshed2.draft_version.id, bootstrap_draft_id  # type: ignore[union-attr]
        )

    def test_legacy_origin_promotes_bootstrap_to_shadow_not_native(self) -> None:
        """Shadow/migration-owned saves must not steal native ownership."""
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            SaveMainAgentProfileDraftCommand,
        )

        profile = self.svc.ensure_default()
        self.assertEqual(profile.migration_state, "bootstrap")

        legacy_snap = MainAgentProfileSnapshotV1.model_validate(
            _valid_snapshot_dict(basePrompt="legacy bridge prompt")
        )
        draft = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(snapshot=legacy_snap,
                version_name="legacy-general-chat",
                origin="legacy",
                source_ref={"legacySkillName": "general_chat"}, expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-4"),
        )
        self.assertEqual(draft.origin, "legacy")
        refreshed = self.svc.get_default()
        self.assertEqual(refreshed.migration_state, "shadow")
        self.assertEqual(refreshed.draft_version.id, draft.id)  # type: ignore[union-attr]

        # Second legacy save stays shadow.
        again = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(basePrompt="legacy bridge prompt v2")
                ),
                origin="legacy", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-108"),
        )
        refreshed2 = self.svc.get_default()
        self.assertEqual(refreshed2.migration_state, "shadow")
        self.assertEqual(refreshed2.draft_version.id, again.id)  # type: ignore[union-attr]

        # Only api origin promotes to native.
        admin = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(basePrompt="admin takes over")
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-107"),
        )
        refreshed3 = self.svc.get_default()
        self.assertEqual(refreshed3.migration_state, "native")
        self.assertEqual(refreshed3.draft_version.id, admin.id)  # type: ignore[union-attr]

    def test_save_changed_drafts_append_sequence(self) -> None:
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            SaveMainAgentProfileDraftCommand,
        )

        profile = self.svc.ensure_default()
        a = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(basePrompt="prompt-a")
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-106"),
        )
        b = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(basePrompt="prompt-b")
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-105"),
        )
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.sequence_no + 1, b.sequence_no)
        versions = self.svc.list_versions(profile.id)
        self.assertEqual(len(versions), 3)
        self.assertEqual([v.sequence_no for v in versions], [1, 2, 3])

    def test_publish_creates_new_row_and_records_source_draft(self) -> None:
        from app.assistant.skills.models import AssistantMainAgentProfileVersion
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            PublishMainAgentProfileCommand,
            SaveMainAgentProfileDraftCommand,
        )

        profile = self.svc.ensure_default()
        draft = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(basePrompt="ready to publish")
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-104"),
        )
        published = self.svc.publish(
            profile.id,
            PublishMainAgentProfileCommand(draft_version_id=draft.id, request_id="profile-pub-5", expected_aggregate_revision=_current_profile_rev(self.db, profile.id)),
        )
        self.assertEqual(published.version_source, "publish")
        self.assertEqual(published.source_draft_version_id, draft.id)
        self.assertEqual(published.content_digest, draft.content_digest)
        self.assertNotEqual(published.id, draft.id)

        # Draft row is unchanged.
        draft_row = self.db.get(AssistantMainAgentProfileVersion, draft.id)
        assert draft_row is not None
        self.assertEqual(draft_row.version_source, "save")
        self.assertIsNone(draft_row.source_draft_version_id)

        # Pointer advanced; runtime still disabled.
        refreshed = self.svc.get_default()
        self.assertEqual(refreshed.published_version.id, published.id)  # type: ignore[union-attr]
        self.assertFalse(refreshed.runtime_enabled)

        # Publishing the same draft again still creates a distinct publish row.
        published2 = self.svc.publish(
            profile.id,
            PublishMainAgentProfileCommand(draft_version_id=draft.id, request_id="profile-pub-6", expected_aggregate_revision=_current_profile_rev(self.db, profile.id)),
        )
        self.assertNotEqual(published2.id, published.id)
        self.assertEqual(published2.source_draft_version_id, draft.id)
        self.assertEqual(published2.content_digest, published.content_digest)

    def test_publish_rejects_cross_profile_draft(self) -> None:
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
        )
        from app.assistant.skills.schemas import (
            PublishMainAgentProfileCommand,
            default_main_agent_profile_snapshot,
        )
        from app.common.exceptions import ApiException

        profile = self.svc.ensure_default()
        other = AssistantMainAgentProfile(
            profile_key="other",
            display_name="Other",
            is_default=False,
            migration_state="native",
            runtime_enabled=False,
        )
        self.db.add(other)
        self.db.flush()
        snap = default_main_agent_profile_snapshot()
        foreign_draft = AssistantMainAgentProfileVersion(
            profile_id=other.id,
            sequence_no=1,
            version_name="foreign",
            version_source="save",
            origin="api",
            snapshot=snap.normalized_payload(),
            content_digest=snap.content_digest(),
        )
        self.db.add(foreign_draft)
        self.db.commit()

        with self.assertRaises(ApiException) as ctx:
            self.svc.publish(
                profile.id,
                PublishMainAgentProfileCommand(draft_version_id=foreign_draft.id, request_id="profile-pub-7", expected_aggregate_revision=_current_profile_rev(self.db, profile.id)),
            )
        self.assertEqual(ctx.exception.code, 40493)

    def test_publish_rejects_partial_control_keys(self) -> None:
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            PublishMainAgentProfileCommand,
            SaveMainAgentProfileDraftCommand,
        )
        from app.common.exceptions import ApiException

        profile = self.svc.ensure_default()
        draft = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(
                        basePrompt="with controls",
                        controlCapabilityKeys=["skill.inject"],
                    )
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-103"),
        )
        # Draft save is allowed for editing.
        self.assertEqual(draft.version_source, "save")

        with self.assertRaises(ApiException) as ctx:
            self.svc.publish(
                profile.id,
                PublishMainAgentProfileCommand(draft_version_id=draft.id, request_id="profile-pub-8", expected_aggregate_revision=_current_profile_rev(self.db, profile.id)),
            )
        self.assertEqual(ctx.exception.code, 42294)

    def test_publish_accepts_exact_four_control_keys(self) -> None:
        from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            PublishMainAgentProfileCommand,
            SaveMainAgentProfileDraftCommand,
        )

        profile = self.svc.ensure_default()
        draft = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(
                        basePrompt="plan04 controls ready",
                        controlCapabilityKeys=list(MAIN_AGENT_CONTROL_KEYS),
                    )
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-102"),
        )
        published = self.svc.publish(
            profile.id,
            PublishMainAgentProfileCommand(draft_version_id=draft.id, request_id="profile-pub-9", expected_aggregate_revision=_current_profile_rev(self.db, profile.id)),
        )
        self.assertEqual(published.version_source, "publish")
        detail = self.svc.get_version(profile.id, published.id)
        self.assertEqual(
            tuple(detail.snapshot.get("controlCapabilityKeys") or ()),
            MAIN_AGENT_CONTROL_KEYS,
        )
        # Publish never auto-enables runtime.
        self.assertFalse(self.svc.get_default().runtime_enabled)

    def test_concurrent_default_creation_converges(self) -> None:
        from app.assistant.skills.models import AssistantMainAgentProfile
        from app.assistant.skills.service import MainAgentProfileService
        from app.common.exceptions import ApiException

        # Simulate race: insert a default outside the service after a failed path
        # by calling ensure_default twice on separate sessions against shared state
        # is hard with in-memory SQLite. Instead, assert unique constraint + service
        # conflict translation when a second default is forced.
        first = self.svc.ensure_default()
        self.assertTrue(first.is_default)

        # Direct insert of a second default must fail uniqueness.
        from sqlalchemy.exc import IntegrityError

        dup = AssistantMainAgentProfile(
            profile_key="default-2",
            display_name="Dup",
            is_default=True,
            migration_state="bootstrap",
            runtime_enabled=False,
        )
        self.db.add(dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # ensure_default remains stable.
        again = MainAgentProfileService(self.db).ensure_default()
        self.assertEqual(again.id, first.id)
        self.assertEqual(self.db.query(AssistantMainAgentProfile).count(), 1)

        # get_default before ensure raises 40493 when empty.
        from tests._db import make_session

        empty = make_session()
        empty_svc = MainAgentProfileService(empty)
        with self.assertRaises(ApiException) as ctx:
            empty_svc.get_default()
        self.assertEqual(ctx.exception.code, 40493)
        empty.close()

    def test_service_has_no_version_update_or_delete(self) -> None:
        from app.assistant.skills.service import MainAgentProfileService

        forbidden = {
            "update_version",
            "delete_version",
            "update_draft",
            "delete_draft",
            "trim_versions",
            "restore_version",
        }
        public = {name for name in dir(MainAgentProfileService) if not name.startswith("_")}
        self.assertTrue(forbidden.isdisjoint(public))
        for method in (
            "ensure_default",
            "get_default",
            "save_draft",
            "list_versions",
            "publish",
        ):
            self.assertTrue(callable(getattr(MainAgentProfileService, method)))

    def test_does_not_touch_general_chat_or_runtime(self) -> None:
        """No action here changes old general_chat routing or assistant runtime."""
        from app.assistant_config.models import AssistantAgentProfile, AssistantSkill
        from app.assistant.skills.models import AssistantMainAgentProfile

        agent = AssistantAgentProfile(
            name="general-chat-agent",
            description="legacy agent for general chat",
            system_prompt="hello",
            is_system=True,
            enabled=True,
        )
        self.db.add(agent)
        self.db.flush()
        legacy = AssistantSkill(
            name="general_chat",
            description="legacy general chat",
            enabled=True,
            is_system=True,
            agent_profile_id=agent.id,
            mode="langgraph",
            langgraph_pattern="agent_loop",
        )
        self.db.add(legacy)
        self.db.commit()
        legacy_id = legacy.id
        legacy_enabled = legacy.enabled
        legacy_is_system = legacy.is_system
        agent_id = agent.id

        profile = self.svc.ensure_default()
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            PublishMainAgentProfileCommand,
            SaveMainAgentProfileDraftCommand,
        )

        draft = self.svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=MainAgentProfileSnapshotV1.model_validate(
                    _valid_snapshot_dict(basePrompt="still disabled runtime")
                ),
                origin="api", expected_aggregate_revision=_current_profile_rev(self.db, profile.id), request_id="profile-draft-101"),
        )
        self.svc.publish(
            profile.id, PublishMainAgentProfileCommand(draft_version_id=draft.id, request_id="profile-pub-10", expected_aggregate_revision=_current_profile_rev(self.db, profile.id))
        )

        # Legacy row untouched.
        reloaded = self.db.get(AssistantSkill, legacy_id)
        assert reloaded is not None
        self.assertEqual(reloaded.name, "general_chat")
        self.assertEqual(reloaded.enabled, legacy_enabled)
        self.assertEqual(reloaded.is_system, legacy_is_system)
        self.assertEqual(reloaded.agent_profile_id, agent_id)

        # Profile never enables runtime; no general-chat package.
        row = self.db.get(AssistantMainAgentProfile, profile.id)
        assert row is not None
        self.assertFalse(row.runtime_enabled)
        self.assertNotEqual(row.profile_key, "general_chat")
        self.assertNotEqual(row.profile_key, "general-chat")

        from app.assistant.skills.models import AssistantSkillPackage

        packages = self.db.query(AssistantSkillPackage).all()
        self.assertEqual(packages, [])


if __name__ == "__main__":
    unittest.main()
