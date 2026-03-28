# MindAtlas Overview Skill

This page is a lightweight reference for the shipped OpenClaw `mindatlas-overview` skill.

## Canonical Prompt File

- [`integrations/openclaw-mindatlas/skills/mindatlas-overview/SKILL.md`](../../integrations/openclaw-mindatlas/skills/mindatlas-overview/SKILL.md)

The plugin copy is the only authoritative prompt source. Update that file first; keep this page as a short explainer.

## Responsibility

- Introduces MindAtlas as the user's long-term knowledge and experience system
- Explains when OpenClaw should prefer MindAtlas over transient chat memory
- Enforces catalog-first tool selection instead of hard-coded tool names
- States the current product boundaries: single-user setup, context-submission-friendly recording, and prompt-driven best-effort capture

## Relationship To Other Shipped Skills

- `mindatlas-auto-capture`: capture timing, thin-context submission, and low-noise recording policy
- `mindatlas-retrieval`: search, detail lookup, and graph-style retrieval routing
- `mindatlas-summary`: weekly, monthly, and topic-summary routing
