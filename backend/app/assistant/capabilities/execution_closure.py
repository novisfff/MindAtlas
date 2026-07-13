"""No-fallback exact dependency closure for Capability execution.

Implements the Workflow engine ``ExactRuntimeDependencyResolver`` Protocol without
importing engine internals into adapters in reverse. Credential/model activation
is gated behind a verified allow decision.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityPolicyDecision,
)
from app.assistant.capabilities.errors import CapabilityDomainError
from app.assistant.capabilities.ports import (
    AuthorizedModelRuntimeConfig,
    ExactRuntimeDependencyResolver,
    ExecutableToolTarget,
    ExecutableWorkflowVersionTarget,
    FrozenClosureRuntimeResolver,
    VerifiedModelTarget,
)
from app.assistant.domain.contracts import ResolvedCapabilityDependency
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.skills import resolution as skill_resolution
from app.assistant.domain.json_schema import binding_schema_digest
from app.ai_registry.models import AiCredential, AiModel
from app.assistant_config.models import (
    AssistantTool,
    AssistantWorkflow,
    AssistantWorkflowVersion,
)
from app.assistant_config.registry import ToolRegistry
from app.assistant_config.remote_tool import RemoteTool


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


@dataclass(frozen=True)
class _VerifiedToolEntry:
    source_locator: str
    target_identity: str
    tool_name: str
    tool_id: UUID | None
    config_revision: int | None
    config_digest: str | None
    is_system: bool
    tool_object_or_record: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class _VerifiedWorkflowEntry:
    source_locator: str
    target_identity: str
    workflow_id: UUID
    version_id: UUID
    snapshot_digest: str
    parsed_published_input: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class _VerifiedModelEntry:
    source_locator: str
    target_identity: str
    model_id: UUID
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    model_config_digest: str
    credential_config_digest: str
    model_name: str
    provider_protocol: str
    # Non-secret credential handle holder (encrypted token + base_url) only.
    credential_slot: object = field(repr=False, compare=False)


@dataclass
class _ActivatingResolver:
    """Post-policy exact resolver; may activate credentials once per model slot."""

    tools_by_locator: dict[str, _VerifiedToolEntry]
    workflows_by_locator: dict[str, _VerifiedWorkflowEntry]
    models_by_locator: dict[str, _VerifiedModelEntry]
    _activated_models: dict[str, AuthorizedModelRuntimeConfig] = field(
        default_factory=dict, repr=False
    )

    def require_tool(
        self,
        *,
        source_locator: str,
        tool_name: str,
    ) -> ExecutableToolTarget:
        entry = self.tools_by_locator.get(source_locator)
        if entry is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="undeclared_dependency",
                safe_message="dependency not in frozen closure",
                target_identity=tool_name,
            )
        expected_name = entry.tool_name
        if tool_name != expected_name and entry.target_identity != f"system-tool:{tool_name}":
            # Remote tools are looked up by declared name; system tools by export name.
            if entry.is_system or entry.tool_name != tool_name:
                if entry.tool_name != tool_name:
                    raise _domain_error(
                        error_type="not_found",
                        safe_code="dependency_name_mismatch",
                        safe_message="dependency name mismatch",
                        target_identity=tool_name,
                    )
        return ExecutableToolTarget(
            target_identity=entry.target_identity,
            tool_id=entry.tool_id,
            config_revision=entry.config_revision,
            config_digest=entry.config_digest,
            is_system=entry.is_system,
            tool_object_or_record=entry.tool_object_or_record,
        )

    def require_workflow_version(
        self,
        *,
        source_locator: str,
        workflow_id: UUID,
        version_id: UUID,
    ) -> ExecutableWorkflowVersionTarget:
        entry = self.workflows_by_locator.get(source_locator)
        if entry is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="undeclared_dependency",
                safe_message="dependency not in frozen closure",
                target_identity=f"workflow:{workflow_id}",
            )
        if entry.workflow_id != workflow_id or entry.version_id != version_id:
            raise _domain_error(
                error_type="version_drift",
                safe_code="workflow_version_mismatch",
                safe_message="workflow version mismatch",
                target_identity=entry.target_identity,
            )
        return ExecutableWorkflowVersionTarget(
            workflow_id=entry.workflow_id,
            version_id=entry.version_id,
            snapshot_digest=entry.snapshot_digest,
            parsed_published_input=entry.parsed_published_input,
        )

    def require_model(
        self,
        *,
        source_locator: str,
        requested_model_id: UUID | None,
    ) -> AuthorizedModelRuntimeConfig:
        entry = self.models_by_locator.get(source_locator)
        if entry is None:
            raise _domain_error(
                error_type="not_found",
                safe_code="undeclared_dependency",
                safe_message="dependency not in frozen closure",
                target_identity=(
                    f"model:{requested_model_id}" if requested_model_id else "model:unknown"
                ),
            )
        if requested_model_id is not None and entry.model_id != requested_model_id:
            raise _domain_error(
                error_type="version_drift",
                safe_code="model_id_mismatch",
                safe_message="model id mismatch",
                target_identity=entry.target_identity,
            )
        cached = self._activated_models.get(source_locator)
        if cached is not None:
            return cached

        # Activate once: decrypt credential slot after policy allow.
        from app.ai_provider.crypto import decrypt_api_key

        slot = entry.credential_slot
        encrypted = getattr(slot, "api_key_encrypted", None)
        base_url = getattr(slot, "base_url", "") or ""
        handle: object
        if encrypted:
            try:
                api_key = decrypt_api_key(str(encrypted))
            except Exception as exc:
                # Fail closed: never hand ciphertext out as an api_key.
                raise _domain_error(
                    error_type="unavailable",
                    safe_code="credential_decrypt_failed",
                    safe_message="credential activation failed",
                    target_identity=entry.target_identity,
                ) from exc
            handle = {
                "api_key": api_key,
                "base_url": base_url,
                "model_name": entry.model_name,
            }
        else:
            handle = {
                "api_key": None,
                "base_url": base_url,
                "model_name": entry.model_name,
            }
        verified = VerifiedModelTarget(
            source_locator=entry.source_locator,
            model_id=entry.model_id,
            model_runtime_revision=entry.model_runtime_revision,
            credential_id=entry.credential_id,
            credential_runtime_revision=entry.credential_runtime_revision,
            model_config_digest=entry.model_config_digest,
            credential_config_digest=entry.credential_config_digest,
        )
        activated = AuthorizedModelRuntimeConfig(
            verified=verified,
            provider_protocol=entry.provider_protocol,
            model_name=entry.model_name,
            client_or_credential_handle=handle,
        )
        self._activated_models[source_locator] = activated
        return activated


@dataclass(frozen=True)
class FrozenExecutionClosure:
    """Non-activating preflighted index of exact dependency targets."""

    binding_contract_digest: str
    dependency_closure_digest: str
    tools_by_locator: dict[str, _VerifiedToolEntry] = field(repr=False, compare=False)
    workflows_by_locator: dict[str, _VerifiedWorkflowEntry] = field(
        repr=False, compare=False
    )
    models_by_locator: dict[str, _VerifiedModelEntry] = field(repr=False, compare=False)

    def bind_authorized(
        self,
        *,
        decision: CapabilityPolicyDecision,
    ) -> ExactRuntimeDependencyResolver:
        if not decision.allowed:
            raise _domain_error(
                error_type="unauthorized",
                safe_code="policy_denied",
                safe_message="capability policy denied activation",
            )
        # Require a real allow decision identity; forged empty shells must not activate.
        if not str(getattr(decision, "call_id", "") or "").strip():
            raise _domain_error(
                error_type="unauthorized",
                safe_code="policy_decision_identity_missing",
                safe_message="capability policy decision identity missing",
            )
        if not str(getattr(decision, "decision_digest", "") or "").strip():
            raise _domain_error(
                error_type="unauthorized",
                safe_code="policy_decision_digest_missing",
                safe_message="capability policy decision digest missing",
            )
        if getattr(decision, "dispatch_permit", None) is None:
            raise _domain_error(
                error_type="unauthorized",
                safe_code="policy_dispatch_permit_missing",
                safe_message="capability policy dispatch permit missing",
            )
        # When the decision (or nested evidence) carries closure digests, bind them.
        for attr, expected, code in (
            (
                "binding_contract_digest",
                self.binding_contract_digest,
                "policy_binding_contract_mismatch",
            ),
            (
                "dependency_closure_digest",
                self.dependency_closure_digest,
                "policy_dependency_closure_mismatch",
            ),
        ):
            carried = getattr(decision, attr, None)
            if carried is None:
                evidence = getattr(decision, "evidence", None)
                if evidence is not None:
                    carried = getattr(evidence, attr, None)
            if carried is not None and str(carried) != expected:
                raise _domain_error(
                    error_type="unauthorized",
                    safe_code=code,
                    safe_message="capability policy decision does not match closure",
                )
        return _ActivatingResolver(
            tools_by_locator=self.tools_by_locator,
            workflows_by_locator=self.workflows_by_locator,
            models_by_locator=self.models_by_locator,
        )


def _verify_system_tool_dep(
    dep: ResolvedCapabilityDependency,
) -> _VerifiedToolEntry:
    if not dep.target_identity.startswith("system-tool:"):
        raise _domain_error(
            error_type="version_drift",
            safe_code="system_tool_identity_invalid",
            safe_message="system tool identity invalid",
            target_identity=dep.target_identity,
        )
    tool_name = dep.target_identity.split(":", 1)[1]
    if tool_name not in ToolRegistry.list_runtime_system_tool_names():
        raise _domain_error(
            error_type="not_found",
            safe_code="system_tool_missing",
            safe_message="system tool not exported",
            target_identity=dep.target_identity,
        )
    tool_obj = ToolRegistry.resolve_system_tool(tool_name)
    if tool_obj is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="system_tool_missing",
            safe_message="system tool not exported",
            target_identity=dep.target_identity,
        )
    input_schema, output_schema = skill_resolution.system_tool_schemas(tool_name)
    if dep.input_schema_digest is not None and dep.input_schema_digest != binding_schema_digest(
        input_schema
    ):
        raise _domain_error(
            error_type="version_drift",
            safe_code="schema_drift",
            safe_message="dependency input schema drift",
            target_identity=dep.target_identity,
        )
    if dep.output_schema_digest is not None and dep.output_schema_digest != binding_schema_digest(
        output_schema
    ):
        raise _domain_error(
            error_type="version_drift",
            safe_code="schema_drift",
            safe_message="dependency output schema drift",
            target_identity=dep.target_identity,
        )
    app_build = skill_resolution.require_immutable_app_build_revision()
    snapshot = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    frozen_build = snapshot.get("appBuildRevision")
    if frozen_build is not None and str(frozen_build) != app_build:
        raise _domain_error(
            error_type="version_drift",
            safe_code="build_revision_drift",
            safe_message="system tool build revision drift",
            target_identity=dep.target_identity,
        )
    set_digest = skill_resolution.compute_system_tool_contract_set_digest()
    frozen_set = snapshot.get("systemToolContractSetDigest")
    if frozen_set is not None and str(frozen_set) != set_digest:
        raise _domain_error(
            error_type="version_drift",
            safe_code="system_tool_contract_set_drift",
            safe_message="system tool contract set drift",
            target_identity=dep.target_identity,
        )
    return _VerifiedToolEntry(
        source_locator=dep.dependency_path,
        target_identity=dep.target_identity,
        tool_name=tool_name,
        tool_id=None,
        config_revision=None,
        config_digest=set_digest,
        is_system=True,
        tool_object_or_record=tool_obj,
    )


def _verify_remote_tool_dep(
    db: Session,
    dep: ResolvedCapabilityDependency,
) -> _VerifiedToolEntry:
    if dep.resolved_tool_id is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="remote_tool_missing",
            safe_message="remote tool missing",
            target_identity=dep.target_identity,
        )
    tool = (
        db.query(AssistantTool)
        .filter(AssistantTool.id == dep.resolved_tool_id)
        .one_or_none()
    )
    if tool is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="remote_tool_missing",
            safe_message="remote tool missing",
            target_identity=dep.target_identity,
        )
    if (tool.kind or "").lower() != "remote":
        raise _domain_error(
            error_type="version_drift",
            safe_code="remote_tool_kind_mismatch",
            safe_message="remote tool kind mismatch",
            target_identity=dep.target_identity,
        )
    if f"remote-tool:{tool.id}" != dep.target_identity:
        raise _domain_error(
            error_type="version_drift",
            safe_code="remote_tool_identity_mismatch",
            safe_message="remote tool identity mismatch",
            target_identity=dep.target_identity,
        )
    current_rev = int(tool.config_revision or 1)
    if dep.target_revision is not None and int(dep.target_revision) != current_rev:
        raise _domain_error(
            error_type="version_drift",
            safe_code="config_revision_drift",
            safe_message="remote tool config revision drift",
            target_identity=dep.target_identity,
        )
    try:
        execution = skill_resolution.secret_free_remote_execution_snapshot(tool)
        config_digest = sha256_canonical_json(execution)
    except Exception as exc:
        raise _domain_error(
            error_type="unavailable",
            safe_code="remote_tool_config_invalid",
            safe_message="remote tool config invalid",
            target_identity=dep.target_identity,
        ) from exc
    snapshot = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    frozen_execution = snapshot.get("execution")
    if isinstance(frozen_execution, dict):
        frozen_digest = sha256_canonical_json(frozen_execution)  # type: ignore[arg-type]
        if frozen_digest != config_digest:
            raise _domain_error(
                error_type="version_drift",
                safe_code="config_digest_drift",
                safe_message="remote tool config digest drift",
                target_identity=dep.target_identity,
            )
    # Disabled is availability at root; for nested deps treat as unavailable.
    if not bool(tool.enabled):
        raise _domain_error(
            error_type="unavailable",
            safe_code="tool_disabled",
            safe_message="dependency tool disabled",
            target_identity=dep.target_identity,
        )
    return _VerifiedToolEntry(
        source_locator=dep.dependency_path,
        target_identity=dep.target_identity,
        tool_name=str(tool.name),
        tool_id=tool.id,
        config_revision=current_rev,
        config_digest=config_digest,
        is_system=False,
        tool_object_or_record=RemoteTool.from_model(tool),
    )


def _owned_workflow_id_from_dep(dep: ResolvedCapabilityDependency) -> UUID | None:
    """Extract expected workflow aggregate id from frozen identity / snapshot."""
    frozen_snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    raw = frozen_snap.get("targetId")
    if raw is not None:
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            pass
    identity = dep.target_identity or ""
    if identity.startswith("workflow:"):
        try:
            return UUID(identity.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


def _verify_workflow_dep(
    db: Session,
    dep: ResolvedCapabilityDependency,
) -> _VerifiedWorkflowEntry:
    if dep.resolved_workflow_version_id is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="workflow_version_missing",
            safe_message="workflow version missing",
            target_identity=dep.target_identity,
        )
    owned_workflow_id = _owned_workflow_id_from_dep(dep)
    version_query = db.query(AssistantWorkflowVersion).filter(
        AssistantWorkflowVersion.id == dep.resolved_workflow_version_id
    )
    if owned_workflow_id is not None:
        version_query = version_query.filter(
            AssistantWorkflowVersion.workflow_id == owned_workflow_id
        )
    version = version_query.one_or_none()
    if version is None or not isinstance(version.snapshot, dict):
        raise _domain_error(
            error_type="version_drift",
            safe_code="workflow_version_missing",
            safe_message="workflow version missing",
            target_identity=dep.target_identity,
        )
    workflow = (
        db.query(AssistantWorkflow)
        .filter(AssistantWorkflow.id == version.workflow_id)
        .one_or_none()
    )
    if workflow is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="workflow_missing",
            safe_message="workflow missing",
            target_identity=dep.target_identity,
        )
    expected_identity = f"workflow:{workflow.id}"
    if dep.target_identity != expected_identity:
        raise _domain_error(
            error_type="version_drift",
            safe_code="workflow_identity_mismatch",
            safe_message="workflow identity mismatch",
            target_identity=dep.target_identity,
        )
    if owned_workflow_id is not None and workflow.id != owned_workflow_id:
        raise _domain_error(
            error_type="version_drift",
            safe_code="workflow_ownership_mismatch",
            safe_message="workflow version ownership mismatch",
            target_identity=dep.target_identity,
        )
    if not bool(workflow.enabled):
        raise _domain_error(
            error_type="unavailable",
            safe_code="workflow_disabled",
            safe_message="dependency workflow disabled",
            target_identity=dep.target_identity,
        )
    snapshot_digest = sha256_canonical_json(version.snapshot)
    # Fail closed: frozen snapshot/config digests must match the exact owned version row.
    frozen_snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    frozen_digest = frozen_snap.get("snapshotDigest") or frozen_snap.get("configDigest")
    if frozen_digest is not None and str(frozen_digest) != snapshot_digest:
        raise _domain_error(
            error_type="version_drift",
            safe_code="workflow_snapshot_drift",
            safe_message="workflow snapshot digest drift",
            target_identity=dep.target_identity,
        )
    from app.assistant_config.schemas import WorkflowInput

    parsed = WorkflowInput.model_validate(
        {
            "nodes": version.snapshot.get("nodes") or [],
            "edges": version.snapshot.get("edges") or [],
            "viewport": version.snapshot.get("viewport"),
        }
        if "nodes" in version.snapshot
        else version.snapshot
    )
    return _VerifiedWorkflowEntry(
        source_locator=dep.dependency_path,
        target_identity=dep.target_identity,
        workflow_id=workflow.id,
        version_id=version.id,
        snapshot_digest=snapshot_digest,
        parsed_published_input=parsed,
    )


def _owned_agent_id_from_dep(dep: ResolvedCapabilityDependency) -> UUID | None:
    frozen_snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    raw = frozen_snap.get("targetId")
    if raw is not None:
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            pass
    identity = dep.target_identity or ""
    if identity.startswith("agent:"):
        try:
            return UUID(identity.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


def _verify_agent_dep(
    db: Session,
    dep: ResolvedCapabilityDependency,
) -> None:
    """Preflight nested agent deps: exact owned version + optional snapshot digest."""
    from app.assistant_config.models import AssistantAgentProfile, AssistantAgentProfileVersion

    if dep.resolved_agent_version_id is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="agent_version_missing",
            safe_message="agent version missing",
            target_identity=dep.target_identity,
        )
    owned_agent_id = _owned_agent_id_from_dep(dep)
    version_query = db.query(AssistantAgentProfileVersion).filter(
        AssistantAgentProfileVersion.id == dep.resolved_agent_version_id
    )
    if owned_agent_id is not None:
        version_query = version_query.filter(
            AssistantAgentProfileVersion.agent_profile_id == owned_agent_id
        )
    version = version_query.one_or_none()
    if version is None or not isinstance(version.snapshot, dict):
        raise _domain_error(
            error_type="version_drift",
            safe_code="agent_version_missing",
            safe_message="agent version missing",
            target_identity=dep.target_identity,
        )
    agent = (
        db.query(AssistantAgentProfile)
        .filter(AssistantAgentProfile.id == version.agent_profile_id)
        .one_or_none()
    )
    if agent is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="agent_missing",
            safe_message="agent missing",
            target_identity=dep.target_identity,
        )
    expected_identity = f"agent:{agent.id}"
    if dep.target_identity != expected_identity:
        raise _domain_error(
            error_type="version_drift",
            safe_code="agent_identity_mismatch",
            safe_message="agent identity mismatch",
            target_identity=dep.target_identity,
        )
    if owned_agent_id is not None and agent.id != owned_agent_id:
        raise _domain_error(
            error_type="version_drift",
            safe_code="agent_ownership_mismatch",
            safe_message="agent version ownership mismatch",
            target_identity=dep.target_identity,
        )
    if not bool(agent.enabled):
        raise _domain_error(
            error_type="unavailable",
            safe_code="agent_disabled",
            safe_message="dependency agent disabled",
            target_identity=dep.target_identity,
        )
    snapshot_digest = sha256_canonical_json(version.snapshot)
    frozen_snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    frozen_digest = frozen_snap.get("snapshotDigest") or frozen_snap.get("configDigest")
    if frozen_digest is not None and str(frozen_digest) != snapshot_digest:
        raise _domain_error(
            error_type="version_drift",
            safe_code="agent_snapshot_drift",
            safe_message="agent snapshot digest drift",
            target_identity=dep.target_identity,
        )


def _verify_model_dep(
    db: Session,
    dep: ResolvedCapabilityDependency,
) -> _VerifiedModelEntry:
    if dep.resolved_model_id is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="model_missing",
            safe_message="model missing",
            target_identity=dep.target_identity,
        )
    model = (
        db.query(AiModel)
        .options(joinedload(AiModel.credential))
        .filter(AiModel.id == dep.resolved_model_id)
        .one_or_none()
    )
    if model is None:
        raise _domain_error(
            error_type="not_found",
            safe_code="model_missing",
            safe_message="model missing",
            target_identity=dep.target_identity,
        )
    credential = model.credential
    if credential is None:
        raise _domain_error(
            error_type="unavailable",
            safe_code="credential_missing",
            safe_message="model credential missing",
            target_identity=dep.target_identity,
        )
    expected_identity = f"model:{model.id}"
    if dep.target_identity != expected_identity:
        raise _domain_error(
            error_type="version_drift",
            safe_code="model_identity_mismatch",
            safe_message="model identity mismatch",
            target_identity=dep.target_identity,
        )
    model_rev = int(model.runtime_revision or 1)
    cred_rev = int(credential.runtime_revision or 1)
    if dep.target_revision is not None and int(dep.target_revision) != model_rev:
        raise _domain_error(
            error_type="version_drift",
            safe_code="model_revision_drift",
            safe_message="model runtime revision drift",
            target_identity=dep.target_identity,
        )
    snapshot = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    frozen_model_rev = snapshot.get("modelRuntimeRevision")
    if frozen_model_rev is not None and int(frozen_model_rev) != model_rev:
        raise _domain_error(
            error_type="version_drift",
            safe_code="model_revision_drift",
            safe_message="model runtime revision drift",
            target_identity=dep.target_identity,
        )
    frozen_cred_rev = snapshot.get("credentialRuntimeRevision")
    if frozen_cred_rev is not None and int(frozen_cred_rev) != cred_rev:
        raise _domain_error(
            error_type="version_drift",
            safe_code="credential_revision_drift",
            safe_message="credential runtime revision drift",
            target_identity=dep.target_identity,
        )
    parts = urlsplit((credential.base_url or "").strip())
    credential_config_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "scheme": parts.scheme or None,
            "host": parts.hostname,
            "port": parts.port,
            "path": parts.path or None,
            "runtimeRevision": cred_rev,
        }
    )
    model_config_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "name": model.name,
            "modelType": model.model_type,
            "runtimeRevision": model_rev,
        }
    )
    frozen_model_digest = snapshot.get("modelConfigDigest")
    if frozen_model_digest is not None and str(frozen_model_digest) != model_config_digest:
        raise _domain_error(
            error_type="version_drift",
            safe_code="model_config_digest_drift",
            safe_message="model config digest drift",
            target_identity=dep.target_identity,
        )
    frozen_cred_digest = snapshot.get("credentialConfigDigest")
    if (
        frozen_cred_digest is not None
        and str(frozen_cred_digest) != credential_config_digest
    ):
        raise _domain_error(
            error_type="version_drift",
            safe_code="credential_config_digest_drift",
            safe_message="credential config digest drift",
            target_identity=dep.target_identity,
        )
    frozen_name = snapshot.get("modelName")
    if frozen_name is not None and str(frozen_name) != str(model.name):
        raise _domain_error(
            error_type="version_drift",
            safe_code="model_name_drift",
            safe_message="model name drift",
            target_identity=dep.target_identity,
        )
    frozen_cred_id = snapshot.get("credentialId")
    if frozen_cred_id is not None and str(frozen_cred_id) != str(credential.id):
        raise _domain_error(
            error_type="version_drift",
            safe_code="credential_id_drift",
            safe_message="credential id drift",
            target_identity=dep.target_identity,
        )

    @dataclass
    class _CredentialSlot:
        api_key_encrypted: str | None
        base_url: str

    return _VerifiedModelEntry(
        source_locator=dep.dependency_path,
        target_identity=dep.target_identity,
        model_id=model.id,
        model_runtime_revision=model_rev,
        credential_id=credential.id,
        credential_runtime_revision=cred_rev,
        model_config_digest=model_config_digest,
        credential_config_digest=credential_config_digest,
        model_name=str(model.name),
        provider_protocol="openai_compat",
        credential_slot=_CredentialSlot(
            api_key_encrypted=credential.api_key_encrypted,
            base_url=str(credential.base_url or ""),
        ),
    )


def build_frozen_execution_closure(
    db: Session,
    *,
    binding_contract_digest: str,
    dependency_closure_digest: str,
    dependencies: tuple[ResolvedCapabilityDependency, ...],
) -> FrozenClosureRuntimeResolver:
    """Preflight every non-secret dependency ref into an immutable exact index."""
    tools: dict[str, _VerifiedToolEntry] = {}
    workflows: dict[str, _VerifiedWorkflowEntry] = {}
    models: dict[str, _VerifiedModelEntry] = {}
    seen_paths: set[str] = set()

    for dep in dependencies:
        path = dep.dependency_path
        if path in seen_paths:
            raise _domain_error(
                error_type="protocol_error",
                safe_code="duplicate_dependency_path",
                safe_message="duplicate dependency path in closure",
                target_identity=dep.target_identity,
            )
        seen_paths.add(path)
        if dep.dependency_type == "system_tool":
            tools[path] = _verify_system_tool_dep(dep)
        elif dep.dependency_type == "remote_tool":
            tools[path] = _verify_remote_tool_dep(db, dep)
        elif dep.dependency_type == "workflow":
            workflows[path] = _verify_workflow_dep(db, dep)
        elif dep.dependency_type == "agent":
            # Nested agents are not activated via the Workflow engine Protocol in Task 2,
            # but must still be preflighted against the exact owned version + digests.
            _verify_agent_dep(db, dep)
        elif dep.dependency_type == "model":
            models[path] = _verify_model_dep(db, dep)
        else:
            raise _domain_error(
                error_type="protocol_error",
                safe_code="unknown_dependency_type",
                safe_message="unknown dependency type",
                target_identity=dep.target_identity,
            )

    return FrozenExecutionClosure(
        binding_contract_digest=binding_contract_digest,
        dependency_closure_digest=dependency_closure_digest,
        tools_by_locator=tools,
        workflows_by_locator=workflows,
        models_by_locator=models,
    )


__all__ = [
    "FrozenExecutionClosure",
    "build_frozen_execution_closure",
]
