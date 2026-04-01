---
name: mindatlas-auto-capture
description: Capture policy for deciding when OpenClaw should store durable, high-value context into MindAtlas instead of leaving it in transient chat history.
---

# MindAtlas Auto Capture

You can use MindAtlas as the user's long-term store for experiences, notes, decisions, and reusable conclusions.

This skill is about capture policy.
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

Capture timing:
- Prefer task-level capture over message-level capture
- Wait for a clear milestone, result, or completed subtask instead of recording every turn
- Automatic capture is a prompt-driven best-effort behavior, not a guaranteed system hook

Capture workflow:
1. Read the current MindAtlas capability catalog first
2. Prefer an exposed recording capability or administrator-curated capture workflow
3. Submit thin context rather than assembling every final entry field yourself
4. Include only the high-value clues MindAtlas needs, such as what happened, why it matters, source or session context, likely tag hints, and time hints
5. Let MindAtlas materialize the final entry type, summary, content, tags, relations, merge, and dedupe behavior whenever the chosen capability supports that
6. If duplicate risk is high and a search-style capability is exposed, do a lightweight recent search first

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
