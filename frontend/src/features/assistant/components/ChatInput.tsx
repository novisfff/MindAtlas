import { useState, KeyboardEvent, useEffect, useRef } from 'react'
import { ArrowUp, Plus, Mic, Square } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { uiChrome, uiRadius } from '@/components/ui/styles'

interface ChatInputProps {
  onSend: (message: string) => void
  onStop: () => void
  isLoading: boolean
  conversationId?: string | null
  variant?: 'default' | 'compact'
}

export function ChatInput({ onSend, onStop, isLoading, conversationId = null, variant = 'default' }: ChatInputProps) {
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

  useEffect(() => {
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }, [conversationId])

  const isCompact = variant === 'compact'

  return (
    <div className={cn(
      'bg-transparent',
      isCompact ? 'px-2 py-2' : 'px-4 py-4'
    )}>
      <div className={cn(
        uiChrome.control,
        'relative mx-auto flex max-w-3xl items-end gap-2 border-border/80 transition-all focus-within:border-primary/30 focus-within:ring-[3px] focus-within:ring-primary/10',
        isCompact ? 'p-1 pl-2' : 'p-1.5 pl-3'
      )}>

        {/* Left Action Button (Placeholder) */}
        {!isCompact && (
          <button
            className="mb-1.5 rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-muted"
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
            uiRadius.inset,
            'flex-1 resize-none border-0 bg-transparent',
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
        <div className="mr-1 flex self-center items-center gap-1">
          {/* Mic Button (Placeholder) */}
          {!input && (
            <button
              className="rounded-full p-2 text-muted-foreground transition-colors hover:bg-muted"
              aria-label="Voice input"
            >
              <Mic className="h-5 w-5" />
            </button>
          )}

          <button
            onClick={isLoading ? onStop : handleSend}
            disabled={isLoading ? false : !input.trim()}
            aria-label={isLoading ? 'Stop generation' : 'Send message'}
            className={cn(
              'relative shrink-0 rounded-full leading-none transition-all duration-200',
              'flex items-center justify-center',
              isCompact ? 'h-8 w-8' : 'h-10 w-10',
              isLoading
                ? 'bg-destructive text-white hover:bg-destructive/90 active:scale-95'
                : input.trim()
                  ? 'bg-primary text-primary-foreground hover:opacity-90 active:scale-95'
                  : 'bg-muted text-muted-foreground cursor-not-allowed opacity-50'
            )}
          >
            {isLoading ? (
              <Square
                className={cn(
                  'pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2',
                  isCompact ? 'h-3.5 w-3.5' : 'h-4 w-4'
                )}
              />
            ) : (
              <ArrowUp
                className={cn(
                  'pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2',
                  isCompact ? 'h-4 w-4' : 'h-5 w-5'
                )}
              />
            )}
          </button>
        </div>
      </div>

      <div className="mt-3 select-none text-center text-[11px] text-muted-foreground/60">
        {t('pages.assistant.footer', 'AI can make mistakes. Check important info.')}
      </div>
    </div>
  )
}
