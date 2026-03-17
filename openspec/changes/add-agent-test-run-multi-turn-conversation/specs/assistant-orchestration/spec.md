## ADDED Requirements

### Requirement: Agent Draft Test Run SHALL Support Multi-Turn Conversation
系统 SHALL 允许 Agent 草稿试运行在编辑器内连续发送多轮消息，并在下一轮执行时携带先前的 user/assistant 上下文。

#### Scenario: Follow-up question reuses prior turns
- **WHEN** 用户在 Agent 草稿试运行中完成第一轮问答后继续发送第二轮问题
- **THEN** 系统 SHALL 将之前的 user/assistant 消息作为 history 传入下一轮执行
- **AND** Agent SHALL 能基于前文继续回答而不是从空上下文开始

### Requirement: Agent Draft Test Run SHALL Preserve Turn-Level Tool Chains
系统 SHALL 在 Agent 草稿试运行的连续对话中，为每条助手回复保留并展示对应的工具调用链路。

#### Scenario: Assistant reply displays its own tool chain
- **WHEN** 某一轮 Agent 回复过程中发生工具调用
- **THEN** 该轮 assistant 消息 SHALL 展示对应 Tool Chain
- **AND** Tool Chain SHALL 显示在该条回复内容上方
- **AND** SHALL NOT 覆盖或串联到其他轮次的 assistant 回复

### Requirement: Agent Draft Test Run SHALL Preserve Existing Public Transport
多轮对话能力 SHALL NOT 改变 Agent 草稿试运行路由或 SSE 事件名。

#### Scenario: Existing transport remains compatible
- **WHEN** 前端发起 Agent 草稿试运行请求
- **THEN** 系统 SHALL 继续使用现有 test-run 路由与现有 SSE 事件名
- **AND** 新增能力仅通过可选 history 请求字段和前端消息聚合实现
