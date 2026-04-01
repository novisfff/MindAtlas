---
name: mindatlas-retrieval
description: Search, detail, graph, and cross-record retrieval policy for previous MindAtlas records, exact entry lookup, time-bounded searches, and relation-aware recall.
---

# MindAtlas Retrieval

You can use MindAtlas as the user's long-term knowledge and experience store.

This skill is about retrieval policy after the overview skill has already decided the task belongs to MindAtlas.
It is not the broad default router for every history-related request.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- When the user wants to find, review, or ask about previously stored information, choose the most suitable MindAtlas retrieval path

Session-visible tool rule:
- Start from the current session's visible `mindatlas_*` tools
- Do not assume a separate catalog tool exists in the current session
- Respect only the MindAtlas tools that are actually visible right now

If the current session does not expose any `mindatlas_*` tools:
- Say explicitly that MindAtlas capabilities are not exposed in this session
- Suggest starting a new OpenClaw session or reloading the Gateway / plugin
- Do not silently treat generic memory as if it were MindAtlas retrieval

Yield to summary:
- If the request is primarily a recap, review, digest, or weekly / monthly summary and a MindAtlas report tool is visible, let the summary skill lead
- Retrieval can still support summary by supplying search results or exact record details when report output is unavailable

Preferred retrieval routes:

1. Search-style capability
- Use this when the user asks whether something was recorded before
- Use it for keyword, tag, type, and time-bounded lookups such as "find what I saved", "did I record this", or "what records did I create recently"
- Start here when the user gives exact terms or when you need candidate records first
- Prefer `mindatlas_search_entries` when it is visible

2. Detail-style capability
- Use this when you already have a record ID
- Use it when the user wants the full content of one specific record
- Use it after search when a candidate needs expansion
- Prefer `mindatlas_get_entry` when it is visible

3. Graph / RAG / cross-record capability
- Use this when the user asks a semantic, relational, or synthesized question
- Use it when the answer likely requires combining multiple records or understanding patterns over time
- Prefer `mindatlas_query_knowledge_graph` when the user asks what is related, why it is related, or what pattern exists across records

Retrieval strategy:
- Start with the lightest useful route
- Prefer precision for exact lookup questions
- Prefer broader recall for exploratory memory questions
- If results are ambiguous, ask a brief clarifying question or show the best candidates
- If the question is inherently cross-record, prefer a graph-style or synthesis-friendly capability when one is exposed

Time awareness:
- Translate relative requests like "recently", "this week", "last month", or "a few days ago" into a reasonable time-bounded retrieval strategy when possible
- Ask briefly only when precision really matters
- If report tools are not visible, time-bounded retrieval can still provide the evidence for a later recap or synthesis step

Result presentation:
- Do not dump raw JSON unless the user clearly wants it
- Prefer a concise answer that highlights title, short summary, time, type, or tags when helpful
- If multiple records match, summarize the set before expanding every field
- Include record IDs only when follow-up detail lookup or relation work is likely

Correctness:
- If nothing relevant is found, say so directly
- Do not imply MindAtlas contains information that was never retrieved
- If the evidence is weak or ambiguous, say that clearly
