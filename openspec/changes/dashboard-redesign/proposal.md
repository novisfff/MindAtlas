# Dashboard Redesign

## Context

### User Need
重新设计 Dashboard 页面，移除低信息量的统计数据（记录总数、类型分布），增加更有价值的内容：
1. 日历热力图 - 可视化近期活动分布
2. AI 智能内容 - 周期性生成的活动总结、行动建议、趋势分析

### Current State
- 现有 Dashboard 位于 `frontend/src/features/dashboard/`
- 包含 4 个组件：StatsCard(x3)、RecentEntries、QuickActions、TypeDistribution
- 后端 API: `GET /api/stats/dashboard` 返回 totalEntries, totalTags, totalRelations, entriesByType
- 已有完整日历功能 (`features/calendar/`)，支持月/周/日视图
- 已有 AI 能力：摘要生成、标签推荐、Assistant 对话

### User Decisions (Phase 1 - 已完成)
- **热力图数据源**: 基于事件时间 (timeAt/timeFrom)，非创建时间
- **热力图范围**: 默认展示过去 3 个月
- **热力图筛选**: 支持按 EntryType 筛选
- **热力图实现**: 使用 `react-activity-calendar` 库
- **AI 生成形式**: 周期性自动生成（每周）+ 定时任务
- **AI 内容类型**: 近期活动总结 + 行动建议/提醒 + 趋势分析
- **AI 报告存储**: JSONB 格式持久化存储，可查看历史
- **AI 重试策略**: 自动重试（最多3次，指数退避）
- **定时任务部署**: 集成在 FastAPI 进程，使用数据库锁保证幂等

### User Decisions (Phase 2 - 布局优化)
- **热力图改为小月历**: 横向热力图太占篇幅，改为紧凑的月历形式
- **小月历范围**: 默认显示当前月份，支持切换月份
- **小月历颜色**: 保持热力图的颜色深浅表示活动强度
- **新增关键指标卡片**: 本周记录数、活跃天数、总记录数、总关系数
- **新增类型/标签热度**: 过去30天 Top 5 类型 + Top 5 标签，细条形/占比展示
- **热度点击跳转**: 点击类型/标签跳转到过滤后的 Entries 列表
- **布局**: 三列 + 两列紧凑设计

### 最终布局方案
```
┌─────────────────────────────────────────────────────────────┐
│  问候语                                      QuickActions   │
├─────────────────┬─────────────────┬─────────────────────────┤
│  关键指标卡片   │   最近记录(5条) │      小月历             │
│  - 本周记录数   │                 │   (带热力图颜色)        │
│  - 活跃天数     │                 │   (可切换月份)          │
│  - 总记录数     │                 │                         │
│  - 总关系数     │                 │                         │
├─────────────────┴────────────────┬┴─────────────────────────┤
│         AI 周报                  │    类型/标签热度         │
│                                  │    Top 5 类型 (条形图)   │
│                                  │    Top 5 标签 (条形图)   │
└──────────────────────────────────┴──────────────────────────┘
```

---

## Requirements

### R1: 小月历组件 (替代横向热力图)
**场景**: 用户打开 Dashboard 即可看到当月活动分布

**验收标准**:
- [ ] 显示当前月份的日历格子
- [ ] 颜色深浅表示当天 Entry 数量（基于 timeAt/timeFrom）
- [ ] 支持切换到上/下月份
- [ ] 鼠标悬停显示 Tooltip：日期 + Entry 数量
- [ ] 点击日期跳转到日历页面对应日期
- [ ] 紧凑设计，适合放在右上角区域

### R2: AI 周报生成服务
**场景**: 系统每周自动生成上周活动总结

**验收标准**:
- [ ] 后端新增 `WeeklyReport` 模型，存储周报内容
- [ ] 周报包含：活动总结、行动建议、趋势分析三个部分
- [ ] 提供手动触发生成的 API（用于测试和补生成）
- [ ] 周报基于上周的 Entry 数据（按 timeAt/timeFrom 筛选）
- [ ] 无 Entry 时生成简短的"本周无记录"提示
- [ ] 使用 APScheduler 实现定时任务，每周一凌晨自动生成

### R3: AI 周报展示组件
**场景**: 用户在 Dashboard 查看最新周报

