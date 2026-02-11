import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, X } from 'lucide-react'
import type { SkillStepInput, OutputFieldSpec, OutputFieldType } from '../api/skills'
import type { AssistantTool, InputParam } from '../api/tools'
import { RichMentionInput } from './RichMentionInput'
import { Tooltip } from '@/components/ui/Tooltip'
import { SummaryIcon } from '@/components/icons/SummaryIcon'

export interface StepEditorProps {
  index: number
  step: SkillStepInput
  allSteps: SkillStepInput[]
  availableTools: AssistantTool[]
  onChange: (updates: Partial<SkillStepInput>) => void
  onRemove: () => void
  canRemove: boolean
}

export function StepEditor({
  index,
  step,
  allSteps,
  availableTools,
  onChange,
  onRemove,
  canRemove,
}: StepEditorProps) {
  const { t } = useTranslation()

  const inputParams = useMemo((): InputParam[] => {
    const base: InputParam[] = [
      { name: 'user_input', description: 'User message content', paramType: 'string', required: true },
      { name: 'history', description: 'Conversation history', paramType: 'string', required: true },
      { name: 'last_step_result', description: 'Result of the previous step', paramType: 'string', required: true },
      { name: 'last_step_result_raw', description: 'Parsed JSON result of the previous step (if any)', paramType: 'object', required: false },
    ]

    const prev: InputParam[] = []
    for (let i = 0; i < index; i++) {
      prev.push({
        name: `step_${i + 1}_result`,
        description: `Result of step ${i + 1}`,
        paramType: 'string',
        required: false,
      })
      prev.push({
        name: `step_${i + 1}_result_raw`,
        description: `Parsed JSON result of step ${i + 1} (if any)`,
        paramType: 'object',
        required: false,
      })

      const s = allSteps[i]
      if (s?.type === 'analysis' && (s.outputMode || 'text') === 'json' && Array.isArray(s.outputFields) && s.outputFields.length > 0) {
        s.outputFields.forEach((field) => {
          const fieldName = typeof field === 'string' ? field : field.name
          const f = (fieldName || '').trim()
          if (!f) return
          prev.push({
            name: `step_${i + 1}_${f}`,
            description: `JSON field "${f}" from step ${i + 1}`,
            paramType: 'object',
            required: false,
          })
        })
      }
    }

    return [...base, ...prev]
  }, [allSteps, index])

  const analysisInstructionParams = useMemo((): InputParam[] => {
    return inputParams.filter((p) => p.name !== 'user_input' && p.name !== 'history')
  }, [inputParams])

  return (
    <div className="group relative pl-10 pr-12 py-4 rounded-xl border bg-background/50 hover:bg-background hover:shadow-sm transition-all">
      <div className="absolute left-0 top-0 bottom-0 w-8 flex items-center justify-center border-r bg-muted/30 rounded-l-xl text-xs font-mono text-muted-foreground">
        {index + 1}
      </div>

      <div className="space-y-3">
        <div className="flex gap-3">
          <select
            value={step.type}
            onChange={(e) =>
              onChange({ type: e.target.value as SkillStepInput['type'] })
            }
            className="w-[140px] px-3 py-2 text-sm rounded-lg border bg-background focus:ring-2 focus:ring-primary/20"
          >
            <option value="analysis">{t('settings.skills.stepAnalysis')}</option>
            <option value="tool">{t('settings.skills.stepTool')}</option>
            <option value="summary">{t('settings.skills.stepSummary')}</option>
          </select>

          {step.type !== 'summary' && (
            <Tooltip content={t('settings.skills.includeInSummary')}>
              <button
                type="button"
                onClick={() => onChange({ includeInSummary: !(step.includeInSummary ?? false) })}
                className={`p-2 rounded-md transition-all ${step.includeInSummary
                  ? 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm'
                  : 'bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
              >
                <SummaryIcon className="w-4 h-4" />
              </button>
            </Tooltip>
          )}

          {step.type === 'tool' && (
            <>
              <select
                value={step.toolName || ''}
                onChange={(e) => onChange({ toolName: e.target.value })}
                className="flex-1 px-3 py-2 text-sm rounded-lg border bg-background focus:ring-2 focus:ring-primary/20"
              >
                <option value="">{t('settings.skills.selectTool')}</option>
                {availableTools.map((t) => (
                  <option key={t.id} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
              <select
                value={step.argsFrom || 'context'}
                onChange={(e) =>
                  onChange({ argsFrom: e.target.value as 'context' | 'previous' | 'custom' | 'json' })
                }
                className="w-[160px] px-3 py-2 text-sm rounded-lg border bg-background focus:ring-2 focus:ring-primary/20"
              >
                <option value="context">{t('settings.skills.argsFromContext')}</option>
                <option value="previous">{t('settings.skills.argsFromPrevious')}</option>
                <option value="custom">{t('settings.skills.argsFromCustom')}</option>
                <option value="json">{t('settings.skills.argsFromJson')}</option>
              </select>
            </>
          )}
        </div>

        {step.type === 'tool' && (step.argsFrom === 'custom' || step.argsFrom === 'json') && (
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">
              {t('settings.skills.argsTemplate')}
            </label>
            <RichMentionInput
              value={step.argsTemplate || ''}
              onChange={(val) => onChange({ argsTemplate: val })}
              inputParams={inputParams}
              placeholder={step.argsFrom === 'json' ? '{"keyword": {{user_input}}, "limit": 10}' : t('settings.skills.argsTemplatePlaceholder')}
              className="font-mono text-sm"
              multiline
              rows={3}
            />
            <p className="text-xs text-muted-foreground">
              {step.argsFrom === 'json' ? t('settings.skills.argsTemplateJsonHint') : t('settings.skills.argsTemplateHint')}
            </p>
          </div>
        )}

        {(step.type === 'analysis' || step.type === 'summary') && (
          <>
            {step.type === 'analysis' && (
              <div className="flex flex-col gap-2">
                <div className="flex items-start gap-4 p-3 rounded-lg border bg-muted/30">
                  <div className="flex flex-col gap-1.5 shrink-0">
                    <label className="text-xs font-medium text-muted-foreground">
                      {t('settings.skills.outputMode')}
                    </label>
                    <div className="flex bg-background border rounded-lg p-1 w-fit">
                      {(['text', 'json'] as const).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() =>
                            onChange({
                              outputMode: mode,
                              outputFields: mode === 'json' ? step.outputFields || [] : undefined,
                            })
                          }
                          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${(step.outputMode || 'text') === mode
                            ? 'bg-primary text-primary-foreground shadow-sm'
                            : 'text-muted-foreground hover:bg-muted'
                            }`}
                        >
                          {mode === 'text'
                            ? t('settings.skills.outputModeText')
                            : t('settings.skills.outputModeJson')}
                        </button>
                      ))}
                    </div>
                  </div>

                  {(step.outputMode || 'text') === 'json' && (
                    <div className="flex flex-col gap-1.5 flex-1 min-w-0 border-l pl-4">
                      <div className="flex items-center justify-between">
                        <label className="text-xs font-medium text-muted-foreground">
                          {t('settings.skills.jsonFields')}
                        </label>
                        <span className="text-[10px] text-muted-foreground">
                          {t('settings.skills.jsonFieldsHint').replace(
                            '{{step_N_<field>}}',
                            `{{step_${index + 1}_<field>}}`
                          )}
                        </span>
                      </div>

                      <div className="flex flex-col gap-2 min-h-[32px]">
                        {(step.outputFields || []).map((field, i) => {
                          const spec = typeof field === 'string'
                            ? { name: field, type: 'string' as OutputFieldType, nullable: false }
                            : field
                          return (
                            <div
                              key={i}
                              className="flex items-center gap-2 p-2 rounded bg-background border text-xs group"
                            >
                              <input
                                type="text"
                                value={spec.name}
                                onChange={(e) => {
                                  const newFields = [...(step.outputFields || [])] as OutputFieldSpec[]
                                  newFields[i] = { ...spec, name: e.target.value }
                                  onChange({ outputFields: newFields })
                                }}
                                className="w-24 px-2 py-1 rounded border bg-transparent font-mono focus:ring-1 focus:ring-primary/20"
                                placeholder="name"
                              />
                              <select
                                value={spec.type}
                                onChange={(e) => {
                                  const newFields = [...(step.outputFields || [])] as OutputFieldSpec[]
                                  const newType = e.target.value as OutputFieldType
                                  newFields[i] = {
                                    ...spec,
                                    type: newType,
                                    itemsType: newType === 'array' ? (spec.itemsType || 'string') : undefined
                                  }
                                  onChange({ outputFields: newFields })
                                }}
                                className="w-20 px-1 py-1 rounded border bg-transparent text-xs"
                              >
                                <option value="string">string</option>
                                <option value="number">number</option>
                                <option value="integer">integer</option>
                                <option value="boolean">boolean</option>
                                <option value="array">array</option>
                                <option value="object">object</option>
                              </select>
                              {spec.type === 'array' && (
                                <select
                                  value={spec.itemsType || 'string'}
                                  onChange={(e) => {
                                    const newFields = [...(step.outputFields || [])] as OutputFieldSpec[]
                                    newFields[i] = { ...spec, itemsType: e.target.value as OutputFieldType }
                                    onChange({ outputFields: newFields })
                                  }}
                                  className="w-20 px-1 py-1 rounded border bg-transparent text-xs"
                                >
                                  <option value="string">string</option>
                                  <option value="number">number</option>
                                  <option value="integer">integer</option>
                                  <option value="boolean">boolean</option>
                                  <option value="object">object</option>
                                </select>
                              )}
                              <label className="flex items-center gap-1 text-muted-foreground cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={spec.nullable}
                                  onChange={(e) => {
                                    const newFields = [...(step.outputFields || [])] as OutputFieldSpec[]
                                    newFields[i] = { ...spec, nullable: e.target.checked }
                                    onChange({ outputFields: newFields })
                                  }}
                                  className="w-3 h-3 rounded"
                                />
                                <span className="text-[10px]">null</span>
                              </label>
                              <button
                                type="button"
                                onClick={() => {
                                  const newFields = (step.outputFields || []).filter((_, idx) => idx !== i)
                                  onChange({ outputFields: newFields })
                                }}
                                className="p-1 rounded-sm opacity-50 hover:opacity-100 hover:bg-red-50 hover:text-red-500 transition-all ml-auto"
                              >
                                <X className="w-3 h-3" />
                              </button>
                            </div>
                          )
                        })}

                        <div className="relative group">
                          <div className="absolute inset-y-0 left-0 pl-2 flex items-center pointer-events-none">
                            <Plus className="w-3 h-3 text-muted-foreground group-focus-within:text-primary transition-colors" />
                          </div>
                          <input
                            type="text"
                            className="w-[100px] py-1 pl-6 pr-2 text-xs rounded border bg-transparent hover:bg-background focus:bg-background focus:w-[140px] transition-all outline-none focus:ring-2 focus:ring-primary/20 placeholder:text-muted-foreground/50"
                            placeholder={t('common.add')}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault()
                                const val = e.currentTarget.value.trim()
                                const existingNames = (step.outputFields || []).map(f =>
                                  typeof f === 'string' ? f : f.name
                                )
                                if (val && !existingNames.includes(val)) {
                                  const newField: OutputFieldSpec = {
                                    name: val,
                                    type: 'string',
                                    nullable: false
                                  }
                                  onChange({
                                    outputFields: [...(step.outputFields || []), newField] as OutputFieldSpec[],
                                  })
                                  e.currentTarget.value = ''
                                }
                              }
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {step.type === 'analysis' ? (
              <RichMentionInput
                value={step.instruction || ''}
                onChange={(val) => onChange({ instruction: val })}
                inputParams={analysisInstructionParams}
                placeholder="Type / to insert variables (user_input/history not allowed)"
                className="font-mono text-sm"
                multiline
                rows={3}
              />
            ) : (
              <textarea
                value={step.instruction || ''}
                onChange={(e) => onChange({ instruction: e.target.value })}
                className="w-full px-3 py-2 text-sm rounded-lg border bg-background resize-none focus:ring-2 focus:ring-primary/20"
                rows={2}
                placeholder={t('settings.skills.instructionPlaceholder')}
              />
            )}
          </>
        )}
      </div>

      {canRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="absolute right-2 top-2 p-1.5 rounded-lg opacity-0 group-hover:opacity-100 hover:bg-red-50 text-red-500 transition-all"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}