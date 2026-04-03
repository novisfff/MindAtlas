---
name: mindatlas-auto-capture
description: Capture policy for remember/save/record/store/archive requests that should create durable MindAtlas memory through `mindatlas_submit_context_capture` or another visible capture workflow.
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
- The result is likely to matter for later recall, reuse, reporting, or review

Avoid recording when the conversation is mostly:
- Small talk
- Short factual Q&A with no durable value
- Unverified guesses
- Repetitive restatements
- Low-signal fragments that do not deserve long-term storage

Session-visible tool rule:
- Start from the current session's visible `mindatlas_*` tools
- Prefer `mindatlas_submit_context_capture` when it is visible and the user wants something remembered, saved, stored, recorded, or archived
- Do not assume a separate catalog tool exists inside the current session

If the current session does not expose any `mindatlas_*` tools:
- Say explicitly that MindAtlas capabilities are not exposed in this session
- Suggest starting a new OpenClaw session or reloading the Gateway / plugin
- Do not silently downgrade the request into transient local memory

Capture timing:
- Prefer task-level capture over message-level capture
- Wait for a clear milestone, result, or completed subtask instead of recording every turn
- Automatic capture is a prompt-driven best-effort behavior, not a guaranteed system hook

Capture workflow:
1. Prefer `mindatlas_submit_context_capture` or another visible capture workflow when the user explicitly wants durable memory
2. When `mindatlas_submit_context_capture` is visible, submit one high-value `context` block instead of assembling every final entry field yourself
3. In that `context`, include what happened, the result, why it matters later, and any clear time clues
4. Do not manually pass source, channel, session, or tool fields when the capability only asks for `context`; OpenClaw provides that request metadata automatically
5. Let MindAtlas materialize the final entry type, summary, content, tags, relations, merge, and dedupe behavior whenever the chosen capability supports that
6. If duplicate risk is high and `mindatlas_search_entries` is visible, do a lightweight recent search first

Field-level creation guidance:
- Do not assume you should assemble the full entry schema yourself
- Only use a field-level creation capability when the exposed catalog item explicitly requires structured fields and the user has already provided them clearly

Title and summary guidance:
- If the chosen capability asks for a short title or summary, keep it concise, specific, and factual
- Prefer describing what was done, what the result was, and why it matters later
- Avoid vague labels like "some setup" or "random note"

Tags:
- Use only a few tags that are likely to help later retrieval
- Prefer project names, system names, components, and clear task categories
- Avoid over-tagging

User control:
- If the user explicitly does not want something recorded, do not store it
- If privacy sensitivity is unclear, ask briefly instead of assuming
- If the information is only temporary or operational and does not belong in long-term knowledge, do not force it into MindAtlas
