import { Plus, Trash2 } from 'lucide-react'
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
    <div className="space-y-5">
      <CommonSegmentedControl
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
          label={t('settings.skills.outputTextTemplate')}
          value={String(config.textTemplate ?? '')}
          onChange={(value) => onUpdate({ textTemplate: value })}
          mentionParams={mentionParams}
          placeholder={t('settings.skills.outputTextTemplatePlaceholder') || t('settings.skills.templatePlaceholder')}
          rows={4}
        />
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>{t('settings.skills.outputStructuredFields')}</Label>
            <button
              type="button"
              onClick={addOutputField}
              className="flex items-center gap-1 text-[10px] bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1 rounded transition-colors"
            >
              <Plus className="w-3 h-3" />
              {t('actions.add')}
            </button>
          </div>
          <div className="space-y-2">
            {outputFields.map((field, index) => (
              <div key={index} className="space-y-2 p-2 rounded-md border bg-card/50">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={field.name}
                    onChange={(event) => updateOutputField(index, { name: event.target.value })}
                    className="flex-1 px-2 py-1 text-xs rounded border bg-background"
                    placeholder={t('settings.skills.jsonFieldsPlaceholder')}
                  />
                  <button
                    type="button"
                    onClick={() => removeOutputField(index)}
                    className="text-muted-foreground hover:text-red-500 p-1 rounded hover:bg-red-50 transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>

                <CommonRichInput
                  label={t('settings.skills.outputFieldValueTemplate')}
                  value={field.value}
                  onChange={(value) => updateOutputField(index, { value })}
                  mentionParams={mentionParams}
                  placeholder={t('settings.skills.templatePlaceholder')}
                  rows={2}
                />

                <div className="flex gap-2">
                  <CommonSelect
                    value={field.type}
                    onChange={(value) => updateOutputField(index, { type: value })}
                    options={FIELD_TYPES}
                    className="flex-1"
                  />
                  {field.type === 'array' && (
                    <CommonSelect
                      value={field.itemsType ?? 'string'}
                      onChange={(value) => updateOutputField(index, { itemsType: value })}
                      options={ARRAY_ITEM_TYPES}
                      className="flex-1"
                    />
                  )}
                </div>

                <input
                  type="text"
                  value={Array.isArray(field.enum) ? field.enum.join(', ') : ''}
                  onChange={(event) => updateOutputField(index, {
                    enum: event.target.value
                      .split(',')
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })}
                  className="w-full px-2 py-1 text-xs rounded border bg-background"
                  placeholder={t('settings.skills.outputEnumPlaceholder')}
                />

                <CommonSwitch
                  label={t('settings.skills.outputNullable')}
                  checked={Boolean(field.nullable)}
                  onChange={(checked) => updateOutputField(index, { nullable: checked })}
                />
              </div>
            ))}
            {outputFields.length === 0 && (
              <div className="text-center py-4 text-xs text-muted-foreground border border-dashed rounded-md">
                {t('settings.skills.noParams')}
              </div>
            )}
          </div>
        </div>
      )}

      <CommonOutputList
        label={t('settings.skills.toolOutput')}
        outputs={outputNames}
      />
    </div>
  )
}
