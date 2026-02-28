import { create } from 'zustand'
import type { AgentTestRunEvent } from '../api/agents'

export type AgentTestRunStatus = 'idle' | 'running' | 'completed' | 'error' | 'cancelled'

const TRACE_EVENT_LIMIT = 300

export interface AgentTestRunResult {
  finalText: string
  errorMessage: string | null
  durationMs: number | null
}

export interface AgentAnalysisTrace {
  id: string
  content: string
  status: 'running' | 'completed'
}

interface AgentTestRunState {
  status: AgentTestRunStatus
  input: string
  streamOutput: boolean
  activeRunId: string | null
  activeRunStartedAt: string | null
  result: AgentTestRunResult
  traceEvents: AgentTestRunEvent[]
  analysisMap: Record<string, AgentAnalysisTrace>
  abortController: AbortController | null

  setInput: (input: string) => void
  setStreamOutput: (streamOutput: boolean) => void
  beginRun: (abortController: AbortController) => void
  cancelRun: () => void
  ingestEvent: (event: AgentTestRunEvent) => void
  markRunError: (message: string) => void
  clearResult: () => void
}

const EMPTY_RESULT: AgentTestRunResult = {
  finalText: '',
  errorMessage: null,
  durationMs: null,
}

function capTraceEvents(events: AgentTestRunEvent[]): AgentTestRunEvent[] {
  return events.length > TRACE_EVENT_LIMIT ? events.slice(-TRACE_EVENT_LIMIT) : events
}

function isTraceEvent(event: AgentTestRunEvent): boolean {
  return event.event !== 'content_delta' && event.event !== 'analysis_delta'
}

export const useAgentTestRunStore = create<AgentTestRunState>()((set, get) => ({
  status: 'idle',
  input: '',
  streamOutput: true,
  activeRunId: null,
  activeRunStartedAt: null,
  result: EMPTY_RESULT,
  traceEvents: [],
  analysisMap: {},
  abortController: null,

  setInput: (input) => set({ input }),
  setStreamOutput: (streamOutput) => set({ streamOutput }),

  beginRun: (abortController) => {
    const currentAbort = get().abortController
    currentAbort?.abort()
    set({
      status: 'running',
      activeRunId: null,
      activeRunStartedAt: null,
      result: EMPTY_RESULT,
      traceEvents: [],
      analysisMap: {},
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
          analysisMap: {},
        }
      }

      const activeRunId = state.activeRunId
      if (!activeRunId) {
        return state
      }
      const eventRunId = (event.data as { runId?: string }).runId
      if (eventRunId && eventRunId !== activeRunId) {
        return state
      }

      if (event.event === 'content_delta') {
        const deltaText = `${event.data.delta ?? ''}`
        if (!deltaText) return state
        return {
          ...state,
          result: {
            ...state.result,
            finalText: `${state.result.finalText}${deltaText}`,
          },
        }
      }

      if (event.event === 'analysis_delta') {
        const deltaText = `${event.data.delta ?? ''}`
        const analysisId = event.data.analysisId
        if (!analysisId || !deltaText) return state
        const existing = state.analysisMap[analysisId] ?? {
          id: analysisId,
          content: '',
          status: 'running' as const,
        }
        return {
          ...state,
          analysisMap: {
            ...state.analysisMap,
            [analysisId]: {
              ...existing,
              content: `${existing.content}${deltaText}`,
            },
          },
        }
      }

      if (event.event === 'analysis_start') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const analysisId = event.data.analysisId
        const existing = state.analysisMap[analysisId]
        return {
          ...state,
          traceEvents,
          analysisMap: {
            ...state.analysisMap,
            [analysisId]: {
              id: analysisId,
              content: existing?.content ?? '',
              status: 'running',
            },
          },
        }
      }

      if (event.event === 'analysis_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const analysisId = event.data.analysisId
        const existing = state.analysisMap[analysisId] ?? {
          id: analysisId,
          content: '',
          status: 'running' as const,
        }
        return {
          ...state,
          traceEvents,
          analysisMap: {
            ...state.analysisMap,
            [analysisId]: {
              ...existing,
              status: 'completed',
            },
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
        return {
          ...state,
          traceEvents,
          status: event.data.status === 'error' ? 'error' : event.data.status,
          result: {
            ...state.result,
            finalText: event.data.finalText ?? state.result.finalText,
            durationMs: event.data.durationMs ?? state.result.durationMs,
          },
          abortController: null,
          activeRunId: null,
          activeRunStartedAt: null,
        }
      }

      if (isTraceEvent(event)) {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        return {
          ...state,
          traceEvents,
        }
      }
      return state
    })
  },

  markRunError: (message) => {
    set((state) => ({
      ...state,
      status: 'error',
      abortController: null,
      activeRunId: null,
      activeRunStartedAt: null,
      result: {
        ...state.result,
        errorMessage: message,
      },
    }))
  },

  clearResult: () => {
    const controller = get().abortController
    controller?.abort()
    set({
      status: 'idle',
      activeRunId: null,
      activeRunStartedAt: null,
      result: EMPTY_RESULT,
      traceEvents: [],
      analysisMap: {},
      abortController: null,
    })
  },
}))
