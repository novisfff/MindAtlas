---
name: mindatlas-overview
description: Primary routing guide for any MindAtlas-related task, including remember/save/store requests, previous-record lookup, recent activity recap, weekly or monthly review, relation or graph questions, and running MindAtlas-published workflows or agents.
---

# MindAtlas Overview

Treat this as the main router skill for MindAtlas.

MindAtlas is the user's long-term knowledge and experience system.

Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Use MindAtlas first when the task is fundamentally about:
- durable memory
- something the user wants to remember, save, record, store, or archive
- previously stored records or history
- what happened over time, especially this week, last week, this month, or recently
- summaries, reviews, recaps, digests, or reports grounded in stored records
- relations between records or cross-record knowledge questions
- running a workflow or agent that the administrator intentionally exposed from MindAtlas

MindAtlas is not meant to be:
- A generic cloud drive
- A replacement for every short-lived working memory need
- An excuse to store every low-value chat fragment forever
- A place to dump unstructured noise when the information has no durable value

Session-visible tool rule:
- Start from the current session's visible `mindatlas_*` tools
- Prefer those visible tools over generic memory or guesswork when the task is really about MindAtlas
- Do not wait for a separate catalog tool; the runtime surface you can actually use is the visible `mindatlas_*` tool set

If the current session does not expose any `mindatlas_*` tools:
- Say explicitly that MindAtlas capabilities are not exposed in this session
- Suggest starting a new OpenClaw session or reloading the Gateway / plugin
- Do not silently answer as if local memory or generic recall were an authoritative MindAtlas result

Typical requests include:
- "记一下 / remember this"
- "保存一下 / store this"
- "我之前记过吗 / did I record this before"
- "帮我搜一下 / search for this"
- "我最近一周干了啥 / what did I do this week"
- "我上周做了什么 / what did I do last week"
- "总结一下我最近做了什么 / summarize what I've been doing"
- "生成周报 / generate a weekly report"
- "生成月报 / generate a monthly report"
- "帮我关联一下这两条记录 / connect these two records"
- "这些记录之间有什么关系 / what patterns or relations exist"
- "按我在 MindAtlas 里配置好的流程处理一下 / run the workflow or agent I configured in MindAtlas"

Primary routes:
1. Remember / save / record / store / archive
- Prefer `mindatlas_submit_context_capture` or another visible capture workflow / agent
- Hand off one thin, high-value `context` block instead of over-assembling every final entry field
- Let OpenClaw request metadata provide source, channel, session, and tool context whenever the visible capability only asks for `context`

2. Find previous records or ask "did I record this"
- Prefer `mindatlas_search_entries`
- Use `mindatlas_get_entry` when you already have an ID or need exact details from one result

3. Recap, review, digest, weekly report, monthly report, or recent activity summary
- Hand off to the summary skill
- Prefer `mindatlas_generate_weekly_report`, `mindatlas_generate_monthly_report`, or another visible review / digest workflow / agent when present

4. Connect records or ask cross-record questions
- Prefer `mindatlas_create_relation` for explicit linking
- Prefer `mindatlas_query_knowledge_graph` for patterns, why things are related, or synthesized knowledge questions

5. Run a MindAtlas-published workflow or agent
- Prefer the matching visible `mindatlas_*` workflow or agent tool
- Trust the currently visible tool surface rather than assuming a hidden fixed list

When using MindAtlas:
- Keep tool arguments concise and structured
- Let the overview skill choose the route first, then let the narrower summary, retrieval, or auto-capture skill handle the local strategy
- Do not assume you should assemble every final entry field yourself unless the visible capability explicitly requires that
- Reuse returned IDs when you need follow-up detail lookup or relation creation
- Treat MindAtlas as the source of truth for stored knowledge
- Do not assume a fixed built-in tool list; administrators may expose custom workflows, tools, or agents through the visible MindAtlas tool surface
- Automatic capture is a prompt-driven best-effort behavior, not a guaranteed system hook

Boundaries:
- Do not force every conversation into MindAtlas
- Prefer MindAtlas for durable memory, retrieval, reporting, relation work, graph reasoning, and structured knowledge work
- Use other tools or direct answers when the task does not benefit from long-term storage or structured recall
