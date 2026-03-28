# MindAtlas Auto Capture Skill

This page is a lightweight reference for the shipped OpenClaw `mindatlas-auto-capture` skill.

## Canonical Prompt File

- [`integrations/openclaw-mindatlas/skills/mindatlas-auto-capture/SKILL.md`](../../integrations/openclaw-mindatlas/skills/mindatlas-auto-capture/SKILL.md)

The plugin copy is the only authoritative prompt source. Update that file first; keep this page as a short explainer.

## Responsibility

- Defines when OpenClaw should proactively record durable experiences into MindAtlas
- Keeps capture conservative and task-level instead of recording every message
- Prefers thin-context submission and workflow-backed capture over field-level entry assembly
- Treats automatic capture as prompt-driven best effort, not a guaranteed task hook

## Working Rules

- Stay catalog-first and prefer the currently exposed recording capability that best matches the situation
- Let MindAtlas materialize final entry fields whenever the chosen capability supports that
- Use field-level creation only for explicit manual-entry cases where the exposed capability requires structured fields
