---
name: mindatlas-dispatcher
description: Dynamic capability discovery and fallback policy for MindAtlas when no visible dedicated `mindatlas_*` tool clearly matches the request.
---

# MindAtlas Dispatcher

Use this skill after the overview skill has already decided the request belongs to MindAtlas, but the current session does not expose a clearly matching dedicated MindAtlas tool.

Primary goal:
- Discover the latest exposed MindAtlas capabilities and execute the right custom tool, workflow, or agent without waiting for a dedicated session-visible tool to appear

When to use the dispatcher:
- The user wants a MindAtlas workflow, agent, or custom capability that is not obviously covered by the visible dedicated `mindatlas_*` tools
- The administrator may have exposed a new custom capability after the current plugin session started
- A generic built-in route like capture, retrieval, review, or graph reasoning is too weak or too broad for the task

Preferred dispatcher flow:
1. Use `mindatlas_list_capabilities` to fetch the latest MindAtlas capability catalog
2. Inspect each candidate's `title`, `description`, `sourceType`, `implementationType`, `inputSummary`, and `inputSchema`
3. Prefer a dedicated visible `mindatlas_*` tool if the list shows one is already registered and clearly matches the request
4. Otherwise choose the best matching capability and call `mindatlas_run_capability`

How to pick a capability:
- Match the user's goal first, not just keyword overlap
- Prefer capabilities whose description or title clearly names the workflow, project, team process, review, or agent the user asked for
- Use `sourceType` to understand whether the capability is a tool, workflow, or agent path
- Use `inputSchema` and `inputSummary` to shape the request payload
- If multiple custom capabilities overlap, choose the narrower and more intentional one

How to call `mindatlas_run_capability`:
- Pass the selected `capabilityKey`
- Put the target capability payload inside `input`
- Follow the target `inputSchema`; do not invent extra wrapper fields
- Keep the payload structured and concise

Correctness rules:
- Dispatcher is a fallback and dynamic-discovery tool, not the default replacement for every dedicated MindAtlas tool
- Do not skip a clearly matching dedicated tool just because dispatcher is available
- If `mindatlas_list_capabilities` returns no suitable custom capability, fall back to the best dedicated built-in route when one still makes sense
- If neither dedicated MindAtlas tools nor dispatcher tools are visible, say MindAtlas is not exposed in the current session
- Dispatcher helps when the current session lacks a dedicated tool for a newly exposed capability, but it does not replace plugin or shipped-skill reinstall after upgrades
- If the administrator just upgraded the plugin or shipped skills, or if the current session still shows obviously stale guidance, tell them to re-run plugin install, re-run `configure:skills`, restart the Gateway, and open a brand-new session
