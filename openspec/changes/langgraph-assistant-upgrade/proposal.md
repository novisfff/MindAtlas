# Change: LangGraph 架构硬切换（移除 steps/agent 旧模式）

## Why
当前技能体系仍保留 `steps` / legacy `agent` / `sequential` 的历史路径，导致执行分支、数据模型和前端交互长期分叉，维护成本高且容易出现配置与运行不一致。项目仍处于开发阶段，适合进行破坏式收敛。

## What Changes
- 执行架构只保留 `mode="langgraph"`，删除 legacy `SkillExecutor` 执行路径。
- `langgraph_pattern` 只允许 `agent_loop | workflow_dag`，移除 `sequential`。
- 数据层移除 `assistant_skill_step`（含 ORM/Schema/API 暴露），并清理 `mode in ('steps','agent')` 历史技能数据。
- `assistant_skill.mode` 列保留但固定为 `langgraph`（不再作为用户可选模式）。
- 前端技能编辑器由“三模式切换”收敛为“两种图模式切换”：
  - `workflow_dag` 显示“工作流模式”
  - `agent_loop` 显示“Agent模式”
- 新建技能默认 `agent_loop`。
- 工作流编辑体验统一为横向语义（主链左到右，分支上下展开），空图默认模板改为横向。
- 官方默认系统工作流（`quick_stats/smart_capture/periodic_review`）默认节点坐标改为横向布局。
- `if_else` 节点升级为完整 IF/ELIF/ELSE 模型：分支内支持 AND/OR，多操作符比较，ELSE 强制连线。
- 工作流模板变量支持 `sys.*` 最小集（`sys.date/sys.datetime/sys.conversation_id`），用于条件与模板渲染。
- 工作流变量引用语义升级为“用户侧 Label、存储侧 node_id”：UI 插入/展示使用 `Label.field`，入库前转换回 `{{node_id.field}}`。
- 工作流节点 Label 在单图内强制唯一（不区分大小写），且禁止 `.`；旧重复标签在加载编辑器时自动补 `#N` 修复。
- 变量面板升级为两级结构：一级为“节点/系统变量”，二级为字段列表，降低复杂 DAG 的引用成本。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/agent.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/converters.py`
  - `backend/app/assistant_config/models.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/registry.py`
  - `backend/app/assistant/skills/definitions.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/alembic/versions/*`
  - `frontend/src/features/assistant-config/components/workflow/PropertyPanel.tsx`
  - `frontend/src/features/assistant-config/components/workflow/ifElseConfig.ts`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowNode.tsx`
  - `frontend/src/features/assistant-config/components/workflow/serialization.ts`
  - `frontend/src/features/assistant-config/*`
  - `frontend/src/locales/*/common.json`
