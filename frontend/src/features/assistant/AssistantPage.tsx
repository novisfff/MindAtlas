import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Menu } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { uiChrome, uiRadius, uiSurface } from '@/components/ui/styles'
import { cn } from '@/lib/utils'
import { ChatWindow } from './components/ChatWindow'
import { ConversationList } from './components/ConversationList'
import { ChatStoreProvider } from './components/ChatStoreProvider'
import { useConversationsQuery, useConversationQuery, useDeleteConversationMutation } from './queries'
import { listPendingApprovals } from './api'
import { useChatStore } from './stores/chat-store'

function AssistantPageContent() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const {
    currentConversationId,
    setConversationId,
    clearMessages,
    isLoading,
    setMessages,
    setConversationPendingApprovals,
  } = useChatStore()
  const [isSheetOpen, setSheetOpen] = useState(false)

  // Handle URL query parameter for conversation ID
  useEffect(() => {
    const idFromUrl = searchParams.get('id')
    if (idFromUrl && idFromUrl !== currentConversationId) {
      setConversationId(idFromUrl)
      // Clean up URL after processing
      setSearchParams({})
    }
  }, [searchParams, currentConversationId, setConversationId, setSearchParams])
  const loadedIdRef = useRef<string | null>(null)

  const { data: conversationsData } = useConversationsQuery()
  const deleteMutation = useDeleteConversationMutation()

  const conversations = conversationsData?.items || []

  const { data: conversation } = useConversationQuery(currentConversationId, { enabled: !isLoading })

  useEffect(() => {
    if (conversation?.messages) {
      if (isLoading) {
        if (conversation.id === currentConversationId) {
          loadedIdRef.current = conversation.id
        }
        return
      }

      if (conversation.id !== loadedIdRef.current) {
        const mappedMessages = conversation.messages.map((msg: any) => {
          let toolCalls: { id: string; name: string; args: Record<string, unknown>; result?: string; status: 'completed' | 'error' }[] | undefined
          
          const rawToolCalls = msg.toolCalls || msg.tool_calls
          const rawToolResults = msg.toolResults || msg.tool_results
          const rawSkillCalls = msg.skillCalls || msg.skill_calls

          if (rawToolCalls && Array.isArray(rawToolCalls)) {
            const resultsMap = new Map<string, {
              status: string
              result: string
              nodeId?: string
              nodeType?: string
              nodeExecutionId?: string
              agentRound?: number
              toolCallIndex?: number
              toolKind?: string
              startedAt?: string
              endedAt?: string
              durationMs?: number
            }>()
            if (rawToolResults && Array.isArray(rawToolResults)) {
              for (const r of rawToolResults) {
                // Handle potentially different naming in results too
                const id = r.id || r.tool_call_id || r.toolCallId
                resultsMap.set(id, {
                  status: r.status,
                  result: r.result,
                  nodeId: r.nodeId ?? r.node_id,
                  nodeType: r.nodeType ?? r.node_type,
                  nodeExecutionId: r.nodeExecutionId ?? r.node_execution_id,
                  agentRound: r.agentRound ?? r.agent_round,
                  toolCallIndex: r.toolCallIndex ?? r.tool_call_index,
                  toolKind: r.toolKind ?? r.tool_kind,
                  startedAt: r.startedAt ?? r.started_at,
                  endedAt: r.endedAt ?? r.ended_at,
                  durationMs: r.durationMs ?? r.duration_ms,
                })
              }
            }
            toolCalls = rawToolCalls.map((tc: any) => {
              const id = tc.id || tc.tool_call_id || tc.toolCallId
              const result = resultsMap.get(id)
              return {
                id: id,
                name: tc.name,
                args: tc.args || {},
                result: result?.result,
                status: (result?.status === 'completed' ? 'completed' : 'error') as 'completed' | 'error',
                hidden: tc.hidden ?? false,
                nodeId: tc.nodeId ?? tc.node_id ?? result?.nodeId,
                nodeType: tc.nodeType ?? tc.node_type ?? result?.nodeType,
                nodeExecutionId: tc.nodeExecutionId ?? tc.node_execution_id ?? result?.nodeExecutionId,
                agentRound: tc.agentRound ?? tc.agent_round ?? result?.agentRound,
                toolCallIndex: tc.toolCallIndex ?? tc.tool_call_index ?? result?.toolCallIndex,
                toolKind: tc.toolKind ?? tc.tool_kind ?? result?.toolKind,
                startedAt: tc.startedAt ?? tc.started_at ?? result?.startedAt,
                endedAt: result?.endedAt,
                durationMs: result?.durationMs,
              }
            })
          }
          // Map skillCalls from history
          let skillCalls: { id: string; name: string; status: 'running' | 'completed' | 'error' }[] | undefined
          if (rawSkillCalls && Array.isArray(rawSkillCalls)) {
            skillCalls = rawSkillCalls.map((sc: any) => ({
              id: sc.id,
              name: sc.name,
              status: (sc.status === 'completed' ? 'completed' : sc.status === 'error' ? 'error' : 'running') as 'running' | 'completed' | 'error',
            }))
          }
          // Map analysis from history (now as array)
          let analysisSteps: { id: string; content: string; status: 'running' | 'completed' }[] | undefined
          const rawAnalysis = msg.analysis || msg.analysisSteps
          if (rawAnalysis) {
            // Handle both old single object format and new array format
            if (Array.isArray(rawAnalysis)) {
              analysisSteps = rawAnalysis.map((a: any) => ({
                id: a.id,
                content: a.content || '',
                status: (a.status === 'completed' ? 'completed' : 'running') as 'running' | 'completed',
              }))
            } else if (typeof rawAnalysis === 'object') {
              // Legacy single object format - convert to array
              const a = rawAnalysis as { id: string; content: string; status: string }
              analysisSteps = [{
                id: a.id,
                content: a.content || '',
                status: (a.status === 'completed' ? 'completed' : 'running') as 'running' | 'completed',
              }]
            }
          }
          return {
            id: msg.id,
            role: (msg.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant',
            content: msg.content,
            toolCalls,
            skillCalls,
            analysisSteps,
            createdAt: new Date(msg.createdAt || msg.created_at).getTime(),
          }
        })

        setMessages(mappedMessages)
        loadedIdRef.current = conversation.id
      }
    }
  }, [conversation, isLoading, currentConversationId, setMessages])

  useEffect(() => {
    if (!currentConversationId || isLoading) return
    if (!conversation || conversation.id !== currentConversationId) return
    let cancelled = false
    void listPendingApprovals(currentConversationId)
      .then((items) => {
        if (cancelled) return
        setConversationPendingApprovals(items)
      })
      .catch(() => {
        if (cancelled) return
        setConversationPendingApprovals([])
      })
    return () => {
      cancelled = true
    }
  }, [conversation, currentConversationId, isLoading, setConversationPendingApprovals])

  const handleNewConversation = async () => {
    clearMessages()
    setConversationId(null)
    setSheetOpen(false)
    loadedIdRef.current = null
  }

  const handleSelectConversation = (id: string) => {
    if (id !== currentConversationId) {
      clearMessages()
      setConversationId(id)
      setSheetOpen(false)
      loadedIdRef.current = null
    }
  }

  const handleDeleteConversation = async (id: string) => {
    await deleteMutation.mutateAsync(id)
    if (id === currentConversationId) {
      clearMessages()
      setConversationId(null)
      loadedIdRef.current = null
    }
  }

  return (
    <div className={cn(uiSurface.pageBackdrop, 'flex h-full min-h-0 gap-3 overflow-hidden p-3 sm:gap-4 sm:p-4 lg:p-5')}>
      {/* 左侧对话列表 */}
      <div className={cn(uiChrome.shell, 'hidden min-h-0 w-[288px] shrink-0 overflow-hidden md:flex md:flex-col')}>
        <ConversationList
          conversations={conversations}
          currentId={currentConversationId}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDeleteConversation}
        />
      </div>

      {/* Mobile Sheet */}
      {isSheetOpen && (
        <div
          className={cn(uiSurface.overlay, 'fixed inset-0 z-50 md:hidden')}
          onClick={() => setSheetOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Conversation list"
        >
          <div
            className={cn(
              uiChrome.float,
              'fixed inset-y-3 left-3 z-50 flex w-[min(320px,calc(100vw-1.5rem))] flex-col overflow-hidden animate-in slide-in-from-left md:hidden',
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <ConversationList
              conversations={conversations}
              currentId={currentConversationId}
              onSelect={handleSelectConversation}
              onNew={handleNewConversation}
              onDelete={handleDeleteConversation}
            />
          </div>
        </div>
      )}

      {/* Right Chat Area */}
      <div className={cn(uiChrome.shell, 'relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden')}>
        <header className={cn(uiSurface.headerGlass, 'sticky top-0 z-10 flex shrink-0 items-center gap-3 border-b border-border/70 px-4 py-4 sm:px-5')}>
          <button
            className={cn(uiRadius.pill, '-ml-1 flex h-10 w-10 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden')}
            onClick={() => setSheetOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              {t('pages.assistant.title', 'AI Assistant')}
            </h1>
            <p className="hidden text-sm text-muted-foreground sm:block">
              {t('pages.assistant.subtitle', 'Ask me anything about your knowledge base')}
            </p>
          </div>
        </header>

        <ChatWindow className="min-h-0 flex-1" />
      </div>
    </div>
  )
}

export default function AssistantPage() {
  return (
    <ChatStoreProvider>
      <AssistantPageContent />
    </ChatStoreProvider>
  )
}
