import { Plus, Trash2, FileText, Settings2, List } from 'lucide-react'
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
      <div className="p-3 rounded-xl border border-slate-200 bg-slate-50 text-sm font-medium text-slate-500 flex items-center justify-center">
        {t('settings.skills.startNodeSubflowReadonly')}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label icon={<FileText className="w-4 h-4" />}>{t('settings.skills.workflowDescription')}</Label>
        <textarea
          value={workflowDescription}
          onChange={(e) => onWorkflowDescriptionChange(e.target.value)}
          className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none resize-none min-h-[80px] font-mono shadow-sm transition-all"
          placeholder={t('settings.skills.descriptionPlaceholder')}
        />
      </div>

      <div className="h-px bg-slate-200/60" />

      <CommonSegmentedControl
        icon={<Settings2 className="w-4 h-4" />}
        label={t('settings.skills.startInputMode')}
        value={normalized.inputMode}
        onChange={(value) => updateMode(value === 'structured' ? 'structured' : 'text')}
        options={[
          { label: t('settings.skills.startInputModeText'), value: 'text' },
          { label: t('settings.skills.startInputModeStructured'), value: 'structured' },
        ]}
      />

      {normalized.inputMode === 'structured' && (
        <div className="space-y-3 pt-2">
          <div className="flex items-center justify-between">
            <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.structuredInputFields')}</Label>
            <button
              type="button"
              onClick={addField}
              className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2.5 py-1.5 rounded-lg transition-colors border border-primary/10"
            >
              <Plus className="w-3.5 h-3.5" />
              {t('actions.add')}
            </button>
          </div>

          <div className="space-y-2.5">
            {fields.map((field, index) => (
              <div key={`field-${index}`} className="group relative flex flex-col gap-2.5 p-3 rounded-xl border border-slate-200/80 bg-slate-50/50 hover:bg-slate-50 transition-all shadow-sm">
                <div className="flex items-start gap-2 w-full pr-8">
                  <input
                    type="text"
                    value={field.name}
                    onChange={(e) => updateField(index, { name: e.target.value })}
                    className="flex-1 px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all shadow-sm font-mono"
                    placeholder="customer_id"
                  />
                  <div className="relative w-36 shrink-0">
                    <select
                      value={field.type}
                      onChange={(e) => updateField(index, { type: (e.target.value as StartStructuredField['type']) || 'string' })}
                      className="w-full px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all shadow-sm appearance-none cursor-pointer"
                      style={{
                        backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                        backgroundPosition: 'right 0.5rem center',
                        backgroundRepeat: 'no-repeat',
                        backgroundSize: '1.5em 1.5em',
                        paddingRight: '2.5rem'
                      }}
                    >
                      {START_FIELD_TYPE_OPTIONS.map(t => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="w-full pr-8">
                  <input
                    type="text"
                    value={field.description ?? ''}
                    onChange={(e) => updateField(index, { description: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                    placeholder={t('settings.skills.structuredInputFieldDescription')}
                  />
                </div>

                <div className="pt-0.5">
                  <CommonSwitch
                    label={t('settings.skills.structuredInputFieldRequired')}
                    checked={Boolean(field.required)}
                    onChange={(checked) => updateField(index, { required: checked })}
                  />
                </div>

                <button
                  type="button"
                  onClick={() => removeField(index)}
                  className="absolute right-2 top-3 p-1.5 text-slate-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}

            {fields.length === 0 && (
              <div className="text-center py-6 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                {t('settings.skills.structuredInputInvalid')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
