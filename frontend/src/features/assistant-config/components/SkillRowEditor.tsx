import { useTranslation } from 'react-i18next'
import { Loader2, Check, X, Plus, RotateCcw, MessageSquare, Wrench, Bot, ListChecks, BookOpen } from 'lucide-react'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest } from '../api/skills'
import type { AssistantTool } from '../api/tools'
import { useSkillForm } from './useSkillForm'
import { StepEditor } from './SkillStepsEditor'

export interface SkillRowProps {
  skill?: AssistantSkill
  isNew?: boolean
  isEditing?: boolean
  availableTools: AssistantTool[]
  onCancel: () => void
  onReset?: () => void
  onSave: (data: CreateSkillRequest | UpdateSkillRequest) => void
  isSaving: boolean
}

export function SkillRowEditor({
  skill,
  isNew,
  availableTools,
  onCancel,
  onReset,
  onSave,
  isSaving,
}: SkillRowProps) {
  const { t } = useTranslation()
  const { state, derivedTools, isValid, actions, buildSubmitData } = useSkillForm({ skill })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSave(buildSubmitData())
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="p-6 rounded-xl border bg-card shadow-sm space-y-6"
    >
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
              value={state.name}
              onChange={(e) => actions.setName(e.target.value)}
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
              value={state.description}
              onChange={(e) => actions.setDescription(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-lg border bg-background focus:ring-2 focus:ring-primary/20 transition-shadow"
              placeholder={t('settings.skills.descriptionPlaceholder')}
            />
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <label className="text-sm font-medium">{t('settings.skills.mode')}</label>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => actions.setMode('steps')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg border-2 transition-all ${state.mode === 'steps'
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
            onClick={() => actions.setMode('agent')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg border-2 transition-all ${state.mode === 'agent'
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
        {state.mode === 'agent' && (
          <div className="space-y-3 p-4 rounded-lg bg-muted/30 border">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <BookOpen className="w-4 h-4" />
                {t('settings.skills.kbEnabled')}
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={state.kbConfig.enabled}
                onClick={() => actions.setKbConfig({ ...state.kbConfig, enabled: !state.kbConfig.enabled })}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ${
                  state.kbConfig.enabled ? 'bg-primary' : 'bg-input/50'
                }`}
              >
                <span
                  className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                    state.kbConfig.enabled ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
            <p className="text-xs text-muted-foreground">{t('settings.skills.kbEnabledDesc')}</p>
          </div>
        )}

        <div className="space-y-3 p-4 rounded-lg bg-muted/30 border">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <MessageSquare className="w-4 h-4" />
            {t('settings.skills.intentExamples')}
          </div>

          <div className="space-y-2 max-h-[200px] overflow-y-auto custom-scrollbar">
            {state.intentExamples.map((ex, i) => (
              <div
                key={i}
                className="group flex items-center justify-between gap-2 p-2 rounded-md bg-background border text-sm"
              >
                <span>{ex}</span>
                <button
                  type="button"
                  onClick={() => actions.removeIntent(i)}
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
              value={state.newIntent}
              onChange={(e) => actions.setNewIntent(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), actions.addIntent())}
              className="flex-1 px-3 py-2 text-sm rounded-lg border bg-background focus:ring-2 focus:ring-primary/20"
              placeholder={t('settings.skills.intentPlaceholder')}
            />
            <button
              type="button"
              onClick={actions.addIntent}
              disabled={!state.newIntent.trim()}
              className="px-3 py-2 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-50 transition-colors"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="space-y-3 p-4 rounded-lg bg-muted/30 border">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Wrench className="w-4 h-4" />
            {t('settings.skills.tools')}
          </div>

          {state.mode === 'agent' ? (
            <div className="space-y-2 max-h-[200px] overflow-y-auto custom-scrollbar">
              {availableTools.map((tool) => (
                <label
                  key={tool.id}
                  className="flex items-center gap-2 p-2 rounded-md bg-background border text-sm cursor-pointer hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    checked={state.agentTools.includes(tool.name)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        actions.setAgentTools([...state.agentTools, tool.name])
                      } else {
                        actions.setAgentTools(state.agentTools.filter((t) => t !== tool.name))
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

      {state.mode === 'steps' ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">
              {t('settings.skills.stepsDetail')} <span className="text-red-500">*</span>
            </label>
            <button
              type="button"
              onClick={actions.addStep}
              className="text-xs px-3 py-1.5 rounded-lg bg-muted hover:bg-muted/80 font-medium transition-colors flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              {t('settings.skills.addStep')}
            </button>
          </div>
          <div className="space-y-3">
            {state.steps.map((step, i) => (
              <StepEditor
                key={i}
                index={i}
                step={step}
                allSteps={state.steps}
                availableTools={availableTools}
                onChange={(updates) => actions.updateStep(i, updates)}
                onRemove={() => actions.removeStep(i)}
                canRemove={state.steps.length > 1}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <label className="text-sm font-medium">
            {t('settings.skills.systemPrompt')} <span className="text-red-500">*</span>
          </label>
          <textarea
            value={state.systemPrompt}
            onChange={(e) => actions.setSystemPrompt(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background resize-none focus:ring-2 focus:ring-primary/20"
            rows={6}
            placeholder={t('settings.skills.systemPromptPlaceholder')}
          />
          <div className="text-xs text-muted-foreground">
            {t('settings.skills.systemPromptHelpText')}
          </div>
        </div>
      )}

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
            disabled={isSaving || !isValid}
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
