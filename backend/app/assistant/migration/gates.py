"""Inherited Plan 06/07/08/09 ship-gate matrix for Plan 10 stages."""

from __future__ import annotations

from app.assistant.migration.contracts import ProductionCutoverStatus, UpstreamGateEntry

# Stage applicability:
# - missing read gates fix shadow/read percentages at zero
# - missing write gates fix write mode off
# - missing Plan 09 auth blocks production cutover entirely (local tooling OK)
GATE_MATRIX: tuple[UpstreamGateEntry, ...] = (
    UpstreamGateEntry(
        plan="06",
        gate_id="plan06_preinsert_runtime_choice",
        description="Admission chooses final runtime before AssistantChatRun insert",
        satisfied=True,
        blocks_stages=("shadow", "read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-06-task10-rollout.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="06",
        gate_id="plan06_single_nonterminal_run",
        description="At most one nonterminal production Run per conversation scope",
        satisfied=True,
        blocks_stages=("shadow", "read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-06-task10-rollout.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="06",
        gate_id="plan06_lease_recovery_sse_memory_cas",
        description="Lease/recovery/SSE/memory CAS vectors pass",
        satisfied=True,
        blocks_stages=("shadow", "read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-06-task10-rollout.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="07",
        gate_id="plan07_interrupt_cas",
        description="Waiting/resolve uses single Run-first interrupt CAS",
        satisfied=True,
        blocks_stages=("read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-07-task10-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="07",
        gate_id="plan07_entrypoint_decision_channels",
        description="Durable decision-channel coverage for every admitted entrypoint",
        satisfied=True,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-07-task9-golden-path.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="08",
        gate_id="plan08_independent_write_grant",
        description="Independent write-grant derivation passes",
        satisfied=True,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-08-task9-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="08",
        gate_id="plan08_call_owned_approval",
        description="Call-owned approval path passes",
        satisfied=True,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-08-task9-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="08",
        gate_id="plan08_cancel_started_settlement",
        description="cancel x started-call settlement passes",
        satisfied=True,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-08-task9-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="08",
        gate_id="plan08_idempotency_reconciliation",
        description="Idempotency and reconciliation vectors pass",
        satisfied=True,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-08-task9-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="09",
        gate_id="plan09_publish_gate_mode_enforce",
        description="Publish gate mode enforce for production pointer advances",
        satisfied=False,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-09-task9-verification.md",
        reason_code="publish_gate_default_observe",
        local_tooling_allowed=True,
    ),
    UpstreamGateEntry(
        plan="09",
        gate_id="plan09_gate_use_on_enable",
        description="Enabled Skill/Profile pointer advances require matching gate-use evidence",
        satisfied=True,
        blocks_stages=("write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-09-task9-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="09",
        gate_id="plan09_eval_isolation",
        description="Eval isolation tripwires pass",
        satisfied=True,
        blocks_stages=("shadow", "read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-09-task9-verification.md",
        reason_code=None,
    ),
    UpstreamGateEntry(
        plan="09",
        gate_id="plan09_operator_principal",
        description=(
            "Real server-side assistant-config principal/operator RBAC guard "
            "on every mounted admin/diagnostic/migration route"
        ),
        satisfied=False,
        blocks_stages=("shadow", "read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-09-task9-verification.md",
        reason_code="plan09_operator_principal_missing",
        local_tooling_allowed=True,
    ),
    UpstreamGateEntry(
        plan="09",
        gate_id="plan09_m4_release_complete",
        description="Plan 09 M4 release-complete (not merely code-complete behind trusted mount)",
        satisfied=False,
        blocks_stages=("shadow", "read", "write", "cleanup"),
        evidence_ref="docs/superpowers/evidence/plan-09-task9-verification.md",
        reason_code="plan09_m4_not_release_complete",
        local_tooling_allowed=True,
    ),
    UpstreamGateEntry(
        plan="02B",
        gate_id="plan02b_shared_only_openclaw",
        description="OpenClaw on shared capability runtime; no legacy OpenClaw execution owner",
        satisfied=True,
        blocks_stages=("cleanup",),
        evidence_ref="docs/superpowers/evidence/plan-02b-final.md",
        reason_code=None,
        local_tooling_allowed=True,
    ),
)


def production_cutover_blocked() -> ProductionCutoverStatus:
    """Production cutover is blocked when any unsatisfied gate blocks stages."""
    unsatisfied = [g for g in GATE_MATRIX if not g.satisfied]
    reason_codes: list[str] = []
    for gate in unsatisfied:
        if gate.reason_code:
            reason_codes.append(gate.reason_code)
        else:
            reason_codes.append(gate.gate_id)
    # Auth / M4 incompleteness blocks production; local tooling remains allowed
    # when every unsatisfied gate still permits local tooling.
    local_ok = all(g.local_tooling_allowed for g in unsatisfied) if unsatisfied else True
    blocked = any(
        stage in g.blocks_stages
        for g in unsatisfied
        for stage in ("shadow", "read", "write", "cleanup")
    )
    return ProductionCutoverStatus(
        blocked=blocked,
        local_tooling_allowed=local_ok,
        reason_codes=tuple(reason_codes),
    )


def unsatisfied_gates_for_stage(stage: str) -> tuple[UpstreamGateEntry, ...]:
    return tuple(
        g
        for g in GATE_MATRIX
        if not g.satisfied and stage in g.blocks_stages
    )


__all__ = (
    "GATE_MATRIX",
    "production_cutover_blocked",
    "unsatisfied_gates_for_stage",
)
