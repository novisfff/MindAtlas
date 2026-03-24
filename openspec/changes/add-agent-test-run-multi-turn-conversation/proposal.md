# Change: Add Agent Test Run Multi-Turn Conversation

## Why
当前 Agent 草稿试运行只支持单轮请求。每次发送测试输入都会覆盖上一次运行上下文，无法在编辑器里验证多轮追问、上下文承接和 Agent 工具行为在连续对话中的表现。

## What Changes
- 为 Agent 草稿试运行请求增加可选 `history`，让后端在下一轮执行时把先前 `user/assistant` 消息一并传给引擎。
- 将 Agent 编辑页右侧试运行面板改为消息列表视图，保留多轮用户/助手消息，而不是只显示最后一轮结果。
- 每条助手消息保留自己的工具调用链路，支持连续对话下按轮查看 Agent 的工具使用过程。
- 保持现有路由、SSE 事件名和正式 assistant chat 接口不变。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - backend agent test-run request schema and stream service
  - frontend agent editor test-run store and panel rendering
