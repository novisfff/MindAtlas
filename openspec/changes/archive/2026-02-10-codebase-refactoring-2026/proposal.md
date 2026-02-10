# Codebase Refactoring 2026

## Context

MindAtlas 项目经过多轮功能迭代，代码库积累了一些技术债务。本次重构旨在提升代码质量和可维护性，采用前后端分工模式：
- **前端重构**：由 Gemini 负责
- **后端重构**：由 Codex 负责

### 扫描发现

**后端**：16 个模块，121 个 Python 文件
- 大部分模块遵循 models → schemas → service → router 模式
- `assistant` 和 `lightrag` 模块结构复杂，需要重点关注

**前端**：13 个 feature 模块，172 个 TSX/TS 文件
- 存在多个超大组件（>300 行）
- Manager/Row 组件存在重复模式
- 状态管理不一致（app-store vs chat-store）

---

## Requirements

### Phase 1: 前端超大组件拆分 (Gemini)

#### REQ-F1: 拆分 SkillRow 组件
- **场景**：assistant-config/SkillRow.tsx 798 行，职责过多
- **约束**：
  - 拆分为 SkillRowDisplay, SkillRowEditor, SkillRowActions
  - 保持现有 API 接口不变
  - 使用 composition pattern
- **验收**：每个子组件 < 200 行

#### REQ-F2: 拆分 KnowledgeGraph 组件
- **场景**：graph/KnowledgeGraph.tsx 779 行，可视化逻辑未分层
- **约束**：
  - 分离数据层、渲染层、交互层
  - 提取 useGraphData, useGraphInteraction hooks
- **验收**：主组件 < 300 行，hooks 独立文件

#### REQ-F3: 拆分 ToolEditor 组件
- **场景**：assistant-config/ToolEditor.tsx 467 行
- **约束**：
  - 提取 JsonEditor 为独立组件
  - 分离验证逻辑到 useToolValidation hook
- **验收**：主组件 < 250 行

#### REQ-F4: 统一 Manager 组件模式
- **场景**：SkillManager, ToolManager, TagManager, TypeManager 重复模式
- **约束**：
  - 创建 GenericListManager 抽象组件
  - 各 Manager 组件继承或组合使用
- **验收**：消除 50% 以上重复代码

#### REQ-F5: 优化 Calendar 视图组件
- **场景**：MonthView 366 行, WeekView 334 行，日期逻辑重复
- **约束**：
  - 提取共享的 dateUtils 和 layoutUtils
  - 创建 useCalendarLayout hook
- **验收**：每个视图组件 < 250 行

#### REQ-F6: 优化 EntryForm 组件
- **场景**：entries/EntryForm.tsx 373 行，表单+AI+标签混合
- **约束**：
  - 提取 useEntryForm hook 管理表单状态
  - 分离 AiSuggestionPanel 组件
  - 分离 TagSuggestionPanel 组件
- **验收**：主组件 < 200 行

---

### Phase 2: 后端模块优化 (Codex)

#### REQ-B1: 重构 assistant 模块结构
- **场景**：assistant 模块包含 agent.py, tools/, skills/ 等复杂结构
- **约束**：
  - 统一 tools 和 skills 的注册机制
  - 提取 AgentExecutor 基类
  - 简化 service.py 中的回调链
- **验收**：模块结构清晰，单个文件 < 400 行

#### REQ-B2: 优化 lightrag 模块
- **场景**：lightrag 模块 12+ 文件，缓存和并发逻辑复杂
- **约束**：
  - 统一 outbox 处理模式
  - 提取缓存管理为独立类
  - 简化 clients/ 目录结构
- **验收**：消除重复的 outbox 逻辑

#### REQ-B3: 统一 attachment 处理流程
- **场景**：attachment 模块有 parser.py, worker.py, preview.py
- **约束**：
  - 统一文件处理管道
  - 提取 FileProcessor 基类
- **验收**：处理流程清晰，易于扩展新文件类型

#### REQ-B4: 优化 ai_registry 运行时解析
- **场景**：ai_registry/runtime.py 包含复杂的模型解析逻辑
- **约束**：
  - 简化 resolve_openai_compat_config 函数
  - 提取 SSRF 验证为独立模块
- **验收**：运行时解析逻辑 < 150 行

---

### Phase 3: 代码风格统一

#### REQ-S1: 前端状态管理统一
- **场景**：app-store.ts 和 chat-store.ts 分离
- **约束**：
  - 评估是否合并或保持分离
  - 统一 Zustand store 的命名和结构
- **验收**：状态管理模式一致

#### REQ-S2: 后端异常处理统一
- **场景**：部分模块使用 try/except 静默失败
- **约束**：
  - 统一使用 ApiException
  - 添加适当的日志记录
- **验收**：所有模块异常处理一致

---

## Task Breakdown

按中等粒度 (3-5 文件) 拆分任务，按优先级排序：

### 前端任务 (Gemini)

| ID | 任务 | 涉及文件 | 依赖 |
|----|------|----------|------|
| F1 | 拆分 SkillRow 组件 | SkillRow.tsx + 3 新文件 | - |
| F2 | 拆分 KnowledgeGraph | KnowledgeGraph.tsx + 2 hooks | - |
| F3 | 拆分 ToolEditor | ToolEditor.tsx + 2 新文件 | - |
| F4 | 创建 GenericListManager | 新组件 + 4 Manager 重构 | F1, F3 |
| F5 | 优化 Calendar 视图 | MonthView + WeekView + hooks | - |
| F6 | 优化 EntryForm | EntryForm.tsx + 2 新组件 | - |

### 后端任务 (Codex)

| ID | 任务 | 涉及文件 | 依赖 |
|----|------|----------|------|
| B1 | 重构 assistant agent | agent.py + service.py | - |
| B2 | 统一 tools/skills 注册 | tools/*.py + skills/*.py | B1 |
| B3 | 优化 lightrag outbox | outbox_repo.py + attachment_outbox_repo.py | - |
| B4 | 提取 lightrag 缓存管理 | service.py + manager.py | B3 |
| B5 | 统一 attachment 处理 | parser.py + worker.py + preview.py | - |
| B6 | 优化 ai_registry runtime | runtime.py + service.py | - |

---

## Constraints Summary

### 硬约束
- 不改变现有 API 接口
- 保持数据库 schema 不变
- 保持现有功能行为一致

### 软约束
- 单个组件/文件 < 300 行
- 遵循现有命名约定
- 优先使用 composition 而非 inheritance

---

## Success Criteria

1. **代码质量**
   - 所有超大组件 (>300 行) 完成拆分
   - 消除 50% 以上重复代码模式

2. **可维护性**
   - 模块结构清晰，职责单一
   - 新增功能可快速定位修改位置

3. **功能完整性**
   - 所有现有功能正常运行
   - 无回归 bug
