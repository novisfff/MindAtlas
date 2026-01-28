import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Menu } from 'lucide-react'
import { ChatWindow } from './components/ChatWindow'
import { ConversationList } from './components/ConversationList'
import { ChatStoreProvider } from './components/ChatStoreProvider'
import { useConversationsQuery, useConversationQuery, useDeleteConversationMutation } from './queries'
import { useChatStore } from './stores/chat-store'
import { useSearchParams } from 'react-router-dom'

function AssistantPageContent() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const { currentConversationId, setConversationId, clearMessages, isLoading, setMessages } = useChatStore()
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
            const resultsMap = new Map<string, { status: string; result: string }>()
            if (rawToolResults && Array.isArray(rawToolResults)) {
              for (const r of rawToolResults) {
                // Handle potentially different naming in results too
                const id = r.id || r.tool_call_id || r.toolCallId
                resultsMap.set(id, { status: r.status, result: r.result })
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
    <div className="flex h-full">
      {/* 左侧对话列表 */}
      <div className="hidden w-64 shrink-0 border-r bg-muted/20 md:block">
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
          className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm md:hidden"
          onClick={() => setSheetOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Conversation list"
        >
          <div
            className="fixed inset-y-0 left-0 z-50 w-64 border-r bg-background shadow-lg animate-in slide-in-from-left"
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
      <div className="flex flex-1 flex-col overflow-hidden relative bg-gradient-to-b from-background to-muted/20 min-h-0">
        <header className="flex items-center gap-3 border-b p-4 bg-background/95 backdrop-blur-xl sticky top-0 z-10 shrink-0 shadow-sm">
          <button
            className="md:hidden p-2 -ml-2 hover:bg-muted rounded-full transition-colors"
            onClick={() => setSheetOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              {t('pages.assistant.title', 'AI Assistant')}
            </h1>
            <p className="text-sm text-muted-foreground hidden sm:block">
              {t('pages.assistant.subtitle', 'Ask me anything about your knowledge base')}
            </p>
          </div>
        </header>

        <ChatWindow className="flex-1" />
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
