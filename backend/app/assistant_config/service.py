from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.ai_registry.models import AiModel
from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id
from app.ai_provider.crypto import api_key_hint, encrypt_api_key
from app.assistant_config.models import (
    AssistantAgentProfile,
    AssistantAgentProfileVersion,
    AssistantSkill,
    AssistantSkillEdge,
    AssistantSkillNode,
    AssistantSystemBehaviorBinding,
    AssistantWorkflow,
    AssistantWorkflowEdge,
    AssistantWorkflowNode,
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
    AssistantSkillCreateRequest,
    AssistantSkillUpdateRequest,
    AssistantToolCreateRequest,
    AssistantToolUpdateRequest,
    AssistantWorkflowCreateRequest,
    AssistantWorkflowUpdateRequest,
    ClearVersionsResponse,
    DeleteVersionResponse,
    RollbackVersionResponse,
    SystemBehaviorResponse,
    TargetType,
    TargetVersionResponse,
    WorkflowPublishRequest,
    WorkflowVersionListResponse,
    WorkflowInput,
)
from app.assistant_config.system_behavior_defaults_loader import get_system_behavior_default_workflow
from app.assistant_config.system_behavior_registry import (
    SystemBehaviorDefinition,
    SystemBehaviorFieldDefinition,
    get_system_behavior_definition,
    list_system_behavior_definitions,
)
from app.assistant.skill_catalog.defaults_loader import load_system_workflow_preset_file
from app.common.exceptions import ApiException
from app.system_settings.service import resolve_system_locale

