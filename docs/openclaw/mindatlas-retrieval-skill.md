# MindAtlas Retrieval Skill

This page is a lightweight reference for the shipped OpenClaw `mindatlas-retrieval` skill.

## Canonical Prompt File

- [`integrations/openclaw-mindatlas/skills/mindatlas-retrieval/SKILL.md`](../../integrations/openclaw-mindatlas/skills/mindatlas-retrieval/SKILL.md)

The plugin copy is the only authoritative prompt source. Update that file first; keep this page as a short explainer.

## Responsibility

- Routes retrieval requests between search-style, detail-style, and graph-style MindAtlas capabilities
- Works as a narrower retrieval strategy after the overview router chooses MindAtlas
- Starts from the current session's visible `mindatlas_*` tools instead of assuming fixed tool names or a separate catalog tool
- Guides result presentation so users get useful answers instead of raw payload dumps
- Enforces honest behavior when evidence is weak, ambiguous, or absent

## Working Rules

- Start with the lightest useful retrieval route
- Prefer precision for exact lookups and broader recall for exploratory memory questions
- Yield to summary when the request is really a weekly, monthly, or recap-style review and a report path is visible
- Use record IDs and follow-up detail lookup only when needed
