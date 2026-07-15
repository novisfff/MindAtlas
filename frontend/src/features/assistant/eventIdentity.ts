/**
 * Plan 06 Task 7 — client-side event identity for at-least-once SSE replay.
 *
 * Server transport is ordered and at-least-once across uncertain reconnects.
 * Consumers must ignore already-applied (runId, seq) and (runId, eventKey)
 * pairs so duplicate last events from cursor uncertainty never re-apply.
 */

export type AssistantRunStatus =
  | 'queued'
  | 'running'
  | 'recovering'
  | 'waiting_approval'
  | 'waiting_input'
  | 'cancelling'
  | 'needs_reconciliation'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | string

export const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/** Public nonterminal statuses that keep the UI in waiting/recovering mode. */
export const ACTIVE_RUN_STATUSES = new Set([
  'queued',
  'running',
  'recovering',
  'waiting_approval',
  'waiting_input',
  'cancelling',
  'needs_reconciliation',
])

export function isTerminalRunStatus(status?: string | null): boolean {
  return TERMINAL_RUN_STATUSES.has(String(status || '').toLowerCase())
}

export function isActiveRunStatus(status?: string | null): boolean {
  return ACTIVE_RUN_STATUSES.has(String(status || '').toLowerCase())
}

/** True for waiting_* / recovering / needs_reconciliation — preserve, do not clear. */
export function isPreservedWaitingStatus(status?: string | null): boolean {
  const s = String(status || '').toLowerCase()
  return (
    s === 'recovering' ||
    s === 'waiting_approval' ||
    s === 'waiting_input' ||
    s === 'needs_reconciliation' ||
    s === 'cancelling'
  )
}

export interface AppliedEventIdentity {
  runId: string
  seq: number
  eventKey?: string | null
}

export interface EventDedupeState {
  runId: string | null
  /** Highest applied sequence for the active Run (0 = none). */
  lastAppliedSeq: number
  /** Applied event keys for the active Run (bounded). */
  appliedEventKeys: Set<string>
}

export function createEventDedupeState(runId: string | null = null): EventDedupeState {
  return {
    runId,
    lastAppliedSeq: 0,
    appliedEventKeys: new Set(),
  }
}

/**
 * Reset identity tracking when attaching a different Run. Same Run reconnect
 * preserves lastAppliedSeq / keys so equal/older cursors stay idempotent.
 */
export function bindEventDedupeRun(state: EventDedupeState, runId: string | null): EventDedupeState {
  const next = String(runId || '').trim() || null
  if (state.runId === next) return state
  return createEventDedupeState(next)
}

const MAX_TRACKED_KEYS = 512

/**
 * Returns true when this event should be applied; mutates state on accept.
 * Duplicate seq (already seen) or identical eventKey is rejected.
 * seq <= 0 (legacy events without seq) always apply without identity tracking.
 */
export function shouldApplyEvent(
  state: EventDedupeState,
  identity: AppliedEventIdentity,
): boolean {
  const runId = String(identity.runId || state.runId || '').trim()
  if (!runId) {
    // No run binding — apply (legacy path before message_start).
    return true
  }
  if (state.runId && state.runId !== runId) {
    // Event belongs to a different Run; do not apply to this stream.
    return false
  }
  if (!state.runId) {
    state.runId = runId
  }

  const seq = Number(identity.seq)
  const hasSeq = Number.isFinite(seq) && seq > 0
  const key = identity.eventKey ? String(identity.eventKey).trim() : ''

  if (key && state.appliedEventKeys.has(key)) {
    // Duplicate key (uncertain cursor / at-least-once).
    if (hasSeq && seq > state.lastAppliedSeq) {
      state.lastAppliedSeq = Math.floor(seq)
    }
    return false
  }

  if (hasSeq) {
    const normalized = Math.floor(seq)
    if (normalized <= state.lastAppliedSeq) {
      // Older or equal sequence already applied.
      if (key) {
        // Still record key so later key-only duplicates are ignored.
        state.appliedEventKeys.add(key)
        trimKeys(state)
      }
      return false
    }
    state.lastAppliedSeq = normalized
  }

  if (key) {
    state.appliedEventKeys.add(key)
    trimKeys(state)
  }
  return true
}

function trimKeys(state: EventDedupeState): void {
  if (state.appliedEventKeys.size <= MAX_TRACKED_KEYS) return
  // Drop arbitrary older keys; seq gate still protects monotonic replay.
  const excess = state.appliedEventKeys.size - MAX_TRACKED_KEYS
  let dropped = 0
  for (const k of state.appliedEventKeys) {
    state.appliedEventKeys.delete(k)
    dropped += 1
    if (dropped >= excess) break
  }
}

/** Extract identity fields from an SSE data payload. */
export function identityFromPayload(
  data: Record<string, unknown> | null | undefined,
  fallbackRunId?: string | null,
): AppliedEventIdentity {
  const payload = data && typeof data === 'object' ? data : {}
  const runId = String(payload.runId || fallbackRunId || '').trim()
  const seq = Number(payload.seq)
  const eventKeyRaw = payload.eventKey ?? payload.event_key
  const eventKey =
    eventKeyRaw === undefined || eventKeyRaw === null ? null : String(eventKeyRaw)
  return {
    runId,
    seq: Number.isFinite(seq) ? seq : 0,
    eventKey,
  }
}
