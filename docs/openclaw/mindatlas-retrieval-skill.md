# MindAtlas Retrieval Skill Draft

Use this as the source prompt for a future OpenClaw `mindatlas-retrieval` skill.

## Skill Intent

Teach the OpenClaw agent how to retrieve the right information from MindAtlas and how to choose between search, detail lookup, and knowledge-graph style querying.

## Suggested Skill Text

```md
You can use MindAtlas as the user's long-term knowledge and experience store.

This skill is about **retrieval**, not about capture or report generation.

## Primary goal

When the user wants to find, review, or ask about previously stored information, choose the most suitable MindAtlas retrieval path.

## Retrieval routing

Prefer these routes:

### 1. Search entries
Use entry search when the user asks for:
- whether something was recorded before
- records matching a keyword
- records within a type, tag set, or time range
- recent items about a topic

Typical requests:
- "我之前记过 OpenClaw 吗"
- "搜一下和插件有关的记录"
- "找最近一周和部署相关的内容"

### 2. Get entry detail
Use entry detail lookup when:
- you already have an entry ID
- the user wants the full content of a specific record
- a previous search result needs expansion

### 3. Query knowledge graph / RAG
Use knowledge-graph style querying when:
- the user asks a semantic or cross-record question
- the answer likely requires combining multiple records
- the user asks for context, patterns, or synthesized recall rather than exact keyword matches

Typical requests:
- "最近和 OpenClaw 相关的重点是什么"
- "我最近在折腾哪些自动化方向"
- "围绕部署和插件，我之前形成过哪些结论"

## Retrieval strategy

Start with the lightest useful retrieval path.
- If the user gives exact keywords, use search first
- If search results are ambiguous, ask a brief clarifying question or show the best candidates
- If the question is inherently cross-record, use knowledge graph / RAG directly when available

## Result presentation

Do not dump raw JSON unless the user clearly wants it.

Prefer a concise structured answer that includes:
- title
- short summary
- relevant tags or type when useful
- time when useful
- record ID only when follow-up detail or relation work is likely

If multiple records match, summarize the set first instead of expanding every field.

## Time-awareness

When the user asks things like:
- 最近
- 这周
- 上个月
- 前几天

translate that into an explicit time-bounded retrieval strategy when possible.

## Precision vs recall

If the user asks a precise lookup question, prefer precision.
If the user asks an exploratory memory question, prefer broader recall.

## Follow-up behavior

After showing search results:
- offer to expand a specific entry
- offer to continue with a knowledge-graph query if the user is asking for synthesis
- avoid automatically opening every result in detail

## Safety and correctness

If nothing relevant is found, say so directly.
Do not pretend MindAtlas contains something that was never retrieved.
If the result set is weak or ambiguous, say so.
```

## Notes

- This skill should focus on *retrieval policy*.
- It should not decide what to record or how to generate reports.
- Keep result presentation concise and human-friendly.
