import { GenericListManager } from '@/features/common/components/GenericListManager'
import { useTagsQuery, useCreateTagMutation, useUpdateTagMutation, useDeleteTagMutation } from '@/features/tags/queries'
import { TagRow } from './TagRow'
import { useTranslation } from 'react-i18next'

export function TagManager() {
  const { data: tags = [], isLoading } = useTagsQuery()
  const createMutation = useCreateTagMutation()
  const updateMutation = useUpdateTagMutation()
  const deleteMutation = useDeleteTagMutation()
  const { t } = useTranslation()

  return (
    <GenericListManager
      title={t('settings.tags.title')}
      addButtonText={t('settings.tags.add')}
      items={tags}
      isLoading={isLoading}
      isSaving={createMutation.isPending || updateMutation.isPending}
      onDelete={(id) => deleteMutation.mutate(id)}
      deleteDialogTitle={t('settings.tags.deleteTitle')}
      deleteDialogDescription={t('settings.tags.deleteConfirm')}
      deleteDialogConfirmText={t('actions.delete')}
      renderNewRow={({ onCancel }) => (
        <TagRow
          isNew
          onCancel={onCancel}
          onSave={(data) => {
            createMutation.mutate(data, { onSuccess: onCancel })
          }}
          isSaving={createMutation.isPending}
        />
      )}
      renderRow={(tag, { isEditing, onEdit, onCancel, onDelete }) => (
        <TagRow
          key={tag.id}
          tag={tag}
          isEditing={isEditing}
          onEdit={onEdit}
          onCancel={onCancel}
          onSave={(data) => {
            updateMutation.mutate({ id: tag.id, payload: data }, { onSuccess: onCancel })
          }}
          onDelete={onDelete}
          isSaving={updateMutation.isPending}
        />
      )}
    />
  )
}