import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyValueEditor, type KeyValuePair } from './KeyValueEditor'
import { RichMentionInput } from './RichMentionInput'
import type { AuthType, BodyType, InputParam } from '../api/tools'

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']
const AUTH_TYPES: AuthType[] = ['none', 'bearer', 'basic', 'api-key']
const BODY_TYPES: BodyType[] = ['none', 'form-data', 'x-www-form-urlencoded', 'json', 'xml', 'raw']

type TabType = 'params' | 'body' | 'headers'

export interface ToolRequestConfigProps {
  httpMethod: string
  endpointUrl: string
  authType: AuthType
  authHeaderName: string
  apiKey: string
  bodyType: BodyType
  bodyContent: string
  queryParams: KeyValuePair[]
  headers: KeyValuePair[]
  inputParams: InputParam[]
  onHttpMethodChange: (method: string) => void
  onEndpointUrlChange: (url: string) => void
  onAuthTypeChange: (type: AuthType) => void
  onAuthHeaderNameChange: (name: string) => void
  onApiKeyChange: (key: string) => void
  onBodyTypeChange: (type: BodyType) => void
  onBodyContentChange: (content: string) => void
  onQueryParamsChange: (params: KeyValuePair[]) => void
  onHeadersChange: (headers: KeyValuePair[]) => void
}

export function ToolRequestConfig({
  httpMethod,
  endpointUrl,
  authType,
  authHeaderName,
  apiKey,
  bodyType,
  bodyContent,
  queryParams,
  headers,
  inputParams,
  onHttpMethodChange,
  onEndpointUrlChange,
  onAuthTypeChange,
  onAuthHeaderNameChange,
  onApiKeyChange,
  onBodyTypeChange,
  onBodyContentChange,
  onQueryParamsChange,
  onHeadersChange,
}: ToolRequestConfigProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabType>('params')

  return (
    <div className="p-6 space-y-8 flex-1 overflow-y-auto custom-scrollbar">
      {/* Request URL Block */}
      <div className="space-y-4">
        <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.requestConfig')}</h4>
        <div className="flex rounded-md shadow-sm">
          <select
            value={httpMethod}
            onChange={(e) => onHttpMethodChange(e.target.value)}
            className="rounded-l-md border-r-0 border-input bg-muted/40 px-3 py-2 text-sm font-medium focus:ring-1 focus:ring-primary/20 w-24"
          >
            {HTTP_METHODS.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <input
            type="url"
            value={endpointUrl}
            onChange={(e) => onEndpointUrlChange(e.target.value)}
            required
            className="flex-1 rounded-r-md border-input bg-background px-4 py-2 text-sm font-mono focus:ring-1 focus:ring-primary/20"
            placeholder="https://api.example.com/v1/resource"
          />
        </div>
      </div>

      {/* Auth Config */}
      <div className="space-y-4">
        <h4 className="font-medium text-sm text-foreground/80">{t('settings.tools.authConfig')}</h4>
        <div className="p-4 rounded-lg border bg-muted/10 space-y-4">
          <div className="grid grid-cols-1 gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t('settings.tools.authType')}</label>
            <select
              value={authType}
              onChange={(e) => onAuthTypeChange(e.target.value as AuthType)}
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
                    onChange={(e) => onApiKeyChange(e.target.value)}
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
                      onChange={(e) => onAuthHeaderNameChange(e.target.value)}
                      placeholder="X-API-Key"
                      className="w-full px-3 py-2 rounded-md border bg-background text-sm"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">Value</label>
                    <input
                      type="password"
                      value={apiKey}
                      onChange={(e) => onApiKeyChange(e.target.value)}
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
          {(['params', 'body', 'headers'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`text-sm font-medium pb-2 border-b-2 transition-colors ${activeTab === tab ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
            >
              {tab === 'params' ? 'Params' : tab === 'body' ? 'Body' : 'Headers'}
            </button>
          ))}
        </div>

        <div className="min-h-[200px]">
          {activeTab === 'params' && (
            <KeyValueEditor
              pairs={queryParams}
              onChange={onQueryParamsChange}
              keyPlaceholder="Query Param Key"
              valuePlaceholder="Value"
              inputParams={inputParams}
            />
          )}

          {activeTab === 'headers' && (
            <KeyValueEditor
              pairs={headers}
              onChange={onHeadersChange}
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
                    onClick={() => onBodyTypeChange(bt)}
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
                    onChange={onBodyContentChange}
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
  )
}
