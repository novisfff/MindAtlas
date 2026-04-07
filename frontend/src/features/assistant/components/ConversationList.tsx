import { Plus, MessageSquare, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { uiRadius, uiSurface } from '@/components/ui/styles'
import { Conversation } from '../types'
import { useState } from 'react'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

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
  const [deleteConfirmation, setDeleteConfirmation] = useState<{ isOpen: boolean, id: string | null }>({
    isOpen: false,
    id: null
  })

  const handleDeleteClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setDeleteConfirmation({ isOpen: true, id })
  }

  const handleConfirmDelete = async () => {
    if (deleteConfirmation.id) {
      const id = deleteConfirmation.id
      setDeletingId(id)
      setDeleteConfirmation({ isOpen: false, id: null })
      await onDelete(id)
      setDeletingId(null)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-transparent">
      <div className={cn(uiSurface.headerGlass, 'sticky top-0 z-10 border-b border-border/70 p-4')}>
        <button
          onClick={onNew}
          className={cn(
            uiRadius.control,
            'flex w-full items-center justify-center gap-2 bg-primary px-4 py-3 text-sm font-medium text-primary-foreground',
            'shadow-[0_12px_28px_rgba(15,23,42,0.14)] transition-all duration-200 hover:bg-primary/92'
          )}
        >
          <Plus className="h-5 w-5" />
          <span>{t('pages.assistant.newChat')}</span>
        </button>
      </div>

      <div className="custom-scrollbar flex-1 space-y-2 overflow-y-auto p-3">
        {conversations.length === 0 ? (
          <div className="flex h-40 flex-col items-center justify-center text-muted-foreground">
            <MessageSquare className="mb-2 h-8 w-8 opacity-20" />
            <p className="text-sm">{t('pages.assistant.noConversations')}</p>
          </div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={cn(
                uiRadius.control,
                'group relative flex cursor-pointer items-center gap-3 border px-3 py-3 transition-all duration-200',
                currentId === conv.id
                  ? 'border-border/70 bg-background/92 text-foreground shadow-[0_8px_22px_rgba(15,23,42,0.06)] dark:shadow-[0_10px_26px_rgba(2,6,23,0.24)]'
                  : 'border-transparent text-muted-foreground hover:border-border/70 hover:bg-background/72 hover:text-foreground'
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
                onClick={(e) => handleDeleteClick(e, conv.id)}
                disabled={deletingId === conv.id}
                className={cn(
                  'rounded-full p-1.5 transition-opacity hover:bg-destructive/10 hover:text-destructive focus:opacity-100 focus:outline-none',
                  'opacity-0 group-hover:opacity-100'
                )}
                title={t('actions.delete', 'Delete')}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))
        )}
      </div>

      <ConfirmDialog
        isOpen={deleteConfirmation.isOpen}
        title={t('pages.assistant.deleteTitle', 'Delete Conversation')}
        description={t('pages.assistant.deleteConfirm', 'Are you sure you want to delete this conversation? This action cannot be undone.')}
        confirmText={t('actions.delete', 'Delete')}
        cancelText={t('actions.cancel', 'Cancel')}
        variant="destructive"
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteConfirmation({ isOpen: false, id: null })}
      />
    </div>
  )
}
