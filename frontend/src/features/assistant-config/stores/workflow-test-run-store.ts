import { create } from 'zustand'
import type {
  WorkflowHumanApproval,
  WorkflowTestMemoryScope,
  WorkflowRunEvent,
  WorkflowTestSessionMemory,
} from '../api/workflow'

export type WorkflowTestRunStatus = 'idle' | 'running' | 'completed' | 'error' | 'cancelled'
const TRACE_EVENT_LIMIT = 300
const DELTA_NODE_SUMMARY_LIMIT = 50
const NODE_DELTA_PREVIEW_LIMIT = 200

export interface WorkflowTestToolTrace {
  id: string
  name: string
  status: 'running' | 'completed' | 'error'
  args?: Record<string, unknown>
  result?: string
  startedAt?: number
  endedAt?: number
  durationMs?: number
  agentRound?: number
  toolCallIndex?: number
  toolKind?: 'tool' | 'knowledge'
}

export interface WorkflowTestNodeTrace {
  executionKey: string
  nodeId: string
  nodeExecutionId?: string
  nodeType?: string
  status: 'idle' | 'running' | 'success' | 'error'
  startedAt?: number
  endedAt?: number
  durationMs?: number
  outputPreview?: string
  toolCalls?: WorkflowTestToolTrace[]
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
  executionKey: string
  nodeId: string
  nodeExecutionId?: string
  nodeType: string
  status: 'ok' | 'error'
  input: unknown
  output: unknown | null
  errorMessage: string | null
  hardTruncated?: boolean
  ts: string
}

export interface WorkflowTestMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: 'running' | 'completed' | 'error' | 'cancelled'
  runId?: string
  finalJson?: Record<string, unknown> | Array<unknown> | null
  errorMessage?: string | null
}

export interface WorkflowTestTurnRecord {
  runId: string
  status: WorkflowTestRunStatus
  startedAt: string
  result: WorkflowTestRunResult
  deltaSummary: WorkflowDeltaSummary
  traceEvents: WorkflowRunEvent[]
  pendingApprovals: WorkflowHumanApproval[]
  nodeSnapshots: Record<string, WorkflowNodeSnapshot>
  nodeTraceMap: Record<string, WorkflowTestNodeTrace>
  sessionMemory: Required<WorkflowTestSessionMemory>
}

interface WorkflowTestRunState {
  status: WorkflowTestRunStatus
  input: string
  structuredInput: Record<string, unknown>
  streamOutput: boolean
  sessionId: string
  sessionMemory: Required<WorkflowTestSessionMemory>
  messages: WorkflowTestMessage[]
  turnsByRunId: Record<string, WorkflowTestTurnRecord>
  selectedRunId: string | null
  activeRunId: string | null
  activeRunStartedAt: string | null
  activeAssistantMessageId: string | null
  result: WorkflowTestRunResult
  deltaSummary: WorkflowDeltaSummary
  traceEvents: WorkflowRunEvent[]
  pendingApprovals: WorkflowHumanApproval[]
  nodeSnapshots: Record<string, WorkflowNodeSnapshot>
  nodeTraceMap: Record<string, WorkflowTestNodeTrace>
  nodeExecutionMap: Record<string, string>
  toolCallNodeMap: Record<string, string>
  sessionRuns: WorkflowSessionRunSummary[]
  abortController: AbortController | null

  setInput: (input: string) => void
  setStructuredInput: (value: Record<string, unknown>) => void
  setStructuredInputField: (field: string, value: unknown) => void
  setStreamOutput: (streamOutput: boolean) => void
  beginRun: (
    abortController: AbortController,
    options?: { mode?: 'text' | 'structured'; submittedInput?: string },
  ) => void
  cancelRun: () => void
  ingestEvent: (event: WorkflowRunEvent) => void
  markRunError: (message: string) => void
  selectRun: (runId: string) => void
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

const EMPTY_SESSION_MEMORY: Required<WorkflowTestSessionMemory> = {
  conversationSummary: '',
  skillFacts: [],
  workflowCallScopes: {},
}

function generateSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16)
    const value = char === 'x' ? random : ((random & 0x3) | 0x8)
    return value.toString(16)
  })
}

