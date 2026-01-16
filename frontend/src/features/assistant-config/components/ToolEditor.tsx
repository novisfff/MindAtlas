import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Check, X, Plus, Trash2 } from 'lucide-react'
import { KeyValueEditor, type KeyValuePair } from './KeyValueEditor'
import { RichMentionInput } from './RichMentionInput'
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

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']
const AUTH_TYPES: AuthType[] = ['none', 'bearer', 'basic', 'api-key']
const BODY_TYPES: BodyType[] = ['none', 'form-data', 'x-www-form-urlencoded', 'json', 'xml', 'raw']
const PARAM_TYPES = ['string', 'number', 'boolean', 'array', 'object']

type TabType = 'params' | 'body' | 'headers' | 'auth'

export function ToolEditor({ tool, isNew, onCancel, onSave, isSaving, errorMessage }: ToolEditorProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabType>('params')

  // Basic info
  const [name, setName] = useState(tool?.name || '')
  const [description, setDescription] = useState(tool?.description || '')

  // Input params
  const [inputParams, setInputParams] = useState<InputParam[]>(tool?.inputParams || [])

  // Request config
  const [httpMethod, setHttpMethod] = useState(tool?.httpMethod || 'POST')
  const [endpointUrl, setEndpointUrl] = useState(tool?.endpointUrl || '')

  // Use Arrays for editing to support "Add" better
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
  const [authScheme, setAuthScheme] = useState(tool?.authScheme || 'Bearer')
  const [apiKey, setApiKey] = useState('')

  // Other
  const [timeoutSeconds, setTimeoutSeconds] = useState(tool?.timeoutSeconds || 30)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    // Convert arrays back to records
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

  const tabs: { key: TabType; label: string }[] = [
    { key: 'params', label: t('settings.tools.queryParams') },
    { key: 'body', label: t('settings.tools.body') },
    { key: 'headers', label: t('settings.tools.headers') },
    { key: 'auth', label: t('settings.tools.auth') },
  ]

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-[700px] w-full max-w-full bg-background rounded-lg border shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b shrink-0 bg-muted/20">
        <h3 className="font-semibold text-lg">{isNew ? t('common.create') : t('common.edit')}</h3>
        <button
          type="button"
          onClick={onCancel}
          className="text-muted-foreground hover:text-foreground p-1"
        >
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

          {/* Section: Basic Info */}
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

          {/* Section: Input Params */}
          <div className="space-y-4 flex-1">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.inputParams')}</h4>
                <div className="text-muted-foreground hover:text-foreground cursor-help" title="Parameters that the AI will generate and pass to this tool">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></svg>
                </div>
              </div>
              <button
                type="button"
                onClick={addInputParam}
                className="text-xs px-2.5 py-1.5 rounded-md border hover:bg-muted bg-background font-medium flex items-center gap-1 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                {t('common.add')}
              </button>
            </div>

            <div className="space-y-3">
              {inputParams.length === 0 ? (
                <div className="text-sm text-muted-foreground italic text-center py-8 border-2 border-dashed rounded-lg bg-muted/20">
                  {t('settings.tools.noParams', 'No parameters defined')}
                </div>
              ) : (
                <div className="grid gap-3">
                  <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-1 uppercase tracking-wider">
                    <div className="col-span-3">Name</div>
                    <div className="col-span-6">Description</div>
                    <div className="col-span-3">Type</div>
                  </div>
                  {inputParams.map((param, i) => (
                    <div key={i} className="group relative p-3 rounded-lg border bg-background hover:shadow-sm transition-all space-y-2">
                      <div className="grid grid-cols-12 gap-2">
                        <div className="col-span-3">
                          <input
                            type="text"
                            value={param.name}
                            onChange={(e) => updateInputParam(i, { name: e.target.value })}
                            placeholder="key"
                            className="w-full px-2 py-1 text-sm rounded border-b border-transparent focus:border-primary bg-transparent focus:bg-muted/10 font-medium"
                          />
                        </div>
                        <div className="col-span-6">
                          <input
                            type="text"
                            value={param.description || ''}
                            onChange={(e) => updateInputParam(i, { description: e.target.value })}
                            placeholder="desc"
                            className="w-full px-2 py-1 text-sm rounded border-b border-transparent focus:border-primary bg-transparent focus:bg-muted/10 text-muted-foreground"
                          />
                        </div>
                        <div className="col-span-3">
                          <select
                            value={param.paramType}
                            onChange={(e) => updateInputParam(i, { paramType: e.target.value })}
                            className="w-full px-1 py-1 text-xs rounded border-none bg-muted/50 focus:ring-0 cursor-pointer"
                          >
                            {PARAM_TYPES.map((t) => (
                              <option key={t} value={t}>{t}</option>
                            ))}
                          </select>
                        </div>
                      </div>

                      <div className="flex items-center justify-end gap-3 pt-1 border-t border-muted/30">
                        <label className="flex items-center gap-1.5 text-xs cursor-pointer select-none text-muted-foreground hover:text-foreground">
                          <input
                            type="checkbox"
                            checked={param.required}
                            onChange={(e) => updateInputParam(i, { required: e.target.checked })}
                            className="rounded border-gray-300 text-primary focus:ring-primary"
                          />
                          Required
                        </label>
                        <button
                          type="button"
                          onClick={() => removeInputParam(i)}
                          className="text-muted-foreground hover:text-destructive transition-colors p-1"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN: Request Config (7/12) */}
        <div className="col-span-7 flex flex-col h-full bg-background">
          <div className="p-6 space-y-8 flex-1 overflow-y-auto custom-scrollbar">

            {/* Request URL Block */}
            <div className="space-y-4">
              <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.requestConfig')}</h4>

              <div className="flex rounded-md shadow-sm">
                <select
                  value={httpMethod}
                  onChange={(e) => setHttpMethod(e.target.value)}
                  className="rounded-l-md border-r-0 border-input bg-muted/40 px-3 py-2 text-sm font-medium focus:ring-1 focus:ring-primary/20 w-24"
                >
                  {HTTP_METHODS.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <input
                  type="url"
                  value={endpointUrl}
                  onChange={(e) => setEndpointUrl(e.target.value)}
                  required
                  className="flex-1 rounded-r-md border-input bg-background px-4 py-2 text-sm font-mono focus:ring-1 focus:ring-primary/20"
                  placeholder="https://api.example.com/v1/resource"
                />
              </div>
            </div>

            {/* Auth Config */}
            <div className="space-y-4">
              <div className="flex items-center justify-between cursor-pointer" onClick={() => setActiveTab('auth')}>
                <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.authConfig')}</h4>
              </div>

              <div className="p-4 rounded-lg border bg-muted/10 space-y-4">
                <div className="grid grid-cols-1 gap-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t('settings.tools.authType')}</label>
                  <select
                    value={authType}
                    onChange={(e) => setAuthType(e.target.value as AuthType)}
                    className="w-full px-3 py-2 rounded-md border bg-background text-sm"
                  >
                    {AUTH_TYPES.map((at) => (
                      <option key={at} value={at}>{at}</option>
                    ))}
                  </select>
                </div>

                {authType !== 'none' && (
                  <div className="animate-in fade-in slide-in-from-top-1 duration-200 space-y-3">
                    {authType === 'bearer' && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground">Token Value</label>
                        <input
                          type="password"
                          value={apiKey}
                          onChange={(e) => setApiKey(e.target.value)}
                          placeholder="Bearer Token"
                          className="w-full px-3 py-2 rounded-md border bg-background text-sm font-mono"
                        />
                      </div>
                    )}
                    {authType === 'api-key' && (
                      <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-muted-foreground">Key</label>
                          <input
                            type="text"
                            value={authHeaderName}
                            onChange={(e) => setAuthHeaderName(e.target.value)}
                            placeholder="X-API-Key"
                            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
                          />
                        </div>
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-muted-foreground">Value</label>
                          <input
                            type="password"
                            value={apiKey}
                            onChange={(e) => setApiKey(e.target.value)}
                            placeholder="Key Value"
                            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Request Details (Tabs) */}
            <div className="space-y-4">
              <div className="flex border-b gap-6">
                <button
                  type="button"
                  onClick={() => setActiveTab('params')}
                  className={`text-sm font-medium pb-2 border-b-2 transition-colors ${activeTab === 'params' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
                >
                  Params
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('body')}
                  className={`text-sm font-medium pb-2 border-b-2 transition-colors ${activeTab === 'body' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
                >
                  Body
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('headers')}
                  className={`text-sm font-medium pb-2 border-b-2 transition-colors ${activeTab === 'headers' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
                >
                  Headers
                </button>
              </div>

              <div className="min-h-[200px]">
                {activeTab === 'params' && (
                  <KeyValueEditor
                    pairs={queryParams}
                    onChange={setQueryParams}
                    keyPlaceholder="Query Param Key"
                    valuePlaceholder="Value"
                    inputParams={inputParams}
                  />
                )}

                {activeTab === 'headers' && (
                  <KeyValueEditor
                    pairs={headers}
                    onChange={setHeaders}
                    keyPlaceholder="Header Name"
                    valuePlaceholder="Value"
                    inputParams={inputParams}
                  />
                )}

                {activeTab === 'body' && (
                  <div className="space-y-3">
                    <div className="flex items-center gap-1 bg-muted/40 p-1 rounded-md inline-flex">
                      {BODY_TYPES.map((bt) => (
                        <button
                          key={bt}
                          type="button"
                          onClick={() => setBodyType(bt)}
                          className={`px-3 py-1 text-xs rounded font-medium transition-all ${bodyType === bt ? 'bg-background shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
                        >
                          {bt}
                        </button>
                      ))}
                    </div>
                    {bodyType !== 'none' && (
                      <div className="relative">
                        <RichMentionInput
                          value={bodyContent}
                          onChange={setBodyContent}
                          inputParams={inputParams}
                          multiline
                          rows={8}
                          className="w-full font-mono text-sm"
                          placeholder={bodyType === 'json' ? '{\n  "key": "value"\n}' : ''}
                        />
                        <div className="absolute right-2 bottom-2 text-xs text-muted-foreground pointer-events-none">
                          {bodyType.toUpperCase()} Content
                        </div>
                      </div>
                    )}
                    {bodyType === 'none' && (
                      <div className="flex items-center justify-center h-32 border-2 border-dashed rounded-lg bg-muted/10 text-muted-foreground text-sm">
                        No Body Content
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

          </div>

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
    </form >
  )
}
