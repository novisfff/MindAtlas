"""add durable run interrupts

Revision ID: 7a3dac0ac2a8
Revises: 6af373ef040f
Create Date: 2026-07-16 11:23:18.081767

Plan 07 Task 4: durable human Interrupt table, partial unique pending index,
resolution request idempotency unique, request/suspension immutability trigger
(token rotation + one pending->terminal only), same-run FK guards, and
downgrade refusal when active/waiting durable Runs or Interrupt history remain.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "7a3dac0ac2a8"
down_revision = "6af373ef040f"
branch_labels = None
depends_on = None


DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN07_DOWNGRADE_BLOCKED_INTERRUPT_DATA"

ACTIVE_OR_WAITING_STATUS_SQL = (
    "'queued','running','recovering','waiting_approval','waiting_input',"
    "'cancelling','needs_reconciliation'"
)


def upgrade() -> None:
    op.create_table(
        "assistant_run_interrupt",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("interrupt_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "checkpoint_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_run_checkpoint.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "resolution_checkpoint_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_run_checkpoint.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "manifest_revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_run_manifest_revision.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_skill_package_id", UUID(as_uuid=True), nullable=True),
        sa.Column("owner_skill_version_id", UUID(as_uuid=True), nullable=True),
        # Always null in Plan 07; Plan 08 adds FK/population.
        sa.Column("capability_call_id", UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_frame_id", UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("node_visit_id", sa.String(length=160), nullable=False),
        sa.Column(
            "request_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("request_run_revision", sa.Integer(), nullable=False),
        sa.Column("resolution_run_revision", sa.Integer(), nullable=True),
        sa.Column(
            "budget_revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_run_budget_revision.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("budget_suspension_state", sa.JSON(), nullable=False),
        sa.Column("budget_suspension_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "resolution_budget_revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_run_budget_revision.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("field_schema", sa.JSON(), nullable=True),
        sa.Column("field_schema_digest", sa.String(length=64), nullable=True),
        sa.Column("initial_values", sa.JSON(), nullable=False),
        sa.Column("submitted_values", sa.JSON(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.String(length=4000), nullable=True),
        sa.Column("resume_token_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "token_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("resolution_request_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_digest", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('approval','input')",
            name="ck_assistant_run_interrupt_kind",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending','approved','rejected','submitted','cancelled','expired'"
            ")",
            name="ck_assistant_run_interrupt_status",
        ),
        sa.CheckConstraint(
            "request_revision > 0",
            name="ck_assistant_run_interrupt_request_revision",
        ),
        sa.CheckConstraint(
            "request_run_revision >= 0",
            name="ck_assistant_run_interrupt_request_run_revision",
        ),
        sa.CheckConstraint(
            "resolution_run_revision IS NULL OR resolution_run_revision >= 0",
            name="ck_assistant_run_interrupt_resolution_run_revision",
        ),
        sa.CheckConstraint(
            "token_revision >= 0",
            name="ck_assistant_run_interrupt_token_revision",
        ),
        sa.CheckConstraint(
            "("
            "  kind = 'approval'"
            ") OR ("
            "  kind = 'input'"
            "  AND field_schema IS NOT NULL"
            "  AND field_schema_digest IS NOT NULL"
            ")",
            name="ck_assistant_run_interrupt_input_schema",
        ),
        sa.CheckConstraint(
            "("
            "  resolution_budget_revision_id IS NULL"
            "  AND resolution_checkpoint_id IS NULL"
            ") OR ("
            "  resolution_budget_revision_id IS NOT NULL"
            "  AND resolution_checkpoint_id IS NOT NULL"
            ")",
            name="ck_assistant_run_interrupt_resolution_pair",
        ),
        sa.CheckConstraint(
            "length(budget_suspension_digest) = 64",
            name="ck_assistant_run_interrupt_budget_suspension_digest",
        ),
        sa.CheckConstraint(
            "length(request_digest) = 64",
            name="ck_assistant_run_interrupt_request_digest",
        ),
        sa.CheckConstraint(
            "field_schema_digest IS NULL OR length(field_schema_digest) = 64",
            name="ck_assistant_run_interrupt_field_schema_digest",
        ),
        sa.CheckConstraint(
            "resume_token_digest IS NULL OR length(resume_token_digest) = 64",
            name="ck_assistant_run_interrupt_resume_token_digest",
        ),
        sa.CheckConstraint(
            "resolution_digest IS NULL OR length(resolution_digest) = 64",
            name="ck_assistant_run_interrupt_resolution_digest",
        ),
    )

    op.create_index(
        "ix_assistant_run_interrupt_run_id",
        "assistant_run_interrupt",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "uq_assistant_run_interrupt_run_key",
        "assistant_run_interrupt",
        ["run_id", "interrupt_key"],
        unique=True,
    )
    # One pending Interrupt per Run (concurrent-state exclusivity only).
    op.create_index(
        "uq_assistant_run_interrupt_one_pending",
        "assistant_run_interrupt",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_assistant_run_interrupt_resolution_request",
        "assistant_run_interrupt",
        ["run_id", "resolution_request_id"],
        unique=True,
        postgresql_where=sa.text("resolution_request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_assistant_run_interrupt_run_status",
        "assistant_run_interrupt",
        ["run_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_run_interrupt_expires_at",
        "assistant_run_interrupt",
        ["expires_at"],
        unique=False,
    )

    _create_interrupt_mutation_trigger()
    _create_interrupt_same_run_guard()


def _create_interrupt_mutation_trigger() -> None:
    """Permit only token rotation and one pending -> terminal resolution mutation.

    DELETE allowed only under Plan 06 controlled purge flag.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_assistant_run_interrupt_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            allow_purge text;
            token_only boolean;
            resolve_only boolean;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                allow_purge := current_setting('mindatlas.allow_durable_run_purge', true);
                IF allow_purge IS DISTINCT FROM 'on' THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN07_IMMUTABLE_INTERRUPT: DELETE requires '
                        'SET LOCAL mindatlas.allow_durable_run_purge = ''on'''
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN OLD;
            END IF;

            -- Immutable request / suspension identity.
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.run_id IS DISTINCT FROM OLD.run_id
               OR NEW.interrupt_key IS DISTINCT FROM OLD.interrupt_key
               OR NEW.kind IS DISTINCT FROM OLD.kind
               OR NEW.checkpoint_id IS DISTINCT FROM OLD.checkpoint_id
               OR NEW.manifest_revision_id IS DISTINCT FROM OLD.manifest_revision_id
               OR NEW.owner_skill_package_id IS DISTINCT FROM OLD.owner_skill_package_id
               OR NEW.owner_skill_version_id IS DISTINCT FROM OLD.owner_skill_version_id
               OR NEW.capability_call_id IS DISTINCT FROM OLD.capability_call_id
               OR NEW.workflow_frame_id IS DISTINCT FROM OLD.workflow_frame_id
               OR NEW.node_id IS DISTINCT FROM OLD.node_id
               OR NEW.node_visit_id IS DISTINCT FROM OLD.node_visit_id
               OR NEW.request_revision IS DISTINCT FROM OLD.request_revision
               OR NEW.request_run_revision IS DISTINCT FROM OLD.request_run_revision
               OR NEW.budget_revision_id IS DISTINCT FROM OLD.budget_revision_id
               OR NEW.budget_suspension_state::jsonb IS DISTINCT FROM OLD.budget_suspension_state::jsonb
               OR NEW.budget_suspension_digest IS DISTINCT FROM OLD.budget_suspension_digest
               OR NEW.request_payload::jsonb IS DISTINCT FROM OLD.request_payload::jsonb
               OR NEW.request_digest IS DISTINCT FROM OLD.request_digest
               OR NEW.field_schema::jsonb IS DISTINCT FROM OLD.field_schema::jsonb
               OR NEW.field_schema_digest IS DISTINCT FROM OLD.field_schema_digest
               OR NEW.initial_values::jsonb IS DISTINCT FROM OLD.initial_values::jsonb
               OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN07_IMMUTABLE_INTERRUPT: request/suspension identity is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            -- Token rotation: pending stays pending; only token fields + updated_at change.
            token_only := (
                OLD.status = 'pending'
                AND NEW.status = 'pending'
                AND NEW.token_revision = OLD.token_revision + 1
                AND NEW.resume_token_digest IS DISTINCT FROM OLD.resume_token_digest
                AND NEW.resolution_checkpoint_id IS NOT DISTINCT FROM OLD.resolution_checkpoint_id
                AND NEW.resolution_budget_revision_id IS NOT DISTINCT FROM OLD.resolution_budget_revision_id
                AND NEW.resolution_run_revision IS NOT DISTINCT FROM OLD.resolution_run_revision
                AND NEW.submitted_values::jsonb IS NOT DISTINCT FROM OLD.submitted_values::jsonb
                AND NEW.decision IS NOT DISTINCT FROM OLD.decision
                AND NEW.comment IS NOT DISTINCT FROM OLD.comment
                AND NEW.resolution_request_id IS NOT DISTINCT FROM OLD.resolution_request_id
                AND NEW.resolution_digest IS NOT DISTINCT FROM OLD.resolution_digest
                AND NEW.resolved_at IS NOT DISTINCT FROM OLD.resolved_at
            );
            IF token_only THEN
                RETURN NEW;
            END IF;

            -- One pending -> terminal resolution mutation.
            resolve_only := (
                OLD.status = 'pending'
                AND NEW.status IN ('approved','rejected','submitted','cancelled','expired')
                AND NEW.decision IS NOT NULL
                AND NEW.resolved_at IS NOT NULL
                AND NEW.resolution_request_id IS NOT NULL
                AND NEW.resolution_digest IS NOT NULL
                -- Token is consumed (cleared) or left null; revision must not decrease.
                AND NEW.token_revision >= OLD.token_revision
            );
            IF resolve_only THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION
                'MINDATLAS_PLAN07_IMMUTABLE_INTERRUPT: only token rotation or one pending->terminal resolution is allowed'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_run_interrupt_mutation
        BEFORE UPDATE OR DELETE ON assistant_run_interrupt
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_assistant_run_interrupt_mutation();
        """
    )


def _create_interrupt_same_run_guard() -> None:
    """Linked checkpoint / budget / manifest revisions must belong to the same Run."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_interrupt_same_run_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner uuid;
        BEGIN
            SELECT run_id INTO owner FROM assistant_run_checkpoint
             WHERE id = NEW.checkpoint_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN07_POINTER_OWNERSHIP: interrupt checkpoint mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.resolution_checkpoint_id IS NOT NULL THEN
                SELECT run_id INTO owner FROM assistant_run_checkpoint
                 WHERE id = NEW.resolution_checkpoint_id;
                IF owner IS NULL OR owner <> NEW.run_id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN07_POINTER_OWNERSHIP: interrupt resolution checkpoint mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            SELECT run_id INTO owner FROM assistant_run_manifest_revision
             WHERE id = NEW.manifest_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN07_POINTER_OWNERSHIP: interrupt manifest revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            SELECT run_id INTO owner FROM assistant_run_budget_revision
             WHERE id = NEW.budget_revision_id;
            IF owner IS NULL OR owner <> NEW.run_id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN07_POINTER_OWNERSHIP: interrupt budget revision mismatch'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.resolution_budget_revision_id IS NOT NULL THEN
                SELECT run_id INTO owner FROM assistant_run_budget_revision
                 WHERE id = NEW.resolution_budget_revision_id;
                IF owner IS NULL OR owner <> NEW.run_id THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN07_POINTER_OWNERSHIP: interrupt resolution budget revision mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_assistant_run_interrupt_same_run
        AFTER INSERT OR UPDATE ON assistant_run_interrupt
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_interrupt_same_run_guard();
        """
    )


def _has_blocking_interrupt_data(conn) -> bool:
    """Block downgrade when active/waiting durable Runs or any Interrupt history remain."""
    active = conn.execute(
        sa.text(
            f"""
            SELECT COUNT(*) FROM assistant_chat_run
            WHERE runtime_kind = 'main_agent'
              AND status IN ({ACTIVE_OR_WAITING_STATUS_SQL})
            """
        )
    ).scalar()
    if int(active or 0) > 0:
        return True
    interrupts = conn.execute(
        sa.text("SELECT COUNT(*) FROM assistant_run_interrupt")
    ).scalar()
    return int(interrupts or 0) > 0


def downgrade() -> None:
    """Drop Plan 07 Interrupt schema.

    Refusal rule: block when any active/waiting durable Main Agent Run exists or
    any Interrupt history remains (including terminal rows). Empty disposable
    databases allow upgrade -> downgrade -> upgrade without an env ack.
    """
    conn = op.get_bind()
    if _has_blocking_interrupt_data(conn):
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: active/waiting durable Main Agent Runs or "
            "unacknowledged Interrupt history exist; drain/reconcile Runs and purge "
            "Interrupt rows before downgrade"
        )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_run_interrupt_same_run "
        "ON assistant_run_interrupt"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_run_interrupt_mutation "
        "ON assistant_run_interrupt"
    )
    op.execute("DROP FUNCTION IF EXISTS mindatlas_interrupt_same_run_guard()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_assistant_run_interrupt_mutation()")

    op.drop_index(
        "ix_assistant_run_interrupt_expires_at",
        table_name="assistant_run_interrupt",
    )
    op.drop_index(
        "ix_assistant_run_interrupt_run_status",
        table_name="assistant_run_interrupt",
    )
    op.drop_index(
        "uq_assistant_run_interrupt_resolution_request",
        table_name="assistant_run_interrupt",
    )
    op.drop_index(
        "uq_assistant_run_interrupt_one_pending",
        table_name="assistant_run_interrupt",
    )
    op.drop_index(
        "uq_assistant_run_interrupt_run_key",
        table_name="assistant_run_interrupt",
    )
    op.drop_index(
        "ix_assistant_run_interrupt_run_id",
        table_name="assistant_run_interrupt",
    )
    op.drop_table("assistant_run_interrupt")
