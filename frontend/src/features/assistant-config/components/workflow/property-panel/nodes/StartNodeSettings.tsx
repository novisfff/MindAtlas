import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { StartStructuredField } from '../../../../api/workflow'
import { CommonSelect, CommonSwitch, Label, CommonSegmentedControl } from '../CommonInputs'
import {
  normalizeStartNodeConfig,
  START_FIELD_TYPE_OPTIONS,
  type StartInputMode,
} from '../../startNodeConfig'

interface StartNodeSettingsProps {
  config: Record<string, unknown>
  onUpdate: (updates: Record<string, unknown>) => void
  workflowDescription: string
  onWorkflowDescriptionChange: (value: string) => void
  isSubflowNode: boolean
}

const EMPTY_FIELD: StartStructuredField = {
  name: '',
  type: 'string',
  required: false,
}

export function StartNodeSettings({
  config,
  onUpdate,
  workflowDescription,
  onWorkflowDescriptionChange,
  isSubflowNode,
}: StartNodeSettingsProps) {
  const { t } = useTranslation()
  const normalized = normalizeStartNodeConfig(config)
  const fields = normalized.structuredFields

  const updateMode = (mode: StartInputMode) => {
    onUpdate({
      inputMode: mode,
      structuredFields: mode === 'structured' ? fields : [],
    })
  }

  const updateField = (index: number, patch: Partial<StartStructuredField>) => {
    const next = [...fields]
    const current = next[index] ?? EMPTY_FIELD
    next[index] = { ...current, ...patch }
    onUpdate({ structuredFields: next })
  }

  const addField = () => {
    onUpdate({
      structuredFields: [...fields, { ...EMPTY_FIELD }],
    })
  }

  const removeField = (index: number) => {
    const next = [...fields]
    next.splice(index, 1)
    onUpdate({ structuredFields: next })
  }

  if (isSubflowNode) {
    return (
      <div className="p-3 rounded-md border bg-muted/30 text-xs text-muted-foreground">
        {t('settings.skills.startNodeSubflowReadonly')}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <Label>{t('settings.skills.workflowDescription')}</Label>
        <textarea
          value={workflowDescription}
          onChange={(e) => onWorkflowDescriptionChange(e.target.value)}
          className="w-full px-3 py-2 text-xs rounded-md border bg-background/50 focus:ring-1 focus:ring-primary/20 focus:border-primary/50 outline-none resize-none min-h-[100px] font-mono"
          placeholder={t('settings.skills.descriptionPlaceholder')}
        />
      </div>

      <div className="h-px bg-border/50" />

      <CommonSegmentedControl
        label={t('settings.skills.startInputMode')}
        value={normalized.inputMode}
        onChange={(value) => updateMode(value === 'structured' ? 'structured' : 'text')}
        options={[
          { label: t('settings.skills.startInputModeText'), value: 'text' },
          { label: t('settings.skills.startInputModeStructured'), value: 'structured' },
        ]}
      />

      {normalized.inputMode === 'structured' && (
        <div className="space-y-3 pl-1 mt-4">
          <div className="flex items-center justify-between">
            <Label>{t('settings.skills.structuredInputFields')}</Label>
            <button
              type="button"
              onClick={addField}
              className="flex items-center gap-1 text-[10px] bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1 rounded transition-colors"
            >
              <Plus className="w-3 h-3" />
              {t('actions.add')}
            </button>
          </div>

          <div className="space-y-2">
            {fields.map((field, index) => (
              <div key={`field-${index}`} className="flex items-start gap-2 p-2 rounded-md border bg-card/50">
                <div className="flex-1 space-y-2">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={field.name}
                      onChange={(e) => updateField(index, { name: e.target.value })}
                      className="flex-1 px-2 py-1 text-xs rounded border bg-background"
                      placeholder="customer_id"
                    />
                    <select
                      value={field.type}
                      onChange={(e) => updateField(index, { type: (e.target.value as StartStructuredField['type']) || 'string' })}
                      className="flex-1 px-2 py-1 text-xs rounded border bg-background"
                    >
                      {START_FIELD_TYPE_OPTIONS.map(t => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>
                  </div>

                  <input
                    type="text"
                    value={field.description ?? ''}
                    onChange={(e) => updateField(index, { description: e.target.value })}
                    className="w-full px-2 py-1 text-xs rounded border bg-background"
                    placeholder={t('settings.skills.structuredInputFieldDescription')}
                  />

                  <CommonSwitch
                    label={t('settings.skills.structuredInputFieldRequired')}
                    checked={Boolean(field.required)}
                    onChange={(checked) => updateField(index, { required: checked })}
                  />
                </div>

                <button
                  type="button"
                  onClick={() => removeField(index)}
                  className="p-1 text-muted-foreground hover:text-red-500 rounded hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}

            {fields.length === 0 && (
              <div className="text-center py-4 text-xs text-muted-foreground border border-dashed rounded-md">
                {t('settings.skills.structuredInputInvalid')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
