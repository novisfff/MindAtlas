import { useState } from 'react'
import { Check, X, Pencil, Trash2, Eye, EyeOff, Loader2, RefreshCw, ChevronDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { AiProvider, AiProviderCreateRequest } from '../api/aiProviders'
import { useFetchModelsMutation, useFetchModelsByIdMutation } from '../queries'

interface ProviderRowProps {
  provider?: AiProvider
  isNew?: boolean
  isEditing?: boolean
  onEdit?: () => void
  onCancel: () => void
  onSave: (data: AiProviderCreateRequest) => void
  onDelete?: () => void
  isSaving: boolean
}

export function ProviderRow({
  provider,
  isNew,
  isEditing,
  onEdit,
  onCancel,
  onSave,
  onDelete,
  isSaving,
}: ProviderRowProps) {
  const { t } = useTranslation()
  const [name, setName] = useState(provider?.name || '')
  const [baseUrl, setBaseUrl] = useState(provider?.baseUrl || '')
  const [model, setModel] = useState(provider?.model || '')
  const [apiKey, setApiKey] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)

  // Model selector state
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [showModelDropdown, setShowModelDropdown] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)

  const fetchModelsMutation = useFetchModelsMutation()
  const fetchModelsByIdMutation = useFetchModelsByIdMutation()

  const handleFetchModels = async () => {
    setFetchError(null)

    // 编辑模式且未输入新 Key：使用已存储的 Key
    if (isEditing && provider?.id && !apiKey.trim()) {
      const result = await fetchModelsByIdMutation.mutateAsync(provider.id)
      if (result.ok) {
        setAvailableModels(result.models)
        setShowModelDropdown(true)
      } else {
        setFetchError(result.message || t('messages.error'))
      }
      return
    }

    // 新建模式或编辑时输入了新 Key：需要 URL 和 Key
    if (!baseUrl.trim() || !apiKey.trim()) {
      setFetchError(t('aiProvider.enterUrlAndKey'))
      return
    }
    const result = await fetchModelsMutation.mutateAsync({
      baseUrl: baseUrl.trim(),
      apiKey: apiKey.trim(),
    })
    if (result.ok) {
      setAvailableModels(result.models)
      setShowModelDropdown(true)
    } else {
      setFetchError(result.message || t('messages.error'))
    }
  }

  const handleSelectModel = (selectedModel: string) => {
    setModel(selectedModel)
    setShowModelDropdown(false)
  }

  const handleSave = () => {
    if (!name.trim() || !baseUrl.trim() || !model.trim()) return
    if (isNew && !apiKey.trim()) return
    onSave({
      name: name.trim(),
      baseUrl: baseUrl.trim(),
      model: model.trim(),
      apiKey: apiKey.trim() || '',
    })
  }

  if (isNew || isEditing) {
    return (
      <div className="flex flex-col gap-3 p-4 rounded-lg border bg-muted/50">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">
              {t('settings.ai.providers.form.name')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('settings.ai.providers.form.namePlaceholder')}
              className="w-full px-2 py-1.5 rounded border bg-background text-sm"
              autoFocus
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">
              {t('settings.ai.providers.form.model')}
            </label>
            <div className="relative">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type="text"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder={t('settings.ai.providers.form.modelPlaceholder')}
                    className="w-full px-2 py-1.5 rounded border bg-background text-sm pr-8"
                  />
                  {availableModels.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowModelDropdown(!showModelDropdown)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      <ChevronDown className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
                <button
                  type="button"
                  onClick={handleFetchModels}
                  disabled={fetchModelsMutation.isPending || fetchModelsByIdMutation.isPending}
                  className="px-2 py-1.5 rounded border bg-background text-sm hover:bg-muted disabled:opacity-50 flex items-center gap-1"
                  title={t('aiProvider.fetchModels')}
                >
                  {(fetchModelsMutation.isPending || fetchModelsByIdMutation.isPending) ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="w-3.5 h-3.5" />
                  )}
                </button>
              </div>
              {showModelDropdown && availableModels.length > 0 && (
                <div className="absolute z-10 mt-1 w-full max-h-48 overflow-auto rounded border bg-background shadow-lg">
                  {availableModels.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => handleSelectModel(m)}
                      className="w-full px-2 py-1.5 text-left text-sm hover:bg-muted truncate"
                    >
                      {m}
                    </button>
                  ))}
                </div>
              )}
              {fetchError && (
                <p className="text-xs text-red-500 mt-1">{fetchError}</p>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            {t('settings.ai.providers.form.baseUrl')}
          </label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={t('settings.ai.providers.form.baseUrlPlaceholder')}
            className="w-full px-2 py-1.5 rounded border bg-background text-sm"
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            {t('settings.ai.providers.form.apiKey')} {isEditing && t('settings.ai.providers.form.apiKeyHint')}
          </label>
          <div className="relative">
            <input
              type={showApiKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEditing ? '••••••••' : t('settings.ai.providers.form.apiKeyPlaceholder')}
              className="w-full px-2 py-1.5 rounded border bg-background text-sm pr-8"
            />
            <button
              type="button"
              onClick={() => setShowApiKey(!showApiKey)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground"
            >
              {showApiKey ? (
                <EyeOff className="w-3.5 h-3.5" />
              ) : (
                <Eye className="w-3.5 h-3.5" />
              )}
            </button>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 pt-2">
          <button
            onClick={onCancel}
            className="p-1.5 rounded hover:bg-red-100 text-red-600"
          >
            <X className="w-4 h-4" />
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving || !name.trim() || !baseUrl.trim() || !model.trim() || (isNew && !apiKey.trim())}
            className="p-1.5 rounded hover:bg-green-100 text-green-600 disabled:opacity-50"
          >
            <Check className="w-4 h-4" />
          </button>
        </div>
      </div>
    )
  }

  if (!provider) return null

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border hover:bg-muted/50 group">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium truncate">{provider.name}</span>
          <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
            {provider.model}
          </span>
        </div>
        <p className="text-xs text-muted-foreground truncate">{provider.baseUrl}</p>
      </div>
      <span className="text-xs text-muted-foreground font-mono">
        {provider.apiKeyHint}
      </span>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={onEdit}
          className="p-1.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground"
        >
          <Pencil className="w-4 h-4" />
        </button>
        {onDelete && (
          <button
            onClick={onDelete}
            className="p-1.5 rounded hover:bg-red-100 text-muted-foreground hover:text-red-600"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  )
}
