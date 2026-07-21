/**
 * Evaluation workbench event store (Plan 09 Task 9).
 * Replay after sequence, dedupe by runId+sequence, cancel, terminal reconcile.
 * Heartbeats are tracked separately and never enter the trace.
 * Traces are owner-qualified and never hold raw secrets/provider payloads.
 */
import { create, type StoreApi, type UseBoundStore } from 'zustand'
import type { EvalEventSummary, EvalRunSummary } from '../api/skill-evaluations'

const TRACE_LIMIT = 400

export type SkillTestRunStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'error'

export type SkillTestTransportMode = 'idle' | 'sse' | 'polling' | 'closed'

export interface SkillTestTraceEvent {
  runId: string
  sequence: number
  eventType: string
  payload: Record<string, unknown>
  createdAt?: string | null
}

export interface SkillTestHeartbeat {
  afterSequence: number
  status?: string
  ts?: number
  terminal?: boolean
}

interface SkillTestRunState {
  status: SkillTestRunStatus
  activeRunId: string | null
  lastSequence: number
  run: EvalRunSummary | null
  events: SkillTestTraceEvent[]
  assertions: Array<Record<string, unknown>>
  metrics: Record<string, unknown>
  errorMessage: string | null
  seenKeys: Record<string, true>
  lastHeartbeat: SkillTestHeartbeat | null
  transportMode: SkillTestTransportMode

  beginRun: (run: EvalRunSummary) => void
  ingestEvents: (runId: string, events: EvalEventSummary[]) => void
  ingestHeartbeat: (runId: string, payload: Record<string, unknown>) => void
  setTransportMode: (mode: SkillTestTransportMode) => void
  setMetrics: (metrics: Record<string, unknown>) => void
  mergeMetrics: (metrics: Record<string, unknown>) => void
  reconcileRun: (run: EvalRunSummary) => void
  markCancelRequested: () => void
  markError: (message: string) => void
  /** Non-terminal transport notice — does not unlock Start / freeze Cancel. */
  markTransportNotice: (message: string | null) => void
  /** Clear run/trace after subject pointer changes (e.g. draft → published). */
  clearForSubjectChange: () => void
  reset: () => void
}

function sanitizePayload(payload: Record<string, unknown>): Record<string, unknown> {
  const blocked = new Set([
    'authorization',
    'cookie',
    'set-cookie',
    'apiKey',
    'api_key',
    'secret',
    'password',
    'token',
    'rawProviderPayload',
    'providerPayload',
    'credentials',
    'rawBytes',
    'resourceBytes',
  ])
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(payload || {})) {
    if (blocked.has(k) || blocked.has(k.toLowerCase())) continue
    if (typeof v === 'string' && v.length > 4000) {
      out[k] = `${v.slice(0, 4000)}…`
    } else {
      out[k] = v
    }
  }
  return out
}

