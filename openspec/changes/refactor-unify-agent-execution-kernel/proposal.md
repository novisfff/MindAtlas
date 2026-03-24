# Change: Refactor Unified Agent Execution Kernel

## Why
当前顶层 `agent_loop` 和 `workflow_dag.agent` 都支持“模型自主决定是否调用工具”，但后台主循环实现分散在两套代码路径中。这样会放大行为漂移风险，尤其是在工具失败语义、KB 内建工具、trace 扩展字段、流式输出和默认最大迭代次数上。

## What Changes
- 抽出共享 `agent_execution_core`，统一 LLM 回合、单工具串行调用、KB 内建工具接入、流式输出与 trace 事件扩展。
- 用共享内核重写顶层 `agent_loop` 主执行路径和 `workflow_dag.agent` 节点执行路径。
- 保留两层 wrapper 的接线职责与差异语义：
  - `agent_loop` 保持 skill 视角 prompt、仅注入 L1/L2 system memory block、不额外注入 L0 消息。
  - `workflow_dag.agent` 保持节点视角 prompt、`memoryMode=auto` 下继续使用 L0 消息流 + L1/L2 system memory block。
- 统一默认最大迭代次数为 `12`，统一工具失败/非法工具的终止语义与工具追踪字段。
- 不新增顶层 skill 公共配置，不修改 workflow `agent` 公共配置，不修改 chat/run/SSE 事件名。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - Backend workflow engine shared agent execution core
  - top-level `agent_loop` subgraph wrapper
  - workflow DAG `agent` node wrapper
  - trace / runtime event payload compatibility tests
