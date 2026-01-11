import ReactMarkdown from 'react-markdown'
import { Bot, User } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { ToolCall } from '../types'
import { ToolCallDisplay } from './ToolCallDisplay'

interface MessageItemProps {
  message: {
    id: string
    role: 'user' | 'assistant'
    content: string
    toolCalls?: ToolCall[]
    createdAt: number
  }
  variant?: 'default' | 'compact'
}

export function MessageItem({ message, variant = 'default' }: MessageItemProps) {
  const { t } = useTranslation()
  const isUser = message.role === 'user'
  const isCompact = variant === 'compact'

  return (
    <div className={cn(
      'flex w-full',
      isCompact ? 'gap-2 px-3 py-3' : 'gap-4 px-4 py-6',
      isUser ? 'flex-row-reverse' : 'flex-row'
    )}>
      <div className={cn(
        'flex shrink-0 items-center justify-center rounded-full border shadow-sm',
        isCompact ? 'h-7 w-7' : 'h-10 w-10',
        isUser ? 'bg-background' : 'bg-primary/10 border-primary/20 text-primary'
      )}>
        {isUser ? <User className={cn(isCompact ? "h-4 w-4" : "h-6 w-6")} /> : <Bot className={cn(isCompact ? "h-4 w-4" : "h-6 w-6")} />}
      </div>

      <div className={cn(
        "flex flex-col max-w-[85%]",
        isUser ? "items-end" : "items-start"
      )}>
        <div className="flex items-center gap-2 mb-1.5 px-1">
          <span className="text-xs font-medium text-muted-foreground group-hover:text-foreground transition-colors">
            {isUser ? t('pages.assistant.you') : t('pages.assistant.assistant')}
          </span>
          <span className="text-[10px] text-muted-foreground/60">
            {new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>

        <div className={cn(
          "prose prose-sm dark:prose-invert max-w-none break-words shadow-sm transition-all",
          isCompact ? "rounded-xl px-3 py-2 text-sm" : "rounded-2xl px-5 py-3.5",
          isUser
            ? "bg-primary text-primary-foreground rounded-tr-sm"
            : "bg-muted/50 border border-border/50 rounded-tl-sm hover:bg-muted/80 hover:shadow-md"
        )}>
          {message.toolCalls && message.toolCalls.length > 0 && (
            <ToolCallDisplay toolCalls={message.toolCalls!} />
          )}
          <ReactMarkdown
            components={{
              p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
              code: ({ node, inline, className, children, ...props }: any) => {
                return inline ? (
                  <code className="bg-black/10 dark:bg-white/10 px-1 py-0.5 rounded font-mono text-sm" {...props}>{children}</code>
                ) : (
                  <code className="block bg-black/10 dark:bg-white/10 p-3 rounded-lg font-mono text-sm overflow-x-auto my-2" {...props}>{children}</code>
                )
              }
            }}
          >
            {message.content || '...'}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

