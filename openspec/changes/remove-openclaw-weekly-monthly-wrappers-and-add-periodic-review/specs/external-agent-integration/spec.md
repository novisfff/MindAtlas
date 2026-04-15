## MODIFIED Requirements

### Requirement: OpenClaw Recap Surface SHALL Use One Periodic Review Capability
MindAtlas SHALL expose one workflow-backed OpenClaw periodic review capability instead of separate weekly and monthly report wrappers.

#### Scenario: Settings and runtime catalog expose only the unified review capability
- **WHEN** MindAtlas seeds, resets, or lists OpenClaw system capability catalog items
- **THEN** it SHALL expose `generate_periodic_review` with the tool name `mindatlas_generate_periodic_review`
- **AND** it SHALL no longer expose `generate_weekly_report` or `generate_monthly_report` as shipped system items

#### Scenario: OpenClaw executes periodic review through the shared workflow contract
- **WHEN** OpenClaw executes `generate_periodic_review`
- **THEN** MindAtlas SHALL run the published `periodic_review_core` workflow contract
- **AND** the capability result SHALL be a JSON object with a single `content` string field

#### Scenario: Legacy weekly and monthly capability keys are unavailable
- **WHEN** an OpenClaw runtime request still targets `generate_weekly_report` or `generate_monthly_report`
- **THEN** MindAtlas SHALL return the capability as unavailable or not found
- **AND** it SHALL not provide compatibility execution through the removed weekly/monthly wrappers

#### Scenario: Legacy report source bindings are flagged as invalid
- **WHEN** an existing custom OpenClaw catalog item still points at the removed weekly/monthly report source tool names
- **THEN** MindAtlas SHALL keep that catalog item marked unavailable in settings/runtime metadata
- **AND** new or updated catalog items SHALL be prevented from binding to those removed source tool names
