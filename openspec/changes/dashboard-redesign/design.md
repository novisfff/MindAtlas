# Dashboard Redesign - 设计文档

## 1. 布局设计

### Phase 2 最终布局（三列+两列紧凑设计）

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

### 响应式断点

- **桌面 (≥1024px)**: 第一行三列，第二行两列
- **平板 (768-1023px)**: 第一行两列（指标+月历），第二行两列
- **移动 (＜768px)**: 单列堆叠

---

## 2. Phase 2 新增组件设计

### 2.1 关键指标卡片 (KeyMetricsCard)

```typescript
interface WeeklyMetrics {
  weekEntryCount: number    // 本周记录数
  activeDays: number        // 本周活跃天数 (有记录的天数)
  totalEntries: number      // 总记录数
  totalRelations: number    // 总关系数
  weekStart: string         // 周起始日期 (UTC 周一)
  weekEnd: string           // 周结束日期 (UTC 周日)
}
```

**布局**: 2x2 网格，每个指标显示标签+数值

### 2.2 小月历 (MiniCalendar)

**实现方式**: 自定义实现 (date-fns + CSS Grid)

```typescript
interface MiniCalendarProps {
  // 复用现有 heatmap API 数据
}

// 内部状态
const [currentMonth, setCurrentMonth] = useState(new Date())
```

**颜色映射**: 复用热力图颜色等级
- 0 条: `bg-muted/30`
- 1-2 条: `bg-emerald-200`
- 3-5 条: `bg-emerald-400`
- 6+ 条: `bg-emerald-600`

**交互**:
- 点击日期 → 跳转 `/calendar?date=YYYY-MM-DD`
- 悬停显示 Tooltip (日期 + 记录数)
- 左右箭头切换月份

### 2.3 类型/标签热度 (TypeTagHotness)

```typescript
interface HotnessData {
  topTypes: Array<{
    typeId: string
    typeName: string
    typeColor: string
    count: number
  }>
  topTags: Array<{
    tagId: string
    tagName: string
    tagColor: string
    count: number
  }>
  windowStart: string  // 30天窗口起始
  windowEnd: string    // 30天窗口结束
}
```

**布局**: 两个区块垂直排列
- Top 5 类型 (细条形图)
- Top 5 标签 (细条形图)

**交互**:
- 点击类型 → 跳转 `/entries?typeId=xxx`
- 点击标签 → 跳转 `/entries?tagId=xxx`

---

## 3. 热力图组件设计 (Phase 1 - 已完成，Phase 2 移除)

### 数据结构

```typescript
interface HeatmapData {
  date: string        // "2024-01-15"
  count: number       // Entry 数量
  entries: {          // 最多5条，用于 Tooltip
    id: string
    title: string
  }[]
}
```

### API 响应

```json
GET /api/stats/heatmap?typeId=xxx&months=3

{
  "code": 0,
  "data": {
    "startDate": "2024-10-30",
    "endDate": "2025-01-30",
    "data": [
      { "date": "2024-11-01", "count": 3, "entries": [...] },
      { "date": "2024-11-05", "count": 1, "entries": [...] }
    ]
  }
}
```

### 颜色等级

| 数量 | 颜色 | Tailwind Class |
|------|------|----------------|
| 0 | 浅灰 | `bg-muted/30` |
| 1-2 | 浅绿 | `bg-emerald-200` |
| 3-5 | 中绿 | `bg-emerald-400` |
| 6+ | 深绿 | `bg-emerald-600` |

---

## 3. 周报数据模型

### WeeklyReport 模型

```python
class WeeklyReport(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "weekly_report"

    week_start = Column(Date, nullable=False, unique=True)  # 周一日期（唯一约束）
    week_end = Column(Date, nullable=False)    # 周日日期
    entry_count = Column(Integer, default=0)   # 本周 Entry 数量

    # AI 生成的内容（JSONB 存储，便于扩展）
    content = Column(JSONB, nullable=True)
    # content 结构: {"summary": "...", "suggestions": [...], "trends": "..."}

    # 状态和重试
    status = Column(String(32), default="pending")  # pending/generating/completed/failed
    attempts = Column(Integer, default=0)           # 重试次数
    last_error = Column(Text, nullable=True)        # 最后一次错误信息
    generated_at = Column(DateTime(timezone=True), nullable=True)  # 生成完成时间

    __table_args__ = (
        CheckConstraint("week_end = week_start + interval '6 days'", name="ck_week_range"),
    )
```

