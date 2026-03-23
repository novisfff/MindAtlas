import { useMemo } from 'react'
import { ArrowLeft, ExternalLink, Loader2, RotateCcw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { SystemBehavior } from '../api/system-behaviors'
import {
  useAgentProfilesQuery,
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

export function SystemAiBehaviorsSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: workflows = [], isLoading: isLoadingWorkflows } = useWorkflowsQuery()
  const { data: agents = [], isLoading: isLoadingAgents } = useAgentProfilesQuery()
  const { data: behaviors = [], isLoading: isLoadingBehaviors } = useSystemBehaviorsQuery()
  const updateBindingMutation = useUpdateSystemBehaviorBindingMutation()
  const resetBindingMutation = useResetSystemBehaviorBindingMutation()

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

  const optionLabel = (target: AssistantExecutableTarget) => {
    if (target.key === SYSTEM_DEFAULT_TARGET_KEY) {
      return t('settings.skills.systemDefaultTargetOption', {
        type: targetTypeLabel(target.type),
        name: target.name,
      })
    }
    const suffix = !target.bindable ? ` (${disabledReasonLabel(target)})` : ''
    return `[${targetTypeLabel(target.type)}] ${target.name}${suffix}`
  }

  const openTarget = (targetType: 'workflow' | 'agent', id: string) => {
    if (targetType === 'workflow') {
      navigate(`/settings/workflow-editor/${id}`)
      return
    }
    navigate(`/settings/agent-editor/${id}`)
  }

  const handleSelectTarget = async (behavior: SystemBehavior, nextKey: string) => {
    const availableTargets = targetsByBehavior.get(behavior.behaviorKey) ?? []
    const selected = availableTargets.find((item) => item.key === nextKey)
    if (!selected) return

    try {
      if (selected.key === SYSTEM_DEFAULT_TARGET_KEY) {
        await resetBindingMutation.mutateAsync(behavior.behaviorKey)
        toast.success(t('settings.systemBehaviors.resetSuccess'))
        return
      }

      if (!selected.bindable) {
        toast.error(disabledReasonLabel(selected))
        return
      }

      if (selected.type === 'workflow') {
        await updateBindingMutation.mutateAsync({
          behaviorKey: behavior.behaviorKey,
          data: {
            targetType: 'workflow',
            workflowId: selected.id,
          },
        })
      } else {
        await updateBindingMutation.mutateAsync({
          behaviorKey: behavior.behaviorKey,
          data: {
            targetType: 'agent',
            agentProfileId: selected.id,
          },
        })
      }
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

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/settings')}
              className="p-1.5 -ml-2 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
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
          <Loader2 className="w-8 h-8 animate-spin text-primary" />
          <p className="text-sm">{t('messages.loading')}</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {behaviors.map((behavior) => {
            const localeKey = BEHAVIOR_LOCALE_KEY[behavior.behaviorKey]
            const availableTargets = targetsByBehavior.get(behavior.behaviorKey) ?? []
            const currentTargetKey = resolveSkillTargetKey(
              {
                targetType: behavior.currentBinding.targetType,
                workflowId: behavior.currentBinding.workflowId ?? null,
                agentProfileId: behavior.currentBinding.agentProfileId ?? null,
              },
              availableTargets,
            ) ?? ''
            const isMutating = updateBindingMutation.isPending || resetBindingMutation.isPending

            return (
              <section
                key={behavior.behaviorKey}
                className="rounded-2xl border bg-card p-5 shadow-sm"
              >
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold">
                        {t(`settings.systemBehaviors.behaviors.${localeKey}.title`, {
                          defaultValue: behavior.name,
                        })}
                      </h2>
                      {behavior.currentBinding.isCanonicalDefault && (
                        <span className="inline-flex items-center rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
                          {t('settings.skills.systemDefaultTarget')}
                        </span>
                      )}
                    </div>
                    <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
                      {t(`settings.systemBehaviors.behaviors.${localeKey}.description`, {
                        defaultValue: behavior.description,
                      })}
                    </p>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => openTarget(behavior.currentBinding.targetType, behavior.currentBinding.id)}
                      className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted/40 transition-colors"
                    >
                      <ExternalLink className="w-4 h-4" />
                      {t('settings.systemBehaviors.openTarget')}
                    </button>
                    <button
                      onClick={() => handleReset(behavior.behaviorKey)}
                      disabled={isMutating}
                      className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted/40 transition-colors disabled:opacity-50"
                    >
                      <RotateCcw className="w-4 h-4" />
                      {t('settings.systemBehaviors.resetToDefault')}
                    </button>
                  </div>
                </div>

                <div className="mt-5 grid gap-4 lg:grid-cols-[1.2fr_1.2fr_1.6fr]">
                  <div className="rounded-xl border bg-muted/20 p-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {t('settings.systemBehaviors.currentBinding')}
                    </div>
                    <div className="mt-2 text-sm font-medium">{behavior.currentBinding.name}</div>
                    <div className="mt-1 text-sm text-muted-foreground">
                      {targetTypeLabel(behavior.currentBinding.targetType)}
                    </div>
                  </div>

                  <div className="rounded-xl border bg-muted/20 p-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {t('settings.systemBehaviors.canonicalDefault')}
                    </div>
                    <div className="mt-2 text-sm font-medium">{behavior.canonicalDefaultTarget.name}</div>
                    <div className="mt-1 text-sm text-muted-foreground">
                      {targetTypeLabel(behavior.canonicalDefaultTarget.targetType)}
                    </div>
                  </div>

                  <div className="rounded-xl border bg-muted/20 p-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {t('settings.systemBehaviors.bindingTarget')}
                    </div>
                    <div className="mt-2">
                      <select
                        value={currentTargetKey}
                        onChange={(event) => handleSelectTarget(behavior, event.target.value)}
                        disabled={isMutating}
                        className="flex h-10 w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                      >
                        {availableTargets.map((target) => (
                          <option key={target.key} value={target.key} disabled={!target.bindable && target.key !== SYSTEM_DEFAULT_TARGET_KEY}>
                            {optionLabel(target)}
                          </option>
                        ))}
                      </select>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {t('settings.systemBehaviors.fallbackPolicy')}
                      : {t('settings.systemBehaviors.fallbackPolicyCanonicalDefault')}
                    </p>
                  </div>
                </div>

                <div className="mt-4 rounded-xl border bg-background p-4">
                  <div className="text-sm font-medium">
                    {t('settings.systemBehaviors.contractSummary')}
                  </div>
                  <div className="mt-3 grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        {t('settings.systemBehaviors.contractInput')}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {behavior.contract.inputFields.map((field) => (
                          <span
                            key={field.name}
                            className="inline-flex items-center rounded-md border bg-muted/30 px-2.5 py-1 text-xs"
                          >
                            {field.name}: {formatFieldType(field)}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        {t('settings.systemBehaviors.contractOutput')}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {behavior.contract.outputFields.map((field) => (
                          <span
                            key={field.name}
                            className="inline-flex items-center rounded-md border bg-muted/30 px-2.5 py-1 text-xs"
                          >
                            {field.name}: {formatFieldType(field)}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
