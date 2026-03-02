import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2, Globe, Link2, Shield, Clock3, RotateCcw, ListChecks, FileText } from 'lucide-react'
import type { InputParam } from '../../../../api/tools'
import { RichMentionInput } from '../../../RichMentionInput'
import { CommonOutputList, CommonSelect, CommonSwitch, Label, CommonRichInput } from '../CommonInputs'
import type { NodeSettingsProps } from './ToolNodeSettings'

type HttpKeyValueRow = {
  key: string
  value: string
  type: 'text' | 'file'
  enabled: boolean
}

function normalizeRows(raw: unknown): HttpKeyValueRow[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      key: String(item.key ?? '').trim(),
      value: String(item.value ?? ''),
      type: String(item.type ?? 'text').trim().toLowerCase() === 'file' ? 'file' : 'text',
      enabled: typeof item.enabled === 'boolean' ? item.enabled : true,
    }))
}

function KeyValueRowsEditor({
  label,
  rows,
  mentionParams,
  keyPlaceholder,
  valuePlaceholder,
  onChange,
  showTypeSelector = false,
}: {
  label: string
  rows: HttpKeyValueRow[]
  mentionParams: InputParam[]
  keyPlaceholder: string
  valuePlaceholder: string
  onChange: (rows: HttpKeyValueRow[]) => void
  showTypeSelector?: boolean
}) {
  const { t } = useTranslation()

  const updateRow = (index: number, patch: Partial<HttpKeyValueRow>) => {
    const next = [...rows]
    if (index === rows.length) {
      if (!patch.key?.trim() && !patch.value?.trim()) return
      next.push({ key: '', value: '', type: 'text', enabled: true, ...patch })
    } else {
      next[index] = { ...next[index], ...patch }
    }
    onChange(next)
  }

  const removeRow = (index: number) => {
    const next = [...rows]
    next.splice(index, 1)
    onChange(next)
  }

  const displayRows = [...rows, { key: '', value: '', type: 'text' as const, enabled: true }]

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between mb-1">
        <Label icon={<ListChecks className="w-4 h-4" />}>{label}</Label>
      </div>
      <div className="rounded-xl border border-slate-200 overflow-hidden bg-white shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)]">
        <div className="flex items-center bg-slate-50 border-b border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600">
          <div className={`${showTypeSelector ? 'flex-[2.8]' : 'flex-[3]'}`}>{t('settings.skills.httpRequest.key', '键')}</div>
          <div className="w-px h-3 bg-slate-200 mx-2" />
          {showTypeSelector && (
            <>
              <div className="w-[100px]">{t('settings.skills.httpRequest.formDataType', '类型')}</div>
              <div className="w-px h-3 bg-slate-200 mx-2" />
            </>
          )}
          <div className="flex-[4]">{t('settings.skills.httpRequest.value', '值')}</div>
        </div>
        <div className="flex flex-col">
          {displayRows.map((row, index) => {
            const isLast = index === displayRows.length - 1
            return (
              <div
                key={index}
                className={`flex items-stretch group hover:bg-slate-50/50 transition-colors relative ${isLast ? '' : 'border-b border-slate-100'}`}
              >
                <div className={`${showTypeSelector ? 'flex-[2.8]' : 'flex-[3]'} min-w-0`}>
                  <input
                    type="text"
                    value={row.key}
                    onChange={(event) => updateRow(index, { key: event.target.value })}
                    placeholder={keyPlaceholder}
                    className="w-full px-3 py-2 text-sm bg-transparent outline-none text-slate-700 font-mono"
                  />
                </div>
                <div className="w-px bg-slate-200" />
                {showTypeSelector && (
                  <>
                    <div className="w-[100px] px-2 py-1.5">
                      <select
                        value={row.type}
                        onChange={(event) => updateRow(index, { type: event.target.value === 'file' ? 'file' : 'text' })}
                        className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-700 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50"
                      >
                        <option value="text">{t('settings.skills.httpRequest.formDataTypeText', 'text')}</option>
                        <option value="file">{t('settings.skills.httpRequest.formDataTypeFile', 'file')}</option>
                      </select>
                    </div>
                    <div className="w-px bg-slate-200" />
                  </>
                )}
                <div className="flex-[4] min-w-0 relative flex items-center">
                  <RichMentionInput
                    value={row.value}
                    onChange={(value) => updateRow(index, { value })}
                    inputParams={mentionParams}
                    placeholder={valuePlaceholder}
                    inputClassName="w-full text-sm border-0 bg-transparent focus:ring-0 focus:outline-none text-slate-700 min-h-[36px] px-3 py-2 whitespace-nowrap overflow-x-auto scrolbar-hide flex flex-wrap"
                  />
                </div>
                {!isLast && (
                  <button
                    type="button"
                    onClick={() => removeRow(index)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-red-500 rounded-md hover:bg-red-50 transition-all"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export function HttpRequestNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
  const { t } = useTranslation()
  const normalizedMentionParams = Array.isArray(mentionParams) ? (mentionParams as InputParam[]) : []

  const method = String(config.method ?? 'GET').trim().toUpperCase() || 'GET'
  const url = String(config.url ?? '')
  const bodyType = String(config.bodyType ?? 'none').trim().toLowerCase() || 'none'
  const authType = String(config.authType ?? 'none').trim().toLowerCase() || 'none'
  const apiKeyIn = String(config.apiKeyIn ?? 'header').trim().toLowerCase() || 'header'
  const timeoutMs = Number.isFinite(Number(config.timeoutMs)) ? String(config.timeoutMs) : '15000'
  const retryEnabled = Boolean(config.retryEnabled)
  const maxRetries = Number.isFinite(Number(config.maxRetries)) ? String(config.maxRetries) : '2'
  const retryIntervalMs = Number.isFinite(Number(config.retryIntervalMs)) ? String(config.retryIntervalMs) : '200'
  const verifySsl = config.verifySsl === undefined ? true : Boolean(config.verifySsl)

  const headerRows = useMemo(() => normalizeRows(config.headers), [config.headers])
  const queryRows = useMemo(
    () => normalizeRows(config.queryParams ?? config.query_params),
    [config.queryParams, config.query_params],
  )
  const formRows = useMemo(
    () => normalizeRows(config.formBody ?? config.form_body),
    [config.formBody, config.form_body],
  )

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2.5">
        <CommonSelect
          className="w-1/3 shrink-0"
          icon={<Globe className="w-4 h-4" />}
          label={t('settings.skills.httpRequest.method')}
          value={method}
          onChange={(value) => onUpdate({ method: value || 'GET' })}
          options={[
            { label: 'GET', value: 'GET' },
            { label: 'POST', value: 'POST' },
            { label: 'PUT', value: 'PUT' },
            { label: 'PATCH', value: 'PATCH' },
            { label: 'DELETE', value: 'DELETE' },
          ]}
        />
        <div className="flex-1 relative min-w-0">
          <CommonRichInput
            icon={<Link2 className="w-4 h-4" />}
            label={t('settings.skills.httpRequest.url')}
            value={url}
            onChange={(value) => onUpdate({ url: value })}
            mentionParams={normalizedMentionParams}
            placeholder={t('settings.skills.httpRequest.urlPlaceholder')}
            rows={1}
          />
        </div>
      </div>

      <KeyValueRowsEditor
        label={t('settings.skills.httpRequest.headers')}
        rows={headerRows}
        mentionParams={normalizedMentionParams}
        keyPlaceholder={t('settings.skills.httpRequest.keyPlaceholder')}
        valuePlaceholder={t('settings.skills.httpRequest.valuePlaceholder')}
        onChange={(rows) => onUpdate({ headers: rows })}
      />

      <KeyValueRowsEditor
        label={t('settings.skills.httpRequest.queryParams')}
        rows={queryRows}
        mentionParams={normalizedMentionParams}
        keyPlaceholder={t('settings.skills.httpRequest.keyPlaceholder')}
        valuePlaceholder={t('settings.skills.httpRequest.valuePlaceholder')}
        onChange={(rows) => onUpdate({ queryParams: rows })}
      />

      <CommonSelect
        icon={<FileText className="w-4 h-4" />}
        label={t('settings.skills.httpRequest.bodyType')}
        value={bodyType}
        onChange={(value) => onUpdate({ bodyType: value || 'none' })}
        options={[
          { label: t('settings.skills.httpRequest.bodyTypeNone'), value: 'none' },
          { label: t('settings.skills.httpRequest.bodyTypeFormData'), value: 'form-data' },
          { label: t('settings.skills.httpRequest.bodyTypeJson'), value: 'json' },
          { label: t('settings.skills.httpRequest.bodyTypeRaw'), value: 'raw' },
          { label: t('settings.skills.httpRequest.bodyTypeFormUrlencoded'), value: 'x-www-form-urlencoded' },
        ]}
      />

      {bodyType === 'json' && (
        <CommonRichInput
          icon={<FileText className="w-4 h-4" />}
          label={t('settings.skills.httpRequest.jsonBodyTemplate')}
          value={String(config.jsonBodyTemplate ?? '')}
          onChange={(value) => onUpdate({ jsonBodyTemplate: value })}
          mentionParams={normalizedMentionParams}
          placeholder={t('settings.skills.httpRequest.jsonBodyPlaceholder')}
          rows={4}
        />
      )}

      {bodyType === 'raw' && (
        <CommonRichInput
          icon={<FileText className="w-4 h-4" />}
          label={t('settings.skills.httpRequest.rawBodyTemplate')}
          value={String(config.rawBodyTemplate ?? '')}
          onChange={(value) => onUpdate({ rawBodyTemplate: value })}
          mentionParams={normalizedMentionParams}
          placeholder={t('settings.skills.httpRequest.rawBodyPlaceholder')}
          rows={4}
        />
      )}

      {bodyType === 'form-data' && (
        <KeyValueRowsEditor
          label={t('settings.skills.httpRequest.formDataBody')}
          rows={formRows}
          mentionParams={normalizedMentionParams}
          keyPlaceholder={t('settings.skills.httpRequest.keyPlaceholder')}
          valuePlaceholder={t('settings.skills.httpRequest.valuePlaceholder')}
          onChange={(rows) => onUpdate({ formBody: rows })}
          showTypeSelector
        />
      )}

      {bodyType === 'x-www-form-urlencoded' && (
        <KeyValueRowsEditor
          label={t('settings.skills.httpRequest.formBody')}
          rows={formRows}
          mentionParams={normalizedMentionParams}
          keyPlaceholder={t('settings.skills.httpRequest.keyPlaceholder')}
          valuePlaceholder={t('settings.skills.httpRequest.valuePlaceholder')}
          onChange={(rows) => onUpdate({ formBody: rows })}
        />
      )}

      <CommonSelect
        icon={<Shield className="w-4 h-4" />}
        label={t('settings.skills.httpRequest.authType')}
        value={authType}
        onChange={(value) => onUpdate({ authType: value || 'none' })}
        options={[
          { label: t('settings.skills.httpRequest.authTypeNone'), value: 'none' },
          { label: t('settings.skills.httpRequest.authTypeBearer'), value: 'bearer' },
          { label: t('settings.skills.httpRequest.authTypeApiKey'), value: 'api_key' },
        ]}
      />

      {authType === 'bearer' && (
        <CommonRichInput
          icon={<Shield className="w-4 h-4" />}
          label={t('settings.skills.httpRequest.bearerToken')}
          value={String(config.bearerToken ?? '')}
          onChange={(value) => onUpdate({ bearerToken: value })}
          mentionParams={normalizedMentionParams}
          placeholder={t('settings.skills.httpRequest.bearerTokenPlaceholder')}
          rows={1}
        />
      )}

      {authType === 'api_key' && (
        <div className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2.5">
            <CommonSelect
              icon={<Shield className="w-4 h-4" />}
              label={t('settings.skills.httpRequest.apiKeyIn')}
              value={apiKeyIn}
              onChange={(value) => onUpdate({ apiKeyIn: value || 'header' })}
              options={[
                { label: t('settings.skills.httpRequest.apiKeyInHeader'), value: 'header' },
                { label: t('settings.skills.httpRequest.apiKeyInQuery'), value: 'query' },
              ]}
            />
            <div className="space-y-1.5">
              <Label icon={<Shield className="w-4 h-4" />}>{t('settings.skills.httpRequest.apiKeyName')}</Label>
              <input
                type="text"
                value={String(config.apiKeyName ?? 'X-API-Key')}
                onChange={(event) => onUpdate({ apiKeyName: event.target.value })}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                placeholder="X-API-Key"
              />
            </div>
          </div>
          <div className="relative">
            <CommonRichInput
              icon={<Shield className="w-4 h-4" />}
              label={t('settings.skills.httpRequest.apiKeyValue')}
              value={String(config.apiKeyValue ?? '')}
              onChange={(value) => onUpdate({ apiKeyValue: value })}
              mentionParams={normalizedMentionParams}
              placeholder={t('settings.skills.httpRequest.apiKeyValuePlaceholder')}
              rows={1}
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2.5">
        <div className="space-y-1.5">
          <Label icon={<Clock3 className="w-4 h-4" />}>{t('settings.skills.httpRequest.timeoutMs')}</Label>
          <input
            type="number"
            min={1}
            max={60000}
            value={timeoutMs}
            onChange={(event) => {
              const text = event.target.value.trim()
              if (!text) {
                onUpdate({ timeoutMs: undefined })
                return
              }
              const parsed = Number.parseInt(text, 10)
              if (Number.isNaN(parsed)) return
              onUpdate({ timeoutMs: Math.max(1, Math.min(60000, parsed)) })
            }}
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
          />
        </div>
        <div className="space-y-1.5">
          <Label icon={<RotateCcw className="w-4 h-4" />}>{t('settings.skills.httpRequest.maxRetries')}</Label>
          <input
            type="number"
            min={0}
            max={5}
            value={maxRetries}
            onChange={(event) => {
              const text = event.target.value.trim()
              if (!text) {
                onUpdate({ maxRetries: undefined })
                return
              }
              const parsed = Number.parseInt(text, 10)
              if (Number.isNaN(parsed)) return
              onUpdate({ maxRetries: Math.max(0, Math.min(5, parsed)) })
            }}
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
            disabled={!retryEnabled}
          />
        </div>
      </div>

      <div className="space-y-2.5">
        <CommonSwitch
          icon={<Shield className="w-4 h-4" />}
          label={t('settings.skills.httpRequest.verifySsl')}
          checked={verifySsl}
          onChange={(checked) => onUpdate({ verifySsl: checked })}
        />
        <CommonSwitch
          icon={<RotateCcw className="w-4 h-4" />}
          label={t('settings.skills.httpRequest.retryEnabled')}
          checked={retryEnabled}
          onChange={(checked) => onUpdate({ retryEnabled: checked })}
        />
      </div>

      {retryEnabled && (
        <div className="space-y-1.5">
          <Label icon={<RotateCcw className="w-4 h-4" />}>{t('settings.skills.httpRequest.retryIntervalMs')}</Label>
          <input
            type="number"
            min={0}
            max={5000}
            value={retryIntervalMs}
            onChange={(event) => {
              const text = event.target.value.trim()
              if (!text) {
                onUpdate({ retryIntervalMs: undefined })
                return
              }
              const parsed = Number.parseInt(text, 10)
              if (Number.isNaN(parsed)) return
              onUpdate({ retryIntervalMs: Math.max(0, Math.min(5000, parsed)) })
            }}
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
          />
        </div>
      )}

      <CommonOutputList
        icon={<FileText className="w-4 h-4" />}
        label={t('settings.skills.httpRequest.fixedOutputs')}
        outputs={['body', 'status_code', 'headers', 'ok', 'error_message', 'response']}
      />
    </div>
  )
}
