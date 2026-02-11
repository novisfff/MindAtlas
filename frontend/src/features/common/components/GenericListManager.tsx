import { useState, ReactNode } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

interface RowProps {
  isEditing: boolean
  onEdit: () => void
  onCancel: () => void
  onDelete: () => void
  isSaving: boolean
}

interface NewRowProps {
  onCancel: () => void
  isSaving: boolean
}

interface GenericListManagerProps<T extends { id: string }> {
  title: string
  addButtonText: string
  items: T[]
  isLoading: boolean
  renderRow: (item: T, props: RowProps) => ReactNode
  renderNewRow: (props: NewRowProps) => ReactNode
  onDelete: (id: string) => void
  deleteDialogTitle: string
  deleteDialogDescription: string
  deleteDialogConfirmText: string
  isSaving: boolean
}

export function GenericListManager<T extends { id: string }>({
  title,
  addButtonText,
  items,
  isLoading,
  renderRow,
  renderNewRow,
  onDelete,
  deleteDialogTitle,
  deleteDialogDescription,
  deleteDialogConfirmText,
  isSaving,
}: GenericListManagerProps<T>) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{title}</h3>
        <button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> {addButtonText}
        </button>
      </div>

      <div className="space-y-2">
        {isAdding && renderNewRow({
          onCancel: () => setIsAdding(false),
          isSaving
        })}
        {items.map((item) => renderRow(item, {
          isEditing: editingId === item.id,
          onEdit: () => setEditingId(item.id),
          onCancel: () => setEditingId(null),
          onDelete: () => setDeleteId(item.id),
          isSaving
        }))}
      </div>

      <ConfirmDialog
        isOpen={!!deleteId}
        title={deleteDialogTitle}
        description={deleteDialogDescription}
        confirmText={deleteDialogConfirmText}
        variant="destructive"
        onConfirm={() => {
          if (deleteId) {
            onDelete(deleteId)
            setDeleteId(null)
          }
        }}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  )
}
