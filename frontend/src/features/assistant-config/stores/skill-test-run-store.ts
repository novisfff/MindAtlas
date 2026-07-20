/**
 * Evaluation workbench event store (Plan 09 Task 7).
 * Replay after sequence, dedupe by runId+sequence, cancel, terminal reconcile.
 * Traces are owner-qualified and never hold raw secrets/provider payloads.
 */
import { create } from 'zustand'
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

export interface SkillTestTraceEvent {
  runId: string
  sequence: number
  eventType: string
  payload: Record<string, unknown>
  createdAt?: string | null
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

  beginRun: (run: EvalRunSummary) => void
  ingestEvents: (runId: string, events: EvalEventSummary[]) => void
  reconcileRun: (run: EvalRunSummary) => void
  markCancelRequested: () => void
  markError: (message: string) => void
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

export const useSkillTestRunStore = create<SkillTestRunState>()((set, get) => ({
  status: 'idle',
  activeRunId: null,
  lastSequence: 0,
  run: null,
  events: [],
  assertions: [],
  metrics: {},
  errorMessage: null,
  seenKeys: {},

  beginRun: (run) =>
    set({
      status: mapStatus(run.status),
      activeRunId: run.id,
      lastSequence: 0,
      run,
      events: [],
      assertions: [],
      metrics: {},
      errorMessage: null,
      seenKeys: {},
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
      events: nextEvents.length > TRACE_LIMIT ? nextEvents.slice(-TRACE_LIMIT) : nextEvents,
      lastSequence,
      assertions,
      metrics,
      seenKeys: seen,
    })
  },

  reconcileRun: (run) =>
    set((state) => ({
      run,
      status: mapStatus(run.status),
      activeRunId: run.id,
      lastSequence: Math.max(state.lastSequence, run.lastEventSeq || 0),
      errorMessage: run.failureCode || state.errorMessage,
    })),

  markCancelRequested: () =>
    set((state) => ({
      status: state.status === 'completed' || state.status === 'failed' ? state.status : 'cancelling',
    })),

  markError: (message) => set({ status: 'error', errorMessage: message }),

  reset: () =>
    set({
      status: 'idle',
      activeRunId: null,
      lastSequence: 0,
      run: null,
      events: [],
      assertions: [],
      metrics: {},
      errorMessage: null,
      seenKeys: {},
    }),
}))
