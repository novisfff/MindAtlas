import { useMemo, useState } from 'react'
import {
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ExternalLink,
  Loader2,
  Plus,
  RotateCcw,
  Workflow,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import type { SystemBehavior } from '../api/system-behaviors'
import {
  useAgentProfilesQuery,
  useCreateSystemBehaviorExampleWorkflowMutation,
  useResetAllSystemBehaviorsMutation,
  useResetSystemBehaviorBindingMutation,
  useSystemBehaviorsQuery,
  useUpdateSystemBehaviorBindingMutation,
  useWorkflowsQuery,
} from '../queries'
import {
  buildSystemBehaviorBindingTargets,
  resolveSkillTargetKey,
  SYSTEM_DEFAULT_TARGET_KEY,
  type AssistantExecutableTarget,
} from '../components/skillTargetOptions'
import { ResetDangerConfirmDialog } from '../components/ResetDangerConfirmDialog'

const BEHAVIOR_LOCALE_KEY: Record<SystemBehavior['behaviorKey'], string> = {
  weekly_report_generation: 'weeklyReportGeneration',
  monthly_report_generation: 'monthlyReportGeneration',
}

function formatFieldType(
  field: { type: string; itemsType?: string | null },
) {
  if (field.type === 'array') {
    return `${field.itemsType ?? 'string'}[]`
  }
  return field.type
}

function orderSystemBehaviorTargets(targets: AssistantExecutableTarget[]): AssistantExecutableTarget[] {
  const alias = targets.find((target) => target.key === SYSTEM_DEFAULT_TARGET_KEY)
  const remaining = targets.filter((target) => target.key !== SYSTEM_DEFAULT_TARGET_KEY)
  const bindableWorkflows = remaining.filter((target) => target.type === 'workflow' && target.bindable)
  const bindableAgents = remaining.filter((target) => target.type === 'agent' && target.bindable)
  const disabledTargets = remaining.filter((target) => !target.bindable)
  return [
    ...(alias ? [alias] : []),
    ...bindableWorkflows,
    ...bindableAgents,
    ...disabledTargets,
  ]
}

export function SystemAiBehaviorsSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [expandedBehaviorKey, setExpandedBehaviorKey] = useState<SystemBehavior['behaviorKey'] | null>(null)
  const [pickerOpenBehaviorKey, setPickerOpenBehaviorKey] = useState<SystemBehavior['behaviorKey'] | null>(null)
  const [showResetAllConfirm, setShowResetAllConfirm] = useState(false)
  const [resetBehaviorPrompt, setResetBehaviorPrompt] = useState<{
    behaviorKey: SystemBehavior['behaviorKey']
    behaviorName: string
  } | null>(null)
  const [createdExamplePrompt, setCreatedExamplePrompt] = useState<{
    behaviorKey: SystemBehavior['behaviorKey']
    behaviorName: string
    workflowId: string
    workflowName: string
  } | null>(null)
  const { data: workflows = [], isLoading: isLoadingWorkflows } = useWorkflowsQuery()
  const { data: agents = [], isLoading: isLoadingAgents } = useAgentProfilesQuery()
  const { data: behaviors = [], isLoading: isLoadingBehaviors } = useSystemBehaviorsQuery()
  const updateBindingMutation = useUpdateSystemBehaviorBindingMutation()
  const resetBindingMutation = useResetSystemBehaviorBindingMutation()
  const resetAllMutation = useResetAllSystemBehaviorsMutation()
  const createExampleWorkflowMutation = useCreateSystemBehaviorExampleWorkflowMutation()

  const targetsByBehavior = useMemo(() => new Map(
    behaviors.map((behavior) => [
      behavior.behaviorKey,
      buildSystemBehaviorBindingTargets(
        workflows,
        agents,
        {
          defaultTargetType: behavior.canonicalDefaultTarget.targetType,
          defaultTargetId: behavior.canonicalDefaultTarget.id,
        },
      ),
    ]),
  ), [agents, behaviors, workflows])

  const loading = isLoadingBehaviors || isLoadingWorkflows || isLoadingAgents

  const targetTypeLabel = (type: 'workflow' | 'agent') => (
    type === 'workflow'
      ? t('settings.skills.targetTypeWorkflow')
      : t('settings.skills.targetTypeAgent')
  )

  const disabledReasonLabel = (target: AssistantExecutableTarget) => {
    if (target.disabledReason === 'unstructured_workflow') {
      return t('settings.systemBehaviors.disabledReasons.unstructuredWorkflow')
    }
    if (target.disabledReason === 'unpublished_target') {
      return t('settings.systemBehaviors.disabledReasons.unpublishedTarget')
    }
    return t('settings.systemBehaviors.disabledReasons.unavailableTarget')
  }

  const openTarget = (targetType: 'workflow' | 'agent', id: string) => {
    if (targetType === 'workflow') {
      navigate(`/settings/workflow-editor/${id}`)
      return
    }
    navigate(`/settings/agent-editor/${id}`)
  }

  const toggleBehavior = (behaviorKey: SystemBehavior['behaviorKey']) => {
    setExpandedBehaviorKey((current) => current === behaviorKey ? null : behaviorKey)
  }

  const dismissCreatedExamplePrompt = () => {
    if (updateBindingMutation.isPending) return
    if (createdExamplePrompt) {
      toast.success(t('settings.systemBehaviors.exampleWorkflowCreated'))
    }
    setCreatedExamplePrompt(null)
  }

  const handleSelectTarget = async (behavior: SystemBehavior, target: AssistantExecutableTarget) => {
    try {
      if (target.key === SYSTEM_DEFAULT_TARGET_KEY) {
        await resetBindingMutation.mutateAsync(behavior.behaviorKey)
        setPickerOpenBehaviorKey(null)
        toast.success(t('settings.systemBehaviors.resetSuccess'))
        return
      }

      if (!target.bindable) {
        toast.error(disabledReasonLabel(target))
        return
      }

      if (target.type === 'workflow') {
        await updateBindingMutation.mutateAsync({
          behaviorKey: behavior.behaviorKey,
          data: {
            targetType: 'workflow',
            workflowId: target.id,
          },
        })
      } else {
        await updateBindingMutation.mutateAsync({
          behaviorKey: behavior.behaviorKey,
          data: {
            targetType: 'agent',
            agentProfileId: target.id,
          },
        })
      }
      setPickerOpenBehaviorKey(null)
      toast.success(t('settings.systemBehaviors.bindingUpdated'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  const handleReset = async (behaviorKey: string) => {
    try {
      await resetBindingMutation.mutateAsync(behaviorKey)
      setResetBehaviorPrompt(null)
      toast.success(t('settings.systemBehaviors.resetSuccess'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  const handleResetAll = async () => {
    try {
      await resetAllMutation.mutateAsync()
      setShowResetAllConfirm(false)
      toast.success(t('settings.systemBehaviors.resetAllSuccess'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('settings.systemBehaviors.resetAllError')
      toast.error(message)
    }
  }

  const handleCreateExampleWorkflow = async (behavior: SystemBehavior) => {
    try {
      const payload = await createExampleWorkflowMutation.mutateAsync({
        behaviorKey: behavior.behaviorKey,
        data: {
          bindToBehavior: false,
        },
      })
      setExpandedBehaviorKey(behavior.behaviorKey)
      setPickerOpenBehaviorKey(null)
      setCreatedExamplePrompt({
        behaviorKey: behavior.behaviorKey,
        behaviorName: t(`settings.systemBehaviors.behaviors.${BEHAVIOR_LOCALE_KEY[behavior.behaviorKey]}.title`, {
          defaultValue: behavior.name,
        }),
        workflowId: payload.createdWorkflow.id,
        workflowName: payload.createdWorkflow.name,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  const handleBindCreatedExampleWorkflow = async () => {
    if (!createdExamplePrompt) return

    try {
      await updateBindingMutation.mutateAsync({
        behaviorKey: createdExamplePrompt.behaviorKey,
        data: {
          targetType: 'workflow',
          workflowId: createdExamplePrompt.workflowId,
        },
      })
      setExpandedBehaviorKey(createdExamplePrompt.behaviorKey)
      setCreatedExamplePrompt(null)
      toast.success(t('settings.systemBehaviors.exampleWorkflowCreatedAndBound'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/settings')}
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
            >
              <ArrowLeft className="h-5 w-5" />
            </button>
            <h1 className="text-3xl font-bold tracking-tight">
              {t('pages.settings.systemAiBehaviors')}
            </h1>
          </div>
          <p className="max-w-3xl text-base text-muted-foreground">
            {t('pages.settings.systemAiBehaviorsDesc')}
          </p>
        </div>
        {behaviors.length > 0 && (
          <button
            type="button"
            onClick={() => setShowResetAllConfirm(true)}
            disabled={loading || resetAllMutation.isPending}
            className="group flex items-center gap-1.5 self-start px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-orange-600 disabled:opacity-50"
            title={t('settings.systemBehaviors.resetAll')}
          >
            {resetAllMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5 transition-transform duration-500 group-hover:rotate-180" />
            )}
            {t('settings.systemBehaviors.resetAll')}
          </button>
        )}
      </div>

      {loading ? (
        <div className="flex min-h-[220px] flex-col items-center justify-center gap-4 text-muted-foreground">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm">{t('messages.loading')}</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {behaviors.map((behavior) => {
            const localeKey = BEHAVIOR_LOCALE_KEY[behavior.behaviorKey]
            const availableTargets = targetsByBehavior.get(behavior.behaviorKey) ?? []
            const orderedTargets = orderSystemBehaviorTargets(availableTargets)
            const currentTargetKey = resolveSkillTargetKey(
              {
                targetType: behavior.currentBinding.targetType,
                workflowId: behavior.currentBinding.workflowId ?? null,
                agentProfileId: behavior.currentBinding.agentProfileId ?? null,
              },
              availableTargets,
            ) ?? ''
            const currentTarget = orderedTargets.find((target) => target.key === currentTargetKey) ?? null
            const isMutating = (
              updateBindingMutation.isPending
              || resetBindingMutation.isPending
              || createExampleWorkflowMutation.isPending
            )
            const isExpanded = expandedBehaviorKey === behavior.behaviorKey
            const resetDisabled = isMutating || behavior.currentBinding.isCanonicalDefault
            const pickerOpen = pickerOpenBehaviorKey === behavior.behaviorKey

            return (
              <section
                key={behavior.behaviorKey}
                className={cn(
                  "overflow-hidden rounded-[24px] border bg-card transition-all duration-300",
                  isExpanded ? "shadow-md ring-1 ring-border/50" : "shadow-sm hover:shadow-md hover:border-primary/20",
                )}
              >
                <button
                  type="button"
                  onClick={() => toggleBehavior(behavior.behaviorKey)}
                  className="w-full text-left transition-colors hover:bg-muted/30"
                >
                  <div className="flex items-start justify-between gap-4 p-5 sm:p-6 sm:pb-5">
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <h2 className="text-[1.1rem] font-semibold tracking-tight text-foreground">
                          {t(`settings.systemBehaviors.behaviors.${localeKey}.title`, {
                            defaultValue: behavior.name,
                          })}
                        </h2>
                        {behavior.currentBinding.isCanonicalDefault && (
                          <span className="inline-flex items-center rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-medium text-amber-600 dark:bg-amber-500/20 dark:text-amber-400">
                            {t('settings.skills.systemDefaultTarget')}
                          </span>
                        )}
                        <span className="inline-flex items-center rounded-full bg-muted/60 px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground border">
                          {targetTypeLabel(behavior.currentBinding.targetType)}
                        </span>
                      </div>

                      <p className="max-w-3xl line-clamp-2 text-[13px] leading-relaxed text-muted-foreground">
                        {t(`settings.systemBehaviors.behaviors.${localeKey}.description`, {
                          defaultValue: behavior.description,
                        })}
                      </p>

                      {!isExpanded && (
                        <div className="flex flex-wrap gap-2 pt-1.5 animate-in fade-in slide-in-from-top-1 duration-200">
                          <div className="inline-flex max-w-full items-center gap-2 rounded-full border bg-muted/10 px-3 py-1 text-xs shadow-sm">
                            <span className="text-muted-foreground/80">
                              {t('settings.systemBehaviors.currentBinding')}
                            </span>
                            <span
                              className="max-w-[300px] truncate font-medium text-foreground"
                              title={behavior.currentBinding.name}
                            >
                              {behavior.currentBinding.name}
                            </span>
                          </div>
                        </div>
                      )}
                    </div>

                    <div className={cn(
                      "flex shrink-0 items-center justify-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors md:px-3.5",
                      isExpanded ? "bg-muted/40 text-foreground border-border/80" : "bg-background text-muted-foreground hover:bg-muted/60"
                    )}>
                      <span className="hidden md:inline">{t(isExpanded ? 'actions.collapse' : 'actions.expand')}</span>
                      <ChevronDown
                        className={cn(
                          'h-3.5 w-3.5 transition-transform duration-300',
                          isExpanded ? 'rotate-180' : '',
                        )}
                      />
                    </div>
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t bg-slate-50/40 p-5 sm:p-6 animate-in slide-in-from-top-2 duration-300 dark:bg-muted/5">
                    <div className="space-y-5">
                      {/* Box 1: Current Binding */}
                      <div className="rounded-[20px] border border-border/60 bg-background p-5 shadow-sm transition-all hover:shadow-md">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex items-center gap-2 text-[13px] font-medium text-muted-foreground">
                            <span className="h-4 w-1 rounded-full bg-primary/40"></span>
                            {t('settings.systemBehaviors.currentBinding')}
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge variant="secondary" className="rounded-md font-medium px-2 py-0 border shadow-sm bg-muted/40 text-muted-foreground">
                              {targetTypeLabel(behavior.currentBinding.targetType)}
                            </Badge>
                          </div>
                        </div>
                        <div className="mt-4 flex min-h-[56px] items-center rounded-2xl border border-muted-foreground/15 bg-muted/10 px-4 py-3 shadow-inner">
                          <div className="truncate text-base font-semibold tracking-tight text-foreground" title={behavior.currentBinding.name}>
                            {behavior.currentBinding.name}
                          </div>
                        </div>
                      </div>

                      {/* Box 2: Settings */}
                      <aside className="rounded-[20px] border border-border/60 bg-background p-5 shadow-sm transition-all hover:shadow-md">
                        <div className="flex flex-col gap-6">
                          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div className="space-y-1.5">
                              <div className="text-[15px] font-bold text-foreground">
                                {t('settings.systemBehaviors.bindingSettings')}
                              </div>
                              <p className="text-[13px] text-muted-foreground max-w-xl leading-relaxed">
                                {t('settings.systemBehaviors.bindingSettingsDesc')}
                                <br/>
                                {t('settings.systemBehaviors.createExampleWorkflowHint')}
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() => handleCreateExampleWorkflow(behavior)}
                              disabled={isMutating}
                              className="inline-flex h-[36px] shrink-0 items-center justify-center gap-1.5 rounded-lg border border-border bg-background px-3 text-[13px] font-medium shadow-sm transition-all hover:bg-muted hover:text-foreground disabled:opacity-50"
                            >
                              {createExampleWorkflowMutation.isPending ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Plus className="h-3.5 w-3.5" />
                              )}
                              {t('settings.systemBehaviors.createExampleWorkflow')}
                            </button>
                          </div>

                          <div className="space-y-3">
                            <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-muted-foreground">
                              {t('settings.systemBehaviors.bindingTarget')}
                            </div>

                            <Popover
                              open={pickerOpen}
                              onOpenChange={(open) => setPickerOpenBehaviorKey(open ? behavior.behaviorKey : null)}
                            >
                              <PopoverTrigger asChild>
                                <button
                                  type="button"
                                  disabled={isMutating}
                                  className="group flex min-h-[72px] w-full items-center justify-between gap-4 rounded-2xl border border-border/80 bg-background px-4 py-2.5 text-left shadow-sm transition-all hover:border-primary/40 hover:bg-muted/20 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
                                >
                                  <div className="flex items-center gap-4 min-w-0">
                                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-muted/50 border border-border/50 text-muted-foreground group-hover:bg-primary/5 group-hover:text-primary transition-colors">
                                      {(currentTarget?.type ?? behavior.currentBinding.targetType) === 'workflow' ? (
                                        <Workflow className="h-5 w-5" />
                                      ) : (
                                        <Bot className="h-5 w-5" />
                                      )}
                                    </div>

                                    <div className="min-w-0 space-y-0.5">
                                      <div className="flex items-center gap-2">
                                        <span className="text-[12px] font-medium text-muted-foreground group-hover:text-primary/70 transition-colors">
                                          {targetTypeLabel(currentTarget?.type ?? behavior.currentBinding.targetType)}
                                        </span>
                                      </div>
                                      <div
                                        className="truncate text-[15px] font-semibold text-foreground group-hover:text-primary transition-colors"
                                        title={currentTarget?.name ?? behavior.currentBinding.name}
                                      >
                                        {currentTarget?.name ?? behavior.currentBinding.name}
                                      </div>
                                    </div>
                                  </div>

                                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm transition-all group-hover:bg-muted/50 group-hover:border-border">
                                    <ChevronDown className="h-4 w-4 transition-transform group-data-[state=open]:rotate-180" />
                                  </div>
                                </button>
                              </PopoverTrigger>
                              <PopoverContent
                                align="start"
                                sideOffset={8}
                                className="w-[min(560px,calc(100vw-3rem))] rounded-2xl p-2 shadow-xl"
                              >
                                <div className="border-b px-3 pb-3 pt-2">
                                  <div className="text-[14px] font-semibold text-foreground">
                                    {t('settings.systemBehaviors.targetPickerTitle')}
                                  </div>
                                  <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
                                    {t('settings.systemBehaviors.targetPickerHint')}
                                  </p>
                                </div>
                                <div className="mt-2 max-h-[400px] space-y-1 overflow-y-auto pr-1">
                                  {orderedTargets.map((target) => {
                                    const isSelected = target.key === currentTargetKey
                                    const Icon = target.type === 'workflow' ? Workflow : Bot
                                    return (
                                      <button
                                        key={target.key}
                                        type="button"
                                        disabled={!target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY}
                                        onClick={() => handleSelectTarget(behavior, target)}
                                        className={cn(
                                          'flex w-full items-start gap-4 rounded-xl border px-3 py-3 text-left transition-all',
                                          isSelected
                                            ? 'border-primary/25 bg-primary/5 shadow-sm'
                                            : 'border-transparent bg-background hover:bg-muted/40',
                                          !target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY
                                            ? 'cursor-not-allowed opacity-50 grayscale'
                                            : 'cursor-pointer',
                                        )}
                                      >
                                        <div className={cn(
                                          'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] border',
                                          isSelected ? 'border-primary/20 bg-primary/10 text-primary' : 'border-transparent bg-muted/60 text-muted-foreground',
                                        )}>
                                          <Icon className="h-4 w-4" />
                                        </div>

                                        <div className="min-w-0 flex-1">
                                          <div className="flex flex-wrap items-center gap-2">
                                            <span
                                              className="truncate text-[14px] font-medium text-foreground"
                                              title={target.name}
                                            >
                                              {target.name}
                                            </span>
                                            <Badge variant="outline" className="font-normal text-[10px] uppercase tracking-wide leading-none px-1.5 py-0.5 opacity-80">
                                              {targetTypeLabel(target.type)}
                                            </Badge>
                                            {target.key === SYSTEM_DEFAULT_TARGET_KEY && (
                                              <Badge variant="secondary" className="font-normal text-[10px] uppercase tracking-wide leading-none px-1.5 py-0.5 bg-amber-500/10 text-amber-600 border border-amber-500/20">
                                                {t('settings.skills.systemDefaultTarget')}
                                              </Badge>
                                            )}
                                          </div>

                                          <div className="mt-1 text-[12px] leading-snug text-muted-foreground/80">
                                            {!target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY
                                              ? disabledReasonLabel(target)
                                              : target.description || t('settings.systemBehaviors.targetReady')}
                                          </div>
                                        </div>

                                        {isSelected && (
                                          <Check className="mt-1.5 h-4 w-4 shrink-0 text-primary" />
                                        )}
                                      </button>
                                    )
                                  })}
                                </div>
                              </PopoverContent>
                            </Popover>
                          </div>

                          <div className="grid gap-3 sm:grid-cols-2">
                            <button
                              type="button"
                              onClick={() => openTarget(behavior.currentBinding.targetType, behavior.currentBinding.id)}
                              className="group inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border/80 bg-background px-4 text-[13px] font-medium text-muted-foreground shadow-sm transition-all hover:bg-muted/40 hover:text-foreground hover:shadow-md"
                            >
                              <ExternalLink className="h-4 w-4 transition-transform group-hover:scale-110" />
                              {t('settings.systemBehaviors.editTarget')}
                            </button>

                            <button
                              onClick={() => setResetBehaviorPrompt({
                                behaviorKey: behavior.behaviorKey,
                                behaviorName: t(`settings.systemBehaviors.behaviors.${localeKey}.title`, {
                                  defaultValue: behavior.name,
                                }),
                              })}
                              disabled={resetDisabled}
                              className="group inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border/80 bg-background px-4 text-[13px] font-medium text-foreground shadow-sm transition-all hover:bg-muted/40 hover:shadow-md disabled:opacity-50"
                            >
                              <RotateCcw className={cn(
                                "h-4 w-4 transition-all duration-300",
                                !resetDisabled && "group-hover:-rotate-180"
                              )} />
                              {t('settings.systemBehaviors.resetToDefault')}
                            </button>
                          </div>
                        </div>
                      </aside>

                      {/* Box 3: Contract Summary */}
                      <div className="rounded-[20px] border border-border/60 bg-background p-5 shadow-sm transition-all hover:shadow-md">
                        <div className="flex flex-col gap-1.5">
                          <div className="flex items-center gap-2 text-[15px] font-bold text-foreground">
                            {t('settings.systemBehaviors.contractSummary')}
                          </div>
                          <p className="text-[13px] text-muted-foreground leading-relaxed">
                            {t('settings.systemBehaviors.contractHint')}
                          </p>
                        </div>

                        <div className="mt-5 grid gap-4 md:grid-cols-2">
                          <div className="rounded-xl border border-muted/50 bg-slate-50/50 p-4 dark:bg-muted/10">
                            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.2em] text-muted-foreground mb-3">
                              <span className="w-1.5 h-1.5 rounded-full bg-blue-500/60"></span>
                              {t('settings.systemBehaviors.contractInput')}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {behavior.contract.inputFields.map((field) => (
                                <span
                                  key={field.name}
                                  className="inline-flex items-center rounded-md border border-border/50 bg-background px-2.5 py-1 text-[12px] font-medium text-foreground shadow-sm"
                                >
                                  {field.name}
                                  <span className="ml-1.5 text-[11px] text-muted-foreground/70 font-mono scale-90">
                                    {formatFieldType(field)}
                                  </span>
                                </span>
                              ))}
                            </div>
                          </div>

                          <div className="rounded-xl border border-muted/50 bg-slate-50/50 p-4 dark:bg-muted/10">
                            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.2em] text-muted-foreground mb-3">
                              <span className="w-1.5 h-1.5 rounded-full bg-green-500/60"></span>
                              {t('settings.systemBehaviors.contractOutput')}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {behavior.contract.outputFields.map((field) => (
                                <span
                                  key={field.name}
                                  className="inline-flex items-center rounded-md border border-border/50 bg-background px-2.5 py-1 text-[12px] font-medium text-foreground shadow-sm"
                                >
                                  {field.name}
                                  <span className="ml-1.5 text-[11px] text-muted-foreground/70 font-mono scale-90">
                                    {formatFieldType(field)}
                                  </span>
                                </span>
                              ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}

      <ConfirmDialog
        isOpen={!!createdExamplePrompt}
        title={t('settings.systemBehaviors.bindCreatedWorkflowTitle')}
        description={t('settings.systemBehaviors.bindCreatedWorkflowDescription', {
          workflowName: createdExamplePrompt?.workflowName ?? '',
          behaviorName: createdExamplePrompt?.behaviorName ?? '',
        })}
        onCancel={dismissCreatedExamplePrompt}
        onConfirm={handleBindCreatedExampleWorkflow}
        confirmText={t('settings.systemBehaviors.bindCreatedWorkflowConfirm')}
        cancelText={t('settings.systemBehaviors.bindCreatedWorkflowSkip')}
        isLoading={updateBindingMutation.isPending}
      />

      <ResetDangerConfirmDialog
        open={!!resetBehaviorPrompt}
        scope="systemBehaviors"
        mode="single"
        targetName={resetBehaviorPrompt?.behaviorName}
        loading={resetBindingMutation.isPending}
        onOpenChange={(open) => {
          if (!open) {
            setResetBehaviorPrompt(null)
          }
        }}
        onConfirm={() => {
          if (!resetBehaviorPrompt) return
          void handleReset(resetBehaviorPrompt.behaviorKey)
        }}
      />

      <ResetDangerConfirmDialog
        open={showResetAllConfirm}
        scope="systemBehaviors"
        mode="all"
        affectedCount={behaviors.length}
        loading={resetAllMutation.isPending}
        onOpenChange={setShowResetAllConfirm}
        onConfirm={() => {
          void handleResetAll()
        }}
      />
    </div>
  )
}
