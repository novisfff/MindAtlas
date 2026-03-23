from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.ai_registry.runtime import (
    resolve_openai_compat_config,
    resolve_openai_compat_config_by_model_id,
)
from app.assistant.skill_catalog.base import (
    ConditionExpression,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)
from app.assistant.workflow.engine.engine import LangGraphEngine
from app.assistant.workflow.engine.runtime_helpers import extract_json_object
from app.assistant_config.models import AssistantAgentProfile, AssistantWorkflow
from app.assistant_config.schemas import AgentPublishDraftInput, WorkflowInput
from app.assistant_config.service import AssistantConfigService
from app.assistant_config.system_behavior_registry import SystemBehaviorDefinition
from app.common.exceptions import ApiException


@dataclass(frozen=True)
class SystemAiBehaviorRunInput:
    period_type: str
    period_start: date
    period_end: date
    entry_count: int

    def to_structured_input(self) -> dict[str, Any]:
        return {
            "periodType": str(self.period_type or "").strip(),
            "periodStart": self.period_start.isoformat(),
            "periodEnd": self.period_end.isoformat(),
            "entryCount": int(self.entry_count),
        }


class SystemAiBehaviorRunner:
    def __init__(self, db: Session):
        self.db = db
        self.config_service = AssistantConfigService(db)

    def _build_engine(self, skill: SkillDefinition) -> LangGraphEngine:
        if skill.langgraph_pattern == "agent_loop" and getattr(skill, "model_source", "default") == "custom":
            selected_model_id = getattr(skill, "model_id", None)
            cfg = resolve_openai_compat_config_by_model_id(
                self.db,
                model_id=selected_model_id or "",
                model_type="llm",
            )
        else:
            cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")

        if cfg is None:
            raise ApiException(
                status_code=409,
                code=40965,
                message="No available model configuration for system AI behavior execution",
            )

        return LangGraphEngine(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            db=self.db,
        )

    @staticmethod
    def _build_workflow_skill_definition(
        *,
        definition: SystemBehaviorDefinition,
        workflow: AssistantWorkflow,
        workflow_input: WorkflowInput,
    ) -> SkillDefinition:
        workflow_nodes = [
            WorkflowNodeDefinition(
                node_id=node.node_id,
                node_type=node.node_type,
                label=node.label,
                position_x=node.position_x,
                position_y=node.position_y,
                config=node.config or {},
            )
            for node in workflow_input.nodes
        ]

        workflow_edges: list[WorkflowEdgeDefinition] = []
        for edge in workflow_input.edges:
            condition_expr = None
            if edge.condition_expr is not None:
                condition_expr = ConditionExpression(
                    id=edge.condition_expr.id,
                    variable=edge.condition_expr.variable,
                    operator=edge.condition_expr.operator,
                    value=edge.condition_expr.value,
                    handle=edge.condition_expr.handle,
                )
            workflow_edges.append(
                WorkflowEdgeDefinition(
                    edge_id=edge.edge_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    source_handle=edge.source_handle,
                    target_handle=edge.target_handle,
                    condition_type=edge.condition_type,
                    condition_expr=condition_expr,
                    label=edge.label,
                )
            )

        tool_names = sorted(AssistantConfigService._collect_workflow_tool_names(workflow_nodes))
        return SkillDefinition(
            name=f"{definition.key}__{workflow.name}__workflow",
            description=workflow.description or definition.description,
            intent_examples=[],
            tools=tool_names,
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            workflow_nodes=workflow_nodes,
            workflow_edges=workflow_edges,
        )

    @staticmethod
    def _build_agent_system_prompt(
        *,
        definition: SystemBehaviorDefinition,
        base_prompt: str,
    ) -> str:
        contract = (
            f"You are executing the system AI behavior '{definition.name}'.\n"
            "You may use tools if needed.\n"
            "Your final answer must be a single JSON object with exactly these fields:\n"
            '- "summary": string\n'
            '- "suggestions": string[]\n'
            '- "trends": string\n'
            "Do not output Markdown fences or any prose outside the JSON object."
        )
        trimmed_base = str(base_prompt or "").strip()
        if not trimmed_base:
            return contract
        return f"{trimmed_base}\n\n{contract}"

    @staticmethod
    def _build_agent_user_input(
        *,
        definition: SystemBehaviorDefinition,
        payload: SystemAiBehaviorRunInput,
    ) -> str:
        body = json.dumps(payload.to_structured_input(), ensure_ascii=False)
        return (
            f"Run the system AI behavior '{definition.name}' for the following structured input.\n"
            f"Use the time range to inspect relevant records if needed.\n\n"
            f"{body}"
        )

    def _build_agent_skill_definition(
        self,
        *,
        definition: SystemBehaviorDefinition,
        agent_profile: AssistantAgentProfile,
        draft: AgentPublishDraftInput,
    ) -> SkillDefinition:
        normalized_kb = draft.kb_config if isinstance(draft.kb_config, dict) else {"enabled": False}
        return SkillDefinition(
            name=f"{definition.key}__{agent_profile.name}__agent",
            description=agent_profile.description or definition.description,
            intent_examples=[],
            tools=list(draft.tools or []),
            mode="langgraph",
            langgraph_pattern="agent_loop",
            model_source=draft.model_source,
            model_id=str(draft.model_id) if draft.model_id is not None else None,
            system_prompt=self._build_agent_system_prompt(
                definition=definition,
                base_prompt=draft.system_prompt or "",
            ),
            kb=SkillKBConfig(enabled=bool(normalized_kb.get("enabled", False))),
            workflow_nodes=[],
            workflow_edges=[],
        )

    @staticmethod
    def _parse_behavior_output(raw_output: str) -> dict[str, Any]:
        raw = str(raw_output or "").strip()
        if not raw:
            raise RuntimeError("System AI behavior returned empty output")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        parsed = extract_json_object(raw)
        if isinstance(parsed, dict):
            return parsed
        raise RuntimeError("System AI behavior did not return a valid JSON object")

    @staticmethod
    def _validate_behavior_output(parsed: dict[str, Any]) -> dict[str, Any]:
        summary = parsed.get("summary")
        suggestions = parsed.get("suggestions")
        trends = parsed.get("trends")

        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError("System AI behavior output field 'summary' must be a non-empty string")
        if not isinstance(trends, str):
            raise RuntimeError("System AI behavior output field 'trends' must be a string")
        if not isinstance(suggestions, list) or any(not isinstance(item, str) for item in suggestions):
            raise RuntimeError("System AI behavior output field 'suggestions' must be a string array")

        return {
            "summary": summary.strip(),
            "suggestions": [str(item).strip() for item in suggestions if str(item).strip()],
            "trends": trends.strip(),
        }

    def run_report_behavior(
        self,
        *,
        behavior_key: str,
        payload: SystemAiBehaviorRunInput,
    ) -> dict[str, Any]:
        definition, _binding, target_type, target, _fallback_used = (
            self.config_service.resolve_system_behavior_execution_target(behavior_key)
        )

        if target_type == "workflow":
            if not isinstance(target, AssistantWorkflow):
                raise RuntimeError("Resolved workflow target has unexpected type")
            workflow_input = self.config_service._validate_system_behavior_workflow_target(  # noqa: SLF001
                definition=definition,
                workflow=target,
            )
            skill = self._build_workflow_skill_definition(
                definition=definition,
                workflow=target,
                workflow_input=workflow_input,
            )
            engine = self._build_engine(skill)
            output = "".join(
                engine.execute(
                    skill=skill,
                    user_input="",
                    history=[],
                    runtime_context={
                        "stream_output": False,
                        "conversation_id": f"system_behavior:{behavior_key}:{uuid4().hex}",
                        "structured_input": payload.to_structured_input(),
                        "run_id": uuid4().hex,
                        "channel_type": "system_behavior",
                        "workflow_id": str(target.id),
                    },
                )
            )
            return self._validate_behavior_output(self._parse_behavior_output(output))

        if not isinstance(target, AssistantAgentProfile):
            raise RuntimeError("Resolved agent target has unexpected type")
        draft = self.config_service._validate_system_behavior_agent_target(agent_profile=target)  # noqa: SLF001
        skill = self._build_agent_skill_definition(
            definition=definition,
            agent_profile=target,
            draft=draft,
        )
        engine = self._build_engine(skill)
        output = "".join(
            engine.execute(
                skill=skill,
                user_input=self._build_agent_user_input(definition=definition, payload=payload),
                history=[],
                runtime_context={
                    "stream_output": False,
                    "conversation_id": f"system_behavior:{behavior_key}:{uuid4().hex}",
                    "run_id": uuid4().hex,
                    "channel_type": "system_behavior",
                },
            )
        )
        return self._validate_behavior_output(self._parse_behavior_output(output))
