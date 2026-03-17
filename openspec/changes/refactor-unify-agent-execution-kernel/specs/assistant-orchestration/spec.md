## ADDED Requirements

### Requirement: Agent Loop And Workflow Agent SHALL Share A Common Execution Kernel
系统 SHALL 使用同一套后台 agent 执行内核来驱动顶层 `agent_loop` 与 `workflow_dag.agent` 的工具循环、KB 工具接入、流式输出和工具追踪。

#### Scenario: Top-level agent_loop uses shared execution kernel
- **WHEN** supervisor 选中一个 `langgraph_pattern=agent_loop` 的 skill 并开始执行
- **THEN** 系统 SHALL 通过共享 agent 执行内核驱动 LLM 回合与工具调用循环
- **AND** SHALL NOT 继续依赖旧的双节点 `agent_node -> agent_tool_node` 作为真实主执行路径

#### Scenario: Workflow DAG agent uses shared execution kernel
- **WHEN** workflow 运行一个 `agent` 节点
- **THEN** 系统 SHALL 通过同一共享 agent 执行内核驱动该节点的 LLM 回合与工具调用循环
- **AND** 共享内核 SHALL 与顶层 `agent_loop` 使用一致的单工具串行执行策略

### Requirement: Shared Agent Kernel SHALL Preserve Wrapper-Specific Memory Semantics
共享 agent 执行内核 SHALL 不直接决定 memory 注入策略；wrapper SHALL 在调用内核前完成各自的 memory 组装。

#### Scenario: agent_loop keeps top-level memory semantics
- **WHEN** 顶层 `agent_loop` 执行
- **THEN** wrapper SHALL 只保留现有 L1/L2 system memory block 语义
- **AND** SHALL NOT 额外注入 L0 对话消息流

#### Scenario: Workflow agent keeps node memory semantics
- **WHEN** `workflow_dag.agent` 在 `memoryMode=auto` 下执行
- **THEN** wrapper SHALL 注入 L0 最近对话消息流
- **AND** SHALL 将 L1/L2 作为 system memory block 注入

### Requirement: Shared Agent Kernel SHALL Use Unified Tool Loop And Trace Metadata
共享 agent 执行内核 SHALL 使用统一的串行单工具循环，并为工具调用输出一致的追踪元数据。

#### Scenario: Only the first tool call is executed per round
- **WHEN** 模型在同一轮返回多个 tool call
- **THEN** 共享内核 SHALL 只执行第一个 tool call
- **AND** SHALL 记录告警日志

#### Scenario: Tool tracing metadata is aligned across wrappers
- **WHEN** 顶层 `agent_loop` 或 `workflow_dag.agent` 触发工具调用
- **THEN** `tool_call_start` 与 `tool_call_end` payload SHALL 包含 `agentRound`、`toolCallIndex` 与 `toolKind`
- **AND** `workflow_dag.agent` SHALL 继续附带其 workflow 节点 trace 上下文

### Requirement: Shared Agent Kernel SHALL Preserve Existing Public Contracts
共享 agent 执行内核重构 SHALL NOT 改变顶层 skill 公共配置、workflow agent 公共配置或 chat/run/SSE 对外事件名。

#### Scenario: Top-level public config remains unchanged
- **WHEN** skill 使用顶层 `agent_loop`
- **THEN** 系统 SHALL 继续只暴露现有 skill 配置项
- **AND** SHALL NOT 因共享内核重构新增顶层 agent 公共配置字段

#### Scenario: Workflow agent public config remains unchanged
- **WHEN** workflow 编辑器创建或保存 `agent` 节点
- **THEN** 系统 SHALL 继续使用现有 `agent` 节点配置合同
- **AND** chat/run/SSE 事件名 SHALL 保持不变
