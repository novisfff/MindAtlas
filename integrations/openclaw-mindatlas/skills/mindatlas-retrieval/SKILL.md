---
name: mindatlas-retrieval
description: Retrieval policy for choosing the right MindAtlas search, detail, graph, or synthesis path when the user asks about previously stored information.
---

# MindAtlas Retrieval

You can use MindAtlas as the user's long-term knowledge and experience store.

This skill is about retrieval policy.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- When the user wants to find, review, or ask about previously stored information, choose the most suitable MindAtlas retrieval path

Always start catalog-first:
- Read the current capability catalog before choosing a MindAtlas tool
- Do not assume fixed built-in tool names
- Respect `available` and `availabilityReason`
- Use the catalog item's title, description, input summary, and output summary to choose the best route

Preferred retrieval routes:

1. Search-style capability
- Use this when the user asks whether something was recorded before
- Use it for keyword, tag, type, and time-bounded lookups
- Start here when the user gives exact terms or when you need candidate records first

2. Detail-style capability
- Use this when you already have a record ID
- Use it when the user wants the full content of one specific record
- Use it after search when a candidate needs expansion

3. Graph / RAG / cross-record capability
- Use this when the user asks a semantic, relational, or synthesized question
- Use it when the answer likely requires combining multiple records or understanding patterns over time

Retrieval strategy:
- Start with the lightest useful route
- Prefer precision for exact lookup questions
- Prefer broader recall for exploratory memory questions
- If results are ambiguous, ask a brief clarifying question or show the best candidates
- If the question is inherently cross-record, prefer a graph-style or synthesis-friendly capability when one is exposed

Time awareness:
- Translate relative requests like "recently", "this week", "last month", or "a few days ago" into a reasonable time-bounded retrieval strategy when possible
- Ask briefly only when precision really matters

Result presentation:
- Do not dump raw JSON unless the user clearly wants it
- Prefer a concise answer that highlights title, short summary, time, type, or tags when helpful
- If multiple records match, summarize the set before expanding every field
- Include record IDs only when follow-up detail lookup or relation work is likely

Correctness:
- If nothing relevant is found, say so directly
- Do not imply MindAtlas contains information that was never retrieved
- If the evidence is weak or ambiguous, say that clearly
