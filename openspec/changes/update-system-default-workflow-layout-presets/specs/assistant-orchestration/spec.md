## MODIFIED Requirements

### Requirement: Official system workflows default to horizontal coordinates
System default workflow definitions SHALL use optimized baseline coordinates for readability: main chain left-to-right with a stable centerline, and parallel branches expanded vertically.

#### Scenario: First sync uses optimized preset coordinates
- **WHEN** system skills are synced and a system workflow target is created for the first time
- **THEN** the created workflow SHALL use optimized preset node coordinates from system definitions
- **AND** the graph SHALL present a horizontal main chain with readable branch offsets

#### Scenario: Reset restores optimized preset coordinates
- **WHEN** a system workflow skill is reset to default
- **THEN** workflow node positions SHALL be rebuilt from the optimized system preset
- **AND** the resulting draft/published graph SHALL match the default coordinate baseline

#### Scenario: Normal sync does not overwrite existing persisted layout
- **WHEN** a system workflow target already exists and sync runs normally
- **THEN** sync SHALL NOT rewrite existing node coordinates
- **AND** user-adjusted layout SHALL remain unchanged
