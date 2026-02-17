# Change: 移除 Template/Aggregator 节点并引入 LLM 显式知识绑定

## Why
当前 workflow_dag 仍保留 `template` / `variable_aggregator` 历史节点，且 `knowledge_retrieval` 输出语义与 LLM 注入路径不够可控，导致编排行为与用户预期存在偏差（隐式混入、可观测性不足、配置不清晰）。

## What Changes
- **BREAKING**: 从节点类型集合中硬移除 `template` 与 `variable_aggregator`。
- 为 `knowledge_retrieval` 节点补齐结构化输出与高级参数：`query` + `mode?` + `topK?`（节点优先、系统兜底）。
- 为 `llm` 节点新增知识输入绑定配置：
  - `knowledgeEnabled`
  - `knowledgeSourceNodeIds`
  - `knowledgeInjectMode` (`references_only` | `full_payload`)
  - `knowledgeMaxRefs`
- 运行时改为“显式注入”：KR 不再通过通用上下文自动混入 LLM，仅在 LLM 配置启用并绑定来源后注入。
- 历史 workflow 若包含下线节点类型，保存/校验/执行阶段统一返回明确错误，不做自动迁移。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/skills/converters.py`
  - `backend/app/assistant/skills/base.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant/tools/kb_tools.py`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/locales/*/common.json`
