import { create } from 'zustand'
import type { WorkflowRunEvent } from '../api/workflow'

export type WorkflowTestRunStatus = 'idle' | 'running' | 'completed' | 'error' | 'cancelled'
const TRACE_EVENT_LIMIT = 300
const DELTA_NODE_SUMMARY_LIMIT = 50
const NODE_DELTA_PREVIEW_LIMIT = 200

export interface WorkflowTestNodeTrace {
  nodeId: string
  nodeType?: string
  status: 'idle' | 'running' | 'success' | 'error'
  startedAt?: number
  endedAt?: number
  durationMs?: number
  outputPreview?: string
}

export interface WorkflowTestRunResult {
  finalText: string
  finalJson: Record<string, unknown> | Array<unknown> | null
  errorMessage: string | null
  durationMs: number | null
}

export interface WorkflowSessionRunSummary {
  runId: string
  status: WorkflowTestRunStatus
  startedAt: string
  durationMs: number | null
  finalText: string
}

export interface WorkflowDeltaSummaryCounter {
  chunks: number
  chars: number
}

export interface WorkflowNodeDeltaSummary extends WorkflowDeltaSummaryCounter {
  preview?: string
}

export interface WorkflowDeltaSummary {
  content: WorkflowDeltaSummaryCounter
  nodes: Record<string, WorkflowNodeDeltaSummary>
}

export interface WorkflowNodeSnapshot {
  nodeId: string
  nodeType: string
  status: 'ok' | 'error'
  input: unknown
  output: unknown | null
  errorMessage: string | null
  hardTruncated?: boolean
  ts: string
}

interface WorkflowTestRunState {
  panelOpen: boolean
  status: WorkflowTestRunStatus
  input: string
  structuredInput: Record<string, unknown>
  streamOutput: boolean
  activeRunId: string | null
  activeRunStartedAt: string | null
  result: WorkflowTestRunResult
  deltaSummary: WorkflowDeltaSummary
  traceEvents: WorkflowRunEvent[]
  nodeSnapshots: Record<string, WorkflowNodeSnapshot>
  nodeTraceMap: Record<string, WorkflowTestNodeTrace>
  sessionRuns: WorkflowSessionRunSummary[]
  abortController: AbortController | null

  setPanelOpen: (open: boolean) => void
  setInput: (input: string) => void
  setStructuredInput: (value: Record<string, unknown>) => void
  setStructuredInputField: (field: string, value: unknown) => void
  setStreamOutput: (streamOutput: boolean) => void
  beginRun: (abortController: AbortController) => void
  cancelRun: () => void
  ingestEvent: (event: WorkflowRunEvent) => void
  markRunError: (message: string) => void
  reset: () => void
  clearSessionRuns: () => void
}

const EMPTY_RESULT: WorkflowTestRunResult = {
  finalText: '',
  finalJson: null,
  errorMessage: null,
  durationMs: null,
}

const EMPTY_DELTA_SUMMARY: WorkflowDeltaSummary = {
  content: { chunks: 0, chars: 0 },
  nodes: {},
}

function capTraceEvents(events: WorkflowRunEvent[]): WorkflowRunEvent[] {
  return events.length > TRACE_EVENT_LIMIT ? events.slice(-TRACE_EVENT_LIMIT) : events
}

function upsertNodeDeltaSummary(
  currentNodes: Record<string, WorkflowNodeDeltaSummary>,
  nodeId: string,
  deltaText: string,
): Record<string, WorkflowNodeDeltaSummary> {
  const existing = currentNodes[nodeId]
  const nextNodes = { ...currentNodes }
  if (existing) {
    delete nextNodes[nodeId]
  }
  const nextPreview = `${existing?.preview ?? ''}${deltaText}`.slice(-NODE_DELTA_PREVIEW_LIMIT)
  nextNodes[nodeId] = {
    chunks: (existing?.chunks ?? 0) + 1,
    chars: (existing?.chars ?? 0) + deltaText.length,
    preview: nextPreview || undefined,
  }

  const keys = Object.keys(nextNodes)
  while (keys.length > DELTA_NODE_SUMMARY_LIMIT) {
    const oldest = keys.shift()
    if (!oldest) break
    delete nextNodes[oldest]
  }
  return nextNodes
}

