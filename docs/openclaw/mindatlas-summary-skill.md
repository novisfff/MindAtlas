# MindAtlas Summary Skill Draft

Use this as the source prompt for a future OpenClaw `mindatlas-summary` skill.

## Skill Intent

Teach the OpenClaw agent how to generate useful summaries from MindAtlas, including weekly reviews, monthly reviews, and topic-oriented summaries.

## Suggested Skill Text

```md
You can use MindAtlas to summarize the user's experiences, notes, and progress over time.

This skill is about **summarization**, not about deciding what to capture or how to do generic retrieval.

## Primary goal

When the user asks for a recap, review, digest, or summary of what they have done, use MindAtlas to produce a useful structured summary.

## Summary routing

Prefer these routes:

### 1. Weekly summary
Use the weekly report capability when the user asks for:
- 周报
- this week review
- what happened this week
- a weekly digest

### 2. Monthly summary
Use the monthly report capability when the user asks for:
- 月报
- this month review
- what happened this month
- a monthly digest

### 3. Topic summary
If a topic-summary capability exists, use it when the user asks for:
- a summary of work around a project, topic, or theme
- a digest of all records related to a subject

If a dedicated topic-summary capability does not exist, retrieve relevant records first and then synthesize the result.

## Summary structure

Prefer summaries that include:
- the main themes
- key completed items
- notable progress
- important problems or blockers
- reusable conclusions or lessons

Do not just concatenate raw records.

## Time-bounded summaries

When the user specifies a time period, honor it.
Examples:
- 这周
- 上周
- 本月
- 最近两周
- 三月下旬

If the requested period is vague, use a reasonable interpretation or ask a brief clarifying question when precision matters.

## Topic-oriented summaries

For topic summaries, prefer grouping by:
- major themes
- project progress
- repeated issues
- notable decisions

If many records exist, synthesize patterns instead of listing every item.

## Presentation style

Keep the final answer readable and useful.
Prefer:
- short overview first
- then bullets for highlights
- then optional deeper detail if needed

Avoid overwhelming the user with every raw record unless explicitly requested.

## When not to summarize

If there is too little data, say that clearly.
If only one or two weakly related records exist, do not overstate patterns.

## Follow-up behavior

After a summary, it is often useful to offer one next step, such as:
- expand a particular topic
- show the source records
- generate a more focused summary

Do this briefly and only when it helps.
```

## Notes

- This skill should focus on *summary policy*.
- It should prefer native MindAtlas reporting capabilities when they exist.
- Topic summary can be implemented as retrieval + synthesis in the first phase.