function mapStatus(status: string): SkillTestRunStatus {
  switch (status) {
    case 'queued':
    case 'running':
    case 'cancelling':
    case 'completed':
    case 'failed':
    case 'cancelled':
      return status
    default:
      return 'error'
  }
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

function createInitialState(): Omit<
  SkillTestRunState,
  | 'beginRun'
  | 'ingestEvents'
  | 'ingestHeartbeat'
  | 'setTransportMode'
  | 'setMetrics'
  | 'mergeMetrics'
  | 'reconcileRun'
  | 'markCancelRequested'
  | 'markError'
  | 'markTransportNotice'
  | 'clearForSubjectChange'
  | 'reset'
> {
  return {
    status: 'idle',
    activeRunId: null,
    lastSequence: 0,
    run: null,
    events: [],
    assertions: [],
    metrics: {},
    errorMessage: null,
    seenKeys: {},
    lastHeartbeat: null,
    transportMode: 'idle',
  }
}

function createStoreApi() {
  return create<SkillTestRunState>()((set, get) => ({
    ...createInitialState(),

    beginRun: (run) =>
      set({
        ...createInitialState(),
        status: mapStatus(run.status),
        activeRunId: run.id,
        run,
      }),

    ingestEvents: (runId, events) => {
      const state = get()
      if (state.activeRunId && state.activeRunId !== runId) return
      const seen = { ...state.seenKeys }
      const nextEvents = [...state.events]
      let lastSequence = state.lastSequence
      let assertions = [...state.assertions]
      let metrics = { ...state.metrics }

      for (const event of events) {
        const key = `${runId}:${event.sequence}`
        if (seen[key]) continue
        seen[key] = true
        const safePayload = sanitizePayload(event.payload || {})
        nextEvents.push({
          runId,
          sequence: event.sequence,
          eventType: event.eventType,
          payload: safePayload,
          createdAt: event.createdAt,
        })
        lastSequence = Math.max(lastSequence, event.sequence)
        if (event.eventType === 'assertion' || event.eventType === 'case_result') {
          assertions.push(safePayload)
        }
        if (event.eventType === 'metrics' && safePayload && typeof safePayload === 'object') {
          metrics = { ...metrics, ...safePayload }
        }
      }

      set({
        activeRunId: state.activeRunId ?? runId,
        events: nextEvents.length > TRACE_LIMIT ? nextEvents.slice(-TRACE_LIMIT) : nextEvents,
        lastSequence,
        assertions,
        metrics,
        seenKeys: seen,
      })
    },

    ingestHeartbeat: (runId, payload) => {
      const state = get()
      if (state.activeRunId && state.activeRunId !== runId) return
      const afterSequence =
        typeof payload.afterSequence === 'number'
          ? payload.afterSequence
          : Number(payload.afterSequence ?? state.lastSequence)
      const heartbeatStatus =
        typeof payload.status === 'string' ? payload.status : undefined
      const next: Partial<SkillTestRunState> = {
        lastHeartbeat: {
          afterSequence: Number.isFinite(afterSequence) ? afterSequence : state.lastSequence,
          status: heartbeatStatus,
          ts: typeof payload.ts === 'number' ? payload.ts : undefined,
          terminal: payload.terminal === true || Boolean(heartbeatStatus && TERMINAL_STATUSES.has(heartbeatStatus)),
        },
      }
      // Heartbeat may carry a fresher non-conflicting status while transport is live.
      // Never demote a terminal status, and never invent an 'error' from unknown values.
      if (
        heartbeatStatus &&
        !TERMINAL_STATUSES.has(state.status) &&
        (heartbeatStatus === 'queued' ||
          heartbeatStatus === 'running' ||
          heartbeatStatus === 'cancelling' ||
          TERMINAL_STATUSES.has(heartbeatStatus))
      ) {
        next.status = mapStatus(heartbeatStatus)
      }
      set(next)
    },

    setTransportMode: (mode) => set({ transportMode: mode }),

    setMetrics: (metrics) => set({ metrics: { ...metrics } }),

    mergeMetrics: (metrics) =>
      set((state) => ({
        metrics: { ...state.metrics, ...metrics },
      })),

    reconcileRun: (run) =>
      set((state) => {
        const nextStatus = mapStatus(run.status)
        const terminal = TERMINAL_STATUSES.has(nextStatus)
        return {
          run,
          status: nextStatus,
          activeRunId: run.id,
          lastSequence: Math.max(state.lastSequence, run.lastEventSeq || 0),
          // Prefer server failureCode; drop transport notices once the run is terminal.
          errorMessage: run.failureCode || (terminal ? null : state.errorMessage),
        }
      }),

    markCancelRequested: () =>
      set((state) => ({
        status:
          state.status === 'completed' || state.status === 'failed' || state.status === 'cancelled'
            ? state.status
            : 'cancelling',
      })),

    markError: (message) => set({ status: 'error', errorMessage: message }),

    markTransportNotice: (message) => set({ errorMessage: message }),

    clearForSubjectChange: () => set(createInitialState()),

    reset: () => set(createInitialState()),
  }))
}

export type SkillTestRunStore = UseBoundStore<StoreApi<SkillTestRunState>>

/** Factory for isolated store instances (tests / multi-panel). */
export function createSkillTestRunStore(): SkillTestRunStore {
  return createStoreApi()
}

export const useSkillTestRunStore = createStoreApi()
