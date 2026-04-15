## MODIFIED Requirements

### Requirement: Quick Stats Skill SHALL Return Grounded MindAtlas Statistics
The `quick_stats` skill SHALL summarize only MindAtlas system data and SHALL ground each conclusion in loaded statistics rather than inferring unsupported trends.

#### Scenario: Quick stats normalizes explicit or relative date ranges from text
- **WHEN** the quick stats workflow runs for a text request
- **THEN** it SHALL extract the requested focus from text input
- **AND** if the user specified a concrete or relative time range it SHALL normalize that request into `start_date/end_date`
- **AND** if the user did not specify a time range it SHALL leave the scoped overview range empty and use the default recent activity window

#### Scenario: Quick stats scopes overview statistics by business time
- **WHEN** a normalized `start_date/end_date` range is available
- **THEN** the overview, type distribution, and tag distribution statistics SHALL be computed from entries whose business time overlaps that range
- **AND** the response SHALL make clear that these scoped totals are not all-time snapshot totals

#### Scenario: Quick stats explains statistical basis
- **WHEN** the workflow returns a summary
- **THEN** the response SHALL distinguish business-time overview statistics from created-at activity statistics
- **AND** it SHALL explicitly avoid presenting created-at activity as business-event time

#### Scenario: Quick stats handles sparse recent data conservatively
- **WHEN** the recent activity window has no new entries or too little data for a strong trend judgment
- **THEN** the response SHALL say that clearly
- **AND** it SHALL NOT claim upward or downward trends without sufficient supporting counts
