import { Plus, Trash2, Settings2, FileText, List, MessageSquare } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { CommonOutputList, CommonRichInput, CommonSegmentedControl, CommonSelect, CommonSwitch, Label } from '../CommonInputs'
import type { NodeSettingsProps } from './ToolNodeSettings'

const FIELD_TYPES = [
  { label: 'String', value: 'string' },
  { label: 'Number', value: 'number' },
  { label: 'Integer', value: 'integer' },
  { label: 'Boolean', value: 'boolean' },
  { label: 'Object', value: 'object' },
  { label: 'Array', value: 'array' },
]

const ARRAY_ITEM_TYPES = FIELD_TYPES.filter((item) => item.value !== 'array')

type OutputField = {
  name: string
  type: string
  nullable: boolean
  itemsType?: string
  enum?: string[]
  value: string
}

function normalizeOutputMode(raw: unknown): 'text' | 'structured' {
  const mode = String(raw ?? 'text').trim().toLowerCase()
  return mode === 'structured' || mode === 'json' ? 'structured' : 'text'
}

function normalizeOutputFields(raw: unknown): OutputField[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      name: String(item.name ?? ''),
      type: String(item.type ?? 'string') || 'string',
      nullable: Boolean(item.nullable),
      itemsType: typeof item.itemsType === 'string'
        ? item.itemsType
        : (typeof item.items_type === 'string' ? item.items_type : undefined),
      enum: Array.isArray(item.enum) ? item.enum.map((value) => String(value)).filter(Boolean) : undefined,
      value: String(item.value ?? ''),
    }))
}

export function OutputNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
  const { t } = useTranslation()
  const outputMode = normalizeOutputMode(config.outputMode)
  const outputFields = normalizeOutputFields(config.outputFields)

  const updateOutputFields = (next: OutputField[]) => {
    onUpdate({ outputFields: next })
  }

  const addOutputField = () => {
    updateOutputFields([
      ...outputFields,
      {
        name: 'field',
        type: 'string',
        nullable: false,
        value: '',
      },
    ])
  }

  const removeOutputField = (index: number) => {
    const next = [...outputFields]
    next.splice(index, 1)
    updateOutputFields(next)
  }

  const updateOutputField = (index: number, patch: Partial<OutputField>) => {
    const next = [...outputFields]
    const current = next[index] ?? { name: '', type: 'string', nullable: false, value: '' }
    const merged: OutputField = { ...current, ...patch }
    if (merged.type !== 'array') {
      delete merged.itemsType
    } else if (!merged.itemsType) {
      merged.itemsType = 'string'
    }
    next[index] = merged
    updateOutputFields(next)
  }

  const outputNames = outputMode === 'structured'
    ? outputFields.map((field) => field.name.trim()).filter(Boolean)
    : ['response']

  return (
    <div className="space-y-4">
      <CommonSegmentedControl
        icon={<Settings2 className="w-4 h-4" />}
        label={t('settings.skills.outputMode')}
        value={outputMode}
        onChange={(value) => onUpdate({ outputMode: value })}
        options={[
          { label: t('settings.skills.outputModeText'), value: 'text' },
          { label: t('settings.skills.llmOutputModeStructured'), value: 'structured' },
        ]}
      />

      {outputMode === 'text' ? (
        <CommonRichInput
          icon={<FileText className="w-4 h-4" />}
          label={t('settings.skills.outputTextTemplate')}
          value={String(config.textTemplate ?? '')}
          onChange={(value) => onUpdate({ textTemplate: value })}
          mentionParams={mentionParams}
          placeholder={t('settings.skills.outputTextTemplatePlaceholder') || t('settings.skills.templatePlaceholder')}
          rows={4}
        />
      ) : (
        <div className="space-y-2.5">
          <div className="flex items-center justify-between">
            <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.outputStructuredFields')}</Label>
            <button
              type="button"
              onClick={addOutputField}
              className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2.5 py-1.5 rounded-lg transition-colors border border-primary/10"
            >
              <Plus className="w-3.5 h-3.5" />
              {t('actions.add')}
            </button>
          </div>
          <div className="space-y-2.5">
            {outputFields.map((field, index) => (
              <div key={index} className="relative group flex flex-col gap-2.5 p-3 rounded-xl border border-slate-200/80 bg-slate-50/50 hover:bg-slate-50 transition-all shadow-sm">
                <div className="flex items-start gap-2.5 w-full pr-8">
                  <input
                    type="text"
                    value={field.name}
                    onChange={(event) => updateOutputField(index, { name: event.target.value })}
                    className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] font-mono text-slate-700"
                    placeholder={t('settings.skills.jsonFieldsPlaceholder')}
                  />
                </div>

                <div className="w-full pr-8">
                  <CommonRichInput
                    icon={<FileText className="w-4 h-4" />}
                    label={t('settings.skills.outputFieldValueTemplate')}
                    value={field.value}
                    onChange={(value) => updateOutputField(index, { value })}
                    mentionParams={mentionParams}
                    placeholder={t('settings.skills.templatePlaceholder')}
                    rows={2}
                  />
                </div>

                <div className="flex gap-2.5 w-full pr-8">
                  <div className="flex-1">
                    <CommonSelect
                      value={field.type}
                      onChange={(value) => updateOutputField(index, { type: value })}
                      options={FIELD_TYPES}
                    />
                  </div>
                  {field.type === 'array' && (
                    <div className="flex-1">
                      <CommonSelect
                        value={field.itemsType ?? 'string'}
                        onChange={(value) => updateOutputField(index, { itemsType: value })}
                        options={ARRAY_ITEM_TYPES}
                      />
                    </div>
                  )}
                </div>

                <div className="w-full pr-8">
                  <input
                    type="text"
                    value={Array.isArray(field.enum) ? field.enum.join(', ') : ''}
                    onChange={(event) => updateOutputField(index, {
                      enum: event.target.value
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean),
                    })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                    placeholder={t('settings.skills.outputEnumPlaceholder')}
                  />
                </div>

                <div className="w-full pr-8">
                  <CommonSwitch
                    label={t('settings.skills.outputNullable')}
                    checked={Boolean(field.nullable)}
                    onChange={(checked) => updateOutputField(index, { nullable: checked })}
                  />
                </div>

                <button
                  type="button"
                  onClick={() => removeOutputField(index)}
                  className="absolute right-3 top-4 p-1.5 text-slate-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
            {outputFields.length === 0 && (
              <div className="text-center py-5 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                {t('settings.skills.noParams')}
              </div>
            )}
          </div>
        </div>
      )}

      <CommonOutputList
        icon={<MessageSquare className="w-4 h-4" />}
        label={t('settings.skills.toolOutput')}
        outputs={outputNames}
      />
    </div>
  )
}
