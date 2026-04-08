import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'
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
    <div className="space-y-6">
      <div className="space-y-4">
        <h4 className="text-sm font-semibold text-foreground">{t('settings.tools.requestConfig')}</h4>
        <div className={cn(uiChrome.inset, 'space-y-4 p-4')}>
          <div className="grid gap-3 sm:grid-cols-[120px_minmax(0,1fr)]">
          <select
            value={httpMethod}
            onChange={(e) => onHttpMethodChange(e.target.value)}
              className={uiField.select}
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
              className={cn(uiField.input, 'font-mono')}
            placeholder="https://api.example.com/v1/resource"
          />
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <h4 className="text-sm font-semibold text-foreground">{t('settings.tools.authConfig')}</h4>
        <div className={cn(uiChrome.inset, 'space-y-4 p-4')}>
          <div className="grid grid-cols-1 gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t('settings.tools.authType')}</label>
            <select
              value={authType}
              onChange={(e) => onAuthTypeChange(e.target.value as AuthType)}
              className={uiField.select}
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
                    className={cn(uiField.input, 'font-mono')}
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
                      className={uiField.input}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">Value</label>
                    <input
                      type="password"
                      value={apiKey}
                      onChange={(e) => onApiKeyChange(e.target.value)}
                      placeholder="Key Value"
                      className={cn(uiField.input, 'font-mono')}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {(['params', 'body', 'headers'] as const).map((tab) => (
            <Button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              variant={activeTab === tab ? 'secondary' : 'ghost'}
              size="sm"
            >
              {tab === 'params' ? 'Params' : tab === 'body' ? 'Body' : 'Headers'}
            </Button>
          ))}
        </div>

        <div className={cn(uiChrome.inset, 'min-h-[220px] p-4')}>
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
              <div className={cn(uiChrome.control, 'inline-flex items-center gap-1 p-1 shadow-none')}>
                {BODY_TYPES.map((bt) => (
                  <Button
                    key={bt}
                    type="button"
                    onClick={() => onBodyTypeChange(bt)}
                    variant={bodyType === bt ? 'secondary' : 'ghost'}
                    size="sm"
                    className="capitalize"
                  >
                    {bt}
                  </Button>
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
                    inputClassName={cn(uiField.textarea, 'min-h-[220px] font-mono')}
                    placeholder={bodyType === 'json' ? '{\n  "key": "value"\n}' : ''}
                  />
                  <div className="absolute right-2 bottom-2 text-xs text-muted-foreground pointer-events-none">
                    {bodyType.toUpperCase()} Content
                  </div>
                </div>
              )}
              {bodyType === 'none' && (
                <div className="flex h-32 items-center justify-center rounded-[12px] border border-dashed border-border/75 bg-background/72 text-sm text-muted-foreground">
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
