import { useState, KeyboardEvent, useEffect, useRef } from 'react'
import { ArrowUp, Loader2, Plus, Mic } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

interface ChatInputProps {
  onSend: (message: string) => void
  isLoading: boolean
  variant?: 'default' | 'compact'
}

export function ChatInput({ onSend, isLoading, variant = 'default' }: ChatInputProps) {
  const [input, setInput] = useState('')
  const { t } = useTranslation()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    if (!input.trim() || isLoading) return
    onSend(input)
    setInput('')
    // Reset height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const adjustHeight = () => {
    const target = textareaRef.current
    if (target) {
      target.style.height = 'auto'
      target.style.height = `${Math.min(target.scrollHeight, 160)}px`
    }
  }

  useEffect(() => {
    adjustHeight()
  }, [input])

  const isCompact = variant === 'compact'

  return (
    <div className={cn(
      "bg-transparent",
      isCompact ? "px-2 py-2" : "px-4 py-4"
    )}>
      <div className={cn(
        "mx-auto max-w-3xl relative flex items-end gap-2 bg-background border shadow-sm transition-all focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50",
        isCompact ? "p-1 pl-2 rounded-[20px]" : "p-1.5 pl-3 rounded-[26px]"
      )}>

        {/* Left Action Button (Placeholder) */}
        {!isCompact && (
          <button
            className="mb-1.5 p-1.5 text-muted-foreground hover:bg-muted rounded-full transition-colors"
            aria-label="Add attachment"
          >
            <Plus className="h-5 w-5" />
          </button>
        )}

        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('pages.assistant.inputPlaceholder', 'Ask anything...')}
          aria-label="Chat message input"
          className={cn(
            'flex-1 resize-none rounded-lg',
            'bg-transparent text-base md:text-sm leading-relaxed',
            'placeholder:text-muted-foreground/60',
            'focus:outline-none',
            'disabled:cursor-not-allowed disabled:opacity-50',
            'custom-scrollbar',
            isCompact ? 'min-h-[36px] py-1.5 px-2' : 'min-h-[44px] py-3 px-2',
            isCompact ? 'max-h-[120px]' : 'max-h-[160px]'
          )}
          rows={1}
          disabled={isLoading}
          style={{ height: 'auto', minHeight: isCompact ? '36px' : '44px' }}
        />

        {/* Right Action Buttons */}
        <div className="flex items-center gap-1 mb-0 mr-1">
          {/* Mic Button (Placeholder) */}
          {!input && (
            <button
              className="p-2 text-muted-foreground hover:bg-muted rounded-full transition-colors"
              aria-label="Voice input"
            >
              <Mic className="h-5 w-5" />
            </button>
          )}

          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            aria-label={isLoading ? 'Sending message' : 'Send message'}
            className={cn(
              'flex items-center justify-center rounded-full transition-all duration-200',
              isCompact ? 'h-8 w-8' : 'h-10 w-10',
              input.trim()
                ? 'bg-primary text-primary-foreground hover:opacity-90 active:scale-95'
                : 'bg-muted text-muted-foreground cursor-not-allowed opacity-50'
            )}
          >
            {isLoading ? (
              <Loader2 className={cn("animate-spin", isCompact ? "h-4 w-4" : "h-5 w-5")} />
            ) : (
              <ArrowUp className={cn(isCompact ? "h-4 w-4" : "h-5 w-5")} />
            )}
          </button>
        </div>
      </div>

      <div className="text-center text-[11px] text-muted-foreground/50 mt-3 select-none">
        {t('pages.assistant.footer', 'AI can make mistakes. Check important info.')}
      </div>
    </div>
  )
}

