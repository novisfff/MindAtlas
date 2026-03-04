## 1. L0（近期原文窗口）
- [x] 1.1 定义 L0 构建器：从最近会话消息生成带角色标记的文本窗口。
- [x] 1.2 增加 L0 截断与预算控制（turns/chars）。
- [x] 1.3 在 runtime 内把 L0 放入统一 memory context（仅内存态）。
- [x] 1.4 增加 L0 观测日志：`source_count`、`trimmed_chars`。

## 2. L1（会话级增量摘要）
- [x] 2.1 新增 L1 持久化模型与迁移（`conversation_id` 唯一）。
- [x] 2.2 实现增量摘要器：`prev_l1 + 本轮增量 -> next_l1`。
- [x] 2.3 turn 结束后异步/后置更新 L1（fail-open）。
- [x] 2.4 读取 L1 并加入 memory context。
- [x] 2.5 增加日志与指标：更新耗时、摘要长度、失败率。

## 3. L2（会话+skill 事实记忆）
- [x] 3.1 新增 L2 持久化模型与迁移（`conversation_id + skill_name` 唯一）。
- [x] 3.2 定义 L2 facts JSON 结构与容量淘汰策略。
- [x] 3.3 本轮结束后增量更新 L2（按 selected skill）。
- [x] 3.4 仅加载当前 skill 对应 L2 并加入 memory context。
- [x] 3.5 增加日志与指标：facts 数量、裁剪次数、更新失败率。

## 4. Start `memoryMode` 与注入机制
- [x] 4.1 Start 配置扩展 `memoryMode=auto|off|structured`，默认 `auto`。
- [x] 4.2 `auto`：在 eligible LLM 节点自动拼接 memory block。
- [x] 4.3 `off`：禁用注入与字段暴露。
- [x] 4.4 `structured`：暴露 `start.memory_recent_dialogue` / `start.memory_conversation_summary` / `start.memory_skill_facts`，禁用自动注入（旧 `memory_l*` 名称非法）。
- [x] 4.5 增加 reserved 字段冲突校验（禁止业务字段占用 memory 字段名）。

## 5. 校验、编辑器与接口对齐
- [x] 5.1 更新 workflow validator：`start.memory_*` 的合法性随 `memoryMode` 变化。
- [x] 5.2 更新 assistant-config 前端 Start 设置面板与类型定义。
- [x] 5.3 保持 chat API/SSE 完全兼容，不新增用户命令入口。
- [x] 5.4 增加说明文案与默认值提示。

## 6. 测试与回归
- [x] 6.1 L0 单测：窗口抽取、截断、空历史。
- [x] 6.2 L1 单测：增量摘要更新、失败降级。
- [x] 6.3 L2 单测：会话+skill 隔离、去重/淘汰。
- [x] 6.4 runtime 集成：`auto/off/structured` 三模式行为。
- [x] 6.5 validator 回归：`start.memory_*` 引用合法性。
- [x] 6.6 端到端：连续对话中同 skill 承接与跨 skill 切换。
- [x] 6.7 回归现有 router/fallback/background-run/HITL 行为不变。
- [x] 6.8 执行 `openspec validate add-assistant-l0-l1-l2-short-memory --strict --no-interactive`。
