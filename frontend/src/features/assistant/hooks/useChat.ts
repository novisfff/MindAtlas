import { useCallback, useContext, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ChatStoreContext, globalChatStore, useChatStore } from '../stores/chat-store'
import { buildRunStreamUrl, createConversation, getActiveRun, stopRun } from '../api'
import { assistantKeys } from '../queries'
import { ToolCall, SkillCall, WorkflowStep } from '../types'
import {
  bindEventDedupeRun,
  createEventDedupeState,
  identityFromPayload,
  isActiveRunStatus,
  isPreservedWaitingStatus,
  isTerminalRunStatus,
  messageEndAction,
  shouldApplyEvent,
  type EventDedupeState,
} from '../eventIdentity'
import { withMindAtlasLocale } from '@/lib/api/locale'
import { SSEParser } from '@/lib/sse/SSEParser'

const RUN_CURSOR_KEY_PREFIX = 'assistant.run.cursor.'
/** Delay before re-attaching SSE after a preserved disconnect. */
const REATTACH_DELAY_MS = 750

const readStoredRunSeq = (runId: string): number => {
  try {
    const raw = sessionStorage.getItem(`${RUN_CURSOR_KEY_PREFIX}${runId}`)
    const parsed = Number(raw)
    if (Number.isFinite(parsed) && parsed >= 0) return Math.floor(parsed)
  } catch {
    // ignore
  }
  return 0
}

const writeStoredRunSeq = (runId: string, seq: number) => {
  try {
    sessionStorage.setItem(`${RUN_CURSOR_KEY_PREFIX}${runId}`, String(Math.max(0, Math.floor(seq || 0))))
  } catch {
    // ignore
  }
}

