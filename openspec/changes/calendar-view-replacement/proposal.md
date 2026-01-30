# Calendar View Replacement

## Context

### User Need
将现有的时间轴（Timeline）视图完全替换为日历看板视图，提供更直观的时间维度展示和交互体验。

### Current State
- 现有 `TimelinePage` 位于 `frontend/src/features/timeline/`
- 采用垂直时间轴布局，按时间倒序展示 Entry
- 支持按年份筛选
- Entry 有三种时间模式：`NONE`、`POINT`、`RANGE`
- 时间字段：`timeAt`（单点）、`timeFrom`/`timeTo`（范围）

### User Decisions
- **替换策略**: 完全替换时间轴
- **视图模式**: 月视图 + 周视图 + 日视图
- **拖拽交互**: 需要支持
- **RANGE 展示**: 跨日条形（类似 Google Calendar）
- **溢出处理**: "+N more" 折叠模式
- **键盘导航**: 需要支持
- **周起始日**: 周一
- **API 更新**: 新增 PATCH 端点用于拖拽更新时间
- **时区策略**: 保持 UTC 存储，前端负责转换
- **快速创建**: 支持点击空白日期创建 Entry
- **时间粒度**: 日期级别（非小时级别）

---

## Requirements

### R1: 月视图 (Month View)
**场景**: 用户需要查看整月的 Entry 分布概览

**约束**:
- 显示完整月份网格（6 行 × 7 列）
- 每个日期格子显示前 2-3 个 Entry，超出显示 "+N more"
- 点击 "+N more" 展开完整列表（弹窗或展开）
- RANGE 类型 Entry 显示为跨日条形
- 点击日期可进入日视图
- 支持月份切换（上/下月、快速跳转）
- 支持"今日"快捷按钮

**成功判据**:
- [ ] 月视图正确渲染 42 个日期格子
- [ ] RANGE Entry 正确跨越多个日期格子
- [ ] 月份切换响应时间 < 100ms

### R2: 周视图 (Week View)
**场景**: 用户需要查看一周内的详细安排

**约束**:
- 显示 7 天列布局
- 每天显示完整 Entry 列表
- RANGE Entry 跨列显示
- 支持周切换

**成功判据**:
- [ ] 周视图正确显示 7 天
- [ ] Entry 按日期正确分组
- [ ] 跨周 RANGE Entry 正确截断显示

### R3: 日视图 (Day View)
**场景**: 用户需要查看单日所有 Entry 详情

**约束**:
- 显示选中日期的所有 Entry
- 支持快速创建新 Entry（预填当日日期）
- 显示 Entry 完整信息（标题、类型、标签、摘要）

**成功判据**:
- [ ] 日视图正确显示当日所有 Entry
- [ ] 新建 Entry 自动填充当前日期

### R4: 拖拽交互 (Drag & Drop)
**场景**: 用户需要快速调整 Entry 的时间

**约束**:
- 支持拖拽 Entry 到不同日期
- 拖拽后自动更新 Entry 的时间字段
- POINT 类型更新 `timeAt`
- RANGE 类型更新 `timeFrom`（保持时长不变）
- 提供视觉反馈（拖拽预览、放置提示）

**成功判据**:
- [ ] 拖拽操作正确更新后端数据
- [ ] 乐观更新 UI，失败时回滚
- [ ] 拖拽过程有清晰视觉反馈

### R5: Entry 展示样式
**场景**: 在日历中清晰展示不同类型的 Entry

**约束**:
- 使用 Entry Type 的颜色作为背景/边框色
- 显示 Entry 标题（过长截断）
- RANGE Entry 显示为条形，跨越开始到结束日期
- 悬停显示完整信息（Tooltip/HoverCard）
- 点击进入 Entry 详情页

**成功判据**:
- [ ] Entry 颜色与类型配置一致
- [ ] 长标题正确截断并显示省略号
- [ ] HoverCard 显示完整信息

### R6: 键盘导航 (Keyboard Navigation)
**场景**: 用户需要高效地使用键盘浏览日历

**约束**:
- 方向键切换选中日期
- Enter 键查看选中日期详情或进入日视图
- Escape 键返回上级视图
- T 键快速跳转到今日

**成功判据**:
- [ ] 方向键正确移动焦点
- [ ] 焦点状态有清晰视觉指示
- [ ] 键盘操作与鼠标操作等效

---

## Technical Constraints

### 前端技术栈
- React 18 + TypeScript
- TanStack Query（数据获取）
- Tailwind CSS（样式）
- date-fns（日期处理，已安装）
- Zustand（状态管理）

### 组件库选择约束
- 优先使用现有 UI 组件（dialog, hover-card, tooltip）
- 日历核心逻辑需自行实现或引入轻量库
- 拖拽可考虑：原生 HTML5 DnD / @dnd-kit / react-beautiful-dnd

### 后端约束
- 无需后端改动，复用现有 Entry API
- 可能需要优化查询：按日期范围筛选 Entry

### 数据模型约束
- `timeMode: NONE` 的 Entry 不在日历中显示
- `timeMode: POINT` 使用 `timeAt` 字段
- `timeMode: RANGE` 使用 `timeFrom` + `timeTo` 字段

---

## Dependencies

### 内部依赖
- `useEntriesQuery` - 获取 Entry 列表
- `useUpdateEntryMutation` - 更新 Entry（拖拽后）
- Entry 类型定义 (`frontend/src/types/index.ts`)

### 外部依赖（待评估）
- 日历组件库（可选）
- 拖拽库（推荐 @dnd-kit）

---

## Risks

### R1: 性能风险
- **问题**: 大量 Entry 时日历渲染可能卡顿
- **缓解**: 虚拟化长列表、按可视范围加载数据

### R2: 跨日 Entry 布局复杂度
- **问题**: RANGE Entry 跨越多日的布局计算复杂
- **缓解**: 参考 Google Calendar 的分层布局算法

### R3: 拖拽体验
- **问题**: 移动端拖拽体验可能不佳
- **缓解**: 移动端提供替代交互（长按菜单）

---

## Open Questions

*所有关键问题已解决*

---

## References

- [Google Calendar UI](https://calendar.google.com) - 月/周/日视图参考
- [Notion Calendar](https://notion.so/product/calendar) - 数据库集成参考
- [FullCalendar](https://fullcalendar.io) - 开源日历组件参考