function capTraceEvents(events: WorkflowRunEvent[]): WorkflowRunEvent[] {
  return events.length > TRACE_EVENT_LIMIT ? events.slice(-TRACE_EVENT_LIMIT) : events
}

function buildExecutionKey(nodeId: string, nodeExecutionId?: string | null): string {
  const scopedExecutionId = String(nodeExecutionId || '').trim()
  return scopedExecutionId || nodeId
}

function parseEventTs(value?: string | null): number | undefined {
  if (!value) return undefined
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

function normalizeMemoryScope(
  value?: WorkflowTestMemoryScope | null,
): Required<WorkflowTestMemoryScope> {
  const conversationSummary = `${value?.conversationSummary ?? ''}`.trim()
  const rawSkillFacts = Array.isArray(value?.skillFacts) ? value.skillFacts : []
  const skillFacts = rawSkillFacts
    .map((item) => `${item ?? ''}`.trim())
    .filter((item) => item.length > 0)
  return {
    conversationSummary,
    skillFacts,
  }
}

function normalizeSessionMemory(
  value?: WorkflowTestSessionMemory | null,
): Required<WorkflowTestSessionMemory> {
  const normalizedScope = normalizeMemoryScope(value)
  const rawScopes = value?.workflowCallScopes
  const workflowCallScopes: Record<string, Required<WorkflowTestMemoryScope>> = {}

  Object.entries(rawScopes ?? {}).forEach(([rawKey, rawValue]) => {
    const scopeKey = `${rawKey ?? ''}`.trim()
    if (!scopeKey) return
    const normalized = normalizeMemoryScope(rawValue)
    if (!normalized.conversationSummary && normalized.skillFacts.length === 0) return
    workflowCallScopes[scopeKey] = normalized
  })

  return {
    ...normalizedScope,
    workflowCallScopes,
  }
}

function upsertNodeDeltaSummary(
  currentNodes: Record<string, WorkflowNodeDeltaSummary>,
  executionKey: string,
  deltaText: string,
): Record<string, WorkflowNodeDeltaSummary> {
  const existing = currentNodes[executionKey]
  const nextNodes = { ...currentNodes }
  if (existing) {
    delete nextNodes[executionKey]
  }
  const nextPreview = `${existing?.preview ?? ''}${deltaText}`.slice(-NODE_DELTA_PREVIEW_LIMIT)
  nextNodes[executionKey] = {
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

function upsertPendingApproval(
  approvals: WorkflowHumanApproval[],
  approval: WorkflowHumanApproval,
): WorkflowHumanApproval[] {
  const next = approvals.filter((item) => item.id !== approval.id)
  return [...next, approval]
}

function removePendingApproval(
  approvals: WorkflowHumanApproval[],
  approvalId: string,
): WorkflowHumanApproval[] {
  return approvals.filter((item) => item.id !== approvalId)
}

function updateTurnPendingApprovals(
  turnsByRunId: Record<string, WorkflowTestTurnRecord>,
  runId: string,
  updater: (approvals: WorkflowHumanApproval[]) => WorkflowHumanApproval[],
): Record<string, WorkflowTestTurnRecord> {
  const current = turnsByRunId[runId]
  if (!current) return turnsByRunId
  return {
    ...turnsByRunId,
    [runId]: {
      ...current,
      pendingApprovals: updater(current.pendingApprovals),
    },
  }
}

function upsertToolCall(
  toolCalls: WorkflowTestToolTrace[] | undefined,
  nextToolCall: WorkflowTestToolTrace,
): WorkflowTestToolTrace[] {
  const existing = toolCalls ?? []
  const next = existing.filter((item) => item.id !== nextToolCall.id)
  return [...next, nextToolCall]
}

function updateToolCall(
  toolCalls: WorkflowTestToolTrace[] | undefined,
  toolCallId: string,
  updates: Partial<WorkflowTestToolTrace>,
): WorkflowTestToolTrace[] {
  const existing = toolCalls ?? []
  if (!existing.some((item) => item.id === toolCallId)) {
    return existing
  }
  return existing.map((item) => (
    item.id === toolCallId ? { ...item, ...updates } : item
  ))
}

function updateMessage(
  messages: WorkflowTestMessage[],
  messageId: string | null,
  updater: (message: WorkflowTestMessage) => WorkflowTestMessage,
): WorkflowTestMessage[] {
  if (!messageId) return messages
  return messages.map((message) => (
    message.id === messageId ? updater(message) : message
  ))
}

function buildRunSummary(runId: string, turn: WorkflowTestTurnRecord): WorkflowSessionRunSummary {
  return {
    runId,
    status: turn.status,
    startedAt: turn.startedAt,
    durationMs: turn.result.durationMs,
    finalText: turn.result.finalText,
  }
}

function upsertRunSummary(
  sessionRuns: WorkflowSessionRunSummary[],
  nextRun: WorkflowSessionRunSummary,
): WorkflowSessionRunSummary[] {
  return [nextRun, ...sessionRuns.filter((item) => item.runId !== nextRun.runId)].slice(0, 10)
}

function buildTurnRecord(
  state: Pick<WorkflowTestRunState, 'activeRunStartedAt' | 'result' | 'deltaSummary' | 'traceEvents' | 'pendingApprovals' | 'nodeSnapshots' | 'nodeTraceMap'>,
  runId: string,
  status: WorkflowTestRunStatus,
  sessionMemory: Required<WorkflowTestSessionMemory>,
): WorkflowTestTurnRecord {
  return {
    runId,
    status,
    startedAt: state.activeRunStartedAt ?? new Date().toISOString(),
    result: state.result,
    deltaSummary: state.deltaSummary,
    traceEvents: state.traceEvents,
    pendingApprovals: state.pendingApprovals,
    nodeSnapshots: state.nodeSnapshots,
    nodeTraceMap: state.nodeTraceMap,
    sessionMemory,
  }
}

function applyTurnView(turn: WorkflowTestTurnRecord) {
  return {
    status: turn.status,
    result: turn.result,
    deltaSummary: turn.deltaSummary,
    traceEvents: turn.traceEvents,
    pendingApprovals: turn.pendingApprovals,
    nodeSnapshots: turn.nodeSnapshots,
    nodeTraceMap: turn.nodeTraceMap,
    nodeExecutionMap: {},
    toolCallNodeMap: {},
    selectedRunId: turn.runId,
  }
}

function formatAssistantMessageContent(message: WorkflowTestMessage): string {
  if (message.finalJson !== undefined && message.finalJson !== null) {
    return JSON.stringify(message.finalJson, null, 2)
  }
  if (message.content) return message.content
  if (message.errorMessage) return message.errorMessage
  return ''
}

export const useWorkflowTestRunStore = create<WorkflowTestRunState>()((set, get) => ({
  status: 'idle',
  input: '',
  structuredInput: {},
  streamOutput: true,
  sessionId: generateSessionId(),
  sessionMemory: EMPTY_SESSION_MEMORY,
  messages: [],
  turnsByRunId: {},
  selectedRunId: null,
  activeRunId: null,
  activeRunStartedAt: null,
  activeAssistantMessageId: null,
  result: EMPTY_RESULT,
  deltaSummary: EMPTY_DELTA_SUMMARY,
  traceEvents: [],
  pendingApprovals: [],
  nodeSnapshots: {},
  nodeTraceMap: {},
  nodeExecutionMap: {},
  toolCallNodeMap: {},
  sessionRuns: [],
  abortController: null,

  setInput: (input) => set({ input }),
  setStructuredInput: (value) => set({ structuredInput: value }),
  setStructuredInputField: (field, value) => set((state) => ({
    structuredInput: {
      ...state.structuredInput,
      [field]: value,
    },
  })),
  setStreamOutput: (streamOutput) => set({ streamOutput }),

  beginRun: (abortController, options) => {
    const currentAbort = get().abortController
    currentAbort?.abort()
    const mode = options?.mode ?? 'structured'
    const submittedInput = `${options?.submittedInput ?? ''}`.trim()
    const turnId = Date.now().toString(36)
    const assistantMessageId = mode === 'text' ? `assistant_${turnId}` : null

    set({
      status: 'running',
      input: mode === 'text' ? '' : get().input,
      activeRunId: null,
      activeRunStartedAt: null,
      activeAssistantMessageId: assistantMessageId,
      result: EMPTY_RESULT,
      deltaSummary: EMPTY_DELTA_SUMMARY,
      traceEvents: [],
      pendingApprovals: [],
      nodeSnapshots: {},
      nodeTraceMap: {},
      nodeExecutionMap: {},
      toolCallNodeMap: {},
      selectedRunId: null,
      abortController,
      messages: mode === 'text'
        ? [
            ...get().messages,
            { id: `user_${turnId}`, role: 'user', content: submittedInput, status: 'completed' },
            { id: assistantMessageId!, role: 'assistant', content: '', status: 'running' },
          ]
        : get().messages,
    })
  },

  cancelRun: () => {
    const controller = get().abortController
    controller?.abort()
    set((state) => {
      let nextState: Partial<WorkflowTestRunState> = {
        abortController: null,
        activeRunId: null,
        activeRunStartedAt: null,
        activeAssistantMessageId: null,
        pendingApprovals: [],
        nodeExecutionMap: {},
        toolCallNodeMap: {},
        status: state.status === 'running' ? 'cancelled' : state.status,
        messages: updateMessage(
          state.messages,
          state.activeAssistantMessageId,
          (message) => ({ ...message, status: 'cancelled' }),
        ),
      }

      if (state.activeRunId) {
        const turn = buildTurnRecord(state, state.activeRunId, 'cancelled', state.sessionMemory)
        const sessionRuns = upsertRunSummary(state.sessionRuns, buildRunSummary(state.activeRunId, turn))
        nextState = {
          ...nextState,
          turnsByRunId: {
            ...state.turnsByRunId,
            [state.activeRunId]: turn,
          },
          sessionRuns,
          ...applyTurnView(turn),
        }
      }

      return nextState as WorkflowTestRunState
    })
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
          selectedRunId: event.data.runId,
          status: 'running',
          result: EMPTY_RESULT,
          deltaSummary: EMPTY_DELTA_SUMMARY,
          nodeSnapshots: {},
          pendingApprovals: [],
          nodeTraceMap: {},
          nodeExecutionMap: {},
          toolCallNodeMap: {},
          messages: updateMessage(
            state.messages,
            state.activeAssistantMessageId,
            (message) => ({ ...message, runId: event.data.runId }),
          ),
        }
      }

      if (event.event === 'human_approval_requested') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const nextTurns = updateTurnPendingApprovals(
          state.turnsByRunId,
          event.data.runId,
          (approvals) => upsertPendingApproval(approvals, event.data.approval),
        )
        const shouldUpdateVisibleApprovals = state.activeRunId === event.data.runId || state.selectedRunId === event.data.runId
        return {
          ...state,
          traceEvents,
          turnsByRunId: nextTurns,
          pendingApprovals: shouldUpdateVisibleApprovals
            ? upsertPendingApproval(state.pendingApprovals, event.data.approval)
            : state.pendingApprovals,
        }
      }

      if (event.event === 'human_approval_resolved') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const nextTurns = updateTurnPendingApprovals(
          state.turnsByRunId,
          event.data.runId,
          (approvals) => removePendingApproval(approvals, event.data.approval.id),
        )
        const shouldUpdateVisibleApprovals = state.activeRunId === event.data.runId || state.selectedRunId === event.data.runId
        return {
          ...state,
          traceEvents,
          turnsByRunId: nextTurns,
          pendingApprovals: shouldUpdateVisibleApprovals
            ? removePendingApproval(state.pendingApprovals, event.data.approval.id)
            : state.pendingApprovals,
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
      const nodeExecutionMap = { ...state.nodeExecutionMap }
      const toolCallNodeMap = { ...state.toolCallNodeMap }

      const resolveExecutionKey = (nodeId: string, nodeExecutionId?: string | null): string => {
        const explicit = buildExecutionKey(nodeId, nodeExecutionId)
        if (nodeExecutionId) return explicit
        return nodeExecutionMap[nodeId] || explicit
      }

      if (event.event === 'node_start') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const executionKey = buildExecutionKey(event.data.nodeId, event.data.nodeExecutionId)
        nodeExecutionMap[event.data.nodeId] = executionKey
        nodeTraceMap[executionKey] = {
          ...(nodeTraceMap[executionKey] ?? {
            executionKey,
            nodeId: event.data.nodeId,
            status: 'idle',
          }),
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId,
          nodeType: event.data.nodeType,
          status: 'running',
          startedAt: parseEventTs(event.data.ts) ?? Date.now(),
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
          nodeExecutionMap,
        }
      }

      if (event.event === 'node_output_delta') {
        const deltaText = `${event.data.delta ?? ''}`
        if (!deltaText) return state
        const executionKey = resolveExecutionKey(event.data.nodeId, event.data.nodeExecutionId)
        if (event.data.nodeExecutionId) {
          nodeExecutionMap[event.data.nodeId] = executionKey
        }
        const current = nodeTraceMap[executionKey] ?? {
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId,
          status: 'idle',
        }
        nodeTraceMap[executionKey] = {
          ...current,
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId ?? current.nodeExecutionId,
          outputPreview: `${current.outputPreview ?? ''}${deltaText}`.slice(-NODE_DELTA_PREVIEW_LIMIT),
        }
        const deltaSummary: WorkflowDeltaSummary = {
          content: state.deltaSummary.content,
          nodes: upsertNodeDeltaSummary(state.deltaSummary.nodes, executionKey, deltaText),
        }
        return {
          ...state,
          deltaSummary,
          nodeTraceMap,
          nodeExecutionMap,
        }
      }

      if (event.event === 'node_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const executionKey = resolveExecutionKey(event.data.nodeId, event.data.nodeExecutionId)
        if (event.data.nodeExecutionId) {
          nodeExecutionMap[event.data.nodeId] = executionKey
        }
        const current = nodeTraceMap[executionKey] ?? {
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId,
          status: 'idle',
        }
        const endedAt = parseEventTs(event.data.ts) ?? Date.now()
        const startedAt = current.startedAt
        nodeTraceMap[executionKey] = {
          ...current,
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId ?? current.nodeExecutionId,
          status: event.data.status === 'ok' ? 'success' : 'error',
          endedAt,
          durationMs: typeof startedAt === 'number' ? Math.max(0, endedAt - startedAt) : current.durationMs,
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
          nodeExecutionMap,
        }
      }

      if (event.event === 'node_snapshot') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const executionKey = resolveExecutionKey(event.data.nodeId, event.data.nodeExecutionId)
        if (event.data.nodeExecutionId) {
          nodeExecutionMap[event.data.nodeId] = executionKey
        }
        const snapshot: WorkflowNodeSnapshot = {
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId,
          nodeType: event.data.nodeType,
          status: event.data.status,
          input: event.data.input,
          output: event.data.output,
          errorMessage: event.data.errorMessage ?? null,
          hardTruncated: event.data.hardTruncated,
          ts: event.data.ts,
        }
        const current = nodeTraceMap[executionKey] ?? {
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId,
          status: 'idle',
        }
        nodeTraceMap[executionKey] = {
          ...current,
          executionKey,
          nodeId: event.data.nodeId,
          nodeExecutionId: event.data.nodeExecutionId ?? current.nodeExecutionId,
          nodeType: event.data.nodeType || current.nodeType,
          status: event.data.status === 'ok' ? 'success' : 'error',
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
          nodeExecutionMap,
          nodeSnapshots: {
            ...state.nodeSnapshots,
            [executionKey]: snapshot,
          },
        }
      }

      if (event.event === 'tool_call_start') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const nodeId = String(event.data.nodeId || '').trim()
        if (!nodeId) {
          return { ...state, traceEvents }
        }
        const executionKey = resolveExecutionKey(nodeId, event.data.nodeExecutionId)
        if (event.data.nodeExecutionId) {
          nodeExecutionMap[nodeId] = executionKey
        }
        toolCallNodeMap[event.data.toolCallId] = executionKey
        const current = nodeTraceMap[executionKey] ?? {
          executionKey,
          nodeId,
          nodeExecutionId: event.data.nodeExecutionId,
          status: 'idle',
        }
        nodeTraceMap[executionKey] = {
          ...current,
          executionKey,
          nodeId,
          nodeExecutionId: event.data.nodeExecutionId ?? current.nodeExecutionId,
          nodeType: event.data.nodeType || current.nodeType,
          toolCalls: upsertToolCall(current.toolCalls, {
            id: event.data.toolCallId,
            name: event.data.name,
            args: event.data.args,
            status: 'running',
            startedAt: parseEventTs(event.data.startedAt) ?? parseEventTs(event.data.ts),
            agentRound: event.data.agentRound,
            toolCallIndex: event.data.toolCallIndex,
            toolKind: event.data.toolKind,
          }),
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
          nodeExecutionMap,
          toolCallNodeMap,
        }
      }

      if (event.event === 'tool_call_end') {
        const traceEvents = capTraceEvents([...state.traceEvents, event])
        const nodeId = String(event.data.nodeId || '').trim()
        const executionKey = toolCallNodeMap[event.data.toolCallId]
          || (nodeId ? resolveExecutionKey(nodeId, event.data.nodeExecutionId) : '')
        if (!executionKey) {
          return { ...state, traceEvents }
        }
        if (nodeId && event.data.nodeExecutionId) {
          nodeExecutionMap[nodeId] = executionKey
        }
        const current = nodeTraceMap[executionKey]
        if (!current) {
          return {
            ...state,
            traceEvents,
            nodeExecutionMap,
            toolCallNodeMap,
          }
        }
        nodeTraceMap[executionKey] = {
          ...current,
          toolCalls: updateToolCall(current.toolCalls, event.data.toolCallId, {
            status: event.data.status === 'completed' ? 'completed' : 'error',
            result: event.data.result,
            startedAt: parseEventTs(event.data.startedAt) ?? current.toolCalls?.find((item) => item.id === event.data.toolCallId)?.startedAt,
            endedAt: parseEventTs(event.data.endedAt) ?? parseEventTs(event.data.ts),
            durationMs: typeof event.data.durationMs === 'number' ? event.data.durationMs : undefined,
            agentRound: event.data.agentRound,
            toolCallIndex: event.data.toolCallIndex,
            toolKind: event.data.toolKind,
          }),
        }
        return {
          ...state,
          traceEvents,
          nodeTraceMap,
          nodeExecutionMap,
          toolCallNodeMap,
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
        const nextSessionMemory = status === 'completed'
          ? normalizeSessionMemory(event.data.sessionMemory)
          : state.sessionMemory
        const turn = buildTurnRecord(
          {
            ...state,
            result,
            traceEvents,
          },
          event.data.runId,
          status,
          nextSessionMemory,
        )
        const sessionRuns = upsertRunSummary(state.sessionRuns, buildRunSummary(event.data.runId, turn))
        const assistantContent = event.data.finalText
          || (event.data.finalJson !== null ? JSON.stringify(event.data.finalJson, null, 2) : state.result.finalText)
          || (status === 'error' ? state.result.errorMessage ?? '' : '')

        return {
          ...state,
          ...applyTurnView(turn),
          activeRunId: null,
          activeRunStartedAt: null,
          activeAssistantMessageId: null,
          abortController: null,
          sessionMemory: nextSessionMemory,
          turnsByRunId: {
            ...state.turnsByRunId,
            [event.data.runId]: turn,
          },
          sessionRuns,
          messages: updateMessage(
            state.messages,
            state.activeAssistantMessageId,
            (message) => ({
              ...message,
              runId: event.data.runId,
              content: assistantContent,
              finalJson: event.data.finalJson,
              errorMessage: status === 'error' ? state.result.errorMessage ?? 'Run failed' : null,
              status,
            }),
          ),
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
    set((state) => {
      const nextStatus = state.status === 'cancelled' ? 'cancelled' : 'error'
      let nextState: Partial<WorkflowTestRunState> = {
        status: nextStatus,
        activeRunId: null,
        activeRunStartedAt: null,
        activeAssistantMessageId: null,
        abortController: null,
        pendingApprovals: [],
        nodeExecutionMap: {},
        toolCallNodeMap: {},
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
            errorMessage: message,
            status: 'error',
          }),
        ),
      }

      if (state.activeRunId) {
        const turn = buildTurnRecord(
          {
            ...state,
            result: {
              ...state.result,
              errorMessage: message,
            },
          },
          state.activeRunId,
          nextStatus,
          state.sessionMemory,
        )
        nextState = {
          ...nextState,
          turnsByRunId: {
            ...state.turnsByRunId,
            [state.activeRunId]: turn,
          },
          sessionRuns: upsertRunSummary(state.sessionRuns, buildRunSummary(state.activeRunId, turn)),
          ...applyTurnView(turn),
        }
      }

      return nextState as WorkflowTestRunState
    })
  },

  selectRun: (runId) => {
    set((state) => {
      if (state.activeRunId && state.activeRunId !== runId) {
        return state
      }
      const turn = state.turnsByRunId[runId]
      if (!turn) {
        return state
      }
      return {
        ...state,
        ...applyTurnView(turn),
      }
    })
  },

  reset: () => {
    const controller = get().abortController
    controller?.abort()
    set({
      status: 'idle',
      input: '',
      structuredInput: {},
      sessionId: generateSessionId(),
      sessionMemory: EMPTY_SESSION_MEMORY,
      messages: [],
      turnsByRunId: {},
      selectedRunId: null,
      activeRunId: null,
      activeRunStartedAt: null,
      activeAssistantMessageId: null,
      result: EMPTY_RESULT,
      deltaSummary: EMPTY_DELTA_SUMMARY,
      traceEvents: [],
      pendingApprovals: [],
      nodeSnapshots: {},
      nodeTraceMap: {},
      nodeExecutionMap: {},
      toolCallNodeMap: {},
      sessionRuns: [],
      abortController: null,
    })
  },

  clearSessionRuns: () => set({
    messages: [],
    turnsByRunId: {},
    sessionRuns: [],
    selectedRunId: null,
    sessionMemory: EMPTY_SESSION_MEMORY,
    sessionId: generateSessionId(),
    result: EMPTY_RESULT,
    deltaSummary: EMPTY_DELTA_SUMMARY,
    traceEvents: [],
    pendingApprovals: [],
    nodeSnapshots: {},
    nodeTraceMap: {},
    nodeExecutionMap: {},
    toolCallNodeMap: {},
    status: 'idle',
  }),
}))

export function buildCompletedWorkflowConversationHistory(
  messages: WorkflowTestMessage[],
): Array<{ role: 'user' | 'assistant'; content: string }> {
  const history: Array<{ role: 'user' | 'assistant'; content: string }> = []
  let pendingUser: { role: 'user'; content: string } | null = null

  for (const message of messages) {
    const content = formatAssistantMessageContent(message).trim()
    if (!content) continue

    if (message.role === 'user') {
      pendingUser = { role: 'user', content }
      continue
    }

    if (message.status !== 'completed') {
      pendingUser = null
      continue
    }

    if (pendingUser) {
      history.push(pendingUser)
      pendingUser = null
    }

    history.push({ role: 'assistant', content })
  }

  return history
}
