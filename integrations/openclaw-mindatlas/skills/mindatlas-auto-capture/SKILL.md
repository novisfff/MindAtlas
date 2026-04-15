---
name: mindatlas-auto-capture
description: Capture policy for remember/save/record/store/archive requests that should create durable MindAtlas memory through dedicated or dynamically exposed MindAtlas capture capabilities.
---

# MindAtlas Auto Capture

You can use MindAtlas as the user's long-term store for experiences, notes, decisions, and reusable conclusions.

This skill is only for capture, create, store, and durable-memory submission after the overview skill has already routed the request to MindAtlas.
Treat the current integration as a personal single-user setup unless the administrator explicitly says otherwise.

Primary goal:
- Record durable, high-value experiences into MindAtlas with low noise

Prefer recording when at least one of these is true:
- The user explicitly asks to remember, record, save, or store something
- A task has reached a meaningful completion point
- A bug, failure, or blocker has been diagnosed and resolved
- A setup, installation, deployment, or configuration task has been completed
- A stable decision, lesson, or conclusion has been produced

Priority order:
1. Prefer `mindatlas_submit_context_capture` or another visible dedicated capture path
2. If no visible dedicated capture tool matches but the task still clearly belongs in MindAtlas, route back through overview/dispatcher and inspect custom capabilities
3. If no capture path exists, do not silently downgrade the request into transient local memory

Capture workflow:
1. Prefer `mindatlas_submit_context_capture` or another visible capture workflow when the user explicitly wants durable memory
2. When the chosen capability asks only for `context`, submit one high-value `context` block instead of assembling every final entry field yourself
3. In that `context`, include what happened, the result, why it matters later, and any clear time clues
4. Do not manually pass source, channel, session, or tool fields when the capability only asks for `context`; OpenClaw provides that request metadata automatically
5. Let MindAtlas materialize the final entry type, summary, content, tags, relations, merge, and dedupe behavior whenever the chosen capability supports that

Custom capability rule:
- Do not assume the only valid capture path is the built-in submit-context tool
- Administrators may expose custom capture workflows or agents
- If the visible dedicated capture tools are insufficient, route back through the dispatcher flow and inspect `mindatlas_list_capabilities`
- If the current session's MindAtlas capture surface looks stale after plugin, shipped-skill, or catalog changes, route back through overview/dispatcher and recommend reinstalling the plugin path, rerunning `configure:skills`, then restarting the Gateway and opening a new session

User control:
- If the user explicitly does not want something recorded, do not store it
- If privacy sensitivity is unclear, ask briefly instead of assuming
- If the information is only temporary or operational and does not belong in long-term knowledge, do not force it into MindAtlas
