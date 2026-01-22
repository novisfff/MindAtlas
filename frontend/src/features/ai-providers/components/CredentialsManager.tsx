import { useState } from 'react'
import { Plus, Pencil, Trash2, Plug } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  useCredentialsQuery,
  useCreateCredentialMutation,
  useUpdateCredentialMutation,
  useDeleteCredentialMutation,
  useTestCredentialMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

export function CredentialsManager() {
  const { t } = useTranslation()
  const { data: credentials = [] } = useCredentialsQuery()
  const createMutation = useCreateCredentialMutation()
  const updateMutation = useUpdateCredentialMutation()
  const deleteMutation = useDeleteCredentialMutation()
  const testMutation = useTestCredentialMutation()

  const [isAdding, setIsAdding] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)

  const [formData, setFormData] = useState({
    name: '',
    baseUrl: '',
    apiKey: '',
  })

  const handleSubmit = async () => {
    if (!formData.name || !formData.baseUrl || (!editingId && !formData.apiKey)) return

    if (editingId) {
      await updateMutation.mutateAsync({
        id: editingId,
        payload: {
          name: formData.name,
          baseUrl: formData.baseUrl,
          ...(formData.apiKey ? { apiKey: formData.apiKey } : {}),
        },
      })
      setEditingId(null)
    } else {
      await createMutation.mutateAsync(formData)
      setIsAdding(false)
    }

    setFormData({ name: '', baseUrl: '', apiKey: '' })
  }

  const handleEdit = (id: string) => {
    const cred = credentials.find((c) => c.id === id)
    if (cred) {
      setFormData({
        name: cred.name,
        baseUrl: cred.baseUrl,
        apiKey: '',
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

  const handleTest = async (id: string) => {
    setTestingId(id)
    await testMutation.mutateAsync(id)
    setTimeout(() => setTestingId(null), 2000)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">{t('settings.ai.sections.credentials')}</h3>
          <p className="text-sm text-muted-foreground">
            {t('settings.ai.credentialsDesc', { defaultValue: '管理 AI 服务提供商的凭据' })}
          </p>
        </div>
        <button
          onClick={() => setIsAdding(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
        >
          <Plus className="w-4 h-4" />
          {t('settings.ai.addCredential', { defaultValue: '添加凭据' })}
        </button>
      </div>

      {(isAdding || editingId) && (
        <div className="p-4 border rounded-lg space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1">
              {t('labels.name')}
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-3 py-2 border rounded-md"
              placeholder="OpenAI"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Base URL</label>
            <input
              type="text"
              value={formData.baseUrl}
              onChange={(e) => setFormData({ ...formData, baseUrl: e.target.value })}
              className="w-full px-3 py-2 border rounded-md"
              placeholder="https://api.openai.com/v1"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">
              API Key {editingId && `(${t('form.optional')})`}
            </label>
            <input
              type="password"
              value={formData.apiKey}
              onChange={(e) => setFormData({ ...formData, apiKey: e.target.value })}
              className="w-full px-3 py-2 border rounded-md"
              placeholder={editingId ? t('settings.tools.leaveBlank') : 'sk-...'}
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              disabled={!formData.name || !formData.baseUrl || (!editingId && !formData.apiKey)}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
            >
              {t('actions.save')}
            </button>
            <button
              onClick={() => {
                setIsAdding(false)
                setEditingId(null)
                setFormData({ name: '', baseUrl: '', apiKey: '' })
              }}
              className="px-4 py-2 border rounded-md hover:bg-muted"
            >
              {t('actions.cancel')}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {credentials.map((cred) => (
          <div
            key={cred.id}
            className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/50"
          >
            <div className="flex-1">
              <div className="font-medium">{cred.name}</div>
              <div className="text-sm text-muted-foreground">{cred.baseUrl}</div>
              <div className="text-xs text-muted-foreground font-mono">{cred.apiKeyHint}</div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => handleTest(cred.id)}
                disabled={testingId === cred.id}
                className="p-2 hover:bg-muted rounded"
                title={t('settings.tools.auth')}
              >
                <Plug className={`w-4 h-4 ${testingId === cred.id ? 'animate-pulse' : ''}`} />
              </button>
              <button
                onClick={() => handleEdit(cred.id)}
                className="p-2 hover:bg-muted rounded"
              >
                <Pencil className="w-4 h-4" />
              </button>
              <button
                onClick={() => setDeleteId(cred.id)}
                className="p-2 hover:bg-destructive/10 text-destructive rounded"
              >
                <Trash2 className="w-4 h-4" />
              </button>
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
