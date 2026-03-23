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
      toast.success(t('settings.systemBehaviors.resetSuccess'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
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
      <div className="flex items-start justify-between gap-4">
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
                className="overflow-hidden rounded-3xl border bg-card/80 shadow-sm ring-1 ring-border/40"
              >
                <button
                  type="button"
                  onClick={() => toggleBehavior(behavior.behaviorKey)}
                  className="w-full p-5 text-left transition-colors hover:bg-muted/10"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1 space-y-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold tracking-tight text-foreground">
                          {t(`settings.systemBehaviors.behaviors.${localeKey}.title`, {
                            defaultValue: behavior.name,
                          })}
                        </h2>
                        {behavior.currentBinding.isCanonicalDefault && (
                          <span className="inline-flex items-center rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-700">
                            {t('settings.skills.systemDefaultTarget')}
                          </span>
                        )}
                        <span className="inline-flex items-center rounded-full border bg-background px-3 py-1 text-xs font-medium text-muted-foreground">
                          {targetTypeLabel(behavior.currentBinding.targetType)}
                        </span>
                      </div>

                      <p className="max-w-3xl line-clamp-2 text-sm leading-6 text-muted-foreground">
                        {t(`settings.systemBehaviors.behaviors.${localeKey}.description`, {
                          defaultValue: behavior.description,
                        })}
                      </p>

                      <div className="flex flex-wrap gap-2">
                        <div className="inline-flex max-w-full items-center gap-2 rounded-full border bg-background/90 px-3 py-1.5 text-xs">
                          <span className="text-muted-foreground">
                            {t('settings.systemBehaviors.currentBinding')}
                          </span>
                          <span
                            className="max-w-[240px] truncate font-medium text-foreground"
                            title={behavior.currentBinding.name}
                          >
                            {behavior.currentBinding.name}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex shrink-0 items-center gap-2 rounded-full border bg-background/90 px-3 py-1.5 text-xs font-medium text-muted-foreground">
                      <span>{t(isExpanded ? 'actions.collapse' : 'actions.expand')}</span>
                      <ChevronDown
                        className={cn(
                          'h-4 w-4 transition-transform duration-200',
                          isExpanded ? 'rotate-180' : '',
                        )}
                      />
                    </div>
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t bg-muted/10 p-5 animate-in slide-in-from-top-2">
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(300px,0.85fr)]">
                      <div className="space-y-4">
                        <div className="rounded-2xl border bg-background/95 p-4">
                          <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                            {t('settings.systemBehaviors.currentBinding')}
                          </div>
                          <div
                            className="mt-3 truncate text-base font-semibold text-foreground"
                            title={behavior.currentBinding.name}
                          >
                            {behavior.currentBinding.name}
                          </div>
                          <div className="mt-3 flex flex-wrap gap-2">
                            <span className="inline-flex items-center rounded-full bg-muted px-3 py-1 text-xs font-medium text-foreground">
                              {targetTypeLabel(behavior.currentBinding.targetType)}
                            </span>
                            {behavior.currentBinding.isCanonicalDefault && (
                              <span className="inline-flex items-center rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-700">
                                {t('settings.skills.systemDefaultTarget')}
                              </span>
                            )}
                          </div>
                        </div>

                        <div className="rounded-2xl border bg-background/95 p-4">
                          <div className="flex flex-col gap-1">
                            <div className="text-sm font-medium text-foreground">
                              {t('settings.systemBehaviors.contractSummary')}
                            </div>
                            <p className="text-sm text-muted-foreground">
                              {t('settings.systemBehaviors.contractHint')}
                            </p>
                          </div>

                          <div className="mt-4 grid gap-3 md:grid-cols-2">
                            <div className="rounded-2xl border bg-muted/10 p-4">
                              <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t('settings.systemBehaviors.contractInput')}
                              </div>
                              <div className="mt-3 flex flex-wrap gap-2">
                                {behavior.contract.inputFields.map((field) => (
                                  <span
                                    key={field.name}
                                    className="inline-flex items-center rounded-full border bg-background px-3 py-1.5 text-xs font-medium text-foreground"
                                  >
                                    {field.name}: {formatFieldType(field)}
                                  </span>
                                ))}
                              </div>
                            </div>

                            <div className="rounded-2xl border bg-muted/10 p-4">
                              <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t('settings.systemBehaviors.contractOutput')}
                              </div>
                              <div className="mt-3 flex flex-wrap gap-2">
                                {behavior.contract.outputFields.map((field) => (
                                  <span
                                    key={field.name}
                                    className="inline-flex items-center rounded-full border bg-background px-3 py-1.5 text-xs font-medium text-foreground"
                                  >
                                    {field.name}: {formatFieldType(field)}
                                  </span>
                                ))}
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>

                      <aside className="rounded-2xl border bg-background/95 p-4">
                        <div className="space-y-4">
                          <div>
                            <div className="text-sm font-medium text-foreground">
                              {t('settings.systemBehaviors.bindingSettings')}
                            </div>
                            <p className="mt-1 text-sm leading-6 text-muted-foreground">
                              {t('settings.systemBehaviors.bindingSettingsDesc')}
                            </p>
                          </div>

                          <div className="rounded-2xl border bg-muted/10 p-4">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                              <div className="space-y-1">
                                <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                  {t('settings.systemBehaviors.bindingTarget')}
                                </div>
                                <p className="text-xs leading-5 text-muted-foreground">
                                  {t('settings.systemBehaviors.createExampleWorkflowHint')}
                                </p>
                              </div>
                              <button
                                type="button"
                                onClick={() => handleCreateExampleWorkflow(behavior)}
                                disabled={isMutating}
                                className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl border bg-background px-3.5 text-sm font-medium shadow-sm transition-colors hover:bg-muted/30 disabled:opacity-50"
                              >
                                {createExampleWorkflowMutation.isPending ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <Plus className="h-4 w-4" />
                                )}
                                {t('settings.systemBehaviors.createExampleWorkflow')}
                              </button>
                            </div>

                            <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                              <Popover
                                open={pickerOpen}
                                onOpenChange={(open) => setPickerOpenBehaviorKey(open ? behavior.behaviorKey : null)}
                              >
                                <PopoverTrigger asChild>
                                  <button
                                    type="button"
                                    disabled={isMutating}
                                    className="group flex min-h-[68px] w-full flex-1 items-center gap-3 rounded-2xl border bg-background px-3.5 py-3 text-left shadow-sm transition-all hover:border-primary/30 hover:bg-muted/15 disabled:opacity-50"
                                  >
                                    <div className="rounded-xl border bg-muted/30 p-2 text-muted-foreground">
                                      {(currentTarget?.type ?? behavior.currentBinding.targetType) === 'workflow' ? (
                                        <Workflow className="h-4 w-4" />
                                      ) : (
                                        <Bot className="h-4 w-4" />
                                      )}
                                    </div>

                                    <div className="min-w-0 flex-1">
                                      <div className="flex flex-wrap items-center gap-2">
                                        <Badge variant="outline" className="font-normal">
                                          {targetTypeLabel(currentTarget?.type ?? behavior.currentBinding.targetType)}
                                        </Badge>
                                        {currentTarget?.key === SYSTEM_DEFAULT_TARGET_KEY && (
                                          <Badge variant="secondary" className="font-normal">
                                            {t('settings.skills.systemDefaultTarget')}
                                          </Badge>
                                        )}
                                      </div>
                                      <div
                                        className="mt-1.5 truncate text-sm font-semibold text-foreground"
                                        title={currentTarget?.name ?? behavior.currentBinding.name}
                                      >
                                        {currentTarget?.name ?? behavior.currentBinding.name}
                                      </div>
                                    </div>

                                    <div className="rounded-full border bg-muted/30 p-2 text-muted-foreground transition-colors group-hover:bg-muted/50">
                                      <ChevronDown className="h-4 w-4 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
                                    </div>
                                  </button>
                                </PopoverTrigger>
                                <PopoverContent
                                  align="start"
                                  sideOffset={8}
                                  className="w-[min(560px,calc(100vw-3rem))] rounded-2xl p-2 shadow-xl"
                                >
                                  <div className="border-b px-3 pb-3 pt-2">
                                    <div className="text-sm font-medium text-foreground">
                                      {t('settings.systemBehaviors.targetPickerTitle')}
                                    </div>
                                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                      {t('settings.systemBehaviors.targetPickerHint')}
                                    </p>
                                  </div>
                                  <div className="mt-2 max-h-[360px] space-y-1 overflow-y-auto pr-1">
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
                                            'flex w-full items-start gap-3 rounded-2xl border px-3 py-3 text-left transition-all',
                                            isSelected
                                              ? 'border-primary/25 bg-primary/5'
                                              : 'border-transparent bg-background hover:border-border hover:bg-muted/40',
                                            !target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY
                                              ? 'cursor-not-allowed opacity-55 grayscale'
                                              : 'cursor-pointer',
                                          )}
                                        >
                                          <div className={cn(
                                            'mt-0.5 rounded-lg p-2',
                                            isSelected ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
                                          )}>
                                            <Icon className="h-4 w-4" />
                                          </div>

                                          <div className="min-w-0 flex-1">
                                            <div className="flex flex-wrap items-center gap-2">
                                              <span
                                                className="truncate text-sm font-medium text-foreground"
                                                title={target.name}
                                              >
                                                {target.name}
                                              </span>
                                              <Badge variant="outline" className="font-normal">
                                                {targetTypeLabel(target.type)}
                                              </Badge>
                                              {target.key === SYSTEM_DEFAULT_TARGET_KEY && (
                                                <Badge variant="secondary" className="font-normal">
                                                  {t('settings.skills.systemDefaultTarget')}
                                                </Badge>
                                              )}
                                              {target.isSystemDefault && target.key !== SYSTEM_DEFAULT_TARGET_KEY && (
                                                <Badge variant="secondary" className="font-normal">
                                                  {t('settings.systemBehaviors.defaultAliasSource')}
                                                </Badge>
                                              )}
                                            </div>

                                            <div className="mt-1 text-xs leading-5 text-muted-foreground">
                                              {!target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY
                                                ? disabledReasonLabel(target)
                                                : target.description || t('settings.systemBehaviors.targetReady')}
                                            </div>
                                          </div>

                                          {isSelected && (
                                            <Check className="mt-1 h-4 w-4 shrink-0 text-primary" />
                                          )}
                                        </button>
                                      )
                                    })}
                                  </div>
                                </PopoverContent>
                              </Popover>

                              <button
                                type="button"
                                onClick={() => openTarget(behavior.currentBinding.targetType, behavior.currentBinding.id)}
                                className="inline-flex h-14 items-center justify-center gap-2 rounded-2xl border bg-background px-4 text-sm font-medium text-muted-foreground shadow-sm transition-colors hover:bg-muted/30 hover:text-foreground md:min-w-[128px]"
                              >
                                <ExternalLink className="h-4 w-4" />
                                {t('settings.systemBehaviors.editTarget')}
                              </button>
                            </div>
                          </div>

                          <div className="flex justify-end">
                            <button
                              onClick={() => handleReset(behavior.behaviorKey)}
                              disabled={resetDisabled}
                              className="inline-flex h-11 min-w-[180px] items-center justify-center gap-2 rounded-xl border px-4 text-sm font-medium transition-colors hover:bg-muted/40 disabled:opacity-50"
                            >
                              <RotateCcw className="h-4 w-4" />
                              {t('settings.systemBehaviors.resetToDefault')}
                            </button>
                          </div>
                        </div>
                      </aside>
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
    </div>
  )
}
