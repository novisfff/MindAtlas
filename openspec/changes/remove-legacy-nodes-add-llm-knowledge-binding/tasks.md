## 1. OpenSpec
- [x] 1.1 创建 proposal/tasks/spec delta 并通过 `openspec validate --strict --no-interactive`

## 2. Backend Node Model & Validation
- [x] 2.1 删除 `template` / `variable_aggregator` 节点类型定义与节点目录暴露
- [x] 2.2 在 converter/validator 对下线节点类型返回显式错误
- [x] 2.3 清理 validator 中 aggregator 相关规则与并行分支 join 依赖

## 3. Backend Runtime
- [x] 3.1 移除 template/aggregator builder 与分发分支
- [x] 3.2 增强 KR 节点：支持 `mode/topK` 覆盖并输出结构化 `json_fields`
- [x] 3.3 增强 LLM 节点：新增知识绑定配置并按 `knowledgeInjectMode` 注入
- [x] 3.4 默认排除 KR 的隐式上下文注入，仅允许显式绑定注入

## 4. Tooling
- [x] 4.1 扩展 `kb_search(query, mode=None, top_k=None)` 且保持向后兼容

## 5. Frontend Workflow Editor
- [x] 5.1 移除 template/aggregator 的 palette、渲染、属性面板与标签映射
- [x] 5.2 KR 面板新增 `mode/topK` 配置
- [x] 5.3 LLM 面板新增知识绑定配置（启用、来源、注入模式、最大引用）
- [x] 5.4 变量引用菜单补齐 KR 结构化字段
- [x] 5.5 更新中英文 i18n 文案

## 6. Tests
- [x] 6.1 新增/更新后端校验测试（下线节点、LLM 知识来源约束）
- [x] 6.2 新增/更新执行测试（KR 输出结构、LLM 注入模式）
- [x] 6.3 运行关键测试并修复回归
