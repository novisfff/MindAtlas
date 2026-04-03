## MODIFIED Requirements

### Requirement: The Plugin SHALL Ship The MindAtlas Overview Skill
The plugin package SHALL bundle a `MindAtlas Overview` skill so OpenClaw agents know what MindAtlas is for and when to use it, and that skill SHALL act as the primary router for MindAtlas-related work.

#### Scenario: Capture guidance tells OpenClaw to submit thin context
- **WHEN** the current session exposes `mindatlas_submit_context_capture`
- **THEN** the shipped capture guidance SHALL tell OpenClaw to provide one high-value context block that includes what happened, the result, why it matters, and any clear time clues
- **AND** it SHALL explicitly discourage hand-assembling final title, summary, type, tags, or time fields when the visible MindAtlas capability does not require them

### Requirement: The Plugin SHALL Discover The MindAtlas Capability Catalog Dynamically
The plugin SHALL fetch the MindAtlas runtime catalog and register OpenClaw tools from the returned catalog items rather than from a hard-coded list.

#### Scenario: Registered capture tool schema is thin-context only
- **WHEN** the plugin registers the shipped `mindatlas_submit_context_capture` capability
- **THEN** the generated tool schema SHALL expose only the required `context` field
- **AND** the generated tool description SHALL reinforce that MindAtlas will extract and merge final entry fields internally
