# MindAtlas Summary Skill

This page is a lightweight reference for the shipped OpenClaw `mindatlas-summary` skill.

## Canonical Prompt File

- [`integrations/openclaw-mindatlas/skills/mindatlas-summary/SKILL.md`](../../integrations/openclaw-mindatlas/skills/mindatlas-summary/SKILL.md)

The plugin copy is the only authoritative prompt source. Update that file first; keep this page as a short explainer.

## Responsibility

- Routes weekly, monthly, recent-activity, and topic-oriented summary requests
- Works as a narrower recap strategy after the overview router chooses MindAtlas
- Prefers currently exposed report or summary capabilities when they exist
- Falls back to retrieval plus synthesis when the visible tool surface does not expose a dedicated report capability
- Keeps summary output concise, structured, and honest about sparse evidence

## Working Rules

- Start from the current session's visible `mindatlas_*` tools and choose the summary path that best matches the user's request and time range
- For recap questions, stay report-first and fall back to `mindatlas_search_entries` plus synthesis when a dedicated report tool is absent
- Prefer synthesized patterns and conclusions over raw record concatenation
- Offer one concise follow-up only when it helps the user go deeper
