"""Raw PostgreSQL fixtures for complete current-schema Main Agent Runs.

These helpers deliberately avoid the ORM when creating a Run fixture.  A
historical migration test may import the current ORM while exercising an older
schema, but a current-schema fixture must satisfy every frozen Run invariant
introduced by the Plan 2 head.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_9 = "9" * 64


def _unique_digest() -> str:
    return f"{uuid.uuid4().hex}{uuid.uuid4().hex}"


def insert_complete_main_agent_run(
    engine: Engine,
    *,
    status: str = "queued",
    state_revision: int = 0,
    last_event_seq: int = 0,
    checkpoint_seq: int = 0,
    runtime_contract_version: int = 1,
    required_checkpoint_codec_version: int = 3,
    required_capability_feature_digest: str = DIGEST_F,
    required_app_build_revision: str = "build-pg-1",
    runtime_closure_digest: str = DIGEST_9,
    capability_ledger_mode: str = "enforced",
    memory_commit_status: str = "pending",
) -> uuid.UUID:
    """Insert a Run that satisfies the complete Plan 2 frozen shape."""
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    profile_version_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    model_id = uuid.uuid4()
    rollout_revision_id = uuid.uuid4()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assistant_conversation
                    (id, title, is_archived, created_at, updated_at)
                VALUES (:id, :title, false, NOW(), NOW())
                """
            ),
            {
                "id": conversation_id,
                "title": f"plan2-run-{conversation_id.hex[:8]}",
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO ai_credential (
                    id, name, base_url, api_key_encrypted, api_key_hint,
                    runtime_revision, created_at, updated_at
                ) VALUES (
                    :id, :name, 'https://example.test/v1', 'enc-test', '****',
                    1, NOW(), NOW()
                )
                """
            ),
            {"id": credential_id, "name": f"cred-{credential_id.hex[:8]}"},
        )
        conn.execute(
            text(
                """
                INSERT INTO ai_model (
                    id, credential_id, name, model_type, runtime_revision,
                    created_at, updated_at
                ) VALUES (
                    :id, :credential_id, 'gpt-test', 'llm', 1, NOW(), NOW()
                )
                """
            ),
            {"id": model_id, "credential_id": credential_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_profile (
                    id, profile_key, display_name, is_default, migration_state,
                    runtime_enabled, aggregate_revision, created_at, updated_at
                ) VALUES (
                    :id, :profile_key, 'Main Agent', false, 'native',
                    false, 0, NOW(), NOW()
                )
                """
            ),
            {"id": profile_id, "profile_key": f"pg-{profile_id.hex[:16]}"},
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_profile_version (
                    id, profile_id, sequence_no, version_name, version_source,
                    origin, snapshot, content_digest, created_at
                ) VALUES (
                    :id, :profile_id, 1, 'v1', 'save', 'api',
                    CAST(:snapshot AS json), :digest, NOW()
                )
                """
            ),
            {
                "id": profile_version_id,
                "profile_id": profile_id,
                "snapshot": '{"schemaVersion": 2}',
                "digest": DIGEST_A,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_rollout_revision (
                    id, revision_label, profile_version_id, profile_content_digest,
                    model_id, model_identity_digest, package_closure_json,
                    package_closure_digest, capability_closure_digest,
                    seed_manifest_digest, build_revision, runtime_contract_version,
                    checkpoint_codec_version, capability_feature_digest,
                    revision_digest, prepared_reason, created_at
                ) VALUES (
                    :id, :label, :profile_version_id, :profile_digest,
                    :model_id, :model_digest, CAST(:package_closure AS json),
                    :package_digest, :capability_digest, :seed_digest, :build, :contract,
                    :codec, :feature_digest, :revision_digest, 'test', NOW()
                )
                """
            ),
            {
                "id": rollout_revision_id,
                "label": f"main-agent-{rollout_revision_id.hex[:24]}",
                "profile_version_id": profile_version_id,
                "profile_digest": DIGEST_A,
                "model_id": model_id,
                "model_digest": DIGEST_B,
                "package_closure": "[]",
                "package_digest": DIGEST_C,
                "capability_digest": DIGEST_D,
                "seed_digest": DIGEST_E,
                "build": required_app_build_revision,
                "contract": runtime_contract_version,
                "codec": required_checkpoint_codec_version,
                "feature_digest": required_capability_feature_digest,
                "revision_digest": _unique_digest(),
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_chat_run (
                    id, conversation_id, status, runtime_kind,
                    main_agent_rollout_revision_id, main_agent_profile_version_id,
                    resolved_model_id, runtime_closure_digest,
                    runtime_contract_version, required_checkpoint_codec_version,
                    required_capability_feature_digest, required_app_build_revision,
                    capability_ledger_mode, memory_commit_status,
                    last_event_seq, checkpoint_seq, state_revision,
                    lease_generation, recovery_count, created_at, updated_at
                ) VALUES (
                    :id, :conversation_id, :status, 'main_agent',
                    :rollout_id, :profile_version_id, :model_id, :closure_digest,
                    :contract, :codec, :feature_digest, :build,
                    :ledger_mode, :memory_status,
                    :last_event_seq, :checkpoint_seq, :state_revision,
                    0, 0, NOW(), NOW()
                )
                """
            ),
            {
                "id": run_id,
                "conversation_id": conversation_id,
                "status": status,
                "rollout_id": rollout_revision_id,
                "profile_version_id": profile_version_id,
                "model_id": model_id,
                "closure_digest": runtime_closure_digest,
                "contract": runtime_contract_version,
                "codec": required_checkpoint_codec_version,
                "feature_digest": required_capability_feature_digest,
                "build": required_app_build_revision,
                "ledger_mode": capability_ledger_mode,
                "memory_status": memory_commit_status,
                "last_event_seq": last_event_seq,
                "checkpoint_seq": checkpoint_seq,
                "state_revision": state_revision,
            },
        )
    return run_id
