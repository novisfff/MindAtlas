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

export interface AgentToolTrace {
  id: string
  name: string
  status: 'running' | 'completed' | 'error'
  args?: Record<string, unknown>
  result?: string
  startedAt?: string
  endedAt?: string
  durationMs?: number | null
  agentRound?: number
  toolCallIndex?: number
  toolKind?: 'tool' | 'knowledge'
}

export interface AgentTestMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status?: 'running' | 'completed' | 'error' | 'cancelled'
  toolCalls?: AgentToolTrace[]
}

interface AgentTestRunState {
  status: AgentTestRunStatus
  input: string
  submittedInput: string
  streamOutput: boolean
  activeRunId: string | null
  activeRunStartedAt: string | null
  result: AgentTestRunResult
  traceEvents: AgentTestRunEvent[]
  analysisMap: Record<string, AgentAnalysisTrace>
  toolCalls: AgentToolTrace[]
  messages: AgentTestMessage[]
  activeAssistantMessageId: string | null
  abortController: AbortController | null

  setInput: (input: string) => void
  setStreamOutput: (streamOutput: boolean) => void
  beginRun: (abortController: AbortController, submittedInput: string) => void
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

function upsertToolCall(toolCalls: AgentToolTrace[], nextToolCall: AgentToolTrace): AgentToolTrace[] {
  const next = toolCalls.filter((item) => item.id !== nextToolCall.id)
  return [...next, nextToolCall]
}

function updateToolCall(
  toolCalls: AgentToolTrace[],
  toolCallId: string,
  updates: Partial<AgentToolTrace>,
): AgentToolTrace[] {
  const existing = toolCalls.find((item) => item.id === toolCallId)
  if (!existing) {
    return toolCalls
  }
  return toolCalls.map((item) => (
    item.id === toolCallId ? { ...item, ...updates } : item
  ))
}

function updateMessage(
  messages: AgentTestMessage[],
  messageId: string | null,
  updater: (message: AgentTestMessage) => AgentTestMessage,
): AgentTestMessage[] {
  if (!messageId) return messages
  return messages.map((message) => (
    message.id === messageId ? updater(message) : message
  ))
}

export const useAgentTestRunStore = create<AgentTestRunState>()((set, get) => ({
  status: 'idle',
  input: '',
  submittedInput: '',
  streamOutput: true,
  activeRunId: null,
  activeRunStartedAt: null,
  result: EMPTY_RESULT,
  traceEvents: [],
  analysisMap: {},
  toolCalls: [],
  messages: [],
  activeAssistantMessageId: null,
  abortController: null,

  setInput: (input) => set({ input }),
  setStreamOutput: (streamOutput) => set({ streamOutput }),

  beginRun: (abortController, submittedInput) => {
    const currentAbort = get().abortController
    currentAbort?.abort()
    const turnId = Date.now().toString(36)
    const assistantMessageId = `assistant_${turnId}`
    set({
      status: 'running',
      input: '',
      submittedInput,
      activeRunId: null,
      activeRunStartedAt: null,
      result: EMPTY_RESULT,
      traceEvents: [],
      analysisMap: {},
      toolCalls: [],
      messages: [
        ...get().messages,
        { id: `user_${turnId}`, role: 'user', content: submittedInput, status: 'completed' },
        { id: assistantMessageId, role: 'assistant', content: '', status: 'running', toolCalls: [] },
      ],
      activeAssistantMessageId: assistantMessageId,
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
      activeAssistantMessageId: null,
      messages: updateMessage(
        state.messages,
        state.activeAssistantMessageId,
        (message) => ({ ...message, status: 'cancelled' }),
      ),
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
          toolCalls: [],
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
          messages: updateMessage(
            state.messages,
            state.activeAssistantMessageId,
            (message) => ({
              ...message,
              content: `${message.content}${deltaText}`,
            }),
          ),
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

      if (event.event === 'tool_call_start') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        return {
          ...state,
          traceEvents,
          toolCalls: upsertToolCall(state.toolCalls, {
            id: event.data.toolCallId,
            name: event.data.name,
            status: 'running',
            args: event.data.args,
            startedAt: event.data.startedAt ?? event.data.ts,
            agentRound: event.data.agentRound,
            toolCallIndex: event.data.toolCallIndex,
            toolKind: event.data.toolKind,
          }),
          messages: updateMessage(
            state.messages,
            state.activeAssistantMessageId,
            (message) => ({
              ...message,
              toolCalls: upsertToolCall(message.toolCalls ?? [], {
                id: event.data.toolCallId,
                name: event.data.name,
                status: 'running',
                args: event.data.args,
                startedAt: event.data.startedAt ?? event.data.ts,
                agentRound: event.data.agentRound,
                toolCallIndex: event.data.toolCallIndex,
                toolKind: event.data.toolKind,
              }),
            }),
          ),
        }
      }

      if (event.event === 'tool_call_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const existing = state.toolCalls.find((item) => item.id === event.data.toolCallId)
        if (!existing) {
          return {
            ...state,
            traceEvents,
            toolCalls: upsertToolCall(state.toolCalls, {
              id: event.data.toolCallId,
              name: 'unknown_tool',
              status: event.data.status === 'completed' ? 'completed' : 'error',
              result: event.data.result,
              startedAt: event.data.startedAt ?? undefined,
              endedAt: event.data.endedAt ?? event.data.ts,
              durationMs: event.data.durationMs ?? null,
              agentRound: event.data.agentRound,
              toolCallIndex: event.data.toolCallIndex,
              toolKind: event.data.toolKind,
            }),
          }
        }
        return {
          ...state,
          traceEvents,
          toolCalls: updateToolCall(state.toolCalls, event.data.toolCallId, {
            status: event.data.status === 'completed' ? 'completed' : 'error',
            result: event.data.result,
            startedAt: event.data.startedAt ?? existing.startedAt,
            endedAt: event.data.endedAt ?? event.data.ts,
            durationMs: event.data.durationMs ?? existing.durationMs ?? null,
            agentRound: event.data.agentRound ?? existing.agentRound,
            toolCallIndex: event.data.toolCallIndex ?? existing.toolCallIndex,
            toolKind: event.data.toolKind ?? existing.toolKind,
          }),
          messages: updateMessage(
            state.messages,
            state.activeAssistantMessageId,
            (message) => ({
              ...message,
              toolCalls: updateToolCall(message.toolCalls ?? [], event.data.toolCallId, {
                status: event.data.status === 'completed' ? 'completed' : 'error',
                result: event.data.result,
                startedAt: event.data.startedAt ?? existing.startedAt,
                endedAt: event.data.endedAt ?? event.data.ts,
                durationMs: event.data.durationMs ?? existing.durationMs ?? null,
                agentRound: event.data.agentRound ?? existing.agentRound,
                toolCallIndex: event.data.toolCallIndex ?? existing.toolCallIndex,
                toolKind: event.data.toolKind ?? existing.toolKind,
              }),
            }),
          ),
        }
      }

      if (event.event === 'run_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const nextStatus = event.data.status === 'error' ? 'error' : event.data.status
        const nextFinalText = event.data.finalText || state.result.finalText
        return {
          ...state,
          traceEvents,
          status: nextStatus,
          result: {
            ...state.result,
            finalText: nextFinalText,
            durationMs: event.data.durationMs ?? state.result.durationMs,
          },
          messages: updateMessage(
            state.messages,
            state.activeAssistantMessageId,
            (message) => ({
              ...message,
              content: event.data.finalText || message.content,
              status: nextStatus,
            }),
          ),
          abortController: null,
          activeRunId: null,
          activeRunStartedAt: null,
          activeAssistantMessageId: null,
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
      activeAssistantMessageId: null,
      result: {
        ...state.result,
        errorMessage: message,
      },
      messages: updateMessage(
        state.messages,
        state.activeAssistantMessageId,
        (item) => ({
          ...item,
          content: item.content || message,
          status: 'error',
        }),
      ),
    }))
  },

  clearResult: () => {
    const controller = get().abortController
    controller?.abort()
    set({
      status: 'idle',
      submittedInput: '',
      activeRunId: null,
      activeRunStartedAt: null,
      result: EMPTY_RESULT,
      traceEvents: [],
      analysisMap: {},
      toolCalls: [],
      messages: [],
      activeAssistantMessageId: null,
      abortController: null,
    })
  },
}))
