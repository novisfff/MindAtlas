import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Loader2, Check, X, Plus, RotateCcw, Workflow, Bot, ArrowRight, ChevronDown } from 'lucide-react'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest } from '../api/skills'
import type { AssistantExecutableTarget } from './skillTargetOptions'
import { Popover, PopoverContent, PopoverTrigger } from '../../../components/ui/popover'
import { Badge } from '../../../components/ui/badge'
import { useSkillForm } from './useSkillForm'

export interface SkillRowProps {
  skill?: AssistantSkill
  isNew?: boolean
  isEditing?: boolean
  availableTargets: AssistantExecutableTarget[]
  onCancel: () => void
  onReset?: () => void
  onSave: (data: CreateSkillRequest | UpdateSkillRequest) => void
  isSaving: boolean
}

export function SkillRowEditor({
  skill,
  isNew,
  availableTargets,
  onCancel,
  onReset,
  onSave,
  isSaving,
}: SkillRowProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { state, selectedTarget, isValid, actions, buildSubmitData } = useSkillForm({
    skill,
    availableTargets,
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSave(buildSubmitData())
  }

  const targetTypeLabel = selectedTarget?.type === 'workflow'
    ? t('settings.skills.targetTypeWorkflow')
    : t('settings.skills.targetTypeAgent')

  const targetTypeIcon = selectedTarget?.type === 'workflow'
    ? <Workflow className="w-4 h-4" />
    : <Bot className="w-4 h-4" />

  const handleEditTarget = () => {
    if (!selectedTarget) return
    if (selectedTarget.type === 'workflow') {
      navigate(`/settings/workflow-editor/${selectedTarget.id}`)
      return
    }
    navigate(`/settings/agent-editor/${selectedTarget.id}`)
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="p-6 rounded-2xl border bg-card shadow-[0_4px_16px_rgba(0,0,0,0.03)] space-y-6"
    >
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">
          {isNew ? t('settings.skills.addSkill') : t('settings.skills.editSkill')}
        </h3>
        <div className="grid grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('settings.skills.name')} <span className="text-red-500/80">*</span>
            </label>
            <input
              type="text"
              value={state.name}
              onChange={(e) => actions.setName(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-xl border bg-background focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-sm"
              placeholder="my_custom_skill"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('settings.skills.description')} <span className="text-red-500/80">*</span>
            </label>
            <input
              type="text"
              value={state.description}
              onChange={(e) => actions.setDescription(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-xl border bg-background focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all shadow-sm"
              placeholder={t('settings.skills.descriptionPlaceholder')}
            />
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <label className="text-sm font-medium">
          {t('settings.skills.bindTarget')} <span className="text-red-500/80">*</span>
        </label>

        <div className="flex gap-2 items-stretch">
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="flex-1 flex items-center justify-between w-full px-3 py-2.5 rounded-xl border bg-background hover:bg-muted/30 focus:ring-2 focus:ring-blue-500/20 focus:outline-none transition-all shadow-sm group text-left"
              >
                {selectedTarget ? (
                  <div className="flex items-center gap-2 truncate">
                    <div className="p-1.5 rounded-md bg-primary/5 text-primary">
                      {targetTypeIcon}
                    </div>
                    {selectedTarget.isSystem && (
                      <Badge variant="secondary" className="font-normal text-[10px] px-1.5 py-0">
                        {t('settings.skills.system')}
                      </Badge>
                    )}
                    <span className="text-sm font-medium truncate">{selectedTarget.name}</span>
                  </div>
                ) : (
                  <span className="text-sm text-muted-foreground">{t('settings.skills.noTargetOptions')}</span>
                )}
                <ChevronDown className="w-4 h-4 text-muted-foreground opacity-50 group-hover:opacity-100 transition-opacity flex-shrink-0" />
              </button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-[480px] p-1.5 shadow-xl rounded-xl">
              <div className="max-h-[300px] overflow-y-auto custom-scrollbar flex flex-col gap-1 pr-1">
                {availableTargets.map((target) => {
                  const typeLabel = target.type === 'workflow'
                    ? t('settings.skills.targetTypeWorkflow')
                    : t('settings.skills.targetTypeAgent')

                  const IconComponent = target.type === 'workflow' ? Workflow : Bot
                  const isSelected = state.selectedTargetKey === target.key

                  return (
                    <button
                      key={target.key}
                      type="button"
                      disabled={!target.bindable}
                      title={target.description || ''}
                      onClick={() => {
                        actions.setSelectedTargetKey(target.key)
                        // Trigger click on body to close popover natively if needed, or controlled state
                      }}
                      className={`
                        w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all
                        ${!target.bindable ? 'opacity-50 cursor-not-allowed bg-muted/30 grayscale' : 'hover:bg-accent hover:text-accent-foreground cursor-pointer'}
                        ${isSelected ? 'bg-primary/5 border border-primary/20' : 'border border-transparent'}
                      `}
                    >
                      <div className={`p-1.5 flex-shrink-0 rounded-md ${isSelected ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                        <IconComponent className="w-4 h-4" />
                      </div>

                      <div className="flex flex-col flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          {target.isSystem && (
                            <Badge variant="outline" className="text-[10px] font-normal px-1 py-0 h-4">
                              {t('settings.skills.system')}
                            </Badge>
                          )}
                          <span className="text-sm font-medium truncate">{target.name}</span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[11px] text-muted-foreground uppercase tracking-wider">{typeLabel}</span>
                          {!target.bindable && (
                            <span className="text-[11px] text-red-500 font-medium">
                              • {t('settings.skills.structuredWorkflowSkillBindingBlocked')}
                            </span>
                          )}
                        </div>
                      </div>

                      {isSelected && <Check className="w-4 h-4 text-primary flex-shrink-0" />}
                    </button>
                  )
                })}
              </div>
            </PopoverContent>
          </Popover>

          {selectedTarget && (
            <button
              type="button"
              onClick={handleEditTarget}
              title={t('settings.skills.editTarget')}
              className="flex items-center justify-center px-4 rounded-xl border bg-muted/20 hover:bg-muted/50 hover:border-border text-muted-foreground hover:text-foreground transition-all flex-shrink-0"
            >
              <ArrowRight className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      <div className="space-y-3 p-5 rounded-2xl bg-slate-50 border shadow-inner">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
          {t('settings.skills.intentExamples')}
        </div>

        <div className="space-y-2 max-h-[200px] overflow-y-auto custom-scrollbar">
          {state.intentExamples.length === 0 ? (
            <div className="text-center py-6 border-2 border-dashed border-slate-200 rounded-xl bg-white/50 text-sm text-muted-foreground">
              {t('settings.skills.intentPlaceholder')}
            </div>
          ) : (
            state.intentExamples.map((ex, i) => (
              <div
                key={i}
                className="group flex items-center justify-between gap-2 p-2.5 px-3 rounded-xl bg-white border shadow-sm text-sm hover:border-slate-300 transition-colors"
              >
                <span className="text-slate-700 font-medium">{ex}</span>
                <button
                  type="button"
                  onClick={() => actions.removeIntent(i)}
                  className="opacity-0 group-hover:opacity-100 p-1.5 hover:bg-red-50 hover:text-red-500 rounded-lg transition-all"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))
          )}
        </div>

        <div className="flex gap-2 pt-2">
          <input
            type="text"
            value={state.newIntent}
            onChange={(e) => actions.setNewIntent(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), actions.addIntent())}
            className="flex-1 px-3 py-2 text-sm rounded-xl border bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-shadow shadow-sm"
            placeholder={t('settings.skills.intentExamples')}
          />
          <button
            type="button"
            onClick={actions.addIntent}
            disabled={!state.newIntent.trim()}
            className="px-3 min-w-[44px] flex items-center justify-center rounded-xl bg-blue-50 text-blue-600 border border-blue-100 hover:bg-blue-100 hover:border-blue-200 disabled:opacity-50 disabled:grayscale transition-all shadow-sm"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex items-center justify-between pt-6 border-t">
        <div>
          {onReset && (
            <button
              type="button"
              onClick={onReset}
              className="px-4 py-2.5 text-sm rounded-xl hover:bg-orange-50 text-orange-600 dark:hover:bg-orange-950/30 flex items-center gap-2 transition-colors font-medium border border-transparent hover:border-orange-200"
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
            className="px-5 py-2.5 text-sm rounded-xl border shadow-sm hover:bg-slate-50 transition-colors font-medium"
          >
            {t('common.cancel')}
          </button>
          <button
            type="submit"
            disabled={isSaving || !isValid}
            className="px-6 py-2.5 text-sm rounded-xl bg-blue-600 text-white hover:bg-blue-700 shadow-sm disabled:opacity-50 flex items-center gap-2 transition-all min-w-[120px] justify-center font-medium border border-blue-700"
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
