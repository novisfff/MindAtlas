import { useChat } from '../hooks/useChat'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { cn } from '@/lib/utils'

interface ChatWindowProps {
  className?: string
  variant?: 'default' | 'compact'
}

export function ChatWindow({ className, variant = 'default' }: ChatWindowProps) {
  const { messages, isLoading, sendMessage } = useChat()

  return (
    <div className={cn('flex flex-col min-h-0', className)}>
      <div className="flex-1 overflow-hidden">
        <MessageList messages={messages} variant={variant} isLoading={isLoading} />
      </div>
      <ChatInput onSend={sendMessage} isLoading={isLoading} variant={variant} />
    </div>
  )
}