**验收标准**:
- [ ] 展示最新一期周报内容
- [ ] 支持展开/折叠各部分（总结、建议、趋势）
- [ ] 提供"查看历史周报"入口
- [ ] 周报未生成时显示占位提示
- [ ] 支持手动刷新/重新生成

### R4: Dashboard 布局重构 (Phase 2)
**场景**: 优化布局，三列+两列紧凑设计

**验收标准**:
- [ ] 第一行三列：关键指标 | 最近记录(5条) | 小月历
- [ ] 第二行两列：AI 周报 | 类型/标签热度
- [ ] 移除横向热力图组件 (ActivityHeatmap)
- [ ] 保留 QuickActions 在顶部右侧
- [ ] 响应式：移动端改为单列堆叠

### R5: 后端 API 扩展
**场景**: 支持新组件的数据获取

**验收标准**:
- [x] `GET /api/stats/heatmap` - 热力图数据（已完成）
- [x] `GET /api/reports/weekly` - 周报列表（已完成）
- [x] `GET /api/reports/weekly/latest` - 最新周报（已完成）
- [x] `POST /api/reports/weekly/generate` - 手动生成（已完成）
- [ ] `GET /api/stats/weekly-metrics` - 本周指标（新增）
- [ ] `GET /api/stats/hotness` - 类型/标签热度（新增）

### R6: 定时任务服务
**场景**: 自动化周报生成

**验收标准**:
- [ ] 使用 APScheduler 集成到 FastAPI
- [ ] 每周一 00:00 自动触发周报生成
- [ ] 支持通过环境变量配置定时任务开关
- [ ] 任务执行日志记录

### R7: 关键指标卡片 (新增)
**场景**: 用户快速了解核心数据指标

**验收标准**:
- [ ] 显示本周新增记录数
- [ ] 显示本周活跃天数（有记录的天数）
- [ ] 显示总记录数
- [ ] 显示总关系数
- [ ] 紧凑的卡片布局，适合左上角区域

### R8: 类型/标签热度 (新增)
**场景**: 用户了解近期内容分布趋势

**验收标准**:
- [ ] 显示过去30天 Top 5 类型（细条形图/占比）
- [ ] 显示过去30天 Top 5 标签（细条形图/占比）
- [ ] 点击类型跳转到 `/entries?typeId=xxx`
- [ ] 点击标签跳转到 `/entries?tagId=xxx`
- [ ] 紧凑设计，适合右下角区域

---

## Constraints

### 技术约束
- 前端：React 18 + TypeScript + TanStack Query + Tailwind CSS
- 后端：FastAPI + SQLAlchemy + PostgreSQL
- AI 调用：复用现有 `resolve_openai_compat_config` 获取配置
- 热力图：使用 `react-activity-calendar` 库
- 定时任务：APScheduler 集成在 FastAPI 进程
- 幂等保证：使用 PostgreSQL advisory lock + unique 约束

### 数据约束
- Entry 时间字段：`time_mode` (NONE/POINT/RANGE), `time_at`, `time_from`, `time_to`
- `time_mode=NONE` 的 Entry 不计入热力图
- 周报生成需要至少配置一个可用的 AI Provider

### 设计约束
- 保持与现有 UI 风格一致（圆角卡片、阴影、hover 效果）
- 国际化：所有文案需支持中英文
- 响应式：支持桌面和平板尺寸

---

## Dependencies

### 内部依赖
- `useEntriesQuery` - 获取 Entry 列表
- `resolve_openai_compat_config` - 获取 AI 配置
- 现有 Entry/Tag/Relation 模型

### 外部依赖
- `react-activity-calendar` - 热力图组件
- `apscheduler` - 定时任务调度

---

## Risks

### R1: AI 生成质量不稳定
- **问题**: 不同 AI Provider 生成的周报质量可能差异较大
- **缓解**: 设计结构化 Prompt，明确输出格式要求

### R2: 热力图性能
- **问题**: 大量 Entry 时统计查询可能较慢
- **缓解**: 后端按日期聚合查询，避免返回完整 Entry 列表

### R3: 周报生成时机
- **问题**: 自动生成需要定时任务支持
- **缓解**: 使用 APScheduler 实现，支持环境变量控制开关

---

## Open Questions

已全部解决。
