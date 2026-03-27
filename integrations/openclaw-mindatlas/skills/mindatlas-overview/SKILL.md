# MindAtlas Overview

You can use MindAtlas as a personal knowledge and experience management system.

MindAtlas is best for:
- Recording things the user wants to keep
- Organizing experiences, knowledge, projects, and notes
- Searching past entries
- Building relations between entries
- Querying the knowledge graph when that capability is available
- Generating weekly or monthly reports from what the user has recorded
- Running administrator-curated workflows or agent capabilities when their titles and descriptions clearly match the user's request

MindAtlas is not meant to be:
- A generic cloud drive
- A full task-management system
- Unlimited passive memory with no structure

Prefer MindAtlas tools when the user says things like:
- "记一下 / remember this"
- "帮我整理一下 / organize this"
- "我之前写过吗 / did I write about this before"
- "帮我关联一下 / connect these two things"
- "生成周报 / generate my weekly report"
- "生成月报 / generate my monthly report"
- "按我已经配置好的流程处理一下 / run the workflow or agent I configured in MindAtlas"

Before calling a MindAtlas capability:
- Read the capability catalog provided by MindAtlas
- Only use the catalog items that are currently exposed
- Respect `available` and `availabilityReason`
- Prefer the catalog item's title, description, input summary, and output summary over guesswork
- If a required type, relation name, or workflow parameter is unclear, ask the user briefly instead of guessing wildly

When using MindAtlas:
- Keep tool arguments concise and structured
- Reuse returned IDs when you need follow-up detail or relation creation
- Treat MindAtlas as the source of truth for stored knowledge
- Do not assume a fixed built-in tool list; administrators may expose custom workflows, tools, or agents through the catalog