---

## 4. 周报生成 Prompt 设计

### 输入数据准备

```python
def prepare_report_context(entries: list[Entry]) -> str:
    """将 Entry 列表转换为 Prompt 上下文"""
    if not entries:
        return "本周无记录"

    context_parts = []
    for entry in entries:
        time_str = format_entry_time(entry)
        tags_str = ", ".join(t.name for t in entry.tags) or "无标签"
        context_parts.append(
            f"- [{entry.type.name}] {entry.title} ({time_str})\n"
            f"  标签: {tags_str}\n"
            f"  摘要: {entry.summary or '无摘要'}"
        )
    return "\n".join(context_parts)
```

### Prompt 模板

```python
WEEKLY_REPORT_PROMPT = """你是 MindAtlas 的智能助手，负责生成用户的周报。

【时间范围】
{week_start} 至 {week_end}

【本周记录】
{entries_context}

【任务】
请基于以上记录，生成一份简洁的周报，包含以下三个部分：

1. **活动总结** (summary)
   - 概括本周的主要活动和成果
   - 按类型或主题归纳
   - 100-200字

2. **行动建议** (suggestions)
   - 基于记录内容给出 2-3 条具体建议
   - 可以是待办提醒、知识巩固建议、或下一步行动
   - 每条建议简短明确

3. **趋势分析** (trends)
   - 与往期相比的变化（如有历史数据）
   - 关注领域的变化
   - 50-100字

【输出格式】
请以 JSON 格式输出：
```json
{{
  "summary": "活动总结内容...",
  "suggestions": ["建议1", "建议2", "建议3"],
  "trends": "趋势分析内容..."
}}
```

【注意事项】
- 使用与记录相同的语言（中文记录用中文回复）
- 保持客观，不要过度解读
- 如果记录较少，总结可以更简短
"""
```

### 无记录时的 Prompt

```python
EMPTY_WEEK_PROMPT = """本周（{week_start} 至 {week_end}）没有记录。

请生成一份简短的周报：
- summary: 简短说明本周无记录
- suggestions: 给出 1-2 条鼓励性建议
- trends: 可以留空或简短说明

以 JSON 格式输出。
"""
```

---

## 5. 定时任务设计

### APScheduler 集成

```python
# backend/app/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

def setup_scheduler(app: FastAPI):
    if settings.SCHEDULER_ENABLED:
        scheduler.add_job(
            generate_weekly_report_job,
            CronTrigger(day_of_week='mon', hour=0, minute=0, timezone='UTC'),
            id='weekly_report',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600  # 1小时
        )
        scheduler.start()
```

### 幂等保证

```python
def generate_weekly_report_job():
    """周报生成任务（幂等）"""
    week_start = get_last_monday()

    # 使用 PostgreSQL advisory lock 防止并发
    with db.begin():
        lock_key = int(week_start.strftime('%Y%m%d'))
        db.execute(text(f"SELECT pg_advisory_xact_lock({lock_key})"))

        # 检查是否已存在
        existing = db.query(WeeklyReport).filter_by(week_start=week_start).first()
        if existing and existing.status == 'completed':
            return  # 已生成，跳过

        # 创建或更新记录
        if not existing:
            report = WeeklyReport(week_start=week_start, ...)
            db.add(report)
        else:
            report = existing

        report.status = 'generating'
        db.commit()

    # 调用 AI 生成（在锁外执行）
    try:
        content = generate_report_content(week_start)
        report.content = content
        report.status = 'completed'
        report.generated_at = datetime.utcnow()
    except Exception as e:
        report.status = 'failed'
        report.last_error = str(e)
        report.attempts += 1

    db.commit()
```

### 重试策略

