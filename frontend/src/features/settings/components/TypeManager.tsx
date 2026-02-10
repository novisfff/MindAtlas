import { GenericListManager } from '@/features/common/components/GenericListManager'
import { useEntryTypesQuery, useUpdateEntryTypeMutation, useCreateEntryTypeMutation, useDeleteEntryTypeMutation } from '@/features/entry-types/queries'
import { TypeRow } from './TypeRow'
import { useTranslation } from 'react-i18next'

export function TypeManager() {
  const { data: types = [], isLoading } = useEntryTypesQuery()
  const updateMutation = useUpdateEntryTypeMutation()
  const createMutation = useCreateEntryTypeMutation()
  const deleteMutation = useDeleteEntryTypeMutation()
  const { t } = useTranslation()

  return (
    <GenericListManager
      title={t('settings.entryTypes.title')}
      addButtonText={t('settings.entryTypes.add')}
      items={types}
      isLoading={isLoading}
      isSaving={createMutation.isPending || updateMutation.isPending}
      onDelete={(id) => deleteMutation.mutate(id)}
      deleteDialogTitle={t('settings.entryTypes.deleteTitle')}
      deleteDialogDescription={t('settings.entryTypes.deleteConfirm')}
      deleteDialogConfirmText={t('actions.delete')}
      renderNewRow={({ onCancel }) => (
        <TypeRow
          isNew
          onCancel={onCancel}
          onSave={(data) => {
            if (data.code && data.name) {
              createMutation.mutate(
                { code: data.code, name: data.name, color: data.color },
                { onSuccess: onCancel }
              )
            }
          }}
          isSaving={createMutation.isPending}
        />
      )}
      renderRow={(type, { isEditing, onEdit, onCancel, onDelete }) => (
        <TypeRow
          key={type.id}
          type={type}
          isEditing={isEditing}
          onEdit={onEdit}
          onCancel={onCancel}
          onSave={(data) => {
            updateMutation.mutate(
              { id: type.id, data },
              { onSuccess: onCancel }
            )
          }}
          onDelete={onDelete}
          isSaving={updateMutation.isPending}
        />
      )}
    />
  )
}