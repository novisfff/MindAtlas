# Dashboard Redesign - 实施任务

## Phase 1: 后端基础设施

### T1.1 WeeklyReport 模型和迁移 ✅
- [x] 创建 `backend/app/report/models.py`
- [x] 创建 `backend/app/report/schemas.py`
- [x] 生成 Alembic 迁移脚本
- [x] 添加索引：`ix_weekly_report_week_start`

### T1.2 热力图 API ✅
- [x] 更新 `backend/app/stats/schemas.py`
- [x] 更新 `backend/app/stats/service.py`
- [x] 实现 `GET /api/stats/heatmap` 端点
- [x] SQL 优化：UNION ALL + 窗口函数

### T1.3 周报 API ✅
- [x] 创建 `backend/app/report/service.py`
- [x] 创建 `backend/app/report/router.py`
- [x] 实现端点：
  - `GET /api/reports/weekly` (分页列表)
  - `GET /api/reports/weekly/latest`
  - `POST /api/reports/weekly/generate`

## Phase 2: AI 周报生成 ✅

### T2.1 周报生成服务 ✅
- [x] 实现 `WeeklyReportService.generate()`
- [x] 集成 `resolve_openai_compat_config`
- [x] 实现 Prompt 模板
- [x] 实现 JSONB 内容解析

### T2.2 重试机制 ✅
- [x] 实现指数退避重试
- [x] 错误分类（可重试/不可重试）
- [x] 记录 `last_error` 和 `attempts`

### T2.3 APScheduler 集成 ✅
- [x] 创建 `backend/app/scheduler.py`
- [x] 实现 advisory lock 幂等保证
- [x] 配置 lifespan 启动/关闭
- [x] 添加环境变量 `SCHEDULER_ENABLED`

## Phase 3: 前端组件 ✅

### T3.1 安装依赖 ✅
- [x] `npm install react-activity-calendar`

### T3.2 热力图组件 ✅
- [x] 创建 `ActivityHeatmap.tsx`
- [x] 创建 `HeatmapTooltip.tsx`
- [x] 实现类型筛选下拉
- [x] 实现点击跳转日历

### T3.3 周报组件 ✅
- [x] 创建 `WeeklyReportCard.tsx`
- [x] 创建 `ReportHistoryDialog.tsx`
- [x] 实现展开/折叠
- [x] 实现重新生成按钮

### T3.4 API 和 Queries ✅
- [x] 创建 `api/heatmap.ts`
- [x] 创建 `api/reports.ts`
- [x] 更新 `queries.ts`

## Phase 4: 布局重构 ✅

### T4.1 DashboardPage 重构 ✅
- [x] 移除 StatsCard 组件引用
- [x] 移除 TypeDistribution 组件引用
- [x] 调整 RecentEntries 为 3 条
- [x] 紧凑化 QuickActions

### T4.2 布局整合 ✅
- [x] 顶部：问候语 + QuickActions
- [x] 中部：热力图 + 类型筛选
- [x] 底部：周报卡片 + 最近记录

### T4.3 响应式适配 ✅
- [x] 桌面：两列布局
- [x] 平板：紧凑两列
- [x] 移动：单列堆叠

## Phase 5: 国际化和测试 ✅

### T5.1 国际化 ✅
- [x] 更新 `locales/zh/common.json`
- [x] 更新 `locales/en/common.json`

### T5.2 测试
- [ ] 热力图 API 测试
- [ ] 周报生成测试
- [ ] 前端组件测试

## Phase 6: 布局优化 (Phase 2)

### T6.1 后端 - weekly-metrics API ✅
- [x] 在 `backend/app/stats/schemas.py` 添加 `WeeklyMetrics` schema
- [x] 在 `backend/app/stats/service.py` 添加 `get_weekly_metrics()` 方法
- [x] 在 `backend/app/stats/router.py` 添加 `GET /api/stats/weekly-metrics` 端点
- [x] SQL 优化：单次查询返回所有 4 个指标

### T6.2 后端 - hotness API ✅
- [x] 在 `backend/app/stats/schemas.py` 添加 `HotnessData` schema
- [x] 在 `backend/app/stats/service.py` 添加 `get_hotness()` 方法
- [x] 在 `backend/app/stats/router.py` 添加 `GET /api/stats/hotness` 端点
- [x] SQL 优化：CTE + json_agg 单次查询

### T6.3 前端 - KeyMetricsCard 组件 ✅
- [x] 创建 `frontend/src/features/dashboard/components/KeyMetricsCard.tsx`
- [x] 在 `queries.ts` 添加 `useWeeklyMetricsQuery`
- [x] 在 `api/` 添加 `weeklyMetrics.ts`
- [x] 2x2 网格布局，显示 4 个指标

### T6.4 前端 - MiniCalendar 组件 ✅
- [x] 创建 `frontend/src/features/dashboard/components/MiniCalendar.tsx`
- [x] 使用 date-fns 生成月历数据
- [x] 复用 `useHeatmapQuery` 获取热力图数据
- [x] 实现月份切换、Tooltip、点击跳转

### T6.5 前端 - TypeTagHotness 组件 ✅
- [x] 创建 `frontend/src/features/dashboard/components/TypeTagHotness.tsx`
- [x] 在 `queries.ts` 添加 `useHotnessQuery`
- [x] 在 `api/` 添加 `hotness.ts`
- [x] 实现 Top 5 条形图、点击跳转

### T6.6 DashboardPage 布局重构 ✅
- [x] 移除 `ActivityHeatmap` 组件引用
- [x] 第一行三列：KeyMetricsCard | RecentEntries(5) | MiniCalendar
- [x] 第二行两列：WeeklyReportCard | TypeTagHotness
- [x] 响应式适配

### T6.7 国际化更新 ✅
- [x] 更新 `locales/zh/common.json` 添加新文案
- [x] 更新 `locales/en/common.json` 添加新文案

---

## 依赖关系

### Phase 1-5 (已完成)
```
T1.1 ──┬── T1.3 ── T2.1 ── T2.2 ── T2.3
       │
T1.2 ──┴── T3.2

T3.1 ── T3.2 ──┬── T4.1 ── T4.2 ── T4.3 ── T5.1 ── T5.2
               │
T2.1 ── T3.3 ──┘
```

### Phase 6 (布局优化)
```
T6.1 ────────────────┬── T6.3 ──┐
                     │          │
T6.2 ────────────────┴── T6.5 ──┼── T6.6 ── T6.7
                                │
T1.2 (heatmap API) ──── T6.4 ──┘
```

**关键路径**: T6.1 → T6.3 → T6.6 (后端 API 先行)
