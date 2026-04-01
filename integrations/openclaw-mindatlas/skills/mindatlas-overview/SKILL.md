---
name: mindatlas-overview
description: High-level guidance for when OpenClaw should use MindAtlas for durable memory, retrieval, reporting, and structured knowledge work.
---

# MindAtlas Overview

You can use MindAtlas as the user's long-term knowledge and experience system.

Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

MindAtlas is best for:
- Recording things the user wants to keep
- Storing experiences, notes, project progress, decisions, and reusable conclusions
- Searching previously stored entries
- Reading entry details later
- Building relations between records
- Querying the knowledge graph when that capability is available
- Generating weekly or monthly reports from stored records
- Running administrator-exposed workflows or agent capabilities that are intentionally published through the MindAtlas OpenClaw catalog

MindAtlas is not meant to be:
- A generic cloud drive
- A replacement for every short-lived working memory need
- An excuse to store every low-value chat fragment forever
- A place to dump unstructured noise when the information has no durable value

Prefer MindAtlas when the user is trying to:
- Remember something for later
- Find something they recorded before
- Review what happened over time
- Summarize a period, project, or topic
- Connect related ideas, events, or records
- Query across stored knowledge instead of relying only on the current conversation

Typical requests include:
- "记一下 / remember this"
- "我之前记过吗 / did I record this before"
- "帮我搜一下 / search for this"
- "总结一下我最近做了什么 / summarize what I've been doing"
- "生成周报 / generate a weekly report"
- "生成月报 / generate a monthly report"
- "帮我关联一下这两条记录 / connect these two records"
- "按我在 MindAtlas 里配置好的流程处理一下 / run the workflow or agent I configured in MindAtlas"

Before calling a MindAtlas capability:
- Read the capability catalog provided by MindAtlas
- Only use the catalog items that are currently exposed
- Respect `available` and `availabilityReason`
- Prefer the catalog item's title, description, input summary, and output summary over guesswork
- If a required type, relation name, or workflow parameter is unclear, ask a short clarifying question instead of guessing wildly

When using MindAtlas:
- Keep tool arguments concise and structured
- For recording, prefer a high-level capture capability or system item backed by a capture workflow or tool that accepts thin context and lets MindAtlas materialize the final record
- Do not assume you should assemble every final entry field yourself unless the exposed capability explicitly requires that
- Reuse returned IDs when you need follow-up detail lookup or relation creation
- Treat MindAtlas as the source of truth for stored knowledge
- Do not assume a fixed built-in tool list; administrators may expose custom workflows, tools, or agents through the catalog
- Automatic capture is a prompt-driven best-effort behavior, not a guaranteed system hook

Boundaries:
- Do not force every conversation into MindAtlas
- Prefer MindAtlas for durable memory, retrieval, reporting, and structured knowledge work
- Use other tools or direct answers when the task does not benefit from long-term storage or structured recall
