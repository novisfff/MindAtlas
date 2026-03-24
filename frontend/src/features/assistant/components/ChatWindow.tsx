import { useEffect, useRef } from 'react'
import { useChat } from '../hooks/useChat'
import { useChatStore } from '../stores/chat-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { cn } from '@/lib/utils'

interface ChatWindowProps {
  className?: string
  variant?: 'default' | 'compact'
}

export function ChatWindow({ className, variant = 'default' }: ChatWindowProps) {
  const { messages, isLoading, sendMessage, stop, attachActiveRun } = useChat()
  const currentConversationId = useChatStore((state) => state.currentConversationId)
  const attachedConversationRef = useRef<string | null>(null)

  useEffect(() => {
    if (!currentConversationId) {
      attachedConversationRef.current = null
      return
    }
    if (isLoading) return
    if (messages.length === 0) return
    if (attachedConversationRef.current === currentConversationId) return
    attachedConversationRef.current = currentConversationId
    void attachActiveRun(currentConversationId).catch(() => {
      // ignore attach failures
    })
  }, [attachActiveRun, currentConversationId, isLoading, messages.length])

  return (
    <div className={cn('flex flex-col min-h-0', className)}>
      <div className="flex-1 overflow-hidden">
        <MessageList messages={messages} variant={variant} isLoading={isLoading} />
      </div>
      <ChatInput
        onSend={sendMessage}
        onStop={stop}
        isLoading={isLoading}
        conversationId={currentConversationId}
        variant={variant}
      />
    </div>
  )
}
