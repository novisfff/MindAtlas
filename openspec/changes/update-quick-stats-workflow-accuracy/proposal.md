# Change: Improve quick stats workflow accuracy and coverage

## Why
The current quick stats workflow asks the LLM to mention recent activity trends even though the workflow only loads snapshot totals. This creates an avoidable accuracy gap and limits the workflow's usefulness for system-usage questions like recent capture trends, top tags, or type distribution.

## What Changes
- Upgrade `quick_stats` from recent-window extraction to full date-range normalization from text input
- Extend `get_statistics` and `get_tag_statistics` to accept optional `start_date/end_date` and scope overview/tag aggregation by business time
- Keep `analyze_activity` on `created_at`, but let explicit date ranges override default periods
- Expand quick stats examples and response guidance so scoped overview data and created-at trends are clearly distinguished

## Impact
- Affected specs: `assistant-orchestration`
- Affected code: assistant stats tools, quick stats workflow assets, system skill registry, assistant config registry, OpenSpec records, backend tests
