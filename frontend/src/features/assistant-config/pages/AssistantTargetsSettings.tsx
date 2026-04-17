import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, Loader2, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  useAgentProfilesQuery,
  useAgentProfileDetailQuery,
  useCopyAgentProfileMutation,
  useCopyWorkflowMutation,
  useCreateAgentProfileMutation,
  useCreateWorkflowMutation,
  useDeleteAgentProfileMutation,
  useDeleteWorkflowMutation,
  useSkillsQuery,
  useWorkflowDetailQuery,
  useWorkflowsQuery,
} from '../queries'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { uiField } from '@/components/ui/styles'
import { buildAssistantExecutableTargets, type AssistantExecutableTarget } from '../components/skillTargetOptions'
import { AssistantTargetCard } from '../components/AssistantTargetCard'
import {
  SettingsBadge,
  SettingsEmptyState,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { isApiError } from '@/lib/api/client'

type CreateTargetType = 'workflow' | 'agent' | null
type DeleteRebindConflict = {
  target: AssistantExecutableTarget
  behaviorKeys: string[]
}

export function AssistantTargetsSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: workflows = [], isLoading: isLoadingWorkflows } = useWorkflowsQuery()
  const { data: agents = [], isLoading: isLoadingAgents } = useAgentProfilesQuery()
  const { data: skills = [] } = useSkillsQuery()

  const createWorkflowMutation = useCreateWorkflowMutation()
  const createAgentMutation = useCreateAgentProfileMutation()
  const copyWorkflowMutation = useCopyWorkflowMutation()
  const copyAgentMutation = useCopyAgentProfileMutation()
  const deleteWorkflowMutation = useDeleteWorkflowMutation()
  const deleteAgentMutation = useDeleteAgentProfileMutation()

  const [createType, setCreateType] = useState<CreateTargetType>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<AssistantExecutableTarget | null>(null)
  const [deleteRebindConflict, setDeleteRebindConflict] = useState<DeleteRebindConflict | null>(null)
  const [expandedTargetKey, setExpandedTargetKey] = useState<string | null>(null)
  const [copyingTargetKey, setCopyingTargetKey] = useState<string | null>(null)

  const targets = useMemo(() => {
    const systemDefaultSkill = skills.find((item) => item.name === 'general_chat')
    const defaultTargetType = systemDefaultSkill?.targetType ?? null
    const defaultTargetId = defaultTargetType === 'workflow'
      ? (systemDefaultSkill?.workflowId ?? null)
      : (systemDefaultSkill?.agentProfileId ?? null)
    return buildAssistantExecutableTargets(
      workflows,
      agents,
      {
        defaultTargetType,
        defaultTargetId,
      },
    )
  }, [agents, skills, workflows])
  const workflowById = useMemo(
    () => new Map(workflows.map((item) => [item.id, item])),
    [workflows],
  )
  const agentById = useMemo(
    () => new Map(agents.map((item) => [item.id, item])),
    [agents],
  )
  const expandedTarget = useMemo(
    () => targets.find((item) => item.key === expandedTargetKey) ?? null,
    [expandedTargetKey, targets],
  )
  const expandedWorkflowSummary = expandedTarget?.type === 'workflow'
    ? workflowById.get(expandedTarget.id)
    : undefined
  const expandedAgentSummary = expandedTarget?.type === 'agent'
    ? agentById.get(expandedTarget.id)
    : undefined
  const shouldLoadExpandedWorkflow = !!expandedWorkflowSummary && !expandedWorkflowSummary.detailsLoaded
  const shouldLoadExpandedAgent = !!expandedAgentSummary && !expandedAgentSummary.detailsLoaded
  const { data: expandedWorkflowDetail, isLoading: isLoadingExpandedWorkflow } = useWorkflowDetailQuery(
    expandedTarget?.type === 'workflow' ? expandedTarget.id : null,
    shouldLoadExpandedWorkflow,
  )
  const { data: expandedAgentDetail, isLoading: isLoadingExpandedAgent } = useAgentProfileDetailQuery(
    expandedTarget?.type === 'agent' ? expandedTarget.id : null,
    shouldLoadExpandedAgent,
  )

  const beginCreate = (type: Exclude<CreateTargetType, null>) => {
    setCreateType(type)
    setName('')
    setDescription('')
  }

  const resetCreateForm = () => {
    setCreateType(null)
    setName('')
    setDescription('')
  }

  const handleCreate = async () => {
    const trimmedName = name.trim()
    if (!trimmedName) return

    try {
      if (createType === 'workflow') {
        const created = await createWorkflowMutation.mutateAsync({
          name: trimmedName,
          description: description.trim(),
        })
        toast.success(t('settings.skills.workflowCreated'))
        resetCreateForm()
        navigate(`/settings/workflow-editor/${created.id}`)
        return
      }

      if (createType === 'agent') {
        const defaultPrompt = t('settings.skills.agentCreateDefaultSystemPrompt').trim()
        const created = await createAgentMutation.mutateAsync({
          name: trimmedName,
          description: description.trim(),
          systemPrompt: defaultPrompt,
          kbConfig: { enabled: false },
          tools: [],
        })
        toast.success(t('settings.skills.agentCreated'))
        resetCreateForm()
        navigate(`/settings/agent-editor/${created.id}`)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  const handleEdit = (target: AssistantExecutableTarget) => {
    if (target.type === 'workflow') {
      navigate(`/settings/workflow-editor/${target.id}`)
      return
    }
    navigate(`/settings/agent-editor/${target.id}`)
  }

  const handleCopy = async (target: AssistantExecutableTarget) => {
    if (copyWorkflowMutation.isPending || copyAgentMutation.isPending) return

    const loadingToastId = toast.loading(
      target.type === 'workflow'
        ? t('settings.skills.workflowCopying')
        : t('settings.skills.agentCopying'),
    )
    setCopyingTargetKey(target.key)

    try {
      if (target.type === 'workflow') {
        const copied = await copyWorkflowMutation.mutateAsync(target.id)
        toast.success(t('settings.skills.workflowCopied'), { id: loadingToastId })
        navigate(`/settings/workflow-editor/${copied.id}`)
        return
      }

      const copied = await copyAgentMutation.mutateAsync(target.id)
      toast.success(t('settings.skills.agentCopied'), { id: loadingToastId })
      navigate(`/settings/agent-editor/${copied.id}`)
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message, { id: loadingToastId })
    } finally {
      setCopyingTargetKey(null)
    }
  }

  const isLoading = isLoadingWorkflows || isLoadingAgents
  const isCreating = createWorkflowMutation.isPending || createAgentMutation.isPending
  const isCopyingAny = copyWorkflowMutation.isPending || copyAgentMutation.isPending

  const executeDelete = (
    target: AssistantExecutableTarget,
    confirmRebindSystemBehaviors: boolean,
  ) => {
    const mutation = target.type === 'workflow' ? deleteWorkflowMutation : deleteAgentMutation
    mutation.mutate(
      {
        id: target.id,
        confirmRebindSystemBehaviors,
      },
      {
        onSuccess: () => {
          setDeleteTarget(null)
          setDeleteRebindConflict(null)
        },
        onError: (error) => {
          if (
            isApiError(error)
            && (error.code === 40961 || error.code === 40963)
            && error.details
            && typeof error.details === 'object'
          ) {
            const details = error.details as { referencedSystemBehaviorKeys?: unknown }
            const behaviorKeys = Array.isArray(details.referencedSystemBehaviorKeys)
              ? details.referencedSystemBehaviorKeys.map((item) => String(item)).filter(Boolean)
              : []
            setDeleteTarget(null)
            setDeleteRebindConflict({
              target,
              behaviorKeys,
            })
            return
          }

          const message = error instanceof Error ? error.message : t('messages.error')
          toast.error(message)
        },
      },
    )
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.assistantTargets')}
        description={t('pages.settings.assistantTargetsDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => beginCreate('workflow')}>
              <Plus className="h-4 w-4" />
              {t('settings.skills.createWorkflow')}
            </Button>
            <Button onClick={() => beginCreate('agent')} variant="outline">
              <Plus className="h-4 w-4" />
              {t('settings.skills.createAgent')}
            </Button>
          </div>
        }
      />

      <div className="space-y-6">
        {createType && (
          <SettingsSection className="animate-in space-y-6 fade-in slide-in-from-top-4 duration-300">
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-foreground">
                  {createType === 'workflow' ? t('settings.skills.createWorkflow') : t('settings.skills.createAgent')}
                </h3>
                <Button type="button" onClick={resetCreateForm} variant="ghost" size="icon">
                  <ChevronDown className="w-5 h-5" />
                </Button>
              </div>

              <div className="grid gap-5">
                <div className="grid gap-2">
                  <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                    {t('settings.skills.name')}
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className={uiField.input}
                    placeholder={t('settings.skills.name')}
                    autoFocus
                  />
                </div>

                <div className="grid gap-2">
                  <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                    {t('settings.skills.description')}
                  </label>
                  <input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className={uiField.input}
                    placeholder={t('settings.skills.description')}
                  />
                </div>

              </div>

              <div className="flex justify-end gap-3 pt-2">
                <Button
                  type="button"
                  onClick={resetCreateForm}
                  variant="outline"
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  type="button"
                  onClick={handleCreate}
                  disabled={
                    isCreating
                    || !name.trim()
                  }
                >
                  {isCreating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {t('common.create')}
                </Button>
              </div>
            </div>
          </SettingsSection>
        )}

        <SettingsSection className="space-y-4">
          <SettingsSectionHeader
            title={
              <span className="flex items-center gap-3">
                <span>{t('pages.settings.assistantTargets')}</span>
                <SettingsBadge>{targets.length}</SettingsBadge>
              </span>
            }
            description={t('pages.settings.assistantTargetsDesc')}
          />

          {isLoading ? (
            <div className="flex flex-col items-center justify-center gap-4 py-20 text-muted-foreground">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm">{t('messages.loading')}</p>
            </div>
          ) : targets.length === 0 ? (
            <SettingsEmptyState
              title={t('settings.skills.noTargetsFound')}
              description={t('pages.settings.assistantTargetsDesc')}
            />
          ) : (
            <div className="grid gap-4">
              {targets.map((target) => {
                const isWorkflow = target.type === 'workflow'
                const isExpanded = expandedTargetKey === target.key
                const workflow = isWorkflow
                  ? (isExpanded && expandedWorkflowDetail?.id === target.id
                    ? expandedWorkflowDetail
                    : workflowById.get(target.id))
                  : undefined
                const agent = isWorkflow
                  ? undefined
                  : (isExpanded && expandedAgentDetail?.id === target.id
                    ? expandedAgentDetail
                    : agentById.get(target.id))
                const isDeleting = (
                  deleteWorkflowMutation.isPending
                  || deleteAgentMutation.isPending
                ) && deleteTarget?.key === target.key
                const disableDelete = target.referenceCount > 0 || target.isSystem

                return (
                  <AssistantTargetCard
                    key={target.key}
                    target={target}
                    workflow={workflow}
                    agent={agent}
                    isExpanded={isExpanded}
                    onToggleExpand={() => setExpandedTargetKey((prev) => (prev === target.key ? null : target.key))}
                    onEdit={() => handleEdit(target)}
                    onCopy={() => void handleCopy(target)}
                    onDelete={() => setDeleteTarget(target)}
                    isCopying={copyingTargetKey === target.key}
                    disableCopy={isCopyingAny}
                    isDeleting={isDeleting}
                    disableDelete={disableDelete}
                    isDetailLoading={isExpanded ? (isWorkflow ? isLoadingExpandedWorkflow : isLoadingExpandedAgent) : false}
                  />
                )
              })}
            </div>
          )}
        </SettingsSection>
      </div>

      <ConfirmDialog
        isOpen={!!deleteTarget}
        title={t('settings.skills.deleteTargetTitle')}
        description={t('settings.skills.deleteTargetDescription')}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return
          executeDelete(deleteTarget, false)
        }}
        isLoading={deleteWorkflowMutation.isPending || deleteAgentMutation.isPending}
      />

      <ConfirmDialog
        isOpen={!!deleteRebindConflict}
        title={t('settings.systemBehaviors.deleteRebindTitle')}
        description={t('settings.systemBehaviors.deleteRebindDescription', {
          count: deleteRebindConflict?.behaviorKeys.length ?? 0,
        })}
        onCancel={() => setDeleteRebindConflict(null)}
        onConfirm={() => {
          if (!deleteRebindConflict) return
          executeDelete(deleteRebindConflict.target, true)
        }}
        isLoading={deleteWorkflowMutation.isPending || deleteAgentMutation.isPending}
        confirmText={t('settings.systemBehaviors.confirmRebindDelete')}
      />
    </SettingsPageShell>
  )
}
