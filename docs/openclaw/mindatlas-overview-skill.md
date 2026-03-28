# MindAtlas Overview Skill Draft

Use this as the source prompt for a future OpenClaw `mindatlas-overview` skill.

## Skill Intent

Teach the OpenClaw agent what MindAtlas is, what it is good at, and when it should prefer MindAtlas capabilities.

## Suggested Skill Text

```md
You can use MindAtlas as the user's long-term knowledge and experience system.

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

## When to prefer MindAtlas

Prefer MindAtlas when the user is trying to:
- remember something for later
- find something they recorded before
- review what happened over time
- summarize a period, project, or topic
- connect related ideas, events, or records
- query across stored knowledge rather than only the current conversation

Typical requests include:
- "记一下 / remember this"
- "我之前记过吗 / did I record this before"
- "帮我搜一下 / search for this"
- "总结一下我最近做了什么 / summarize what I've been doing"
- "生成周报 / 月报"
- "帮我关联一下这两条记录"
- "按我在 MindAtlas 里配置好的流程处理一下"

## How to think about MindAtlas

Treat MindAtlas as a structured, durable system of record.
It is more appropriate than ad-hoc conversation memory when the information:
- should survive future sessions
- may need to be searched later
- belongs to a timeline, project, topic, or relationship graph
- should contribute to future summaries or reports

## Tool-use guidance

Before calling a MindAtlas capability:
- rely on the current capability catalog instead of assuming a fixed tool list
- respect whether a capability is currently available
- use the catalog item's title, description, input summary, and output summary to guide tool choice
- ask a short clarifying question if a required parameter is unclear and guessing would likely be wrong

When using MindAtlas:
- keep tool arguments concise and structured
- prefer a stable type, title, summary, and a small number of useful tags
- reuse returned IDs for follow-up detail lookup or relation creation
- treat stored MindAtlas records as the source of truth for previously saved knowledge

## Boundaries

Do not force every conversation into MindAtlas.
Prefer MindAtlas for durable memory, retrieval, reporting, and structured knowledge work.
Use other tools or direct answers when the task does not benefit from long-term storage or structured recall.
```

## Notes

- This is the broad orientation skill for MindAtlas.
- It should stay higher-level than `mindatlas-auto-capture`, `mindatlas-retrieval`, and `mindatlas-summary`.
- The overview skill explains positioning and preference; the other skills define more specific behavior.
