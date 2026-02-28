## 1. OpenSpec
- [x] 1.1 更新 proposal/design/spec/tasks，移除并存与兼容描述
- [x] 1.2 执行 `openspec validate langgraph-assistant-upgrade --strict --no-interactive`

## 2. Database & Model
- [x] 2.1 新增 Alembic：删除 legacy skills、固定 mode、收敛 pattern、删除 `assistant_skill_step`
- [x] 2.2 清理 ORM 中 `AssistantSkillStep` 及其 relationship 引用
- [x] 2.3 清理 Alembic `env.py` 对 `AssistantSkillStep` 的导入

## 3. Backend Runtime
- [x] 3.1 移除 legacy `SkillExecutor` 执行路径
- [x] 3.2 `LangGraphEngine` 仅接受 `agent_loop|workflow_dag`
- [x] 3.3 converter/registry/service 全面移除 steps/sequential 兼容逻辑

## 4. Frontend
- [x] 4.1 `SkillRowEditor` 改为两态：工作流模式/Agent模式
- [x] 4.2 `SkillManager` badge 与详情去除 steps 展示
- [x] 4.3 i18n 清理旧模式文案，新增两态文案

## 5. Tests
- [x] 5.1 更新后端单测：拒绝 legacy mode/pattern
- [x] 5.2 更新前端类型/表单相关测试（如有）
- [x] 5.3 运行关键测试并修复回归

## 6. Workflow Horizontal Layout
- [x] 6.1 前端 workflow 节点句柄改为左进右出，if_else 分支句柄改为右侧纵向分布
- [x] 6.2 空白 workflow 默认模板改为横向 `start -> llm`
- [x] 6.3 官方系统默认 workflow（quick_stats/smart_capture/periodic_review）坐标改为横向布局
- [x] 6.4 新增/更新测试断言：系统默认 workflow 边方向 X 递增，分支目标 Y 有差异

## 7. If/Else Productization
- [x] 7.1 前后端 `if_else` 配置收敛为 `branches + elseHandle`，保留 legacy 读兼容
- [x] 7.2 属性面板支持 IF/ELIF/ELSE 编辑（AND/OR、条件增删、操作符下拉、右值变量引用）
- [x] 7.3 运行时支持 `sys.*` 变量注入与模板解析（date/datetime/conversation_id）
- [x] 7.4 校验器强化：ELSE 必连、handle 一一映射、条件变量/操作符/值约束
- [x] 7.5 新增/更新单测覆盖条件分支执行和校验场景

## 8. Label/Reference UX 收敛
- [x] 8.1 节点 Label 唯一化（大小写不敏感）与非法字符约束（禁止 `.`）接入前后端
- [x] 8.2 前端变量展示/插入切换为 `Label.field`，保存前转换回 `node_id.field`
- [x] 8.3 工作流加载时自动修复重复 Label（补 `#N`）并支持后续持久化
- [x] 8.4 变量面板升级为两级分组（节点/系统变量 -> 字段）
- [x] 8.5 后端校验与单测补充（空 Label、重复 Label、含点 Label）
