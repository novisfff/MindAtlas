import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useChatStore } from '../stores/chat-store'
import { buildRunStreamUrl, createConversation, getActiveRun, stopRun } from '../api'
import { assistantKeys } from '../queries'
import { ToolCall, SkillCall, WorkflowStep } from '../types'
import { SSEParser } from '@/lib/sse/SSEParser'

const RUN_CURSOR_KEY_PREFIX = 'assistant.run.cursor.'

const isTerminalRunStatus = (status?: string | null) =>
  ['completed', 'failed', 'cancelled'].includes(String(status || '').toLowerCase())

const isActiveRunStatus = (status?: string | null) =>
  ['queued', 'running', 'waiting_approval', 'cancelling'].includes(String(status || '').toLowerCase())

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

  useEffect(() => {
    const previous = conversationRef.current
    const current = currentConversationId
    if (previous && previous !== current) {
      abortRef.current?.abort()
      streamingRunRef.current = null
      clearActiveRun()
      setActiveWorkflowSteps([])
      setLoading(false)
    }
    conversationRef.current = current
  }, [clearActiveRun, currentConversationId, setActiveWorkflowSteps, setLoading])

  const streamFromUrl = useCallback(async (
    params: {
      convId: string
      url: string
      initialContent?: string
      onMessageStart?: (messageId: string) => void
    },
  ) => {
    const { convId, url, initialContent = '', onMessageStart } = params
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

    const processSeq = (evtData: Record<string, unknown>) => {
      const seq = Number(evtData.seq)
      if (!Number.isFinite(seq) || seq <= 0) return
      const normalized = Math.floor(seq)
      setLastEventSeq(normalized)
      if (localRunId) writeStoredRunSeq(localRunId, normalized)
    }

    try {
      const response = await fetch(url, {
        method: 'GET',
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
          processSeq((evt.data || {}) as Record<string, unknown>)

          if (evt.event === 'message_start') {
            const messageId = evt.data.messageId as string | undefined
            const runId = evt.data.runId as string | undefined
            if (runId) {
              localRunId = runId
              streamingRunRef.current = runId
              setActiveRun(runId, 'running')
            }
            if (messageId) {
              setLastMessageId(messageId)
              onMessageStart?.(messageId)
            }
          } else if (evt.event === 'run_status') {
            const status = String(evt.data.status || '')
            setActiveRunStatus(status || null)
            setLoading(isActiveRunStatus(status))
            if (isTerminalRunStatus(status)) {
              clearActiveRun()
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
            }
            addToolCall(toolCall)
          } else if (evt.event === 'tool_call_end') {
            updateToolCall(evt.data.toolCallId as string, {
              status: (evt.data.status as string) === 'completed' ? 'completed' : 'error',
              result: evt.data.result as string,
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
            setActiveWorkflowSteps([])
            setLoading(false)
            clearActiveRun()
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
      setActiveWorkflowSteps([])
      setLoading(false)
      clearActiveRun()
      queryClient.invalidateQueries({ queryKey: [...assistantKeys.conversations(), convId] })
      queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        enqueueDelta('\n\n*Connection error*')
        if (drainingPromise) await drainingPromise
      }
      setActiveWorkflowSteps([])
      setLoading(false)
    } finally {
      streamingRunRef.current = null
    }
  }, [
    addSkillCall,
    addToolCall,
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

    const assistantId = (Date.now() + 1).toString()
    addMessage({
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })

    const chatUrl = `/api/assistant/conversations/${convId}/chat`
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    try {
      const response = await fetch(chatUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: content }),
        signal: abortRef.current.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      // Reuse stream parser through a temporary object URL-like flow by direct reader consumption.
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      const parser = new SSEParser()

      const pendingDeltas: string[] = []
      let drainingPromise: Promise<void> | null = null
      let fullContent = ''
      let localRunId: string | null = null

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

      const handleSeq = (evtData: Record<string, unknown>) => {
        const seq = Number(evtData.seq)
        if (!Number.isFinite(seq) || seq <= 0) return
        const normalized = Math.floor(seq)
        setLastEventSeq(normalized)
        if (localRunId) writeStoredRunSeq(localRunId, normalized)
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value, { stream: true })
        const events = parser.parse(chunk)
        for (const evt of events) {
          handleSeq((evt.data || {}) as Record<string, unknown>)

          if (evt.event === 'message_start') {
            const messageId = evt.data.messageId as string | undefined
            const runId = evt.data.runId as string | undefined
            if (runId) {
              localRunId = runId
              streamingRunRef.current = runId
              setActiveRun(runId, 'running')
            }
            if (messageId) setLastMessageId(messageId)
          } else if (evt.event === 'run_status') {
            const status = String(evt.data.status || '')
            setActiveRunStatus(status || null)
            setLoading(isActiveRunStatus(status))
            if (isTerminalRunStatus(status)) clearActiveRun()
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
            }
            addToolCall(toolCall)
          } else if (evt.event === 'tool_call_end') {
            updateToolCall(evt.data.toolCallId as string, {
              status: (evt.data.status as string) === 'completed' ? 'completed' : 'error',
              result: evt.data.result as string,
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
            setActiveWorkflowSteps([])
            setLoading(false)
            clearActiveRun()
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
      setActiveWorkflowSteps([])
      setLoading(false)
      clearActiveRun()
      queryClient.invalidateQueries({ queryKey: [...assistantKeys.conversations(), convId] })
      queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        updateLastMessage('*Connection error*')
      }
      setActiveWorkflowSteps([])
      setLoading(false)
    } finally {
      streamingRunRef.current = null
    }
  }, [
    addMessage,
    addSkillCall,
    addToolCall,
    clearActiveRun,
    currentConversationId,
    endAnalysis,
    isLoading,
    queryClient,
    setActiveRun,
    setActiveRunStatus,
    setActiveWorkflowSteps,
    setConversationId,
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

      setActiveRun(run.runId, run.status || null, run.lastEventSeq || 0)
      setLoading(isActiveRunStatus(run.status))
      streamingRunRef.current = run.runId

      const storedSeq = readStoredRunSeq(run.runId)
      const afterSeq = Math.max(storedSeq, Number(run.checkpointSeq || 0))
      const url = buildRunStreamUrl(convId, run.runId, afterSeq)
      await streamFromUrl({ convId, url, initialContent: messages[messages.length - 1]?.content || '' })
      return true
    } catch {
      return false
    }
  }, [clearActiveRun, messages, setActiveRun, setLoading, streamFromUrl])

  const stop = useCallback(async () => {
    const convId = currentConversationId
    const runId = activeRunId || streamingRunRef.current
    if (convId && runId) {
      try {
        await stopRun(convId, runId)
        setActiveRunStatus('cancelling')
      } catch {
        // ignore stop errors and still abort local stream
      }
    }
    abortRef.current?.abort()
    setActiveWorkflowSteps([])
    setLoading(false)
  }, [activeRunId, currentConversationId, setActiveRunStatus, setActiveWorkflowSteps, setLoading])

  return { messages, isLoading, sendMessage, stop, attachActiveRun }
}
