import { useMemo, useState } from 'react'
import {
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
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { uiChrome } from '@/components/ui/styles'
import { isApiError } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import type { SystemBehavior } from '../api/system-behaviors'
import {
  useAgentProfilesQuery,
  useCallableWorkflowsQuery,
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
import {
  SettingsBadge,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsInset,
} from '@/features/settings/components/SettingsShell'

const BEHAVIOR_LOCALE_KEY: Record<SystemBehavior['behaviorKey'], string> = {
  weekly_report_generation: 'weeklyReportGeneration',
  monthly_report_generation: 'monthlyReportGeneration',
}

function formatFieldType(
  t: (key: string, options?: Record<string, unknown>) => string,
  field: { type: string; itemsType?: string | null },
) {
  if (field.type === 'array') {
    return t('settings.systemBehaviors.fieldTypes.arrayOf', {
      type: t(`settings.systemBehaviors.fieldTypes.${field.itemsType ?? 'string'}`, {
        defaultValue: field.itemsType ?? 'string',
      }),
    })
  }
  return t(`settings.systemBehaviors.fieldTypes.${field.type}`, {
    defaultValue: field.type,
  })
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
  const [showCollapsedTargetKeys, setShowCollapsedTargetKeys] = useState<SystemBehavior['behaviorKey'][]>([])
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
  const { data: callableWorkflows = [], isLoading: isLoadingCallableWorkflows } = useCallableWorkflowsQuery()
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
          callableWorkflows,
          systemBehaviorContract: behavior.contract,
        },
      ),
    ]),
  ), [agents, behaviors, callableWorkflows, workflows])

  const loading = isLoadingBehaviors || isLoadingWorkflows || isLoadingCallableWorkflows || isLoadingAgents

  const targetTypeLabel = (type: 'workflow' | 'agent') => (
    type === 'workflow'
      ? t('settings.skills.targetTypeWorkflow')
      : t('settings.skills.targetTypeAgent')
  )

  const disabledReasonLabel = (target: AssistantExecutableTarget) => {
    if (target.disabledReason === 'unstructured_workflow') {
      return t('settings.systemBehaviors.disabledReasons.unstructuredWorkflow')
    }
    if (target.disabledReason === 'contract_mismatch') {
      return t('settings.systemBehaviors.disabledReasons.contractMismatch')
    }
    if (target.disabledReason === 'agent_contract_unsupported') {
      return t('settings.systemBehaviors.disabledReasons.agentContractUnsupported')
    }
    if (target.disabledReason === 'unpublished_target') {
      return t('settings.systemBehaviors.disabledReasons.unpublishedTarget')
    }
    return t('settings.systemBehaviors.disabledReasons.unavailableTarget')
  }

  const resolveBehaviorErrorMessage = (error: unknown) => {
    if (isApiError(error)) {
      if (error.code === 42248) return t('settings.systemBehaviors.disabledReasons.unstructuredWorkflow')
      if (error.code === 42249 || error.code === 42250) {
        return t('settings.systemBehaviors.disabledReasons.contractMismatch')
      }
      if (error.code === 42276) return t('settings.systemBehaviors.disabledReasons.agentContractUnsupported')
      if (error.code === 40950 || error.code === 40952) {
        return t('settings.systemBehaviors.disabledReasons.unpublishedTarget')
      }
      if (error.code === 40949 || error.code === 40951) {
        return t('settings.systemBehaviors.disabledReasons.unavailableTarget')
      }
      return error.message
    }
    return error instanceof Error ? error.message : t('messages.error')
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

  const toggleCollapsedTargets = (behaviorKey: SystemBehavior['behaviorKey']) => {
    setShowCollapsedTargetKeys((current) => (
      current.includes(behaviorKey)
        ? current.filter((item) => item !== behaviorKey)
        : [...current, behaviorKey]
    ))
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
      toast.error(resolveBehaviorErrorMessage(error))
    }
  }

  const handleReset = async (behaviorKey: string) => {
    try {
      await resetBindingMutation.mutateAsync(behaviorKey)
      setResetBehaviorPrompt(null)
      toast.success(t('settings.systemBehaviors.resetSuccess'))
    } catch (error) {
      toast.error(resolveBehaviorErrorMessage(error))
    }
  }

  const handleResetAll = async () => {
    try {
      await resetAllMutation.mutateAsync()
      setShowResetAllConfirm(false)
      toast.success(t('settings.systemBehaviors.resetAllSuccess'))
    } catch (error) {
      toast.error(resolveBehaviorErrorMessage(error) || t('settings.systemBehaviors.resetAllError'))
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
      toast.error(resolveBehaviorErrorMessage(error))
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
      toast.error(resolveBehaviorErrorMessage(error))
    }
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.systemAiBehaviors')}
        description={t('pages.settings.systemAiBehaviorsDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        actions={behaviors.length > 0 ? (
          <Button
            type="button"
            onClick={() => setShowResetAllConfirm(true)}
            disabled={loading || resetAllMutation.isPending}
            variant="outline"
            size="sm"
          >
            {resetAllMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            {t('settings.systemBehaviors.resetAll')}
          </Button>
        ) : null}
      />

      <SettingsSection className="space-y-4">
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
            const showCollapsedTargets = showCollapsedTargetKeys.includes(behavior.behaviorKey)
            const collapsedTargets = orderedTargets.filter((target) => (
              target.key !== SYSTEM_DEFAULT_TARGET_KEY
              && target.key !== currentTargetKey
              && (target.hidden || !target.bindable)
            ))
            const visibleTargets = orderedTargets.filter((target) => (
              showCollapsedTargets
              || target.key === SYSTEM_DEFAULT_TARGET_KEY
              || target.key === currentTargetKey
              || (!target.hidden && target.bindable)
            ))

            return (
              <section
                key={behavior.behaviorKey}
                className={cn(
                  uiChrome.card,
                  "overflow-hidden transition-all duration-300",
                  isExpanded ? "border-primary/20 ring-1 ring-primary/10" : "hover:border-primary/20",
                )}
              >
                <button
                  type="button"
                  onClick={() => toggleBehavior(behavior.behaviorKey)}
                  className="w-full text-left transition-colors hover:bg-muted/25"
                >
                  <div className="flex items-start justify-between gap-4 px-5 py-5 sm:px-6">
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <h2 className="text-[1.1rem] font-semibold tracking-tight text-foreground">
                          {t(`settings.systemBehaviors.behaviors.${localeKey}.title`, {
                            defaultValue: behavior.name,
                          })}
                        </h2>
                        {behavior.currentBinding.isCanonicalDefault && (
                          <SettingsBadge className="border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                            {t('settings.skills.systemDefaultTarget')}
                          </SettingsBadge>
                        )}
                        <SettingsBadge>
                          {targetTypeLabel(behavior.currentBinding.targetType)}
                        </SettingsBadge>
                      </div>

                      <p className="max-w-3xl line-clamp-2 text-[13px] leading-relaxed text-muted-foreground">
                        {t(`settings.systemBehaviors.behaviors.${localeKey}.description`, {
                          defaultValue: behavior.description,
                        })}
                      </p>

                      {!isExpanded && (
                        <div className="animate-in fade-in slide-in-from-top-1 flex flex-wrap gap-2 pt-1.5 duration-200">
                          <SettingsBadge className="max-w-full gap-2">
                            <span className="text-muted-foreground/80">
                              {t('settings.systemBehaviors.currentBinding')}
                            </span>
                            <span
                              className="max-w-[300px] truncate font-medium text-foreground"
                              title={behavior.currentBinding.name}
                            >
                              {behavior.currentBinding.name}
                            </span>
                          </SettingsBadge>
                        </div>
                      )}
                    </div>

                    <div className={cn(
                      uiChrome.control,
                      "flex shrink-0 items-center justify-center gap-1.5 px-3 py-1.5 text-[12px] font-medium shadow-none md:px-3.5",
                      isExpanded ? "bg-muted/50 text-foreground" : "text-muted-foreground hover:bg-muted/60"
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
                  <div className="animate-in border-t border-border/70 px-5 py-5 slide-in-from-top-2 duration-300 sm:px-6">
                    <div className="space-y-5">
                      <SettingsInset className="space-y-4 p-5">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex items-center gap-2 text-[13px] font-medium text-muted-foreground">
                            <span className="h-4 w-1 rounded-full bg-primary/40"></span>
                            {t('settings.systemBehaviors.currentBinding')}
                          </div>
                          <div className="flex items-center gap-2">
                            <SettingsBadge>
                              {targetTypeLabel(behavior.currentBinding.targetType)}
                            </SettingsBadge>
                          </div>
                        </div>
                        <div className={cn(uiChrome.control, "flex min-h-[56px] items-center px-4 py-3 shadow-none")}>
                          <div className="truncate text-base font-semibold tracking-tight text-foreground" title={behavior.currentBinding.name}>
                            {behavior.currentBinding.name}
                          </div>
                        </div>
                      </SettingsInset>

                      <SettingsInset className="space-y-6 p-5">
                        <div className="flex flex-col gap-6">
                          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div className="space-y-1.5">
                              <div className="text-[15px] font-bold text-foreground">
                                {t('settings.systemBehaviors.bindingSettings')}
                              </div>
                              <p className="text-[13px] text-muted-foreground max-w-xl leading-relaxed">
                                {t('settings.systemBehaviors.bindingSettingsDesc')}
                                <br />
                                {t('settings.systemBehaviors.createExampleWorkflowHint')}
                              </p>
                              {(behavior.currentBinding.isCanonicalDefault || currentTarget?.isSystem) && (
                                <div className="rounded-[12px] border border-amber-200 bg-amber-50/90 px-4 py-3 text-[13px] leading-6 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                                  {t('settings.skills.systemTargetBindingHint')}
                                </div>
                              )}
                            </div>
                            <Button
                              type="button"
                              onClick={() => handleCreateExampleWorkflow(behavior)}
                              disabled={isMutating}
                              variant="outline"
                              size="sm"
                              className="shrink-0"
                            >
                              {createExampleWorkflowMutation.isPending ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Plus className="h-3.5 w-3.5" />
                              )}
                              {t('settings.systemBehaviors.createExampleWorkflow')}
                            </Button>
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
                                  className={cn(
                                    uiChrome.control,
                                    "group flex min-h-[72px] w-full items-center justify-between gap-4 px-4 py-2.5 text-left shadow-none transition-all hover:border-primary/25 hover:bg-muted/20 focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
                                  )}
                                >
                                  <div className="flex items-center gap-4 min-w-0">
                                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-muted/50 text-muted-foreground transition-colors group-hover:bg-primary/5 group-hover:text-primary">
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

                                  <div className={cn(uiChrome.control, "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground shadow-none transition-all group-hover:bg-muted/50")}>
                                    <ChevronDown className="h-4 w-4 transition-transform group-data-[state=open]:rotate-180" />
                                  </div>
                                </button>
                              </PopoverTrigger>
                              <PopoverContent
                                align="start"
                                sideOffset={8}
                                className="w-[min(560px,calc(100vw-3rem))] p-2"
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
                                  {visibleTargets.map((target) => {
                                    const isSelected = target.key === currentTargetKey
                                    const Icon = target.type === 'workflow' ? Workflow : Bot
                                    return (
                                      <button
                                        key={target.key}
                                        type="button"
                                        disabled={!target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY}
                                      onClick={() => handleSelectTarget(behavior, target)}
                                      className={cn(
                                          uiChrome.control,
                                          'flex w-full items-start gap-4 px-3 py-3 text-left shadow-none transition-all',
                                          isSelected
                                            ? 'border-primary/25 bg-primary/5'
                                            : 'border-transparent bg-transparent hover:bg-muted/40',
                                          !target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY
                                            ? 'cursor-not-allowed opacity-50 grayscale'
                                            : 'cursor-pointer',
                                        )}
                                      >
                                        <div className={cn(
                                          'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
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
                                            <SettingsBadge className="text-[10px] uppercase tracking-wide opacity-80">
                                              {targetTypeLabel(target.type)}
                                            </SettingsBadge>
                                            {target.key === SYSTEM_DEFAULT_TARGET_KEY && (
                                              <SettingsBadge className="border-amber-200 bg-amber-50 text-[10px] uppercase tracking-wide text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                                                {t('settings.skills.systemDefaultTarget')}
                                              </SettingsBadge>
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

                                  {collapsedTargets.length > 0 && (
                                    <div className="border-t border-border/70 px-2 pb-2 pt-3">
                                      <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => toggleCollapsedTargets(behavior.behaviorKey)}
                                        className="h-auto w-full justify-center px-3 py-2 text-[12px] font-medium text-muted-foreground"
                                      >
                                        {showCollapsedTargets
                                          ? t('settings.systemBehaviors.hideCollapsedTargets')
                                          : t('settings.systemBehaviors.showCollapsedTargets', {
                                              count: collapsedTargets.length,
                                            })}
                                      </Button>
                                    </div>
                                  )}
                                </div>
                              </PopoverContent>
                            </Popover>
                          </div>

                          <div className="grid gap-3 sm:grid-cols-2">
                            <Button
                              type="button"
                              onClick={() => openTarget(behavior.currentBinding.targetType, behavior.currentBinding.id)}
                              variant="outline"
                              className="group h-11 justify-center text-[13px] text-muted-foreground hover:text-foreground"
                            >
                              <ExternalLink className="h-4 w-4 transition-transform group-hover:scale-110" />
                              {t('settings.systemBehaviors.editTarget')}
                            </Button>

                            <Button
                              type="button"
                              onClick={() => setResetBehaviorPrompt({
                                behaviorKey: behavior.behaviorKey,
                                behaviorName: t(`settings.systemBehaviors.behaviors.${localeKey}.title`, {
                                  defaultValue: behavior.name,
                                }),
                              })}
                              disabled={resetDisabled}
                              variant="outline"
                              className="group h-11 justify-center text-[13px]"
                            >
                              <RotateCcw className={cn(
                                "h-4 w-4 transition-all duration-300",
                                !resetDisabled && "group-hover:-rotate-180"
                              )} />
                              {t('settings.systemBehaviors.resetToDefault')}
                            </Button>
                          </div>
                        </div>
                      </SettingsInset>

                      <SettingsInset className="space-y-4 p-5">
                        <div className="flex flex-col gap-1.5">
                          <div className="flex items-center gap-2 text-[15px] font-bold text-foreground">
                            {t('settings.systemBehaviors.contractSummary')}
                          </div>
                          <p className="text-[13px] text-muted-foreground leading-relaxed">
                            {t('settings.systemBehaviors.contractHint')}
                          </p>
                        </div>

                        <div className="mt-5 grid gap-4 md:grid-cols-2">
                          <div className={cn(uiChrome.control, 'p-4 shadow-none')}>
                            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.2em] text-muted-foreground mb-3">
                              <span className="w-1.5 h-1.5 rounded-full bg-blue-500/60"></span>
                              {t('settings.systemBehaviors.contractInput')}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {behavior.contract.inputFields.map((field) => (
                                <SettingsBadge
                                  key={field.name}
                                  className="rounded-[12px] border-border/70 bg-background/92 px-2.5 py-1 text-[12px] font-medium text-foreground"
                                >
                                    {field.name}
                                  <span className="ml-1.5 text-[11px] text-muted-foreground/70 font-mono scale-90">
                                    {formatFieldType(t, field)}
                                  </span>
                                </SettingsBadge>
                              ))}
                            </div>
                          </div>

                          <div className={cn(uiChrome.control, 'p-4 shadow-none')}>
                            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.2em] text-muted-foreground mb-3">
                              <span className="w-1.5 h-1.5 rounded-full bg-green-500/60"></span>
                              {t('settings.systemBehaviors.contractOutput')}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {behavior.contract.outputFields.map((field) => (
                                <SettingsBadge
                                  key={field.name}
                                  className="rounded-[12px] border-border/70 bg-background/92 px-2.5 py-1 text-[12px] font-medium text-foreground"
                                >
                                    {field.name}
                                  <span className="ml-1.5 text-[11px] text-muted-foreground/70 font-mono scale-90">
                                    {formatFieldType(t, field)}
                                  </span>
                                </SettingsBadge>
                              ))}
                            </div>
                          </div>
                        </div>
                      </SettingsInset>
                    </div>
                  </div>
                )}
              </section>
            )
          })}
        </div>
        )}
      </SettingsSection>

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
    </SettingsPageShell>
  )
}
