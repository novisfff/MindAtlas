# Calendar View - Technical Design

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    CalendarPage                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │              CalendarHeader                      │    │
│  │  [< Prev] [Today] [Next >]  [Month|Week|Day]    │    │
│  └─────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────┐    │
│  │         DndContext (from @dnd-kit)              │    │
│  │  ┌─────────────────────────────────────────┐   │    │
│  │  │   MonthView / WeekView / DayView        │   │    │
│  │  │   ┌─────┐ ┌─────┐ ┌─────┐              │   │    │
│  │  │   │Cell │ │Cell │ │Cell │  ...         │   │    │
│  │  │   │     │ │     │ │     │              │   │    │
│  │  │   └─────┘ └─────┘ └─────┘              │   │    │
│  │  └─────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## 2. Component Hierarchy

```
features/calendar/
├── CalendarPage.tsx           # 主页面，路由入口
├── components/
│   ├── CalendarHeader.tsx     # 导航栏（视图切换、日期导航）
│   ├── MonthView.tsx          # 月视图容器
│   ├── WeekView.tsx           # 周视图容器
│   ├── DayView.tsx            # 日视图容器
│   ├── CalendarCell.tsx       # 日期格子（Droppable）
│   ├── CalendarEvent.tsx      # Entry 展示（Draggable）
│   ├── MultiDayEvent.tsx      # 跨日 Entry 条形
│   ├── MoreEventsPopover.tsx  # "+N more" 弹出层
│   └── QuickCreateDialog.tsx  # 快速创建对话框
├── hooks/
│   ├── useCalendarNavigation.ts  # 日期导航逻辑
│   ├── useCalendarLayout.ts      # 布局计算（行分配）
│   └── useCalendarDnd.ts         # 拖拽逻辑封装
├── utils/
│   ├── dateUtils.ts           # 日期计算工具
│   └── layoutUtils.ts         # 布局算法
├── queries.ts                 # TanStack Query hooks
└── index.ts                   # 导出
```

## 3. State Management

### 3.1 URL State (react-router)
```typescript
// URL: /calendar?view=month&date=2025-01-15
interface CalendarUrlParams {
  view: 'month' | 'week' | 'day'  // 默认 'month'
  date: string                     // ISO date, 默认今日
}
```

### 3.2 Local State (useState)
```typescript
interface CalendarLocalState {
  focusedDate: Date | null      // 键盘导航焦点
  draggedEntryId: string | null // 拖拽中的 Entry
  hoverDate: Date | null        // 拖拽悬停日期
}
```

### 3.3 Server State (TanStack Query)
```typescript
// 按可视范围获取 Entries
useCalendarEntriesQuery({
  timeFrom: startOfMonth(date),
  timeTo: endOfMonth(date),
})
```

## 4. Layout Algorithm (Row-Packing)

### 4.1 多日事件行分配
```typescript
interface LayoutEntry {
  entry: Entry
  row: number        // 分配的行号 (0-based)
  startCol: number   // 起始列 (0-6 for week)
  span: number       // 跨越列数
}

function assignRows(entries: Entry[], weekStart: Date): LayoutEntry[] {
  // 1. 过滤并排序：开始日期升序，时长降序
  // 2. 贪心分配：每个 entry 放入第一个无冲突的行
  // 3. 返回带行号的布局信息
}
```

### 4.2 月视图特殊处理
- 每周独立计算行分配
- 跨周 Entry 在每周行首显示续接标记
- 单日格子最多显示 2-3 个 Entry + "+N more"

## 5. Drag & Drop Implementation

### 5.1 @dnd-kit 配置
```typescript
// CalendarPage.tsx
<DndContext
  sensors={sensors}
  onDragStart={handleDragStart}
  onDragEnd={handleDragEnd}
>
  <MonthView entries={entries} />
  <DragOverlay>
    {activeEntry && <CalendarEvent entry={activeEntry} />}
  </DragOverlay>
</DndContext>
```

### 5.2 拖拽更新逻辑
```typescript
function handleDragEnd(event: DragEndEvent) {
  const { active, over } = event
  if (!over) return

  const entryId = active.id as string
  const targetDate = over.id as string // ISO date

  // 乐观更新
  queryClient.setQueryData(['calendar-entries'], (old) => {
    // 更新 entry 的时间字段
  })

  // 调用 PATCH API
  patchEntryTimeMutation.mutate({
    id: entryId,
    targetDate,
  })
}
```

## 6. Backend API Changes

### 6.1 新增 PATCH 端点
```python
# backend/app/entry/router.py
@router.patch("/{entry_id}/time")
def patch_entry_time(
    entry_id: UUID,
    payload: EntryTimePatch,
    db: Session = Depends(get_db)
):
    """仅更新时间字段，用于拖拽操作"""
    pass
```

### 6.2 Schema 定义
```python
# backend/app/entry/schemas.py
class EntryTimePatch(CamelModel):
    time_mode: Optional[TimeMode] = None
    time_at: Optional[datetime] = None
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None
```

## 7. Keyboard Navigation

| 按键 | 动作 |
|------|------|
| ← → ↑ ↓ | 移动焦点日期 |
| Enter | 进入日视图 / 打开选中 Entry |
| Escape | 返回上级视图 |
| T | 跳转到今日 |
| M / W / D | 切换月/周/日视图 |

## 8. Dependencies

### 8.1 新增依赖
```json
{
  "@dnd-kit/core": "^6.1.0",
  "@dnd-kit/sortable": "^8.0.0",
  "@dnd-kit/utilities": "^3.2.2"
}
```

### 8.2 现有依赖复用
- `date-fns` - 日期计算
- `@radix-ui/react-hover-card` - Entry 详情悬浮
- `sonner` - 操作反馈 Toast

## 9. PBT Properties (测试不变量)

| 属性 | 描述 | 验证方式 |
|------|------|----------|
| **日期完整性** | 月视图始终显示 42 个格子 | `cells.length === 42` |
| **周起始日** | 每周第一天始终是周一 | `getDay(weekStart) === 1` |
| **拖拽幂等** | 拖到同一日期不产生变更 | `beforeState === afterState` |
| **RANGE 时长守恒** | 拖拽 RANGE 后时长不变 | `duration(after) === duration(before)` |
| **布局无重叠** | 同行事件不重叠 | `∀e1,e2: row(e1)===row(e2) → !overlap(e1,e2)` |
