"""fix capability attempt lifecycle trigger

Revision ID: f2c3a4b5d6e7
Revises: 984c07876856
Create Date: 2026-07-18 18:00:00.000000

Plan 08 follow-up: preserve the shipped ledger revision while replacing its
unconditional Attempt UPDATE rejection with a constrained lifecycle guard.
"""

from __future__ import annotations

from alembic import op


revision = "f2c3a4b5d6e7"
down_revision = "984c07876856"
branch_labels = None
depends_on = None


_LIFECYCLE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION mindatlas_capability_call_attempt_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_APPEND_ONLY: attempts cannot be deleted'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.call_id IS DISTINCT FROM OLD.call_id
       OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
       OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
       OR NEW.lease_generation IS DISTINCT FROM OLD.lease_generation
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.dispatch_deadline_at IS DISTINCT FROM OLD.dispatch_deadline_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_IMMUTABLE: identity/counter fields are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF (OLD.ended_at IS NOT NULL
            AND NEW.ended_at IS DISTINCT FROM OLD.ended_at)
       OR (OLD.external_request_id IS NOT NULL
            AND NEW.external_request_id IS DISTINCT FROM OLD.external_request_id)
       OR (OLD.external_idempotency_echo IS NOT NULL
            AND NEW.external_idempotency_echo IS DISTINCT FROM OLD.external_idempotency_echo)
       OR (OLD.request_digest IS NOT NULL
            AND NEW.request_digest IS DISTINCT FROM OLD.request_digest)
       OR (OLD.response_digest IS NOT NULL
            AND NEW.response_digest IS DISTINCT FROM OLD.response_digest)
       OR (OLD.transport_status IS NOT NULL
            AND NEW.transport_status IS DISTINCT FROM OLD.transport_status)
       OR (OLD.side_effect_started AND NOT NEW.side_effect_started)
       OR (OLD.side_effect_started_at IS NOT NULL
            AND NEW.side_effect_started_at IS DISTINCT FROM OLD.side_effect_started_at)
       OR (OLD.error_code IS NOT NULL
            AND NEW.error_code IS DISTINCT FROM OLD.error_code)
       OR (OLD.retry_classification IS NOT NULL
            AND NEW.retry_classification IS DISTINCT FROM OLD.retry_classification)
       OR (OLD.diagnostic_artifact_id IS NOT NULL
            AND NEW.diagnostic_artifact_id IS DISTINCT FROM OLD.diagnostic_artifact_id)
    THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_IMMUTABLE: captured evidence is immutable once set'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NOT (
        (OLD.status = 'claimed'
            AND NEW.status IN ('dispatched','failed','abandoned'))
        OR (OLD.status = 'dispatched'
            AND NEW.status IN ('response_received','failed','uncertain'))
        OR (OLD.status = 'response_received'
            AND NEW.status IN ('committed','failed','uncertain'))
    ) THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_TRANSITION: illegal attempt status transition % -> %',
            OLD.status, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status = 'dispatched'
       AND (NEW.request_digest IS NULL OR NEW.response_digest IS NOT NULL)
    THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_EVIDENCE: dispatched requires request evidence only'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status IN ('response_received','committed')
       AND (NEW.request_digest IS NULL OR NEW.response_digest IS NULL)
    THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_EVIDENCE: captured response requires request and response digests'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status IN ('committed','failed','uncertain','abandoned')
       AND NEW.ended_at IS NULL
    THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_EVIDENCE: terminal attempt requires ended_at'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status IN ('claimed','dispatched','response_received')
       AND NEW.ended_at IS NOT NULL
    THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_ATTEMPT_EVIDENCE: active attempt cannot have ended_at'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


_UNCONDITIONAL_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION mindatlas_capability_call_attempt_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'MINDATLAS_PLAN08_ATTEMPT_APPEND_ONLY: attempts are append-only'
        USING ERRCODE = 'integrity_constraint_violation';
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_LIFECYCLE_FUNCTION_SQL)


def downgrade() -> None:
    op.execute(_UNCONDITIONAL_FUNCTION_SQL)
