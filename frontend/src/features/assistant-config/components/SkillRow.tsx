import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Check, X, Plus, Trash2, RotateCcw, MessageSquare, Wrench, Bot, ListChecks, FileText, BookOpen } from 'lucide-react'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest, SkillStepInput, SkillMode, SkillKBConfig } from '../api/skills'
import type { AssistantTool, InputParam } from '../api/tools'
import { RichMentionInput } from './RichMentionInput'
import { Tooltip } from '@/components/ui/Tooltip'
import { SummaryIcon } from '@/components/icons/SummaryIcon'

interface SkillRowProps {
  skill?: AssistantSkill
  isNew?: boolean
  isEditing?: boolean
  availableTools: AssistantTool[]
  onCancel: () => void
  onReset?: () => void
  onSave: (data: CreateSkillRequest | UpdateSkillRequest) => void
  isSaving: boolean
}

export function SkillRow({
  skill,
  isNew,
  isEditing,
  availableTools,
  onCancel,
  onReset,
  onSave,
  isSaving,
}: SkillRowProps) {
  const { t } = useTranslation()
  const [name, setName] = useState(skill?.name || '')
  const [description, setDescription] = useState(skill?.description || '')
  const [mode, setMode] = useState<SkillMode>(skill?.mode || 'steps')
  const [systemPrompt, setSystemPrompt] = useState(skill?.systemPrompt || '')
  const [intentExamples, setIntentExamples] = useState<string[]>(
    skill?.intentExamples || []
  )
  // Agent mode: tools are selected directly
  const [agentTools, setAgentTools] = useState<string[]>(
    skill?.mode === 'agent' ? (skill?.tools || []) : []
  )
  // Tools are now derived from steps, no local state needed
  const [steps, setSteps] = useState<SkillStepInput[]>(
    skill?.steps?.map((s) => ({
      type: s.type,
      instruction: s.instruction || undefined,
      toolName: s.toolName || undefined,
      argsFrom: s.argsFrom || undefined,
      argsTemplate: s.argsTemplate || undefined,
      outputMode: s.outputMode || undefined,
      outputFields: s.outputFields || undefined,
      includeInSummary: s.includeInSummary ?? false,
    })) || [{ type: 'analysis', instruction: '' }]
  )

  // KB 配置状态（仅 Agent 模式支持）
  const [kbConfig, setKbConfig] = useState<SkillKBConfig>(
    skill?.kbConfig || { enabled: false }
  )

  const [newIntent, setNewIntent] = useState('')

  // Derive tools from steps automatically
  const derivedTools = useMemo(() => {
    const usedTools = new Set<string>()
    steps.forEach((step) => {
      if (step.type === 'tool' && step.toolName) {
        usedTools.add(step.toolName)
      }
    })
    return Array.from(usedTools)
  }, [steps])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const data: CreateSkillRequest | UpdateSkillRequest = {
      name,
      description,
      intentExamples: intentExamples.length > 0 ? intentExamples : undefined,
      mode,
      ...(mode === 'agent'
        ? {
          tools: agentTools.length > 0 ? agentTools : undefined,
          systemPrompt: systemPrompt || undefined,
          kbConfig,
          steps: undefined,
        }
        : {
          tools: derivedTools.length > 0 ? derivedTools : undefined,
          steps,
          kbConfig,
          systemPrompt: undefined,
        }),
    }
    onSave(data)
  }

  const addIntent = () => {
    if (newIntent.trim()) {
      setIntentExamples([...intentExamples, newIntent.trim()])
      setNewIntent('')
    }
  }

  const removeIntent = (index: number) => {
    setIntentExamples(intentExamples.filter((_, i) => i !== index))
  }

  const addStep = () => {
    setSteps([...steps, { type: 'tool', toolName: '', includeInSummary: false }])
  }

  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index))
  }

  const updateStep = (index: number, updates: Partial<SkillStepInput>) => {
    setSteps(steps.map((s, i) => (i === index ? { ...s, ...updates } : s)))
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="p-6 rounded-xl border bg-card shadow-sm space-y-6"
    >
      {/* Header & Basic Info */}
      <div className="space-y-4">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          {isNew ? t('settings.skills.addSkill') : t('settings.skills.editSkill')}
        </h3>
        <div className="grid grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('settings.skills.name')} <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-lg border bg-background focus:ring-2 focus:ring-primary/20 transition-shadow"
              placeholder="my_custom_skill"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('settings.skills.description')} <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-lg border bg-background focus:ring-2 focus:ring-primary/20 transition-shadow"
              placeholder={t('settings.skills.descriptionPlaceholder')}
            />
          </div>
        </div>
      </div>

      {/* Mode Selector */}
      <div className="space-y-3">
        <label className="text-sm font-medium">{t('settings.skills.mode')}</label>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setMode('steps')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg border-2 transition-all ${mode === 'steps'
              ? 'border-primary bg-primary/5 text-primary'
              : 'border-muted hover:border-muted-foreground/30'
              }`}
          >
            <ListChecks className="w-5 h-5" />
            <div className="text-left">
              <div className="font-medium">{t('settings.skills.modeSteps')}</div>
              <div className="text-xs text-muted-foreground">{t('settings.skills.modeStepsDesc')}</div>
            </div>
          </button>
          <button
            type="button"
            onClick={() => setMode('agent')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg border-2 transition-all ${mode === 'agent'
              ? 'border-primary bg-primary/5 text-primary'
              : 'border-muted hover:border-muted-foreground/30'
              }`}
          >
            <Bot className="w-5 h-5" />
            <div className="text-left">
              <div className="font-medium">{t('settings.skills.modeAgent')}</div>
              <div className="text-xs text-muted-foreground">{t('settings.skills.modeAgentDesc')}</div>
            </div>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Knowledge Base Configuration - Agent 模式专属 */}
        {mode === 'agent' && (
          <div className="space-y-3 p-4 rounded-lg bg-muted/30 border">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <BookOpen className="w-4 h-4" />
                {t('settings.skills.kbEnabled')}
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={kbConfig.enabled}
                onClick={() => setKbConfig({ ...kbConfig, enabled: !kbConfig.enabled })}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ${
                  kbConfig.enabled ? 'bg-primary' : 'bg-input/50'
                }`}
              >
                <span
                  className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                    kbConfig.enabled ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
            <p className="text-xs text-muted-foreground">{t('settings.skills.kbEnabledDesc')}</p>
          </div>
        )}

        {/* Intent Examples */}
        <div className="space-y-3 p-4 rounded-lg bg-muted/30 border">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <MessageSquare className="w-4 h-4" />
            {t('settings.skills.intentExamples')}
          </div>

          <div className="space-y-2 max-h-[200px] overflow-y-auto custom-scrollbar">
            {intentExamples.map((ex, i) => (
              <div
                key={i}
                className="group flex items-center justify-between gap-2 p-2 rounded-md bg-background border text-sm"
              >
                <span>{ex}</span>
                <button
                  type="button"
                  onClick={() => removeIntent(i)}
                  className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-50 hover:text-red-500 rounded transition-all"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>

          <div className="flex gap-2 pt-2">
            <input
              type="text"
              value={newIntent}
              onChange={(e) => setNewIntent(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addIntent())}
              className="flex-1 px-3 py-2 text-sm rounded-lg border bg-background focus:ring-2 focus:ring-primary/20"
              placeholder={t('settings.skills.intentPlaceholder')}
            />
            <button
              type="button"
              onClick={addIntent}
              disabled={!newIntent.trim()}
              className="px-3 py-2 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-50 transition-colors"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Tools - different UI based on mode */}
        <div className="space-y-3 p-4 rounded-lg bg-muted/30 border">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Wrench className="w-4 h-4" />
            {t('settings.skills.tools')}
          </div>

          {mode === 'agent' ? (
            /* Agent mode: multi-select tools */
            <div className="space-y-2 max-h-[200px] overflow-y-auto custom-scrollbar">
              {availableTools.map((tool) => (
                <label
                  key={tool.id}
                  className="flex items-center gap-2 p-2 rounded-md bg-background border text-sm cursor-pointer hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    checked={agentTools.includes(tool.name)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setAgentTools([...agentTools, tool.name])
                      } else {
                        setAgentTools(agentTools.filter((t) => t !== tool.name))
                      }
                    }}
                    className="rounded"
                  />
                  <Wrench className="w-3 h-3 text-muted-foreground" />
                  <span>{tool.name}</span>
                </label>
              ))}
            </div>
          ) : (
            /* Steps mode: derived from steps */
            <>
              <div className="space-y-2 max-h-[200px] overflow-y-auto custom-scrollbar">
                {derivedTools.map((tool, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 p-2 rounded-md bg-background border text-sm"
                  >
                    <Wrench className="w-3 h-3 text-muted-foreground" />
                    <span>{tool}</span>
                  </div>
                ))}
                {derivedTools.length === 0 && (
                  <div className="text-sm text-muted-foreground italic px-2">
                    {t('settings.skills.noToolsSelected')}
                  </div>
                )}
              </div>
              <div className="text-xs text-muted-foreground px-2">
                {t('settings.skills.toolsHelpText')}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Mode-specific configuration */}
      {mode === 'steps' ? (
        /* Steps mode: step editor */
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">
              {t('settings.skills.stepsDetail')} <span className="text-red-500">*</span>
            </label>
            <button
              type="button"
              onClick={addStep}
              className="text-xs px-3 py-1.5 rounded-lg bg-muted hover:bg-muted/80 font-medium transition-colors flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              {t('settings.skills.addStep')}
            </button>
          </div>
          <div className="space-y-3">
            {steps.map((step, i) => (
              <StepEditor
                key={i}
                index={i}
                step={step}
                allSteps={steps}
                availableTools={availableTools}
                onChange={(updates) => updateStep(i, updates)}
                onRemove={() => removeStep(i)}
                canRemove={steps.length > 1}
              />
            ))}
          </div>
        </div>
      ) : (
        /* Agent mode: system prompt editor */
        <div className="space-y-3">
          <label className="text-sm font-medium">
            {t('settings.skills.systemPrompt')} <span className="text-red-500">*</span>
          </label>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background resize-none focus:ring-2 focus:ring-primary/20"
            rows={6}
            placeholder={t('settings.skills.systemPromptPlaceholder')}
          />
          <div className="text-xs text-muted-foreground">
            {t('settings.skills.systemPromptHelpText')}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-4 border-t">
        <div>
          {onReset && (
            <button
              type="button"
              onClick={onReset}
              className="px-4 py-2 text-sm rounded-lg hover:bg-orange-50 text-orange-600 dark:hover:bg-orange-950/30 flex items-center gap-2 transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
              {t('settings.skills.reset')}
            </button>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSaving}
            className="px-4 py-2 text-sm rounded-lg border hover:bg-muted transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            type="submit"
            disabled={
              isSaving ||
              !name ||
              !description ||
              (mode === 'steps' && steps.length === 0) ||
              (mode === 'agent' && !systemPrompt.trim())
            }
            className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2 transition-colors min-w-[100px] justify-center"
          >
            {isSaving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Check className="w-4 h-4" />
            )}
            {isNew ? t('common.create') : t('common.save')}
          </button>
        </div>
      </div>
    </form>
  )
}

interface StepEditorProps {
  index: number
  step: SkillStepInput
  allSteps: SkillStepInput[]
  availableTools: AssistantTool[]
  onChange: (updates: Partial<SkillStepInput>) => void
  onRemove: () => void
  canRemove: boolean
}

function StepEditor({ index, step, allSteps, availableTools, onChange, onRemove, canRemove }: StepEditorProps) {
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
          const f = (field || '').trim()
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
    // analysis instruction 支持变量引用，但不允许 user_input/history 这类“用户输入变量”
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

          {/* Include in Summary Switch */}
          {/* Include in Summary Switch - Hide for Summary step */}
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
                  {/* Output Mode Selection */}
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

                  {/* JSON Fields Configuration */}
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

                      <div className="flex flex-wrap gap-2 items-center min-h-[32px]">
                        {(step.outputFields || []).map((field, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-1 pl-2 pr-1 py-1 rounded bg-background border text-xs font-mono text-primary group"
                          >
                            <span>{field}</span>
                            <button
                              type="button"
                              onClick={() => {
                                const newFields = (step.outputFields || []).filter(
                                  (_, idx) => idx !== i
                                )
                                onChange({ outputFields: newFields })
                              }}
                              className="p-0.5 rounded-sm opacity-50 text-muted-foreground hover:opacity-100 hover:bg-red-50 hover:text-red-500 transition-all"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        ))}

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
                                if (val && !(step.outputFields || []).includes(val)) {
                                  onChange({
                                    outputFields: [...(step.outputFields || []), val],
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
