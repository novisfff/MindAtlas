"""Conservative recursive Capability side-effect classification (Plan 02 Task 3).

Derives behavior only from frozen Plan 01 evidence plus a versioned declarative
classification ruleset. Never defaults unclassified targets to read-only, never
executes nodes, and never queries Draft/latest mutable state to repair a missing
closure entry.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID
from urllib.parse import urlsplit

from app.assistant.capabilities.contracts import (
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    SideEffectClass,
)
from app.assistant.capabilities.ports import (
    ExecutableAgentVersionTarget,
    ExecutableToolTarget,
    ExecutableWorkflowVersionTarget,
    MainAgentControlExecutable,
    ResolvedCapabilitySurface,
)
from app.assistant.domain.contracts import (
    MAX_CAPABILITY_CLASSIFIED_NODES,
    MAX_CAPABILITY_CLOSURE_DEPTH,
    MAX_CAPABILITY_CLOSURE_REFS,
    ResolvedCapabilityDependency,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.common.ssrf import validate_url_ssrf

# ---------------------------------------------------------------------------
# Contract revision + risk lattice
# ---------------------------------------------------------------------------

CLASSIFICATION_CONTRACT_REVISION = "plan02-v1"

SIDE_EFFECT_RANK: dict[SideEffectClass, int] = {
    "none": 0,
    "compute": 1,
    "read": 2,
    "draft": 3,
    "write_local": 4,
    "write_external": 5,
    "unknown": 6,
}

INTERRUPT_RANK: dict[str, int] = {
    "none": 0,
    "legacy_blocking": 1,
    "durable": 2,
}

# Workflow parallel_safe requires an explicit adapter opt-in; Plan 02 ships false.
WORKFLOW_PARALLEL_SAFE_OPT_IN = False

# ---------------------------------------------------------------------------
# Reviewed system-Tool table (Section 6.2)
# ---------------------------------------------------------------------------

# Values: (side_effect, parallel_safe). Inspected against code-native implementations.
# Conservatism notes (Step 2):
# - search/get/list/stats: pure DB reads → read / parallel_safe=true
# - create/update/relation/report/openclaw write paths: local mutations → write_local
# - query_knowledge_graph / kb_search / kb_relation_recommendations: external KB or
#   multi-step graph state → read / parallel_safe=false
# - openclaw_query_knowledge_graph: same as query_knowledge_graph
SYSTEM_TOOL_CLASSIFICATIONS: dict[str, tuple[SideEffectClass, bool]] = {
    "search_entries": ("read", True),
    "search_similar_entries": ("read", True),
    "get_entry_detail": ("read", True),
    "create_entry": ("write_local", False),
    "update_entry": ("write_local", False),
    "create_relation": ("write_local", False),
    "query_knowledge_graph": ("read", False),
    "generate_weekly_report": ("write_local", False),
    "generate_monthly_report": ("write_local", False),
    "openclaw_capture_entry": ("write_local", False),
    "openclaw_search_entries": ("read", True),
    "openclaw_get_entry": ("read", True),
    "openclaw_create_relation": ("write_local", False),
    "openclaw_query_knowledge_graph": ("read", False),
    "get_statistics": ("read", True),
    "get_entries_by_time_range": ("read", True),
    "analyze_activity": ("read", True),
    "get_tag_statistics": ("read", True),
    "list_entry_types": ("read", True),
    "list_tags": ("read", True),
    "kb_relation_recommendations": ("read", False),
    "kb_search": ("read", False),
}

# Plan 04 Main Agent code-native controls (exhaustive; no unclassified control allowed).
# Values: (side_effect, parallel_safe). Identity prefix: main-agent-control:<domain_key>
MAIN_AGENT_CONTROL_CLASSIFICATIONS: dict[str, tuple[SideEffectClass, bool]] = {
    "skill.search": ("read", True),
    "skill.inject": ("none", False),
    "skill.read_resource": ("read", True),
    "artifact.read": ("read", True),
}

# ---------------------------------------------------------------------------
# Declarative ruleset (digest input)
# ---------------------------------------------------------------------------

_CONTROL_NODE_TYPES = ("start", "output", "if_else", "variable_assign")
_READ_LLM_NODE_TYPES = ("llm", "parameter_extractor")
_HTTP_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_HTTP_SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _system_tool_ruleset_payload() -> dict[str, Any]:
    return {
        name: {"sideEffect": side, "parallelSafe": parallel}
        for name, (side, parallel) in sorted(SYSTEM_TOOL_CLASSIFICATIONS.items())
    }


def _main_agent_control_ruleset_payload() -> dict[str, Any]:
    return {
        name: {"sideEffect": side, "parallelSafe": parallel}
        for name, (side, parallel) in sorted(MAIN_AGENT_CONTROL_CLASSIFICATIONS.items())
    }


def build_classification_ruleset() -> dict[str, Any]:
    """Canonical declarative classification ruleset (Plan 02 v1)."""
    return {
        "schemaVersion": 1,
        "revision": CLASSIFICATION_CONTRACT_REVISION,
        "riskLattice": [
            "none",
            "compute",
            "read",
            "draft",
            "write_local",
            "write_external",
            "unknown",
        ],
        "systemTools": _system_tool_ruleset_payload(),
        "mainAgentControls": _main_agent_control_ruleset_payload(),
        "remoteToolDefault": {
            "sideEffect": "write_external",
            "parallelSafe": False,
            "interruptMode": "none",
            "timeoutMode": "native",
            "cancellationSupported": False,
        },
        "workflowNodeRules": {
            "control": {"nodeTypes": list(_CONTROL_NODE_TYPES), "sideEffect": "none"},
            "llmFamily": {
                "nodeTypes": list(_READ_LLM_NODE_TYPES),
                "sideEffect": "read",
                "parallelSafe": False,
            },
            "knowledge_retrieval": {"sideEffect": "read"},
            "code_executor": {
                "sideEffect": "unknown",
                "reason": "sandbox_profile_not_in_plan01_closure",
            },
            "http_request": {
                "safeGetRequires": [
                    "literal_supported_GET",
                    "static_ssrf_valid_url",
                    "verified_tls",
                    "reviewed_retry_auth",
                ],
                "mutatingMethods": list(_HTTP_MUTATING_METHODS),
                "unsupportedOrAmbiguous": "unknown",
            },
            "tool": "resolve_frozen_tool_classification",
            "agent": "classify_frozen_tools_in_node_config",
            "workflow_call": "recurse_pinned_published_version",
            "iteration_loop": "max_body_when_statically_bounded_else_unknown",
            "human_in_loop": {
                "sideEffect": "draft",
                "interruptMode": "legacy_blocking",
                "parallelSafe": False,
            },
            "unknownNodeType": "unknown",
        },
        "agentRules": {
            "baseSideEffect": "read",
            "kbEnabledAdds": "kb_search",
            "parallelSafe": False,
            "missingOrDynamicTool": "unknown",
            "nestedAgentOrRestart": "unknown",
        },
        "interruptRules": {
            "modes": ["none", "legacy_blocking", "durable"],
            "aggregate": "strongest_reachable",
            "durableReserved": True,
        },
        "timeoutRules": {
            "systemTool": {
                "mode": "cooperative",
                "timeoutSeconds": None,
                "cancellationSupported": True,
            },
            "remoteTool": {
                "mode": "native",
                "timeoutSecondsFromFrozenConfig": True,
                "cancellationSupported": False,
            },
            "workflow": {
                "mode": "cooperative",
                "timeoutSeconds": None,
                "cancellationSupported": True,
            },
            "agent": {
                "mode": "cooperative",
                "timeoutSeconds": None,
                "cancellationSupported": True,
            },
        },
        "limits": {
            "maxClosureDepth": MAX_CAPABILITY_CLOSURE_DEPTH,
            "maxClosureRefs": MAX_CAPABILITY_CLOSURE_REFS,
            "maxClassifiedNodes": MAX_CAPABILITY_CLASSIFIED_NODES,
        },
        "workflowParallelSafeOptIn": WORKFLOW_PARALLEL_SAFE_OPT_IN,
    }


CLASSIFICATION_RULESET: dict[str, Any] = build_classification_ruleset()
CLASSIFICATION_RULESET_DIGEST = sha256_canonical_json(CLASSIFICATION_RULESET)  # type: ignore[arg-type]


def classification_contract_ref() -> ClassificationContractRef:
    return ClassificationContractRef(
        revision=CLASSIFICATION_CONTRACT_REVISION,
        ruleset_digest=CLASSIFICATION_RULESET_DIGEST,
    )


# ---------------------------------------------------------------------------
# Internal aggregation helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PartialBehavior:
    side_effect: SideEffectClass
    parallel_safe: bool
    interrupt_mode: Literal["none", "legacy_blocking", "durable"]
    timeout_policy: CapabilityTimeoutPolicy
    classified_nodes: int = 0
    dependency_refs: int = 0


def _max_side_effect(left: SideEffectClass, right: SideEffectClass) -> SideEffectClass:
    return left if SIDE_EFFECT_RANK[left] >= SIDE_EFFECT_RANK[right] else right


def _max_interrupt(
    left: Literal["none", "legacy_blocking", "durable"],
    right: Literal["none", "legacy_blocking", "durable"],
) -> Literal["none", "legacy_blocking", "durable"]:
    return left if INTERRUPT_RANK[left] >= INTERRUPT_RANK[right] else right


def _merge_parallel(left: bool, right: bool) -> bool:
    return bool(left and right)


def _merge_timeout(
    left: CapabilityTimeoutPolicy, right: CapabilityTimeoutPolicy
) -> CapabilityTimeoutPolicy:
    # Prefer native over cooperative over none; keep the stricter (lower) timeout.
    mode_rank = {"none": 0, "cooperative": 1, "native": 2}
    mode = left.mode if mode_rank[left.mode] >= mode_rank[right.mode] else right.mode
    timeouts = [t for t in (left.timeout_seconds, right.timeout_seconds) if t is not None]
    timeout_seconds = min(timeouts) if timeouts else None
    cancellation = bool(left.cancellation_supported or right.cancellation_supported)
    # If either side cannot cancel, the aggregate cannot honestly claim full support
    # when a non-cancellable native path is reachable; keep OR for cooperative paths
    # but false if any native non-cancellable remote is present.
    if left.mode == "native" and not left.cancellation_supported:
        cancellation = False if right.mode == "native" and not right.cancellation_supported else cancellation
    if (left.mode == "native" and not left.cancellation_supported) or (
        right.mode == "native" and not right.cancellation_supported
    ):
        # Native non-cancellable remote makes cancellation claim false at aggregate.
        if left.mode == "native" or right.mode == "native":
            cancellation = left.cancellation_supported and right.cancellation_supported
    return CapabilityTimeoutPolicy(
        mode=mode,  # type: ignore[arg-type]
        timeout_seconds=timeout_seconds,
        cancellation_supported=cancellation,
    )


def _unknown_partial(*, nodes: int = 1) -> _PartialBehavior:
    return _PartialBehavior(
        side_effect="unknown",
        parallel_safe=False,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none", timeout_seconds=None, cancellation_supported=False
        ),
        classified_nodes=nodes,
    )


def _none_partial() -> _PartialBehavior:
    return _PartialBehavior(
        side_effect="none",
        parallel_safe=True,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="cooperative", timeout_seconds=None, cancellation_supported=True
        ),
        classified_nodes=1,
    )


def _read_partial(*, parallel_safe: bool) -> _PartialBehavior:
    return _PartialBehavior(
        side_effect="read",
        parallel_safe=parallel_safe,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="cooperative", timeout_seconds=None, cancellation_supported=True
        ),
        classified_nodes=1,
    )


def _merge_partial(left: _PartialBehavior, right: _PartialBehavior) -> _PartialBehavior:
    return _PartialBehavior(
        side_effect=_max_side_effect(left.side_effect, right.side_effect),
        parallel_safe=_merge_parallel(left.parallel_safe, right.parallel_safe),
        interrupt_mode=_max_interrupt(left.interrupt_mode, right.interrupt_mode),
        timeout_policy=_merge_timeout(left.timeout_policy, right.timeout_policy),
        classified_nodes=left.classified_nodes + right.classified_nodes,
        dependency_refs=left.dependency_refs + right.dependency_refs,
    )


def _cfg_get(cfg: Mapping[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not isinstance(cfg, Mapping):
        return default
    for key in keys:
        if key in cfg:
            return cfg[key]
    return default


def _node_type(node: Any) -> str:
    if hasattr(node, "node_type"):
        return str(getattr(node, "node_type") or "").strip()
    if isinstance(node, Mapping):
        return str(node.get("node_type") or node.get("nodeType") or "").strip()
    return ""


def _node_id(node: Any) -> str:
    if hasattr(node, "node_id"):
        return str(getattr(node, "node_id") or "").strip()
    if isinstance(node, Mapping):
        return str(node.get("node_id") or node.get("nodeId") or "").strip()
    return ""


def _node_config(node: Any) -> dict[str, Any]:
    if hasattr(node, "config"):
        cfg = getattr(node, "config")
        return dict(cfg) if isinstance(cfg, Mapping) else {}
    if isinstance(node, Mapping):
        cfg = node.get("config")
        return dict(cfg) if isinstance(cfg, Mapping) else {}
    return {}


def _extract_nodes(workflow_input: Any) -> list[Any]:
    if workflow_input is None:
        return []
    if hasattr(workflow_input, "nodes"):
        nodes = getattr(workflow_input, "nodes")
        return list(nodes) if isinstance(nodes, Sequence) else []
    if isinstance(workflow_input, Mapping):
        nodes = workflow_input.get("nodes")
        return list(nodes) if isinstance(nodes, list) else []
    return []


def _extract_body_nodes(cfg: Mapping[str, Any]) -> list[Any]:
    raw = _cfg_get(cfg, "body_nodes", "bodyNodes", default=[])
    return list(raw) if isinstance(raw, list) else []


def _url_is_templated(url: str) -> bool:
    return "{{" in url or "{%" in url or "${" in url


def _http_request_partial(cfg: Mapping[str, Any]) -> _PartialBehavior:
    method_raw = _cfg_get(cfg, "method", default="GET")
    method = str(method_raw or "GET").strip().upper()
    url = _cfg_get(cfg, "url", default="")
    url_text = str(url or "").strip()
    verify_ssl = _cfg_get(cfg, "verify_ssl", "verifySsl", default=True)
    body_type = str(_cfg_get(cfg, "body_type", "bodyType", default="none") or "none").strip().lower()

    base_timeout = CapabilityTimeoutPolicy(
        mode="native", timeout_seconds=None, cancellation_supported=False
    )

    if method not in _HTTP_SUPPORTED_METHODS:
        return _PartialBehavior(
            side_effect="unknown",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )
    if not url_text or _url_is_templated(url_text):
        return _PartialBehavior(
            side_effect="unknown",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )
    if verify_ssl is not True:
        return _PartialBehavior(
            side_effect="unknown",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )
    if body_type == "form-data":
        # File body / form-data is ambiguous for Plan 02 v1.
        return _PartialBehavior(
            side_effect="unknown",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )

    if method in _HTTP_MUTATING_METHODS:
        return _PartialBehavior(
            side_effect="write_external",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )

    # GET: require static SSRF-valid URL.
    try:
        validate_url_ssrf(url_text)
    except Exception:
        return _PartialBehavior(
            side_effect="unknown",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )
    parts = urlsplit(url_text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return _PartialBehavior(
            side_effect="unknown",
            parallel_safe=False,
            interrupt_mode="none",
            timeout_policy=base_timeout,
            classified_nodes=1,
        )
    return _PartialBehavior(
        side_effect="read",
        parallel_safe=False,  # Workflow-level HTTP is never parallel-safe by default.
        interrupt_mode="none",
        timeout_policy=base_timeout,
        classified_nodes=1,
    )


def _system_tool_partial(tool_name: str) -> _PartialBehavior:
    entry = SYSTEM_TOOL_CLASSIFICATIONS.get(tool_name)
    if entry is None:
        return _unknown_partial()
    side, parallel = entry
    return _PartialBehavior(
        side_effect=side,
        parallel_safe=parallel,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="cooperative", timeout_seconds=None, cancellation_supported=True
        ),
        classified_nodes=1,
        dependency_refs=1,
    )


def _main_agent_control_partial(domain_key: str) -> _PartialBehavior:
    entry = MAIN_AGENT_CONTROL_CLASSIFICATIONS.get(domain_key)
    if entry is None:
        return _unknown_partial()
    side, parallel = entry
    return _PartialBehavior(
        side_effect=side,
        parallel_safe=parallel,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="cooperative", timeout_seconds=None, cancellation_supported=True
        ),
        classified_nodes=1,
        dependency_refs=0,
    )


def _remote_tool_partial(*, timeout_seconds: float | None = None) -> _PartialBehavior:
    return _PartialBehavior(
        side_effect="write_external",
        parallel_safe=False,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="native",
            timeout_seconds=timeout_seconds,
            cancellation_supported=False,
        ),
        classified_nodes=1,
        dependency_refs=1,
    )


def _tool_identity_partial(
    target_identity: str,
    *,
    timeout_seconds: float | None = None,
) -> _PartialBehavior:
    if target_identity.startswith("system-tool:"):
        return _system_tool_partial(target_identity.split(":", 1)[1])
    if target_identity.startswith("main-agent-control:"):
        return _main_agent_control_partial(target_identity.split(":", 1)[1])
    if target_identity.startswith("remote-tool:"):
        return _remote_tool_partial(timeout_seconds=timeout_seconds)
    return _unknown_partial()


def _deps_by_path(
    dependencies: Sequence[ResolvedCapabilityDependency],
) -> dict[str, ResolvedCapabilityDependency]:
    return {dep.dependency_path: dep for dep in dependencies}


def _timeout_from_remote_dep(dep: ResolvedCapabilityDependency | None) -> float | None:
    if dep is None:
        return None
    snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, Mapping) else {}
    execution = snap.get("execution")
    if not isinstance(execution, Mapping):
        return None
    raw = execution.get("timeoutSeconds")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _lookup_tool_dep(
    deps: Mapping[str, ResolvedCapabilityDependency],
    *,
    path_prefix: str,
    tool_name: str,
    node_id: str | None = None,
) -> ResolvedCapabilityDependency | None:
    """Resolve a frozen tool dep without consulting live ToolRegistry/current state.

    Plan 01 freezes:
    - Agent: ``{prefix}/tool:{name}``
    - Workflow tool/agent/KB nodes: ``{prefix}/node:{node_id}/tool:{name}``
    - Nested body: ``{prefix}/node:{container}/body/node:{node_id}/tool:{name}``
    """
    candidates: list[str] = []
    if node_id:
        candidates.append(f"{path_prefix}/node:{node_id}/tool:{tool_name}")
    candidates.append(f"{path_prefix}/tool:{tool_name}")
    if path_prefix != "root":
        candidates.append(f"root/tool:{tool_name}")
    for key in candidates:
        dep = deps.get(key)
        if dep is not None and dep.dependency_type in {"system_tool", "remote_tool"}:
            return dep

    # Exact suffix under the active path prefix (handles body nesting).
    suffix = f"/tool:{tool_name}"
    node_suffix = f"/node:{node_id}/tool:{tool_name}" if node_id else None
    for path, dep in deps.items():
        if dep.dependency_type not in {"system_tool", "remote_tool"}:
            continue
        if not path.startswith(path_prefix):
            continue
        if node_suffix and path.endswith(node_suffix):
            return dep
        if path.endswith(suffix):
            return dep
    return None


def _workflow_snapshot_from_closure(
    closure: Any, source_locator: str
) -> Any | None:
    workflows = getattr(closure, "workflows_by_locator", None)
    if isinstance(workflows, Mapping):
        entry = workflows.get(source_locator)
        if entry is not None:
            return getattr(entry, "parsed_published_input", None)
    # Optional public inspect hook.
    inspect = getattr(closure, "workflow_input_for_classification", None)
    if callable(inspect):
        return inspect(source_locator=source_locator)
    return None


def _compute_behavior_digest(
    *,
    partial: _PartialBehavior,
    binding_contract_digest: str,
    dependency_closure_digest: str,
    resolution_digest: str,
) -> str:
    return sha256_canonical_json(
        {
            "classificationRulesetDigest": CLASSIFICATION_RULESET_DIGEST,
            "revision": CLASSIFICATION_CONTRACT_REVISION,
            "bindingContractDigest": binding_contract_digest,
            "dependencyClosureDigest": dependency_closure_digest,
            "resolutionDigest": resolution_digest,
            "sideEffect": partial.side_effect,
            "parallelSafe": partial.parallel_safe,
            "interruptMode": partial.interrupt_mode,
            "timeoutPolicy": {
                "mode": partial.timeout_policy.mode,
                "timeoutSeconds": partial.timeout_policy.timeout_seconds,
                "cancellationSupported": partial.timeout_policy.cancellation_supported,
            },
        }
    )


def build_capability_behavior(
    partial: _PartialBehavior,
    *,
    binding_contract_digest: str,
    dependency_closure_digest: str,
    resolution_digest: str,
) -> CapabilityBehavior:
    # Enforce unknown ⇏ parallel_safe at construction boundary.
    parallel_safe = False if partial.side_effect == "unknown" else partial.parallel_safe
    digest = _compute_behavior_digest(
        partial=_PartialBehavior(
            side_effect=partial.side_effect,
            parallel_safe=parallel_safe,
            interrupt_mode=partial.interrupt_mode,
            timeout_policy=partial.timeout_policy,
            classified_nodes=partial.classified_nodes,
            dependency_refs=partial.dependency_refs,
        ),
        binding_contract_digest=binding_contract_digest,
        dependency_closure_digest=dependency_closure_digest,
        resolution_digest=resolution_digest,
    )
    return CapabilityBehavior(
        classification=classification_contract_ref(),
        side_effect=partial.side_effect,
        parallel_safe=parallel_safe,
        interrupt_mode=partial.interrupt_mode,
        timeout_policy=partial.timeout_policy,
        behavior_digest=digest,
    )


def compute_descriptor_digest(
    *,
    capability_key: str,
    capability_type: str,
    target_identity: str,
    target_id: UUID | None,
    target_version_id: UUID | None,
    target_revision: int | None,
    resolution_digest: str,
    binding_contract_digest: str,
    dependency_closure_digest: str,
    input_schema_digest: str,
    output_schema_digest: str,
    executable_revision: str,
    behavior_digest: str,
    availability_status: str,
    completion: Mapping[str, Any],
) -> str:
    """Digest excludes display text and mutable availability reason text."""
    return sha256_canonical_json(
        {
            "capabilityKey": capability_key,
            "capabilityType": capability_type,
            "targetIdentity": target_identity,
            "targetId": str(target_id) if target_id is not None else None,
            "targetVersionId": str(target_version_id) if target_version_id is not None else None,
            "targetRevision": target_revision,
            "resolutionDigest": resolution_digest,
            "bindingContractDigest": binding_contract_digest,
            "dependencyClosureDigest": dependency_closure_digest,
            "inputSchemaDigest": input_schema_digest,
            "outputSchemaDigest": output_schema_digest,
            "executableRevision": executable_revision,
            "behaviorDigest": behavior_digest,
            "availabilityStatus": availability_status,
            "completion": dict(completion),
            "classificationRulesetDigest": CLASSIFICATION_RULESET_DIGEST,
            "classificationRevision": CLASSIFICATION_CONTRACT_REVISION,
        }
    )


def assemble_capability_descriptor(
    surface: ResolvedCapabilitySurface,
    behavior: CapabilityBehavior,
) -> CapabilityDescriptor:
    binding = surface.binding
    resolved = binding.resolved
    availability = surface.availability
    if behavior.side_effect == "unknown" and availability.status == "available":
        availability = CapabilityAvailability(
            status="unsupported",
            reason_code="unknown_side_effect",
            compatibility_only=availability.compatibility_only,
        )

    completion_payload = resolved.completion.model_dump(mode="json", by_alias=True)
    executable_revision = str(resolved.executable_revision or resolved.resolution_digest)
    descriptor_digest = compute_descriptor_digest(
        capability_key=resolved.capability_key,
        capability_type=resolved.capability_type,
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        executable_revision=executable_revision,
        behavior_digest=behavior.behavior_digest,
        availability_status=availability.status,
        completion=completion_payload,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type=resolved.capability_type,
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=surface.display_name,
        description=surface.description,
        input_schema=copy.deepcopy(resolved.input_schema),  # type: ignore[arg-type]
        output_schema=copy.deepcopy(resolved.output_schema),  # type: ignore[arg-type]
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=descriptor_digest,
        executable_revision=executable_revision,
        behavior=behavior,
        availability=availability,
        completion=resolved.completion,
    )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class CapabilityClassifier:
    """Pure classifier over a resolved frozen surface + verified dependency closure."""

    def classify(self, surface: ResolvedCapabilitySurface) -> CapabilityBehavior:
        if not isinstance(surface, ResolvedCapabilitySurface):
            raise TypeError("surface must be a ResolvedCapabilitySurface")

        resolved = surface.binding.resolved
        executable = surface.executable
        deps = _deps_by_path(resolved.dependencies)

        if isinstance(executable, ExecutableToolTarget):
            partial = self._classify_tool_target(executable, deps)
        elif isinstance(executable, MainAgentControlExecutable):
            partial = _main_agent_control_partial(executable.capability_key)
        elif isinstance(executable, ExecutableWorkflowVersionTarget):
            partial = self._classify_workflow_root(surface, deps)
        elif isinstance(executable, ExecutableAgentVersionTarget):
            partial = self._classify_agent_root(surface, deps)
        else:
            partial = _unknown_partial()

        return build_capability_behavior(
            partial,
            binding_contract_digest=resolved.binding_contract_digest,
            dependency_closure_digest=resolved.dependency_closure_digest,
            resolution_digest=resolved.resolution_digest,
        )

    def classify_for_durable_publish(
        self,
        surface: ResolvedCapabilitySurface,
        *,
        plan: Any,
    ) -> CapabilityBehavior:
        """New-publish-only path: emit interrupt_mode=durable from a validated plan.

        Does not change the default :meth:`classify` path (Legacy human_in_loop
        remains draft + legacy_blocking). Callers must supply a frozen
        ``DurableExecutionPlanV1`` already validated by the durable planner.
        Business side effect is the plan maximum over non-control nodes;
        human_in_loop control bookkeeping does not authorize Draft.
        """
        if not isinstance(surface, ResolvedCapabilitySurface):
            raise TypeError("surface must be a ResolvedCapabilitySurface")
        if plan is None:
            raise TypeError("plan is required for durable publish classification")

        # Local import keeps Plan 02 classification importable without planner.
        from app.assistant.workflow.durable.contracts import DurableExecutionPlanV1
        from app.assistant.workflow.durable.planner import (
            business_side_effect_maximum,
            plan_allows_durable_interrupt,
            plan_parallel_safe,
        )

        if not isinstance(plan, DurableExecutionPlanV1):
            raise TypeError("plan must be a DurableExecutionPlanV1")

        resolved = surface.binding.resolved
        # Only workflow/agent targets may declare durable interrupt.
        if resolved.capability_type not in {"workflow", "agent"}:
            raise ValueError("durable publish classification requires workflow or agent")

        side = business_side_effect_maximum(plan)
        if side not in {"none", "read", "compute"}:
            raise ValueError(
                f"durable publish rejects business side effect {side!r}"
            )
        if not plan_allows_durable_interrupt(plan):
            # A durable-mode descriptor without any interrupt-capable node is
            # meaningless; fail closed rather than silently claiming durable.
            raise ValueError("durable publish requires at least one may_interrupt node")

        # Interrupt-capable durable plans are never parallel_safe
        # (plan_parallel_safe already forces False when any may_interrupt).
        parallel_safe = plan_parallel_safe(plan)

        partial = _PartialBehavior(
            side_effect=side,
            parallel_safe=parallel_safe,
            interrupt_mode="durable",
            timeout_policy=CapabilityTimeoutPolicy(
                mode="cooperative",
                timeout_seconds=None,
                cancellation_supported=True,
            ),
            classified_nodes=len(plan.nodes),
            dependency_refs=sum(len(n.dependency_refs) for n in plan.nodes),
        )
        return build_capability_behavior(
            partial,
            binding_contract_digest=resolved.binding_contract_digest,
            dependency_closure_digest=resolved.dependency_closure_digest,
            resolution_digest=resolved.resolution_digest,
        )

    def _classify_tool_target(
        self,
        executable: ExecutableToolTarget,
        deps: Mapping[str, ResolvedCapabilityDependency],
    ) -> _PartialBehavior:
        identity = executable.target_identity
        if identity.startswith("system-tool:"):
            return _system_tool_partial(identity.split(":", 1)[1])
        if identity.startswith("main-agent-control:"):
            return _main_agent_control_partial(identity.split(":", 1)[1])
        if identity.startswith("remote-tool:"):
            # Prefer frozen timeout from any matching remote dep, else executable record.
            timeout_seconds: float | None = None
            for dep in deps.values():
                if dep.target_identity == identity and dep.dependency_type == "remote_tool":
                    timeout_seconds = _timeout_from_remote_dep(dep)
                    break
            if timeout_seconds is None:
                record = executable.tool_object_or_record
                raw = getattr(record, "timeout_seconds", None)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    timeout_seconds = float(raw)
            return _remote_tool_partial(timeout_seconds=timeout_seconds)
        return _unknown_partial()

    def _classify_workflow_root(
        self,
        surface: ResolvedCapabilitySurface,
        deps: Mapping[str, ResolvedCapabilityDependency],
    ) -> _PartialBehavior:
        executable = surface.executable
        assert isinstance(executable, ExecutableWorkflowVersionTarget)
        key = (executable.workflow_id, executable.version_id)
        partial = self._classify_workflow_graph(
            workflow_input=executable.parsed_published_input,
            path_prefix="root",
            deps=deps,
            closure=surface.execution_closure,
            visited={key},
            depth=0,
        )
        # Workflow-level parallel_safe requires adapter opt-in (default false).
        if not WORKFLOW_PARALLEL_SAFE_OPT_IN:
            partial = _PartialBehavior(
                side_effect=partial.side_effect,
                parallel_safe=False,
                interrupt_mode=partial.interrupt_mode,
                timeout_policy=CapabilityTimeoutPolicy(
                    mode="cooperative"
                    if partial.timeout_policy.mode == "none"
                    else partial.timeout_policy.mode,  # type: ignore[arg-type]
                    timeout_seconds=partial.timeout_policy.timeout_seconds,
                    cancellation_supported=True
                    if partial.timeout_policy.mode != "native"
                    else partial.timeout_policy.cancellation_supported,
                ),
                classified_nodes=partial.classified_nodes,
                dependency_refs=partial.dependency_refs,
            )
        # Ensure workflow timeout policy is at least cooperative.
        if partial.timeout_policy.mode == "none" and partial.side_effect != "unknown":
            partial = _PartialBehavior(
                side_effect=partial.side_effect,
                parallel_safe=partial.parallel_safe,
                interrupt_mode=partial.interrupt_mode,
                timeout_policy=CapabilityTimeoutPolicy(
                    mode="cooperative",
                    timeout_seconds=None,
                    cancellation_supported=True,
                ),
                classified_nodes=partial.classified_nodes,
                dependency_refs=partial.dependency_refs,
            )
        return partial

    def _classify_agent_root(
        self,
        surface: ResolvedCapabilitySurface,
        deps: Mapping[str, ResolvedCapabilityDependency],
    ) -> _PartialBehavior:
        executable = surface.executable
        assert isinstance(executable, ExecutableAgentVersionTarget)
        snapshot = executable.parsed_snapshot
        if not isinstance(snapshot, Mapping):
            return _unknown_partial()

        # Nested/restart semantics are not enabled.
        if snapshot.get("nested_agent") or snapshot.get("restart") or snapshot.get("main_agent_restart"):
            return _unknown_partial()

        tools_raw = snapshot.get("tools") or []
        if not isinstance(tools_raw, list):
            return _unknown_partial()

        acc = _read_partial(parallel_safe=False)
        # Agent always non-parallel.
        acc = _PartialBehavior(
            side_effect=acc.side_effect,
            parallel_safe=False,
            interrupt_mode=acc.interrupt_mode,
            timeout_policy=CapabilityTimeoutPolicy(
                mode="cooperative", timeout_seconds=None, cancellation_supported=True
            ),
            classified_nodes=1,
            dependency_refs=0,
        )

        for tool_name_raw in tools_raw:
            if not isinstance(tool_name_raw, str) or not tool_name_raw.strip():
                acc = _merge_partial(acc, _unknown_partial())
                continue
            tool_name = tool_name_raw.strip()
            dep = _lookup_tool_dep(deps, path_prefix="root", tool_name=tool_name)
            if dep is None:
                # Missing from frozen closure → unknown (do not repair via live registry).
                acc = _merge_partial(acc, _unknown_partial())
                continue
            timeout = _timeout_from_remote_dep(dep)
            acc = _merge_partial(
                acc,
                _tool_identity_partial(dep.target_identity, timeout_seconds=timeout),
            )

        kb_config = snapshot.get("kb_config")
        kb_enabled = False
        if isinstance(kb_config, Mapping):
            kb_enabled = bool(kb_config.get("enabled"))
        if kb_enabled:
            acc = _merge_partial(acc, _system_tool_partial("kb_search"))

        return acc

    def _classify_workflow_graph(
        self,
        *,
        workflow_input: Any,
        path_prefix: str,
        deps: Mapping[str, ResolvedCapabilityDependency],
        closure: Any,
        visited: set[tuple[UUID, UUID]],
        depth: int,
    ) -> _PartialBehavior:
        if depth > MAX_CAPABILITY_CLOSURE_DEPTH:
            return _unknown_partial()

        nodes = _extract_nodes(workflow_input)
        if not nodes:
            # Empty/missing graph cannot be proven safe.
            return _unknown_partial()

        acc = _PartialBehavior(
            side_effect="none",
            parallel_safe=True,
            interrupt_mode="none",
            timeout_policy=CapabilityTimeoutPolicy(
                mode="cooperative", timeout_seconds=None, cancellation_supported=True
            ),
            classified_nodes=0,
            dependency_refs=0,
        )

        for node in nodes:
            node_partial = self._classify_node(
                node,
                path_prefix=path_prefix,
                deps=deps,
                closure=closure,
                visited=visited,
                depth=depth,
                classified_so_far=acc.classified_nodes,
            )
            acc = _merge_partial(acc, node_partial)
            if acc.classified_nodes > MAX_CAPABILITY_CLASSIFIED_NODES:
                return _unknown_partial(nodes=acc.classified_nodes)
            if acc.dependency_refs > MAX_CAPABILITY_CLOSURE_REFS:
                return _unknown_partial(nodes=acc.classified_nodes)

        # Presence of agent/loop/human already folded into parallel_safe via handlers.
        # LLM family forces non-parallel at workflow level.
        return acc

    def _classify_node(
        self,
        node: Any,
        *,
        path_prefix: str,
        deps: Mapping[str, ResolvedCapabilityDependency],
        closure: Any,
        visited: set[tuple[UUID, UUID]],
        depth: int,
        classified_so_far: int,
    ) -> _PartialBehavior:
        if classified_so_far + 1 > MAX_CAPABILITY_CLASSIFIED_NODES:
            return _unknown_partial()

        ntype = _node_type(node)
        cfg = _node_config(node)
        nid = _node_id(node)

        if ntype in _CONTROL_NODE_TYPES:
            return _none_partial()

        if ntype in _READ_LLM_NODE_TYPES:
            # read; never parallel-safe at Workflow level
            return _read_partial(parallel_safe=False)

        if ntype == "knowledge_retrieval":
            return _read_partial(parallel_safe=True)

        if ntype == "code_executor":
            return _unknown_partial()

        if ntype == "http_request":
            return _http_request_partial(cfg)

        if ntype == "human_in_loop":
            return _PartialBehavior(
                side_effect="draft",
                parallel_safe=False,
                interrupt_mode="legacy_blocking",
                timeout_policy=CapabilityTimeoutPolicy(
                    mode="cooperative",
                    timeout_seconds=None,
                    cancellation_supported=True,
                ),
                classified_nodes=1,
            )

        if ntype == "tool":
            tool_name = str(
                _cfg_get(cfg, "tool_name", "toolName", default="") or ""
            ).strip()
            if not tool_name:
                return _unknown_partial()
            dep = _lookup_tool_dep(
                deps, path_prefix=path_prefix, tool_name=tool_name, node_id=nid or None
            )
            if dep is None:
                return _unknown_partial()
            return _tool_identity_partial(
                dep.target_identity, timeout_seconds=_timeout_from_remote_dep(dep)
            )

        if ntype == "agent":
            return self._classify_workflow_agent_node(
                cfg, path_prefix=path_prefix, deps=deps, node_id=nid
            )

        if ntype == "workflow_call":
            return self._classify_workflow_call(
                cfg,
                node_id=nid,
                path_prefix=path_prefix,
                deps=deps,
                closure=closure,
                visited=visited,
                depth=depth,
            )

        if ntype in {"iteration", "loop"}:
            return self._classify_container(
                ntype,
                cfg,
                path_prefix=path_prefix,
                deps=deps,
                closure=closure,
                visited=visited,
                depth=depth,
                classified_so_far=classified_so_far,
            )

        # Unknown node type
        return _unknown_partial()

    def _classify_workflow_agent_node(
        self,
        cfg: Mapping[str, Any],
        *,
        path_prefix: str,
        deps: Mapping[str, ResolvedCapabilityDependency],
        node_id: str = "",
    ) -> _PartialBehavior:
        # Nested agent/restart not enabled for shared mode.
        if _cfg_get(cfg, "nested_agent", "nestedAgent", default=False):
            return _unknown_partial()
        if _cfg_get(cfg, "restart", default=False):
            return _unknown_partial()

        acc = _read_partial(parallel_safe=False)
        tool_names_raw = _cfg_get(cfg, "tool_names", "toolNames", default=[])
        if tool_names_raw is None:
            tool_names_raw = []
        if not isinstance(tool_names_raw, list):
            return _unknown_partial()
        for item in tool_names_raw:
            if not isinstance(item, str) or not item.strip():
                acc = _merge_partial(acc, _unknown_partial())
                continue
            tool_name = item.strip()
            dep = _lookup_tool_dep(
                deps,
                path_prefix=path_prefix,
                tool_name=tool_name,
                node_id=node_id or None,
            )
            if dep is None:
                acc = _merge_partial(acc, _unknown_partial())
                continue
            acc = _merge_partial(
                acc,
                _tool_identity_partial(
                    dep.target_identity, timeout_seconds=_timeout_from_remote_dep(dep)
                ),
            )
        knowledge_enabled = bool(
            _cfg_get(cfg, "knowledge_enabled", "knowledgeEnabled", default=False)
        )
        if knowledge_enabled:
            acc = _merge_partial(acc, _system_tool_partial("kb_search"))
        # Agent nodes are never parallel-safe.
        return _PartialBehavior(
            side_effect=acc.side_effect,
            parallel_safe=False,
            interrupt_mode=acc.interrupt_mode,
            timeout_policy=acc.timeout_policy,
            classified_nodes=acc.classified_nodes,
            dependency_refs=acc.dependency_refs,
        )

    def _classify_workflow_call(
        self,
        cfg: Mapping[str, Any],
        *,
        node_id: str,
        path_prefix: str,
        deps: Mapping[str, ResolvedCapabilityDependency],
        closure: Any,
        visited: set[tuple[UUID, UUID]],
        depth: int,
    ) -> _PartialBehavior:
        binding_mode = str(
            _cfg_get(cfg, "binding_mode", "bindingMode", default="pinned") or "pinned"
        ).strip().lower()
        if binding_mode != "pinned":
            return _unknown_partial()

        version_raw = _cfg_get(
            cfg, "target_published_version_id", "targetPublishedVersionId", default=None
        )
        workflow_raw = _cfg_get(cfg, "target_workflow_id", "targetWorkflowId", default=None)
        if version_raw is None or workflow_raw is None:
            return _unknown_partial()

        try:
            version_id = UUID(str(version_raw))
            workflow_id = UUID(str(workflow_raw))
        except (TypeError, ValueError):
            return _unknown_partial()

        call_path = f"{path_prefix}/workflow_call:{node_id}"
        dep = deps.get(call_path)
        if dep is None:
            # Also accept any workflow dep matching version id under this prefix.
            for path, candidate in deps.items():
                if (
                    candidate.dependency_type == "workflow"
                    and candidate.resolved_workflow_version_id == version_id
                    and path.startswith(f"{path_prefix}/workflow_call:")
                ):
                    dep = candidate
                    call_path = path
                    break
        if dep is None or dep.dependency_type != "workflow":
            return _unknown_partial()
        if dep.resolved_workflow_version_id != version_id:
            return _unknown_partial()

        key = (workflow_id, version_id)
        if key in visited:
            return _unknown_partial()  # cycle
        if depth + 1 > MAX_CAPABILITY_CLOSURE_DEPTH:
            return _unknown_partial()

        nested_input = _workflow_snapshot_from_closure(closure, call_path)
        if nested_input is None:
            # Never repair by querying current state; missing frozen nested graph → unknown.
            return _unknown_partial()

        nested = self._classify_workflow_graph(
            workflow_input=nested_input,
            path_prefix=call_path,
            deps=deps,
            closure=closure,
            visited=visited | {key},
            depth=depth + 1,
        )
        # Count the workflow_call node itself plus nested.
        return _PartialBehavior(
            side_effect=nested.side_effect,
            parallel_safe=nested.parallel_safe,
            interrupt_mode=nested.interrupt_mode,
            timeout_policy=nested.timeout_policy,
            classified_nodes=nested.classified_nodes + 1,
            dependency_refs=nested.dependency_refs + 1,
        )

    def _classify_container(
        self,
        ntype: str,
        cfg: Mapping[str, Any],
        *,
        path_prefix: str,
        deps: Mapping[str, ResolvedCapabilityDependency],
        closure: Any,
        visited: set[tuple[UUID, UUID]],
        depth: int,
        classified_so_far: int,
    ) -> _PartialBehavior:
        max_raw = _cfg_get(cfg, "max_iterations", "maxIterations", default=None)
        try:
            max_iterations = int(max_raw) if max_raw is not None else None
        except (TypeError, ValueError):
            max_iterations = None

        body_nodes = _extract_body_nodes(cfg)
        # Statically bounded only when max iterations is a positive finite int and body is present.
        if max_iterations is None or max_iterations < 1 or not body_nodes:
            return _unknown_partial()

        acc = _PartialBehavior(
            side_effect="none",
            parallel_safe=False,  # loop/iteration never parallel-safe
            interrupt_mode="none",
            timeout_policy=CapabilityTimeoutPolicy(
                mode="cooperative", timeout_seconds=None, cancellation_supported=True
            ),
            classified_nodes=1,  # container node
            dependency_refs=0,
        )
        # Body as a synthetic mini-graph (nodes only).
        body_input = {"nodes": body_nodes, "edges": []}
        body_partial = self._classify_workflow_graph(
            workflow_input=body_input,
            path_prefix=path_prefix,
            deps=deps,
            closure=closure,
            visited=visited,
            depth=depth,
        )
        merged = _merge_partial(acc, body_partial)
        # Containers are never parallel-safe.
        return _PartialBehavior(
            side_effect=merged.side_effect,
            parallel_safe=False,
            interrupt_mode=merged.interrupt_mode,
            timeout_policy=merged.timeout_policy,
            classified_nodes=merged.classified_nodes,
            dependency_refs=merged.dependency_refs,
        )


__all__ = [
    "CLASSIFICATION_CONTRACT_REVISION",
    "CLASSIFICATION_RULESET",
    "CLASSIFICATION_RULESET_DIGEST",
    "MAIN_AGENT_CONTROL_CLASSIFICATIONS",
    "SIDE_EFFECT_RANK",
    "SYSTEM_TOOL_CLASSIFICATIONS",
    "WORKFLOW_PARALLEL_SAFE_OPT_IN",
    "CapabilityClassifier",
    "assemble_capability_descriptor",
    "build_capability_behavior",
    "build_classification_ruleset",
    "classification_contract_ref",
    "compute_descriptor_digest",
]
