from __future__ import annotations
from types import SimpleNamespace

import copy
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.ai_registry.models import AiModel
from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id
from app.ai_provider.crypto import api_key_hint, encrypt_api_key
from app.assistant_config.models import (
    AssistantAgentProfile,
    AssistantAgentProfileVersion,
    AssistantSystemBehaviorBinding,
    AssistantTargetFolder,
    AssistantWorkflow,
    AssistantWorkflowVersion,
    AssistantTool,
)
from app.assistant_config.registry import SkillRegistry, ToolRegistry
from app.assistant_config.schemas import (
    AgentPublishDraftInput,
    AgentPublishRequest,
    AgentVersionListResponse,
    AssistantAgentProfileCreateRequest,
    AssistantAgentProfileUpdateRequest,
    AssistantFolderMoveRequest,
    AssistantTargetFolderCreateRequest,
    AssistantTargetFolderResponse,
    AssistantTargetFolderUpdateRequest,
    AssistantTargetMoveRequest,
    AssistantToolCreateRequest,
    AssistantToolUpdateRequest,
    AssistantWorkflowCreateRequest,
    CallableWorkflowResponse,
    CallableWorkflowVersionResponse,
    AssistantWorkflowUpdateRequest,
    ClearVersionsResponse,
    DeleteVersionResponse,
    RollbackVersionResponse,
    SystemBehaviorResponse,
    TargetType,
    TargetVersionResponse,
    WorkflowContractParamSchema,
    WorkflowPublishRequest,
    WorkflowVersionListResponse,
    WorkflowInput,
)
from app.assistant_config.workflow_contracts import (
    WorkflowContractError,
    WorkflowContractSnapshot,
    field_specs_to_params,
    schema_summary,
    workflow_contract_from_input,
)
from app.assistant_config.workflow_references import (
    collect_workflow_call_references as pure_collect_workflow_call_references,
    collect_workflow_custom_model_ids as pure_collect_workflow_custom_model_ids,
    collect_workflow_tool_names as pure_collect_workflow_tool_names,
    cfg_get as pure_cfg_get,
    iter_workflow_call_node_configs as pure_iter_workflow_call_node_configs,
    parse_uuid_value as pure_parse_uuid_value,
)
from app.assistant.skills.models import (
    AssistantSkillCapabilityBinding,
    AssistantSkillCapabilityDependency,
)
from app.assistant_config.system_behavior_registry import (
    SystemBehaviorDefinition,
    SystemBehaviorFieldDefinition,
    get_system_behavior_definition,
    list_system_behavior_definitions,
)
from app.assistant_config.standalone_system_target_registry import (
    StandaloneSystemWorkflowDefinition,
    get_standalone_system_workflow_definition,
    list_standalone_system_workflow_definitions,
)
from app.assistant.workflow.system_assets import (
    get_system_asset_by_canonical_name,
    get_system_skill_asset,
    list_system_assets,
    load_system_agent_asset,
    load_system_workflow_asset,
)
from app.common.exceptions import ApiException
from app.system_settings.models import AppSetting
from app.system_settings.service import resolve_system_locale


_SYSTEM_CATALOG_SYNC_LOCK_KEY = 2026040901
_TARGET_FOLDER_MUTATION_LOCK_KEY = 2026041901
_SYSTEM_CATALOG_SIGNATURE_SETTING_KEY = "assistant_config_system_catalog_signature"

_TARGET_VERSION_LIMIT = 100
_SYSTEM_BEHAVIOR_EXAMPLE_WORKFLOW_METADATA: dict[str, dict[str, dict[str, str]]] = {
    "weekly_report_generation": {
        "zh": {
            "name": "weekly_report_example__workflow",
            "description": "周报生成示例工作流。",
        },
        "en": {
            "name": "weekly_report_example__workflow",
            "description": "Weekly report example workflow.",
        },
    },
    "monthly_report_generation": {
        "zh": {
            "name": "monthly_report_example__workflow",
            "description": "月报生成示例工作流。",
        },
        "en": {
            "name": "monthly_report_example__workflow",
            "description": "Monthly report example workflow.",
        },
    },
}


@dataclass(frozen=True)
class WorkflowCallReference:
    source_workflow_id: UUID
    source_workflow_name: str
    source_kind: str
    source_node_id: str
    source_container_node_id: str | None
    source_version_id: UUID | None
    source_version_name: str | None
    source_version_source: str | None
    target_workflow_id: UUID
    binding_mode: str
    target_published_version_id: UUID | None


@dataclass(frozen=True)
class ResolvedWorkflowCallTarget:
    workflow: AssistantWorkflow
    version: AssistantWorkflowVersion
    workflow_input: WorkflowInput
    contract: WorkflowContractSnapshot


@dataclass(frozen=True)
class TargetFolderStats:
    folder_count: int
    workflow_count: int
    agent_count: int
    direct_folder_count: int
    direct_workflow_count: int
    direct_agent_count: int
    last_activity_at: datetime


