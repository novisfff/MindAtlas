import { useState } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { useTagsQuery, useCreateTagMutation, useUpdateTagMutation, useDeleteTagMutation } from '@/features/tags/queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { TagRow } from './TagRow'

export function TagManager() {
  const { data: tags = [], isLoading } = useTagsQuery()
  const createMutation = useCreateTagMutation()
  const updateMutation = useUpdateTagMutation()
  const deleteMutation = useDeleteTagMutation()
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
        <h3 className="font-semibold">Tags</h3>
        <button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> Add Tag
        </button>
      </div>

      <div className="space-y-2">
        {isAdding && (
          <TagRow
            isNew
            onCancel={() => setIsAdding(false)}
            onSave={(data) => {
              createMutation.mutate(data, { onSuccess: () => setIsAdding(false) })
            }}
            isSaving={createMutation.isPending}
          />
        )}
        {tags.map((tag) => (
          <TagRow
            key={tag.id}
            tag={tag}
            isEditing={editingId === tag.id}
            onEdit={() => setEditingId(tag.id)}
            onCancel={() => setEditingId(null)}
            onSave={(data) => {
              updateMutation.mutate({ id: tag.id, payload: data }, { onSuccess: () => setEditingId(null) })
            }}
            onDelete={() => setDeleteId(tag.id)}
            isSaving={updateMutation.isPending}
          />
        ))}
      </div>

      <ConfirmDialog
        isOpen={!!deleteId}
        title="Delete Tag"
        description="Are you sure you want to delete this tag? This action cannot be undone."
        confirmText="Delete"
        variant="destructive"
        onConfirm={() => {
          if (deleteId) {
            deleteMutation.mutate(deleteId)
            setDeleteId(null)
          }
        }}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  )
}
