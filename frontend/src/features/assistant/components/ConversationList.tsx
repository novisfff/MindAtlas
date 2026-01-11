import { Plus, MessageSquare, Trash2, MoreHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Conversation } from '../types'
import { useState } from 'react'

interface ConversationListProps {
  conversations: Conversation[]
  currentId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}

export function ConversationList({
  conversations,
  currentId,
  onSelect,
  onNew,
  onDelete,
}: ConversationListProps) {
  const { t } = useTranslation()
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (confirm(t('pages.assistant.confirmDelete', 'Delete this conversation?'))) {
      setDeletingId(id)
      await onDelete(id)
      setDeletingId(null)
    }
  }

  return (
    <div className="flex h-full flex-col bg-muted/10">
      <div className="p-4 border-b bg-background/95 backdrop-blur-xl sticky top-0 z-10 transition-all">
        <button
          onClick={onNew}
          className={cn(
            'flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3',
            'bg-gradient-to-r from-primary to-primary/80 text-primary-foreground',
            'shadow-md hover:shadow-lg hover:from-primary/90 hover:to-primary/70',
            'transition-all duration-200 font-medium'
          )}
        >
          <Plus className="h-5 w-5" />
          <span>{t('pages.assistant.newChat')}</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-1 custom-scrollbar">
        {conversations.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
            <MessageSquare className="h-8 w-8 mb-2 opacity-20" />
            <p className="text-sm">{t('pages.assistant.noConversations')}</p>
          </div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={cn(
                'group relative flex items-center gap-3 rounded-lg px-3 py-3 cursor-pointer',
                'border border-transparent transition-all duration-200',
                'hover:bg-accent/50',
                currentId === conv.id
                  ? 'bg-accent border-border/50 shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              )}
              onClick={() => onSelect(conv.id)}
            >
              <MessageSquare className={cn(
                "h-4 w-4 shrink-0 transition-colors",
                currentId === conv.id ? "text-primary" : "text-muted-foreground/70"
              )} />

              <div className="flex-1 overflow-hidden">
                <p className="truncate text-sm font-medium leading-none">
                  {conv.title || t('pages.assistant.newChat')}
                </p>
                <p className="truncate text-xs text-muted-foreground/70 mt-1.5">
                  {new Date(conv.updatedAt || Date.now()).toLocaleDateString()}
                </p>
              </div>

              <button
                onClick={(e) => handleDelete(e, conv.id)}
                disabled={deletingId === conv.id}
                className={cn(
                  "opacity-0 group-hover:opacity-100 transition-opacity",
                  "p-1.5 hover:bg-destructive/10 hover:text-destructive rounded-md",
                  "focus:opacity-100 focus:outline-none"
                )}
                title={t('common.delete', 'Delete')}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}