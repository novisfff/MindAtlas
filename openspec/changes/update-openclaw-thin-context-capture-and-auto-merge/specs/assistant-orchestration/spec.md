## MODIFIED Requirements

### Requirement: Workflow Runtime Provides Sys Variables
workflow 执行态 SHALL 注入系统变量集合，并可在条件右值模板和其他模板解析中引用。

#### Scenario: Generic workflow execution still receives baseline sys vars
- **WHEN** 任意 workflow 运行
- **THEN** `sys.date`、`sys.datetime`、`sys.conversation_id` SHALL continue to be available

#### Scenario: OpenClaw workflow execution receives generic request sys vars
- **WHEN** workflow 通过 OpenClaw capability runtime 执行且请求上下文带有 OpenClaw source/channel/session/tool 元数据
- **THEN** workflow SHALL 额外注入 `sys.request_source`、`sys.request_channel`、`sys.request_session`、`sys.request_tool`
- **AND** workflow 模板可直接引用这些字段而无需把它们声明为 start structured fields
