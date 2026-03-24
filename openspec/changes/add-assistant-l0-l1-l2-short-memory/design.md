## Context
当前 assistant 已具备：
- supervisor 基于历史路由到 skill；
- background run + 可恢复流式执行；
- workflow start `inputMode=text|structured` 与模板引用校验。

但 workflow skill 内部的 LLM 节点默认仍偏向本轮输入，跨轮记忆的统一管理缺失。需要在不改变现有 chat API/SSE 的前提下，补齐短期记忆能力并允许可配置注入策略。

## Goals / Non-Goals
- Goals:
  - 引入 L0/L1/L2 三层短期记忆并定义清晰生命周期。
  - 提供 start 级 `memoryMode=auto|off|structured`，默认 `auto`。
  - `auto` 自动注入到会话型 LLM 节点；`structured` 仅字段暴露；`off` 全关。
  - 保持现有路由/fallback/单 skill 执行与 SSE 语义兼容。
- Non-Goals:
  - 不引入长期向量记忆与跨会话召回。
  - 不引入多 skill 串行执行或额外规则引擎。
  - 不改变现有对外 chat API 请求结构。

## Decisions

### 1) Memory Layer Definitions
- L0（Ephemeral Window）
  - 内容：最近对话原文窗口（user/assistant）。
  - 存储：仅运行时内存，不落库。
- L1（Conversation Summary）
  - 内容：会话级增量摘要文本。
  - 存储：按 `conversation_id` 持久化一条当前摘要。
- L2（Conversation+Skill Facts）
  - 内容：结构化 facts（JSON 列表）与可注入文本视图。
  - 存储：按 `(conversation_id, skill_name)` 持久化。

### 2) Start Memory Mode Semantics
- `auto`：混合注入。
  - `workflow_dag.llm`：L0 作为对话消息流注入；L1/L2 作为系统记忆块注入。
  - `agent_loop`：仅注入 L1/L2 系统记忆块，不额外注入 L0（避免与已有 history 重复）。
- `off`：不注入，不暴露 memory 字段。
- `structured`：不自动注入；仅暴露 `start.memory_*` 字段供模板手动引用。

默认值：`memoryMode=auto`。

### 3) Structured Built-in Fields
当 `memoryMode=structured` 时，start 增加固定字段：
- `start.memory_recent_dialogue`
- `start.memory_conversation_summary`
- `start.memory_skill_facts`

约束：
- 三者均为字符串；
- 不引入嵌套路径（保持现有两段式模板引用兼容）；
- 保留字段名，不允许被业务结构化字段重名覆盖。
- 旧命名 `start.memory_l0/l1/l2` 视为非法引用，不提供兼容别名。

### 4) Eligible Nodes For Auto Injection
- Inject:
  - `workflow_dag` 的 `llm` 节点（L0 message-flow + L1/L2 system-block）
  - `agent_loop` 的主 LLM 回合（仅 L1/L2 system-block）
- Do not inject:
  - `parameter_extractor`
  - `tool`
  - `code_executor`
  - `if_else` 及其他非会话生成节点

### 5) Persistence Model (Implementation Contract)
- L1 表：会话摘要
  - Unique: `conversation_id`
- L2 表：会话+skill 事实记忆
  - Unique: `conversation_id + skill_name`
  - Data: `facts` JSON + version + timestamps

### 6) Update Timing and Failure Policy
- 更新时机：assistant turn 完成且状态非 `failed/cancelled` 后更新 L1/L2。
- 失败策略：fail-open（仅日志告警，不中断用户回复）。

### 7) Config Defaults
- `ASSISTANT_MEMORY_MODE_DEFAULT=auto`
- `ASSISTANT_MEMORY_L0_TURNS=6`
- `ASSISTANT_MEMORY_L0_MAX_CHARS=25000`
- `ASSISTANT_MEMORY_L1_MAX_CHARS=2000`
- `ASSISTANT_MEMORY_L2_MAX_ITEMS=20`
- `ASSISTANT_MEMORY_INJECTION_MAX_CHARS=30000`

### 8) Compatibility
- 不改 `POST /api/assistant/conversations/{id}/chat` 请求结构。
- 不改现有 SSE 事件协议。
- `start.inputMode` 既有 text/structured 语义保持不变，仅扩展 memory 相关字段可见性与注入策略。

## Risks / Trade-offs
- 风险：注入文本过长影响 token 与时延。
  - 缓解：L0/L1/L2 分层预算与统一注入上限。
- 风险：摘要漂移导致误导。
  - 缓解：增量摘要时优先保留用户偏好、任务约束与最新决策，旧信息衰减。
- 风险：事实记忆污染。
  - 缓解：L2 仅按当前 skill 作用域注入，限制数量并支持淘汰。

## Migration Plan
1. OpenSpec 定稿并通过严格校验。
2. 按功能点实施：L0 -> L1 -> L2 -> memoryMode 注入 -> 校验/UI -> 测试回归。
3. 逐阶段启用并观测日志指标，出现异常可先将默认模式降级为 `off`（配置层）。

## Open Questions
- None (all decisions locked by current scope).
