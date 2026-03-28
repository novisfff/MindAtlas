# MindAtlas Summary

You can use MindAtlas to summarize the user's experiences, notes, and progress over time.

This skill is about summary policy.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- When the user asks for a recap, review, digest, or summary, use MindAtlas to produce a useful structured result

Always start catalog-first:
- Read the current capability catalog before choosing a summary path
- Prefer exposed report, summary, retrieval, or graph capabilities that clearly match the user's request
- Do not assume a fixed built-in weekly, monthly, or topic-summary tool list

Preferred summary routes:

1. Weekly summary
- Prefer an exposed weekly-report-style capability when the user asks for a weekly review, digest, or recap

2. Monthly summary
- Prefer an exposed monthly-report-style capability when the user asks for a monthly review, digest, or recap

3. Topic summary
- If the catalog exposes a topic-summary, project-summary, workflow, or agent capability that clearly matches the request, use it
- If no dedicated topic-summary capability exists, retrieve the relevant records first and then synthesize the result

Summary structure:
- Prefer summaries that highlight main themes, completed work, progress, blockers, and reusable conclusions
- Do not just concatenate raw records
- If many records exist, synthesize patterns instead of listing everything

Time-bounded summaries:
- Honor explicit periods such as this week, last week, this month, the last two weeks, or a named date range
- If the requested period is vague, use a reasonable interpretation or ask a brief clarifying question when precision matters

Topic-oriented summaries:
- Prefer grouping by major themes, project progress, repeated issues, decisions, and outcomes
- If evidence is thin, do not overstate patterns

Presentation:
- Keep the final answer readable and useful
- Prefer a short overview first, then highlights, then optional deeper detail if needed
- After a summary, it can help to offer one concise next step such as expanding a topic or showing source records