export function useChat() {
  const queryClient = useQueryClient()
  // Prefer the provider-bound store (AssistantPage local instance). Fall back to
  // the module singleton only when no ChatStoreProvider is mounted (FloatingWidget).
  const storeFromContext = useContext(ChatStoreContext)
  const chatStore = storeFromContext || globalChatStore

  const {
    messages,
    isLoading,
    currentConversationId,
    activeRunId,
    addMessage,
    updateLastMessage,
    setLastMessageId,
    addToolCall,
    updateToolCall,
    addSkillCall,
    updateSkillCall,
    upsertHumanApproval,
    startAnalysis,
    updateAnalysis,
    endAnalysis,
    setActiveWorkflowSteps,
    setLoading,
    setConversationId,
    setActiveRun,
    setActiveRunStatus,
    clearActiveRun,
    setLastEventSeq,
  } = useChatStore()

  const abortRef = useRef<AbortController | null>(null)
  const streamingRunRef = useRef<string | null>(null)
  const conversationRef = useRef<string | null>(currentConversationId)
  /** Per-stream dedupe state; survives equal/older cursor reconnects for the same Run. */
  const eventDedupeRef = useRef<EventDedupeState>(createEventDedupeState())
  const reattachTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Stable ref so streamFromUrl can schedule reattach without circular deps.
  const attachActiveRunRef = useRef<
    ((conversationId: string | null | undefined) => Promise<boolean>) | null
  >(null)

  useEffect(() => {
    const previous = conversationRef.current
    const current = currentConversationId
    if (previous && previous !== current) {
      abortRef.current?.abort()
      streamingRunRef.current = null
      eventDedupeRef.current = createEventDedupeState()
      if (reattachTimerRef.current) {
        clearTimeout(reattachTimerRef.current)
        reattachTimerRef.current = null
      }
      clearActiveRun()
      setActiveWorkflowSteps([])
      setLoading(false)
    }
    conversationRef.current = current
  }, [clearActiveRun, currentConversationId, setActiveWorkflowSteps, setLoading])

  useEffect(() => {
    return () => {
      if (reattachTimerRef.current) {
        clearTimeout(reattachTimerRef.current)
        reattachTimerRef.current = null
      }
    }
  }, [])

  const streamFromUrl = useCallback(async (
    params: {
      convId: string
      url: string
      initialContent?: string
      onMessageStart?: (messageId: string) => void
      method?: 'GET' | 'POST'
      body?: string
    },
  ) => {
    const { convId, url, initialContent = '', onMessageStart, method = 'GET', body } = params
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    const sseParser = new SSEParser()
    const pendingDeltas: string[] = []
    let drainingPromise: Promise<void> | null = null
    let fullContent = initialContent
    let localRunId = streamingRunRef.current

    const waitNextFrame = () =>
      new Promise<void>((resolve) => {
        if (typeof requestAnimationFrame === 'function') {
          requestAnimationFrame(() => resolve())
          return
        }
        setTimeout(resolve, 0)
      })

    const startDeltaDrain = () => {
      if (drainingPromise) return
      drainingPromise = (async () => {
        while (pendingDeltas.length > 0) {
          const next = pendingDeltas.shift()
          if (!next) continue
          fullContent += next
          updateLastMessage(fullContent)
          await waitNextFrame()
        }
        drainingPromise = null
      })()
    }

    const enqueueDelta = (delta: string) => {
      if (!delta) return
      pendingDeltas.push(delta)
      startDeltaDrain()
    }

    const acceptEvent = (evtData: Record<string, unknown>): boolean => {
      const identity = identityFromPayload(evtData, localRunId)
      if (identity.runId) {
        eventDedupeRef.current = bindEventDedupeRun(eventDedupeRef.current, identity.runId)
      }
      const apply = shouldApplyEvent(eventDedupeRef.current, identity)
      if (!apply) return false
      const seq = Number(evtData.seq)
      if (Number.isFinite(seq) && seq > 0) {
        const normalized = Math.floor(seq)
        setLastEventSeq(normalized)
        if (localRunId || identity.runId) {
          writeStoredRunSeq(localRunId || identity.runId, normalized)
        }
      }
      return true
    }

    /** Read status from the same store instance this hook writes to. */
    const readActiveRunStatus = (): string | null => {
      return chatStore.getState().activeRunStatus
    }

    const scheduleReattach = (conversationId: string) => {
      if (reattachTimerRef.current) {
        clearTimeout(reattachTimerRef.current)
      }
      reattachTimerRef.current = setTimeout(() => {
        reattachTimerRef.current = null
        // Only reattach if still no live reader and conversation unchanged.
        if (streamingRunRef.current) return
        if (conversationRef.current !== conversationId) return
        void attachActiveRunRef.current?.(conversationId).catch(() => {
          // On reattach failure, clear loading so the user is not stuck.
          const status = chatStore.getState().activeRunStatus
          if (!isActiveRunStatus(status) && !isPreservedWaitingStatus(status)) {
            setLoading(false)
          } else {
            // One more delayed attempt if still preserved.
            if (reattachTimerRef.current) return
            reattachTimerRef.current = setTimeout(() => {
              reattachTimerRef.current = null
              if (streamingRunRef.current) return
              if (conversationRef.current !== conversationId) return
              void attachActiveRunRef.current?.(conversationId).catch(() => {
                setLoading(false)
              })
            }, REATTACH_DELAY_MS)
          }
        })
      }, REATTACH_DELAY_MS)
    }

    try {
      const response = await fetch(url, {
        method,
        headers: withMindAtlasLocale(
          method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
        ),
        body: method === 'POST' ? body : undefined,
        signal: abortRef.current.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value, { stream: true })
        const events = sseParser.parse(chunk)
        for (const evt of events) {
          const evtData = (evt.data || {}) as Record<string, unknown>
          if (!acceptEvent(evtData)) {
            continue
          }

          if (evt.event === 'message_start') {
            const messageId = evt.data.messageId as string | undefined
            const runId = evt.data.runId as string | undefined
            if (runId) {
              localRunId = runId
              streamingRunRef.current = runId
              eventDedupeRef.current = bindEventDedupeRun(eventDedupeRef.current, runId)
              setActiveRun(runId, 'running')
            }
            if (messageId) {
              setLastMessageId(messageId)
              onMessageStart?.(messageId)
            }
          } else if (evt.event === 'run_status') {
            const status = String(evt.data.status || '')
            // Preserve waiting/recovering/cancelling as active UI state.
            setActiveRunStatus(status || null)
            if (isActiveRunStatus(status) || isPreservedWaitingStatus(status)) {
              setLoading(true)
            } else if (isTerminalRunStatus(status)) {
              setLoading(false)
              clearActiveRun()
            } else {
              setLoading(false)
            }
          } else if (evt.event === 'content_delta') {
            const delta = evt.data.delta as string
            if (delta) enqueueDelta(delta)
          } else if (evt.event === 'error') {
            const error = evt.data.error as string
            if (error) enqueueDelta(`\n\n*Error: ${error}*`)
          } else if (evt.event === 'tool_call_start') {
            const toolCall: ToolCall = {
              id: evt.data.toolCallId as string,
              name: evt.data.name as string,
              args: evt.data.args as Record<string, unknown>,
              status: 'running',
              hidden: (evt.data.hidden as boolean) ?? false,
              nodeId: (evt.data.nodeId as string | undefined) ?? undefined,
              nodeType: (evt.data.nodeType as string | undefined) ?? undefined,
              nodeExecutionId: (evt.data.nodeExecutionId as string | undefined) ?? undefined,
              agentRound: typeof evt.data.agentRound === 'number' ? evt.data.agentRound as number : undefined,
              toolCallIndex: typeof evt.data.toolCallIndex === 'number' ? evt.data.toolCallIndex as number : undefined,
              toolKind: (evt.data.toolKind as ToolCall['toolKind'] | undefined) ?? undefined,
              startedAt: (evt.data.startedAt as string | undefined) ?? undefined,
            }
            addToolCall(toolCall)
          } else if (evt.event === 'tool_call_end') {
            updateToolCall(evt.data.toolCallId as string, {
              status: (evt.data.status as string) === 'completed' ? 'completed' : 'error',
              result: evt.data.result as string,
              nodeId: (evt.data.nodeId as string | undefined) ?? undefined,
              nodeType: (evt.data.nodeType as string | undefined) ?? undefined,
              nodeExecutionId: (evt.data.nodeExecutionId as string | undefined) ?? undefined,
              agentRound: typeof evt.data.agentRound === 'number' ? evt.data.agentRound as number : undefined,
              toolCallIndex: typeof evt.data.toolCallIndex === 'number' ? evt.data.toolCallIndex as number : undefined,
              toolKind: (evt.data.toolKind as ToolCall['toolKind'] | undefined) ?? undefined,
              startedAt: (evt.data.startedAt as string | undefined) ?? undefined,
              endedAt: (evt.data.endedAt as string | undefined) ?? undefined,
              durationMs: typeof evt.data.durationMs === 'number' ? evt.data.durationMs as number : undefined,
            })
          } else if (evt.event === 'skill_start') {
            const skillCall: SkillCall = {
              id: evt.data.id as string,
              name: evt.data.name as string,
              status: 'running',
              hidden: Boolean(evt.data.hidden),
            }
            addSkillCall(skillCall)
          } else if (evt.event === 'skill_end') {
            updateSkillCall(evt.data.id as string, {
              status: (evt.data.status as string) === 'completed'
                ? 'completed'
                : (evt.data.status as string) === 'cancelled'
                  ? 'completed'
                  : 'error',
            })
          } else if (evt.event === 'workflow_steps') {
            const steps = Array.isArray(evt.data.steps) ? (evt.data.steps as WorkflowStep[]) : []
            setActiveWorkflowSteps(steps)
          } else if (evt.event === 'title_updated') {
            queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
          } else if (evt.event === 'message_end') {
            // Do not wipe waiting/recovering/cancelling; terminal clear is owned
            // by run_status / stream-end. Preserve loading while Run stays active.
            // CRITICAL: read from the same store instance this hook writes to.
            const action = messageEndAction(readActiveRunStatus())
            if (action.clearWorkflowSteps) setActiveWorkflowSteps([])
            setLoading(action.setLoading)
            if (action.clearActiveRun) clearActiveRun()
          } else if (evt.event === 'analysis_start') {
            startAnalysis(evt.data.id as string)
          } else if (evt.event === 'analysis_delta') {
            updateAnalysis(evt.data.id as string, evt.data.delta as string)
          } else if (evt.event === 'analysis_end') {
            endAnalysis(evt.data.id as string)
          } else if (evt.event === 'human_approval_requested' || evt.event === 'human_approval_resolved') {
            const approval = evt.data.approval as any
            if (approval && typeof approval === 'object' && typeof approval.id === 'string') {
              upsertHumanApproval(approval)
            }
          }
        }
      }
      if (drainingPromise) await drainingPromise
      // Only clear active Run when stream ends on a terminal status. Waiting /
      // recovering / cancelling must stay visible so reconnect can re-attach.
      const activeStatus = readActiveRunStatus()
      if (isTerminalRunStatus(activeStatus) || !isActiveRunStatus(activeStatus)) {
        setActiveWorkflowSteps([])
        setLoading(false)
        if (isTerminalRunStatus(activeStatus)) {
          clearActiveRun()
        }
      } else {
        // Preserve waiting/recovering/cancelling UI state across reader close.
        // Schedule re-attach so we do not leave loading=true with no reader.
        setLoading(true)
        scheduleReattach(convId)
      }
      queryClient.invalidateQueries({ queryKey: [...assistantKeys.conversations(), convId] })
      queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        enqueueDelta('\n\n*Connection error*')
        if (drainingPromise) await drainingPromise
      }
      // On disconnect/abort keep waiting/recovering state; only drop loading when
      // there is no preserved active status. When preserving, schedule reattach.
      const activeStatus = readActiveRunStatus()
      if (!isActiveRunStatus(activeStatus) && !isPreservedWaitingStatus(activeStatus)) {
        setActiveWorkflowSteps([])
        setLoading(false)
      } else {
        // Keep loading, but re-attach so we are not stuck without a reader.
        setLoading(true)
        scheduleReattach(convId)
      }
    } finally {
      streamingRunRef.current = null
    }
  }, [
    addSkillCall,
    addToolCall,
    chatStore,
    clearActiveRun,
    endAnalysis,
    queryClient,
    setActiveRun,
    setActiveRunStatus,
    setActiveWorkflowSteps,
    setLastEventSeq,
    setLastMessageId,
    setLoading,
    startAnalysis,
    updateAnalysis,
    updateLastMessage,
    updateSkillCall,
    updateToolCall,
    upsertHumanApproval,
  ])

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || isLoading) return

    let convId = currentConversationId
    if (!convId) {
      try {
        const conv = await createConversation()
        convId = conv.id
        setConversationId(convId)
        queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
      } catch (err) {
        console.error('Failed to create conversation:', err)
        addMessage({
          id: Date.now().toString(),
          role: 'assistant',
          content: '*无法创建对话，请检查网络连接后重试*',
          createdAt: Date.now(),
        })
        return
      }
    }

    const userMsg = {
      id: Date.now().toString(),
      role: 'user' as const,
      content,
      createdAt: Date.now(),
    }
    addMessage(userMsg)
    setActiveWorkflowSteps([])
    setLoading(true)
    clearActiveRun()
    eventDedupeRef.current = createEventDedupeState()
    if (reattachTimerRef.current) {
      clearTimeout(reattachTimerRef.current)
      reattachTimerRef.current = null
    }

    const assistantId = (Date.now() + 1).toString()
    addMessage({
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })

    // Initial chat POST streams the same durable event log as GET .../stream.
    const chatUrl = `/api/assistant/conversations/${convId}/chat`
    await streamFromUrl({
      convId,
      url: chatUrl,
      initialContent: '',
      method: 'POST',
      body: JSON.stringify({ message: content }),
    })
  }, [
    addMessage,
    clearActiveRun,
    currentConversationId,
    isLoading,
    queryClient,
    setActiveWorkflowSteps,
    setConversationId,
    setLoading,
    streamFromUrl,
  ])

  const attachActiveRun = useCallback(async (conversationId: string | null | undefined) => {
    const convId = String(conversationId || '').trim()
    if (!convId) return false
    if (streamingRunRef.current) return true

    try {
      const run = await getActiveRun(convId)
      if (!run || !run.runId) {
        clearActiveRun()
        setLoading(false)
        return false
      }

      // Bind dedupe to this Run; seed from stored client cursor only so equal/older
      // reconnects stay idempotent without skipping unapplied public events on
      // cold page loads (where lastEventSeq may be ahead of local application).
      eventDedupeRef.current = bindEventDedupeRun(eventDedupeRef.current, run.runId)
      const storedSeq = readStoredRunSeq(run.runId)
      eventDedupeRef.current.lastAppliedSeq = Math.max(
        eventDedupeRef.current.lastAppliedSeq,
        Math.max(storedSeq, 0),
      )

      setActiveRun(run.runId, run.status || null, Math.max(storedSeq, Number(run.checkpointSeq || 0)))
      // Preserve waiting/recovering/cancelling as active loading state.
      setLoading(isActiveRunStatus(run.status) || isPreservedWaitingStatus(run.status))
      streamingRunRef.current = run.runId

      const afterSeq = Math.max(storedSeq, Number(run.checkpointSeq || 0))
      const url = buildRunStreamUrl(convId, run.runId, afterSeq)
      await streamFromUrl({
        convId,
        url,
        initialContent: messages[messages.length - 1]?.content || '',
      })
      return true
    } catch {
      return false
    }
  }, [clearActiveRun, messages, setActiveRun, setLoading, streamFromUrl])

  // Keep ref in sync for scheduleReattach inside streamFromUrl.
  attachActiveRunRef.current = attachActiveRun

  const stop = useCallback(async () => {
    const convId = currentConversationId
    const runId = activeRunId || streamingRunRef.current
    const hasLiveReader = Boolean(streamingRunRef.current && abortRef.current)

    if (convId && runId) {
      try {
        await stopRun(convId, runId)
        // Durable stop is a state transition; keep the SSE reader attached so
        // cancelling → cancelled (or run_finalizing) events still apply.
        setActiveRunStatus('cancelling')
        if (hasLiveReader) {
          setLoading(true)
        } else {
          // No live reader: clear loading and try re-attach so terminal status
          // can still be observed, or drop spinner if attach fails.
          setLoading(true)
          void attachActiveRun(convId).catch(() => {
            setLoading(false)
          })
        }
      } catch {
        // stopRun failed — if no live reader, clear loading so UI is not stuck.
        if (!hasLiveReader) {
          setLoading(false)
        }
      }
      return
    }

    // No run to stop: abort any local stream and clear loading.
    abortRef.current?.abort()
    streamingRunRef.current = null
    setLoading(false)
  }, [activeRunId, attachActiveRun, currentConversationId, setActiveRunStatus, setLoading])

  return { messages, isLoading, sendMessage, stop, attachActiveRun }
}
