import { useEffect, useRef } from 'react'
import { MessageItem } from './MessageItem'
import { ToolCall } from '../types'
import { useTranslation } from 'react-i18next'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  toolCalls?: ToolCall[]
}

interface MessageListProps {
  messages: Message[]
  variant?: 'default' | 'compact'
}

export function MessageList({ messages, variant = 'default' }: MessageListProps) {
  const { t } = useTranslation()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-center text-muted-foreground">
        <p className="mb-2 text-lg font-medium">{t('pages.assistant.noMessagesYet')}</p>
        <p className="text-sm">{t('pages.assistant.startConversation')}</p>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto overflow-x-hidden custom-scrollbar">
      {messages.map((msg) => (
        <MessageItem key={msg.id} message={msg} variant={variant} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
