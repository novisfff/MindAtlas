## Context
当前系统已经同时存在两类 Agent 能力：
- 顶层 `agent_loop`：面向正式 assistant 会话，由 supervisor 选中 skill 后执行。
- `workflow_dag.agent`：面向 workflow DAG 节点与容器 body 子流。

两边都具备 LLM 决策 + 工具调用 + 再次收敛的行为，但主循环实现已经分叉，并且近期又新增了 KB 内建工具、memory 注入与 trace 扩展字段。如果继续并行演化，后续维护会越来越脆弱。

## Goals / Non-Goals
- Goals:
  - 提取共享 agent 执行内核，统一工具循环、KB 工具接入、流式输出与 trace 字段。
  - 让 `agent_loop` 与 `workflow_dag.agent` 共享执行主循环，同时保留各自 wrapper 的 prompt/memory/输出适配。
  - 对齐默认最大迭代次数与失败语义。
- Non-Goals:
  - 不新增顶层 skill 的 agent 公共配置项。
  - 不改变 workflow `agent` 的公开 schema。
  - 不强行合并顶层 skill prompt 与 workflow node prompt 模板。
  - 不修改 chat/run/SSE 事件名。

## Decisions

### 1) Shared Kernel Boundary
共享内核只负责执行行为，不直接读取 skill/node 配置，也不直接决定 memory 策略。

共享内核统一负责：
- LLM 回合执行
- 单轮只执行第一个 tool call
- 工具调用结果回填消息
- KB 工具与普通工具统一绑定接口
- `tool_call_start/end` 扩展字段
- 流式 `content_delta` / `node_output_delta`
- 统一 `max_iterations` 终止逻辑

### 2) Wrapper Responsibilities
`agent_loop` wrapper 负责：
- 构建 skill 视角系统提示词
- 把 `kb_config.enabled` 映射成内建 `kb_search`
- 只保留 L1/L2 system memory block，不额外注入 L0 消息
- 将最终结果回填到正式 assistant 会话消息流

`workflow_dag.agent` wrapper 负责：
- 解析节点配置（toolNames、knowledgeEnabled、knowledgeMode、knowledgeTopK、memoryMode）
- 组装节点视角 prompt 与 NodeOutput
- `memoryMode=auto` 时注入 L0 消息流 + L1/L2 system memory block
- 追加 workflow 节点 trace 上下文（如 `nodeId/nodeType/nodeExecutionId`）

### 3) Unified Execution Semantics
- 默认最大迭代次数统一为 `12`。
- 每轮最多执行一个工具调用；如果模型返回多个 tool call，仅执行第一个并记录 warning。
- 非法工具与工具执行异常都视为终止条件，不做静默降级。
- 顶层 `agent_loop` 保留原有“超过最大迭代时返回友好提示文本”的用户体验，但主循环停止条件和计数逻辑与 workflow `agent` 一致。

### 4) KB Integration
共享内核只接收“已经绑定好的 KB 工具”，不直接读取 skill 或 node 配置。
- `agent_loop` wrapper 将 `kb_config.enabled` 转为内建 `kb_search`。
- `workflow_dag.agent` wrapper 将 `knowledgeEnabled/mode/topK` 转为内建 `kb_search`。
- 两边共享相同的 KB 工具调用循环与 `toolKind=knowledge` 追踪语义。

### 5) Trace Compatibility
共享内核统一发 `tool_call_start/end`，并补齐：
- `agentRound`
- `toolCallIndex`
- `toolKind`

`workflow_dag.agent` 继续通过 wrapper/节点元数据补充 workflow 侧上下文；`agent_loop` 不新增 workflow 节点上下文，但与 workflow 在工具循环层的 trace 结构保持一致。

## Risks / Trade-offs
- 风险：顶层与 workflow 两边已有细微行为差异，统一时可能引入回归。
  - 缓解：保留 wrapper 差异职责，并跑顶层 chat、workflow DAG、validator、workflow test-run 关键回归。
- 风险：旧 `agent_node.py` / `agent_tool_node.py` 仍存在，后续可能被误用。
  - 缓解：本次先确保它们不再出现在真实主执行路径，后续再做单独清理变更。

## Migration Plan
1. 新增共享 `agent_execution_core`。
2. 切换 `agent_loop` 主路径到共享内核。
3. 切换 `workflow_dag.agent` 到共享内核。
4. 回归 trace/memory/KB 行为并更新 OpenSpec。
