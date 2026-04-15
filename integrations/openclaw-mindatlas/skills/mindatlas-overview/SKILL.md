---
name: mindatlas-overview
description: Primary routing guide for any MindAtlas-related task, including durable memory capture, previous-record lookup, time-bounded recap, relation reasoning, and dynamically exposed custom MindAtlas capabilities.
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
- running a workflow, agent, or custom capability that the administrator intentionally exposed from MindAtlas

MindAtlas is not meant to be:
- a generic cloud drive
- a replacement for every short-lived working memory need
- an excuse to store every low-value chat fragment forever
- a place to dump unstructured noise when the information has no durable value

Tool surface rule:
- Start from the current session's visible `mindatlas_*` tools
- Prefer dedicated visible tools such as `mindatlas_submit_context_capture`, `mindatlas_search_entries`, or `mindatlas_generate_periodic_review` when one clearly matches the request
- Do not assume MindAtlas only exposes a fixed built-in tool list; administrators may expose additional tool, workflow, or agent capabilities

Dynamic capability rule:
- If no visible dedicated `mindatlas_*` tool clearly matches the request, check whether `mindatlas_list_capabilities` is visible
- When `mindatlas_list_capabilities` is visible, use it to inspect the latest MindAtlas capability catalog
- If the catalog reveals a matching custom capability, use `mindatlas_run_capability` with that capability's `capabilityKey` and schema-aligned input
- Only say MindAtlas is not exposed in the current session when neither dedicated MindAtlas tools nor the dispatcher tools are visible

Surface recovery rule:
- Do not assume the current session already reflects the newest MindAtlas plugin, shipped skill, or capability metadata
- If the administrator says they just upgraded the plugin or shipped skills, or if the current session still behaves like an older MindAtlas surface, tell them to run `npm --prefix ./integrations/openclaw-mindatlas run update:openclaw` on the OpenClaw host, then open a brand-new session
- If this is the first install or the plugin is not configured yet, point them to `npm --prefix ./integrations/openclaw-mindatlas run setup:openclaw` instead
- If catalog changes were saved but the current session still cannot see the expected `mindatlas_*` surface, recommend a Gateway restart plus a new session before concluding the capability is unavailable

Typical requests include:
- "记一下 / remember this"
- "保存一下 / store this"
- "我之前记过吗 / did I record this before"
- "帮我搜一下 / search for this"
- "我最近一周干了啥 / what did I do this week"
- "我上周做了什么 / what did I do last week"
- "总结一下我最近做了什么 / summarize what I've been doing"
- "做个时间范围回顾 / generate a time-bounded review"
- "帮我关联一下这两条记录 / connect these two records"
- "这些记录之间有什么关系 / what patterns or relations exist"
- "按我在 MindAtlas 里配置好的流程处理一下 / run the workflow or agent I configured in MindAtlas"
- "跑我新加的项目复盘 workflow / run my new project review workflow"
- "调用我刚暴露出来的团队 agent / run the newly exposed team agent"

Primary routes:
1. Remember / save / record / store / archive
- Prefer `mindatlas_submit_context_capture` or another visible capture workflow / agent
- Hand off one thin, high-value `context` block instead of over-assembling every final entry field
- Let OpenClaw request metadata provide source, channel, session, and tool context whenever the visible capability only asks for `context`

2. Find previous records or ask "did I record this"
- Prefer `mindatlas_search_entries`
- Use `mindatlas_get_entry` when you already have an ID or need exact details from one result

3. Recap, review, digest, time-bounded summary, or recent activity summary
- Hand off to the summary skill
- Prefer `mindatlas_generate_periodic_review` or another visible review / digest workflow or agent when present

4. Connect records or ask cross-record questions
- Prefer `mindatlas_create_relation` for explicit linking
- Prefer `mindatlas_query_knowledge_graph` for patterns, why things are related, or synthesized knowledge questions

5. Run a MindAtlas-published workflow or agent
- Prefer the matching visible `mindatlas_*` workflow or agent tool
- If the needed custom capability is not already visible as a dedicated tool, use the dispatcher skill and `mindatlas_list_capabilities`

When using MindAtlas:
- Keep tool arguments concise and structured
- Let the overview skill choose the route first, then let the narrower summary, retrieval, auto-capture, or dispatcher skill handle local strategy
- Reuse returned IDs when you need follow-up detail lookup or relation creation
- Treat MindAtlas as the source of truth for stored knowledge
- Automatic capture is a prompt-driven best-effort behavior, not a guaranteed system hook

Boundaries:
- Do not force every conversation into MindAtlas
- Prefer MindAtlas for durable memory, retrieval, reporting, relation work, graph reasoning, structured custom workflows, and administrator-exposed custom capabilities
- Use other tools or direct answers when the task does not benefit from long-term storage or structured recall
