## ADDED Requirements

### Requirement: Human Approval Field Schema SHALL Support Widget Metadata
The `human_in_loop` node field schema SHALL support UI widget metadata (`widget`, `options`, `allowCustom`, `placeholder`) in both workflow config and persisted approval field schema.

#### Scenario: Configure select field with options
- **WHEN** workflow config defines a HITL field with `widget=select`
- **THEN** field schema SHALL include `options`
- **AND** workflow validation SHALL reject missing or empty select options

#### Scenario: Backward compatibility without widget
- **WHEN** an existing HITL field omits `widget`
- **THEN** runtime SHALL infer default widget (`switch` for boolean, otherwise `input`)
- **AND** approval form SHALL remain editable and submittable

### Requirement: Human Approval Decision Validation SHALL Enforce Widget Semantics
Approval decision submission SHALL validate values using both declared `type` and widget rules.

#### Scenario: Select/Radio strict option validation
- **WHEN** a submitted value for `select` or `radio` is not in configured `options`
- **THEN** decision submission SHALL fail validation

#### Scenario: Tag selector extensible tags
- **WHEN** field uses `widget=tag_selector` and `allowCustom=true`
- **THEN** submitted `string[]` tags not in options SHALL be accepted

#### Scenario: Date and time format validation
- **WHEN** field uses `widget=date` or `widget=time`
- **THEN** submission SHALL accept only `YYYY-MM-DD` for date and `HH:mm` for time
