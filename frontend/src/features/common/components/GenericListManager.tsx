import { useState, ReactNode } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { uiRadius } from '@/components/ui/styles'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { cn } from '@/lib/utils'

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
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-foreground">{title}</h3>
          <p className="text-sm text-muted-foreground">{items.length} items</p>
        </div>
        <Button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
        >
          <Plus className="w-4 h-4" /> {addButtonText}
        </Button>
      </div>

      <div className="space-y-3">
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
        {items.length === 0 && !isAdding ? (
          <div className={cn(uiRadius.panel, 'border border-dashed border-border/80 bg-muted/20 px-6 py-10 text-center text-sm text-muted-foreground')}>
            No items yet.
          </div>
        ) : null}
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
