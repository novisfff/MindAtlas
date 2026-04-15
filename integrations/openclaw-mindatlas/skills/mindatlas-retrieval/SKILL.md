---
name: mindatlas-retrieval
description: Search, detail, graph, and custom-capability retrieval policy for previous MindAtlas records, exact entry lookup, time-bounded searches, and relation-aware recall.
---

# MindAtlas Retrieval

You can use MindAtlas as the user's long-term knowledge and experience store.

This skill is about retrieval policy after the overview skill has already decided the task belongs to MindAtlas.
It is not the broad default router for every history-related request.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- When the user wants to find, review, or ask about previously stored information, choose the most suitable MindAtlas retrieval path

Priority order:
1. Prefer visible dedicated retrieval tools such as `mindatlas_search_entries`, `mindatlas_get_entry`, or `mindatlas_query_knowledge_graph`
2. If the request needs a custom exposed retrieval workflow or agent that is not already visible, route back through overview/dispatcher
3. If the request is mainly a recap or review, let the summary skill lead

Session-visible tool rule:
- Start from the current session's visible `mindatlas_*` tools
- Respect only the MindAtlas tools that are actually visible right now
- Do not assume a separate catalog tool exists in the current session unless the dispatcher tools are visible

Preferred retrieval routes:
1. Search-style capability
- Use this for keyword, tag, type, and time-bounded lookups such as "find what I saved", "did I record this", or "what records did I create recently"
- Prefer `mindatlas_search_entries` when it is visible

2. Detail-style capability
- Use this when you already have a record ID or need exact details from a search candidate
- Prefer `mindatlas_get_entry` when it is visible

3. Graph / cross-record capability
- Use this when the answer likely requires combining multiple records or understanding patterns over time
- Prefer `mindatlas_query_knowledge_graph` when the user asks what is related, why it is related, or what pattern exists across records

4. Custom retrieval capability
- If the user asks for a named custom workflow, team process, or special retrieval path that is not covered by the visible dedicated tools, route back through the dispatcher flow and inspect `mindatlas_list_capabilities`
- If the current session still looks stale after plugin, shipped-skill, or catalog changes, route back through overview/dispatcher and recommend reinstalling the plugin path, rerunning `configure:skills`, then restarting the Gateway and opening a new session

Correctness:
- If nothing relevant is found, say so directly
- Do not imply MindAtlas contains information that was never retrieved
- If the evidence is weak or ambiguous, say that clearly