```python
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAYS = [60, 300, 900]  # 1分钟, 5分钟, 15分钟

def should_retry(report: WeeklyReport) -> bool:
    return (
        report.status == 'failed' and
        report.attempts < MAX_RETRY_ATTEMPTS
    )
```

### 环境变量

```bash
# .env
SCHEDULER_ENABLED=true  # 是否启用定时任务
```

---

## 6. 前端组件结构

```
features/dashboard/
├── DashboardPage.tsx          # 主页面（重构）
├── api/
│   ├── stats.ts               # 现有
│   ├── heatmap.ts             # 新增：热力图 API
│   └── reports.ts             # 新增：周报 API
├── components/
│   ├── RecentEntries.tsx      # 保留（缩减为3条）
│   ├── QuickActions.tsx       # 保留（改为紧凑图标）
│   ├── ActivityHeatmap.tsx    # 新增：热力图组件
│   ├── HeatmapTooltip.tsx     # 新增：热力图 Tooltip
│   ├── WeeklyReportCard.tsx   # 新增：周报卡片
│   └── ReportHistoryDialog.tsx # 新增：历史周报弹窗
└── queries.ts                 # 更新：新增 hooks
```

---

## 7. 国际化文案

### 中文 (zh)

```json
{
  "dashboard": {
    "heatmap": {
      "title": "活动热力图",
      "noActivity": "无记录",
      "entries": "条记录",
      "filterByType": "按类型筛选",
      "allTypes": "全部类型"
    },
    "weeklyReport": {
      "title": "本周回顾",
      "summary": "活动总结",
      "suggestions": "行动建议",
      "trends": "趋势分析",
      "viewHistory": "查看历史",
      "regenerate": "重新生成",
      "noReport": "暂无周报",
      "generating": "正在生成..."
    }
  }
}
```

### 英文 (en)

```json
{
  "dashboard": {
    "heatmap": {
      "title": "Activity Heatmap",
      "noActivity": "No entries",
      "entries": "entries",
      "filterByType": "Filter by type",
      "allTypes": "All types"
    },
    "weeklyReport": {
      "title": "Weekly Review",
      "summary": "Summary",
      "suggestions": "Suggestions",
      "trends": "Trends",
      "viewHistory": "View History",
      "regenerate": "Regenerate",
      "noReport": "No report yet",
      "generating": "Generating..."
    }
  }
}
```

---

## 8. Phase 2 新增 API 设计

### 8.1 GET /api/stats/weekly-metrics

**请求**: 无参数

**响应**:
```json
{
  "code": 0,
  "data": {
    "weekEntryCount": 12,
    "activeDays": 5,
    "totalEntries": 156,
    "totalRelations": 42,
    "weekStart": "2026-01-27",
    "weekEnd": "2026-02-02"
  }
}
```

**约束**:
- "本周" = UTC 周一 00:00 至下周一 00:00
- POINT 条目按 `time_at` 计入
- RANGE 条目按 `time_from` 计入（起始日期归属）
- `activeDays` = 本周内有记录的不同日期数

### 8.2 GET /api/stats/hotness

**请求**: 无参数（固定 30 天窗口）

**响应**:
```json
{
  "code": 0,
  "data": {
    "topTypes": [
      {"typeId": "uuid", "typeName": "Knowledge", "typeColor": "#3B82F6", "count": 25}
    ],
    "topTags": [
      {"tagId": "uuid", "tagName": "Python", "tagColor": "#10B981", "count": 18}
    ],
    "windowStart": "2025-12-31",
    "windowEnd": "2026-01-30"
  }
}
```

**约束**:
- 时间窗口 = `[now_utc - 30 days, now_utc)`
- 排除 `time_mode = NONE` 的条目
- 最多返回 Top 5

---

## 9. 实施顺序

1. **后端 - 热力图 API** (R5 部分)
2. **后端 - 周报模型和 API** (R2, R5)
3. **后端 - 定时任务** (R6)
4. **前端 - 热力图组件** (R1)
5. **前端 - 周报组件** (R3)
6. **前端 - 布局重构** (R4)
7. **国际化和测试**
