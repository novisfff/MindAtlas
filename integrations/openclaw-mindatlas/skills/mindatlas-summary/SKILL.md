---
name: mindatlas-summary
description: Report and recap policy for weekly, monthly, recent-activity, and topic-oriented MindAtlas reviews after the current session confirms visible `mindatlas_*` summary or retrieval tools.
---

# MindAtlas Summary

You can use MindAtlas to summarize the user's experiences, notes, and progress over time.

This skill is only for summary, recap, report routing, and output organization after the overview skill has already decided the request belongs to MindAtlas.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- When the user asks for a recap, review, digest, or summary, use MindAtlas to produce a useful structured result

Session-visible tool rule:
- Start from the current session's visible `mindatlas_*` tools
- Prefer visible report, workflow, agent, or retrieval tools that clearly match the requested recap
- Do not assume a separate catalog tool exists inside the session

If the current session does not expose any `mindatlas_*` tools:
- Say explicitly that MindAtlas capabilities are not exposed in this session
- Suggest starting a new OpenClaw session or reloading the Gateway / plugin
- Do not silently substitute generic local memory for MindAtlas

Preferred summary routes:

1. Weekly summary
- For requests like "我最近一周干了啥", "上周我做了什么", "最近都忙了什么", or "what did I do this week", prefer `mindatlas_generate_weekly_report` or another visible weekly review / digest workflow or agent
- Treat recent-activity recap as report-first when a weekly review path is available

2. Monthly summary
- Prefer `mindatlas_generate_monthly_report` or another visible monthly review / digest workflow or agent when the user asks for a monthly review, digest, or recap

3. Topic summary
- If the visible MindAtlas tool surface exposes a topic-summary, project-summary, workflow, or agent capability that clearly matches the request, use it
- If no dedicated topic-summary capability exists, retrieve the relevant records first and then synthesize the result

Mandatory fallback:
- If no dedicated weekly, monthly, or topic report tool is visible but `mindatlas_search_entries` is visible, retrieve the relevant time-bounded records first and then synthesize the answer
- Use `mindatlas_get_entry` to expand key records when a search result needs more detail
- Do not abandon MindAtlas just because a dedicated report tool is absent

Yield to retrieval:
- If the user mainly wants to find one record, verify whether something was recorded, or inspect a specific entry, let the retrieval skill lead
- If the user mainly wants a recap or review grounded in multiple records, summary stays in front and can still use retrieval as a sub-step

Summary structure:
- Prefer summaries that highlight main themes, completed work, progress, blockers, and reusable conclusions
- Do not just concatenate raw records
- If many records exist, synthesize patterns instead of listing everything

Time-bounded summaries:
- Honor explicit periods such as this week, last week, this month, the last two weeks, or a named date range
- If the requested period is vague, use a reasonable interpretation or ask a brief clarifying question when precision matters
- For recap requests, prefer report-style outputs over raw search result dumps

Topic-oriented summaries:
- Prefer grouping by major themes, project progress, repeated issues, decisions, and outcomes
- If evidence is thin, do not overstate patterns

Presentation:
- Keep the final answer readable and useful
- Prefer a short overview first, then highlights, then optional deeper detail if needed
- After a summary, it can help to offer one concise next step such as expanding a topic or showing source records
