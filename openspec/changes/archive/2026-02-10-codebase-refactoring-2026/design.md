# Codebase Refactoring 2026 - Technical Design

## Multi-Model Analysis Summary

### Backend Analysis (Codex)

**推荐执行顺序**: B6 → B1 → B2 → B3 → B4 → B5

**核心发现**:
1. `assistant` 模块的 `SkillExecutor` 达 1477 行，需要谨慎重构
2. `lightrag` 的 outbox 并发语义是高风险区
3. SSRF 验证逻辑在多处重复

### Frontend Analysis (Based on Code Scan)

**推荐执行顺序**: F1 → F3 → F2 → F4 → F5 → F6

**核心发现**:
1. `SkillRow` 798 行，混合了显示、编辑、验证逻辑
2. `KnowledgeGraph` 779 行，使用 react-force-graph-2d，状态管理复杂
3. Manager 组件存在明显的 CRUD 模式重复

---

## Technical Decisions

### TD-1: 组件拆分策略
- **决策**: 使用 Composition Pattern，不使用继承
- **理由**: React 推荐组合优于继承，便于测试和复用

### TD-2: 后端抽象层策略
- **决策**: 先做内部抽象层，再迁移调用方
- **理由**: 可回滚，降低一次性改动风险

### TD-3: Outbox 统一策略
- **决策**: 只统一状态机和 SQL 片段到共享 mixin
- **理由**: 保留两个 repo 外壳，减少行为改动面

### TD-4: 缓存管理策略
- **决策**: 先保持内存缓存实现，仅提炼接口
- **理由**: 后续可替换为 cachetools 或 Redis

---

## Detailed Task Specifications

### Frontend Tasks

#### F1: 拆分 SkillRow 组件

**当前问题**:
- 798 行代码，混合显示、编辑、验证逻辑
- 多个 useState 管理复杂表单状态
- Steps 编辑器内嵌在组件中

**拆分方案**:
```
SkillRow.tsx (主组件, <150行)
├── SkillRowDisplay.tsx (只读显示, ~100行)
├── SkillRowEditor.tsx (编辑表单, ~200行)
├── SkillStepsEditor.tsx (步骤编辑器, ~150行)
└── useSkillForm.ts (表单状态hook, ~100行)
```

**验收标准**:
- 主组件 < 150 行
- 每个子组件 < 200 行
- 现有功能无回归

#### F2: 拆分 KnowledgeGraph 组件

**当前问题**:
- 779 行代码，可视化逻辑未分层
- 多个 useState 管理图形状态
- 过滤、搜索、tooltip 逻辑混合

**拆分方案**:
```
KnowledgeGraph.tsx (主组件, <250行)
├── useGraphData.ts (数据处理hook)
├── useGraphInteraction.ts (交互hook)
├── GraphFilters.tsx (过滤面板)
└── GraphTooltip.tsx (tooltip组件)
```

**验收标准**:
- 主组件 < 250 行
- hooks 独立文件
