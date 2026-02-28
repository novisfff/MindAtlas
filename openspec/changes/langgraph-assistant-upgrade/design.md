## Context
历史上技能执行同时存在 `steps`、legacy `agent`、`langgraph(agent_loop/sequential/workflow_dag)` 多套语义。随着可视化工作流上线，`workflow_dag` 已可覆盖原 `sequential` 需求，继续并存会引发：
- 存储模型分裂（`assistant_skill_step` 与 DAG 节点并存）
- 执行器分裂（`SkillExecutor` 与 `LangGraphEngine` 并存）
- 前端模式心智负担（三态甚至四态）

## Goals / Non-Goals
- Goals:
  - 运行路径统一为 LangGraph。
  - 数据模型统一为 `assistant_skill + assistant_skill_node + assistant_skill_edge`。
  - 模式语义统一为 `langgraph_pattern` 双态：`agent_loop | workflow_dag`。
- Non-Goals:
  - 不保留旧模式数据兼容。
  - 不提供自动降级/fallback 到 legacy 执行器。

## Decisions
- Decision: `assistant_skill.mode` 固定 `langgraph`
  - Why: 保留列便于显式约束与运维检索，避免再次引入多 mode 分叉。
- Decision: 删除 `assistant_skill_step`
  - Why: 所有结构化编排都落入 DAG；旧表继续保留会造成双写与歧义。
- Decision: 删除 `sequential` pattern
  - Why: `workflow_dag` 已是更通用表达，保留 sequential 只会增加状态空间。
- Decision: 前端仅展示两种图模式
  - Why: 对用户暴露的“模式”应只对应可运行拓扑，而非历史实现细节。
- Decision: `workflow_dag` 采用横向布局标准（主链左→右，分支上/下展开）
  - Why: 与执行方向一致，降低复杂工作流阅读和调试成本；并统一默认模板与系统内置 workflow 的视觉语义。
- Decision: `if_else` 使用结构化分支模型（IF/ELIF/ELSE + branch-level AND/OR）
  - Why: 将节点配置、画布句柄与执行路由统一到同一语义模型，避免 legacy `conditions[]` 造成表达力不足与前后端歧义。
- Decision: 引入 workflow 运行时 `sys_vars` 最小集
  - Why: 支持条件分支和模板中的系统上下文引用，且不引入额外 DB schema。
- Decision: 工作流变量采用“显示层 Label / 存储层 node_id”双轨语义
  - Why: `node_id` 稳定不可变，适合执行与持久化；Label 更符合用户心智，适合编辑与引用选择。
- Decision: Label 必须大小写不敏感唯一，且禁止 `.` 字符
  - Why: 避免 `Label.field` 语法歧义，确保引用转换可逆且稳定。
- Decision: 变量选择器采用两级分组（节点/系统变量 -> 字段）
  - Why: 大图场景下可发现性更高，减少平铺变量列表的认知成本。

## Migration Plan
1. Alembic 迁移删除 `mode in ('steps','agent')` 的技能记录。
2. 将剩余技能 `mode` 全量更新为 `langgraph`。
3. 收敛 `langgraph_pattern` 到 `agent_loop|workflow_dag`，非法值设为 `agent_loop`（防止脏数据阻塞迁移）。
4. 删除 `assistant_skill_step` 表及相关索引/约束。
5. 应用层移除 steps 相关 ORM/Schema/API/执行逻辑。
6. 更新 workflow 编辑器句柄方向与空白模板默认坐标为横向。
7. 更新官方系统 workflow 默认坐标（仅 definitions），不覆盖已存在且已被用户调整的系统技能实例。
8. 将 if_else 配置统一迁移为 `branches + else_handle`（读取兼容 legacy，写入仅新结构）。
9. 执行态注入 `sys.date/sys.datetime/sys.conversation_id`，供模板与条件计算使用。
10. 编辑器加载 workflow 时自动修复重复 Label（补 `#N`），并在保存时持久化修复结果。
11. 序列化链路将显示层 `Label.field` 双向转换为存储层 `node_id.field`。

## Risks / Trade-offs
- 风险: 破坏式迁移会删除 legacy skill 数据。
  - Mitigation: 迁移脚本执行前通过备份保障可回滚。
- 风险: 前后端在同一发布窗口内必须一起升级。
  - Mitigation: 使用严格 schema 校验，阻断非法 payload，减少静默错误。

## Open Questions
- 无。该方案按开发阶段硬切执行，不保留兼容层。
