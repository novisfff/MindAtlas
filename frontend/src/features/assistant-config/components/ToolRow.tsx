import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Check, X } from 'lucide-react'
import type { AssistantTool, CreateToolRequest, UpdateToolRequest } from '../api/tools'

interface ToolRowProps {
  tool?: AssistantTool
  isNew?: boolean
  isEditing?: boolean
  onCancel: () => void
  onSave: (data: CreateToolRequest | UpdateToolRequest) => void
  isSaving: boolean
}

export function ToolRow({
  tool,
  isNew,
  isEditing,
  onCancel,
  onSave,
  isSaving,
}: ToolRowProps) {
  const { t } = useTranslation()
  const [name, setName] = useState(tool?.name || '')
  const [description, setDescription] = useState(tool?.description || '')
  const [endpointUrl, setEndpointUrl] = useState(tool?.endpointUrl || '')
  const [httpMethod, setHttpMethod] = useState(tool?.httpMethod || 'POST')
  const [authHeaderName, setAuthHeaderName] = useState(tool?.authHeaderName || '')
  const [authScheme, setAuthScheme] = useState(tool?.authScheme || '')
  const [apiKey, setApiKey] = useState('')
  const [timeoutSeconds, setTimeoutSeconds] = useState(tool?.timeoutSeconds || 30)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const data: CreateToolRequest | UpdateToolRequest = {
      name,
      description: description || undefined,
      endpointUrl,
      httpMethod,
      authHeaderName: authHeaderName || undefined,
      authScheme: authScheme || undefined,
      apiKey: apiKey || undefined,
      timeoutSeconds,
    }
    onSave(data)
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="p-4 rounded-lg border bg-muted/30 space-y-4"
    >
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.name')} *
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="w-full px-3 py-2 rounded-md border bg-background"
            placeholder="my_custom_tool"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.description')}
          </label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background"
            placeholder={t('settings.tools.descriptionPlaceholder')}
          />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="col-span-3">
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.endpointUrl')} *
          </label>
          <input
            type="url"
            value={endpointUrl}
            onChange={(e) => setEndpointUrl(e.target.value)}
            required
            className="w-full px-3 py-2 rounded-md border bg-background font-mono text-sm"
            placeholder="https://api.example.com/tool"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.httpMethod')}
          </label>
          <select
            value={httpMethod}
            onChange={(e) => setHttpMethod(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background"
          >
            <option value="POST">POST</option>
            <option value="GET">GET</option>
            <option value="PUT">PUT</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.authHeader')}
          </label>
          <input
            type="text"
            value={authHeaderName}
            onChange={(e) => setAuthHeaderName(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background"
            placeholder="Authorization"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.authScheme')}
          </label>
          <input
            type="text"
            value={authScheme}
            onChange={(e) => setAuthScheme(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background"
            placeholder="Bearer"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            {t('settings.tools.apiKey')}
            {tool?.apiKeyHint && (
              <span className="text-xs text-muted-foreground ml-2">
                ({tool.apiKeyHint})
              </span>
            )}
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background"
            placeholder={isEditing ? t('settings.tools.leaveBlank') : ''}
          />
        </div>
      </div>

      <div className="w-32">
        <label className="block text-sm font-medium mb-1">
          {t('settings.tools.timeout')}
        </label>
        <input
          type="number"
          value={timeoutSeconds}
          onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
          min={1}
          max={300}
          className="w-full px-3 py-2 rounded-md border bg-background"
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={isSaving}
          className="px-3 py-1.5 text-sm rounded-md hover:bg-muted"
        >
          <X className="w-4 h-4 inline mr-1" />
          {t('common.cancel')}
        </button>
        <button
          type="submit"
          disabled={isSaving || !name || !endpointUrl}
          className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isSaving ? (
            <Loader2 className="w-4 h-4 inline mr-1 animate-spin" />
          ) : (
            <Check className="w-4 h-4 inline mr-1" />
          )}
          {isNew ? t('common.create') : t('common.save')}
        </button>
      </div>
    </form>
  )
}
