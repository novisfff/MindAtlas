# MindAtlas Auto Capture Skill Draft

Use this as the source prompt for a future OpenClaw `mindatlas-auto-capture` skill.

## Skill Intent

Teach the OpenClaw agent when it should proactively record high-value experiences into MindAtlas, and how to do so with low noise.

## Suggested Skill Text

```md
You can use MindAtlas as the long-term store for the user's experiences, notes, decisions, and reusable conclusions.

This skill is about **capturing**, not about generic retrieval or reporting.

## Primary goal

Record high-value experiences into MindAtlas with low noise.

Prefer recording only when at least one of these is true:
- The user explicitly says to remember, record, save, or store something
- A task has clearly reached a meaningful completion state
- A bug, failure, or blocker has been diagnosed and fixed
- A setup, installation, deployment, or configuration task has been completed
- A stable conclusion, lesson, or decision has been produced
- The result is likely to be useful later for recall, reuse, or summarization

Avoid recording when the conversation is mostly:
- Small talk
- Short factual Q&A with no durable value
- Unverified guesses
- Repetitive restatements of the same thing
- Low-signal emotional fragments with no enduring context

## Capture timing

Prefer task-level capture over message-level capture.

Do not try to record every message. Instead, wait until there is a clear milestone, result, or completed subtask.

## Capture workflow

When a capture-worthy event happens:
1. Compress the event into a concise experience summary
2. Choose the most suitable entry type
3. Add a small number of useful tags
4. If recent duplicate records are likely, search first
5. Prefer idempotent write or merge behavior when available
6. Otherwise create a new record

## Entry-type guidance

Use the closest available type among these patterns:
- EXPERIENCE: something happened and is worth remembering
- KNOWLEDGE: a reusable conclusion, method, or lesson
- ISSUE: a concrete problem or failure
- DECISION: a choice with clear reasoning or impact
- PROJECT_PROGRESS: progress within a larger project
- SOLUTION: a concrete fix or resolution

If the system only has a smaller type set, choose the closest stable type instead of inventing one.

## Suggested title style

Titles should be concise and specific.

Good examples:
- Installed OpenClaw and completed initial setup
- Enabled MindAtlas plugin in OpenClaw
- Fixed plugin whitelist loading issue
- Confirmed MindAtlas capability catalog is available

Avoid vague titles like:
- Today did some setup
- Plugin issue
- Random note

## Suggested summary style

Summaries should capture:
- what was done
- what the result was
- why it matters later

Keep summaries short and factual.

## Tags

Use only a few tags that are likely to help retrieval later.
Prefer:
- project or system name
- task category
- major tool or component

Avoid over-tagging.

## Duplicate control

Before creating a new record, consider whether the same session or same task already produced a very similar record recently.

If an idempotent upsert tool exists, prefer it.
If only search + create exists, do a lightweight recent search first when duplicate risk is high.

## Tool preference

Prefer MindAtlas capture tools when the value is durable.
If the information is only temporary or operational and does not belong in long-term knowledge, do not force it into MindAtlas.

## User control

If the user explicitly does not want something recorded, do not store it.
If privacy sensitivity is unclear, ask briefly instead of assuming.
```

## Notes

- This skill should focus only on *capture policy*.
- Retrieval and summary behavior should live in separate skills.
- Keep the runtime behavior task-level and conservative in the first version.
