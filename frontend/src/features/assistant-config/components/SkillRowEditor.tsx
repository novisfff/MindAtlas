import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Loader2, Check, X, Plus, RotateCcw, Workflow, Bot, ArrowRight, ChevronDown } from 'lucide-react'
import type { AssistantSkill, CreateSkillRequest, UpdateSkillRequest } from '../api/skills'
import type { AssistantExecutableTarget } from './skillTargetOptions'
import { Popover, PopoverContent, PopoverTrigger } from '../../../components/ui/popover'
import { Badge } from '../../../components/ui/badge'
import { Button } from '@/components/ui/button'
import { uiChrome, uiField } from '@/components/ui/styles'
import { SettingsInset } from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'
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
      className={cn(uiChrome.card, 'space-y-6 p-6')}
    >
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">
          {isNew ? t('settings.skills.addSkill') : t('settings.skills.editSkill')}
        </h3>
        <div className="grid gap-5 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-sm font-medium">
              {t('settings.skills.name')} <span className="text-red-500/80">*</span>
            </label>
            <input
              type="text"
              value={state.name}
              onChange={(e) => actions.setName(e.target.value)}
              required
              className={uiField.input}
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
              className={uiField.input}
              placeholder={t('settings.skills.descriptionPlaceholder')}
            />
          </div>
        </div>
      </div>

      <SettingsInset className="space-y-4">
        <label className="text-sm font-medium">
          {t('settings.skills.bindTarget')} <span className="text-red-500/80">*</span>
        </label>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className={cn(
                  uiChrome.control,
                  'group flex w-full flex-1 items-center justify-between gap-3 px-4 py-3 text-left shadow-none transition-colors hover:bg-muted/55',
                )}
              >
                {selectedTarget ? (
                  <div className="flex min-w-0 items-center gap-2 truncate">
                    <div className="rounded-full bg-primary/10 p-1.5 text-primary">
                      {targetTypeIcon}
                    </div>
                    {selectedTarget.isSystem && (
                      <Badge variant="secondary" className="font-normal text-[10px] px-1.5 py-0">
                        {t('settings.skills.system')}
                      </Badge>
                    )}
                    <span className="truncate text-sm font-medium">{selectedTarget.name}</span>
                  </div>
                ) : (
                  <span className="text-sm text-muted-foreground">{t('settings.skills.noTargetOptions')}</span>
                )}
                <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground opacity-50 transition-opacity group-hover:opacity-100" />
              </button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-[min(520px,calc(100vw-2rem))] p-2">
              <div className="max-h-[320px] overflow-y-auto pr-1">
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
                      }}
                      className={cn(
                        uiChrome.control,
                        'mb-1 flex w-full items-center gap-3 px-3 py-3 text-left shadow-none transition-colors',
                        !target.bindable
                          ? 'cursor-not-allowed opacity-50 grayscale'
                          : 'cursor-pointer hover:bg-muted/60',
                        isSelected ? 'border-primary/20 bg-primary/5' : 'border-transparent bg-transparent',
                      )}
                    >
                      <div className={cn(
                        'rounded-full p-1.5',
                        isSelected ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
                      )}>
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
            <Button
              type="button"
              onClick={handleEditTarget}
              title={t('settings.skills.editTarget')}
              variant="outline"
              size="icon"
              className="h-12 w-12 shrink-0"
            >
              <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </div>

        {selectedTarget?.isSystem && (
          <div className="rounded-[12px] border border-amber-200 bg-amber-50/90 px-3 py-2 text-sm leading-6 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
            {t('settings.skills.systemTargetBindingHint')}
          </div>
        )}
      </SettingsInset>

      <SettingsInset className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          {t('settings.skills.intentExamples')}
        </div>

        <div className="max-h-[220px] space-y-2 overflow-y-auto">
          {state.intentExamples.length === 0 ? (
            <div className="rounded-[12px] border border-dashed border-border/75 bg-background/72 py-6 text-center text-sm text-muted-foreground">
              {t('settings.skills.intentPlaceholder')}
            </div>
          ) : (
            state.intentExamples.map((ex, i) => (
              <div
                key={i}
                className={cn(uiChrome.control, 'group flex items-center justify-between gap-2 px-3 py-2.5 text-sm shadow-none')}
              >
                <span className="font-medium text-foreground">{ex}</span>
                <button
                  type="button"
                  onClick={() => actions.removeIntent(i)}
                  className="rounded-md p-1.5 opacity-0 transition-all group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive"
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
            className={uiField.input}
            placeholder={t('settings.skills.intentExamples')}
          />
          <Button
            type="button"
            onClick={actions.addIntent}
            disabled={!state.newIntent.trim()}
            variant="outline"
            size="icon"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </SettingsInset>

      <div className="flex flex-col gap-3 border-t border-border/70 pt-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          {onReset && (
            <Button
              type="button"
              onClick={onReset}
              variant="outline"
              className="border-amber-200 text-amber-700 hover:bg-amber-50 dark:border-amber-500/20 dark:text-amber-200 dark:hover:bg-amber-500/10"
            >
              <RotateCcw className="h-4 w-4" />
              {t('settings.skills.reset')}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-3">
          <Button type="button" onClick={onCancel} disabled={isSaving} variant="outline">
            {t('common.cancel')}
          </Button>
          <Button type="submit" disabled={isSaving || !isValid} className="min-w-[120px] justify-center">
            {isSaving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Check className="w-4 h-4" />
            )}
            {isNew ? t('common.create') : t('common.save')}
          </Button>
        </div>
      </div>
    </form>
  )
}
