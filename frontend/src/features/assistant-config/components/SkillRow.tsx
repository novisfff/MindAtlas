import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Check, X, Plus, Trash2, RotateCcw, MessageSquare, Wrench, Bot, ListChecks } from 'lucide-react'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest, SkillStepInput, SkillMode } from '../api/skills'
import type { AssistantTool, InputParam } from '../api/tools'
import { RichMentionInput } from './RichMentionInput'

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
    })) || [{ type: 'analysis', instruction: '' }]
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
          steps: undefined,
        }
        : {
          tools: derivedTools.length > 0 ? derivedTools : undefined,
          steps,
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
    setSteps([...steps, { type: 'tool', toolName: '' }])
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
  availableTools: AssistantTool[]
  onChange: (updates: Partial<SkillStepInput>) => void
  onRemove: () => void
  canRemove: boolean
}

function StepEditor({ index, step, availableTools, onChange, onRemove, canRemove }: StepEditorProps) {
  const { t } = useTranslation()

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
              inputParams={[
                { name: 'user_input', description: 'User message content', paramType: 'string', required: true },
                { name: 'history', description: 'Conversation history', paramType: 'string', required: true },
                { name: 'last_step_result', description: 'Result of the previous step', paramType: 'string', required: true },
                ...Array.from({ length: index }).map((_, i) => ({
                  name: `step_${i + 1}_result`,
                  description: `Result of step ${i + 1}`,
                  paramType: 'string' as const,
                  required: true
                }))
              ]}
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
          <textarea
            value={step.instruction || ''}
            onChange={(e) => onChange({ instruction: e.target.value })}
            className="w-full px-3 py-2 text-sm rounded-lg border bg-background resize-none focus:ring-2 focus:ring-primary/20"
            rows={2}
            placeholder={t('settings.skills.instructionPlaceholder')}
          />
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