_TOOL_TEXT_REF_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.text\s*\}\}")
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
_SYSTEM_SKILL_DISPLAY_NAMES: dict[str, dict[str, str]] = {
    "quick_stats": {"zh": "快速统计工作流", "en": "Quick Stats Workflow"},
    "smart_capture": {"zh": "智能创建记录工作流", "en": "Smart Capture Workflow"},
    "periodic_review": {"zh": "周期性回顾工作流", "en": "Periodic Review Workflow"},
    DEFAULT_SKILL_NAME: {"zh": "默认对话智能体", "en": "General Chat Agent"},
}


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
                "viewport": workflow.workflow_viewport,
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
        if self._resolve_start_input_mode(workflow_input) != "structured":
            return []
        if not workflow.skills:
            return []
        skill_names = ", ".join(sorted(skill.name for skill in workflow.skills))
        message = (
            "Structured-input workflow cannot be referenced by skills. "
            f"Current referenced skills: {skill_names}"
        )
        if raise_error:
            raise ApiException(status_code=409, code=40948, message=message)
        return [message]

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

        from app.assistant.skill_catalog.defaults_loader import get_system_workflow_baseline
        from app.openclaw_integration.registry import list_openclaw_system_item_definitions

        locale = self._current_locale()
        for linked_skill in (workflow.skills or []):
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            name = str(getattr(linked_skill, "name", "") or "").strip()
            if not name:
                continue
            baseline = get_system_workflow_baseline(name, locale=locale)
            if baseline is not None:
                return baseline

        workflow_name = str(workflow.name or "").strip()
        if workflow_name:
            for definition in list_system_behavior_definitions(locale=locale):
                if definition.default_target.target_type != "workflow":
                    continue
                if definition.default_target.canonical_name != workflow_name:
                    continue
                return get_system_behavior_default_workflow(definition, locale=locale)

            for definition in list_openclaw_system_item_definitions(locale=locale):
                if definition.source_type != "workflow":
                    continue
                if str(definition.workflow_canonical_name or "").strip() != workflow_name:
                    continue
                if not definition.workflow_preset_file:
                    continue
                return load_system_workflow_preset_file(definition.workflow_preset_file)
        return None

    def _resolve_system_agent_baseline_draft(self, agent_profile: AssistantAgentProfile) -> AgentPublishDraftInput | None:
        if not agent_profile.is_system:
            return None

        from app.assistant.skill_catalog.defaults_loader import get_system_agent_baseline

        locale = self._current_locale()
        for linked_skill in (agent_profile.skills or []):
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            name = str(getattr(linked_skill, "name", "") or "").strip()
            if not name:
                continue
            baseline = get_system_agent_baseline(name, locale=locale)
            if baseline is not None:
                return baseline
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

        if not bool(workflow.is_system):
            workflow.is_system = True
            changed = True
        if bool(workflow.enabled) != bool(enabled):
            workflow.enabled = bool(enabled)
            changed = True
        if (workflow.description or "") != normalized_description:
            workflow.description = normalized_description
            changed = True

        current_published = self._get_workflow_published_input(workflow)
        desired_snapshot = self._workflow_input_to_snapshot(workflow_input)
        current_snapshot = self._workflow_input_to_snapshot(current_published) if current_published is not None else None
        try:
            current_entity_snapshot = self._workflow_input_to_snapshot(self._to_workflow_input_from_entity(workflow))
        except Exception:
            current_entity_snapshot = None

        if current_snapshot != desired_snapshot or workflow.published_version_id is None:
            self._enforce_workflow_structured_input_constraints(
                workflow=workflow,
                workflow_input=workflow_input,
                raise_error=True,
            )
            self._apply_workflow_to_workflow_entity(workflow, workflow_input, persist=True)
            published = self._create_workflow_version(
                workflow=workflow,
                workflow_input=workflow_input,
                version_source="publish",
                version_name=version_name,
            )
            self._keep_only_workflow_version(workflow, published.id)
            return True

        if current_entity_snapshot != desired_snapshot:
            self._enforce_workflow_structured_input_constraints(
                workflow=workflow,
                workflow_input=workflow_input,
                raise_error=True,
            )
            self._apply_workflow_to_workflow_entity(workflow, workflow_input, persist=True)
            changed = True

        keep_version_id = workflow.published_version_id
        if keep_version_id is not None and (
            workflow.draft_version_id != keep_version_id
            or self._workflow_version_count(workflow.id) != 1
        ):
            self._keep_only_workflow_version(workflow, keep_version_id)
            changed = True

        return changed

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
            for linked_skill in agent_profile.skills or []:
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
        if workflow.is_system:
            baseline_id = self._get_workflow_system_baseline_version_id(workflow.id)
            if baseline_id is not None:
                protected_ids.add(baseline_id)
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

    def _serialize_workflow(self, workflow: AssistantWorkflow) -> dict[str, Any]:
        referenced_skill_ids = [s.id for s in (workflow.skills or [])]
        referenced_system_behavior_keys = self._binding_keys_from_relationship(
            getattr(workflow, "system_behavior_bindings", None)
        )
        draft_workflow = self._get_workflow_draft_input(workflow)
        ts = workflow.updated_at or workflow.created_at or self._utcnow()
        return {
            "id": workflow.id,
            "name": self._display_workflow_name(workflow),
            "description": workflow.description or "",
            "is_system": bool(workflow.is_system),
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
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
        }

    def serialize_workflow(self, workflow: AssistantWorkflow) -> dict[str, Any]:
        return self._serialize_workflow(workflow)

    def _serialize_agent_profile(self, agent_profile: AssistantAgentProfile) -> dict[str, Any]:
        referenced_skill_ids = [s.id for s in (agent_profile.skills or [])]
        referenced_system_behavior_keys = self._binding_keys_from_relationship(
            getattr(agent_profile, "system_behavior_bindings", None)
        )
        draft = self._get_agent_profile_draft(agent_profile)
        normalized_kb = dict(draft.kb_config or {})
        normalized_kb["model_source"] = draft.model_source
        normalized_kb["model_id"] = str(draft.model_id) if draft.model_id is not None else None
        model_source, model_id = self._read_agent_model_config(normalized_kb)
        return {
            "id": agent_profile.id,
            "name": self._display_agent_profile_name(agent_profile),
            "description": agent_profile.description or "",
            "system_prompt": draft.system_prompt,
            "tools": draft.tools or [],
            "kb_config": normalized_kb,
            "model_source": model_source,
            "model_id": model_id,
            "is_system": bool(agent_profile.is_system),
            "enabled": bool(agent_profile.enabled),
            "draft_version_id": agent_profile.draft_version_id,
            "published_version_id": agent_profile.published_version_id,
            "referenced_skill_ids": referenced_skill_ids,
            "reference_count": len(referenced_skill_ids),
            "referenced_system_behavior_keys": referenced_system_behavior_keys,
            "system_behavior_reference_count": len(referenced_system_behavior_keys),
            "created_at": agent_profile.created_at,
            "updated_at": agent_profile.updated_at,
        }

    def serialize_agent_profile(self, agent_profile: AssistantAgentProfile) -> dict[str, Any]:
        return self._serialize_agent_profile(agent_profile)

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

    def _display_workflow_name(self, workflow: AssistantWorkflow) -> str:
        raw_name = str(workflow.name or "").strip()
        if not raw_name or not bool(workflow.is_system):
            return raw_name
        locale = self._current_locale()
        for definition in list_system_behavior_definitions(locale=locale):
            if definition.default_target.target_type != "workflow":
                continue
            if definition.default_target.canonical_name == raw_name:
                return f"{definition.name}工作流" if locale == "zh" else f"{definition.name} Workflow"
        for linked_skill in (workflow.skills or []):
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            display = _SYSTEM_SKILL_DISPLAY_NAMES.get(str(getattr(linked_skill, "name", "") or "").strip(), {})
            localized = str(display.get(locale) or "").strip()
            if localized:
                return localized
        return raw_name

    def _display_agent_profile_name(self, agent_profile: AssistantAgentProfile) -> str:
        raw_name = str(agent_profile.name or "").strip()
        if not raw_name or not bool(agent_profile.is_system):
            return raw_name
        locale = self._current_locale()
        for linked_skill in (agent_profile.skills or []):
            if not bool(getattr(linked_skill, "is_system", False)):
                continue
            display = _SYSTEM_SKILL_DISPLAY_NAMES.get(str(getattr(linked_skill, "name", "") or "").strip(), {})
            localized = str(display.get(locale) or "").strip()
            if localized:
                return localized
        return raw_name

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

        normalized_kb = self._normalize_agent_kb_config(
            kb_config=published_draft.kb_config,
            model_source=published_draft.model_source,
            model_id=published_draft.model_id,
            existing_kb_config=published_draft.kb_config if isinstance(published_draft.kb_config, dict) else {"enabled": False},
        )
        self._validate_agent_tool_names(published_draft.tools)
        return AgentPublishDraftInput.model_validate(
            {
                "system_prompt": published_draft.system_prompt,
                "tools": published_draft.tools,
                "kb_config": normalized_kb,
                "model_source": published_draft.model_source,
                "model_id": published_draft.model_id,
            }
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
            .options(joinedload(AssistantWorkflow.system_behavior_bindings))
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

        workflow_input = get_system_behavior_default_workflow(definition, locale=locale)
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

    def _ensure_system_behavior_binding_entity(
        self,
        definition: SystemBehaviorDefinition,
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

        default_workflow = self._resolve_or_create_system_behavior_default_workflow(definition)
        binding = AssistantSystemBehaviorBinding(
            behavior_key=definition.key,
            target_type="workflow",
            workflow=default_workflow,
        )
        self.db.add(binding)
        self.db.flush()
        return binding

    def ensure_system_behaviors(self, *, commit: bool = True) -> None:
        changed = False
        locale = self._current_locale()
        for definition in list_system_behavior_definitions(locale=locale):
            if definition.default_target.target_type == "workflow":
                workflow, workflow_changed = self._ensure_system_behavior_default_workflow(definition)
                if workflow_changed or workflow.published_version_id is None:
                    changed = True
            binding_before = self._get_system_behavior_binding(definition.key)
            binding = self._ensure_system_behavior_binding_entity(definition)
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
        self.ensure_system_behaviors()
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
        self.ensure_system_behaviors()
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
        self.ensure_system_behaviors()
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
        self.ensure_system_behaviors()
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

    def serialize_skill(self, skill: AssistantSkill) -> dict[str, Any]:
        target_type = self._derive_target_type(
            workflow_id=skill.workflow_id,
            agent_profile_id=skill.agent_profile_id,
            langgraph_pattern=getattr(skill, "langgraph_pattern", None),
        )
        workflow = skill.workflow
        agent_profile = skill.agent_profile

        if target_type == "workflow":
            nodes = (workflow.nodes if workflow is not None else skill.nodes) or []
            edges = (workflow.edges if workflow is not None else skill.edges) or []
            workflow_version = (workflow.workflow_version if workflow is not None else skill.workflow_version) or 1
            workflow_viewport = workflow.workflow_viewport if workflow is not None else skill.workflow_viewport
            tools = skill.tools or []
            kb_config = skill.kb_config if isinstance(skill.kb_config, dict) else None
            system_prompt = None
            target_summary = None
            if workflow is not None:
                target_summary = {
                    "id": workflow.id,
                    "name": self._display_workflow_name(workflow),
                    "enabled": bool(workflow.enabled),
                }
        else:
            nodes = []
            edges = []
            workflow_version = 1
            workflow_viewport = None
            tools = (
                agent_profile.tools
                if agent_profile is not None and isinstance(agent_profile.tools, list)
                else (skill.tools or [])
            )
            kb_config = (
                agent_profile.kb_config
                if agent_profile is not None and isinstance(agent_profile.kb_config, dict)
                else (skill.kb_config if isinstance(skill.kb_config, dict) else {"enabled": False})
            )
            system_prompt = (
                agent_profile.system_prompt
                if agent_profile is not None
                else skill.system_prompt
            )
            target_summary = None
            if agent_profile is not None:
                target_summary = {
                    "id": agent_profile.id,
                    "name": agent_profile.name,
                    "enabled": bool(agent_profile.enabled),
                }

        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description or "",
            "intent_examples": skill.intent_examples or [],
            "tools": tools or [],
            "mode": "langgraph",
            "target_type": target_type,
            "workflow_id": skill.workflow_id,
            "agent_profile_id": skill.agent_profile_id,
            "target_summary": target_summary,
            "langgraph_pattern": "workflow_dag" if target_type == "workflow" else "agent_loop",
            "system_prompt": system_prompt,
            "is_system": bool(skill.is_system),
            "enabled": bool(skill.enabled),
            "kb_config": kb_config,
            "workflow_version": workflow_version,
            "workflow_viewport": workflow_viewport,
            "nodes": nodes,
            "edges": edges,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
        }

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

        workflow_tool_names = self.validate_workflow_dependencies(workflow)

        if not persist:
            return workflow_tool_names

        # Clear persisted DAG children through the ORM relationship first so the
        # in-memory collection and delete-orphan state stay aligned before the
        # rebuilt nodes/edges are attached. This keeps repeated system-baseline
        # restores from re-inserting stale edge rows and tripping uq_workflow_edge.
        workflow_model.edges.clear()
        workflow_model.nodes.clear()
        self.db.flush()

        workflow_model.nodes = [
            AssistantWorkflowNode(
                node_id=n.node_id,
                node_type=n.node_type,
                label=n.label,
                position_x=n.position_x,
                position_y=n.position_y,
                config=n.config,
            )
            for n in workflow.nodes
        ]
        workflow_model.edges = [
            AssistantWorkflowEdge(
                edge_id=e.edge_id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                source_handle=e.source_handle,
                target_handle=e.target_handle,
                condition_type=e.condition_type,
                condition_expr=e.condition_expr.model_dump() if e.condition_expr else None,
                label=e.label,
            )
            for e in workflow.edges
        ]
        workflow_model.workflow_version = (workflow_model.workflow_version or 0) + 1
        workflow_model.workflow_viewport = workflow.viewport
        return workflow_tool_names

    def _bind_skill_to_workflow(
        self,
        *,
        skill: AssistantSkill,
        workflow_id: UUID | None,
        request_workflow: WorkflowInput | None,
        default_name: str,
        description: str,
        enabled: bool,
        is_system: bool,
    ) -> None:
        if request_workflow is not None and self._resolve_start_input_mode(request_workflow) == "structured":
            raise ApiException(
                status_code=422,
                code=42248,
                message="Structured-input workflow cannot be bound to skills",
            )

        workflow_model: AssistantWorkflow
        if workflow_id is not None:
            workflow_model = self.get_workflow(workflow_id)
            if self._is_workflow_structured_input(workflow_model):
                raise ApiException(
                    status_code=422,
                    code=42248,
                    message="Structured-input workflow cannot be bound to skills",
                )
        else:
            workflow_model = AssistantWorkflow(
                name=f"{default_name}__workflow",
                description=description,
                workflow_version=0,
                workflow_viewport=None,
                is_system=is_system,
                enabled=enabled,
            )
            self.db.add(workflow_model)
            # skill has a DB constraint that requires binding exactly one target;
            # bind relation before flush so pending inserts remain valid.
            skill.workflow = workflow_model
            skill.agent_profile = None
            self.db.flush()

        should_update_workflow_graph = request_workflow is not None or workflow_id is None
        if should_update_workflow_graph:
            workflow_input = request_workflow or self._build_default_workflow_input()
            workflow_tool_names = self._apply_workflow_to_workflow_entity(workflow_model, workflow_input)
            published = self._create_workflow_version(
                workflow=workflow_model,
                workflow_input=workflow_input,
                version_source="publish",
                version_name=None,
            )
            workflow_model.draft_version_id = published.id
            workflow_model.published_version_id = published.id
            self._trim_workflow_versions(workflow_model)
        else:
            workflow_tool_names = self._collect_workflow_tool_names(workflow_model.nodes or [])
            self._validate_workflow_tool_names(workflow_tool_names)

        skill.workflow_id = workflow_model.id
        skill.agent_profile_id = None
        skill.langgraph_pattern = "workflow_dag"
        skill.system_prompt = None
        skill.kb_config = {"enabled": False}
        skill.tools = sorted(workflow_tool_names)

    def _bind_skill_to_agent_profile(
        self,
        *,
        skill: AssistantSkill,
        agent_profile_id: UUID | None,
        request_system_prompt: str | None,
        request_tools: list[str] | None,
        request_kb_config: dict | None,
        default_name: str,
        description: str,
        enabled: bool,
        is_system: bool,
    ) -> None:
        if agent_profile_id is not None:
            agent_profile = self.get_agent_profile(agent_profile_id)
        else:
            system_prompt = (request_system_prompt or "").strip() or self._default_agent_system_prompt()
            normalized_kb = self._normalize_agent_kb_config(
                kb_config=request_kb_config,
                model_source=None,
                model_id=None,
                existing_kb_config={"enabled": False},
            )
            agent_profile = AssistantAgentProfile(
                name=f"{default_name}__agent",
                description=description,
                system_prompt=system_prompt,
                tools=list(request_tools or []),
                kb_config=normalized_kb,
                is_system=is_system,
                enabled=enabled,
            )
            self.db.add(agent_profile)
            # skill has a DB constraint that requires binding exactly one target;
            # bind relation before flush so pending inserts remain valid.
            skill.agent_profile = agent_profile
            skill.workflow = None
            self.db.flush()
            initial_draft = AgentPublishDraftInput.model_validate(
                {
                    "system_prompt": system_prompt,
                    "tools": request_tools or [],
                    "kb_config": normalized_kb,
                    "model_source": "default",
                    "model_id": None,
                }
            )
            published = self._create_agent_profile_version(
                agent_profile=agent_profile,
                draft=initial_draft,
                version_source="publish",
                version_name=None,
            )
            agent_profile.draft_version_id = published.id
            agent_profile.published_version_id = published.id
            self._trim_agent_versions(agent_profile)

        skill.workflow_id = None
        skill.agent_profile_id = agent_profile.id
        skill.langgraph_pattern = "agent_loop"
        skill.system_prompt = agent_profile.system_prompt
        skill.kb_config = agent_profile.kb_config if isinstance(agent_profile.kb_config, dict) else {"enabled": False}
        skill.tools = list(agent_profile.tools or [])

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
        skill: AssistantSkill,
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

    def ensure_system_workflow_asset_from_preset(
        self,
        *,
        canonical_name: str,
        preset_file: str,
        description: str,
        enabled: bool = True,
    ) -> AssistantWorkflow:
        workflow = (
            self.db.query(AssistantWorkflow)
            .filter(
                AssistantWorkflow.name == canonical_name,
                AssistantWorkflow.is_system.is_(True),
            )
            .first()
        )
        if workflow is None:
            conflicting = (
                self.db.query(AssistantWorkflow)
                .filter(AssistantWorkflow.name == canonical_name)
                .first()
            )
            if conflicting is not None and not bool(conflicting.is_system):
                raise ApiException(
                    status_code=409,
                    code=40946,
                    message=f"Cannot create system workflow target due to custom name conflict: {canonical_name}",
                )
            workflow = AssistantWorkflow(
                name=canonical_name,
                description=description,
                workflow_version=0,
                workflow_viewport=None,
                is_system=True,
                enabled=bool(enabled),
            )
            self.db.add(workflow)
            self.db.flush()

        workflow_input = load_system_workflow_preset_file(preset_file)
        current_input = self._get_workflow_published_input(workflow)
        desired_snapshot = self._workflow_input_to_snapshot(workflow_input)
        current_snapshot = self._workflow_input_to_snapshot(current_input) if current_input is not None else None

        workflow.is_system = True
        workflow.enabled = bool(enabled)
        workflow.description = description or ""

        if current_snapshot != desired_snapshot:
            self._enforce_workflow_structured_input_constraints(
                workflow=workflow,
                workflow_input=workflow_input,
                raise_error=True,
            )
            self._apply_workflow_to_workflow_entity(workflow, workflow_input, persist=True)
            published = self._create_workflow_version(
                workflow=workflow,
                workflow_input=workflow_input,
                version_source="publish",
                version_name=None,
            )
            self._keep_only_workflow_version(workflow, published.id)

        return workflow

    def _resolve_or_create_system_agent_profile_for_reset(
        self,
        *,
        skill: AssistantSkill,
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
        (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.workflow_id == workflow.id,
                AssistantWorkflowVersion.id != keep_version_id,
            )
            .delete(synchronize_session=False)
        )
        workflow.draft_version_id = keep_version_id
        workflow.published_version_id = keep_version_id

    def _keep_only_agent_version(self, agent_profile: AssistantAgentProfile, keep_version_id: UUID) -> None:
        (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.agent_profile_id == agent_profile.id,
                AssistantAgentProfileVersion.id != keep_version_id,
            )
            .delete(synchronize_session=False)
        )
        agent_profile.draft_version_id = keep_version_id
        agent_profile.published_version_id = keep_version_id

    # -------------------------
    # System seed / sync
    # -------------------------
    def sync_system_tools(self) -> None:
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
        removed_names = internal_names.union(stale_names)
        if removed_names:
            skills = self.db.query(AssistantSkill).all()
            for skill in skills:
                tools = getattr(skill, "tools", None)
                if not isinstance(tools, list):
                    continue
                cleaned = [t for t in tools if not (isinstance(t, str) and t in removed_names)]
                if cleaned != tools:
                    skill.tools = cleaned

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
        """同步系统技能到数据库。

        Note:
        - 系统 Workflow 的节点坐标基线来自 JSON 默认定义（system_defaults）。
        - sync 会强制把 shipped 系统执行体恢复为官方基线内容。
        - 系统 Skill 的上层绑定会被保留为系统壳，但底层系统 Workflow / Agent 会压缩为单一基线发布版本。
        """
        system_skills = SkillRegistry.list_system_skills(locale=self._current_locale())
        if not system_skills:
            return

        for attempt in range(2):
            changed = False
            try:
                for s in system_skills:
                    existing = (
                        self.db.query(AssistantSkill)
                        .filter(AssistantSkill.name == s.name)
                        .first()
                    )
                    if not existing:
                        existing = AssistantSkill(
                            name=s.name,
                            description=s.description,
                            intent_examples=s.intent_examples,
                            tools=s.tools,
                            mode="langgraph",
                            langgraph_pattern="workflow_dag" if self._derive_target_type(
                                langgraph_pattern=getattr(s, "langgraph_pattern", None),
                            ) == "workflow" else "agent_loop",
                            system_prompt=s.system_prompt,
                            kb_config=self._skill_default_kb_config(s),
                            is_system=True,
                            enabled=True,
                        )
                        changed = True

                    if self._reset_skill_to_default(existing, s):
                        changed = True

                    if existing not in self.db:
                        self.db.add(existing)

                if changed and commit:
                    self.db.commit()
                return
            except IntegrityError as exc:
                self.db.rollback()
                if attempt == 0:
                    self.db.expire_all()
                    continue
                raise ApiException(status_code=409, code=40920, message="Sync system skills failed") from exc

    @staticmethod
    def _replace_tool_text_refs_in_value(value: Any, tool_node_ids: set[str]) -> tuple[Any, bool]:
        changed = False

        if isinstance(value, str):
            def _repl(match: re.Match[str]) -> str:
                nonlocal changed
                node_id = match.group(1)
                if node_id in tool_node_ids:
                    changed = True
                    return f"{{{{{node_id}.result}}}}"
                return match.group(0)

            return _TOOL_TEXT_REF_RE.sub(_repl, value), changed

        if isinstance(value, list):
            out: list[Any] = []
            for item in value:
                new_item, item_changed = AssistantConfigService._replace_tool_text_refs_in_value(item, tool_node_ids)
                out.append(new_item)
                changed = changed or item_changed
            return out, changed

        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for k, v in value.items():
                new_v, v_changed = AssistantConfigService._replace_tool_text_refs_in_value(v, tool_node_ids)
                out[k] = new_v
                changed = changed or v_changed
            return out, changed

        return value, False

    def _migrate_workflow_tool_text_refs(self, skill: AssistantSkill) -> None:
        nodes = getattr(skill, "nodes", None) or []
        if not nodes:
            return
        tool_node_ids = {
            str(getattr(node, "node_id", "") or "").strip()
            for node in nodes
            if str(getattr(node, "node_type", "") or "").strip() == "tool"
        }
        tool_node_ids.discard("")
        if not tool_node_ids:
            return

        for node in nodes:
            cfg = getattr(node, "config", None)
            if not isinstance(cfg, dict):
                continue
            new_cfg, changed = self._replace_tool_text_refs_in_value(cfg, tool_node_ids)
            if changed:
                node.config = new_cfg

    @staticmethod
    def _requires_system_workflow_output_migration(skill: AssistantSkill) -> bool:
        """Detect legacy system workflow graphs that do not use output-node terminal semantics."""
        nodes = list(getattr(skill, "nodes", None) or [])
        edges = list(getattr(skill, "edges", None) or [])
        if not nodes:
            return True

        output_nodes = [
            node
            for node in nodes
            if str(getattr(node, "node_type", "") or "").strip().lower() == "output"
        ]
        if len(output_nodes) != 1:
            return True

        output_node_id = str(getattr(output_nodes[0], "node_id", "") or "").strip()
        if not output_node_id:
            return True

        has_incoming = any(
            str(getattr(edge, "target_node_id", "") or "").strip() == output_node_id
            for edge in edges
        )
        has_outgoing = any(
            str(getattr(edge, "source_node_id", "") or "").strip() == output_node_id
            for edge in edges
        )
        if has_outgoing or not has_incoming:
            return True

        for node in nodes:
            cfg = getattr(node, "config", None)
            if isinstance(cfg, dict) and ("isOutput" in cfg or "is_output" in cfg):
                return True

        return False

    def _replace_workflow_with_default(self, skill: AssistantSkill, default) -> None:
        """Replace only workflow graph (nodes/edges) from system default definition."""
        skill.edges = []
        skill.nodes = []
        self.db.flush()

        if getattr(default, "workflow_nodes", None):
            skill.nodes = [
                AssistantSkillNode(
                    node_id=n.node_id,
                    node_type=n.node_type,
                    label=n.label,
                    position_x=n.position_x,
                    position_y=n.position_y,
                    config=n.config,
                )
                for n in default.workflow_nodes
            ]
        else:
            skill.nodes = []

        if getattr(default, "workflow_edges", None):
            skill.edges = [
                AssistantSkillEdge(
                    edge_id=e.edge_id,
                    source_node_id=e.source_node_id,
                    target_node_id=e.target_node_id,
                    source_handle=e.source_handle,
                    target_handle=e.target_handle,
                    condition_type=e.condition_type,
                    condition_expr=e.condition_expr.model_dump() if e.condition_expr else None,
                    label=e.label,
                )
                for e in default.workflow_edges
            ]
        else:
            skill.edges = []

    # -------------------------
    # Tools CRUD
    # -------------------------
    def list_tools(self, sync_system: bool = True, include_disabled: bool = False) -> list[AssistantTool]:
        if sync_system:
            self.sync_system_tools()
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
        tool = self.get_tool(id)

        if tool.is_system:
            # 系统工具只允许修改 enabled
            if request.enabled is not None:
                tool.enabled = request.enabled
            else:
                raise ApiException(status_code=400, code=40012, message="System tool can only update enabled")
        else:
            if request.name is not None:
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

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40913, message="Update tool failed") from exc
        self.db.refresh(tool)
        return tool

    def delete_tool(self, id: UUID) -> None:
        tool = self.get_tool(id)
        if tool.is_system:
            raise ApiException(status_code=400, code=40013, message="System tool cannot be deleted")
        self.db.delete(tool)
        self.db.commit()

    # -------------------------
    # Skills CRUD
    # -------------------------
    def list_skills(self, sync_system: bool = True, include_disabled: bool = False) -> list[AssistantSkill]:
        if sync_system:
            self.sync_system_skills()
        q = (
            self.db.query(AssistantSkill)
            .options(
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.nodes),
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.edges),
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.skills),
                joinedload(AssistantSkill.agent_profile).joinedload(AssistantAgentProfile.skills),
            )
            .order_by(AssistantSkill.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantSkill.enabled.is_(True))
        return q.all()

    def get_skill(self, id: UUID) -> AssistantSkill:
        skill = (
            self.db.query(AssistantSkill)
            .options(
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.nodes),
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.edges),
                joinedload(AssistantSkill.workflow).joinedload(AssistantWorkflow.skills),
                joinedload(AssistantSkill.agent_profile).joinedload(AssistantAgentProfile.skills),
            )
            .filter(AssistantSkill.id == id)
            .first()
        )
        if not skill:
            raise ApiException(status_code=404, code=40411, message=f"Skill not found: {id}")
        return skill

    def create_skill(self, request: AssistantSkillCreateRequest) -> AssistantSkill:
        existing = self.db.query(AssistantSkill).filter(
            AssistantSkill.name.ilike(request.name)
        ).first()
        if existing:
            raise ApiException(status_code=400, code=40020, message=f"Skill name exists: {request.name}")

        skill = AssistantSkill(
            name=request.name,
            description=request.description,
            intent_examples=request.intent_examples,
            tools=request.tools or [],
            mode="langgraph",
            langgraph_pattern=request.langgraph_pattern or "agent_loop",
            system_prompt=request.system_prompt,
            kb_config=request.kb_config if isinstance(request.kb_config, dict) else {"enabled": False},
            is_system=False,
            enabled=request.enabled,
        )
        self.db.add(skill)

        target_type = self._derive_target_type(
            target_type=request.target_type,
            workflow_id=request.workflow_id,
            agent_profile_id=request.agent_profile_id,
            langgraph_pattern=request.langgraph_pattern,
        )
        if target_type == "workflow":
            if request.workflow_id is not None and request.workflow is not None:
                raise ApiException(
                    status_code=422,
                    code=42208,
                    message="Cannot provide workflow payload when binding an existing workflow",
                )
            self._bind_skill_to_workflow(
                skill=skill,
                workflow_id=request.workflow_id,
                request_workflow=request.workflow,
                default_name=request.name,
                description=request.description,
                enabled=bool(request.enabled),
                is_system=False,
            )
        else:
            self._bind_skill_to_agent_profile(
                skill=skill,
                agent_profile_id=request.agent_profile_id,
                request_system_prompt=request.system_prompt,
                request_tools=request.tools,
                request_kb_config=request.kb_config,
                default_name=request.name,
                description=request.description,
                enabled=bool(request.enabled),
                is_system=False,
            )

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40920, message="Create skill failed") from exc
        return self.get_skill(skill.id)

    def update_skill(self, id: UUID, request: AssistantSkillUpdateRequest) -> AssistantSkill:
        skill = self.get_skill(id)
        previous_target_type = self._derive_target_type(
            workflow_id=skill.workflow_id,
            agent_profile_id=skill.agent_profile_id,
            langgraph_pattern=skill.langgraph_pattern,
        )

        if skill.is_system:
            # 系统技能允许编辑内容，但禁止改名
            if request.name is not None and request.name != skill.name:
                raise ApiException(status_code=400, code=40021, message="System skill cannot be renamed")
        else:
            if request.name is not None:
                skill.name = request.name

        if request.description is not None:
            skill.description = request.description
        if request.intent_examples is not None:
            skill.intent_examples = request.intent_examples
        if request.mode is not None and request.mode != "langgraph":
            raise ApiException(status_code=422, code=42204, message="mode must be langgraph")
        skill.mode = "langgraph"
        if request.enabled is not None:
            # 阻止禁用默认 Skill
            if skill.name == DEFAULT_SKILL_NAME and request.enabled is False:
                raise ApiException(status_code=400, code=40025, message="General chat skill cannot be disabled")
            skill.enabled = request.enabled

        requested_target_type = self._derive_target_type(
            target_type=request.target_type,
            workflow_id=request.workflow_id,
            agent_profile_id=request.agent_profile_id,
            langgraph_pattern=request.langgraph_pattern or skill.langgraph_pattern,
        )
        target_changed = requested_target_type != previous_target_type

        if requested_target_type == "workflow":
            if request.workflow_id is not None and request.workflow is not None:
                raise ApiException(
                    status_code=422,
                    code=42208,
                    message="Cannot provide workflow payload when binding an existing workflow",
                )
            workflow_id = request.workflow_id
            if workflow_id is None and not target_changed:
                workflow_id = skill.workflow_id
            self._bind_skill_to_workflow(
                skill=skill,
                workflow_id=workflow_id,
                request_workflow=request.workflow,
                default_name=skill.name,
                description=skill.description,
                enabled=bool(skill.enabled),
                is_system=bool(skill.is_system),
            )
        else:
            agent_profile_id = request.agent_profile_id
            if agent_profile_id is None and not target_changed:
                agent_profile_id = skill.agent_profile_id
            self._bind_skill_to_agent_profile(
                skill=skill,
                agent_profile_id=agent_profile_id,
                request_system_prompt=request.system_prompt,
                request_tools=request.tools,
                request_kb_config=request.kb_config,
                default_name=skill.name,
                description=skill.description,
                enabled=bool(skill.enabled),
                is_system=bool(skill.is_system),
            )
            if request.system_prompt is not None and skill.agent_profile is not None:
                skill.agent_profile.system_prompt = request.system_prompt
            if request.kb_config is not None and skill.agent_profile is not None:
                skill.agent_profile.kb_config = self._normalize_agent_kb_config(
                    kb_config=request.kb_config,
                    model_source=None,
                    model_id=None,
                    existing_kb_config=(
                        skill.agent_profile.kb_config
                        if isinstance(skill.agent_profile.kb_config, dict)
                        else {"enabled": False}
                    ),
                )
                skill.kb_config = skill.agent_profile.kb_config
            if request.tools is not None and skill.agent_profile is not None:
                skill.agent_profile.tools = request.tools
                skill.tools = list(request.tools)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40921, message="Update skill failed") from exc
        return self.get_skill(skill.id)

    def reset_skill(self, id: UUID, confirm: bool) -> AssistantSkill:
        """复位系统技能到默认配置"""
        if not confirm:
            raise ApiException(status_code=400, code=40023, message="confirm=true required")

        skill = self.get_skill(id)
        if not skill.is_system:
            raise ApiException(status_code=400, code=40024, message="Only system skill can be reset")

        from app.assistant.skill_catalog.definitions import get_skill_by_name

        default = get_skill_by_name(skill.name, locale=self._current_locale())
        if not default:
            raise ApiException(status_code=404, code=40412, message=f"Default not found: {skill.name}")

        self._reset_skill_to_default(skill, default)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40922, message="Reset skill failed") from exc
        self.db.refresh(skill)
        return skill

    def delete_skill(self, id: UUID) -> None:
        skill = self.get_skill(id)
        if skill.is_system:
            raise ApiException(status_code=400, code=40022, message="System skill cannot be deleted")
        self.db.delete(skill)
        self.db.commit()

    def reset_all_system_skills(self, confirm: bool, *, commit: bool = True) -> dict:
        """重置所有系统技能到默认配置，并清理已下线的系统技能"""
        if not confirm:
            raise ApiException(status_code=400, code=40023, message="confirm=true required")

        locale = self._current_locale()
        from app.assistant.skill_catalog.defaults_loader import load_system_skill_defaults
        from app.assistant.skill_catalog.definitions import get_skill_by_name

        system_defaults = load_system_skill_defaults(locale=locale)

        # 获取代码侧系统技能名称集合
        default_names = {s.name for s in system_defaults}

        # 获取 DB 中所有系统技能
        db_system_skills = (
            self.db.query(AssistantSkill)
            .filter(AssistantSkill.is_system.is_(True))
            .all()
        )

        reset_count = 0
        deleted_count = 0
        created_count = 0
        affected: list[dict] = []

        # 处理 DB 中已存在的系统技能
        for skill in db_system_skills:
            if skill.name in default_names:
                # 重置到默认配置
                default = get_skill_by_name(skill.name, locale=locale)
                if default:
                    self._reset_skill_to_default(skill, default)
                    reset_count += 1
                    affected.append({"name": skill.name, "id": str(skill.id), "action": "reset"})
            else:
                # 已下线的系统技能，删除（保留执行体实体，若后续仍被引用可复用）
                self.db.delete(skill)
                deleted_count += 1
                affected.append({"name": skill.name, "id": str(skill.id), "action": "deleted"})

        # 创建缺失的系统技能
        existing_names = {s.name for s in db_system_skills}
        for s in system_defaults:
            if s.name not in existing_names:
                skill = AssistantSkill(
                    name=s.name,
                    description=s.description,
                    intent_examples=s.intent_examples,
                    tools=list(s.tools or []),
                    mode="langgraph",
                    langgraph_pattern="workflow_dag",
                    system_prompt=s.system_prompt,
                    kb_config=self._skill_default_kb_config(s),
                    is_system=True,
                    enabled=True,
                )
                self._reset_skill_to_default(skill, s)
                self.db.add(skill)
                created_count += 1
                affected.append({"name": s.name, "id": None, "action": "created"})

        if commit:
            try:
                self.db.commit()
            except IntegrityError as exc:
                self.db.rollback()
                raise ApiException(status_code=409, code=40923, message="Reset all skills failed") from exc

        return {
            "resetCount": reset_count,
            "deletedCount": deleted_count,
            "createdCount": created_count,
            "affected": affected,
        }

    def _reset_skill_to_default(self, skill: AssistantSkill, default) -> bool:
        """内部方法：将技能重置到默认配置。

        对 workflow_dag 系统技能，reset 会按 JSON 默认定义（system_defaults）的节点坐标重建草稿并发布，
        因此会恢复到“系统默认布局基线”。
        """
        changed = False
        # 保留 enabled 状态（general_chat 强制启用）
        enabled = skill.enabled
        if skill.name == DEFAULT_SKILL_NAME:
            enabled = True

        normalized_description = default.description or ""
        normalized_intent_examples = list(default.intent_examples or [])
        if (skill.description or "") != normalized_description:
            skill.description = normalized_description
            changed = True
        if list(skill.intent_examples or []) != normalized_intent_examples:
            skill.intent_examples = normalized_intent_examples
            changed = True
        if skill.mode != "langgraph":
            skill.mode = "langgraph"
            changed = True
        if not bool(skill.is_system):
            skill.is_system = True
            changed = True
        target_type = self._derive_target_type(langgraph_pattern=getattr(default, "langgraph_pattern", None))
        kb_config_data = self._skill_default_kb_config(default)

        if target_type == "workflow":
            workflow_input = self._workflow_input_from_skill_default(default)
            workflow_model = self._resolve_or_create_system_workflow_for_reset(
                skill=skill,
                default=default,
                enabled=bool(enabled),
            )
            self._enforce_workflow_structured_input_constraints(
                workflow=workflow_model,
                workflow_input=workflow_input,
                raise_error=True,
            )
            workflow_changed = self._ensure_system_workflow_baseline_state(
                workflow=workflow_model,
                workflow_input=workflow_input,
                description=default.description or "",
                enabled=bool(enabled),
            )
            workflow_tool_names = sorted(self._collect_workflow_tool_names(workflow_input.nodes))
            changed = workflow_changed or changed
            if skill.workflow_id != workflow_model.id:
                skill.workflow_id = workflow_model.id
                changed = True
            if skill.agent_profile_id is not None:
                skill.agent_profile_id = None
                changed = True
            if skill.langgraph_pattern != "workflow_dag":
                skill.langgraph_pattern = "workflow_dag"
                changed = True
            if skill.system_prompt is not None:
                skill.system_prompt = None
                changed = True
            if skill.kb_config != {"enabled": False}:
                skill.kb_config = {"enabled": False}
                changed = True
            if list(skill.tools or []) != workflow_tool_names:
                skill.tools = workflow_tool_names
                changed = True
        else:
            agent_profile = self._resolve_or_create_system_agent_profile_for_reset(
                skill=skill,
                default=default,
                enabled=bool(enabled),
            )
            draft = self._agent_draft_from_skill_default(default)
            profile_changed = self._ensure_system_agent_baseline_state(
                agent_profile=agent_profile,
                draft=draft,
                description=default.description or "",
                enabled=bool(enabled),
            )
            changed = profile_changed or changed
            if skill.workflow_id is not None:
                skill.workflow_id = None
                changed = True
            if skill.agent_profile_id != agent_profile.id:
                skill.agent_profile_id = agent_profile.id
                changed = True
            if skill.langgraph_pattern != "agent_loop":
                skill.langgraph_pattern = "agent_loop"
                changed = True
            if (skill.system_prompt or "") != (agent_profile.system_prompt or ""):
                skill.system_prompt = agent_profile.system_prompt
                changed = True
            effective_kb = agent_profile.kb_config if isinstance(agent_profile.kb_config, dict) else kb_config_data
            if skill.kb_config != effective_kb:
                skill.kb_config = effective_kb
                changed = True
            next_tools = list(agent_profile.tools or [])
            if list(skill.tools or []) != next_tools:
                skill.tools = next_tools
                changed = True

        if bool(skill.enabled) != bool(enabled):
            skill.enabled = enabled
            changed = True

        return changed

    def _apply_workflow_to_skill(self, skill: AssistantSkill, workflow) -> None:
        """Compatibility helper: apply workflow onto a skill's bound workflow target."""
        workflow_model = skill.workflow
        if workflow_model is None:
            workflow_model = AssistantWorkflow(
                name=f"{skill.name}__workflow",
                description=skill.description or "",
                workflow_version=0,
                workflow_viewport=None,
                is_system=bool(skill.is_system),
                enabled=bool(skill.enabled),
            )
            self.db.add(workflow_model)
            self.db.flush()
            skill.workflow_id = workflow_model.id
            skill.agent_profile_id = None

        workflow_tool_names = self._apply_workflow_to_workflow_entity(workflow_model, workflow)
        skill.langgraph_pattern = "workflow_dag"
        skill.tools = sorted(workflow_tool_names)
        skill.workflow_viewport = workflow_model.workflow_viewport
        skill.workflow_version = workflow_model.workflow_version

    def validate_workflow_dependencies(self, workflow) -> set[str]:
        """Validate workflow external dependencies (tools/models) before persistence."""
        workflow_tool_names = self._collect_workflow_tool_names(workflow.nodes)
        self._validate_workflow_tool_names(workflow_tool_names)
        custom_model_ids = self._collect_workflow_custom_model_ids(workflow.nodes)
        self._validate_workflow_model_ids(custom_model_ids)
        return workflow_tool_names

    @staticmethod
    def _collect_workflow_tool_names(workflow_nodes: list) -> set[str]:
        tool_names: set[str] = set()

        def _walk(nodes: list) -> None:
            for node in nodes:
                if isinstance(node, dict):
                    node_type = node.get("node_type") or node.get("nodeType")
                    cfg = node.get("config") or {}
                else:
                    node_type = getattr(node, "node_type", None)
                    cfg = getattr(node, "config", None) or {}

                if node_type == "tool" and isinstance(cfg, dict):
                    tool_name = cfg.get("toolName") or cfg.get("tool_name")
                    if isinstance(tool_name, str) and tool_name.strip():
                        tool_names.add(tool_name.strip())

                if node_type == "knowledge_retrieval":
                    tool_names.add("kb_search")

                if node_type == "agent" and isinstance(cfg, dict):
                    raw_tool_names = cfg.get("toolNames", cfg.get("tool_names"))
                    if isinstance(raw_tool_names, list):
                        for raw_name in raw_tool_names:
                            if not isinstance(raw_name, str):
                                continue
                            tool_name = raw_name.strip()
                            if tool_name:
                                tool_names.add(tool_name)
                    knowledge_enabled = cfg.get("knowledgeEnabled", cfg.get("knowledge_enabled"))
                    if isinstance(knowledge_enabled, bool) and knowledge_enabled:
                        tool_names.add("kb_search")

                if node_type in {"iteration", "loop"} and isinstance(cfg, dict):
                    body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                    if isinstance(body_nodes, list):
                        _walk(body_nodes)

        _walk(workflow_nodes)
        return tool_names

    @staticmethod
    def _collect_workflow_custom_model_ids(workflow_nodes: list) -> set[UUID]:
        model_ids: set[UUID] = set()

        def _walk(nodes: list) -> None:
            for node in nodes:
                if isinstance(node, dict):
                    node_type = node.get("node_type") or node.get("nodeType")
                    cfg = node.get("config") or {}
                else:
                    node_type = getattr(node, "node_type", None)
                    cfg = getattr(node, "config", None) or {}

                if node_type in {"llm", "parameter_extractor", "agent"} and isinstance(cfg, dict):
                    model_source_raw = cfg.get("modelSource", cfg.get("model_source", "default"))
                    model_source = str(model_source_raw or "default").strip().lower()
                    if model_source == "custom":
                        model_id_raw = cfg.get("modelId", cfg.get("model_id"))
                        if isinstance(model_id_raw, str):
                            model_id_text = model_id_raw.strip()
                            if model_id_text:
                                try:
                                    model_ids.add(UUID(model_id_text))
                                except Exception:
                                    # UUID format is validated in workflow validator. Ignore here.
                                    pass

                if node_type in {"iteration", "loop"} and isinstance(cfg, dict):
                    body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                    if isinstance(body_nodes, list):
                        _walk(body_nodes)

        _walk(workflow_nodes)

        return model_ids

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
    # Workflows CRUD
    # -------------------------
    def list_workflows(self, include_disabled: bool = False) -> list[AssistantWorkflow]:
        self.sync_system_skills()
        self.ensure_system_behaviors()
        q = (
            self.db.query(AssistantWorkflow)
            .options(
                joinedload(AssistantWorkflow.nodes),
                joinedload(AssistantWorkflow.edges),
                joinedload(AssistantWorkflow.skills),
                joinedload(AssistantWorkflow.system_behavior_bindings),
            )
            .order_by(AssistantWorkflow.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantWorkflow.enabled.is_(True))
        return q.all()

    def get_workflow(self, workflow_id: UUID) -> AssistantWorkflow:
        self.sync_system_skills()
        self.ensure_system_behaviors()
        workflow = (
            self.db.query(AssistantWorkflow)
            .options(
                joinedload(AssistantWorkflow.nodes),
                joinedload(AssistantWorkflow.edges),
                joinedload(AssistantWorkflow.skills),
                joinedload(AssistantWorkflow.system_behavior_bindings),
            )
            .filter(AssistantWorkflow.id == workflow_id)
            .first()
        )
        if workflow is None:
            raise ApiException(status_code=404, code=40430, message=f"Workflow not found: {workflow_id}")
        return workflow

    def _create_workflow_entity(self, request: AssistantWorkflowCreateRequest) -> AssistantWorkflow:
        if self._workflow_name_exists(request.name):
            raise ApiException(status_code=400, code=40030, message=f"Workflow name exists: {request.name}")
        workflow_input = request.workflow or self._build_default_workflow_input()
        workflow = AssistantWorkflow(
            name=request.name,
            description=request.description or "",
            workflow_version=0,
            workflow_viewport=None,
            is_system=False,
            enabled=request.enabled,
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
        return self.get_workflow(workflow.id)

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
        workflow = self.get_workflow(workflow_id)
        if workflow.is_system:
            self._raise_system_workflow_readonly()
        if workflow.skills:
            skill_names = ", ".join(sorted(s.name for s in workflow.skills))
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
                    "targetName": workflow.name,
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
        self.sync_system_skills()
        self.ensure_system_behaviors()
        q = (
            self.db.query(AssistantAgentProfile)
            .options(
                joinedload(AssistantAgentProfile.skills),
                joinedload(AssistantAgentProfile.system_behavior_bindings),
            )
            .order_by(AssistantAgentProfile.created_at.desc())
        )
        if not include_disabled:
            q = q.filter(AssistantAgentProfile.enabled.is_(True))
        return q.all()

    def get_agent_profile(self, agent_profile_id: UUID) -> AssistantAgentProfile:
        self.sync_system_skills()
        self.ensure_system_behaviors()
        profile = (
            self.db.query(AssistantAgentProfile)
            .options(
                joinedload(AssistantAgentProfile.skills),
                joinedload(AssistantAgentProfile.system_behavior_bindings),
            )
            .filter(AssistantAgentProfile.id == agent_profile_id)
            .first()
        )
        if profile is None:
            raise ApiException(status_code=404, code=40431, message=f"Agent profile not found: {agent_profile_id}")
        return profile

    def create_agent_profile(self, request: AssistantAgentProfileCreateRequest) -> AssistantAgentProfile:
        existing = self.db.query(AssistantAgentProfile).filter(AssistantAgentProfile.name.ilike(request.name)).first()
        if existing:
            raise ApiException(status_code=400, code=40032, message=f"Agent profile name exists: {request.name}")
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
        for skill in profile.skills or []:
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
        return self.get_agent_profile(profile.id)

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
        profile = self.get_agent_profile(agent_profile_id)
        if profile.is_system:
            self._raise_system_agent_readonly()
        if profile.skills:
            skill_names = ", ".join(sorted(s.name for s in profile.skills))
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

    # -------------------------
    # Compatibility workflow methods on skill routes
    # -------------------------
    def get_skill_workflow(self, skill_id: UUID) -> AssistantWorkflow:
        skill = self.get_skill(skill_id)
        if skill.workflow_id is None:
            raise ApiException(
                status_code=409,
                code=40936,
                message=f"Skill '{skill.name}' is bound to an agent, not a workflow",
            )
        return self.get_workflow(skill.workflow_id)

    def update_workflow(self, skill_id, workflow) -> AssistantSkill:
        """Compatibility API: update workflow by skill id."""
        from uuid import UUID as _UUID

        skill_uuid = _UUID(str(skill_id)) if not isinstance(skill_id, _UUID) else skill_id
        skill = self.get_skill(skill_uuid)
        if skill.workflow_id is None:
            raise ApiException(
                status_code=409,
                code=40936,
                message=f"Skill '{skill.name}' is bound to an agent, not a workflow",
            )
        self.update_workflow_by_id(skill.workflow_id, workflow)
        return self.get_skill(skill_uuid)
