import { MessageSquare, X, Maximize2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useChatStore } from '../stores/chat-store'
import { ChatWindow } from './ChatWindow'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'

export function FloatingWidget() {
  const { t } = useTranslation()
  const { isOpen, toggleOpen } = useChatStore()

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-4">
      {isOpen && (
        <div className={cn(
          'w-full sm:w-[400px] max-w-[90vw] h-[500px] flex flex-col overflow-hidden',
          'rounded-lg border bg-background shadow-2xl',
          'animate-in slide-in-from-bottom-5 fade-in duration-200'
        )}
          role="dialog"
          aria-modal="true"
          aria-label={t('pages.assistant.title')}
        >
          <div className="flex items-center justify-between border-b bg-background/80 backdrop-blur-md px-4 py-3 sticky top-0 z-10">
            <h3 className="font-semibold text-sm">{t('pages.assistant.title')}</h3>
            <div className="flex items-center gap-2">
              <Link
                to="/assistant"
                className={cn(
                  'rounded-full p-1.5 hover:bg-background',
                  'text-muted-foreground hover:text-foreground transition-colors'
                )}
                title={t('actions.openFullPage')}
              >
                <Maximize2 className="h-4 w-4" />
              </Link>
              <button
                onClick={toggleOpen}
                className={cn(
                  'rounded-full p-1.5 hover:bg-background',
                  'text-muted-foreground hover:text-foreground transition-colors'
                )}
                aria-label={t('actions.closeChat')}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <ChatWindow className="flex-1" variant="compact" />
        </div>
      )}

      <button
        onClick={toggleOpen}
        className={cn(
          'flex h-14 w-14 items-center justify-center rounded-full',
          'shadow-lg transition-all hover:scale-105 active:scale-95',
          isOpen
            ? 'bg-muted text-foreground'
            : 'bg-primary text-primary-foreground'
        )}
        aria-label={isOpen ? t('actions.closeChat') : t('actions.openChat')}
      >
        {isOpen ? (
          <X className="h-6 w-6" />
        ) : (
          <MessageSquare className="h-6 w-6" />
        )}
      </button>
    </div>
  )
}
