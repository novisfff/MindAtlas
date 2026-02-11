# Codebase Refactoring 2026 - Task List

## Execution Order

### Phase 1: 独立任务 (可并行)
- [x] F1: 拆分 SkillRow (Gemini) ✓
- [x] F3: 拆分 ToolEditor (Gemini) ✓
- [x] B6: 优化 ai_registry runtime (Codex) ✓
- [x] B3: 优化 lightrag outbox (Codex) ✓

### Phase 2: 依赖任务
- [x] F2: 拆分 KnowledgeGraph (Claude) ✓
- [x] B1: 重构 assistant agent (Codex+Claude) ✓
- [x] B5: 统一 attachment 处理 (Codex+Claude) ✓

### Phase 3: 抽象任务
- [x] F4: 统一 Manager 组件 (Gemini+Claude) ✓
- [x] B2: 统一 tools/skills 注册 (Codex+Claude) ✓
- [x] B4: 提取 lightrag 缓存 (Codex+Claude) ✓

### Phase 4: 优化任务
- [x] F5: 优化 Calendar 视图 (Gemini) ✓
- [x] F6: 优化 EntryForm (Gemini) ✓

---

## Task Details

### F1: 拆分 SkillRow 组件
- **负责**: Gemini
- **文件**: `frontend/src/features/assistant-config/components/SkillRow.tsx`
- **输出**:
  - `SkillRow.tsx` (主组件, <150行)
  - `SkillRowDisplay.tsx` (只读显示)
  - `SkillRowEditor.tsx` (编辑表单)
  - `SkillStepsEditor.tsx` (步骤编辑器)
  - `useSkillForm.ts` (表单状态hook)
- **验收**: 每个文件 < 200 行

### F2: 拆分 KnowledgeGraph 组件
- **负责**: Gemini
- **文件**: `frontend/src/features/graph/components/KnowledgeGraph.tsx`
- **输出**:
  - `KnowledgeGraph.tsx` (主组件, <250行)
  - `useGraphData.ts` (数据处理hook)
  - `useGraphInteraction.ts` (交互hook)
  - `GraphFilters.tsx` (过滤面板)
  - `GraphTooltip.tsx` (tooltip组件)
- **验收**: 主组件 < 250 行

### F3: 拆分 ToolEditor 组件
- **负责**: Gemini
- **文件**: `frontend/src/features/assistant-config/components/ToolEditor.tsx`
- **输出**:
  - `ToolEditor.tsx` (主组件, <200行)
  - `JsonSchemaEditor.tsx` (JSON编辑器)
  - `useToolValidation.ts` (验证hook)
- **验收**: 主组件 < 200 行

### F4: 统一 Manager 组件模式
- **负责**: Gemini
- **依赖**: F1, F3
- **文件**: 多个 Manager 组件
- **输出**:
  - `GenericListManager.tsx` (抽象组件)
  - 重构 SkillManager, ToolManager, TagManager, TypeManager
- **验收**: 消除 50% 重复代码

### F5: 优化 Calendar 视图
- **负责**: Gemini
- **文件**: MonthView.tsx, WeekView.tsx
- **输出**:
  - `useCalendarLayout.ts` (布局hook)
  - 优化后的视图组件
- **验收**: 每个视图 < 250 行

### F6: 优化 EntryForm
- **负责**: Gemini
- **文件**: EntryForm.tsx
- **输出**:
  - `useEntryForm.ts` (表单hook)
  - `AiSuggestionPanel.tsx`
  - `TagSuggestionPanel.tsx`
- **验收**: 主组件 < 200 行

---

## Backend Tasks

### B1: 重构 assistant agent
- **负责**: Codex
- **依赖**: B6
- **文件**: agent.py, service.py
- **步骤**:
  1. 引入 AgentExecutor 基类
  2. 抽离事件适配器
  3. Skill 执行下沉到 executor
- **验收**: 单文件 < 400 行

### B2: 统一 tools/skills 注册
- **负责**: Codex
- **依赖**: B1
- **文件**: tools/*.py, skills/*.py
- **步骤**:
  1. 建立统一注册中心
  2. 固化解析路径
  3. 显式元数据标记
- **验收**: 解析逻辑统一

### B3: 优化 lightrag outbox
- **负责**: Codex
- **文件**: outbox_repo.py, attachment_outbox_repo.py
- **步骤**:
  1. 抽象通用 Outbox 仓储基类
  2. 统一失败处理策略
- **验收**: 消除重复 outbox 逻辑

### B4: 提取 lightrag 缓存
- **负责**: Codex
- **依赖**: B3
- **文件**: service.py, manager.py
- **步骤**:
  1. 提取缓存管理模块
  2. 统一 cache key 规范
  3. 定义显式失效接口
- **验收**: 缓存逻辑独立

### B5: 统一 attachment 处理
- **负责**: Codex
- **文件**: parser.py, worker.py, preview.py
- **步骤**:
  1. 建立 FileProcessor 基类
  2. 封装处理 adapter
- **验收**: 处理流程清晰

### B6: 优化 ai_registry runtime
- **负责**: Codex
- **文件**: runtime.py, service.py
- **步骤**:
  1. 拆分 resolve_openai_compat_config
  2. 提取 SSRF 验证到共享模块
  3. 统一 base_url 标准化
- **验收**: 运行时解析 < 150 行

---

## Conventions

- **Hook 命名**: 由 AI 根据上下文自动决定
- **测试要求**: 关键逻辑需要测试
- **分支策略**: 单一分支开发
