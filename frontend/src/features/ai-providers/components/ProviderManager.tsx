import { useState } from 'react'
import { Loader2, Plus, Power, Plug, CheckCircle, XCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  useAiProvidersQuery,
  useCreateAiProviderMutation,
  useUpdateAiProviderMutation,
  useDeleteAiProviderMutation,
  useTestAiProviderMutation,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ProviderRow } from './ProviderRow'
import type { AiProvider } from '../api/aiProviders'
import { Pencil, Trash2 } from 'lucide-react'

interface ProviderItemProps {
  provider: AiProvider
  onEdit: () => void
  onDelete: () => void
  onActivate: () => void
  onTest: () => void
  isActivating: boolean
  isTesting: boolean
  testResult: { ok: boolean; message?: string } | null
}

function ProviderItem({
  provider,
  onEdit,
  onDelete,
  onActivate,
  onTest,
  isActivating,
  isTesting,
  testResult,
}: ProviderItemProps) {
  return (
    <div className="flex items-center gap-4 p-4 rounded-lg border transition-colors hover:bg-muted/50">
      {/* Activate button removed - use Model Bindings instead */}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h4 className="font-medium truncate">{provider.name}</h4>
          <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
            {provider.model}
          </span>
        </div>
        <p className="text-sm text-muted-foreground truncate">{provider.baseUrl}</p>
        <p className="text-xs text-muted-foreground font-mono">{provider.apiKeyHint}</p>
      </div>

      <div className="flex items-center gap-1">
        {testResult && (
          <span
            className={`flex items-center gap-1 text-xs px-2 py-1 rounded ${testResult.ok
                ? 'bg-green-100 text-green-700'
                : 'bg-red-100 text-red-700'
              }`}
          >
            {testResult.ok ? (
              <CheckCircle className="w-3 h-3" />
            ) : (
              <XCircle className="w-3 h-3" />
            )}
            {testResult.ok ? 'OK' : testResult.message || 'Failed'}
          </span>
        )}
        <button
          onClick={onTest}
          disabled={isTesting}
          title="Test Connection"
          className="p-2 rounded hover:bg-blue-100 text-blue-600 disabled:opacity-50"
        >
          <Plug className={`w-4 h-4 ${isTesting ? 'animate-pulse' : ''}`} />
        </button>
        <button
          onClick={onEdit}
          title="Edit"
          className="p-2 rounded hover:bg-muted"
        >
          <Pencil className="w-4 h-4 text-muted-foreground" />
        </button>
        <button
          onClick={onDelete}
          title="Delete"
          className="p-2 rounded hover:bg-red-100 text-red-500"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

export function ProviderManager() {
  const { t } = useTranslation()
  const { data: providers = [], isLoading } = useAiProvidersQuery()
  const createMutation = useCreateAiProviderMutation()
  const updateMutation = useUpdateAiProviderMutation()
  const deleteMutation = useDeleteAiProviderMutation()
  const testMutation = useTestAiProviderMutation()

  const [editingId, setEditingId] = useState<string | null>(null)
  const [isAdding, setIsAdding] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{
    id: string
    ok: boolean
    message?: string
  } | null>(null)

  const handleTest = async (provider: AiProvider) => {
    setTestResult(null)
    const result = await testMutation.mutateAsync(provider.id)
    setTestResult({ id: provider.id, ok: result.ok, message: result.message })
    setTimeout(() => setTestResult(null), 3000)
  }

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
        <h3 className="font-semibold">{t('settings.ai.providers.title')}</h3>
        <button
          onClick={() => setIsAdding(true)}
          disabled={isAdding}
          className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> {t('settings.ai.providers.add')}
        </button>
      </div>

      <div className="space-y-2">
        {isAdding && (
          <ProviderRow
            isNew
            onCancel={() => setIsAdding(false)}
            onSave={(data) => {
              createMutation.mutate(data, { onSuccess: () => setIsAdding(false) })
            }}
            isSaving={createMutation.isPending}
          />
        )}

        {providers.map((provider) => (
          <div key={provider.id} className="space-y-2">
            {editingId === provider.id ? (
              <ProviderRow
                provider={provider}
                isEditing
                onCancel={() => setEditingId(null)}
                onSave={(data) => {
                  const payload = {
                    name: data.name,
                    baseUrl: data.baseUrl,
                    model: data.model,
                    ...(data.apiKey ? { apiKey: data.apiKey } : {}),
                  }
                  updateMutation.mutate(
                    { id: provider.id, payload },
                    { onSuccess: () => setEditingId(null) }
                  )
                }}
                isSaving={updateMutation.isPending}
              />
            ) : (
              <ProviderItem
                provider={provider}
                onEdit={() => setEditingId(provider.id)}
                onDelete={() => setDeleteId(provider.id)}
                onActivate={() => { }}
                onTest={() => handleTest(provider)}
                isActivating={false}
                isTesting={testMutation.isPending && testMutation.variables === provider.id}
                testResult={testResult?.id === provider.id ? testResult : null}
              />
            )}
          </div>
        ))}
      </div>

      <ConfirmDialog
        isOpen={!!deleteId}
        title={t('settings.ai.providers.deleteTitle')}
        description={t('settings.ai.providers.deleteConfirm')}
        confirmText={t('actions.delete')}
        variant="destructive"
        onConfirm={() =>
          deleteId && deleteMutation.mutate(deleteId, { onSuccess: () => setDeleteId(null) })
        }
        onCancel={() => setDeleteId(null)}
      />
    </div>
  )
}
