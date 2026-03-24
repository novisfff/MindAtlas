## ADDED Requirements

### Requirement: Assistant Chat SHALL Run Independently From Client SSE Connection
Assistant chat execution SHALL continue in background once started, regardless of client stream disconnection.

#### Scenario: Refresh page during run
- **WHEN** a user starts chat execution and refreshes/leaves the page
- **THEN** backend run SHALL continue until terminal state or explicit stop
- **AND** run state SHALL remain queryable via active-run API

### Requirement: Assistant Chat SHALL Support Run Event Replay And Live Attach
Assistant chat SHALL support attaching to an existing run stream using cursor-based replay.

#### Scenario: Attach with cursor
- **WHEN** client attaches to `/runs/{runId}/stream?afterSeq=n`
- **THEN** backend SHALL replay events with `seq > n` in order
- **AND** backend SHALL continue streaming live events until run reaches terminal state

### Requirement: Assistant Chat SHALL Support Soft Cancellation
Assistant chat SHALL provide a stop operation that requests cancellation and interrupts execution at safe polling boundaries.

#### Scenario: Stop active run
- **WHEN** client calls run stop endpoint for an active run
- **THEN** run SHALL enter `cancelling` then transition to `cancelled`
- **AND** stream SHALL emit terminal `message_end` with `finishReason=cancelled`

### Requirement: Assistant Chat SHALL Enforce Single Active Run Per Conversation
A conversation SHALL have at most one active run (`queued/running/waiting_approval/cancelling`) at any time.

#### Scenario: Send while run active
- **WHEN** a new chat request arrives while conversation has an active run
- **THEN** backend SHALL reject the request with conflict status
- **AND** existing active run SHALL remain unaffected
