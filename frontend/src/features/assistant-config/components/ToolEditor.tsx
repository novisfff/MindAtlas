import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField } from '@/components/ui/styles'
import { type KeyValuePair } from './KeyValueEditor'
import { ToolInputParamsEditor } from './ToolInputParamsEditor'
import { ToolRequestConfig } from './ToolRequestConfig'
import type {
  AssistantTool,
  CreateToolRequest,
  UpdateToolRequest,
  AuthType,
  BodyType,
  InputParam,
} from '../api/tools'

interface ToolEditorProps {
  tool?: AssistantTool
  isNew?: boolean
  onCancel: () => void
  onSave: (data: CreateToolRequest | UpdateToolRequest) => void
  isSaving: boolean
  errorMessage?: string | null
}

export function ToolEditor({ tool, isNew, onCancel, onSave, isSaving, errorMessage }: ToolEditorProps) {
  const { t } = useTranslation()

  // Basic info
  const [name, setName] = useState(tool?.name || '')
  const [description, setDescription] = useState(tool?.description || '')

  // Input params
  const [inputParams, setInputParams] = useState<InputParam[]>(tool?.inputParams || [])

  // Request config
  const [httpMethod, setHttpMethod] = useState(tool?.httpMethod || 'POST')
  const [endpointUrl, setEndpointUrl] = useState(tool?.endpointUrl || '')

  const [queryParams, setQueryParams] = useState<KeyValuePair[]>(
    Object.entries(tool?.queryParams || {}).map(([k, v]) => ({ key: k, value: v }))
  )
  const [headers, setHeaders] = useState<KeyValuePair[]>(
    Object.entries(tool?.headers || {}).map(([k, v]) => ({ key: k, value: v }))
  )

  // Body config
  const [bodyType, setBodyType] = useState<BodyType>(tool?.bodyType || 'none')
  const [bodyContent, setBodyContent] = useState(tool?.bodyContent || '')

  // Auth config
  const [authType, setAuthType] = useState<AuthType>(tool?.authType || 'none')
  const [authHeaderName, setAuthHeaderName] = useState(tool?.authHeaderName || 'Authorization')
  const [authScheme] = useState(tool?.authScheme || 'Bearer')
  const [apiKey, setApiKey] = useState('')

  // Other
  const [timeoutSeconds] = useState(tool?.timeoutSeconds || 30)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    const queryParamsRecord = queryParams.reduce((acc, { key, value }) => {
      if (key.trim()) acc[key] = value
      return acc
    }, {} as Record<string, string>)

    const headersRecord = headers.reduce((acc, { key, value }) => {
      if (key.trim()) acc[key] = value
      return acc
    }, {} as Record<string, string>)

    const data: CreateToolRequest | UpdateToolRequest = {
      name,
      description: description || undefined,
      inputParams: inputParams.length > 0 ? inputParams : undefined,
      endpointUrl,
      httpMethod,
      queryParams: Object.keys(queryParamsRecord).length > 0 ? queryParamsRecord : undefined,
      headers: Object.keys(headersRecord).length > 0 ? headersRecord : undefined,
      bodyType,
      bodyContent: bodyContent || undefined,
      authType,
      authHeaderName: authType !== 'none' ? authHeaderName : undefined,
      authScheme: authType === 'bearer' ? authScheme : undefined,
      apiKey: apiKey || undefined,
      timeoutSeconds,
    }
    onSave(data)
  }

  const addInputParam = () => {
    setInputParams([...inputParams, { name: '', paramType: 'string', required: false }])
  }

  const removeInputParam = (index: number) => {
    setInputParams(inputParams.filter((_, i) => i !== index))
  }

  const updateInputParam = (index: number, updates: Partial<InputParam>) => {
    const newParams = [...inputParams]
    newParams[index] = { ...newParams[index], ...updates }
    setInputParams(newParams)
  }

  return (
    <form onSubmit={handleSubmit} className={uiChrome.card}>
      <div className="flex items-start justify-between gap-4 border-b border-border/70 px-6 py-5">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold text-foreground">
            {isNew ? t('common.create') : t('common.edit')}
          </h3>
          <p className="text-sm leading-6 text-muted-foreground">
            {t('pages.settings.assistantToolsDesc')}
          </p>
        </div>
        <Button type="button" onClick={onCancel} variant="ghost" size="icon">
          <X className="h-5 w-5" />
        </Button>
      </div>

      {errorMessage && (
        <div className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-6 py-3 text-sm font-medium text-destructive">
          <div className="h-4 w-1 rounded-full bg-destructive"></div>
          {errorMessage}
        </div>
      )}

      <div className="grid min-w-0 gap-0 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="min-w-0 border-b border-border/70 px-6 py-6 xl:border-b-0 xl:border-r">
          <div className="space-y-6">
          <div className="space-y-4">
            <h4 className="text-sm font-semibold text-foreground">{t('settings.tools.basicInfo')}</h4>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  className={uiField.input}
                  placeholder={t('settings.tools.namePlaceholder', 'Tool Name')}
                />
              </div>
              <div className="space-y-1.5">
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={4}
                  className={uiField.textarea}
                  placeholder={t('settings.tools.descriptionPlaceholder', 'Tool Description used by AI to understand when to use this tool...')}
                />
              </div>
            </div>
          </div>

          <ToolInputParamsEditor
            inputParams={inputParams}
            onAdd={addInputParam}
            onRemove={removeInputParam}
            onUpdate={updateInputParam}
          />
        </div>
        </div>

        <div className="min-w-0 px-6 py-6">
          <ToolRequestConfig
            httpMethod={httpMethod}
            endpointUrl={endpointUrl}
            authType={authType}
            authHeaderName={authHeaderName}
            apiKey={apiKey}
            bodyType={bodyType}
            bodyContent={bodyContent}
            queryParams={queryParams}
            headers={headers}
            inputParams={inputParams}
            onHttpMethodChange={setHttpMethod}
            onEndpointUrlChange={setEndpointUrl}
            onAuthTypeChange={setAuthType}
            onAuthHeaderNameChange={setAuthHeaderName}
            onApiKeyChange={setApiKey}
            onBodyTypeChange={setBodyType}
            onBodyContentChange={setBodyContent}
            onQueryParamsChange={setQueryParams}
            onHeadersChange={setHeaders}
          />
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-border/70 px-6 py-4 sm:flex-row sm:justify-end">
        <Button
          type="button"
          onClick={onCancel}
          disabled={isSaving}
          variant="outline"
        >
          {t('common.cancel')}
        </Button>
        <Button
          type="submit"
          disabled={isSaving || !name || !endpointUrl}
        >
          {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
          {isNew ? t('common.create') : t('common.confirm')}
        </Button>
      </div>
    </form>
  )
}