export const useWorkflowTestRunStore = create<WorkflowTestRunState>()((set, get) => ({
  panelOpen: false,
  status: 'idle',
  input: '',
  structuredInput: {},
  streamOutput: true,
  activeRunId: null,
  activeRunStartedAt: null,
  result: EMPTY_RESULT,
  deltaSummary: EMPTY_DELTA_SUMMARY,
  traceEvents: [],
  nodeSnapshots: {},
  nodeTraceMap: {},
  sessionRuns: [],
  abortController: null,

  setPanelOpen: (panelOpen) => set({ panelOpen }),
  setInput: (input) => set({ input }),
  setStructuredInput: (value) => set({ structuredInput: value }),
  setStructuredInputField: (field, value) => set((state) => ({
    structuredInput: {
      ...state.structuredInput,
      [field]: value,
    },
  })),
  setStreamOutput: (streamOutput) => set({ streamOutput }),

  beginRun: (abortController) => {
    const currentAbort = get().abortController
    currentAbort?.abort()
    set({
      status: 'running',
      activeRunId: null,
      activeRunStartedAt: null,
      result: EMPTY_RESULT,
      deltaSummary: EMPTY_DELTA_SUMMARY,
      traceEvents: [],
      nodeSnapshots: {},
      nodeTraceMap: {},
      abortController,
    })
  },

  cancelRun: () => {
    const controller = get().abortController
    controller?.abort()
    set((state) => ({
      abortController: null,
      activeRunId: null,
      activeRunStartedAt: null,
      status: state.status === 'running' ? 'cancelled' : state.status,
    }))
  },

  ingestEvent: (event) => {
    set((state) => {
      if (event.event === 'run_start') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        return {
          ...state,
          traceEvents,
          activeRunId: event.data.runId,
          activeRunStartedAt: event.data.startedAt,
          status: 'running',
          result: EMPTY_RESULT,
          deltaSummary: EMPTY_DELTA_SUMMARY,
          nodeSnapshots: {},
          nodeTraceMap: {},
        }
      }

      const activeRunId = state.activeRunId
      if (!activeRunId) {
        return state
      }
      const eventRunId = (event.data as { runId?: string }).runId
      if (activeRunId && eventRunId && eventRunId !== activeRunId) {
        return state
      }

      const nodeTraceMap = { ...state.nodeTraceMap }

      if (event.event === 'node_start') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const startedAt = Date.parse(event.data.ts) || Date.now()
        nodeTraceMap[event.data.nodeId] = {
          nodeId: event.data.nodeId,
          nodeType: event.data.nodeType,
          status: 'running',
          startedAt,
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
        }
      }

      if (event.event === 'node_output_delta') {
        const deltaText = `${event.data.delta ?? ''}`
        if (!deltaText) return state
        const current = nodeTraceMap[event.data.nodeId] ?? {
          nodeId: event.data.nodeId,
          status: 'idle',
        }
        nodeTraceMap[event.data.nodeId] = {
          ...current,
          outputPreview: `${current.outputPreview ?? ''}${deltaText}`.slice(-NODE_DELTA_PREVIEW_LIMIT),
        }
        const deltaSummary: WorkflowDeltaSummary = {
          content: state.deltaSummary.content,
          nodes: upsertNodeDeltaSummary(state.deltaSummary.nodes, event.data.nodeId, deltaText),
        }
        return {
          ...state,
          deltaSummary,
          nodeTraceMap,
        }
      }

      if (event.event === 'node_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const current = nodeTraceMap[event.data.nodeId] ?? {
          nodeId: event.data.nodeId,
          status: 'idle',
        }
        const endedAt = Date.parse(event.data.ts) || Date.now()
        const startedAt = current.startedAt
        nodeTraceMap[event.data.nodeId] = {
          ...current,
          status: event.data.status === 'ok' ? 'success' : 'error',
          endedAt,
          durationMs: typeof startedAt === 'number' ? Math.max(0, endedAt - startedAt) : undefined,
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
        }
      }

      if (event.event === 'node_snapshot') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const snapshot: WorkflowNodeSnapshot = {
          nodeId: event.data.nodeId,
          nodeType: event.data.nodeType,
          status: event.data.status,
          input: event.data.input,
          output: event.data.output,
          errorMessage: event.data.errorMessage ?? null,
          hardTruncated: event.data.hardTruncated,
          ts: event.data.ts,
        }
        const current = nodeTraceMap[event.data.nodeId] ?? {
          nodeId: event.data.nodeId,
          status: 'idle',
        }
        nodeTraceMap[event.data.nodeId] = {
          ...current,
          nodeType: event.data.nodeType || current.nodeType,
          status: event.data.status === 'ok' ? 'success' : 'error',
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
          nodeSnapshots: {
            ...state.nodeSnapshots,
            [event.data.nodeId]: snapshot,
          },
        }
      }

      if (event.event === 'content_delta') {
        const deltaText = `${event.data.delta ?? ''}`
        if (!deltaText) return state
        const deltaSummary: WorkflowDeltaSummary = {
          content: {
            chunks: state.deltaSummary.content.chunks + 1,
            chars: state.deltaSummary.content.chars + deltaText.length,
          },
          nodes: state.deltaSummary.nodes,
        }
        return {
          ...state,
          deltaSummary,
          result: {
            ...state.result,
            finalText: `${state.result.finalText}${deltaText}`,
          },
        }
      }

      if (event.event === 'run_error') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        return {
          ...state,
          traceEvents,
          status: 'error',
          result: {
            ...state.result,
            errorMessage: event.data.message,
          },
        }
      }

      if (event.event === 'run_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const status = event.data.status === 'completed'
          ? 'completed'
          : event.data.status === 'cancelled'
            ? 'cancelled'
            : 'error'
        const result: WorkflowTestRunResult = {
          finalText: event.data.finalText ?? state.result.finalText,
          finalJson: event.data.finalJson,
          errorMessage: status === 'error' ? state.result.errorMessage ?? 'Run failed' : null,
          durationMs: event.data.durationMs,
        }
        const runSummary: WorkflowSessionRunSummary = {
          runId: event.data.runId,
          status,
          startedAt: state.activeRunStartedAt ?? new Date().toISOString(),
          durationMs: event.data.durationMs,
          finalText: result.finalText,
        }

        return {
          ...state,
          traceEvents,
          status,
          activeRunId: null,
          activeRunStartedAt: null,
          abortController: null,
          result,
          sessionRuns: [runSummary, ...state.sessionRuns].slice(0, 10),
        }
      }

      const traceEvents = capTraceEvents([...state.traceEvents, event])
      return {
        ...state,
        traceEvents,
      }
    })
  },

  markRunError: (message) => {
    set((state) => ({
      status: state.status === 'cancelled' ? 'cancelled' : 'error',
      activeRunId: null,
      activeRunStartedAt: null,
      abortController: null,
      result: {
        ...state.result,
        errorMessage: message,
      },
    }))
  },

  reset: () => {
    const controller = get().abortController
    controller?.abort()
    set({
      status: 'idle',
      input: '',
      structuredInput: {},
      activeRunId: null,
      activeRunStartedAt: null,
      result: EMPTY_RESULT,
      deltaSummary: EMPTY_DELTA_SUMMARY,
      traceEvents: [],
      nodeSnapshots: {},
      nodeTraceMap: {},
      abortController: null,
      // sessionRuns: [], // Optional: decide if we want to clear history too. "Reset all tabs" implies yes.
    })
  },

  clearSessionRuns: () => set({ sessionRuns: [] }),
}))
