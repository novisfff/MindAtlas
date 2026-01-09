import { useState } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { useEntryTypesQuery, useUpdateEntryTypeMutation, useCreateEntryTypeMutation, useDeleteEntryTypeMutation } from '@/features/entry-types/queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { TypeRow } from './TypeRow'
import { useTranslation } from 'react-i18next'

export function TypeManager() {
  const { data: types = [], isLoading } = useEntryTypesQuery()
  const updateMutation = useUpdateEntryTypeMutation()
  const createMutation = useCreateEntryTypeMutation()
  const deleteMutation = useDeleteEntryTypeMutation()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)

  const [deleteId, setDeleteId] = useState<string | null>(null)
  const { t } = useTranslation()

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
        <h3 className="font-semibold">{t('settings.entryTypes.title')}</h3>
        <button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> {t('settings.entryTypes.add')}
        </button>
      </div>

      <div className="space-y-2">
        {isAdding && (
          <TypeRow
            isNew
            onCancel={() => setIsAdding(false)}
            onSave={(data) => {
              if (data.code && data.name) {
                createMutation.mutate(
                  { code: data.code, name: data.name, color: data.color },
                  { onSuccess: () => setIsAdding(false) }
                )
              }
            }}
            isSaving={createMutation.isPending}
          />
        )}
        {types.map((type) => (
          <TypeRow
            key={type.id}
            type={type}
            isEditing={editingId === type.id}
            onEdit={() => setEditingId(type.id)}
            onCancel={() => setEditingId(null)}
            onSave={(data) => {
              updateMutation.mutate(
                { id: type.id, data },
                { onSuccess: () => setEditingId(null) }
              )
            }}
            onDelete={() => setDeleteId(type.id)}
            isSaving={updateMutation.isPending}
          />
        ))}
      </div>

      <ConfirmDialog
        isOpen={!!deleteId}
        title={t('settings.entryTypes.deleteTitle')}
        description={t('settings.entryTypes.deleteConfirm')}
        confirmText={t('actions.delete')}
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
