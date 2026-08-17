"""Create-entry release gate and ledger admission helpers.

Configuration rejects create-entry write without enforced ledger / strong HMAC.
The checked-in workflow fixture is create-only; full smart_capture remains denied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal
from uuid import UUID

from app.assistant.policy.contracts import (
    GOLDEN_WRITE_LATTICE_PREFIX,
    GoldenWriteReleaseV1,
    build_golden_write_release,
)
from app.assistant.policy.write_admission import GOLDEN_CREATE_ENTRY_DOMAIN_KEY
from app.assistant.capability_calls.write_guard import WRITE_COHORT_DIGEST
from app.assistant.workflow.system_assets.registry import (
    SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
    get_system_asset,
)
from app.config import Settings, get_settings

FORBIDDEN_GOLDEN_NODE_TYPES = frozenset(
    {
        "human_in_loop",
        "workflow_call",
        "code_executor",
        "http",
        "iteration",
    }
)
FORBIDDEN_GOLDEN_TOOLS = frozenset(
    {
        "update_entry",
        "create_relation",
        "openclaw_capture_entry",
        "openclaw_create_relation",
        "generate_weekly_report",
        "generate_monthly_report",
    }
)
ALLOWED_GOLDEN_WRITE_TOOLS = frozenset({"create_entry"})


@dataclass(frozen=True, slots=True)
class GoldenGraphAudit:
    asset_key: str
    node_count: int
    edge_count: int
    node_types: tuple[str, ...]
    tool_names: tuple[str, ...]
    write_tools: tuple[str, ...]
    has_human_node: bool
    has_forbidden_edge: bool
    ok: bool
    deny_reasons: tuple[str, ...]


def freeze_capability_ledger_mode_for_run(
    *,
    runtime_kind: str = "main_agent",
    settings: Settings | None = None,
) -> str:
    """Freeze ledger mode at Main Agent Run creation.

    Plan 2 Task 9: live schema admits main_agent only. Non-main-agent
    ``runtime_kind`` raises — never returns a Legacy null mode.
    """
    if runtime_kind != "main_agent":
        raise ValueError(
            f"live schema admits main_agent only; got runtime_kind={runtime_kind!r}"
        )
    s = settings or get_settings()
    return str(s.assistant_capability_ledger_mode or "legacy_read_only")


def validate_write_release_settings(settings: Settings | None = None) -> None:
    """Re-check config gates (also enforced in Settings model_validator)."""
    s = settings or get_settings()
    # Touch properties so callers get ValueError if misconfigured after reload.
    _ = s.assistant_main_agent_write_mode
    _ = s.assistant_capability_ledger_mode
    if str(s.assistant_main_agent_write_mode) == "create_entry":
        if str(s.assistant_capability_ledger_mode) != "enforced":
            raise ValueError("create_entry write requires enforced capability ledger mode")
        secret = (s.assistant_capability_call_idempotency_secret or "").strip()
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("create_entry write requires strong idempotency secret")


def is_create_entry_write_eligible(
    *,
    capability_ledger_mode: str | None,
    write_mode: str | None = None,
    cohort_digest: str | None = None,
    settings: Settings | None = None,
) -> bool:
    """Whether this Run/cohort may use the golden create path."""
    s = settings or get_settings()
    mode = write_mode if write_mode is not None else str(s.assistant_main_agent_write_mode)
    if mode != "create_entry":
        return False
    if str(capability_ledger_mode or "") != "enforced":
        return False
    return str(cohort_digest or "") == WRITE_COHORT_DIGEST


def is_golden_write_eligible(
    *,
    capability_ledger_mode: str | None,
    write_mode: str | None = None,
    cohort_digest: str | None = None,
    settings: Settings | None = None,
) -> bool:
    """Compatibility alias; eligibility itself is no longer environment-owned."""
    return is_create_entry_write_eligible(
        capability_ledger_mode=capability_ledger_mode,
        write_mode=write_mode,
        cohort_digest=cohort_digest,
        settings=settings,
    )


def build_checked_in_golden_release(
    *,
    principal_digest: str,
    cohort_digest: str,
    owner_version_id: UUID,
    binding_contract_digest: str,
    target_digest: str,
    target_version_id: UUID | None = None,
    domain_key: str = GOLDEN_CREATE_ENTRY_DOMAIN_KEY,
) -> GoldenWriteReleaseV1:
    """Build the exact golden release record from server-side digests only."""
    return build_golden_write_release(
        principal_digest=principal_digest,
        cohort_digest=cohort_digest,
        owner_version_id=owner_version_id,
        binding_contract_digest=binding_contract_digest,
        domain_key=domain_key,
        target_digest=target_digest,
        target_version_id=target_version_id,
    )


def _iter_tool_names(nodes: Iterable[dict[str, Any]]) -> list[str]:
    tools: list[str] = []
    for node in nodes:
        cfg = node.get("config") or {}
        name = cfg.get("toolName")
        if name:
            tools.append(str(name))
    return tools


def audit_golden_workflow_graph(payload: dict[str, Any], *, asset_key: str) -> GoldenGraphAudit:
    """Assert create-only topology for the golden workflow fixture."""
    nodes = list(payload.get("nodes") or [])
    edges = list(payload.get("edges") or [])
    types = tuple(str(n.get("nodeType") or "") for n in nodes)
    tools = tuple(_iter_tool_names(nodes))
    write_tools = tuple(t for t in tools if t in FORBIDDEN_GOLDEN_TOOLS or t in ALLOWED_GOLDEN_WRITE_TOOLS)
    deny: list[str] = []
    has_human = any(t == "human_in_loop" for t in types)
    has_forbidden = any(t in FORBIDDEN_GOLDEN_NODE_TYPES for t in types)
    if has_human:
        deny.append("human_in_loop_present")
    if has_forbidden:
        deny.append("forbidden_node_type")
    forbidden_tools = [t for t in tools if t in FORBIDDEN_GOLDEN_TOOLS]
    if forbidden_tools:
        deny.append("forbidden_write_tool:" + ",".join(forbidden_tools))
    create_count = sum(1 for t in tools if t == "create_entry")
    if create_count != 1:
        deny.append(f"create_entry_count={create_count}")
    extra_writes = [t for t in tools if t != "create_entry" and t in ALLOWED_GOLDEN_WRITE_TOOLS]
    if extra_writes:
        deny.append("extra_write_tools")
    # Must be linear-ish: start + llm + create + output at minimum.
    if "start" not in types or "output" not in types:
        deny.append("missing_start_or_output")
    if "tool" not in types:
        deny.append("missing_tool_node")
    ok = not deny
    return GoldenGraphAudit(
        asset_key=asset_key,
        node_count=len(nodes),
        edge_count=len(edges),
        node_types=types,
        tool_names=tools,
        write_tools=write_tools,
        has_human_node=has_human,
        has_forbidden_edge=has_forbidden,
        ok=ok,
        deny_reasons=tuple(deny),
    )


def load_and_audit_golden_workflow(*, locale: str = "zh") -> GoldenGraphAudit:
    """Load the checked-in golden fixture and audit its graph."""
    from app.assistant.workflow.system_assets.loader import load_system_workflow_asset

    asset = get_system_asset(SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY, locale=locale)
    if asset is None:
        return GoldenGraphAudit(
            asset_key=SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
            node_count=0,
            edge_count=0,
            node_types=(),
            tool_names=(),
            write_tools=(),
            has_human_node=False,
            has_forbidden_edge=True,
            ok=False,
            deny_reasons=("asset_missing",),
        )
    # WorkflowInput is a pydantic model; dump to dict for audit.
    wf = load_system_workflow_asset(SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY, locale=locale)
    payload = wf.model_dump(mode="json", by_alias=True)
    # Some schemas nest nodes under root; support both.
    if "nodes" not in payload and hasattr(wf, "nodes"):
        payload = {
            "nodes": [n.model_dump(mode="json", by_alias=True) if hasattr(n, "model_dump") else n for n in wf.nodes],
            "edges": [e.model_dump(mode="json", by_alias=True) if hasattr(e, "model_dump") else e for e in getattr(wf, "edges", [])],
        }
    return audit_golden_workflow_graph(
        payload,
        asset_key=SMART_CAPTURE_GOLDEN_CREATE_ASSET_KEY,
    )


def gateway_allows_write(
    *,
    domain_key: str,
    write_mode: str,
    capability_ledger_mode: str,
    golden_domain_key: str = GOLDEN_CREATE_ENTRY_DOMAIN_KEY,
) -> bool:
    """Independent Gateway allowlist: only exact golden create_entry when enabled."""
    if write_mode != "create_entry" or capability_ledger_mode != "enforced":
        return False
    return domain_key == golden_domain_key


__all__ = [
    "ALLOWED_GOLDEN_WRITE_TOOLS",
    "FORBIDDEN_GOLDEN_NODE_TYPES",
    "FORBIDDEN_GOLDEN_TOOLS",
    "GoldenGraphAudit",
    "audit_golden_workflow_graph",
    "build_checked_in_golden_release",
    "freeze_capability_ledger_mode_for_run",
    "gateway_allows_write",
    "is_create_entry_write_eligible",
    "is_golden_write_eligible",
    "load_and_audit_golden_workflow",
    "validate_write_release_settings",
]