class AssistantConfigService:
    def __init__(self, db: Session):
        self.db = db

    def _current_locale(self, preferred_locale: str | None = None) -> str:
        return resolve_system_locale(self.db, preferred_locale=preferred_locale)

    def _localized_system_behavior_example_meta(self, behavior_key: str, *, locale: str | None = None) -> dict[str, str]:
        normalized_locale = self._current_locale(locale)
        meta = _SYSTEM_BEHAVIOR_EXAMPLE_WORKFLOW_METADATA.get(behavior_key, {})
        localized = meta.get(normalized_locale) or meta.get("zh") or {}
        return {
            "name": str(localized.get("name") or f"{behavior_key}_example_workflow"),
            "description": str(localized.get("description") or ""),
        }

    @staticmethod
    def _build_default_workflow_input() -> WorkflowInput:
        """Build a default start -> llm -> output workflow for new workflow_dag skills."""
        return WorkflowInput.model_validate(
            {
                "nodes": [
                    {
                        "node_id": "start",
                        "node_type": "start",
                        "label": "Start",
                        "position_x": 120,
                        "position_y": 220,
                        "config": {},
                    },
                    {
                        "node_id": "llm_1",
                        "node_type": "llm",
                        "label": "LLM",
                        "position_x": 460,
                        "position_y": 220,
                        "config": {
                            "outputMode": "text",
                            "userInput": "{{start.user_input}}",
                            "modelSource": "default",
                        },
                    },
                    {
                        "node_id": "output_1",
                        "node_type": "output",
                        "label": "Output",
                        "position_x": 800,
                        "position_y": 220,
                        "config": {
                            "outputMode": "text",
                            "textTemplate": "{{llm_1.response}}",
                        },
                    },
                ],
                "edges": [
                    {
                        "edge_id": "edge_start_llm",
                        "source_node_id": "start",
                        "target_node_id": "llm_1",
                        "source_handle": "output",
                        "target_handle": "input",
                    },
                    {
                        "edge_id": "edge_llm_output",
                        "source_node_id": "llm_1",
                        "target_node_id": "output_1",
                        "source_handle": "output",
                        "target_handle": "input",
                    },
                ],
            }
        )

    @staticmethod
    def _derive_target_type(
        *,
        target_type: TargetType | None = None,
        workflow_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
        langgraph_pattern: str | None = None,
    ) -> TargetType:
        if target_type in {"workflow", "agent"}:
            return target_type
        if workflow_id is not None:
            return "workflow"
        if agent_profile_id is not None:
            return "agent"
        if str(langgraph_pattern or "").strip().lower() == "workflow_dag":
            return "workflow"
        return "agent"

    @staticmethod
    def _to_workflow_input_from_entity(workflow: AssistantWorkflow) -> WorkflowInput:
        snapshot = workflow.graph_snapshot if isinstance(getattr(workflow, "graph_snapshot", None), dict) else {}
        nodes = [
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": node.label,
                "position_x": node.position_x,
                "position_y": node.position_y,
                "config": node.config or {},
            }
            for node in (workflow.nodes or [])
        ]
        edges = [
            {
                "edge_id": edge.edge_id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "source_handle": edge.source_handle,
                "target_handle": edge.target_handle,
                "condition_type": edge.condition_type,
                "condition_expr": edge.condition_expr,
                "label": edge.label,
            }
            for edge in (workflow.edges or [])
        ]
        return WorkflowInput.model_validate(
            {
                "nodes": nodes,
                "edges": edges,
                "viewport": snapshot.get("viewport") if isinstance(snapshot.get("viewport"), dict) else workflow.workflow_viewport,
            }
        )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _default_version_name(version_name: str | None) -> str:
        value = str(version_name or "").strip()
        if value:
            return value[:255]
        return AssistantConfigService._utcnow().strftime("%Y-%m-%d %H:%M:%S")

    def _default_agent_system_prompt(self) -> str:
        if self._current_locale() == "zh":
            return "你是 MindAtlas 的 AI 助手，友好地回复用户。"
        return "You are MindAtlas's AI assistant. Respond helpfully and clearly."

    def _workflow_name_exists(self, name: str) -> bool:
        candidate = str(name or "").strip().lower()
        if not candidate:
            return False
        existing = (
            self.db.query(AssistantWorkflow.id)
            .filter(func.lower(AssistantWorkflow.name) == candidate)
            .first()
        )
        return existing is not None

    def _agent_profile_name_exists(self, name: str) -> bool:
        candidate = str(name or "").strip().lower()
        if not candidate:
            return False
        existing = (
            self.db.query(AssistantAgentProfile.id)
            .filter(func.lower(AssistantAgentProfile.name) == candidate)
            .first()
        )
        return existing is not None

    def _next_available_workflow_name(self, base_name: str) -> str:
        normalized = str(base_name or "").strip()
        if not normalized:
            raise ValueError("base_name is required")
        if not self._workflow_name_exists(normalized):
            return normalized

        suffix = 2
        while True:
            candidate = f"{normalized}__{suffix}"
            if not self._workflow_name_exists(candidate):
                return candidate
            suffix += 1

    def _next_available_agent_profile_name(self, base_name: str) -> str:
        normalized = str(base_name or "").strip()
        if not normalized:
            raise ValueError("base_name is required")
        if not self._agent_profile_name_exists(normalized):
            return normalized

        suffix = 2
        while True:
            candidate = f"{normalized}__{suffix}"
            if not self._agent_profile_name_exists(candidate):
                return candidate
            suffix += 1

    def _next_available_copy_name(
        self,
        base_name: str,
        *,
        exists: Callable[[str], bool],
    ) -> str:
        normalized = str(base_name or "").strip()
        if not normalized:
            raise ValueError("base_name is required")

        locale = self._current_locale()
        if locale == "zh":
            def candidate_for(index: int) -> str:
                return f"{normalized}（副本）" if index == 1 else f"{normalized}（副本 {index}）"
        else:
            def candidate_for(index: int) -> str:
                return f"{normalized} (Copy)" if index == 1 else f"{normalized} (Copy {index})"

        index = 1
        while True:
            candidate = candidate_for(index)
            if not exists(candidate):
                return candidate
            index += 1

    def _get_target_folder(self, folder_id: UUID) -> AssistantTargetFolder:
        folder = (
            self.db.query(AssistantTargetFolder)
            .filter(AssistantTargetFolder.id == folder_id)
            .first()
        )
        if folder is None:
            raise ApiException(status_code=404, code=40435, message=f"Target folder not found: {folder_id}")
        return folder

    def _target_folder_name_exists(
        self,
        name: str,
        *,
        parent_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> bool:
        candidate = str(name or "").strip().lower()
        if not candidate:
            return False
        q = self.db.query(AssistantTargetFolder.id).filter(func.lower(AssistantTargetFolder.name) == candidate)
        if parent_id is None:
            q = q.filter(AssistantTargetFolder.parent_id.is_(None))
        else:
            q = q.filter(AssistantTargetFolder.parent_id == parent_id)
        if exclude_id is not None:
            q = q.filter(AssistantTargetFolder.id != exclude_id)
        return q.first() is not None

    def _ensure_target_folder_name_available(
        self,
        name: str,
        *,
        parent_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> None:
        if self._target_folder_name_exists(name, parent_id=parent_id, exclude_id=exclude_id):
            raise ApiException(status_code=400, code=40033, message=f"Target folder name exists: {name}")

    def _next_available_target_folder_name(
        self,
        name: str,
        *,
        parent_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> str:
        normalized = self._normalize_target_folder_name(name)
        if not self._target_folder_name_exists(normalized, parent_id=parent_id, exclude_id=exclude_id):
            return normalized
        index = 2
        while True:
            suffix = f" ({index})"
            candidate = f"{normalized[:128 - len(suffix)]}{suffix}"
            if not self._target_folder_name_exists(candidate, parent_id=parent_id, exclude_id=exclude_id):
                return candidate
            index += 1

    @staticmethod
    def _normalize_target_folder_name(name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ApiException(status_code=400, code=40036, message="Target folder name cannot be blank")
        return normalized

    def _normalize_target_folder_style(self, color_token: str | None, icon_key: str | None) -> tuple[str, str]:
        normalized_color = str(color_token or "slate").strip() or "slate"
        normalized_icon = str(icon_key or "folder").strip() or "folder"
        return normalized_color[:32], normalized_icon[:32]

    def _assert_target_folder_parent_valid(
        self,
        *,
        folder_id: UUID | None,
        parent_id: UUID | None,
    ) -> AssistantTargetFolder | None:
        if parent_id is None:
            return None
        parent = self._get_target_folder(parent_id)
        if folder_id is None:
            return parent
        if folder_id == parent_id:
            raise ApiException(status_code=400, code=40034, message="Folder cannot be its own parent")

        current = parent
        while current is not None:
            if current.id == folder_id:
                raise ApiException(status_code=400, code=40035, message="Folder move would create a cycle")
            current = current.parent
        return parent

    def _validate_folder_assignment(self, folder_id: UUID | None) -> AssistantTargetFolder | None:
        if folder_id is None:
            return None
        return self._get_target_folder(folder_id)

    def _compute_target_folder_payloads(self) -> list[dict[str, Any]]:
        folders = self.db.query(AssistantTargetFolder).order_by(AssistantTargetFolder.created_at.asc()).all()
        workflow_rows = self.db.query(
            AssistantWorkflow.folder_id,
            AssistantWorkflow.updated_at,
        ).all()
        agent_rows = self.db.query(
            AssistantAgentProfile.folder_id,
            AssistantAgentProfile.updated_at,
        ).all()

        folder_map = {folder.id: folder for folder in folders}
        children_map: dict[UUID | None, list[AssistantTargetFolder]] = {}
        for folder in folders:
            children_map.setdefault(folder.parent_id, []).append(folder)

        workflow_updates: dict[UUID | None, list[datetime]] = {}
        for folder_id, updated_at in workflow_rows:
            workflow_updates.setdefault(folder_id, []).append(updated_at or self._utcnow())

        agent_updates: dict[UUID | None, list[datetime]] = {}
        for folder_id, updated_at in agent_rows:
            agent_updates.setdefault(folder_id, []).append(updated_at or self._utcnow())

        path_cache: dict[UUID, list[dict[str, Any]]] = {}
        stats_cache: dict[UUID, TargetFolderStats] = {}
        path_visiting: set[UUID] = set()
        stats_visiting: set[UUID] = set()

        def resolve_path(folder: AssistantTargetFolder) -> list[dict[str, Any]]:
            cached = path_cache.get(folder.id)
            if cached is not None:
                return cached
            if folder.id in path_visiting:
                raise ApiException(status_code=409, code=40971, message="Target folder hierarchy contains a cycle")
            path_visiting.add(folder.id)
            if folder.parent_id is None:
                path = [{"id": folder.id, "name": folder.name}]
            else:
                parent = folder_map.get(folder.parent_id)
                path = [*resolve_path(parent), {"id": folder.id, "name": folder.name}] if parent else [{"id": folder.id, "name": folder.name}]
            path_visiting.remove(folder.id)
            path_cache[folder.id] = path
            return path

        def resolve_stats(folder: AssistantTargetFolder) -> TargetFolderStats:
            cached = stats_cache.get(folder.id)
            if cached is not None:
                return cached
            if folder.id in stats_visiting:
                raise ApiException(status_code=409, code=40971, message="Target folder hierarchy contains a cycle")
            stats_visiting.add(folder.id)
            child_folders = children_map.get(folder.id, [])
            direct_workflow_updates = workflow_updates.get(folder.id, [])
            direct_agent_updates = agent_updates.get(folder.id, [])
            folder_count = len(child_folders)
            workflow_count = len(direct_workflow_updates)
            agent_count = len(direct_agent_updates)
            last_activity = folder.updated_at or folder.created_at or self._utcnow()
            if direct_workflow_updates:
                last_activity = max([last_activity, *direct_workflow_updates])
            if direct_agent_updates:
                last_activity = max([last_activity, *direct_agent_updates])
            for child in child_folders:
                child_stats = resolve_stats(child)
                folder_count += child_stats.folder_count
                workflow_count += child_stats.workflow_count
                agent_count += child_stats.agent_count
                last_activity = max(last_activity, child_stats.last_activity_at)
            stats = TargetFolderStats(
                folder_count=folder_count,
                workflow_count=workflow_count,
                agent_count=agent_count,
                direct_folder_count=len(child_folders),
                direct_workflow_count=len(direct_workflow_updates),
                direct_agent_count=len(direct_agent_updates),
                last_activity_at=last_activity,
            )
            stats_visiting.remove(folder.id)
            stats_cache[folder.id] = stats
            return stats

        payloads: list[dict[str, Any]] = []
        for folder in folders:
            stats = resolve_stats(folder)
            payloads.append(
                {
                    "id": folder.id,
                    "name": folder.name,
                    "description": folder.description or "",
                    "parent_id": folder.parent_id,
                    "color_token": folder.color_token or "slate",
                    "icon_key": folder.icon_key or "folder",
                    "path": resolve_path(folder),
                    "folder_count": stats.folder_count,
                    "workflow_count": stats.workflow_count,
                    "agent_count": stats.agent_count,
                    "direct_folder_count": stats.direct_folder_count,
                    "direct_workflow_count": stats.direct_workflow_count,
                    "direct_agent_count": stats.direct_agent_count,
                    "last_activity_at": stats.last_activity_at,
                    "created_at": folder.created_at,
                    "updated_at": folder.updated_at,
                }
            )
        payloads.sort(key=lambda item: (item["last_activity_at"], item["updated_at"]), reverse=True)
        return payloads

    def _serialize_target_folder(self, folder: AssistantTargetFolder) -> dict[str, Any]:
        payload = next((item for item in self._compute_target_folder_payloads() if item["id"] == folder.id), None)
        if payload is None:
            raise ApiException(status_code=404, code=40435, message=f"Target folder not found: {folder.id}")
        return AssistantTargetFolderResponse.model_validate(payload).model_dump()

    def _ensure_system_targets_folder(self) -> None:
        self._acquire_target_folder_mutation_lock()
        pending_workflows = self.db.query(AssistantWorkflow.id).filter(
            AssistantWorkflow.is_system.is_(True),
            AssistantWorkflow.folder_id.is_(None),
        ).first()
        pending_agents = self.db.query(AssistantAgentProfile.id).filter(
            AssistantAgentProfile.is_system.is_(True),
            AssistantAgentProfile.folder_id.is_(None),
        ).first()
        if pending_workflows is None and pending_agents is None:
            return

        folder = (
            self.db.query(AssistantTargetFolder)
            .filter(
                AssistantTargetFolder.parent_id.is_(None),
                func.lower(AssistantTargetFolder.name) == "系统内置".lower(),
            )
            .first()
        )
        if folder is None:
            folder = AssistantTargetFolder(
                name="系统内置",
                description="",
                parent_id=None,
                color_token="amber",
                icon_key="folder",
            )
            self.db.add(folder)
            self.db.flush()

        self.db.query(AssistantWorkflow).filter(
            AssistantWorkflow.is_system.is_(True),
            AssistantWorkflow.folder_id.is_(None),
        ).update({AssistantWorkflow.folder_id: folder.id}, synchronize_session=False)
        self.db.query(AssistantAgentProfile).filter(
            AssistantAgentProfile.is_system.is_(True),
            AssistantAgentProfile.folder_id.is_(None),
        ).update({AssistantAgentProfile.folder_id: folder.id}, synchronize_session=False)

    @staticmethod
    def _workflow_input_to_snapshot(workflow: WorkflowInput) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "label": n.label,
                    "position_x": n.position_x,
                    "position_y": n.position_y,
                    "config": n.config,
                }
                for n in workflow.nodes
            ],
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "source_node_id": e.source_node_id,
                    "target_node_id": e.target_node_id,
                    "source_handle": e.source_handle,
                    "target_handle": e.target_handle,
                    "condition_type": e.condition_type,
                    "condition_expr": e.condition_expr.model_dump() if e.condition_expr else None,
                    "label": e.label,
                }
                for e in workflow.edges
            ],
            "viewport": workflow.viewport,
        }

    @staticmethod
    def _workflow_input_from_snapshot(snapshot: dict | None) -> WorkflowInput:
        payload = snapshot if isinstance(snapshot, dict) else {}
        return WorkflowInput.model_validate(
            {
                "nodes": payload.get("nodes") or [],
                "edges": payload.get("edges") or [],
                "viewport": payload.get("viewport"),
            }
        )

    @staticmethod
    def _resolve_start_input_mode(workflow: WorkflowInput) -> str:
        for node in workflow.nodes:
            if node.node_type != "start":
                continue
            cfg = node.config if isinstance(node.config, dict) else {}
            raw_mode = str(cfg.get("input_mode", cfg.get("inputMode", "text")) or "text").strip().lower()
            return "structured" if raw_mode == "structured" else "text"
        return "text"

    def _is_workflow_structured_input(self, workflow: AssistantWorkflow) -> bool:
        draft = self._get_workflow_draft_input(workflow)
        return self._resolve_start_input_mode(draft) == "structured"

    def _enforce_workflow_structured_input_constraints(
        self,
        *,
        workflow: AssistantWorkflow,
        workflow_input: WorkflowInput,
        raise_error: bool = True,
    ) -> list[str]:
        # Plan 10 B2: skill-reference constraint removed with assistant_skill.
        _ = (workflow, raise_error)
        if self._resolve_start_input_mode(workflow_input) != "structured":
            return []
        return []

    def collect_workflow_extra_validation_errors(
        self,
        *,
        workflow: AssistantWorkflow | None,
        workflow_input: WorkflowInput,
    ) -> list[str]:
        errors: list[str] = []
        if workflow is not None:
            errors.extend(
                self._enforce_workflow_structured_input_constraints(
                    workflow=workflow,
                    workflow_input=workflow_input,
                    raise_error=False,
                )
            )
        return errors

    @staticmethod
    def _workflow_response_nodes_from_input(
        *,
        workflow_id: UUID,
        workflow: WorkflowInput,
        ts: datetime,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"{workflow_id}:node:{n.node_id}"),
                "node_id": n.node_id,
                "node_type": n.node_type,
                "label": n.label,
                "position_x": n.position_x,
                "position_y": n.position_y,
                "config": n.config,
                "created_at": ts,
                "updated_at": ts,
            }
            for n in workflow.nodes
        ]

    @staticmethod
    def _workflow_response_edges_from_input(
        *,
        workflow_id: UUID,
        workflow: WorkflowInput,
        ts: datetime,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"{workflow_id}:edge:{e.edge_id}"),
                "edge_id": e.edge_id,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "source_handle": e.source_handle,
                "target_handle": e.target_handle,
                "condition_type": e.condition_type,
                "condition_expr": e.condition_expr.model_dump() if e.condition_expr else None,
                "label": e.label,
                "created_at": ts,
                "updated_at": ts,
            }
            for e in workflow.edges
        ]

    def _get_workflow_draft_input(self, workflow: AssistantWorkflow) -> WorkflowInput:
        if workflow.draft_version_id:
            draft = (
                self.db.query(AssistantWorkflowVersion)
                .filter(
                    AssistantWorkflowVersion.id == workflow.draft_version_id,
                    AssistantWorkflowVersion.workflow_id == workflow.id,
                )
                .first()
            )
            if draft and isinstance(draft.snapshot, dict):
                try:
                    return self._workflow_input_from_snapshot(draft.snapshot)
                except Exception:
                    pass
        if workflow.published_version_id:
            published = (
                self.db.query(AssistantWorkflowVersion)
                .filter(
                    AssistantWorkflowVersion.id == workflow.published_version_id,
                    AssistantWorkflowVersion.workflow_id == workflow.id,
                )
                .first()
            )
            if published and isinstance(published.snapshot, dict):
                return self._workflow_input_from_snapshot(published.snapshot)
        return self._to_workflow_input_from_entity(workflow)

    @staticmethod
    def _agent_snapshot_from_fields(
        *,
        system_prompt: str,
        tools: list[str] | None,
        kb_config: dict | None,
        model_source: str,
        model_id: UUID | None,
    ) -> dict[str, Any]:
        snapshot_kb = dict(kb_config or {})
        snapshot_kb["model_source"] = model_source
        snapshot_kb["model_id"] = str(model_id) if model_id is not None else None
        snapshot_kb.pop("modelSource", None)
        snapshot_kb.pop("modelId", None)
        return {
            "system_prompt": system_prompt,
            "tools": [str(item) for item in (tools or []) if str(item).strip()],
            "kb_config": snapshot_kb,
            "model_source": model_source,
            "model_id": str(model_id) if model_id is not None else None,
        }

    def _agent_draft_from_snapshot(self, snapshot: dict | None) -> AgentPublishDraftInput:
        payload = snapshot if isinstance(snapshot, dict) else {}
        kb_config = payload.get("kb_config") if isinstance(payload.get("kb_config"), dict) else {"enabled": False}
        model_source = str(payload.get("model_source", kb_config.get("model_source", "default"))).strip().lower()
        if model_source not in {"default", "custom"}:
            model_source = "default"

        raw_model_id = payload.get("model_id", kb_config.get("model_id"))
        parsed_model_id: UUID | None = None
        if raw_model_id is not None:
            text = str(raw_model_id).strip()
            if text:
                try:
                    parsed_model_id = UUID(text)
                except Exception:
                    parsed_model_id = None

        if model_source == "default":
            parsed_model_id = None

        return AgentPublishDraftInput.model_validate(
            {
                "system_prompt": str(payload.get("system_prompt") or ""),
                "tools": payload.get("tools") or [],
                "kb_config": kb_config,
                "model_source": model_source,
                "model_id": parsed_model_id,
            }
        )

    def _agent_snapshot_from_draft(self, draft: AgentPublishDraftInput) -> dict[str, Any]:
        normalized_kb = self._normalize_agent_kb_config(
            kb_config=draft.kb_config,
            model_source=draft.model_source,
            model_id=draft.model_id,
            existing_kb_config=draft.kb_config if isinstance(draft.kb_config, dict) else {"enabled": False},
        )
        return self._agent_snapshot_from_fields(
            system_prompt=draft.system_prompt,
            tools=draft.tools,
            kb_config=normalized_kb,
            model_source=draft.model_source,
            model_id=draft.model_id,
        )

    def _get_agent_profile_draft(self, agent_profile: AssistantAgentProfile) -> AgentPublishDraftInput:
        if agent_profile.draft_version_id:
            draft = (
                self.db.query(AssistantAgentProfileVersion)
                .filter(
                    AssistantAgentProfileVersion.id == agent_profile.draft_version_id,
                    AssistantAgentProfileVersion.agent_profile_id == agent_profile.id,
                )
                .first()
            )
            if draft and isinstance(draft.snapshot, dict):
                try:
                    return self._agent_draft_from_snapshot(draft.snapshot)
                except Exception:
                    pass

        raw_kb = agent_profile.kb_config if isinstance(agent_profile.kb_config, dict) else {"enabled": False}
        model_source, model_id = self._read_agent_model_config(raw_kb)
        return AgentPublishDraftInput.model_validate(
            {
                "system_prompt": agent_profile.system_prompt or "",
                "tools": agent_profile.tools or [],
                "kb_config": raw_kb,
                "model_source": model_source,
                "model_id": model_id,
            }
        )

    def _next_workflow_version_seq(self, workflow_id: UUID) -> int:
        max_seq = (
            self.db.query(func.max(AssistantWorkflowVersion.sequence_no))
            .filter(AssistantWorkflowVersion.workflow_id == workflow_id)
            .scalar()
        )
        return int(max_seq or 0) + 1

    def _next_agent_version_seq(self, agent_profile_id: UUID) -> int:
        max_seq = (
            self.db.query(func.max(AssistantAgentProfileVersion.sequence_no))
            .filter(AssistantAgentProfileVersion.agent_profile_id == agent_profile_id)
            .scalar()
        )
        return int(max_seq or 0) + 1

    def _create_workflow_version(
        self,
        *,
        workflow: AssistantWorkflow,
        workflow_input: WorkflowInput,
        version_source: str,
        version_name: str | None = None,
    ) -> AssistantWorkflowVersion:
        version = AssistantWorkflowVersion(
            workflow_id=workflow.id,
            sequence_no=self._next_workflow_version_seq(workflow.id),
            version_name=self._default_version_name(version_name),
            version_source=version_source,
            snapshot=self._workflow_input_to_snapshot(workflow_input),
        )
        self.db.add(version)
        self.db.flush()
        return version

    def _create_agent_profile_version(
        self,
        *,
        agent_profile: AssistantAgentProfile,
        draft: AgentPublishDraftInput,
        version_source: str,
        version_name: str | None = None,
    ) -> AssistantAgentProfileVersion:
        normalized_kb = self._normalize_agent_kb_config(
            kb_config=draft.kb_config,
            model_source=draft.model_source,
            model_id=draft.model_id,
            existing_kb_config=draft.kb_config if isinstance(draft.kb_config, dict) else {"enabled": False},
        )
        snapshot = self._agent_snapshot_from_fields(
            system_prompt=draft.system_prompt,
            tools=draft.tools,
            kb_config=normalized_kb,
            model_source=draft.model_source,
            model_id=draft.model_id,
        )
        version = AssistantAgentProfileVersion(
            agent_profile_id=agent_profile.id,
            sequence_no=self._next_agent_version_seq(agent_profile.id),
            version_name=self._default_version_name(version_name),
            version_source=version_source,
            snapshot=snapshot,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def _resolve_system_workflow_baseline_input(self, workflow: AssistantWorkflow) -> WorkflowInput | None:
        if not workflow.is_system:
            return None

        locale = self._current_locale()
        for linked_skill in []:  # assistant_skill dropped
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            name = str(getattr(linked_skill, "name", "") or "").strip()
            if not name:
                continue
            asset = get_system_skill_asset(name, locale=locale)
            if asset is not None and asset.kind == "workflow":
                return load_system_workflow_asset(asset.asset_key, locale=locale)

        workflow_name = str(workflow.name or "").strip()
        if workflow_name:
            asset = get_system_asset_by_canonical_name(
                workflow_name,
                kind="workflow",
                locale=locale,
            )
            if asset is not None:
                return load_system_workflow_asset(asset.asset_key, locale=locale)
        return None

    def _resolve_system_agent_baseline_draft(self, agent_profile: AssistantAgentProfile) -> AgentPublishDraftInput | None:
        if not agent_profile.is_system:
            return None

        locale = self._current_locale()
        for linked_skill in []:  # assistant_skill dropped
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            name = str(getattr(linked_skill, "name", "") or "").strip()
            if not name:
                continue
            asset = get_system_skill_asset(name, locale=locale)
            if asset is not None and asset.kind == "agent":
                return load_system_agent_asset(asset.asset_key, locale=locale)
        raw_name = str(agent_profile.name or "").strip()
        if raw_name:
            asset = get_system_asset_by_canonical_name(
                raw_name,
                kind="agent",
                locale=locale,
            )
            if asset is not None:
                return load_system_agent_asset(asset.asset_key, locale=locale)
        return None

    def _get_workflow_system_baseline_version_id(self, workflow_id: UUID) -> UUID | None:
        baseline = (
            self.db.query(AssistantWorkflowVersion.id)
            .filter(
                AssistantWorkflowVersion.workflow_id == workflow_id,
                AssistantWorkflowVersion.version_source == "publish",
            )
            .order_by(AssistantWorkflowVersion.sequence_no.asc())
            .first()
        )
        if baseline and baseline[0] is not None:
            return baseline[0]
        return None

    def _workflow_version_count(self, workflow_id: UUID) -> int:
        return int(
            self.db.query(func.count(AssistantWorkflowVersion.id))
            .filter(AssistantWorkflowVersion.workflow_id == workflow_id)
            .scalar()
            or 0
        )

    def _agent_version_count(self, agent_profile_id: UUID) -> int:
        return int(
            self.db.query(func.count(AssistantAgentProfileVersion.id))
            .filter(AssistantAgentProfileVersion.agent_profile_id == agent_profile_id)
            .scalar()
            or 0
        )

    @staticmethod
    def _raise_system_workflow_readonly() -> None:
        raise ApiException(
            status_code=400,
            code=40034,
            message="System workflow is read-only. Copy it to a custom workflow before editing or managing versions.",
        )

    @staticmethod
    def _raise_system_agent_readonly() -> None:
        raise ApiException(
            status_code=400,
            code=40035,
            message="System agent profile is read-only. Copy it to a custom agent before editing or managing versions.",
        )

    def _ensure_system_workflow_baseline_state(
        self,
        *,
        workflow: AssistantWorkflow,
        workflow_input: WorkflowInput,
        description: str,
        enabled: bool,
        version_name: str | None = None,
    ) -> bool:
        changed = False
        normalized_description = description or ""
        resolved_workflow_input = self._resolve_system_workflow_asset_references(workflow_input=workflow_input)

        if not bool(workflow.is_system):
            workflow.is_system = True
            changed = True
        if bool(workflow.enabled) != bool(enabled):
            workflow.enabled = bool(enabled)
            changed = True
        if (workflow.description or "") != normalized_description:
            workflow.description = normalized_description
            changed = True
        desired_viewport = resolved_workflow_input.viewport
        if workflow.workflow_viewport != desired_viewport:
            workflow.workflow_viewport = desired_viewport
            changed = True

        current_published = self._get_workflow_published_input(workflow)
        current_draft = self._get_workflow_draft_input(workflow) if workflow.draft_version_id is not None else None
        desired_snapshot = self._workflow_input_to_snapshot(resolved_workflow_input)
        current_snapshot = self._workflow_input_to_snapshot(current_published) if current_published is not None else None

        if current_snapshot != desired_snapshot or workflow.published_version_id is None:
            self._enforce_workflow_structured_input_constraints(
                workflow=workflow,
                workflow_input=resolved_workflow_input,
                raise_error=True,
            )
            self._apply_workflow_to_workflow_entity(workflow, resolved_workflow_input, persist=True)
            published = self._create_workflow_version(
                workflow=workflow,
                workflow_input=resolved_workflow_input,
                version_source="publish",
                version_name=version_name,
            )
            self._keep_only_workflow_version(workflow, published.id)
            return True

        current_draft_snapshot = self._workflow_input_to_snapshot(current_draft) if current_draft is not None else None
        if current_draft_snapshot != desired_snapshot and workflow.published_version_id is not None:
            workflow.draft_version_id = workflow.published_version_id
            changed = True

        keep_version_id = workflow.published_version_id
        if keep_version_id is not None and (
            workflow.draft_version_id != keep_version_id
            or self._workflow_version_count(workflow.id) != 1
        ):
            self._keep_only_workflow_version(workflow, keep_version_id)
            changed = True

        return changed

    def _resolve_system_workflow_asset_references(
        self,
        *,
        workflow_input: WorkflowInput,
        locale: str | None = None,
    ) -> WorkflowInput:
        normalized_locale = self._current_locale(locale)
        payload = workflow_input.model_dump(mode="json", by_alias=True)
        nodes = payload.get("nodes")
        if not isinstance(nodes, list):
            return workflow_input

        def _rewrite_nodes(raw_nodes: list[Any]) -> bool:
            changed = False
            for node in raw_nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("nodeType", node.get("node_type", "")) or "").strip()
                cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
                if node_type == "workflow_call":
                    asset_key = str(
                        self._cfg_get(cfg, "target_system_asset_key", "targetSystemAssetKey", default="") or ""
                    ).strip()
                    if asset_key:
                        target_workflow = self.ensure_standalone_system_workflow_asset(
                            asset_key,
                            locale=normalized_locale,
                        )
                        if target_workflow.published_version_id is None:
                            raise ApiException(
                                status_code=500,
                                code=50037,
                                message=(
                                    "Standalone system workflow asset is missing a published version: "
                                    f"{asset_key}"
                                ),
                            )
                        next_cfg = dict(cfg)
                        next_cfg["targetWorkflowId"] = str(target_workflow.id)
                        next_cfg["bindingMode"] = "pinned"
                        next_cfg["targetPublishedVersionId"] = str(target_workflow.published_version_id)
                        next_cfg.pop("targetSystemAssetKey", None)
                        next_cfg.pop("target_system_asset_key", None)
                        node["config"] = next_cfg
                        cfg = next_cfg
                        changed = True

                if node_type not in {"iteration", "loop"}:
                    continue
                body_nodes = self._cfg_get(cfg, "body_nodes", "bodyNodes", default=[])
                if isinstance(body_nodes, list) and _rewrite_nodes(body_nodes):
                    changed = True
            return changed

        if not _rewrite_nodes(nodes):
            return workflow_input
        return WorkflowInput.model_validate(copy.deepcopy(payload))

    def _ensure_system_agent_baseline_state(
        self,
        *,
        agent_profile: AssistantAgentProfile,
        draft: AgentPublishDraftInput,
        description: str,
        enabled: bool,
        version_name: str | None = None,
    ) -> bool:
        changed = False
        normalized_description = description or ""
        normalized_kb = self._normalize_agent_kb_config(
            kb_config=draft.kb_config,
            model_source=draft.model_source,
            model_id=draft.model_id,
            existing_kb_config=draft.kb_config if isinstance(draft.kb_config, dict) else {"enabled": False},
        )
        self._validate_agent_tool_names(draft.tools)

        if not bool(agent_profile.is_system):
            agent_profile.is_system = True
            changed = True
        if bool(agent_profile.enabled) != bool(enabled):
            agent_profile.enabled = bool(enabled)
            changed = True
        if (agent_profile.description or "") != normalized_description:
            agent_profile.description = normalized_description
            changed = True

        current_published = self._get_agent_profile_published_draft(agent_profile)
        desired_snapshot = self._agent_snapshot_from_fields(
            system_prompt=draft.system_prompt,
            tools=draft.tools,
            kb_config=normalized_kb,
            model_source=draft.model_source,
            model_id=draft.model_id,
        )
        current_snapshot = self._agent_snapshot_from_draft(current_published) if current_published is not None else None
        current_model_source, current_model_id = self._read_agent_model_config(
            agent_profile.kb_config if isinstance(agent_profile.kb_config, dict) else {"enabled": False}
        )
        current_entity_snapshot = self._agent_snapshot_from_fields(
            system_prompt=agent_profile.system_prompt or "",
            tools=list(agent_profile.tools or []),
            kb_config=agent_profile.kb_config if isinstance(agent_profile.kb_config, dict) else {"enabled": False},
            model_source=current_model_source,
            model_id=current_model_id,
        )

        if current_snapshot != desired_snapshot or agent_profile.published_version_id is None:
            agent_profile.system_prompt = draft.system_prompt
            agent_profile.tools = list(draft.tools or [])
            agent_profile.kb_config = normalized_kb
            published = self._create_agent_profile_version(
                agent_profile=agent_profile,
                draft=AgentPublishDraftInput.model_validate(
                    {
                        "system_prompt": agent_profile.system_prompt,
                        "tools": agent_profile.tools or [],
                        "kb_config": agent_profile.kb_config,
                        "model_source": draft.model_source,
                        "model_id": draft.model_id,
                    }
                ),
                version_source="publish",
                version_name=version_name,
            )
            self._keep_only_agent_version(agent_profile, published.id)
            changed = True
        elif current_entity_snapshot != desired_snapshot:
            agent_profile.system_prompt = draft.system_prompt
            agent_profile.tools = list(draft.tools or [])
            agent_profile.kb_config = normalized_kb
            changed = True
        elif agent_profile.draft_version_id != agent_profile.published_version_id or self._agent_version_count(agent_profile.id) != 1:
            if agent_profile.published_version_id is not None:
                self._keep_only_agent_version(agent_profile, agent_profile.published_version_id)
                changed = True

        if changed:
            for linked_skill in []:  # assistant_skill dropped
                linked_skill.system_prompt = agent_profile.system_prompt
                linked_skill.kb_config = agent_profile.kb_config
                linked_skill.tools = list(agent_profile.tools or [])

        return changed

    def _get_agent_system_baseline_version_id(self, agent_profile_id: UUID) -> UUID | None:
        baseline = (
            self.db.query(AssistantAgentProfileVersion.id)
            .filter(
                AssistantAgentProfileVersion.agent_profile_id == agent_profile_id,
                AssistantAgentProfileVersion.version_source == "publish",
            )
            .order_by(AssistantAgentProfileVersion.sequence_no.asc())
            .first()
        )
        if baseline and baseline[0] is not None:
            return baseline[0]
        return None

    def _get_workflow_protected_version_ids(self, workflow: AssistantWorkflow) -> set[UUID]:
        protected_ids: set[UUID] = set()
        if workflow.draft_version_id is not None:
            protected_ids.add(workflow.draft_version_id)
        if workflow.published_version_id is not None:
            protected_ids.add(workflow.published_version_id)
        for ref in self._workflow_call_referrers_for_workflow(workflow.id):
            if ref.binding_mode != "pinned" or ref.target_published_version_id is None:
                continue
            protected_ids.add(ref.target_published_version_id)
        if workflow.is_system:
            baseline_id = self._get_workflow_system_baseline_version_id(workflow.id)
            if baseline_id is not None:
                protected_ids.add(baseline_id)
        # Skill binding/dependency FKs protect exact published history.
        for row in (
            self.db.query(AssistantSkillCapabilityBinding.resolved_workflow_version_id)
            .filter(
                AssistantSkillCapabilityBinding.resolved_workflow_version_id.isnot(None),
            )
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantWorkflowVersion.workflow_id)
                .filter(AssistantWorkflowVersion.id == version_id)
                .scalar()
            )
            if owner == workflow.id:
                protected_ids.add(version_id)
        for row in (
            self.db.query(AssistantSkillCapabilityDependency.resolved_workflow_version_id)
            .filter(
                AssistantSkillCapabilityDependency.resolved_workflow_version_id.isnot(None),
            )
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantWorkflowVersion.workflow_id)
                .filter(AssistantWorkflowVersion.id == version_id)
                .scalar()
            )
            if owner == workflow.id:
                protected_ids.add(version_id)
        return protected_ids

    def _get_agent_protected_version_ids(self, agent_profile: AssistantAgentProfile) -> set[UUID]:
        protected_ids: set[UUID] = set()
        if agent_profile.draft_version_id is not None:
            protected_ids.add(agent_profile.draft_version_id)
        if agent_profile.published_version_id is not None:
            protected_ids.add(agent_profile.published_version_id)
        if agent_profile.is_system:
            baseline_id = self._get_agent_system_baseline_version_id(agent_profile.id)
            if baseline_id is not None:
                protected_ids.add(baseline_id)
        for row in (
            self.db.query(AssistantSkillCapabilityBinding.resolved_agent_version_id)
            .filter(AssistantSkillCapabilityBinding.resolved_agent_version_id.isnot(None))
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantAgentProfileVersion.agent_profile_id)
                .filter(AssistantAgentProfileVersion.id == version_id)
                .scalar()
            )
            if owner == agent_profile.id:
                protected_ids.add(version_id)
        for row in (
            self.db.query(AssistantSkillCapabilityDependency.resolved_agent_version_id)
            .filter(AssistantSkillCapabilityDependency.resolved_agent_version_id.isnot(None))
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantAgentProfileVersion.agent_profile_id)
                .filter(AssistantAgentProfileVersion.id == version_id)
                .scalar()
            )
            if owner == agent_profile.id:
                protected_ids.add(version_id)
        return protected_ids

    def _trim_workflow_versions(self, workflow: AssistantWorkflow) -> None:
        protected_ids = self._get_workflow_protected_version_ids(workflow)
        versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .order_by(AssistantWorkflowVersion.sequence_no.desc())
            .all()
        )
        keep_ids: set[UUID] = set()
        for item in versions[:_TARGET_VERSION_LIMIT]:
            keep_ids.add(item.id)
        keep_ids.update(protected_ids)
        for item in versions[_TARGET_VERSION_LIMIT:]:
            if item.id in keep_ids:
                continue
            self.db.delete(item)

    def _trim_agent_versions(self, agent_profile: AssistantAgentProfile) -> None:
        protected_ids = self._get_agent_protected_version_ids(agent_profile)
        versions = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(AssistantAgentProfileVersion.agent_profile_id == agent_profile.id)
            .order_by(AssistantAgentProfileVersion.sequence_no.desc())
            .all()
        )
        keep_ids: set[UUID] = set()
        for item in versions[:_TARGET_VERSION_LIMIT]:
            keep_ids.add(item.id)
        keep_ids.update(protected_ids)
        for item in versions[_TARGET_VERSION_LIMIT:]:
            if item.id in keep_ids:
                continue
            self.db.delete(item)

    def _serialize_workflow_summary(self, workflow: AssistantWorkflow) -> dict[str, Any]:
        referenced_skill_ids: list = []  # assistant_skill dropped
        referenced_system_behavior_keys = self._binding_keys_from_relationship(
            getattr(workflow, "system_behavior_bindings", None)
        )
        openclaw_reference_count = self._workflow_openclaw_reference_count(workflow)
        return {
            "id": workflow.id,
            "name": self._display_workflow_name(workflow),
            "description": workflow.description or "",
            "details_loaded": False,
            "is_system": bool(workflow.is_system),
            "hidden": self._is_hidden_system_asset("workflow", workflow.name, workflow.is_system),
            "enabled": bool(workflow.enabled),
            "workflow_version": workflow.workflow_version or 1,
            "workflow_viewport": None,
            "nodes": [],
            "edges": [],
            "draft_version_id": workflow.draft_version_id,
            "published_version_id": workflow.published_version_id,
            "referenced_skill_ids": referenced_skill_ids,
            "reference_count": len(referenced_skill_ids),
            "referenced_system_behavior_keys": referenced_system_behavior_keys,
            "system_behavior_reference_count": len(referenced_system_behavior_keys),
            "openclaw_reference_count": openclaw_reference_count,
            "folder_id": workflow.folder_id,
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
        }

    def _serialize_workflow(self, workflow: AssistantWorkflow) -> dict[str, Any]:
        referenced_skill_ids: list = []  # assistant_skill dropped
        referenced_system_behavior_keys = self._binding_keys_from_relationship(
            getattr(workflow, "system_behavior_bindings", None)
        )
        openclaw_reference_count = self._workflow_openclaw_reference_count(workflow)
        draft_workflow = self._get_workflow_draft_input(workflow)
        ts = workflow.updated_at or workflow.created_at or self._utcnow()
        return {
            "id": workflow.id,
            "name": self._display_workflow_name(workflow),
            "description": workflow.description or "",
            "details_loaded": True,
            "is_system": bool(workflow.is_system),
            "hidden": self._is_hidden_system_asset("workflow", workflow.name, workflow.is_system),
            "enabled": bool(workflow.enabled),
            "workflow_version": workflow.workflow_version or 1,
            "workflow_viewport": draft_workflow.viewport,
            "nodes": self._workflow_response_nodes_from_input(
                workflow_id=workflow.id,
                workflow=draft_workflow,
                ts=ts,
            ),
            "edges": self._workflow_response_edges_from_input(
                workflow_id=workflow.id,
                workflow=draft_workflow,
                ts=ts,
            ),
            "draft_version_id": workflow.draft_version_id,
            "published_version_id": workflow.published_version_id,
            "referenced_skill_ids": referenced_skill_ids,
            "reference_count": len(referenced_skill_ids),
            "referenced_system_behavior_keys": referenced_system_behavior_keys,
            "system_behavior_reference_count": len(referenced_system_behavior_keys),
            "openclaw_reference_count": openclaw_reference_count,
            "folder_id": workflow.folder_id,
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
        }

    def serialize_workflow(self, workflow: AssistantWorkflow) -> dict[str, Any]:
        return self._serialize_workflow(workflow)

    def serialize_workflow_summary(self, workflow: AssistantWorkflow) -> dict[str, Any]:
        return self._serialize_workflow_summary(workflow)

    def display_workflow_name(self, workflow: AssistantWorkflow, *, locale: str | None = None) -> str:
        return self._display_workflow_name(workflow, locale=locale)

    def _serialize_agent_profile_summary(self, agent_profile: AssistantAgentProfile) -> dict[str, Any]:
        referenced_skill_ids: list = []  # assistant_skill dropped
        referenced_system_behavior_keys = self._binding_keys_from_relationship(
            getattr(agent_profile, "system_behavior_bindings", None)
        )
        openclaw_reference_count = self._agent_openclaw_reference_count(agent_profile)
        raw_kb = agent_profile.kb_config if isinstance(agent_profile.kb_config, dict) else {"enabled": False}
        model_source, model_id = self._read_agent_model_config(raw_kb)
        return {
            "id": agent_profile.id,
            "name": self._display_agent_profile_name(agent_profile),
            "description": agent_profile.description or "",
            "details_loaded": False,
            "system_prompt": None,
            "tools": None,
            "kb_config": None,
            "model_source": model_source,
            "model_id": model_id,
            "is_system": bool(agent_profile.is_system),
            "hidden": self._is_hidden_system_asset("agent", agent_profile.name, agent_profile.is_system),
            "enabled": bool(agent_profile.enabled),
            "draft_version_id": agent_profile.draft_version_id,
            "published_version_id": agent_profile.published_version_id,
            "referenced_skill_ids": referenced_skill_ids,
            "reference_count": len(referenced_skill_ids),
            "referenced_system_behavior_keys": referenced_system_behavior_keys,
            "system_behavior_reference_count": len(referenced_system_behavior_keys),
            "openclaw_reference_count": openclaw_reference_count,
            "folder_id": agent_profile.folder_id,
            "created_at": agent_profile.created_at,
            "updated_at": agent_profile.updated_at,
        }

    def _serialize_agent_profile(self, agent_profile: AssistantAgentProfile) -> dict[str, Any]:
        referenced_skill_ids: list = []  # assistant_skill dropped
        referenced_system_behavior_keys = self._binding_keys_from_relationship(
            getattr(agent_profile, "system_behavior_bindings", None)
        )
        openclaw_reference_count = self._agent_openclaw_reference_count(agent_profile)
        draft = self._get_agent_profile_draft(agent_profile)
        normalized_kb = dict(draft.kb_config or {})
        normalized_kb["model_source"] = draft.model_source
        normalized_kb["model_id"] = str(draft.model_id) if draft.model_id is not None else None
        model_source, model_id = self._read_agent_model_config(normalized_kb)
        return {
            "id": agent_profile.id,
            "name": self._display_agent_profile_name(agent_profile),
            "description": agent_profile.description or "",
            "details_loaded": True,
            "system_prompt": draft.system_prompt,
            "tools": draft.tools or [],
            "kb_config": normalized_kb,
            "model_source": model_source,
            "model_id": model_id,
            "is_system": bool(agent_profile.is_system),
            "hidden": self._is_hidden_system_asset("agent", agent_profile.name, agent_profile.is_system),
            "enabled": bool(agent_profile.enabled),
            "draft_version_id": agent_profile.draft_version_id,
            "published_version_id": agent_profile.published_version_id,
            "referenced_skill_ids": referenced_skill_ids,
            "reference_count": len(referenced_skill_ids),
            "referenced_system_behavior_keys": referenced_system_behavior_keys,
            "system_behavior_reference_count": len(referenced_system_behavior_keys),
            "openclaw_reference_count": openclaw_reference_count,
            "folder_id": agent_profile.folder_id,
            "created_at": agent_profile.created_at,
            "updated_at": agent_profile.updated_at,
        }

    def serialize_agent_profile(self, agent_profile: AssistantAgentProfile) -> dict[str, Any]:
        return self._serialize_agent_profile(agent_profile)

    def serialize_agent_profile_summary(self, agent_profile: AssistantAgentProfile) -> dict[str, Any]:
        return self._serialize_agent_profile_summary(agent_profile)

    def display_agent_profile_name(
        self,
        agent_profile: AssistantAgentProfile,
        *,
        locale: str | None = None,
    ) -> str:
        return self._display_agent_profile_name(agent_profile, locale=locale)

    def _is_hidden_system_asset(self, kind: TargetType, canonical_name: str | None, is_system: bool) -> bool:
        if not is_system:
            return False
        name = str(canonical_name or "").strip()
        if not name:
            return False
        asset = get_system_asset_by_canonical_name(
            name,
            kind=kind,
            locale=self._current_locale(),
        )
        return bool(asset.hidden) if asset is not None else False

    @staticmethod
    def _serialize_system_behavior_contract_field(
        field: SystemBehaviorFieldDefinition,
    ) -> dict[str, Any]:
        return {
            "name": field.name,
            "type": field.type,
            "required": bool(field.required),
            "description": field.description or "",
            "items_type": field.items_type,
        }

    @staticmethod
    def _binding_keys_from_relationship(bindings: list[AssistantSystemBehaviorBinding] | None) -> list[str]:
        return sorted(
            {
                str(item.behavior_key or "").strip()
                for item in (bindings or [])
                if str(getattr(item, "behavior_key", "") or "").strip()
            }
        )

    def _load_openclaw_reference_counts(
        self,
        *,
        workflow_ids: list[UUID] | tuple[UUID, ...] = (),
        agent_profile_ids: list[UUID] | tuple[UUID, ...] = (),
    ) -> tuple[dict[UUID, int], dict[UUID, int]]:
        from app.openclaw_integration.models import OpenClawCapabilityItem

        workflow_counts: dict[UUID, int] = {}
        normalized_workflow_ids = tuple(dict.fromkeys(workflow_ids))
        if normalized_workflow_ids:
            workflow_rows = (
                self.db.query(
                    OpenClawCapabilityItem.workflow_id,
                    func.count(OpenClawCapabilityItem.id),
                )
                .filter(OpenClawCapabilityItem.workflow_id.in_(normalized_workflow_ids))
                .group_by(OpenClawCapabilityItem.workflow_id)
                .all()
            )
            workflow_counts = {
                workflow_id: int(count)
                for workflow_id, count in workflow_rows
                if workflow_id is not None
            }

        agent_counts: dict[UUID, int] = {}
        normalized_agent_ids = tuple(dict.fromkeys(agent_profile_ids))
        if normalized_agent_ids:
            agent_rows = (
                self.db.query(
                    OpenClawCapabilityItem.agent_profile_id,
                    func.count(OpenClawCapabilityItem.id),
                )
                .filter(OpenClawCapabilityItem.agent_profile_id.in_(normalized_agent_ids))
                .group_by(OpenClawCapabilityItem.agent_profile_id)
                .all()
            )
            agent_counts = {
                agent_profile_id: int(count)
                for agent_profile_id, count in agent_rows
                if agent_profile_id is not None
            }

        return workflow_counts, agent_counts

    def _attach_openclaw_reference_counts(
        self,
        *,
        workflows: list[AssistantWorkflow] | tuple[AssistantWorkflow, ...] = (),
        agent_profiles: list[AssistantAgentProfile] | tuple[AssistantAgentProfile, ...] = (),
    ) -> None:
        workflow_list = [item for item in workflows if getattr(item, "id", None) is not None]
        agent_list = [item for item in agent_profiles if getattr(item, "id", None) is not None]
        if not workflow_list and not agent_list:
            return

        workflow_counts, agent_counts = self._load_openclaw_reference_counts(
            workflow_ids=[item.id for item in workflow_list],
            agent_profile_ids=[item.id for item in agent_list],
        )
        for workflow in workflow_list:
            setattr(workflow, "_openclaw_reference_count", workflow_counts.get(workflow.id, 0))
        for agent_profile in agent_list:
            setattr(agent_profile, "_openclaw_reference_count", agent_counts.get(agent_profile.id, 0))

    def _workflow_openclaw_reference_count(self, workflow: AssistantWorkflow) -> int:
        cached = getattr(workflow, "_openclaw_reference_count", None)
        if isinstance(cached, int):
            return cached
        workflow_counts, _ = self._load_openclaw_reference_counts(workflow_ids=[workflow.id])
        count = workflow_counts.get(workflow.id, 0)
        setattr(workflow, "_openclaw_reference_count", count)
        return count

    def _agent_openclaw_reference_count(self, agent_profile: AssistantAgentProfile) -> int:
        cached = getattr(agent_profile, "_openclaw_reference_count", None)
        if isinstance(cached, int):
            return cached
        _, agent_counts = self._load_openclaw_reference_counts(agent_profile_ids=[agent_profile.id])
        count = agent_counts.get(agent_profile.id, 0)
        setattr(agent_profile, "_openclaw_reference_count", count)
        return count

    def _display_workflow_name(self, workflow: AssistantWorkflow, *, locale: str | None = None) -> str:
        raw_name = str(workflow.name or "").strip()
        if not raw_name or not bool(workflow.is_system):
            return raw_name
        normalized_locale = self._current_locale(locale)
        asset = get_system_asset_by_canonical_name(
            raw_name,
            kind="workflow",
            locale=normalized_locale,
        )
        if asset is not None:
            return asset.display_name
        for linked_skill in []:  # assistant_skill dropped
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            asset = get_system_skill_asset(
                str(getattr(linked_skill, "name", "") or "").strip(),
                locale=normalized_locale,
            )
            if asset is not None and asset.kind == "workflow":
                return asset.display_name
        return raw_name

    def _display_agent_profile_name(
        self,
        agent_profile: AssistantAgentProfile,
        *,
        locale: str | None = None,
    ) -> str:
        raw_name = str(agent_profile.name or "").strip()
        if not raw_name or not bool(agent_profile.is_system):
            return raw_name
        normalized_locale = self._current_locale(locale)
        asset = get_system_asset_by_canonical_name(
            raw_name,
            kind="agent",
            locale=normalized_locale,
        )
        if asset is not None:
            return asset.display_name
        for linked_skill in []:  # assistant_skill dropped
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            asset = get_system_skill_asset(
                str(getattr(linked_skill, "name", "") or "").strip(),
                locale=normalized_locale,
            )
            if asset is not None and asset.kind == "agent":
                return asset.display_name
        return raw_name

    def _audit_system_target_origins(self) -> dict[str, list[dict[str, Any]]]:
        locale = self._current_locale()
        standalone_workflow_names = {
            definition.canonical_name
            for definition in list_standalone_system_workflow_definitions(locale=locale)
        }
        system_behavior_workflow_names = {
            definition.default_target.canonical_name
            for definition in list_system_behavior_definitions(locale=locale)
            if definition.default_target.target_type == "workflow"
        }
        # System skill targets are now bare workflows/agents named after skill defaults
        # (no assistant_skill row). Classify by the same naming contract as reset helpers.
        system_skill_workflow_names: set[str] = set()
        system_skill_agent_names: set[str] = set()
        for skill_def in SkillRegistry.list_system_skills(locale=locale):
            pattern = getattr(skill_def, "langgraph_pattern", None)
            target_type = self._derive_target_type(langgraph_pattern=pattern)
            if target_type == "workflow":
                system_skill_workflow_names.add(f"{skill_def.name}__workflow")
            else:
                system_skill_agent_names.add(f"{skill_def.name}__agent")

        unexpected_workflows: list[dict[str, Any]] = []
        unexpected_agents: list[dict[str, Any]] = []

        workflows = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.is_system.is_(True))
            .all()
        )
        for workflow in workflows:
            matched_kinds: list[str] = []
            name = str(workflow.name or "").strip()
            if name in system_skill_workflow_names:
                matched_kinds.append("system_skill")
            if name in system_behavior_workflow_names:
                matched_kinds.append("system_behavior")
            if name in standalone_workflow_names:
                matched_kinds.append("standalone_system_target")
            if len(matched_kinds) != 1:
                unexpected_workflows.append(
                    {
                        "id": str(workflow.id),
                        "name": workflow.name,
                        "displayName": self._display_workflow_name(workflow),
                        "matchedKinds": matched_kinds,
                    }
                )

        agents = (
            self.db.query(AssistantAgentProfile)
            .filter(AssistantAgentProfile.is_system.is_(True))
            .all()
        )
        for agent_profile in agents:
            matched_kinds: list[str] = []
            name = str(agent_profile.name or "").strip()
            if name in system_skill_agent_names:
                matched_kinds.append("system_skill")
            if len(matched_kinds) != 1:
                unexpected_agents.append(
                    {
                        "id": str(agent_profile.id),
                        "name": agent_profile.name,
                        "displayName": self._display_agent_profile_name(agent_profile),
                        "matchedKinds": matched_kinds,
                    }
                )

        return {
            "unexpectedWorkflows": unexpected_workflows,
            "unexpectedAgents": unexpected_agents,
        }

    def _serialize_system_behavior_target_summary(
        self,
        *,
        target_type: TargetType,
        workflow: AssistantWorkflow | None = None,
        agent_profile: AssistantAgentProfile | None = None,
        is_canonical_default: bool = False,
    ) -> dict[str, Any]:
        if target_type == "workflow":
            if workflow is None:
                raise ValueError("workflow is required when target_type=workflow")
            return {
                "id": workflow.id,
                "target_type": "workflow",
                "name": self._display_workflow_name(workflow),
                "description": workflow.description or "",
                "enabled": bool(workflow.enabled),
                "is_system": bool(workflow.is_system),
                "is_canonical_default": bool(is_canonical_default),
                "workflow_id": workflow.id,
                "agent_profile_id": None,
                "published_version_id": workflow.published_version_id,
            }

        if agent_profile is None:
            raise ValueError("agent_profile is required when target_type=agent")
        return {
            "id": agent_profile.id,
            "target_type": "agent",
            "name": self._display_agent_profile_name(agent_profile),
            "description": agent_profile.description or "",
            "enabled": bool(agent_profile.enabled),
            "is_system": bool(agent_profile.is_system),
            "is_canonical_default": bool(is_canonical_default),
            "workflow_id": None,
            "agent_profile_id": agent_profile.id,
            "published_version_id": agent_profile.published_version_id,
        }

    @staticmethod
    def _assign_system_behavior_binding_target(
        binding: AssistantSystemBehaviorBinding,
        *,
        target_type: TargetType,
        workflow: AssistantWorkflow | None = None,
        agent_profile: AssistantAgentProfile | None = None,
    ) -> None:
        binding.target_type = target_type
        if target_type == "workflow":
            if workflow is None:
                raise ValueError("workflow target requires workflow entity")
            binding.workflow = workflow
            binding.agent_profile = None
            return

        if agent_profile is None:
            raise ValueError("agent target requires agent profile entity")
        binding.workflow = None
        binding.agent_profile = agent_profile

    @staticmethod
    def _system_behavior_binding_matches_workflow(
        binding: AssistantSystemBehaviorBinding,
        workflow: AssistantWorkflow,
    ) -> bool:
        return str(binding.target_type or "") == "workflow" and binding.workflow_id == workflow.id

    @staticmethod
    def _system_behavior_binding_matches_agent(
        binding: AssistantSystemBehaviorBinding,
        agent_profile: AssistantAgentProfile,
    ) -> bool:
        return str(binding.target_type or "") == "agent" and binding.agent_profile_id == agent_profile.id

    def _get_system_behavior_definition_or_error(self, behavior_key: str) -> SystemBehaviorDefinition:
        definition = get_system_behavior_definition(behavior_key, locale=self._current_locale())
        if definition is None:
            raise ApiException(
                status_code=404,
                code=40436,
                message=f"System AI behavior not found: {behavior_key}",
            )
        return definition

    def _get_system_behavior_binding(self, behavior_key: str) -> AssistantSystemBehaviorBinding | None:
        return (
            self.db.query(AssistantSystemBehaviorBinding)
            .options(
                joinedload(AssistantSystemBehaviorBinding.workflow),
                joinedload(AssistantSystemBehaviorBinding.agent_profile),
            )
            .filter(AssistantSystemBehaviorBinding.behavior_key == behavior_key)
            .first()
        )

    def _get_workflow_published_input(self, workflow: AssistantWorkflow) -> WorkflowInput | None:
        if workflow.published_version_id is None:
            return None
        version = (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.id == workflow.published_version_id,
                AssistantWorkflowVersion.workflow_id == workflow.id,
            )
            .first()
        )
        if version is None or not isinstance(version.snapshot, dict):
            return None
        try:
            return self._workflow_input_from_snapshot(version.snapshot)
        except Exception:
            return None

    def _get_agent_profile_published_draft(self, agent_profile: AssistantAgentProfile) -> AgentPublishDraftInput | None:
        if agent_profile.published_version_id is None:
            return None
        version = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.id == agent_profile.published_version_id,
                AssistantAgentProfileVersion.agent_profile_id == agent_profile.id,
            )
            .first()
        )
        if version is None or not isinstance(version.snapshot, dict):
            return None
        try:
            return self._agent_draft_from_snapshot(version.snapshot)
        except Exception:
            return None

    @staticmethod
    def _cfg_get(cfg: dict[str, Any], *keys: str, default: Any = None) -> Any:
        return pure_cfg_get(cfg, *keys, default=default)

    @staticmethod
    def _parse_uuid_value(value: Any) -> UUID | None:
        return pure_parse_uuid_value(value)

    @staticmethod
    def _iter_workflow_call_node_configs(
        nodes: list[Any],
        *,
        container_node_id: str | None = None,
    ) -> list[tuple[str, str | None, dict[str, Any]]]:
        return pure_iter_workflow_call_node_configs(
            nodes,
            container_node_id=container_node_id,
        )

    def _collect_workflow_call_references_from_input(
        self,
        *,
        workflow_input: WorkflowInput,
        source_workflow_id: UUID,
        source_workflow_name: str,
        source_kind: str,
        source_version_id: UUID | None = None,
        source_version_name: str | None = None,
        source_version_source: str | None = None,
    ) -> list[WorkflowCallReference]:
        refs: list[WorkflowCallReference] = []
        for pure_ref in pure_collect_workflow_call_references(workflow_input.nodes):
            refs.append(
                WorkflowCallReference(
                    source_workflow_id=source_workflow_id,
                    source_workflow_name=source_workflow_name,
                    source_kind=source_kind,
                    source_node_id=pure_ref.source_node_id,
                    source_container_node_id=pure_ref.source_container_node_id,
                    source_version_id=source_version_id,
                    source_version_name=source_version_name,
                    source_version_source=source_version_source,
                    target_workflow_id=pure_ref.target_workflow_id,
                    binding_mode=pure_ref.binding_mode,
                    target_published_version_id=pure_ref.target_published_version_id,
                )
            )
        return refs

    @staticmethod
    def _workflow_input_from_version_snapshot(version: AssistantWorkflowVersion) -> WorkflowInput:
        return AssistantConfigService._workflow_input_from_snapshot(
            version.snapshot if isinstance(version.snapshot, dict) else {}
        )

    @staticmethod
    def _workflow_contract_from_input_checked(
        workflow_input: WorkflowInput,
        *,
        workflow_name: str,
        version_id: UUID | None = None,
    ) -> WorkflowContractSnapshot:
        try:
            return workflow_contract_from_input(workflow_input)
        except WorkflowContractError as exc:
            version_text = f" (version {version_id})" if version_id is not None else ""
            raise ApiException(
                status_code=422,
                code=42254,
                message=f"Workflow target is not callable: {workflow_name}{version_text}; {exc.message}",
                details={"reason": exc.reason},
            ) from exc

    def _resolve_workflow_call_target(
        self,
        *,
        target_workflow_id: UUID,
        binding_mode: str,
        target_published_version_id: UUID | None,
    ) -> ResolvedWorkflowCallTarget:
        workflow = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.id == target_workflow_id)
            .first()
        )
        if workflow is None:
            raise ApiException(
                status_code=422,
                code=42251,
                message=f"Workflow call target not found: {target_workflow_id}",
            )
        workflow_display_name = self._display_workflow_name(workflow)
        if not bool(workflow.enabled):
            raise ApiException(
                status_code=422,
                code=42252,
                message=f"Workflow call target is disabled: {workflow_display_name}",
            )

        resolved_version_id = target_published_version_id
        if binding_mode == "latest":
            resolved_version_id = workflow.published_version_id

        if resolved_version_id is None:
            raise ApiException(
                status_code=422,
                code=42253,
                message=f"Workflow call target has no published version: {workflow_display_name}",
            )

        version = (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.id == resolved_version_id,
                AssistantWorkflowVersion.workflow_id == workflow.id,
                AssistantWorkflowVersion.version_source == "publish",
            )
            .first()
        )
        if version is None:
            raise ApiException(
                status_code=422,
                code=42255,
                message=(
                    f"Workflow call target published version not found: "
                    f"{workflow_display_name} ({resolved_version_id})"
                ),
            )

        try:
            workflow_input = self._workflow_input_from_version_snapshot(version)
        except Exception as exc:
            raise ApiException(
                status_code=422,
                code=42256,
                message=f"Workflow call target has invalid published snapshot: {workflow_display_name}",
            ) from exc

        contract = self._workflow_contract_from_input_checked(
            workflow_input,
            workflow_name=workflow_display_name,
            version_id=version.id,
        )
        return ResolvedWorkflowCallTarget(
            workflow=workflow,
            version=version,
            workflow_input=workflow_input,
            contract=contract,
        )

    @staticmethod
    def _validate_workflow_call_input_bindings(
        *,
        cfg: dict[str, Any],
        ref: WorkflowCallReference,
        contract: WorkflowContractSnapshot,
    ) -> None:
        input_bindings = AssistantConfigService._cfg_get(cfg, "input_bindings", "inputBindings", default={})
        normalized_bindings = input_bindings if isinstance(input_bindings, dict) else {}
        allowed_fields = {field.name for field in contract.input_fields}
        required_fields = {field.name for field in contract.input_fields if field.required}
        provided_fields = {str(key).strip() for key in normalized_bindings.keys() if str(key).strip()}

        missing_required = sorted(required_fields - provided_fields)
        unknown_fields = sorted(field for field in provided_fields if field not in allowed_fields)
        if missing_required or unknown_fields:
            detail_parts: list[str] = []
            if missing_required:
                detail_parts.append(f"missing required input bindings: {', '.join(missing_required)}")
            if unknown_fields:
                detail_parts.append(f"unknown input bindings: {', '.join(unknown_fields)}")
            location = ref.source_node_id if ref.source_container_node_id is None else (
                f"{ref.source_container_node_id}::{ref.source_node_id}"
            )
            raise ApiException(
                status_code=422,
                code=42257,
                message=f"workflow_call node '{location}' has invalid input bindings: {'; '.join(detail_parts)}",
            )

    def _build_workflow_call_adjacency(
        self,
        *,
        current_workflow_id: UUID,
        current_workflow_input: WorkflowInput,
    ) -> tuple[dict[UUID, set[UUID]], dict[UUID, str]]:
        workflows = self._list_workflows_query(include_disabled=True)
        adjacency: dict[UUID, set[UUID]] = {}
        name_map: dict[UUID, str] = {}

        for workflow in workflows:
            workflow_id = workflow.id
            name_map[workflow_id] = self._display_workflow_name(workflow)
            if workflow_id == current_workflow_id:
                workflow_input = current_workflow_input
            else:
                try:
                    workflow_input = self._get_workflow_draft_input(workflow)
                except Exception:
                    adjacency[workflow_id] = set()
                    continue
            refs = self._collect_workflow_call_references_from_input(
                workflow_input=workflow_input,
                source_workflow_id=workflow_id,
                source_workflow_name=name_map[workflow_id],
                source_kind="draft",
            )
            adjacency[workflow_id] = {ref.target_workflow_id for ref in refs}

        if current_workflow_id not in adjacency:
            adjacency[current_workflow_id] = set()
        return adjacency, name_map

    def _detect_workflow_call_cycle(
        self,
        *,
        current_workflow_id: UUID,
        current_workflow_input: WorkflowInput,
    ) -> str | None:
        adjacency, name_map = self._build_workflow_call_adjacency(
            current_workflow_id=current_workflow_id,
            current_workflow_input=current_workflow_input,
        )

        for target_id in adjacency.get(current_workflow_id, set()):
            if target_id == current_workflow_id:
                workflow_name = name_map.get(current_workflow_id, str(current_workflow_id))
                return f"workflow_call recursive dependency is not allowed: {workflow_name} -> {workflow_name}"

        def _dfs(node_id: UUID, path: list[UUID], in_path: set[UUID]) -> list[UUID] | None:
            for target_id in adjacency.get(node_id, set()):
                if target_id == current_workflow_id:
                    return [*path, target_id]
                if target_id in in_path:
                    continue
                next_path = [*path, target_id]
                found = _dfs(target_id, next_path, { *in_path, target_id })
                if found is not None:
                    return found
            return None

        cycle_path = _dfs(current_workflow_id, [current_workflow_id], {current_workflow_id})
        if cycle_path is None:
            return None

        readable = " -> ".join(name_map.get(node_id, str(node_id)) for node_id in cycle_path)
        return f"workflow_call recursive dependency is not allowed: {readable}"

    def _scan_workflow_call_references(
        self,
        *,
        include_versions: bool,
    ) -> list[WorkflowCallReference]:
        refs: list[WorkflowCallReference] = []
        workflows = self._list_workflows_query(include_disabled=True)
        for workflow in workflows:
            workflow_name = self._display_workflow_name(workflow)
            try:
                draft_input = self._get_workflow_draft_input(workflow)
            except Exception:
                draft_input = None
            if draft_input is not None:
                refs.extend(
                    self._collect_workflow_call_references_from_input(
                        workflow_input=draft_input,
                        source_workflow_id=workflow.id,
                        source_workflow_name=workflow_name,
                        source_kind="draft",
                    )
                )
            if not include_versions:
                continue
            versions = (
                self.db.query(AssistantWorkflowVersion)
                .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
                .all()
            )
            for version in versions:
                if not isinstance(version.snapshot, dict):
                    continue
                try:
                    workflow_input = self._workflow_input_from_version_snapshot(version)
                except Exception:
                    continue
                refs.extend(
                    self._collect_workflow_call_references_from_input(
                        workflow_input=workflow_input,
                        source_workflow_id=workflow.id,
                        source_workflow_name=workflow_name,
                        source_kind="version",
                        source_version_id=version.id,
                        source_version_name=version.version_name,
                        source_version_source=version.version_source,
                    )
                )
        return refs

    @staticmethod
    def _serialize_workflow_call_references(refs: list[WorkflowCallReference]) -> list[dict[str, Any]]:
        return [
            {
                "sourceWorkflowId": str(ref.source_workflow_id),
                "sourceWorkflowName": ref.source_workflow_name,
                "sourceKind": ref.source_kind,
                "sourceNodeId": ref.source_node_id,
                "sourceContainerNodeId": ref.source_container_node_id,
                "sourceVersionId": str(ref.source_version_id) if ref.source_version_id is not None else None,
                "sourceVersionName": ref.source_version_name,
                "sourceVersionSource": ref.source_version_source,
                "bindingMode": ref.binding_mode,
                "targetPublishedVersionId": (
                    str(ref.target_published_version_id) if ref.target_published_version_id is not None else None
                ),
            }
            for ref in refs
        ]

    def _workflow_call_referrers_for_workflow(self, workflow_id: UUID) -> list[WorkflowCallReference]:
        return [
            ref
            for ref in self._scan_workflow_call_references(include_versions=True)
            if ref.target_workflow_id == workflow_id
        ]

    def _workflow_call_referrers_for_workflow_version(self, version_id: UUID) -> list[WorkflowCallReference]:
        return [
            ref
            for ref in self._scan_workflow_call_references(include_versions=True)
            if ref.binding_mode == "pinned" and ref.target_published_version_id == version_id
        ]

    def list_callable_workflows(self) -> list[dict[str, Any]]:
        workflows = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.enabled.is_(True))
            .order_by(AssistantWorkflow.created_at.desc())
            .all()
        )
        items: list[dict[str, Any]] = []
        for workflow in workflows:
            if workflow.published_version_id is None:
                continue
            published_version = (
                self.db.query(AssistantWorkflowVersion)
                .filter(
                    AssistantWorkflowVersion.id == workflow.published_version_id,
                    AssistantWorkflowVersion.workflow_id == workflow.id,
                    AssistantWorkflowVersion.version_source == "publish",
                )
                .first()
            )
            if published_version is None:
                continue
            try:
                published_input = self._workflow_input_from_version_snapshot(published_version)
                published_contract = workflow_contract_from_input(published_input)
            except Exception:
                continue

            available_versions: list[dict[str, Any]] = []
            versions = (
                self.db.query(AssistantWorkflowVersion)
                .filter(
                    AssistantWorkflowVersion.workflow_id == workflow.id,
                    AssistantWorkflowVersion.version_source == "publish",
                )
                .order_by(AssistantWorkflowVersion.sequence_no.desc())
                .all()
            )
            for version in versions:
                try:
                    version_input = self._workflow_input_from_version_snapshot(version)
                    version_contract = workflow_contract_from_input(version_input)
                except Exception:
                    continue
                available_versions.append(
                    CallableWorkflowVersionResponse.model_validate(
                        {
                            "id": version.id,
                            "sequence_no": version.sequence_no,
                            "version_name": version.version_name,
                            "version_source": version.version_source,
                            "input_mode": version_contract.input_mode,
                            "output_mode": version_contract.output_mode,
                            "input_params": [
                                WorkflowContractParamSchema.model_validate(item).model_dump()
                                for item in field_specs_to_params(version_contract.input_fields)
                            ],
                            "output_params": [
                                WorkflowContractParamSchema.model_validate(item).model_dump()
                                for item in field_specs_to_params(version_contract.output_fields)
                            ],
                            "created_at": version.created_at,
                            "updated_at": version.updated_at,
                        }
                    ).model_dump()
                )

            published_version_present = any(item["id"] == published_version.id for item in available_versions)
            if not published_version_present:
                continue

            items.append(
                CallableWorkflowResponse.model_validate(
                    {
                        "id": workflow.id,
                        "name": self._display_workflow_name(workflow),
                        "description": workflow.description or "",
                        "published_version_id": published_version.id,
                        "input_mode": published_contract.input_mode,
                        "output_mode": published_contract.output_mode,
                        "input_params": [
                            WorkflowContractParamSchema.model_validate(item).model_dump()
                            for item in field_specs_to_params(published_contract.input_fields)
                        ],
                        "output_params": [
                            WorkflowContractParamSchema.model_validate(item).model_dump()
                            for item in field_specs_to_params(published_contract.output_fields)
                        ],
                        "available_versions": available_versions,
                    }
                ).model_dump()
            )
        return items

    def _collect_transitive_workflow_dependency_sets(
        self,
        *,
        workflow_input: WorkflowInput,
        current_workflow_id: UUID | None,
        visited_workflow_ids: set[UUID] | None = None,
    ) -> tuple[set[str], set[UUID]]:
        tool_names = self._collect_workflow_tool_names(workflow_input.nodes)
        model_ids = self._collect_workflow_custom_model_ids(workflow_input.nodes)
        visited = set(visited_workflow_ids or set())
        if current_workflow_id is not None:
            visited.add(current_workflow_id)

        for node_id, container_node_id, cfg in self._iter_workflow_call_node_configs(workflow_input.nodes):
            target_workflow_id = self._parse_uuid_value(
                self._cfg_get(cfg, "target_workflow_id", "targetWorkflowId", default=None)
            )
            if target_workflow_id is None:
                continue

            binding_mode = str(self._cfg_get(cfg, "binding_mode", "bindingMode", default="pinned") or "pinned").strip().lower()
            target_version_id = self._parse_uuid_value(
                self._cfg_get(cfg, "target_published_version_id", "targetPublishedVersionId", default=None)
            )
            resolved = self._resolve_workflow_call_target(
                target_workflow_id=target_workflow_id,
                binding_mode=binding_mode,
                target_published_version_id=target_version_id,
            )
            self._validate_workflow_call_input_bindings(
                cfg=cfg,
                ref=WorkflowCallReference(
                    source_workflow_id=current_workflow_id or resolved.workflow.id,
                    source_workflow_name="",
                    source_kind="draft",
                    source_node_id=node_id,
                    source_container_node_id=container_node_id,
                    source_version_id=None,
                    source_version_name=None,
                    source_version_source=None,
                    target_workflow_id=target_workflow_id,
                    binding_mode=binding_mode,
                    target_published_version_id=target_version_id,
                ),
                contract=resolved.contract,
            )
            tool_names.update(self._collect_workflow_tool_names(resolved.workflow_input.nodes))
            model_ids.update(self._collect_workflow_custom_model_ids(resolved.workflow_input.nodes))
            if resolved.workflow.id in visited:
                continue
            child_tools, child_models = self._collect_transitive_workflow_dependency_sets(
                workflow_input=resolved.workflow_input,
                current_workflow_id=resolved.workflow.id,
                visited_workflow_ids={*visited, resolved.workflow.id},
            )
            tool_names.update(child_tools)
            model_ids.update(child_models)

        return tool_names, model_ids

    @staticmethod
    def _extract_structured_start_fields(workflow_input: WorkflowInput) -> dict[str, dict[str, Any]]:
        for node in workflow_input.nodes:
            if node.node_type != "start":
                continue
            cfg = node.config if isinstance(node.config, dict) else {}
            raw_fields = cfg.get("structured_fields", cfg.get("structuredFields", []))
            if not isinstance(raw_fields, list):
                return {}
            field_map: dict[str, dict[str, Any]] = {}
            for item in raw_fields:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                if not name:
                    continue
                field_map[name] = item
            return field_map
        return {}

    @staticmethod
    def _normalize_output_mode(config: dict[str, Any]) -> str:
        raw_mode = str(config.get("output_mode", config.get("outputMode", "text")) or "text").strip().lower()
        if raw_mode == "json":
            return "structured"
        return "structured" if raw_mode == "structured" else "text"

    def _workflow_has_system_behavior_output_contract(
        self,
        *,
        workflow_input: WorkflowInput,
        definition: SystemBehaviorDefinition,
    ) -> bool:
        for node in workflow_input.nodes:
            if node.node_type != "output":
                continue
            config = node.config if isinstance(node.config, dict) else {}
            if self._normalize_output_mode(config) != "structured":
                continue
            raw_fields = config.get("output_fields", config.get("outputFields", []))
            if not isinstance(raw_fields, list):
                continue
            field_map: dict[str, dict[str, Any]] = {}
            for item in raw_fields:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                if not name:
                    continue
                field_map[name] = item

            matches = True
            for expected in definition.output_fields:
                actual = field_map.get(expected.name)
                if actual is None:
                    matches = False
                    break
                actual_type = str(actual.get("type", "string") or "string").strip().lower()
                if actual_type != expected.type:
                    matches = False
                    break
                if expected.type == "array":
                    actual_items_type = str(
                        actual.get("items_type", actual.get("itemsType", "")) or ""
                    ).strip().lower()
                    if actual_items_type != str(expected.items_type or "").strip().lower():
                        matches = False
                        break
            if matches:
                return True
        return False

    def _validate_system_behavior_workflow_target(
        self,
        *,
        definition: SystemBehaviorDefinition,
        workflow: AssistantWorkflow,
    ) -> WorkflowInput:
        if not workflow.enabled:
            raise ApiException(
                status_code=409,
                code=40949,
                message=f"Workflow target is disabled: {workflow.name}",
            )

        published_input = self._get_workflow_published_input(workflow)
        if published_input is None:
            raise ApiException(
                status_code=409,
                code=40950,
                message=f"Workflow target has no published version: {workflow.name}",
            )

        self._apply_workflow_to_workflow_entity(workflow, published_input, persist=False)

        if self._resolve_start_input_mode(published_input) != "structured":
            raise ApiException(
                status_code=422,
                code=42248,
                message=f"Workflow target must use structured start input: {workflow.name}",
            )

        start_field_map = self._extract_structured_start_fields(published_input)
        missing_fields: list[str] = []
        invalid_fields: list[str] = []
        for expected in definition.input_fields:
            actual = start_field_map.get(expected.name)
            if actual is None:
                missing_fields.append(expected.name)
                continue
            actual_type = str(actual.get("type", "string") or "string").strip().lower()
            if actual_type != expected.type:
                invalid_fields.append(expected.name)
                continue
            if expected.required and not bool(actual.get("required", False)):
                invalid_fields.append(expected.name)

        if missing_fields or invalid_fields:
            detail_parts: list[str] = []
            if missing_fields:
                detail_parts.append(f"missing input fields: {', '.join(sorted(missing_fields))}")
            if invalid_fields:
                detail_parts.append(f"invalid input fields: {', '.join(sorted(set(invalid_fields)))}")
            raise ApiException(
                status_code=422,
                code=42249,
                message=f"Workflow target does not satisfy system behavior input contract: {'; '.join(detail_parts)}",
            )

        if not self._workflow_has_system_behavior_output_contract(
            workflow_input=published_input,
            definition=definition,
        ):
            raise ApiException(
                status_code=422,
                code=42250,
                message=(
                    "Workflow target does not expose a structured output node for "
                    f"system behavior contract: {workflow.name}"
                ),
            )

        return published_input

    def _validate_system_behavior_agent_target(
        self,
        *,
        agent_profile: AssistantAgentProfile,
    ) -> AgentPublishDraftInput:
        if not agent_profile.enabled:
            raise ApiException(
                status_code=409,
                code=40951,
                message=f"Agent target is disabled: {agent_profile.name}",
            )

        published_draft = self._get_agent_profile_published_draft(agent_profile)
        if published_draft is None:
            raise ApiException(
                status_code=409,
                code=40952,
                message=f"Agent target has no published version: {agent_profile.name}",
            )

        raise ApiException(
            status_code=422,
            code=42276,
            message=(
                "Agent target does not expose an explicit published input/output contract "
                f"for system behavior binding: {agent_profile.name}"
            ),
        )

    def _ensure_system_behavior_default_workflow(
        self,
        definition: SystemBehaviorDefinition,
    ) -> tuple[AssistantWorkflow, bool]:
        locale = self._current_locale()
        expected_name = definition.default_target.canonical_name
        changed = False
        workflow = (
            self.db.query(AssistantWorkflow)
            .options(
                joinedload(AssistantWorkflow.draft_version),
                joinedload(AssistantWorkflow.published_version),
                joinedload(AssistantWorkflow.system_behavior_bindings),
            )
            .filter(
                AssistantWorkflow.name == expected_name,
                AssistantWorkflow.is_system.is_(True),
            )
            .first()
        )
        if workflow is None:
            conflicting = (
                self.db.query(AssistantWorkflow)
                .filter(AssistantWorkflow.name == expected_name)
                .first()
            )
            if conflicting is not None and not bool(conflicting.is_system):
                raise ApiException(
                    status_code=409,
                    code=40953,
                    message=f"Cannot create system behavior workflow due to custom name conflict: {expected_name}",
                )
            workflow = AssistantWorkflow(
                name=expected_name,
                description=definition.description,
                workflow_version=0,
                workflow_viewport=None,
                is_system=True,
                enabled=True,
            )
            self.db.add(workflow)
            self.db.flush()
            changed = True

        asset_key = str(definition.default_target.default_target_asset_key or "").strip()
        if not asset_key:
            raise ApiException(
                status_code=500,
                code=50036,
                message=f"System behavior '{definition.key}' is missing its default workflow asset key",
            )
        workflow_input = load_system_workflow_asset(asset_key, locale=locale)
        changed = self._ensure_system_workflow_baseline_state(
            workflow=workflow,
            workflow_input=workflow_input,
            description=definition.description,
            enabled=True,
            version_name="System Default",
        ) or changed
        return workflow, changed

    def _resolve_or_create_system_behavior_default_workflow(
        self,
        definition: SystemBehaviorDefinition,
    ) -> AssistantWorkflow:
        workflow, _ = self._ensure_system_behavior_default_workflow(definition)
        return workflow

    def _reset_system_behavior_default_workflow_to_preset(
        self,
        definition: SystemBehaviorDefinition,
    ) -> AssistantWorkflow:
        workflow, _ = self._ensure_system_behavior_default_workflow(definition)
        return workflow

    def _ensure_standalone_system_workflow(
        self,
        definition: StandaloneSystemWorkflowDefinition,
    ) -> tuple[AssistantWorkflow, bool]:
        locale = self._current_locale()
        expected_name = definition.canonical_name
        changed = False
        workflow = (
            self.db.query(AssistantWorkflow)
            .options(
                joinedload(AssistantWorkflow.draft_version),
                joinedload(AssistantWorkflow.published_version),
                joinedload(AssistantWorkflow.system_behavior_bindings),
            )
            .filter(
                AssistantWorkflow.name == expected_name,
                AssistantWorkflow.is_system.is_(True),
            )
            .first()
        )
        legacy_workflows = []
        if definition.legacy_canonical_names:
            legacy_workflows = (
                self.db.query(AssistantWorkflow)
                .options(
                    joinedload(AssistantWorkflow.draft_version),
                    joinedload(AssistantWorkflow.published_version),
                        joinedload(AssistantWorkflow.system_behavior_bindings),
                )
                .filter(
                    AssistantWorkflow.name.in_(tuple(definition.legacy_canonical_names)),
                    AssistantWorkflow.is_system.is_(True),
                )
                .order_by(AssistantWorkflow.created_at.asc(), AssistantWorkflow.id.asc())
                .all()
            )

        if workflow is not None:
            duplicate_legacy = [item for item in legacy_workflows if item.id != workflow.id]
            if duplicate_legacy:
                duplicate_names = ", ".join(sorted({item.name for item in duplicate_legacy}))
                raise ApiException(
                    status_code=409,
                    code=40967,
                    message=(
                        "Duplicate standalone system workflow targets detected for "
                        f"{expected_name}: {duplicate_names}"
                    ),
                )
        elif legacy_workflows:
            if len(legacy_workflows) > 1:
                duplicate_names = ", ".join(sorted({item.name for item in legacy_workflows}))
                raise ApiException(
                    status_code=409,
                    code=40967,
                    message=(
                        "Duplicate standalone system workflow targets detected for "
                        f"{expected_name}: {duplicate_names}"
                    ),
                )
            workflow = legacy_workflows[0]

        conflicting = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == expected_name)
            .first()
        )
        if conflicting is not None and (workflow is None or conflicting.id != workflow.id):
            if not bool(conflicting.is_system):
                raise ApiException(
                    status_code=409,
                    code=40946,
                    message=f"Cannot create system workflow target due to custom name conflict: {expected_name}",
                )
            raise ApiException(
                status_code=409,
                code=40967,
                message=f"Duplicate standalone system workflow targets detected for {expected_name}",
            )

        if workflow is None:
            workflow = AssistantWorkflow(
                name=expected_name,
                description=definition.description,
                workflow_version=0,
                workflow_viewport=None,
                is_system=True,
                enabled=bool(definition.enabled_by_default),
            )
            self.db.add(workflow)
            self.db.flush()
            changed = True
        elif workflow.name != expected_name:
            workflow.name = expected_name
            changed = True

        workflow_input = load_system_workflow_asset(definition.asset_key, locale=locale)
        changed = self._ensure_system_workflow_baseline_state(
            workflow=workflow,
            workflow_input=workflow_input,
            description=definition.description,
            enabled=bool(definition.enabled_by_default),
            version_name="System Default",
        ) or changed
        return workflow, changed

    def ensure_standalone_system_workflow_asset(
        self,
        asset_key: str,
        *,
        locale: str | None = None,
    ) -> AssistantWorkflow:
        normalized_locale = self._current_locale(locale)
        definition = get_standalone_system_workflow_definition(asset_key, locale=normalized_locale)
        if definition is None:
            raise ApiException(
                status_code=404,
                code=40437,
                message=f"Standalone system workflow asset not found: {asset_key}",
            )
        workflow, _ = self._ensure_standalone_system_workflow(definition)
        return workflow

    def sync_standalone_system_targets(self, *, commit: bool = True) -> None:
        changed = False
        locale = self._current_locale()
        for definition in list_standalone_system_workflow_definitions(locale=locale):
            _, workflow_changed = self._ensure_standalone_system_workflow(definition)
            if workflow_changed:
                changed = True

        if not changed or not commit:
            return

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40968, message="Sync standalone system targets failed") from exc

    def _ensure_system_behavior_binding_entity(
        self,
        definition: SystemBehaviorDefinition,
        *,
        default_workflow: AssistantWorkflow | None = None,
    ) -> AssistantSystemBehaviorBinding:
        binding = self._get_system_behavior_binding(definition.key)
        if binding is not None:
            return binding

        if definition.default_target.target_type != "workflow":
            raise ApiException(
                status_code=500,
                code=50030,
                message=f"Unsupported system behavior default target type: {definition.default_target.target_type}",
            )

        resolved_workflow = default_workflow or self._resolve_or_create_system_behavior_default_workflow(definition)
        binding = AssistantSystemBehaviorBinding(
            behavior_key=definition.key,
            target_type="workflow",
            workflow=resolved_workflow,
        )
        self.db.add(binding)
        self.db.flush()
        return binding

    def ensure_system_behaviors(self, *, commit: bool = True) -> None:
        changed = False
        locale = self._current_locale()
        for definition in list_system_behavior_definitions(locale=locale):
            default_workflow: AssistantWorkflow | None = None
            if definition.default_target.target_type == "workflow":
                default_workflow, workflow_changed = self._ensure_system_behavior_default_workflow(definition)
                if workflow_changed or default_workflow.published_version_id is None:
                    changed = True
            binding_before = self._get_system_behavior_binding(definition.key)
            binding = self._ensure_system_behavior_binding_entity(
                definition,
                default_workflow=default_workflow,
            )
            if binding_before is None:
                changed = True

        if not changed or not commit:
            return

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40954, message="Sync system AI behaviors failed") from exc

    def _serialize_system_behavior(
        self,
        definition: SystemBehaviorDefinition,
        binding: AssistantSystemBehaviorBinding,
    ) -> dict[str, Any]:
        if definition.default_target.target_type == "workflow":
            canonical_workflow = self._resolve_or_create_system_behavior_default_workflow(definition)
            canonical_summary = self._serialize_system_behavior_target_summary(
                target_type="workflow",
                workflow=canonical_workflow,
                is_canonical_default=True,
            )
        else:
            raise ApiException(
                status_code=500,
                code=50031,
                message=f"Unsupported canonical target type: {definition.default_target.target_type}",
            )

        if str(binding.target_type or "") == "workflow":
            if binding.workflow is None:
                raise ApiException(
                    status_code=409,
                    code=40955,
                    message=f"System AI behavior binding is missing workflow target: {binding.behavior_key}",
                )
            current_binding = self._serialize_system_behavior_target_summary(
                target_type="workflow",
                workflow=binding.workflow,
                is_canonical_default=self._system_behavior_binding_matches_workflow(binding, canonical_workflow),
            )
        else:
            if binding.agent_profile is None:
                raise ApiException(
                    status_code=409,
                    code=40956,
                    message=f"System AI behavior binding is missing agent target: {binding.behavior_key}",
                )
            current_binding = self._serialize_system_behavior_target_summary(
                target_type="agent",
                agent_profile=binding.agent_profile,
                is_canonical_default=False,
            )

        return {
            "behavior_key": definition.key,
            "name": definition.name,
            "description": definition.description,
            "supported_target_types": list(definition.supported_target_types),
            "current_binding": current_binding,
            "canonical_default_target": canonical_summary,
            "fallback_policy": definition.fallback_policy,
            "contract": {
                "input_fields": [
                    self._serialize_system_behavior_contract_field(field)
                    for field in definition.input_fields
                ],
                "output_fields": [
                    self._serialize_system_behavior_contract_field(field)
                    for field in definition.output_fields
                ],
            },
        }

    def list_system_behaviors(self) -> list[dict[str, Any]]:
        locale = self._current_locale()
        bindings = {
            item.behavior_key: item
            for item in (
                self.db.query(AssistantSystemBehaviorBinding)
                .options(
                    joinedload(AssistantSystemBehaviorBinding.workflow),
                    joinedload(AssistantSystemBehaviorBinding.agent_profile),
                )
                .all()
            )
        }
        return [
            self._serialize_system_behavior(
                definition,
                bindings[definition.key],
            )
            for definition in list_system_behavior_definitions(locale=locale)
            if definition.key in bindings
        ]

    def create_system_behavior_example_workflow(
        self,
        behavior_key: str,
        *,
        bind_to_behavior: bool = False,
    ) -> dict[str, Any]:
        definition = self._get_system_behavior_definition_or_error(behavior_key)
        locale = self._current_locale()
        self.ensure_system_catalog_synced()
        if definition.default_target.target_type != "workflow":
            raise ApiException(
                status_code=500,
                code=50037,
                message=f"Unsupported canonical target type: {definition.default_target.target_type}",
            )

        example_meta = self._localized_system_behavior_example_meta(definition.key, locale=locale)
        workflow_name = self._next_available_workflow_name(example_meta["name"])
        workflow_description = example_meta["description"] or (
            f"{definition.name}示例工作流。" if locale == "zh" else f"{definition.name} example workflow."
        )
        canonical_workflow = self._resolve_or_create_system_behavior_default_workflow(definition)
        workflow = self._copy_workflow_entity(
            canonical_workflow,
            name_override=workflow_name,
            description_override=workflow_description,
        )

        binding = self._ensure_system_behavior_binding_entity(definition)
        if bind_to_behavior:
            self._assign_system_behavior_binding_target(
                binding,
                target_type="workflow",
                workflow=workflow,
            )

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40964,
                message="Create system AI behavior example workflow failed",
            ) from exc

        created_workflow = self.get_workflow(workflow.id)
        refreshed = self._get_system_behavior_binding(definition.key)
        if refreshed is None:
            raise ApiException(
                status_code=500,
                code=50039,
                message="System AI behavior binding missing after example workflow creation",
            )
        return {
            "created_workflow": self._serialize_workflow(created_workflow),
            "system_behavior": self._serialize_system_behavior(definition, refreshed),
        }

    def update_system_behavior_binding(
        self,
        *,
        behavior_key: str,
        target_type: TargetType,
        workflow_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
    ) -> dict[str, Any]:
        definition = self._get_system_behavior_definition_or_error(behavior_key)
        self.ensure_system_catalog_synced()
        if target_type not in definition.supported_target_types:
            raise ApiException(
                status_code=422,
                code=42251,
                message=f"Unsupported target type for system AI behavior '{behavior_key}': {target_type}",
            )

        binding = self._ensure_system_behavior_binding_entity(definition)
        if target_type == "workflow":
            if workflow_id is None:
                raise ApiException(status_code=422, code=42252, message="workflow_id is required")
            workflow = self.get_workflow(workflow_id)
            self._validate_system_behavior_workflow_target(
                definition=definition,
                workflow=workflow,
            )
            self._assign_system_behavior_binding_target(
                binding,
                target_type="workflow",
                workflow=workflow,
            )
        else:
            if agent_profile_id is None:
                raise ApiException(status_code=422, code=42253, message="agent_profile_id is required")
            agent_profile = self.get_agent_profile(agent_profile_id)
            self._validate_system_behavior_agent_target(agent_profile=agent_profile)
            self._assign_system_behavior_binding_target(
                binding,
                target_type="agent",
                agent_profile=agent_profile,
            )

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40957, message="Update system AI behavior binding failed") from exc

        refreshed = self._get_system_behavior_binding(definition.key)
        if refreshed is None:
            raise ApiException(status_code=500, code=50032, message="System AI behavior binding missing after update")
        return self._serialize_system_behavior(definition, refreshed)

    def reset_system_behavior_binding(self, behavior_key: str) -> dict[str, Any]:
        definition = self._get_system_behavior_definition_or_error(behavior_key)
        self.ensure_system_catalog_synced()
        binding = self._ensure_system_behavior_binding_entity(definition)
        if definition.default_target.target_type != "workflow":
            raise ApiException(
                status_code=500,
                code=50033,
                message=f"Unsupported canonical target type: {definition.default_target.target_type}",
            )
        workflow = self._reset_system_behavior_default_workflow_to_preset(definition)
        self._assign_system_behavior_binding_target(
            binding,
            target_type="workflow",
            workflow=workflow,
        )

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40958, message="Reset system AI behavior binding failed") from exc

        refreshed = self._get_system_behavior_binding(definition.key)
        if refreshed is None:
            raise ApiException(status_code=500, code=50034, message="System AI behavior binding missing after reset")
        return self._serialize_system_behavior(definition, refreshed)

    def reset_all_system_behaviors(self, confirm: bool, *, commit: bool = True) -> dict[str, Any]:
        if not confirm:
            raise ApiException(status_code=400, code=40023, message="confirm=true required")

        self.ensure_system_behaviors(commit=False)
        definitions = list_system_behavior_definitions(locale=self._current_locale())
        affected: list[dict[str, Any]] = []
        for definition in definitions:
            binding = self._ensure_system_behavior_binding_entity(definition)
            if definition.default_target.target_type != "workflow":
                raise ApiException(
                    status_code=500,
                    code=50036,
                    message=f"Unsupported canonical target type: {definition.default_target.target_type}",
                )
            workflow = self._reset_system_behavior_default_workflow_to_preset(definition)
            self._assign_system_behavior_binding_target(
                binding,
                target_type="workflow",
                workflow=workflow,
            )
            affected.append(
                {
                    "behavior_key": definition.key,
                    "name": definition.name,
                    "target_type": "workflow",
                    "target_name": self._display_workflow_name(workflow),
                }
            )

        if commit:
            try:
                self.db.commit()
            except IntegrityError as exc:
                self.db.rollback()
                raise ApiException(status_code=409, code=40965, message="Reset all system AI behaviors failed") from exc

        return {
            "reset_count": len(affected),
            "affected": affected,
        }

    def resolve_system_behavior_execution_target(
        self,
        behavior_key: str,
    ) -> tuple[SystemBehaviorDefinition, AssistantSystemBehaviorBinding, TargetType, AssistantWorkflow | AssistantAgentProfile, bool]:
        definition = self._get_system_behavior_definition_or_error(behavior_key)
        self.ensure_system_behaviors()
        binding = self._ensure_system_behavior_binding_entity(definition)
        fallback_used = False

        def _canonical_target() -> tuple[TargetType, AssistantWorkflow | AssistantAgentProfile]:
            if definition.default_target.target_type == "workflow":
                workflow = self._resolve_or_create_system_behavior_default_workflow(definition)
                self._validate_system_behavior_workflow_target(
                    definition=definition,
                    workflow=workflow,
                )
                return ("workflow", workflow)
            raise ApiException(
                status_code=500,
                code=50035,
                message=f"Unsupported canonical target type: {definition.default_target.target_type}",
            )

        try:
            if str(binding.target_type or "") == "workflow":
                if binding.workflow is None:
                    raise ApiException(status_code=409, code=40959, message="Missing bound workflow target")
                self._validate_system_behavior_workflow_target(
                    definition=definition,
                    workflow=binding.workflow,
                )
                return definition, binding, "workflow", binding.workflow, fallback_used

            if binding.agent_profile is None:
                raise ApiException(status_code=409, code=40960, message="Missing bound agent target")
            self._validate_system_behavior_agent_target(agent_profile=binding.agent_profile)
            return definition, binding, "agent", binding.agent_profile, fallback_used
        except ApiException:
            fallback_used = True
            target_type, target = _canonical_target()
            return definition, binding, target_type, target, fallback_used

    def _rebind_system_behaviors_to_defaults(self, behavior_keys: list[str]) -> None:
        normalized_keys = sorted({str(item or "").strip() for item in behavior_keys if str(item or "").strip()})
        if not normalized_keys:
            return
        for behavior_key in normalized_keys:
            definition = self._get_system_behavior_definition_or_error(behavior_key)
            binding = self._ensure_system_behavior_binding_entity(definition)
            if definition.default_target.target_type != "workflow":
                raise ApiException(
                    status_code=500,
                    code=50036,
                    message=f"Unsupported canonical target type: {definition.default_target.target_type}",
                )
            workflow = self._resolve_or_create_system_behavior_default_workflow(definition)
            self._assign_system_behavior_binding_target(
                binding,
                target_type="workflow",
                workflow=workflow,
            )

    @staticmethod
    def _read_agent_model_config(kb_config: dict | None) -> tuple[str, UUID | None]:
        raw = kb_config if isinstance(kb_config, dict) else {}
        source_raw = raw.get("model_source", raw.get("modelSource", "default"))
        source = str(source_raw or "default").strip().lower()
        if source not in {"default", "custom"}:
            source = "default"

        model_id_value = raw.get("model_id", raw.get("modelId"))
        parsed_model_id: UUID | None = None
        if model_id_value is not None:
            text = str(model_id_value).strip()
            if text:
                try:
                    parsed_model_id = UUID(text)
                except Exception:
                    parsed_model_id = None

        if source == "custom" and parsed_model_id is not None:
            return ("custom", parsed_model_id)
        return ("default", None)

    def _normalize_agent_kb_config(
        self,
        *,
        kb_config: dict | None,
        model_source: str | None,
        model_id: UUID | None,
        existing_kb_config: dict | None = None,
    ) -> dict:
        base: dict[str, Any] = {}
        if isinstance(existing_kb_config, dict):
            base.update(existing_kb_config)
        if isinstance(kb_config, dict):
            base.update(kb_config)

        source_from_existing, id_from_existing = self._read_agent_model_config(base)
        source = (str(model_source).strip().lower() if model_source is not None else source_from_existing)
        if model_source is None and model_id is not None:
            source = "custom"
        if source not in {"default", "custom"}:
            raise ApiException(status_code=422, code=42243, message=f"Unsupported agent model_source: {model_source}")

        resolved_model_id = model_id if model_id is not None else id_from_existing
        if source == "custom":
            if resolved_model_id is None:
                raise ApiException(status_code=422, code=42244, message="custom model_source requires model_id")
            cfg = resolve_openai_compat_config_by_model_id(
                self.db,
                model_id=resolved_model_id,
                model_type="llm",
            )
            if cfg is None:
                raise ApiException(
                    status_code=422,
                    code=42245,
                    message=f"Agent references unavailable llm model: {resolved_model_id}",
                )
        else:
            resolved_model_id = None

        base["enabled"] = bool(base.get("enabled", False))
        base["model_source"] = source
        base["model_id"] = str(resolved_model_id) if resolved_model_id is not None else None
        # 清理历史 camelCase 键，避免同义键冲突
        base.pop("modelSource", None)
        base.pop("modelId", None)
        return base

    def _apply_workflow_to_workflow_entity(
        self,
        workflow_model: AssistantWorkflow,
        workflow: WorkflowInput,
        *,
        persist: bool = True,
    ) -> set[str]:
        """Validate workflow and optionally persist nodes/edges onto workflow entity."""
        from app.assistant.workflow.validation.validator import validate_workflow, validate_parallel_branches

        nodes_raw = [
            {
                "node_id": n.node_id,
                "node_type": n.node_type,
                "label": (getattr(n, "label", "") or getattr(n, "node_id", "")),
                "config": n.config,
            }
            for n in workflow.nodes
        ]
        edges_raw = [
            {
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "source_handle": e.source_handle,
            }
            for e in workflow.edges
        ]

        result = validate_workflow(nodes_raw, edges_raw)
        if not result.valid:
            msgs = "; ".join(e.message for e in result.errors[:5])
            raise ApiException(status_code=422, code=42201, message=f"Invalid workflow topology: {msgs}")

        par_result = validate_parallel_branches(nodes_raw, edges_raw)
        if not par_result.valid:
            msgs = "; ".join(e.message for e in par_result.errors[:5])
            raise ApiException(status_code=422, code=42202, message=f"Invalid parallel branches: {msgs}")

        workflow_tool_names = self.validate_workflow_dependencies(
            workflow,
            current_workflow_id=workflow_model.id,
        )

        if not persist:
            return workflow_tool_names

        workflow_model.workflow_version = (workflow_model.workflow_version or 0) + 1
        workflow_model.workflow_viewport = workflow.viewport
        return workflow_tool_names

    def _workflow_input_from_skill_default(self, default) -> WorkflowInput:
        workflow_input = self._build_default_workflow_input()
        if getattr(default, "workflow_nodes", None):
            workflow_input = WorkflowInput.model_validate(
                {
                    "nodes": [
                        {
                            "node_id": n.node_id,
                            "node_type": n.node_type,
                            "label": n.label,
                            "position_x": n.position_x,
                            "position_y": n.position_y,
                            "config": n.config,
                        }
                        for n in default.workflow_nodes
                    ],
                    "edges": [
                        {
                            "edge_id": e.edge_id,
                            "source_node_id": e.source_node_id,
                            "target_node_id": e.target_node_id,
                            "source_handle": e.source_handle,
                            "target_handle": e.target_handle,
                            "condition_type": e.condition_type,
                            "condition_expr": e.condition_expr.model_dump() if e.condition_expr else None,
                            "label": e.label,
                        }
                        for e in (getattr(default, "workflow_edges", None) or [])
                    ],
                    "viewport": getattr(default, "workflow_viewport", None),
                }
            )
        return workflow_input

    @staticmethod
    def _skill_default_kb_config(default) -> dict[str, Any]:
        return {"enabled": bool(getattr(getattr(default, "kb", None), "enabled", False))}

    def _agent_draft_from_skill_default(self, default) -> AgentPublishDraftInput:
        kb_config = self._skill_default_kb_config(default)
        model_source = str(getattr(default, "model_source", "default") or "default")
        model_id = getattr(default, "model_id", None)
        system_prompt = str(getattr(default, "system_prompt", "") or "").strip() or self._default_agent_system_prompt()
        return AgentPublishDraftInput.model_validate(
            {
                "system_prompt": system_prompt,
                "tools": [str(item) for item in (getattr(default, "tools", None) or []) if str(item).strip()],
                "kb_config": kb_config,
                "model_source": model_source,
                "model_id": model_id,
            }
        )

    def _resolve_or_create_system_workflow_for_reset(
        self,
        *,
        skill: Any,
        default,
        enabled: bool,
    ) -> AssistantWorkflow:
        if skill.workflow_id is not None and skill.workflow is not None and skill.workflow.is_system:
            workflow = skill.workflow
        else:
            expected_name = f"{skill.name}__workflow"
            workflow = (
                self.db.query(AssistantWorkflow)
                .filter(
                    AssistantWorkflow.name == expected_name,
                    AssistantWorkflow.is_system.is_(True),
                )
                .first()
            )
            if workflow is None:
                conflicting = (
                    self.db.query(AssistantWorkflow)
                    .filter(AssistantWorkflow.name == expected_name)
                    .first()
                )
                if conflicting is not None and not bool(conflicting.is_system):
                    raise ApiException(
                        status_code=409,
                        code=40946,
                        message=f"Cannot create system workflow target due to custom name conflict: {expected_name}",
                    )
                workflow = AssistantWorkflow(
                    name=expected_name,
                    description=default.description or "",
                    workflow_version=0,
                    workflow_viewport=None,
                    is_system=True,
                    enabled=bool(enabled),
                )
                self.db.add(workflow)
                self.db.flush()

        workflow.is_system = True
        workflow.enabled = bool(enabled)
        workflow.description = default.description or ""
        return workflow

    def _resolve_or_create_system_agent_profile_for_reset(
        self,
        *,
        skill: Any,
        default,
        enabled: bool,
    ) -> AssistantAgentProfile:
        if skill.agent_profile_id is not None and skill.agent_profile is not None and skill.agent_profile.is_system:
            profile = skill.agent_profile
        else:
            expected_name = f"{skill.name}__agent"
            profile = (
                self.db.query(AssistantAgentProfile)
                .filter(
                    AssistantAgentProfile.name == expected_name,
                    AssistantAgentProfile.is_system.is_(True),
                )
                .first()
            )
            if profile is None:
                conflicting = (
                    self.db.query(AssistantAgentProfile)
                    .filter(AssistantAgentProfile.name == expected_name)
                    .first()
                )
                if conflicting is not None and not bool(conflicting.is_system):
                    raise ApiException(
                        status_code=409,
                        code=40947,
                        message=f"Cannot create system agent target due to custom name conflict: {expected_name}",
                    )
                initial_draft = self._agent_draft_from_skill_default(default)
                normalized_kb = self._normalize_agent_kb_config(
                    kb_config=initial_draft.kb_config,
                    model_source=initial_draft.model_source,
                    model_id=initial_draft.model_id,
                    existing_kb_config=(
                        initial_draft.kb_config if isinstance(initial_draft.kb_config, dict) else {"enabled": False}
                    ),
                )
                profile = AssistantAgentProfile(
                    name=expected_name,
                    description=default.description or "",
                    system_prompt=initial_draft.system_prompt,
                    tools=list(initial_draft.tools or []),
                    kb_config=normalized_kb,
                    is_system=True,
                    enabled=bool(enabled),
                )
                self.db.add(profile)
                self.db.flush()

        profile.is_system = True
        profile.enabled = bool(enabled)
        profile.description = default.description or ""
        return profile

    def _keep_only_workflow_version(self, workflow: AssistantWorkflow, keep_version_id: UUID) -> None:
        # Preserve only Skill-package references (and the keep target). System baseline
        # restore must not retain superseded draft/published/baseline pointers.
        protected_ids = {keep_version_id}
        for row in (
            self.db.query(AssistantSkillCapabilityBinding.resolved_workflow_version_id)
            .filter(AssistantSkillCapabilityBinding.resolved_workflow_version_id.isnot(None))
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantWorkflowVersion.workflow_id)
                .filter(AssistantWorkflowVersion.id == version_id)
                .scalar()
            )
            if owner == workflow.id:
                protected_ids.add(version_id)
        for row in (
            self.db.query(AssistantSkillCapabilityDependency.resolved_workflow_version_id)
            .filter(AssistantSkillCapabilityDependency.resolved_workflow_version_id.isnot(None))
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantWorkflowVersion.workflow_id)
                .filter(AssistantWorkflowVersion.id == version_id)
                .scalar()
            )
            if owner == workflow.id:
                protected_ids.add(version_id)
        (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.workflow_id == workflow.id,
                AssistantWorkflowVersion.id.notin_(list(protected_ids)),
            )
            .delete(synchronize_session=False)
        )
        workflow.draft_version_id = keep_version_id
        workflow.published_version_id = keep_version_id
        self.db.expire(workflow, ["draft_version", "published_version", "versions"])

    def _keep_only_agent_version(self, agent_profile: AssistantAgentProfile, keep_version_id: UUID) -> None:
        # Preserve only Skill-package references (and the keep target). System baseline
        # restore must not retain superseded draft/published/baseline pointers.
        protected_ids = {keep_version_id}
        for row in (
            self.db.query(AssistantSkillCapabilityBinding.resolved_agent_version_id)
            .filter(AssistantSkillCapabilityBinding.resolved_agent_version_id.isnot(None))
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantAgentProfileVersion.agent_profile_id)
                .filter(AssistantAgentProfileVersion.id == version_id)
                .scalar()
            )
            if owner == agent_profile.id:
                protected_ids.add(version_id)
        for row in (
            self.db.query(AssistantSkillCapabilityDependency.resolved_agent_version_id)
            .filter(AssistantSkillCapabilityDependency.resolved_agent_version_id.isnot(None))
            .all()
        ):
            version_id = row[0]
            if version_id is None:
                continue
            owner = (
                self.db.query(AssistantAgentProfileVersion.agent_profile_id)
                .filter(AssistantAgentProfileVersion.id == version_id)
                .scalar()
            )
            if owner == agent_profile.id:
                protected_ids.add(version_id)
        (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.agent_profile_id == agent_profile.id,
                AssistantAgentProfileVersion.id.notin_(list(protected_ids)),
            )
            .delete(synchronize_session=False)
        )
        agent_profile.draft_version_id = keep_version_id
        agent_profile.published_version_id = keep_version_id
        self.db.expire(agent_profile, ["versions"])

    def _acquire_system_catalog_sync_lock(self) -> None:
        bind = self.db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "postgresql":
            return
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _SYSTEM_CATALOG_SYNC_LOCK_KEY},
        )

    def _acquire_target_folder_mutation_lock(self) -> None:
        bind = self.db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "postgresql":
            return
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _TARGET_FOLDER_MUTATION_LOCK_KEY},
        )

    def _expected_system_catalog_names(self, *, locale: str | None = None) -> dict[str, set[str]]:
        normalized_locale = self._current_locale(locale)
        assets = list_system_assets(locale=normalized_locale)
        return {
            "skill_names": {
                definition.name
                for definition in SkillRegistry.list_system_skill_definitions(locale=normalized_locale)
                if definition.name
            },
            "workflow_names": {
                asset.canonical_name
                for asset in assets
                if asset.kind == "workflow" and asset.canonical_name
            },
            "agent_names": {
                asset.canonical_name
                for asset in assets
                if asset.kind == "agent" and asset.canonical_name
            },
            "behavior_keys": {
                definition.key
                for definition in list_system_behavior_definitions(locale=normalized_locale)
                if definition.key
            },
        }

    def _compute_system_catalog_signature(self, *, locale: str | None = None) -> str:
        normalized_locale = self._current_locale(locale)
        assets = list_system_assets(locale=normalized_locale)
        payload = {
            "locale": normalized_locale,
            "internal_tool_names": sorted(ToolRegistry.INTERNAL_TOOL_NAMES or []),
            "system_tool_definitions": [
                asdict(item)
                for item in ToolRegistry.list_system_tool_definitions(locale=normalized_locale)
            ],
            "system_skill_definitions": [
                asdict(item)
                for item in SkillRegistry.list_system_skill_definitions(locale=normalized_locale)
            ],
            "system_asset_definitions": [asdict(item) for item in assets],
            "system_behavior_definitions": [
                asdict(item)
                for item in list_system_behavior_definitions(locale=normalized_locale)
            ],
            "system_workflow_assets": {
                asset.asset_key: load_system_workflow_asset(asset.asset_key, locale=normalized_locale).model_dump(
                    by_alias=True,
                    mode="json",
                )
                for asset in assets
                if asset.kind == "workflow"
            },
            "system_agent_assets": {
                asset.asset_key: load_system_agent_asset(asset.asset_key, locale=normalized_locale).model_dump(
                    by_alias=True,
                    mode="json",
                )
                for asset in assets
                if asset.kind == "agent"
            },
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _load_persisted_system_catalog_signature(self) -> dict[str, Any] | None:
        setting = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == _SYSTEM_CATALOG_SIGNATURE_SETTING_KEY)
            .first()
        )
        if setting is None or not isinstance(setting.value_json, dict):
            return None
        return dict(setting.value_json)

    def _store_system_catalog_signature(self, *, locale: str, signature: str) -> None:
        setting = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == _SYSTEM_CATALOG_SIGNATURE_SETTING_KEY)
            .first()
        )
        payload = {
            "locale": locale,
            "signature": signature,
            "updated_at": self._utcnow().isoformat(),
        }
        if setting is None:
            setting = AppSetting(
                key=_SYSTEM_CATALOG_SIGNATURE_SETTING_KEY,
                value_json=payload,
            )
            self.db.add(setting)
            return
        setting.value_json = payload

    def _has_minimal_system_catalog_presence(self, *, locale: str | None = None) -> bool:
        expected = self._expected_system_catalog_names(locale=locale)

        _ = expected.get("skill_names")  # assistant_skill dropped

        expected_workflow_names = expected["workflow_names"]
        if expected_workflow_names:
            existing_workflows = {
                str(name)
                for name, published_version_id in (
                    self.db.query(AssistantWorkflow.name, AssistantWorkflow.published_version_id)
                    .filter(
                        AssistantWorkflow.is_system.is_(True),
                        AssistantWorkflow.name.in_(tuple(expected_workflow_names)),
                    )
                    .all()
                )
                if name and published_version_id is not None
            }
            if existing_workflows != expected_workflow_names:
                return False

        expected_agent_names = expected["agent_names"]
        if expected_agent_names:
            existing_agents = {
                str(name)
                for name, published_version_id in (
                    self.db.query(AssistantAgentProfile.name, AssistantAgentProfile.published_version_id)
                    .filter(
                        AssistantAgentProfile.is_system.is_(True),
                        AssistantAgentProfile.name.in_(tuple(expected_agent_names)),
                    )
                    .all()
                )
                if name and published_version_id is not None
            }
            if existing_agents != expected_agent_names:
                return False

        expected_behavior_keys = expected["behavior_keys"]
        if expected_behavior_keys:
            existing_behavior_keys = {
                str(key)
                for key, in (
                    self.db.query(AssistantSystemBehaviorBinding.behavior_key)
                    .filter(AssistantSystemBehaviorBinding.behavior_key.in_(tuple(expected_behavior_keys)))
                    .all()
                )
                if key
            }
            if existing_behavior_keys != expected_behavior_keys:
                return False

        return True

    def _system_catalog_needs_sync(self, *, locale: str | None = None) -> bool:
        normalized_locale = self._current_locale(locale)
        persisted = self._load_persisted_system_catalog_signature()
        if not persisted:
            return True
        if persisted.get("locale") != normalized_locale:
            return True
        if not self._has_minimal_system_catalog_presence(locale=normalized_locale):
            return True
        current_signature = self._compute_system_catalog_signature(locale=normalized_locale)
        return str(persisted.get("signature") or "") != current_signature

    def _sync_system_catalog_locked(self, *, locale: str | None = None) -> None:
        normalized_locale = self._current_locale(locale)
        self.sync_system_tools(commit=False)
        self.sync_system_skills(commit=False)
        self.sync_standalone_system_targets(commit=False)
        self.ensure_system_behaviors(commit=False)
        self._ensure_system_targets_folder()
        signature = self._compute_system_catalog_signature(locale=normalized_locale)
        self._store_system_catalog_signature(locale=normalized_locale, signature=signature)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40969, message="Sync system catalog failed") from exc
        # After tools/workflows/agents/skills are present, mirror into disabled shadows.

    def ensure_system_catalog_synced(self) -> None:
        self._acquire_system_catalog_sync_lock()
        self._sync_system_catalog_locked()

    def ensure_system_catalog_warm(self) -> bool:
        normalized_locale = self._current_locale()
        self._acquire_system_catalog_sync_lock()
        if not self._system_catalog_needs_sync(locale=normalized_locale):
            self.db.rollback()
            return False
        self._sync_system_catalog_locked(locale=normalized_locale)
        return True

    # -------------------------
    # System seed / sync
    # -------------------------
    def sync_system_tools(self, *, commit: bool = True) -> None:
        """同步系统工具（仅清理 DB overlay，不写入系统工具定义）。

        设计说明：
        - 系统工具的“定义信息”（描述、参数、Schema）以代码为准，不落库。
        - 数据库仅保存系统工具的 enabled 覆盖（通常只有禁用项）。
        - 该方法负责清理历史遗留的 system/local 工具记录与已删除工具引用，避免 UI/技能配置出现陈旧工具。
        """
        system = ToolRegistry.list_system_tools()
        if not system:
            return

        system_names = {t.name for t in system if getattr(t, "name", None)}
        internal_names = set(ToolRegistry.INTERNAL_TOOL_NAMES or [])

        # 清理内部工具：不应出现在 DB 可配置工具列表/覆盖表里
        if internal_names:
            (
                self.db.query(AssistantTool)
                .filter(
                    (AssistantTool.is_system.is_(True)) | (AssistantTool.kind == "local"),
                    AssistantTool.name.in_(tuple(internal_names)),
                )
                .delete(synchronize_session=False)
            )

        # 清理：系统工具从代码定义获取，DB 中已移除的系统工具需要删除，避免在 UI/配置中继续出现
        stale_names: list[str] = []
        if system_names:
            stale_names_query = (
                self.db.query(AssistantTool.name)
                .filter(
                    (AssistantTool.is_system.is_(True))
                    | (AssistantTool.kind == "local")  # 历史遗留：kind=local 但未标记 is_system
                )
                .filter(~AssistantTool.name.in_(tuple(system_names)))
            )
            if internal_names:
                stale_names_query = stale_names_query.filter(~AssistantTool.name.in_(tuple(internal_names)))

            stale_names = [str(n) for (n,) in stale_names_query.all() if n]
            if stale_names:
                (
                    self.db.query(AssistantTool)
                    .filter(
                        (AssistantTool.is_system.is_(True)) | (AssistantTool.kind == "local"),
                        AssistantTool.name.in_(tuple(stale_names)),
                    )
                    .delete(synchronize_session=False)
                )

            # 清理历史遗留：enabled=True 的系统工具落库记录不再需要（默认启用），仅保留 enabled=False 覆盖
            (
                self.db.query(AssistantTool)
                .filter(
                    (AssistantTool.is_system.is_(True)) | (AssistantTool.kind == "local"),
                    AssistantTool.name.in_(tuple(system_names)),
                    AssistantTool.enabled.is_(True),
                )
                .delete(synchronize_session=False)
            )

        # 同时清理 skills.tools 里引用的已删除工具名，避免“技能配置里仍存在已删除工具”
        _ = internal_names.union(stale_names)  # assistant_skill dropped

        if not commit:
            return

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40910, message="Sync system tools failed") from exc

    def list_system_tool_definitions(
        self,
        *,
        include_disabled: bool = True,
        include_schema: bool = True,
        preferred_locale: str | None = None,
    ) -> list[dict]:
        """返回系统工具完整定义：从代码提取，DB 仅用于 overlay enabled 状态。"""
        from app.assistant_config.schemas import InputParamSchema, OutputParamSchema

        locale = self._current_locale(preferred_locale)
        definitions = ToolRegistry.list_system_tool_definitions(locale=locale)
        names = [d.name for d in definitions]

        enabled_by_name: dict[str, bool] = {n: True for n in names}
        if names:
            rows = (
                self.db.query(AssistantTool.name, AssistantTool.enabled)
                .filter(
                    AssistantTool.name.in_(tuple(names)),
                    AssistantTool.kind == "local",  # 仅使用 DB 里的 enabled 覆盖，不落库定义信息
                )
                .all()
            )
            for name, enabled in rows:
                enabled_by_name[str(name)] = bool(enabled)

        result: list[dict] = []
        for d in definitions:
            enabled = enabled_by_name.get(d.name, True)
            if not include_disabled and not enabled:
                continue

            result.append({
                "name": d.name,
                "description": d.description or None,
                "display_name": d.display_name,
                "display_description": d.display_description,
                "kind": "local",
                "is_system": True,
                "enabled": enabled,
                "input_params": [
                    InputParamSchema(
                        name=p.name,
                        description=p.description,
                        param_type=p.param_type,
                        required=p.required,
                    )
                    for p in (d.input_params or [])
                ],
                "output_params": [
                    OutputParamSchema(
                        name=p.name,
                        description=p.description,
                        param_type=p.param_type,
                    )
                    for p in (d.output_params or [])
                ],
                "returns": d.returns,
                "json_schema": d.json_schema if include_schema else None,
            })
        return result

    def sync_system_skills(self, *, commit: bool = True) -> None:
        """Restore system Workflow/Agent targets without creating assistant_skill rows.

        Plan 10 B2 dropped the legacy skill table. System catalog warm still needs
        the underlying workflow/agent baselines that used to be created as skill
        targets (e.g. ``general_chat__agent``).
        """
        system_skills = SkillRegistry.list_system_skills(locale=self._current_locale())
        if not system_skills:
            return

        for attempt in range(2):
            changed = False
            try:
                for s in system_skills:
                    target_type = self._derive_target_type(
                        langgraph_pattern=getattr(s, "langgraph_pattern", None),
                    )
                    if target_type == "workflow":
                        workflow_input = self._workflow_input_from_skill_default(s)
                        workflow_input = self._resolve_system_workflow_asset_references(
                            workflow_input=workflow_input
                        )
                        # Synthetic skill shell for naming helpers only.
                        skill_shell = SimpleNamespace(
                            name=s.name,
                            description=s.description or "",
                            is_system=True,
                            enabled=True,
                            workflow_id=None,
                            workflow=None,
                            agent_profile_id=None,
                            agent_profile=None,
                        )
                        workflow_model = self._resolve_or_create_system_workflow_for_reset(
                            skill=skill_shell,
                            default=s,
                            enabled=True,
                        )
                        self._enforce_workflow_structured_input_constraints(
                            workflow=workflow_model,
                            workflow_input=workflow_input,
                            raise_error=True,
                        )
                        if self._ensure_system_workflow_baseline_state(
                            workflow=workflow_model,
                            workflow_input=workflow_input,
                            description=s.description or "",
                            enabled=True,
                        ):
                            changed = True
                    else:
                        skill_shell = SimpleNamespace(
                            name=s.name,
                            description=s.description or "",
                            is_system=True,
                            enabled=True,
                            workflow_id=None,
                            workflow=None,
                            agent_profile_id=None,
                            agent_profile=None,
                        )
                        agent_profile = self._resolve_or_create_system_agent_profile_for_reset(
                            skill=skill_shell,
                            default=s,
                            enabled=True,
                        )
                        draft = self._agent_draft_from_skill_default(s)
                        if self._ensure_system_agent_baseline_state(
                            agent_profile=agent_profile,
                            draft=draft,
                            description=s.description or "",
                            enabled=True,
                        ):
                            changed = True

                if changed and commit:
                    self.db.commit()
                elif not commit:
                    self.db.flush()
                return
            except IntegrityError as exc:
                self.db.rollback()
                if attempt == 0:
                    self.db.expire_all()
                    continue
                raise ApiException(
                    status_code=409,
                    code=40920,
                    message="Sync system skills failed",
                ) from exc

    def list_tools(self, sync_system: bool = False, include_disabled: bool = False) -> list[AssistantTool]:
        # Deprecated compatibility flag: reads are now always side-effect free.
        _ = sync_system
        # 仅返回自定义（remote）工具；系统工具定义不落库
        q = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.kind == "remote")
            .order_by(AssistantTool.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantTool.enabled.is_(True))
        return q.all()

    def set_system_tool_enabled(self, name: str, enabled: bool) -> None:
        """设置系统工具 enabled 覆盖（默认启用；禁用才落库）。"""
        system = ToolRegistry.list_system_tools()
        system_names = {t.name for t in system if getattr(t, "name", None)}
        if name not in system_names:
            raise ApiException(status_code=404, code=40413, message=f"System tool not found: {name}")

        record = self.db.query(AssistantTool).filter(AssistantTool.name == name).first()

        # 启用：删除覆盖（恢复默认 True）
        if enabled:
            if record is not None:
                # 避免误删同名 remote 工具（理论上不应发生）
                if (record.kind or "").lower() == "remote" and not record.is_system:
                    raise ApiException(status_code=409, code=40914, message=f"Tool name conflict: {name}")
                self.db.delete(record)
            self.db.commit()
            return

        # 禁用：写入/更新覆盖记录
        if record is None:
            self.db.add(
                AssistantTool(
                    name=name,
                    description=None,
                    kind="local",
                    is_system=True,
                    enabled=False,
                )
            )
        else:
            if (record.kind or "").lower() == "remote" and not record.is_system:
                raise ApiException(status_code=409, code=40914, message=f"Tool name conflict: {name}")
            record.kind = "local"
            record.is_system = True
            record.enabled = False
            # 不落库定义信息
            record.description = None
            record.input_params = None
            record.endpoint_url = None
            record.http_method = None
            record.headers = None
            record.query_params = None
            record.body_type = None
            record.body_content = None
            record.auth_type = None
            record.auth_header_name = None
            record.auth_scheme = None
            record.api_key_encrypted = None
            record.api_key_hint = None
            record.timeout_seconds = None
            record.payload_wrapper = None

        self.db.commit()

    def get_tool(self, id: UUID) -> AssistantTool:
        tool = self.db.query(AssistantTool).filter(AssistantTool.id == id).first()
        if not tool:
            raise ApiException(status_code=404, code=40410, message=f"Tool not found: {id}")
        return tool

    def create_tool(self, request: AssistantToolCreateRequest) -> AssistantTool:
        if request.kind == "local":
            raise ApiException(status_code=400, code=40010, message="kind=local is reserved for system tools")

        existing = self.db.query(AssistantTool).filter(AssistantTool.name.ilike(request.name)).first()
        if existing:
            raise ApiException(status_code=400, code=40011, message=f"Tool name already exists: {request.name}")

        encrypted = None
        hint = None
        if request.api_key:
            try:
                encrypted = encrypt_api_key(request.api_key)
                hint = api_key_hint(request.api_key)
            except Exception as exc:
                raise ApiException(status_code=500, code=50010, message="Encryption key not configured") from exc

        tool = AssistantTool(
            name=request.name,
            description=request.description,
            kind="remote",
            is_system=False,
            enabled=request.enabled,
            input_params=[p.model_dump() for p in request.input_params] if request.input_params else None,
            endpoint_url=request.endpoint_url,
            http_method=request.http_method,
            headers=request.headers,
            query_params=request.query_params,
            body_type=request.body_type,
            body_content=request.body_content,
            auth_type=request.auth_type,
            auth_header_name=request.auth_header_name,
            auth_scheme=request.auth_scheme,
            api_key_encrypted=encrypted,
            api_key_hint=hint,
            timeout_seconds=request.timeout_seconds,
            payload_wrapper=request.payload_wrapper,
            config_revision=1,
        )
        self.db.add(tool)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40912, message="Create tool failed") from exc
        self.db.refresh(tool)
        return tool

    def update_tool(self, id: UUID, request: AssistantToolUpdateRequest) -> AssistantTool:
        from app.assistant.skills.resolution import (
            execution_sensitive_changed,
            find_skill_refs_for_tool,
            skill_reference_conflict,
            tool_execution_sensitive_payload,
        )

        tool = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.id == id)
            .with_for_update()
            .first()
        )
        if not tool:
            raise ApiException(status_code=404, code=40410, message=f"Tool not found: {id}")

        if tool.is_system:
            # 系统工具只允许修改 enabled
            if request.enabled is not None:
                tool.enabled = request.enabled
            else:
                raise ApiException(status_code=400, code=40012, message="System tool can only update enabled")
        else:
            before = tool_execution_sensitive_payload(tool)
            if request.name is not None and request.name != tool.name:
                refs = find_skill_refs_for_tool(self.db, tool.id)
                if refs:
                    package_id, version_id = refs[0]
                    raise skill_reference_conflict(
                        package_id=package_id,
                        version_id=version_id,
                        message=(
                            "Remote tool is referenced by a published skill binding; "
                            "rename requires republish or explicit conflict resolution"
                        ),
                    )
                tool.name = request.name
            if request.description is not None:
                tool.description = request.description
            if request.enabled is not None:
                tool.enabled = request.enabled
            if request.input_params is not None:
                tool.input_params = [p.model_dump() for p in request.input_params]
            if request.endpoint_url is not None:
                tool.endpoint_url = request.endpoint_url
            if request.http_method is not None:
                tool.http_method = request.http_method
            if request.headers is not None:
                tool.headers = request.headers
            if request.query_params is not None:
                tool.query_params = request.query_params
            if request.body_type is not None:
                tool.body_type = request.body_type
            if request.body_content is not None:
                tool.body_content = request.body_content
            if request.auth_type is not None:
                tool.auth_type = request.auth_type
            if request.auth_header_name is not None:
                tool.auth_header_name = request.auth_header_name
            if request.auth_scheme is not None:
                tool.auth_scheme = request.auth_scheme
            if request.timeout_seconds is not None:
                tool.timeout_seconds = request.timeout_seconds
            if request.payload_wrapper is not None:
                tool.payload_wrapper = request.payload_wrapper
            if request.api_key is not None:
                tool.api_key_encrypted = encrypt_api_key(request.api_key)
                tool.api_key_hint = api_key_hint(request.api_key)
            after = tool_execution_sensitive_payload(tool)
            if execution_sensitive_changed(before, after):
                tool.config_revision = int(tool.config_revision or 1) + 1

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40913, message="Update tool failed") from exc
        self.db.refresh(tool)
        return tool

    def delete_tool(self, id: UUID) -> None:
        from app.assistant.skills.resolution import find_skill_refs_for_tool, skill_reference_conflict

        tool = self.get_tool(id)
        if tool.is_system:
            raise ApiException(status_code=400, code=40013, message="System tool cannot be deleted")
        refs = find_skill_refs_for_tool(self.db, tool.id)
        if refs:
            package_id, version_id = refs[0]
            raise skill_reference_conflict(
                package_id=package_id,
                version_id=version_id,
                message="Remote tool is referenced by a published skill binding/dependency",
            )
        try:
            self.db.delete(tool)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40994,
                message="Remote tool is referenced by a published skill binding/dependency",
            ) from exc

    def validate_workflow_dependencies(
        self,
        workflow,
        *,
        current_workflow_id: UUID | None = None,
    ) -> set[str]:
        """Validate workflow external dependencies (tools/models) before persistence."""
        if current_workflow_id is not None:
            cycle_error = self._detect_workflow_call_cycle(
                current_workflow_id=current_workflow_id,
                current_workflow_input=workflow,
            )
            if cycle_error:
                raise ApiException(
                    status_code=422,
                    code=42258,
                    message=cycle_error,
                )

        workflow_tool_names, custom_model_ids = self._collect_transitive_workflow_dependency_sets(
            workflow_input=workflow,
            current_workflow_id=current_workflow_id,
        )
        self._validate_workflow_tool_names(workflow_tool_names)
        self._validate_workflow_model_ids(custom_model_ids)
        return workflow_tool_names

    @staticmethod
    def _collect_workflow_tool_names(workflow_nodes: list) -> set[str]:
        return pure_collect_workflow_tool_names(workflow_nodes)

    @staticmethod
    def _collect_workflow_custom_model_ids(workflow_nodes: list) -> set[UUID]:
        return pure_collect_workflow_custom_model_ids(workflow_nodes)

    def _validate_workflow_tool_names(self, tool_names: set[str]) -> None:
        if not tool_names:
            return

        disabled_names = {
            name
            for name, in self.db.query(AssistantTool.name).filter(AssistantTool.enabled.is_(False)).all()
            if name
        }
        enabled_remote_names = {
            name
            for name, in self.db.query(AssistantTool.name).filter(
                AssistantTool.kind == "remote",
                AssistantTool.enabled.is_(True),
            ).all()
            if name
        }

        unavailable: list[str] = []
        for tool_name in sorted(tool_names):
            if tool_name in disabled_names:
                unavailable.append(f"{tool_name} (disabled)")
                continue
            if ToolRegistry.has_system_tool(tool_name) or tool_name in enabled_remote_names:
                continue
            unavailable.append(f"{tool_name} (not found)")

        if unavailable:
            raise ApiException(
                status_code=422,
                code=42203,
                message=f"Workflow references unavailable tools: {', '.join(unavailable)}",
            )

    def _validate_workflow_model_ids(self, model_ids: set[UUID]) -> None:
        if not model_ids:
            return

        rows = (
            self.db.query(AiModel.id, AiModel.model_type)
            .filter(AiModel.id.in_(tuple(model_ids)))
            .all()
        )
        found: dict[UUID, str] = {mid: str(model_type or "") for mid, model_type in rows}

        missing = sorted(str(mid) for mid in model_ids if mid not in found)
        type_mismatch = sorted(
            str(mid)
            for mid, model_type in found.items()
            if model_type.strip() != "llm"
        )

        if missing or type_mismatch:
            parts: list[str] = []
            if missing:
                parts.append(f"not found: {', '.join(missing)}")
            if type_mismatch:
                parts.append(f"not llm: {', '.join(type_mismatch)}")
            raise ApiException(
                status_code=422,
                code=42207,
                message=f"Workflow references invalid node models ({'; '.join(parts)})",
            )

    def _validate_agent_tool_names(self, tool_names: list[str] | None) -> None:
        requested = {str(item).strip() for item in (tool_names or []) if str(item).strip()}
        if not requested:
            return

        enabled_remote_names = {
            name
            for name, in self.db.query(AssistantTool.name).filter(
                AssistantTool.kind == "remote",
                AssistantTool.enabled.is_(True),
            ).all()
            if name
        }
        disabled_names = {
            name
            for name, in self.db.query(AssistantTool.name).filter(AssistantTool.enabled.is_(False)).all()
            if name
        }

        unavailable: list[str] = []
        for tool_name in sorted(requested):
            if tool_name in disabled_names:
                unavailable.append(f"{tool_name} (disabled)")
                continue
            if ToolRegistry.has_system_tool(tool_name) or tool_name in enabled_remote_names:
                continue
            unavailable.append(f"{tool_name} (not found)")

        if unavailable:
            raise ApiException(
                status_code=422,
                code=42246,
                message=f"Agent references unavailable tools: {', '.join(unavailable)}",
            )

    # -------------------------
    # Target Folders CRUD
    # -------------------------
    def list_target_folders(self) -> list[dict[str, Any]]:
        return [
            AssistantTargetFolderResponse.model_validate(item).model_dump()
            for item in self._compute_target_folder_payloads()
        ]

    def create_target_folder(self, request: AssistantTargetFolderCreateRequest) -> dict[str, Any]:
        self._acquire_target_folder_mutation_lock()
        parent = self._assert_target_folder_parent_valid(folder_id=None, parent_id=request.parent_id)
        normalized_name = self._normalize_target_folder_name(request.name)
        self._ensure_target_folder_name_available(normalized_name, parent_id=parent.id if parent else None)
        color_token, icon_key = self._normalize_target_folder_style(request.color_token, request.icon_key)
        folder = AssistantTargetFolder(
            name=normalized_name,
            description=request.description or "",
            parent_id=parent.id if parent else None,
            color_token=color_token,
            icon_key=icon_key,
        )
        self.db.add(folder)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40965, message="Create target folder failed") from exc
        return self._serialize_target_folder(self._get_target_folder(folder.id))

    def update_target_folder(self, folder_id: UUID, request: AssistantTargetFolderUpdateRequest) -> dict[str, Any]:
        self._acquire_target_folder_mutation_lock()
        folder = self._get_target_folder(folder_id)
        fields_set = request.model_fields_set
        next_parent_id = request.parent_id if "parent_id" in fields_set else folder.parent_id
        parent = self._assert_target_folder_parent_valid(folder_id=folder.id, parent_id=next_parent_id)
        if request.name is not None:
            normalized_name = self._normalize_target_folder_name(request.name)
            self._ensure_target_folder_name_available(
                normalized_name,
                parent_id=parent.id if parent else None,
                exclude_id=folder.id,
            )
            folder.name = normalized_name
        elif "parent_id" in fields_set and request.parent_id != folder.parent_id:
            self._ensure_target_folder_name_available(folder.name, parent_id=parent.id if parent else None, exclude_id=folder.id)
        if request.description is not None:
            folder.description = request.description
        if "parent_id" in fields_set and request.parent_id != folder.parent_id:
            folder.parent_id = parent.id if parent else None
        if request.color_token is not None or request.icon_key is not None:
            color_token, icon_key = self._normalize_target_folder_style(
                request.color_token if request.color_token is not None else folder.color_token,
                request.icon_key if request.icon_key is not None else folder.icon_key,
            )
            folder.color_token = color_token
            folder.icon_key = icon_key
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40966, message="Update target folder failed") from exc
        return self._serialize_target_folder(self._get_target_folder(folder.id))

    def delete_target_folder(self, folder_id: UUID) -> None:
        self._acquire_target_folder_mutation_lock()
        folder = self._get_target_folder(folder_id)
        child_folders = self.db.query(AssistantTargetFolder).filter(AssistantTargetFolder.parent_id == folder.id).all()
        for child in child_folders:
            child.name = self._next_available_target_folder_name(
                child.name,
                parent_id=folder.parent_id,
                exclude_id=child.id,
            )
            child.parent_id = folder.parent_id
        self.db.query(AssistantWorkflow).filter(AssistantWorkflow.folder_id == folder.id).update(
            {AssistantWorkflow.folder_id: folder.parent_id},
            synchronize_session=False,
        )
        self.db.query(AssistantAgentProfile).filter(AssistantAgentProfile.folder_id == folder.id).update(
            {AssistantAgentProfile.folder_id: folder.parent_id},
            synchronize_session=False,
        )
        # Flush reparent updates before deleting the folder. With
        # parent_id ON DELETE SET NULL, SQLite/Postgres apply the FK action on
        # delete and would otherwise wipe the just-written parent pointers if
        # the UPDATE and DELETE land in one statement batch without an intermediate flush.
        self.db.flush()
        self.db.delete(folder)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40967, message="Delete target folder failed") from exc

    def move_target_to_folder(self, request: AssistantTargetMoveRequest) -> None:
        self._acquire_target_folder_mutation_lock()
        self._validate_folder_assignment(request.folder_id)
        if request.target_type == "workflow":
            workflow = self.get_workflow(request.target_id)
            workflow.folder_id = request.folder_id
        else:
            profile = self.get_agent_profile(request.target_id)
            profile.folder_id = request.folder_id
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40968, message="Move target failed") from exc

    def move_target_folder(self, request: AssistantFolderMoveRequest) -> None:
        self._acquire_target_folder_mutation_lock()
        folder = self._get_target_folder(request.folder_id)
        parent = self._assert_target_folder_parent_valid(folder_id=folder.id, parent_id=request.parent_id)
        self._ensure_target_folder_name_available(folder.name, parent_id=parent.id if parent else None, exclude_id=folder.id)
        folder.parent_id = parent.id if parent else None
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40970, message="Move folder failed") from exc

    # -------------------------
    # Workflows CRUD
    # -------------------------
    def _list_workflows_query(self, *, include_disabled: bool) -> list[AssistantWorkflow]:
        q = (
            self.db.query(AssistantWorkflow)
            .options(
                joinedload(AssistantWorkflow.system_behavior_bindings),
            )
            .order_by(AssistantWorkflow.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantWorkflow.enabled.is_(True))
        return q.all()

    def list_workflows(self, include_disabled: bool = False) -> list[AssistantWorkflow]:
        workflows = self._list_workflows_query(include_disabled=include_disabled)
        self._attach_openclaw_reference_counts(workflows=workflows)
        return workflows

    def get_workflow(self, workflow_id: UUID) -> AssistantWorkflow:
        workflow = (
            self.db.query(AssistantWorkflow)
            .populate_existing()
            .options(
                joinedload(AssistantWorkflow.draft_version),
                joinedload(AssistantWorkflow.published_version),
                joinedload(AssistantWorkflow.system_behavior_bindings),
            )
            .filter(AssistantWorkflow.id == workflow_id)
            .first()
        )
        if workflow is None:
            raise ApiException(status_code=404, code=40430, message=f"Workflow not found: {workflow_id}")
        self._attach_openclaw_reference_counts(workflows=[workflow])
        return workflow

    def _create_workflow_entity(self, request: AssistantWorkflowCreateRequest) -> AssistantWorkflow:
        if self._workflow_name_exists(request.name):
            raise ApiException(status_code=400, code=40030, message=f"Workflow name exists: {request.name}")
        workflow_input = request.workflow or self._build_default_workflow_input()
        folder = self._validate_folder_assignment(request.folder_id)
        workflow = AssistantWorkflow(
            name=request.name,
            description=request.description or "",
            workflow_version=0,
            workflow_viewport=None,
            is_system=False,
            enabled=request.enabled,
            folder_id=folder.id if folder else None,
        )
        self.db.add(workflow)
        self.db.flush()
        self._apply_workflow_to_workflow_entity(workflow, workflow_input)
        published = self._create_workflow_version(
            workflow=workflow,
            workflow_input=workflow_input,
            version_source="publish",
            version_name=None,
        )
        workflow.draft_version_id = published.id
        workflow.published_version_id = published.id
        self._trim_workflow_versions(workflow)
        return workflow

    def _copy_workflow_entity(
        self,
        source_workflow: AssistantWorkflow,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
    ) -> AssistantWorkflow:
        name = name_override
        if name is None:
            base_name = self._display_workflow_name(source_workflow) or source_workflow.name
            name = self._next_available_copy_name(base_name, exists=self._workflow_name_exists)

        workflow_input = (
            self._resolve_system_workflow_baseline_input(source_workflow)
            if source_workflow.is_system
            else None
        ) or self._get_workflow_draft_input(source_workflow)
        description = source_workflow.description or ""
        if description_override is not None:
            description = description_override
        return self._create_workflow_entity(
            AssistantWorkflowCreateRequest(
                name=name,
                description=description,
                enabled=bool(source_workflow.enabled),
                folder_id=source_workflow.folder_id,
                workflow=workflow_input,
            )
        )

    def create_workflow(self, request: AssistantWorkflowCreateRequest) -> AssistantWorkflow:
        workflow = self._create_workflow_entity(request)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40930, message="Create workflow failed") from exc
        return self.get_workflow(workflow.id)

    def copy_workflow(self, workflow_id: UUID) -> AssistantWorkflow:
        source_workflow = self.get_workflow(workflow_id)
        workflow = self._copy_workflow_entity(source_workflow)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40936, message="Copy workflow failed") from exc
        return self.get_workflow(workflow.id)

    def update_workflow_entity(self, workflow_id: UUID, request: AssistantWorkflowUpdateRequest) -> AssistantWorkflow:
        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        if request.name is not None:
            workflow.name = request.name
        if request.description is not None:
            workflow.description = request.description
        if request.enabled is not None:
            workflow.enabled = request.enabled
        if "folder_id" in request.model_fields_set:
            folder = self._validate_folder_assignment(request.folder_id)
            workflow.folder_id = folder.id if folder else None
        if request.workflow is not None:
            self._enforce_workflow_structured_input_constraints(
                workflow=workflow,
                workflow_input=request.workflow,
                raise_error=True,
            )
            self._apply_workflow_to_workflow_entity(workflow, request.workflow, persist=False)
            saved = self._create_workflow_version(
                workflow=workflow,
                workflow_input=request.workflow,
                version_source="save",
                version_name=None,
            )
            workflow.draft_version_id = saved.id
            self._trim_workflow_versions(workflow)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40931, message="Update workflow failed") from exc
        return self.get_workflow(workflow.id)

    def list_workflow_versions(self, workflow_id: UUID) -> WorkflowVersionListResponse:
        workflow = self.get_workflow(workflow_id)
        versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .order_by(AssistantWorkflowVersion.sequence_no.desc())
            .all()
        )
        return WorkflowVersionListResponse.model_validate(
            {
                "workflow_id": workflow.id,
                "draft_version_id": workflow.draft_version_id,
                "published_version_id": workflow.published_version_id,
                "versions": [
                    TargetVersionResponse.model_validate(v).model_dump()
                    for v in versions
                ],
            }
        )

    def publish_workflow(self, workflow_id: UUID, request: WorkflowPublishRequest) -> AssistantWorkflow:
        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        if request.description is not None:
            workflow.description = request.description
        self._enforce_workflow_structured_input_constraints(
            workflow=workflow,
            workflow_input=request.workflow,
            raise_error=True,
        )
        try:
            self._apply_workflow_to_workflow_entity(workflow, request.workflow, persist=True)
        except ApiException as exc:
            if exc.status_code == 422:
                raise ApiException(
                    status_code=422,
                    code=42209,
                    message=f"Workflow publish blocked by validation: {exc.message}",
                ) from exc
            raise
        published = self._create_workflow_version(
            workflow=workflow,
            workflow_input=request.workflow,
            version_source="publish",
            version_name=request.version_name,
        )
        workflow.draft_version_id = published.id
        workflow.published_version_id = published.id
        self._trim_workflow_versions(workflow)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40937, message="Publish workflow failed") from exc
        published_workflow = self.get_workflow(workflow.id)
        return published_workflow

    def rollback_workflow_version(self, workflow_id: UUID, version_id: UUID) -> RollbackVersionResponse:
        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        version = (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.id == version_id,
                AssistantWorkflowVersion.workflow_id == workflow.id,
            )
            .first()
        )
        if version is None:
            raise ApiException(status_code=404, code=40432, message=f"Workflow version not found: {version_id}")

        draft_input: WorkflowInput | None = None
        if workflow.is_system:
            baseline_version_id = self._get_workflow_system_baseline_version_id(workflow.id)
            if baseline_version_id is not None and baseline_version_id == version.id:
                canonical_baseline = self._resolve_system_workflow_baseline_input(workflow)
                if canonical_baseline is not None:
                    draft_input = canonical_baseline
                    version.snapshot = self._workflow_input_to_snapshot(canonical_baseline)

        try:
            if draft_input is None:
                draft_input = self._workflow_input_from_snapshot(
                    version.snapshot if isinstance(version.snapshot, dict) else {}
                )
        except Exception as exc:
            raise ApiException(status_code=422, code=42208, message="Invalid workflow version snapshot") from exc
        workflow.draft_version_id = version.id
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40938, message="Rollback workflow version failed") from exc
        return RollbackVersionResponse.model_validate(
            {
                "draft_version_id": workflow.draft_version_id,
                "published_version_id": workflow.published_version_id,
                "workflow": draft_input.model_dump(),
            }
        )

    def delete_workflow_version(self, workflow_id: UUID, version_id: UUID) -> DeleteVersionResponse:
        from app.assistant.skills.resolution import (
            find_skill_refs_for_workflow_version,
            skill_reference_conflict,
        )

        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        version = (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.id == version_id,
                AssistantWorkflowVersion.workflow_id == workflow.id,
            )
            .first()
        )
        if version is None:
            raise ApiException(status_code=404, code=40434, message=f"Workflow version not found: {version_id}")

        skill_refs = find_skill_refs_for_workflow_version(self.db, version.id)
        if skill_refs:
            package_id, skill_version_id = skill_refs[0]
            raise skill_reference_conflict(
                package_id=package_id,
                version_id=skill_version_id,
                message="Workflow version is referenced by a published skill binding/dependency",
            )

        pinned_refs = self._workflow_call_referrers_for_workflow_version(version.id)
        if pinned_refs:
            raise ApiException(
                status_code=409,
                code=40963,
                message="Workflow version is referenced by workflow_call nodes",
                details={
                    "targetType": "workflow_version",
                    "targetId": str(version.id),
                    "targetWorkflowId": str(workflow.id),
                    "targetWorkflowName": self._display_workflow_name(workflow),
                    "references": self._serialize_workflow_call_references(pinned_refs),
                },
            )

        protected_ids = self._get_workflow_protected_version_ids(workflow)
        if version.id in protected_ids:
            raise ApiException(
                status_code=409,
                code=40941,
                message="Protected workflow version cannot be deleted",
            )

        self.db.delete(version)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40942, message="Delete workflow version failed") from exc

        return DeleteVersionResponse.model_validate(
            {
                "deleted_version_id": version_id,
                "draft_version_id": workflow.draft_version_id,
                "published_version_id": workflow.published_version_id,
            }
        )

    def clear_workflow_versions(self, workflow_id: UUID) -> ClearVersionsResponse:
        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == workflow.id)
            .order_by(AssistantWorkflowVersion.sequence_no.desc())
            .all()
        )
        latest_version_id = versions[0].id if versions else None
        protected_ids = self._get_workflow_protected_version_ids(workflow)
        deleted_count = 0

        for version in versions:
            if latest_version_id is not None and version.id == latest_version_id:
                continue
            if version.version_source != "save":
                continue
            if version.id in protected_ids:
                continue
            self.db.delete(version)
            deleted_count += 1

        if deleted_count > 0:
            try:
                self.db.commit()
            except IntegrityError as exc:
                self.db.rollback()
                raise ApiException(status_code=409, code=40943, message="Clear workflow versions failed") from exc

        return ClearVersionsResponse.model_validate(
            {
                "deleted_count": deleted_count,
                "kept_latest_version_id": latest_version_id,
                "draft_version_id": workflow.draft_version_id,
                "published_version_id": workflow.published_version_id,
            }
        )

    def delete_workflow(self, workflow_id: UUID, *, confirm_rebind_system_behaviors: bool = False) -> None:
        from app.assistant.skills.resolution import find_skill_refs_for_workflow, skill_reference_conflict

        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        skill_refs = find_skill_refs_for_workflow(self.db, workflow.id)
        if skill_refs:
            package_id, skill_version_id = skill_refs[0]
            raise skill_reference_conflict(
                package_id=package_id,
                version_id=skill_version_id,
                message="Workflow is referenced by a published skill binding/dependency",
            )
        workflow_call_refs = self._workflow_call_referrers_for_workflow(workflow.id)
        if workflow_call_refs:
            raise ApiException(
                status_code=409,
                code=40964,
                message="Workflow is referenced by workflow_call nodes",
                details={
                    "targetType": "workflow",
                    "targetId": str(workflow.id),
                    "targetName": self._display_workflow_name(workflow),
                    "references": self._serialize_workflow_call_references(workflow_call_refs),
                },
            )
        if False:  # assistant_skill dropped
            skill_names = ""  # assistant_skill dropped
            raise ApiException(
                status_code=409,
                code=40932,
                message=f"Workflow is referenced by skills: {skill_names}",
            )
        behavior_keys = self._binding_keys_from_relationship(getattr(workflow, "system_behavior_bindings", None))
        if behavior_keys and not confirm_rebind_system_behaviors:
            raise ApiException(
                status_code=409,
                code=40961,
                message="Workflow is referenced by system AI behaviors",
                details={
                    "targetType": "workflow",
                    "targetId": str(workflow.id),
                    "targetName": self._display_workflow_name(workflow),
                    "referencedSystemBehaviorKeys": behavior_keys,
                    "action": "confirm_rebind_then_delete",
                },
            )
        if behavior_keys:
            self._rebind_system_behaviors_to_defaults(behavior_keys)
        self.db.delete(workflow)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40962, message="Delete workflow failed") from exc

    def update_workflow_by_id(self, workflow_id: UUID, workflow: WorkflowInput) -> AssistantWorkflow:
        workflow_model = self.get_workflow(workflow_id)
        self.update_workflow_entity(
            workflow_model.id,
            AssistantWorkflowUpdateRequest(workflow=workflow),
        )
        return self.get_workflow(workflow_model.id)

    # -------------------------
    # Agent Profiles CRUD
    # -------------------------
    def list_agent_profiles(self, include_disabled: bool = False) -> list[AssistantAgentProfile]:
        q = (
            self.db.query(AssistantAgentProfile)
            .options(
                joinedload(AssistantAgentProfile.system_behavior_bindings),
            )
            .order_by(AssistantAgentProfile.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantAgentProfile.enabled.is_(True))
        profiles = q.all()
        self._attach_openclaw_reference_counts(agent_profiles=profiles)
        return profiles

    def get_agent_profile(self, agent_profile_id: UUID) -> AssistantAgentProfile:
        profile = (
            self.db.query(AssistantAgentProfile)
            .options(
                joinedload(AssistantAgentProfile.system_behavior_bindings),
            )
            .filter(AssistantAgentProfile.id == agent_profile_id)
            .first()
        )
        if profile is None:
            raise ApiException(status_code=404, code=40431, message=f"Agent profile not found: {agent_profile_id}")
        self._attach_openclaw_reference_counts(agent_profiles=[profile])
        return profile

    def create_agent_profile(self, request: AssistantAgentProfileCreateRequest) -> AssistantAgentProfile:
        existing = self.db.query(AssistantAgentProfile).filter(AssistantAgentProfile.name.ilike(request.name)).first()
        if existing:
            raise ApiException(status_code=400, code=40032, message=f"Agent profile name exists: {request.name}")
        folder = self._validate_folder_assignment(request.folder_id)
        normalized_kb_config = self._normalize_agent_kb_config(
            kb_config=request.kb_config,
            model_source=request.model_source,
            model_id=request.model_id,
            existing_kb_config={"enabled": False},
        )
        self._validate_agent_tool_names(request.tools)
        profile = AssistantAgentProfile(
            name=request.name,
            description=request.description or "",
            system_prompt=request.system_prompt,
            tools=request.tools or [],
            kb_config=normalized_kb_config,
            is_system=False,
            enabled=request.enabled,
            folder_id=folder.id if folder else None,
        )
        self.db.add(profile)
        self.db.flush()
        publish_draft = AgentPublishDraftInput.model_validate(
            {
                "system_prompt": request.system_prompt,
                "tools": request.tools or [],
                "kb_config": normalized_kb_config,
                "model_source": request.model_source,
                "model_id": request.model_id,
            }
        )
        published = self._create_agent_profile_version(
            agent_profile=profile,
            draft=publish_draft,
            version_source="publish",
            version_name=None,
        )
        profile.draft_version_id = published.id
        profile.published_version_id = published.id
        self._trim_agent_versions(profile)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40933, message="Create agent profile failed") from exc
        return self.get_agent_profile(profile.id)

    def _copy_agent_profile_entity(
        self,
        source_profile: AssistantAgentProfile,
        *,
        name_override: str | None = None,
        description_override: str | None = None,
    ) -> AssistantAgentProfile:
        name = name_override
        if name is None:
            base_name = self._display_agent_profile_name(source_profile) or source_profile.name
            name = self._next_available_copy_name(base_name, exists=self._agent_profile_name_exists)

        draft = (
            self._resolve_system_agent_baseline_draft(source_profile)
            if source_profile.is_system
            else None
        ) or self._get_agent_profile_draft(source_profile)
        description = source_profile.description or ""
        if description_override is not None:
            description = description_override

        return self.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name=name,
                description=description,
                system_prompt=draft.system_prompt,
                tools=list(draft.tools or []),
                kb_config=draft.kb_config if isinstance(draft.kb_config, dict) else {"enabled": False},
                enabled=bool(source_profile.enabled),
                model_source=draft.model_source,
                model_id=draft.model_id,
                folder_id=source_profile.folder_id,
            )
        )

    def copy_agent_profile(self, agent_profile_id: UUID) -> AssistantAgentProfile:
        source_profile = self.get_agent_profile(agent_profile_id)
        return self._copy_agent_profile_entity(source_profile)

    def update_agent_profile(self, agent_profile_id: UUID, request: AssistantAgentProfileUpdateRequest) -> AssistantAgentProfile:
        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        if request.name is not None:
            profile.name = request.name
        if request.description is not None:
            profile.description = request.description
        if request.enabled is not None:
            profile.enabled = request.enabled
        if "folder_id" in request.model_fields_set:
            folder = self._validate_folder_assignment(request.folder_id)
            profile.folder_id = folder.id if folder else None

        has_runtime_update = any(
            value is not None
            for value in (request.system_prompt, request.tools, request.kb_config, request.model_source, request.model_id)
        )
        if has_runtime_update:
            current_draft = self._get_agent_profile_draft(profile)
            draft_payload = {
                "system_prompt": request.system_prompt if request.system_prompt is not None else current_draft.system_prompt,
                "tools": request.tools if request.tools is not None else current_draft.tools,
                "kb_config": request.kb_config if request.kb_config is not None else current_draft.kb_config,
                "model_source": request.model_source if request.model_source is not None else current_draft.model_source,
                "model_id": request.model_id if request.model_source is not None or request.model_id is not None else current_draft.model_id,
            }
            next_draft = AgentPublishDraftInput.model_validate(draft_payload)
            normalized_kb = self._normalize_agent_kb_config(
                kb_config=next_draft.kb_config,
                model_source=next_draft.model_source,
                model_id=next_draft.model_id,
                existing_kb_config=next_draft.kb_config if isinstance(next_draft.kb_config, dict) else {"enabled": False},
            )
            next_draft = AgentPublishDraftInput.model_validate(
                {
                    "system_prompt": next_draft.system_prompt,
                    "tools": next_draft.tools,
                    "kb_config": normalized_kb,
                    "model_source": next_draft.model_source,
                    "model_id": next_draft.model_id,
                }
            )
            self._validate_agent_tool_names(next_draft.tools)
            saved = self._create_agent_profile_version(
                agent_profile=profile,
                draft=next_draft,
                version_source="save",
                version_name=None,
            )
            profile.draft_version_id = saved.id
            self._trim_agent_versions(profile)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40934, message="Update agent profile failed") from exc
        return self.get_agent_profile(profile.id)

    def list_agent_profile_versions(self, agent_profile_id: UUID) -> AgentVersionListResponse:
        profile = self.get_agent_profile(agent_profile_id)
        versions = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(AssistantAgentProfileVersion.agent_profile_id == profile.id)
            .order_by(AssistantAgentProfileVersion.sequence_no.desc())
            .all()
        )
        return AgentVersionListResponse.model_validate(
            {
                "agent_profile_id": profile.id,
                "draft_version_id": profile.draft_version_id,
                "published_version_id": profile.published_version_id,
                "versions": [
                    TargetVersionResponse.model_validate(v).model_dump()
                    for v in versions
                ],
            }
        )

    def publish_agent_profile(self, agent_profile_id: UUID, request: AgentPublishRequest) -> AssistantAgentProfile:
        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        normalized_kb = self._normalize_agent_kb_config(
            kb_config=request.draft.kb_config,
            model_source=request.draft.model_source,
            model_id=request.draft.model_id,
            existing_kb_config=request.draft.kb_config if isinstance(request.draft.kb_config, dict) else {"enabled": False},
        )
        self._validate_agent_tool_names(request.draft.tools)
        profile.system_prompt = request.draft.system_prompt
        profile.tools = list(request.draft.tools or [])
        profile.kb_config = normalized_kb
        for skill in []:  # assistant_skill dropped
            if skill.agent_profile_id == profile.id:
                skill.system_prompt = profile.system_prompt
                skill.kb_config = profile.kb_config
                skill.tools = profile.tools or []

        publish_draft = AgentPublishDraftInput.model_validate(
            {
                "system_prompt": profile.system_prompt,
                "tools": profile.tools or [],
                "kb_config": profile.kb_config,
                "model_source": request.draft.model_source,
                "model_id": request.draft.model_id,
            }
        )
        published = self._create_agent_profile_version(
            agent_profile=profile,
            draft=publish_draft,
            version_source="publish",
            version_name=request.version_name,
        )
        profile.draft_version_id = published.id
        profile.published_version_id = published.id
        self._trim_agent_versions(profile)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40939, message="Publish agent profile failed") from exc
        published_profile = self.get_agent_profile(profile.id)
        return published_profile

    def rollback_agent_profile_version(self, agent_profile_id: UUID, version_id: UUID) -> RollbackVersionResponse:
        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        version = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.id == version_id,
                AssistantAgentProfileVersion.agent_profile_id == profile.id,
            )
            .first()
        )
        if version is None:
            raise ApiException(status_code=404, code=40433, message=f"Agent version not found: {version_id}")

        draft: AgentPublishDraftInput | None = None
        if profile.is_system:
            baseline_version_id = self._get_agent_system_baseline_version_id(profile.id)
            if baseline_version_id is not None and baseline_version_id == version.id:
                canonical_baseline = self._resolve_system_agent_baseline_draft(profile)
                if canonical_baseline is not None:
                    draft = canonical_baseline
                    normalized_kb = self._normalize_agent_kb_config(
                        kb_config=canonical_baseline.kb_config,
                        model_source=canonical_baseline.model_source,
                        model_id=canonical_baseline.model_id,
                        existing_kb_config=(
                            canonical_baseline.kb_config
                            if isinstance(canonical_baseline.kb_config, dict)
                            else {"enabled": False}
                        ),
                    )
                    version.snapshot = self._agent_snapshot_from_fields(
                        system_prompt=canonical_baseline.system_prompt,
                        tools=canonical_baseline.tools,
                        kb_config=normalized_kb,
                        model_source=canonical_baseline.model_source,
                        model_id=canonical_baseline.model_id,
                    )

        try:
            if draft is None:
                draft = self._agent_draft_from_snapshot(version.snapshot if isinstance(version.snapshot, dict) else {})
        except Exception as exc:
            raise ApiException(status_code=422, code=42247, message="Invalid agent version snapshot") from exc
        profile.draft_version_id = version.id
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40940, message="Rollback agent profile version failed") from exc
        return RollbackVersionResponse.model_validate(
            {
                "draft_version_id": profile.draft_version_id,
                "published_version_id": profile.published_version_id,
                "agent_draft": draft.model_dump(),
            }
        )

    def delete_agent_profile_version(self, agent_profile_id: UUID, version_id: UUID) -> DeleteVersionResponse:
        from app.assistant.skills.resolution import (
            find_skill_refs_for_agent_version,
            skill_reference_conflict,
        )

        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        version = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.id == version_id,
                AssistantAgentProfileVersion.agent_profile_id == profile.id,
            )
            .first()
        )
        if version is None:
            raise ApiException(status_code=404, code=40435, message=f"Agent version not found: {version_id}")

        skill_refs = find_skill_refs_for_agent_version(self.db, version.id)
        if skill_refs:
            package_id, skill_version_id = skill_refs[0]
            raise skill_reference_conflict(
                package_id=package_id,
                version_id=skill_version_id,
                message="Agent version is referenced by a published skill binding/dependency",
            )

        protected_ids = self._get_agent_protected_version_ids(profile)
        if version.id in protected_ids:
            raise ApiException(
                status_code=409,
                code=40944,
                message="Protected agent version cannot be deleted",
            )

        self.db.delete(version)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40945, message="Delete agent version failed") from exc

        return DeleteVersionResponse.model_validate(
            {
                "deleted_version_id": version_id,
                "draft_version_id": profile.draft_version_id,
                "published_version_id": profile.published_version_id,
            }
        )

    def clear_agent_profile_versions(self, agent_profile_id: UUID) -> ClearVersionsResponse:
        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        versions = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(AssistantAgentProfileVersion.agent_profile_id == profile.id)
            .order_by(AssistantAgentProfileVersion.sequence_no.desc())
            .all()
        )
        latest_version_id = versions[0].id if versions else None
        protected_ids = self._get_agent_protected_version_ids(profile)
        deleted_count = 0

        for version in versions:
            if latest_version_id is not None and version.id == latest_version_id:
                continue
            if version.version_source != "save":
                continue
            if version.id in protected_ids:
                continue
            self.db.delete(version)
            deleted_count += 1

        if deleted_count > 0:
            try:
                self.db.commit()
            except IntegrityError as exc:
                self.db.rollback()
                raise ApiException(status_code=409, code=40946, message="Clear agent versions failed") from exc

        return ClearVersionsResponse.model_validate(
            {
                "deleted_count": deleted_count,
                "kept_latest_version_id": latest_version_id,
                "draft_version_id": profile.draft_version_id,
                "published_version_id": profile.published_version_id,
            }
        )

    def delete_agent_profile(self, agent_profile_id: UUID, *, confirm_rebind_system_behaviors: bool = False) -> None:
        from app.assistant.skills.resolution import find_skill_refs_for_agent, skill_reference_conflict

        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        skill_refs = find_skill_refs_for_agent(self.db, profile.id)
        if skill_refs:
            package_id, skill_version_id = skill_refs[0]
            raise skill_reference_conflict(
                package_id=package_id,
                version_id=skill_version_id,
                message="Agent profile is referenced by a published skill binding/dependency",
            )
        if False:  # assistant_skill dropped
            skill_names = ""  # assistant_skill dropped
            raise ApiException(
                status_code=409,
                code=40935,
                message=f"Agent profile is referenced by skills: {skill_names}",
            )
        behavior_keys = self._binding_keys_from_relationship(getattr(profile, "system_behavior_bindings", None))
        if behavior_keys and not confirm_rebind_system_behaviors:
            raise ApiException(
                status_code=409,
                code=40963,
                message="Agent profile is referenced by system AI behaviors",
                details={
                    "targetType": "agent",
                    "targetId": str(profile.id),
                    "targetName": profile.name,
                    "referencedSystemBehaviorKeys": behavior_keys,
                    "action": "confirm_rebind_then_delete",
                },
            )
        if behavior_keys:
            self._rebind_system_behaviors_to_defaults(behavior_keys)
        self.db.delete(profile)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40964, message="Delete agent profile failed") from exc
