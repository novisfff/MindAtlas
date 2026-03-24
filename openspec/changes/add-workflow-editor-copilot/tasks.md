## 1. Backend
- [x] 1.1 新增 workflow copilot request/response schema 与 operation 合同
- [x] 1.2 新增 workflow copilot route 与后端 service
- [x] 1.3 实现 LLM JSON 输出解析、proposal 规范化、scope 校验、模拟应用和 validation 回填
- [x] 1.4 补充 backend 单元测试
- [x] 1.5 增强 `edit_selection` prompt，上送 `selectionIntent` / `primaryTarget` / `allowedExpansion`

## 2. Frontend
- [x] 2.1 扩展 workflow API types 与 copilot 调用函数
- [x] 2.2 新增 WorkflowCopilotPanel 组件与页面内临时会话
- [x] 2.3 接入顶部按钮、属性面板、校验清单、test-run 分析四个入口
- [x] 2.4 实现 proposal 预览、draft hash 并发保护、apply 后自动 validate / autolayout
- [x] 2.5 补齐中英文文案
- [x] 2.6 抽取可复用的只读工作流画布组件，并支持 Copilot 大画布预览叠层
- [x] 2.7 在 Copilot 面板中补齐预览切换、apply 后状态和“撤销本次应用”交互
- [x] 2.8 在 `edit_selection` 模式下显示当前编辑目标，并把属性面板默认指令改为明确指向当前节点

## 3. Validation
- [x] 3.1 执行 frontend `npm run build`
- [x] 3.2 执行 backend Copilot 定向测试与关键工作流回归
- [x] 3.3 执行 `openspec validate add-workflow-editor-copilot --strict --no-interactive`
