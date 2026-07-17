"""Reconstruct exact durable Workflow materials from immutable published rows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.assistant.capabilities.registry import CapabilityRegistry
from app.assistant.main_agent.inject_wiring import (
    freeze_skill_binding,
    reconstruct_resolved_binding,
)
from app.assistant.skills.models import (
    AssistantSkillCapabilityBinding,
    AssistantSkillCapabilityDependency,
    AssistantSkillVersion,
)
from app.assistant.workflow.durable.contracts import DurableWorkflowStateV1
from app.assistant.workflow.durable.planner import plan_durable_execution_from_surface
from app.assistant.workflow.durable.runner import DurableFrameMaterial


class DurableMaterialResolutionError(ValueError):
    """Frozen material cannot be reconstructed without ambient-state fallback."""

    reason_code = "durable_material_needs_reconciliation"


def _node_configs(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_nodes = snapshot.get("nodes")
    if raw_nodes is None and isinstance(snapshot.get("graph"), Mapping):
        raw_nodes = snapshot["graph"].get("nodes")
    result: dict[str, dict[str, Any]] = {}
    for node in raw_nodes or ():
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or node.get("nodeId") or "")
        if not node_id:
            continue
        config = node.get("config")
        result[node_id] = dict(config) if isinstance(config, Mapping) else {}
    return result


class DurableRuntimeMaterialResolver:
    """Resolve frame materials using only IDs and digests frozen in Checkpoint v2."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve(
        self,
        *,
        workflow_state: DurableWorkflowStateV1,
    ) -> tuple[DurableFrameMaterial, dict[str, DurableFrameMaterial]]:
        if not workflow_state.frame_stack:
            raise DurableMaterialResolutionError("workflow state has no frames")

        materials: dict[str, DurableFrameMaterial] = {}
        for frame in workflow_state.frame_stack:
            material = self._resolve_frame(frame)
            materials[str(frame.target_version_id)] = material

        root_frame = next(
            (
                frame
                for frame in workflow_state.frame_stack
                if frame.frame_id == workflow_state.root_frame_id
            ),
            None,
        )
        if root_frame is None:
            raise DurableMaterialResolutionError("root frame is missing")
        return materials[str(root_frame.target_version_id)], materials

    def _resolve_frame(self, frame: Any) -> DurableFrameMaterial:
        owner_version_id = frame.owner_skill_version_id
        if owner_version_id is None:
            raise DurableMaterialResolutionError(
                "frame is missing frozen owner Skill version"
            )
        version = self.db.get(AssistantSkillVersion, owner_version_id)
        if version is None or str(version.version_source) != "publish":
            raise DurableMaterialResolutionError(
                "frozen owner Skill version is missing or not published"
            )

        bindings = (
            self.db.query(AssistantSkillCapabilityBinding)
            .filter(
                AssistantSkillCapabilityBinding.skill_version_id == owner_version_id,
                AssistantSkillCapabilityBinding.resolution_status == "resolved",
            )
            .order_by(AssistantSkillCapabilityBinding.ordinal.asc())
            .all()
        )
        matching = [
            binding
            for binding in bindings
            if (
                binding.resolved_workflow_version_id == frame.target_version_id
                or binding.resolved_agent_version_id == frame.target_version_id
            )
        ]
        if len(matching) == 1:
            binding = matching[0]
            dependencies = (
                self.db.query(AssistantSkillCapabilityDependency)
                .filter(AssistantSkillCapabilityDependency.binding_id == binding.id)
                .order_by(AssistantSkillCapabilityDependency.ordinal.asc())
                .all()
            )
        else:
            # Nested targets live in the frozen dependency closure of a parent
            # binding. Rehydrate the exact pinned dependency row only.
            candidates = []
            for parent in bindings:
                rows = self.db.query(AssistantSkillCapabilityDependency).filter(
                    AssistantSkillCapabilityDependency.binding_id == parent.id
                ).all()
                for dep in rows:
                    target = dep.resolved_workflow_version_id or dep.resolved_agent_version_id
                    if target == frame.target_version_id:
                        candidates.append((parent, dep, rows))
            if len(candidates) != 1:
                raise DurableMaterialResolutionError(
                    "exact frozen target binding is missing or ambiguous"
                )
            parent, dep, dependencies = candidates[0]
            snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, Mapping) else {}
            binding = SimpleNamespace(
                id=parent.id,
                capability_type=dep.dependency_type,
                capability_key=str(snap.get("capabilityKey") or dep.target_identity),
                target_identity=str(dep.target_identity),
                target_id=None,
                resolved_tool_id=dep.resolved_tool_id,
                resolved_workflow_version_id=dep.resolved_workflow_version_id,
                resolved_agent_version_id=dep.resolved_agent_version_id,
                resolved_revision=dep.target_revision,
                input_schema_digest=dep.input_schema_digest,
                output_schema_digest=dep.output_schema_digest,
                config_digest=snap.get("configDigest"),
                executable_revision=str(frame.target_version_id),
                resolution_digest=dep.resolution_digest,
                dependency_closure_digest=dep.dependency_digest,
                binding_contract_digest=parent.binding_contract_digest,
                resolution_snapshot=snap,
            )
        resolved = reconstruct_resolved_binding(binding, dependencies)
        frozen = freeze_skill_binding(
            resolved=resolved,
            skill_version_id=version.id,
            content_digest=str(version.content_digest or ""),
            binding_row_id=binding.id,
        )
        surface = CapabilityRegistry(self.db).resolve_surface(frozen)
        plan = plan_durable_execution_from_surface(surface)
        if plan.target_version_id != frame.target_version_id:
            raise DurableMaterialResolutionError("target version mismatch")
        if str(plan.target_digest) != str(frame.target_digest):
            raise DurableMaterialResolutionError("target digest mismatch")
        if str(plan.plan_digest) != str(frame.execution_plan_digest):
            raise DurableMaterialResolutionError("plan digest mismatch")

        executable = surface.executable
        snapshot = getattr(executable, "parsed_published_input", None)
        if snapshot is not None and hasattr(snapshot, "model_dump"):
            snapshot = snapshot.model_dump(mode="python")
        if snapshot is None:
            snapshot = getattr(executable, "parsed_snapshot", None)
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        return DurableFrameMaterial(
            plan=plan,
            node_configs=_node_configs(snapshot),
            inputs={},
        )


__all__ = [
    "DurableMaterialResolutionError",
    "DurableRuntimeMaterialResolver",
]
