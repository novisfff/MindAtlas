## ADDED Requirements
### Requirement: Assistant Targets SHALL Support Multi-Level Folders
The system SHALL allow reusable workflows and agent profiles to be organized in shared folders with arbitrary nesting depth.

#### Scenario: User creates nested folders
- **WHEN** a user creates a folder under another folder
- **THEN** the child folder SHALL retain its parent relationship and appear inside that folder in the target browser

#### Scenario: User moves a target into a folder
- **WHEN** a user moves a workflow or agent into a folder
- **THEN** the target SHALL appear in that folder and no longer appear in its previous folder

#### Scenario: User moves a folder safely
- **WHEN** a user moves a folder
- **THEN** the system SHALL reject moves that would make the folder its own ancestor or descendant

### Requirement: Folder Operations SHALL Preserve Assistant Targets
Deleting an assistant target folder SHALL remove only the folder container and SHALL NOT delete workflows, agents, or nested folders.

#### Scenario: User deletes a non-empty folder
- **WHEN** a user deletes a folder containing child folders, workflows, or agents
- **THEN** direct children and targets SHALL move to the deleted folder's parent folder

### Requirement: Assistant Target Browser SHALL Render Mixed Directory Contents
The Workflows & Agents settings page SHALL browse folders, workflows, and agents together, with search, type filtering, path context, and recently active ordering.

#### Scenario: User browses a folder
- **WHEN** a user opens a folder
- **THEN** the page SHALL show that folder's direct child folders and targets together

#### Scenario: User searches targets
- **WHEN** a user enters a search query
- **THEN** matching folders, workflows, and agents SHALL be shown with their folder path context

#### Scenario: User drags an item to a folder
- **WHEN** a user drops a workflow, agent, or folder onto a valid folder target
- **THEN** the item SHALL move to that folder and the UI SHALL confirm the move
