---
name: mindatlas-summary
description: Report and recap policy for time-bounded, recent-activity, topic-oriented, and custom MindAtlas review capabilities after the overview skill has routed the request.
---

# MindAtlas Summary

You can use MindAtlas to summarize the user's experiences, notes, and progress over time.

This skill is only for summary, recap, report routing, and output organization after the overview skill has already decided the request belongs to MindAtlas.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- When the user asks for a recap, review, digest, or summary, use MindAtlas to produce a useful structured result

Priority order:
1. Prefer a dedicated visible summary or review tool such as `mindatlas_generate_periodic_review`
2. If no visible dedicated tool clearly matches, let the overview/dispatcher path inspect custom capabilities
3. If no dedicated or custom summary capability exists but `mindatlas_search_entries` is visible, retrieve evidence first and then synthesize the summary

Session-visible tool rule:
- Start from the current session's visible `mindatlas_*` tools
- Prefer visible report, workflow, agent, or retrieval tools that clearly match the requested recap
- Do not assume the only valid summary path is the system periodic review tool

Preferred summary routes:
1. Time-bounded summary
- For requests like "我最近一周干了啥", "上周我做了什么", "看本月标签分布", "统计 2026-03-01 到 2026-03-31", or "what did I do this week", prefer `mindatlas_generate_periodic_review` or another visible review / digest workflow or agent
- Treat recap, review, digest, and date-range summary requests as review-first when a dedicated periodic review path is available

2. Topic or custom-process summary
- If a visible MindAtlas tool or capability clearly represents the requested project review, sprint digest, team workflow, or custom summary path, use it
- If the needed custom capability is not already visible as a dedicated tool, route back through the dispatcher flow and inspect `mindatlas_list_capabilities`
- If the current session still looks stale after plugin, shipped-skill, or catalog changes, route back through overview/dispatcher and recommend `npm --prefix ./integrations/openclaw-mindatlas run update:openclaw`, then opening a new session

Mandatory fallback:
- If no dedicated periodic-review or topic-summary tool is visible but `mindatlas_search_entries` is visible, retrieve the relevant time-bounded records first and then synthesize the answer
- Use `mindatlas_get_entry` to expand key records when a search result needs more detail
- Do not abandon MindAtlas just because a dedicated report tool is absent

Yield to retrieval:
- If the user mainly wants to find one record, verify whether something was recorded, or inspect a specific entry, let the retrieval skill lead
- If the user mainly wants a recap or review grounded in multiple records, summary stays in front and can still use retrieval as a sub-step

Presentation:
- Prefer a short overview first, then highlights, then optional deeper detail if needed
- Summaries should highlight main themes, completed work, progress, blockers, and reusable conclusions
- Do not just concatenate raw records
