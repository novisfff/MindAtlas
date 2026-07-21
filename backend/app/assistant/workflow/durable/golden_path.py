"""Plan 07 Task 9: hidden ``durable-proposal-review`` golden path fixtures.

Publishes one new-only evaluation Workflow + Skill package through Plan 01
services with a frozen durable execution plan extension. Does not mutate Legacy
versions, enable catalog/runtime admissions, or write business tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.skills.package_io import parse_skill_directory_files
from app.assistant.skills.schemas import (
    CreateSkillPackageCommand,
    PublishSkillVersionCommand,
    SaveSkillDraftCommand,
)
from app.assistant.skills.service import AgentSkillService
from app.assistant_config.models import AssistantWorkflow, AssistantWorkflowVersion

GOLDEN_CANONICAL_NAME = "durable-proposal-review"
GOLDEN_WORKFLOW_NAME = "durable-proposal-review"
GOLDEN_DISPLAY_NAME = "Durable Proposal Review"
GOLDEN_DESCRIPTION = (
    "Plan 07 hidden evaluation Skill: compute a generic MindAtlas note proposal, "
    "pause for durable editable approval, then emit a private Artifact + bounded text. "
    "Creates no Entry, Tag, Relation, Draft, HTTP, or external side effect."
)

# Generic MindAtlas note fields only — never inserted into business tables.
GOLDEN_PROPOSAL_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "title": "Title",
            "maxLength": 200,
        },
        "content": {
            "type": "string",
            "title": "Content",
            "maxLength": 4000,
        },
        "tags": {
            "type": "array",
            "title": "Tags",
            "items": {"type": "string", "maxLength": 64},
            "maxItems": 16,
        },
    },
    "required": ["title", "content"],
    "additionalProperties": False,
}

GOLDEN_PROPOSAL_INITIAL_VALUES: dict[str, Any] = {
    "title": "Untitled proposal",
    "content": "",
    "tags": [],
}

# Scripted compute output used by tests (no live Provider I/O).
GOLDEN_SCRIPTED_PROPOSAL_TEXT = (
    "Proposal title: Weekly reflection\n"
    "Content: Capture wins, risks, and next actions for this week.\n"
    "Tags: reflection, weekly"
)


def golden_proposal_field_schema() -> dict[str, Any]:
    """Return a deep-copy of the frozen proposal field schema."""
    import copy

    return copy.deepcopy(GOLDEN_PROPOSAL_FIELD_SCHEMA)


def golden_proposal_initial_values() -> dict[str, Any]:
    import copy

    return copy.deepcopy(GOLDEN_PROPOSAL_INITIAL_VALUES)


def golden_proposal_graph() -> dict[str, Any]:
    """start -> llm (compute proposal) -> human_in_loop (editable approval) -> output."""
    return {
        "nodes": [
            {
                "node_id": "start",
                "node_type": "start",
                "label": "Start",
                "position_x": 0,
                "position_y": 0,
                "config": {"input_mode": "text"},
            },
            {
                "node_id": "proposal_llm",
                "node_type": "llm",
                "label": "Draft proposal",
                "position_x": 180,
                "position_y": 0,
                "config": {
                    "model_source": "default",
                    "model_id": None,
                    "prompt": (
                        "Draft a generic MindAtlas note proposal from the user input. "
                        "Use only title, content, and tags fields. Do not write to any "
                        "business table or external system."
                    ),
                },
            },
            {
                "node_id": "approve",
                "node_type": "human_in_loop",
                "label": "Review proposal",
                "position_x": 360,
                "position_y": 0,
                "config": {
                    "prompt": "Review and approve the proposal",
                    "mode": "approval",
                    "kind": "approval",
                    "title": "Review proposal",
                    "field_schema": golden_proposal_field_schema(),
                    "initial_values": golden_proposal_initial_values(),
                },
            },
            {
                "node_id": "output",
                "node_type": "output",
                "label": "Emit private Artifact",
                "position_x": 540,
                "position_y": 0,
                "config": {
                    "output_mode": "text",
                    "text": "Approved proposal ready as private Artifact.",
                },
            },
        ],
        "edges": [
            {
                "edge_id": "e1",
                "source_node_id": "start",
                "target_node_id": "proposal_llm",
            },
            {
                "edge_id": "e2",
                "source_node_id": "proposal_llm",
                "target_node_id": "approve",
            },
            {
                "edge_id": "e3",
                "source_node_id": "approve",
                "target_node_id": "output",
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def build_golden_skill_md(
    *,
    name: str = GOLDEN_CANONICAL_NAME,
    description: str = GOLDEN_DESCRIPTION,
) -> bytes:
    body = (
        "# Durable Proposal Review\n\n"
        "Use this Skill only for evaluation of durable human-in-the-loop recovery.\n"
        "Compute a generic note proposal, pause for editable durable approval, then\n"
        "return a private Artifact reference and bounded user text.\n"
        "Never create or update Entry, Tag, Relation, or Draft rows.\n"
        "Never call HTTP or external systems.\n"
    )
    # Quote description: unquoted YAML treats ":" as a mapping indicator.
    safe_description = description.replace('"', '\\"')
    return (
        f"---\nname: {name}\ndescription: \"{safe_description}\"\n---\n\n{body}"
    ).encode("utf-8")


def build_golden_mindatlas_yaml(
    *,
    display_name: str = GOLDEN_DISPLAY_NAME,
    workflow_key: str = GOLDEN_WORKFLOW_NAME,
) -> bytes:
    return (
        "version: 1\n"
        f"display_name: {display_name}\n"
        "legacy_aliases: []\n"
        "\n"
        "routing:\n"
        "  include_examples:\n"
        "  - review a durable proposal for my note\n"
        "  - 请帮我审核一条笔记提案\n"
        "  - draft a private proposal and wait for my approval\n"
        "  exclude_examples:\n"
        "  - create a new entry\n"
        "  - publish a weekly report\n"
        "  - write to the knowledge graph\n"
        "  conflict_rules: []\n"
        "\n"
        "capabilities:\n"
        "  - type: workflow\n"
        f"    key: {workflow_key}\n"
        "\n"
        "policy:\n"
        "  allowed_side_effects:\n"
        "    - compute\n"
        "  max_skill_calls: 4\n"
        "  max_same_read_calls: 1\n"
        "  requires_terminal_output: true\n"
        "  terminal_text_allowed: true\n"
        "\n"
        "provider_aliases: {}\n"
        "metadata:\n"
        "  plan07_golden: \"true\"\n"
        "  golden_strategy: durable_proposal_review\n"
        "  interrupt_mode: durable\n"
        "  evaluation_only: \"true\"\n"
        "  hidden: \"true\"\n"
    ).encode("utf-8")


def parse_golden_package(
    *,
    name: str = GOLDEN_CANONICAL_NAME,
    workflow_key: str = GOLDEN_WORKFLOW_NAME,
):
    return parse_skill_directory_files(
        {
            "SKILL.md": build_golden_skill_md(name=name),
            "mindatlas.yaml": build_golden_mindatlas_yaml(
                workflow_key=workflow_key,
            ),
        },
        expected_root_name=None,
    )


@dataclass(frozen=True, slots=True)
class GoldenPublishResult:
    """Frozen digests and identities for the published golden package."""

    workflow_id: UUID
    workflow_version_id: UUID
    workflow_name: str
    target_digest: str
    plan_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    skill_package_id: UUID
    skill_version_id: UUID
    skill_content_digest: str
    skill_binding_set_digest: str
    skill_version_digest: str
    descriptor_digest: str
    behavior_side_effect: str
    behavior_interrupt_mode: str
    behavior_parallel_safe: bool
    node_configs: dict[str, dict[str, Any]]
    plan: Any
    material: Any
    frozen_binding: Any
    descriptor: Any


def _node_configs_from_graph(graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for node in graph.get("nodes") or ():
        if not isinstance(node, Mapping):
            continue
        nid = str(node.get("node_id") or "")
        if not nid:
            continue
        cfg = node.get("config") or {}
        configs[nid] = dict(cfg) if isinstance(cfg, Mapping) else {}
    return configs


def _ensure_default_model_binding(
    db: Session,
    *,
    component: str = "assistant",
    model_name: str = "gpt-test-golden",
) -> None:
    """Ensure a concrete default LLM binding exists for model dependency freeze."""
    binding = (
        db.query(AiComponentBinding)
        .filter(AiComponentBinding.component == component)
        .one_or_none()
    )
    if binding is not None and binding.llm_model_id is not None:
        return
    cred = AiCredential(
        name=f"cred-golden-{uuid4().hex[:8]}",
        base_url="https://api.example.com/v1",
        api_key_encrypted="enc-test-key-not-secret-material",
        api_key_hint="****test",
        runtime_revision=1,
    )
    db.add(cred)
    db.flush()
    model = AiModel(
        credential_id=cred.id,
        name=model_name,
        model_type="llm",
        runtime_revision=1,
    )
    db.add(model)
    db.flush()
    if binding is None:
        binding = AiComponentBinding(component=component)
        db.add(binding)
        db.flush()
    binding.llm_model_id = model.id
    db.flush()


def _create_published_workflow(
    db: Session,
    *,
    name: str,
    snapshot: dict[str, Any],
    is_system: bool = False,
) -> tuple[AssistantWorkflow, AssistantWorkflowVersion]:
    """New-publish only: insert a fresh Workflow + published version row."""
    workflow = AssistantWorkflow(
        name=name,
        description="Plan 07 durable-proposal-review golden workflow",
        enabled=True,
        is_system=is_system,
    )
    db.add(workflow)
    db.flush()
    version = AssistantWorkflowVersion(
        workflow_id=workflow.id,
        sequence_no=1,
        version_name="v1-golden",
        version_source="publish",
        snapshot=snapshot,
    )
    db.add(version)
    db.flush()
    workflow.published_version_id = version.id
    workflow.draft_version_id = version.id
    db.flush()
    return workflow, version


def publish_durable_proposal_review(
    db: Session,
    *,
    workflow_name: str = GOLDEN_WORKFLOW_NAME,
    skill_name: str = GOLDEN_CANONICAL_NAME,
    is_system: bool = False,
) -> GoldenPublishResult:
    """Create/publish the golden Workflow + Skill with durable plan extension.

    New-publish only: never mutates an existing shadow/Legacy Workflow version.
    Catalog remains disabled. Main Agent admissions flags are not flipped.
    """
    from app.assistant.capabilities.classification import (
        CapabilityClassifier,
        assemble_capability_descriptor,
    )
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.main_agent.inject_wiring import (
        freeze_skill_binding,
        reconstruct_resolved_binding,
    )
    from app.assistant.skills.models import (
        AssistantSkillCapabilityBinding,
        AssistantSkillCapabilityDependency,
        AssistantSkillPackage,
        AssistantSkillVersion,
    )
    from app.assistant.workflow.durable.planner import (
        plan_durable_execution_from_surface,
    )
    from app.assistant.workflow.durable.runner import DurableFrameMaterial

    # Ensure default model binding so workflow model deps freeze cleanly.
    _ensure_default_model_binding(db)

    graph = golden_proposal_graph()
    target_digest = sha256_canonical_json(graph)

    # Always create a NEW workflow version (do not mutate existing published rows).
    # Disambiguate name on collision so we never overwrite.
    existing_wf = (
        db.query(AssistantWorkflow)
        .filter(AssistantWorkflow.name == workflow_name)
        .one_or_none()
    )
    actual_name = (
        f"{workflow_name}-{uuid4().hex[:8]}" if existing_wf is not None else workflow_name
    )
    workflow, version = _create_published_workflow(
        db,
        name=actual_name,
        snapshot=graph,
        is_system=is_system,
    )
    db.flush()

    parsed = parse_golden_package(name=skill_name, workflow_key=workflow.name)
    svc = AgentSkillService(db)
    existing = (
        db.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.canonical_name == skill_name)
        .one_or_none()
    )
    if existing is None:
        detail = svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="golden-draft-1")
        )
        package_id = detail.id
        draft_id = detail.draft_version.id if detail.draft_version else None
    else:
        package_id = existing.id
        rev = int(getattr(existing, "aggregate_revision", 0) or 0)
        draft = svc.save_draft(
            SaveSkillDraftCommand(
                package_id=package_id,
                parsed=parsed,
                version_name="golden-draft",
                origin="api",
                expected_aggregate_revision=rev,
                request_id=f"durable-golden-draft:{package_id}:{rev}",
            )
        )
        draft_id = draft.id
    if draft_id is None:
        raise RuntimeError("golden package has no draft to publish")

    published = svc.publish(
        package_id,
        PublishSkillVersionCommand(
            draft_version_id=draft_id,
            request_id=f"durable-golden-publish:{package_id}:{draft_id}",
        ),
        durable_capability_keys=(workflow.name,),
    )
    detail = svc.get_package(package_id)
    if detail.published_version is None:
        raise RuntimeError("golden package publish did not set published_version")

    version_row = db.get(AssistantSkillVersion, published.id)
    if version_row is None:
        raise RuntimeError("published skill version missing")
    binding_rows = (
        db.query(AssistantSkillCapabilityBinding)
        .filter(AssistantSkillCapabilityBinding.skill_version_id == version_row.id)
        .order_by(AssistantSkillCapabilityBinding.ordinal.asc())
        .all()
    )
    if not binding_rows:
        raise RuntimeError("golden package published with no bindings")
    wf_binding = next(
        (b for b in binding_rows if b.capability_type == "workflow"),
        binding_rows[0],
    )
    deps = (
        db.query(AssistantSkillCapabilityDependency)
        .filter(AssistantSkillCapabilityDependency.binding_id == wf_binding.id)
        .order_by(AssistantSkillCapabilityDependency.ordinal.asc())
        .all()
    )
    resolved = reconstruct_resolved_binding(wf_binding, deps)
    frozen = freeze_skill_binding(
        resolved=resolved,
        skill_version_id=version_row.id,
        content_digest=str(version_row.content_digest or ""),
        binding_row_id=wf_binding.id,
    )
    surface = CapabilityRegistry(db).resolve_surface(frozen)
    plan = plan_durable_execution_from_surface(surface)
    behavior = CapabilityClassifier().classify_for_durable_publish(surface, plan=plan)
    descriptor = assemble_capability_descriptor(surface, behavior)

    # Registry.resolve must also emit durable when extension is present.
    target = CapabilityRegistry(db).resolve(frozen)
    if target.descriptor.behavior.interrupt_mode != "durable":
        raise RuntimeError(
            "registry.resolve did not emit interrupt_mode=durable for golden binding"
        )

    node_configs = _node_configs_from_graph(graph)
    material = DurableFrameMaterial(
        plan=plan,
        node_configs=node_configs,
        inputs={},
    )

    return GoldenPublishResult(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        workflow_name=workflow.name,
        target_digest=target_digest,
        plan_digest=str(plan.plan_digest),
        binding_contract_digest=str(resolved.binding_contract_digest),
        dependency_closure_digest=str(resolved.dependency_closure_digest),
        skill_package_id=package_id,
        skill_version_id=published.id,
        skill_content_digest=str(version_row.content_digest or ""),
        skill_binding_set_digest=str(version_row.binding_set_digest or ""),
        skill_version_digest=str(version_row.version_digest or ""),
        descriptor_digest=str(descriptor.descriptor_digest),
        behavior_side_effect=str(behavior.side_effect),
        behavior_interrupt_mode=str(behavior.interrupt_mode),
        behavior_parallel_safe=bool(behavior.parallel_safe),
        node_configs=node_configs,
        plan=plan,
        material=material,
        frozen_binding=frozen,
        descriptor=descriptor,
    )


def scripted_llm_gateway(text: str = GOLDEN_SCRIPTED_PROPOSAL_TEXT) -> Any:
    """Minimal gateway stub: returns fixed proposal text for llm.v1 adapter."""

    class _Gw:
        def invoke(self, **_kwargs: Any) -> dict[str, Any]:
            return {"text": text}

    return _Gw()


__all__ = [
    "GOLDEN_CANONICAL_NAME",
    "GOLDEN_DESCRIPTION",
    "GOLDEN_DISPLAY_NAME",
    "GOLDEN_PROPOSAL_FIELD_SCHEMA",
    "GOLDEN_PROPOSAL_INITIAL_VALUES",
    "GOLDEN_SCRIPTED_PROPOSAL_TEXT",
    "GOLDEN_WORKFLOW_NAME",
    "GoldenPublishResult",
    "build_golden_mindatlas_yaml",
    "build_golden_skill_md",
    "golden_proposal_field_schema",
    "golden_proposal_graph",
    "golden_proposal_initial_values",
    "parse_golden_package",
    "publish_durable_proposal_review",
    "scripted_llm_gateway",
]
