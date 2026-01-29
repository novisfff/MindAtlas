# Calendar View - Implementation Tasks

## Phase 1: Backend API (PATCH Endpoint)

- [x] Task 1.1: Add EntryTimePatch Schema
- [x] Task 1.2: Add PATCH Service Method
- [x] Task 1.3: Add PATCH Router Endpoint

---

## Phase 2: Frontend Infrastructure

- [x] Task 2.1: Install @dnd-kit
- [x] Task 2.2: Create Calendar Feature Directory
- [x] Task 2.3: Add Calendar Route
- [x] Task 2.4: Update Sidebar Navigation

---

## Phase 3: Calendar Core Components

- [x] Task 3.1: CalendarPage
- [x] Task 3.2: CalendarHeader
- [x] Task 3.3: Date Utilities

---

## Phase 4: Month View

- [x] Task 4.1: MonthView Component
- [x] Task 4.2: CalendarCell Component
- [x] Task 4.3: CalendarEvent Component
- [x] Task 4.4: Layout Algorithm
- [x] Task 4.5: MultiDayEvent Component
- [x] Task 4.6: MoreEventsPopover

---

## Phase 5: Week & Day Views

- [x] Task 5.1: WeekView Component
- [x] Task 5.2: DayView Component

---

## Phase 6: Drag & Drop

- [x] Task 6.1: DnD Hook
- [x] Task 6.2: PATCH API Integration
- [ ] Task 6.3: Optimistic Update

---

## Phase 7: Keyboard Navigation

- [x] Task 7.1: Keyboard Hook
- [x] Task 7.2: Focus Management

---

## Phase 8: Quick Create & Polish

- [x] Task 8.1: QuickCreateDialog
- [x] Task 8.2: i18n Support
- [x] Task 8.3: Remove Timeline Feature

---

## Phase 9: Cleanup

- [x] Task 9.1: Update Exports
- [x] Task 9.2: Remove Timeline Imports

---

## Phase 10: Review Fixes (Critical)

- [x] Task 10.1: Fix getMonthDays to return fixed 42 cells
  - File: `utils/dateUtils.ts`
  - Use `addDays(gridStart, i)` for i in 0..41

- [x] Task 10.2: Implement assignRows layout algorithm
  - File: `utils/layoutUtils.ts`
  - Greedy row-packing for multi-day entries

- [x] Task 10.3: Integrate useCalendarEntriesQuery in CalendarPage
  - File: `CalendarPage.tsx`
  - Fetch entries and pass to views

- [x] Task 10.4: Add entries prop to CalendarCell
  - File: `components/CalendarCell.tsx`
  - Accept entries, render CalendarEvent list

- [x] Task 10.5: Wire DndContext in CalendarPage
  - File: `CalendarPage.tsx`
  - Wrap views with DndContext, add DragOverlay

- [x] Task 10.6: Call useCalendarKeyboard hook
  - File: `CalendarPage.tsx`
  - Activate keyboard navigation

---

## Phase 11: Review Fixes (Warning)

- [x] Task 11.1: Fix URL date serialization (timezone)
  - File: `CalendarPage.tsx`
  - Use `format(date, 'yyyy-MM-dd')` instead of toISOString

- [x] Task 11.2: Fix URL date parsing (timezone)
  - File: `CalendarPage.tsx`
  - Use `parse(dateParam, 'yyyy-MM-dd', new Date())`

- [x] Task 11.3: Fix query time range boundaries
  - File: `queries.ts`
  - Convert to UTC datetime with T00:00:00Z/T23:59:59Z

- [x] Task 11.4: Add optimistic update to mutation
  - File: `queries.ts`
  - Implement onMutate/onError/onSettled

- [x] Task 11.5: Complete keyboard navigation
  - File: `hooks/useCalendarKeyboard.ts`
  - Add Enter/Escape, use viewMode

- [x] Task 11.6: Remove unused imports in MonthView
  - File: `components/MonthView.tsx`
  - Clean up format, locale if unused

- [x] Task 11.7: Add EntryTimePatch validation
  - File: `backend/app/entry/schemas.py`
  - Add model_validator for consistency

- [x] Task 11.8: Add EntryIndexOutbox in patch_time
  - File: `backend/app/entry/service.py`
  - Enqueue outbox entry after time update

- [x] Task 11.9: Make query size configurable
  - File: `queries.ts`
  - Extract DEFAULT_CALENDAR_PAGE_SIZE constant

---

## Task Dependencies

```
Phase 1-9 (Completed) ─────┐
                           v
Phase 10 (Critical Fixes) ─┤
  10.1 dateUtils           │
  10.2 layoutUtils         │
  10.3 CalendarPage query  │
  10.4 CalendarCell        │
  10.5 DndContext          │
  10.6 Keyboard hook       │
                           v
Phase 11 (Warning Fixes) ──┘
  11.1-11.2 URL timezone
  11.3-11.4 Query/Mutation
  11.5-11.6 Keyboard/Cleanup
  11.7-11.8 Backend validation
  11.9 Query size
```

## Execution Order

1. **Phase 10.1** → Fix getMonthDays (42 cells)
2. **Phase 10.2** → Implement assignRows
3. **Phase 11.1-11.2** → Fix URL timezone (prerequisite for query)
4. **Phase 11.3** → Fix query boundaries
5. **Phase 10.3** → Integrate query in CalendarPage
6. **Phase 10.4** → CalendarCell entries prop
7. **Phase 10.5** → DndContext integration
8. **Phase 11.4** → Optimistic update
9. **Phase 11.5** → Complete keyboard
10. **Phase 10.6** → Wire keyboard hook
11. **Phase 11.6** → Cleanup unused imports
12. **Phase 11.7-11.8** → Backend fixes
13. **Phase 11.9** → Query size constant
