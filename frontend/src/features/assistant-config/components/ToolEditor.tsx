import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, X } from 'lucide-react'
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
    <form onSubmit={handleSubmit} className="flex flex-col h-[700px] w-full max-w-full bg-background rounded-lg border shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b shrink-0 bg-muted/20">
        <h3 className="font-semibold text-lg">{isNew ? t('common.create') : t('common.edit')}</h3>
        <button type="button" onClick={onCancel} className="text-muted-foreground hover:text-foreground p-1">
          <X className="w-5 h-5" />
        </button>
      </div>

      {errorMessage && (
        <div className="px-6 py-3 bg-red-50 text-red-600 text-sm font-medium border-b border-red-100 flex items-center gap-2 animate-in slide-in-from-top-2">
          <div className="w-1 h-4 bg-red-500 rounded-full"></div>
          {errorMessage}
        </div>
      )}

      <div className="flex-1 grid grid-cols-12 divide-x h-full overflow-hidden">
        {/* LEFT COLUMN: Tool Info & Inputs (5/12) */}
        <div className="col-span-5 flex flex-col h-full overflow-y-auto custom-scrollbar bg-card/50 p-6 space-y-8">
          <div className="space-y-4">
            <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.basicInfo')}</h4>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  className="w-full px-3 py-2 text-sm rounded-md border bg-background focus:ring-1 focus:ring-primary/20"
                  placeholder={t('settings.tools.namePlaceholder', 'Tool Name')}
                />
              </div>
              <div className="space-y-1.5">
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={4}
                  className="w-full px-3 py-2 text-sm rounded-md border bg-background resize-none focus:ring-1 focus:ring-primary/20"
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

        {/* RIGHT COLUMN: Request Config (7/12) */}
        <div className="col-span-7 flex flex-col h-full bg-background">
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

          {/* Footer Action Bar */}
          <div className="px-6 py-4 border-t bg-muted/10 flex justify-end gap-3 shrink-0">
            <button
              type="button"
              onClick={onCancel}
              disabled={isSaving}
              className="px-4 py-2 text-sm font-medium rounded-md border bg-background hover:bg-muted transition-colors disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={isSaving || !name || !endpointUrl}
              className="px-6 py-2 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
              {isNew ? t('common.create') : t('common.confirm')}
            </button>
          </div>
        </div>
      </div>
    </form>
  )
}
