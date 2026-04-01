## MODIFIED Requirements

### Requirement: Shipped OpenClaw System Items SHALL Behave As First-Class Catalog Items
The system SHALL treat shipped OpenClaw defaults as first-class catalog items while keeping the shipped capability surface aligned to the current official OpenClaw product contract and routing-friendly for MindAtlas tasks.

#### Scenario: System capability metadata reinforces the intended task classes
- **WHEN** MindAtlas returns the 7 shipped OpenClaw system capabilities in runtime discovery
- **THEN** their titles, descriptions, input summaries, and output summaries SHALL make the intended routing cues obvious
- **AND** those cues SHALL cover durable capture, previous-record lookup, exact detail lookup, relation creation, graph reasoning, and weekly or monthly recap work
- **AND** the capability keys, tool names, and schemas SHALL remain unchanged
