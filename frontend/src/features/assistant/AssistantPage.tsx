import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Menu } from 'lucide-react'
import { ChatWindow } from './components/ChatWindow'
import { ConversationList } from './components/ConversationList'
import { useConversationsQuery, useConversationQuery, useDeleteConversationMutation } from './queries'
import { useChatStore } from './stores/chat-store'

export default function AssistantPage() {
  const { t } = useTranslation()
  const { currentConversationId, setConversationId, clearMessages, isLoading } = useChatStore()
  const [isSheetOpen, setSheetOpen] = useState(false)
  const loadedIdRef = useRef<string | null>(null)

  const { data: conversationsData } = useConversationsQuery()
  const deleteMutation = useDeleteConversationMutation()

  const conversations = conversationsData?.items || []

  const { data: conversation } = useConversationQuery(currentConversationId)

  useEffect(() => {
    if (conversation?.messages) {
      // Prevent overwriting local state if we are generating a response (isLoading)
      // or if we have already loaded this conversation (to avoid race conditions with background refetches)
      if (isLoading) {
        // If we heavily rely on local state during loading, we mark this ID as "handled"
        // so that a subsequent non-loading update doesn't clobber it with stale data.
        if (conversation.id === currentConversationId) {
          loadedIdRef.current = conversation.id
        }
        return
      }

      if (conversation.id !== loadedIdRef.current) {
        useChatStore.setState({
          messages: conversation.messages.map((msg) => {
            // 将后端的 toolCalls + toolResults 合并为前端的 ToolCall 格式
            let toolCalls: { id: string; name: string; args: Record<string, unknown>; result?: string; status: 'completed' | 'error' }[] | undefined
            if (msg.toolCalls && Array.isArray(msg.toolCalls)) {
              const resultsMap = new Map<string, { status: string; result: string }>()
              if (msg.toolResults && Array.isArray(msg.toolResults)) {
                for (const r of msg.toolResults) {
                  resultsMap.set(r.id, { status: r.status, result: r.result })
                }
              }
              toolCalls = msg.toolCalls.map((tc: { id: string; name: string; args: Record<string, unknown> }) => {
                const result = resultsMap.get(tc.id)
                return {
                  id: tc.id,
                  name: tc.name,
                  args: tc.args || {},
                  result: result?.result,
                  status: (result?.status === 'completed' ? 'completed' : 'error') as 'completed' | 'error',
                }
              })
            }
            return {
              id: msg.id,
              role: (msg.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant',
              content: msg.content,
              toolCalls,
              createdAt: new Date(msg.createdAt).getTime(),
            }
          }),
        })
        loadedIdRef.current = conversation.id
      }
    }
  }, [conversation, isLoading, currentConversationId])

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
    <div className="flex h-[calc(100vh-4rem)]">
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

