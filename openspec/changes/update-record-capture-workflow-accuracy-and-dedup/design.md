## Context
MindAtlas now has two distinct record-ingestion workflows:
- `smart_capture`: user-facing capture with human confirmation before persistence
- `context_capture`: OpenClaw-facing thin-context workflow that auto creates or auto merges

Both flows already work, but they still suffer from three classes of avoidable error:
- memory leakage can bias capture output with stale conversation facts
- explicit invalid type or time inputs can be silently rewritten into a “valid enough” payload
- keyword-based duplicate recall is too narrow and too brittle, which hurts both duplicate awareness in `smart_capture` and confident auto-merge decisions in `context_capture`

## Goals
- Improve capture accuracy without changing the external `submit_context_capture` contract.
- Keep `smart_capture` assistant-guided and human-confirmed while surfacing likely semantic duplicates before approval.
- Keep `context_capture` automated, but merge only when the recalled top candidate is clearly judged to be the same durable record.
- Reuse existing types and tags more consistently across both flows.

## Non-Goals
- No new public OpenClaw capability.
- No human confirmation added to `context_capture`.
- No auto-merge added to `smart_capture`.
- No new read-only widget added to the human approval form.

## Decisions
### Capture Memory Isolation
Both shipped capture workflows will set `start.memoryMode=off`. This ensures field materialization is based on current input, `sys` metadata, and explicit tool outputs only.

### Strict Explicit Write Validation
`create_entry` and `update_entry` will keep fallback defaults only for omitted or blank fields:
- blank `type_code` still falls back to the default enabled type
- non-blank invalid `type_code` raises an error
- omitted time fields still fall back to `POINT + today`
- explicitly malformed, partial, or conflicting time fields raise an error instead of being silently corrected

### Shared Semantic Retrieval Tool
Both capture workflows will use one shared public system tool, `search_similar_entries`, backed by `LightRagService.recall_sources()`.

The tool will:
1. accept a single semantic `query` plus `limit`
2. recall LightRAG sources without relying on the upstream vector score as a decision score
3. aggregate recalled chunks back to entry-level candidates through `entry_id`
4. include both entry and attachment hits in the candidate evidence
5. return entry-level candidates with retrieval metadata such as matched source kinds, snippets, and retrieval rank
6. degrade to `status=unavailable` with empty candidates when LightRAG is unavailable or recall fails

The tool does not decide same-record identity; it only produces semantic candidates.

### smart_capture Flow
`smart_capture` will pre-search with a single semantic lookup query derived from raw input, then let an LLM rank and filter the returned entry candidates before the human triage step.

The human decision remains the final merge/create chooser before persistence.

### context_capture Flow
`context_capture` keeps its thin-context public contract, but its internal graph will:
1. load both types and tags up front
2. materialize final fields early
3. prepare one semantic lookup query plus same-record clues
4. run one semantic candidate recall
5. keep only the top recalled entry candidate
6. let the LLM conservatively decide whether that top candidate is truly the same durable record

This keeps merge conservative while improving recall for long-running topics and tag reuse.
