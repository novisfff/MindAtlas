## MODIFIED Requirements

### Requirement: The Plugin SHALL Ship The MindAtlas Overview Skill
The plugin package SHALL bundle a `MindAtlas Overview` skill so OpenClaw agents know what MindAtlas is for and when to use it, and that skill SHALL act as the primary router for MindAtlas-related work.

#### Scenario: Overview skill routes durable memory and recap work first
- **WHEN** the current session exposes `mindatlas_*` tools and the user asks to remember something, retrieve previous records, recap recent activity, connect records, query relationships, or run a published MindAtlas workflow or agent
- **THEN** the overview skill SHALL direct the agent to prefer the visible `mindatlas_*` tool surface as the main MindAtlas path
- **AND** the subordinate summary, retrieval, and auto-capture skills SHALL act as narrower follow-on strategies instead of competing broad routers

#### Scenario: Overview skill handles missing session-visible tools explicitly
- **WHEN** the current session does not expose any `mindatlas_*` tools
- **THEN** the overview skill SHALL instruct the agent to explicitly say that MindAtlas is not exposed in the current session
- **AND** it SHALL not treat generic local memory as an implicit MindAtlas substitute

### Requirement: The Plugin SHALL Discover The MindAtlas Capability Catalog Dynamically
The plugin SHALL fetch the MindAtlas runtime catalog and register OpenClaw tools from the returned catalog items rather than from a hard-coded list.

#### Scenario: Registered tools carry routing-oriented MindAtlas hints
- **WHEN** the plugin registers a discovered `mindatlas_*` capability
- **THEN** the generated tool description SHALL include the capability's existing metadata
- **AND** it SHALL also include route hints that make the MindAtlas task category obvious, such as durable capture, historical lookup, time-bounded recap, relation work, graph reasoning, or published workflow/agent execution
