# MindAtlas Summary Skill

This page is a lightweight reference for the shipped OpenClaw `mindatlas-summary` skill.

## Canonical Prompt File

- [`integrations/openclaw-mindatlas/skills/mindatlas-summary/SKILL.md`](../../integrations/openclaw-mindatlas/skills/mindatlas-summary/SKILL.md)

The plugin copy is the only authoritative prompt source. Update that file first; keep this page as a short explainer.

## Responsibility

- Routes weekly, monthly, and topic-oriented summary requests
- Prefers currently exposed report or summary capabilities when they exist
- Falls back to retrieval plus synthesis when the catalog does not expose a dedicated topic-summary capability
- Keeps summary output concise, structured, and honest about sparse evidence

## Working Rules

- Stay catalog-first and choose the summary path that best matches the user's request and time range
- Prefer synthesized patterns and conclusions over raw record concatenation
- Offer one concise follow-up only when it helps the user go deeper
