---
name: mindatlas-universal
description: Search and read MindAtlas knowledge, and propose a new entry only when the user explicitly asks to save new knowledge.
---

# MindAtlas universal

Use `search_entries` before claiming that stored knowledge exists. Use
`get_entry_detail` only for an entry returned by the active context or search.

`create_entry` is the sole supported write. It is available only when the
server's write gate permits it and requires the durable approval flow.

Requests to update, merge, relate, or otherwise mutate existing knowledge are
unsupported. Explain the limitation. Do not translate those requests into
`create_entry`.
