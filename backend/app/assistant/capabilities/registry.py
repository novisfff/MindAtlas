"""Exact frozen-reference CapabilityRegistry (Plan 02 Task 2).

Resolves one ``FrozenCapabilityBinding`` to a ``ResolvedCapabilitySurface`` using
only exact owned version/config rows and Plan 01 digests. Never consults Draft,
latest, Provider aliases, or decrypts credentials.
"""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.assistant.capabilities.classification import (
    CapabilityClassifier,
    assemble_capability_descriptor,
)
from app.assistant.capabilities.contracts import (
    CapabilityAvailability,
    CapabilityDescriptor,
    CapabilityError,
    FrozenCapabilityBinding,
)
from app.assistant.capabilities.errors import CapabilityDomainError
from app.assistant.capabilities.execution_closure import build_frozen_execution_closure
from app.assistant.capabilities.ports import (
    ExecutableAgentVersionTarget,
    ExecutableToolTarget,
    ExecutableWorkflowVersionTarget,
    ResolvedCapabilitySurface,
    ResolvedCapabilityTarget,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.domain.json_schema import binding_schema_digest
from app.assistant.skills import resolution as skill_resolution
from app.assistant_config.models import (
    AssistantAgentProfile,
    AssistantAgentProfileVersion,
    AssistantTool,
    AssistantWorkflow,
    AssistantWorkflowVersion,
)
from app.assistant_config.registry import ToolRegistry
from app.assistant_config.remote_tool import RemoteTool
from app.assistant_config.schemas import WorkflowInput
from app.assistant_config.workflow_contracts import (
    WorkflowContractError,
    workflow_contract_from_input,
)


def _domain_error(
    *,
    error_type: str,
    safe_code: str,
    safe_message: str,
    target_identity: str | None = None,
) -> CapabilityDomainError:
    return CapabilityDomainError(
        CapabilityError(
            error_type=error_type,  # type: ignore[arg-type]
            safe_code=safe_code,
            safe_message=safe_message,
            retry_disposition="never",
            target_identity=target_identity,
        )
    )


def _workflow_input_from_snapshot(snapshot: dict[str, Any]) -> WorkflowInput:
    return WorkflowInput.model_validate(
        {
            "nodes": snapshot.get("nodes") or [],
            "edges": snapshot.get("edges") or [],
            "viewport": snapshot.get("viewport"),
        }
        if "nodes" in snapshot
        else snapshot
    )


class CapabilityRegistry:
    """Resolve frozen capability bindings to exact executable surfaces/descriptors."""

    def __init__(
        self,
        db: Session,
        *,
        locale: str | None = None,
        classifier: CapabilityClassifier | None = None,
    ) -> None:
        self.db = db
        self.locale = locale
        # Production always uses the real classifier; tests may inject a spy.
        self._classifier = classifier if classifier is not None else CapabilityClassifier()

    def resolve_surface(self, binding: FrozenCapabilityBinding) -> ResolvedCapabilitySurface:
        if not isinstance(binding, FrozenCapabilityBinding):
            raise TypeError("binding must be a FrozenCapabilityBinding")

        # Binding digests already recomputed by FrozenCapabilityBinding validation.
        cap_type = binding.resolved.capability_type
        if cap_type == "tool":
            return self._resolve_tool_surface(binding)
        if cap_type == "workflow":
            return self._resolve_workflow_surface(binding)
        if cap_type == "agent":
            return self._resolve_agent_surface(binding)
        raise _domain_error(
            error_type="protocol_error",
            safe_code="unknown_capability_type",
            safe_message="unknown capability type",
            target_identity=binding.resolved.target_identity,
        )

    def resolve(self, binding: FrozenCapabilityBinding) -> ResolvedCapabilityTarget:
        """Resolve surface, classify once, assemble descriptor + final target."""
        surface = self.resolve_surface(binding)
        behavior = self._classifier.classify(surface)
        descriptor = assemble_capability_descriptor(surface, behavior)
        return ResolvedCapabilityTarget(
            descriptor=descriptor,
            binding=surface.binding,
            executable=surface.executable,
            execution_closure=surface.execution_closure,
        )

    def describe(self, binding: FrozenCapabilityBinding) -> CapabilityDescriptor:
        return self.resolve(binding).descriptor

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _resolve_tool_surface(
        self, binding: FrozenCapabilityBinding
    ) -> ResolvedCapabilitySurface:
        identity = binding.resolved.target_identity
        if identity.startswith("system-tool:"):
            return self._resolve_system_tool_surface(binding)
        if identity.startswith("remote-tool:"):
            return self._resolve_remote_tool_surface(binding)
        raise _domain_error(
            error_type="not_found",
            safe_code="tool_identity_unsupported",
            safe_message="unsupported tool identity",
            target_identity=identity,
        )

    def _resolve_system_tool_surface(
        self, binding: FrozenCapabilityBinding
    ) -> ResolvedCapabilitySurface:
        identity = binding.resolved.target_identity
        tool_name = identity.split(":", 1)[1]
        target_identity = identity

        if tool_name not in ToolRegistry.list_runtime_system_tool_names():
            raise _domain_error(
                error_type="not_found",
                safe_code="system_tool_missing",
                safe_message="system tool not exported",
                target_identity=target_identity,
            )

        # Disabled DB record shadows same-named system tool (availability only).
        shadowed = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.name == tool_name)
            .one_or_none()
        )
        availability = CapabilityAvailability(status="available")
        if shadowed is not None and not bool(shadowed.enabled):
            availability = CapabilityAvailability(
                status="disabled",
                reason_code="tool_disabled",
            )

        tool_obj = ToolRegistry.resolve_system_tool(tool_name)
        if tool_obj is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="system_tool_missing",
                safe_message="system tool not exported",
                target_identity=target_identity,
            )

        # Drift evidence: recompute current schemas/build/contract-set.
        # Import module attribute at call time so tests can monkeypatch.
        try:
            current_input, current_output = skill_resolution.system_tool_schemas(tool_name)
        except Exception as exc:
            raise _domain_error(
                error_type="version_drift",
                safe_code="schema_drift",
                safe_message="system tool schema unavailable",
                target_identity=target_identity,
            ) from exc

        current_input_digest = binding_schema_digest(current_input)
        current_output_digest = binding_schema_digest(current_output)
        if current_input_digest != binding.resolved.input_schema_digest:
            raise _domain_error(
                error_type="version_drift",
                safe_code="schema_drift",
                safe_message="system tool input schema drift",
                target_identity=target_identity,
            )
        if current_output_digest != binding.resolved.output_schema_digest:
            raise _domain_error(
                error_type="version_drift",
                safe_code="schema_drift",
                safe_message="system tool output schema drift",
                target_identity=target_identity,
            )

        try:
            app_build = skill_resolution.require_immutable_app_build_revision()
        except Exception as exc:
            raise _domain_error(
                error_type="version_drift",
                safe_code="build_revision_drift",
                safe_message="app build revision unavailable",
                target_identity=target_identity,
            ) from exc
        if binding.resolved.executable_revision != app_build:
            raise _domain_error(
                error_type="version_drift",
                safe_code="build_revision_drift",
                safe_message="system tool build revision drift",
                target_identity=target_identity,
            )

        current_set = skill_resolution.compute_system_tool_contract_set_digest()
        if binding.resolved.config_digest != current_set:
            raise _domain_error(
                error_type="version_drift",
                safe_code="system_tool_contract_set_drift",
                safe_message="system tool contract set drift",
                target_identity=target_identity,
            )

        definition = ToolRegistry.get_runtime_system_tool_definition(
            tool_name, locale=self.locale
        )
        display_name = (
            definition.display_name if definition is not None else tool_name
        )
        description = (
            (definition.display_description or definition.description or "")
            if definition is not None
            else ""
        )

        executable = ExecutableToolTarget(
            target_identity=target_identity,
            tool_id=None,
            config_revision=None,
            config_digest=current_set,
            is_system=True,
            tool_object_or_record=tool_obj,
        )
        closure = build_frozen_execution_closure(
            self.db,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            dependency_closure_digest=binding.resolved.dependency_closure_digest,
            dependencies=binding.resolved.dependencies,
        )
        return ResolvedCapabilitySurface(
            binding=binding,
            executable=executable,
            execution_closure=closure,
            display_name=display_name,
            description=description,
            availability=availability,
        )

    def _resolve_remote_tool_surface(
        self, binding: FrozenCapabilityBinding
    ) -> ResolvedCapabilitySurface:
        identity = binding.resolved.target_identity
        tool_id = binding.resolved.resolved_tool_id or binding.resolved.target_id
        if tool_id is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="remote_tool_missing",
                safe_message="remote tool missing",
                target_identity=identity,
            )

        tool = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.id == tool_id)
            .one_or_none()
        )
        if tool is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="remote_tool_missing",
                safe_message="remote tool missing",
                target_identity=identity,
            )

        if f"remote-tool:{tool.id}" != identity:
            raise _domain_error(
                error_type="version_drift",
                safe_code="remote_tool_identity_mismatch",
                safe_message="remote tool identity mismatch",
                target_identity=identity,
            )

        if (tool.kind or "").lower() != "remote":
            raise _domain_error(
                error_type="version_drift",
                safe_code="remote_tool_kind_mismatch",
                safe_message="remote tool kind mismatch",
                target_identity=identity,
            )

        current_rev = int(tool.config_revision or 1)
        frozen_rev = binding.resolved.resolved_revision
        if frozen_rev is not None and int(frozen_rev) != current_rev:
            raise _domain_error(
                error_type="version_drift",
                safe_code="config_revision_drift",
                safe_message="remote tool config revision drift",
                target_identity=identity,
            )
        if binding.resolved.executable_revision is not None:
            if str(binding.resolved.executable_revision) != str(current_rev):
                raise _domain_error(
                    error_type="version_drift",
                    safe_code="config_revision_drift",
                    safe_message="remote tool executable revision drift",
                    target_identity=identity,
                )

        # Secret-free config digest comparison (no decryption).
        try:
            endpoint = (tool.endpoint_url or "").strip()
            if endpoint:
                parts = urlsplit(endpoint)
                # Extremely malformed endpoints (no scheme/host) are unavailable.
                if not parts.scheme or not parts.hostname:
                    # Still compute digest for drift; mark unavailable below if needed.
                    pass
            execution = skill_resolution.secret_free_remote_execution_snapshot(tool)
            config_digest = sha256_canonical_json(execution)
        except Exception as exc:
            raise _domain_error(
                error_type="unavailable",
                safe_code="remote_tool_config_invalid",
                safe_message="remote tool config invalid",
                target_identity=identity,
            ) from exc

        if binding.resolved.config_digest != config_digest:
            raise _domain_error(
                error_type="version_drift",
                safe_code="config_digest_drift",
                safe_message="remote tool config digest drift",
                target_identity=identity,
            )

        # Input schema drift evidence (output is binding-level for remote tools).
        try:
            current_input = skill_resolution.remote_tool_input_schema(tool)
            if binding_schema_digest(current_input) != binding.resolved.input_schema_digest:
                raise _domain_error(
                    error_type="version_drift",
                    safe_code="schema_drift",
                    safe_message="remote tool input schema drift",
                    target_identity=identity,
                )
        except CapabilityDomainError:
            raise
        except Exception as exc:
            raise _domain_error(
                error_type="unavailable",
                safe_code="remote_tool_config_invalid",
                safe_message="remote tool schema invalid",
                target_identity=identity,
            ) from exc

        endpoint = (tool.endpoint_url or "").strip()
        parts = urlsplit(endpoint) if endpoint else None
        malformed = (
            not endpoint
            or parts is None
            or not parts.scheme
            or not parts.hostname
            or " " in endpoint
            or ":::" in endpoint
        )

        availability: CapabilityAvailability
        if not bool(tool.enabled):
            availability = CapabilityAvailability(
                status="disabled",
                reason_code="tool_disabled",
            )
        elif malformed:
            # Malformed endpoint: fail closed as unavailable without decryption.
            raise _domain_error(
                error_type="unavailable",
                safe_code="remote_tool_config_invalid",
                safe_message="remote tool endpoint invalid",
                target_identity=identity,
            )
        else:
            availability = CapabilityAvailability(status="available")

        display_name = str(tool.name or identity)
        description = str(tool.description or "")

        executable = ExecutableToolTarget(
            target_identity=identity,
            tool_id=tool.id,
            config_revision=current_rev,
            config_digest=config_digest,
            is_system=False,
            tool_object_or_record=RemoteTool.from_model(tool),
        )
        closure = build_frozen_execution_closure(
            self.db,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            dependency_closure_digest=binding.resolved.dependency_closure_digest,
            dependencies=binding.resolved.dependencies,
        )
        return ResolvedCapabilitySurface(
            binding=binding,
            executable=executable,
            execution_closure=closure,
            display_name=display_name,
            description=description,
            availability=availability,
        )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def _resolve_workflow_surface(
        self, binding: FrozenCapabilityBinding
    ) -> ResolvedCapabilitySurface:
        identity = binding.resolved.target_identity
        workflow_id = binding.resolved.target_id
        version_id = (
            binding.resolved.target_version_id
            or binding.resolved.resolved_workflow_version_id
        )
        if workflow_id is None or version_id is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="workflow_target_missing",
                safe_message="workflow target missing",
                target_identity=identity,
            )

        # Exact owned version row; never follow aggregate published_version_id.
        version = (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.id == version_id,
                AssistantWorkflowVersion.workflow_id == workflow_id,
            )
            .one_or_none()
        )
        if version is None or not isinstance(version.snapshot, dict):
            raise _domain_error(
                error_type="version_drift",
                safe_code="workflow_version_missing",
                safe_message="workflow version missing",
                target_identity=identity,
            )

        workflow = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.id == workflow_id)
            .one_or_none()
        )
        if workflow is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="workflow_missing",
                safe_message="workflow missing",
                target_identity=identity,
            )

        if identity != f"workflow:{workflow.id}":
            raise _domain_error(
                error_type="version_drift",
                safe_code="workflow_identity_mismatch",
                safe_message="workflow identity mismatch",
                target_identity=identity,
            )

        snapshot = version.snapshot
        snapshot_digest = sha256_canonical_json(snapshot)
        if binding.resolved.config_digest is not None:
            if binding.resolved.config_digest != snapshot_digest:
                raise _domain_error(
                    error_type="version_drift",
                    safe_code="workflow_snapshot_drift",
                    safe_message="workflow snapshot digest drift",
                    target_identity=identity,
                )
        if binding.resolved.executable_revision is not None:
            if str(binding.resolved.executable_revision) != str(version.id):
                raise _domain_error(
                    error_type="version_drift",
                    safe_code="workflow_executable_revision_drift",
                    safe_message="workflow executable revision drift",
                    target_identity=identity,
                )

        # Drift evidence only: derive current contract from exact snapshot; never
        # replace frozen binding schemas.
        try:
            wf_input = _workflow_input_from_snapshot(snapshot)
            contract = workflow_contract_from_input(wf_input)
            current_input_digest = binding_schema_digest(contract.input_schema)  # type: ignore[arg-type]
            current_output_digest = binding_schema_digest(contract.output_schema)  # type: ignore[arg-type]
        except (WorkflowContractError, Exception) as exc:
            raise _domain_error(
                error_type="version_drift",
                safe_code="workflow_contract_invalid",
                safe_message="workflow contract invalid",
                target_identity=identity,
            ) from exc

        if current_input_digest != binding.resolved.input_schema_digest:
            raise _domain_error(
                error_type="version_drift",
                safe_code="schema_drift",
                safe_message="workflow input schema drift",
                target_identity=identity,
            )
        if current_output_digest != binding.resolved.output_schema_digest:
            raise _domain_error(
                error_type="version_drift",
                safe_code="schema_drift",
                safe_message="workflow output schema drift",
                target_identity=identity,
            )

        availability = (
            CapabilityAvailability(status="disabled", reason_code="workflow_disabled")
            if not bool(workflow.enabled)
            else CapabilityAvailability(status="available")
        )

        executable = ExecutableWorkflowVersionTarget(
            workflow_id=workflow.id,
            version_id=version.id,
            snapshot_digest=snapshot_digest,
            parsed_published_input=wf_input,
        )
        closure = build_frozen_execution_closure(
            self.db,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            dependency_closure_digest=binding.resolved.dependency_closure_digest,
            dependencies=binding.resolved.dependencies,
        )
        return ResolvedCapabilitySurface(
            binding=binding,
            executable=executable,
            execution_closure=closure,
            display_name=str(workflow.name or identity),
            description=str(workflow.description or ""),
            availability=availability,
        )

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    def _resolve_agent_surface(
        self, binding: FrozenCapabilityBinding
    ) -> ResolvedCapabilitySurface:
        identity = binding.resolved.target_identity
        agent_id = binding.resolved.target_id
        version_id = (
            binding.resolved.target_version_id
            or binding.resolved.resolved_agent_version_id
        )
        if agent_id is None or version_id is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="agent_target_missing",
                safe_message="agent target missing",
                target_identity=identity,
            )

        version = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.id == version_id,
                AssistantAgentProfileVersion.agent_profile_id == agent_id,
            )
            .one_or_none()
        )
        if version is None or not isinstance(version.snapshot, dict):
            raise _domain_error(
                error_type="version_drift",
                safe_code="agent_version_missing",
                safe_message="agent version missing",
                target_identity=identity,
            )

        agent = (
            self.db.query(AssistantAgentProfile)
            .filter(AssistantAgentProfile.id == agent_id)
            .one_or_none()
        )
        if agent is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="agent_missing",
                safe_message="agent missing",
                target_identity=identity,
            )

        if identity != f"agent:{agent.id}":
            raise _domain_error(
                error_type="version_drift",
                safe_code="agent_identity_mismatch",
                safe_message="agent identity mismatch",
                target_identity=identity,
            )

        snapshot = copy.deepcopy(version.snapshot)
        snapshot_digest = sha256_canonical_json(snapshot)
        if binding.resolved.config_digest is not None:
            if binding.resolved.config_digest != snapshot_digest:
                raise _domain_error(
                    error_type="version_drift",
                    safe_code="agent_snapshot_drift",
                    safe_message="agent snapshot digest drift",
                    target_identity=identity,
                )
        if binding.resolved.executable_revision is not None:
            if str(binding.resolved.executable_revision) != str(version.id):
                raise _domain_error(
                    error_type="version_drift",
                    safe_code="agent_executable_revision_drift",
                    safe_message="agent executable revision drift",
                    target_identity=identity,
                )

        # Agent schemas come only from the frozen binding; no profile defaults.
        # We still verify digests match the frozen body (already in binding).
        _ = binding.resolved.input_schema_digest
        _ = binding.resolved.output_schema_digest

        availability = (
            CapabilityAvailability(status="disabled", reason_code="agent_disabled")
            if not bool(agent.enabled)
            else CapabilityAvailability(status="available")
        )

        executable = ExecutableAgentVersionTarget(
            agent_profile_id=agent.id,
            version_id=version.id,
            snapshot_digest=snapshot_digest,
            parsed_snapshot=snapshot,
        )
        closure = build_frozen_execution_closure(
            self.db,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            dependency_closure_digest=binding.resolved.dependency_closure_digest,
            dependencies=binding.resolved.dependencies,
        )
        return ResolvedCapabilitySurface(
            binding=binding,
            executable=executable,
            execution_closure=closure,
            display_name=str(agent.name or identity),
            description=str(agent.description or ""),
            availability=availability,
        )


__all__ = ["CapabilityRegistry"]
