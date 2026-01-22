import { useState } from 'react'
import { Plus, Pencil, Trash2, Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  useModelsQuery,
  useCredentialsQuery,
  useCreateModelMutation,
  useUpdateModelMutation,
  useDeleteModelMutation,
  useDiscoverModelsByCredentialMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import type { AiModelType } from '../api/models'

export function ModelsManager() {
  const { t } = useTranslation()
  const { data: models = [] } = useModelsQuery()
  const { data: credentials = [] } = useCredentialsQuery()
  const createMutation = useCreateModelMutation()
  const updateMutation = useUpdateModelMutation()
  const deleteMutation = useDeleteModelMutation()
  const discoverMutation = useDiscoverModelsByCredentialMutation()

  const [isAdding, setIsAdding] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [discoveringId, setDiscoveringId] = useState<string | null>(null)

  const [formData, setFormData] = useState({
    credentialId: '',
    name: '',
    modelType: 'llm' as AiModelType,
  })

  const handleSubmit = async () => {
    if (!formData.credentialId || !formData.name) return

    if (editingId) {
      await updateMutation.mutateAsync({
        id: editingId,
        payload: { name: formData.name, modelType: formData.modelType },
      })
      setEditingId(null)
    } else {
      await createMutation.mutateAsync(formData)
      setIsAdding(false)
    }

    setFormData({ credentialId: '', name: '', modelType: 'llm' })
  }

  const handleEdit = (id: string) => {
    const model = models.find((m) => m.id === id)
    if (model) {
      setFormData({
        credentialId: model.credentialId,
        name: model.name,
        modelType: model.modelType,
      })
      setEditingId(id)
    }
  }

  const handleDelete = async () => {
    if (deleteId) {
      await deleteMutation.mutateAsync(deleteId)
      setDeleteId(null)
    }
  }

  const handleDiscover = async (credentialId: string) => {
    setDiscoveringId(credentialId)
    try {
      const result = await discoverMutation.mutateAsync(credentialId)
      if (result.ok && result.models.length > 0) {
        // 批量创建模型
        for (const model of result.models) {
          await createMutation.mutateAsync({
            credentialId,
            name: model.name,
            modelType: model.suggestedType,
          })
        }
      }
    } finally {
      setDiscoveringId(null)
    }
  }

  const getCredentialName = (credentialId: string) => {
    return credentials.find((c) => c.id === credentialId)?.name ?? 'Unknown'
  }

  const groupedModels = models.reduce(
    (acc, model) => {
      if (!acc[model.credentialId]) {
        acc[model.credentialId] = []
      }
      acc[model.credentialId].push(model)
      return acc
    },
    {} as Record<string, typeof models>
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">{t('settings.ai.sections.models')}</h3>
          <p className="text-sm text-muted-foreground">{t('settings.ai.sections.modelsDesc')}</p>
        </div>
        <button
          onClick={() => setIsAdding(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
        >
          <Plus className="w-4 h-4" />
          {t('settings.ai.addModel')}
        </button>
      </div>

      {(isAdding || editingId) && (
        <div className="p-4 border rounded-lg space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1">
              {t('settings.ai.sections.credentials')}
            </label>
            <select
              value={formData.credentialId}
              onChange={(e) => setFormData({ ...formData, credentialId: e.target.value })}
              disabled={!!editingId}
              className="w-full px-3 py-2 border rounded-md"
            >
              <option value="">Select credential...</option>
              {credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">{t('settings.ai.modelName')}</label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-3 py-2 border rounded-md"
              placeholder="gpt-4o-mini"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">{t('settings.ai.selectType')}</label>
            <select
              value={formData.modelType}
              onChange={(e) => setFormData({ ...formData, modelType: e.target.value as AiModelType })}
              className="w-full px-3 py-2 border rounded-md"
            >
              <option value="llm">{t('settings.ai.modelTypes.llm')}</option>
              <option value="embedding">{t('settings.ai.modelTypes.embedding')}</option>
            </select>
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              disabled={!formData.credentialId || !formData.name}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
            >
              {t('actions.save')}
            </button>
            <button
              onClick={() => {
                setIsAdding(false)
                setEditingId(null)
                setFormData({ credentialId: '', name: '', modelType: 'llm' })
              }}
              className="px-4 py-2 border rounded-md hover:bg-muted"
            >
              {t('actions.cancel')}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {Object.entries(groupedModels).map(([credentialId, credModels]) => (
          <div key={credentialId} className="border rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <h4 className="font-medium">{getCredentialName(credentialId)}</h4>
              <button
                onClick={() => handleDiscover(credentialId)}
                disabled={discoveringId === credentialId}
                className="inline-flex items-center gap-1 px-3 py-1 text-sm border rounded-md hover:bg-muted disabled:opacity-50"
                title={t('aiProvider.fetchModels')}
              >
                <Download className={`w-3 h-3 ${discoveringId === credentialId ? 'animate-pulse' : ''}`} />
                {t('aiProvider.fetchModels')}
              </button>
            </div>
            <div className="space-y-2">
              {credModels.map((model) => (
                <div
                  key={model.id}
                  className="flex items-center justify-between p-2 hover:bg-muted rounded"
                >
                  <div>
                    <span className="font-medium">{model.name}</span>
                    <span className="ml-2 text-sm text-muted-foreground">
                      ({t(`settings.ai.modelTypes.${model.modelType}`)})
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleEdit(model.id)}
                      className="p-1 hover:bg-muted rounded"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => setDeleteId(model.id)}
                      className="p-1 hover:bg-destructive/10 text-destructive rounded"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        isOpen={!!deleteId}
        onCancel={() => setDeleteId(null)}
        onConfirm={handleDelete}
        title={t('actions.delete')}
        description={t('messages.confirmDelete')}
        variant="destructive"
      />
    </div>
  )
}
