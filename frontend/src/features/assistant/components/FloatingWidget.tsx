import { MessageSquare, X, Maximize2, Plus, GripVertical } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useChatStore } from '../stores/chat-store'
import { ChatWindow } from './ChatWindow'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'
import { useState, useRef, useEffect } from 'react'

export function FloatingWidget() {
  const { t } = useTranslation()
  const { isOpen, toggleOpen, clearMessages, setConversationId, currentConversationId } = useChatStore()

  // Position state (bottom-right based)
  const [position, setPosition] = useState({ x: 24, y: 24 })
  const [isDragging, setIsDragging] = useState(false)
  const dragStartPos = useRef({ x: 0, y: 0 })
  const initialButtonPos = useRef({ x: 0, y: 0 })
  const hasMoved = useRef(false)

  const handleMouseDown = (e: React.MouseEvent) => {
    // Only allow dragging from the button itself, not children if needed, but here button is fine
    // Or add a specific handle if refined control is needed. 
    // For now, let's make the entire button draggable.
    e.preventDefault()
    setIsDragging(true)
    hasMoved.current = false
    dragStartPos.current = { x: e.clientX, y: e.clientY }
    initialButtonPos.current = { ...position }
  }

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return

      const deltaX = dragStartPos.current.x - e.clientX // Inverted because we use 'right'
      const deltaY = dragStartPos.current.y - e.clientY // Inverted because we use 'bottom'

      if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) {
        hasMoved.current = true
      }

      setPosition({
        x: Math.max(24, initialButtonPos.current.x + deltaX),
        y: Math.max(24, initialButtonPos.current.y + deltaY)
      })
    }

    const handleMouseUp = () => {
      setIsDragging(false)
    }

    if (isDragging) {
      window.addEventListener('mousemove', handleMouseMove)
      window.addEventListener('mouseup', handleMouseUp)
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging])

  const handleNewChat = () => {
    clearMessages()
    setConversationId(null)
  }

  const handleToggle = () => {
    if (!hasMoved.current) {
      toggleOpen()
    }
  }

  return (
    <div
      className="fixed z-50 flex flex-col items-end gap-4"
      style={{ right: position.x, bottom: position.y }}
    >
      {isOpen && (
        <div className={cn(
          'w-full sm:w-[350px] max-w-[90vw] h-[500px] flex flex-col overflow-hidden',
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
              <button
                onClick={handleNewChat}
                className={cn(
                  'rounded-full p-1.5 hover:bg-background',
                  'text-muted-foreground hover:text-foreground transition-colors'
                )}
                title={t('pages.assistant.newChat')}
              >
                <Plus className="h-4 w-4" />
              </button>
              <Link
                to={currentConversationId ? `/assistant?id=${currentConversationId}` : '/assistant'}
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
        onMouseDown={handleMouseDown}
        onClick={handleToggle}
        style={{ cursor: isDragging ? 'grabbing' : 'pointer' }}
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
