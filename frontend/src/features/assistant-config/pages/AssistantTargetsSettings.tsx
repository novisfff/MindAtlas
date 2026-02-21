import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ChevronDown, Loader2, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  useAgentProfilesQuery,
  useCreateAgentProfileMutation,
  useCreateWorkflowMutation,
  useDeleteAgentProfileMutation,
  useDeleteWorkflowMutation,
  useSkillsQuery,
  useUpdateAgentProfileMutation,
  useUpdateWorkflowMutation,
  useWorkflowsQuery,
} from '../queries'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { buildAssistantExecutableTargets, type AssistantExecutableTarget } from '../components/skillTargetOptions'
import { AssistantTargetCard } from '../components/AssistantTargetCard'

type CreateTargetType = 'workflow' | 'agent' | null

export function AssistantTargetsSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: workflows = [], isLoading: isLoadingWorkflows } = useWorkflowsQuery()
  const { data: agents = [], isLoading: isLoadingAgents } = useAgentProfilesQuery()
  const { data: skills = [] } = useSkillsQuery()

  const createWorkflowMutation = useCreateWorkflowMutation()
  const createAgentMutation = useCreateAgentProfileMutation()
  const updateWorkflowMutation = useUpdateWorkflowMutation()
  const updateAgentMutation = useUpdateAgentProfileMutation()
  const deleteWorkflowMutation = useDeleteWorkflowMutation()
  const deleteAgentMutation = useDeleteAgentProfileMutation()

  const [createType, setCreateType] = useState<CreateTargetType>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<AssistantExecutableTarget | null>(null)
  const [expandedTargetKey, setExpandedTargetKey] = useState<string | null>(null)
  const normalizationDoneRef = useRef(false)
  const normalizationInFlightRef = useRef(false)

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

  const isLoading = isLoadingWorkflows || isLoadingAgents
  const isCreating = createWorkflowMutation.isPending || createAgentMutation.isPending

  useEffect(() => {
    if (isLoading) return
    if (normalizationDoneRef.current || normalizationInFlightRef.current) return

    const disabledWorkflows = workflows.filter((item) => !item.enabled)
    const disabledAgents = agents.filter((item) => !item.enabled)
    if (disabledWorkflows.length === 0 && disabledAgents.length === 0) {
      normalizationDoneRef.current = true
      return
    }

    normalizationInFlightRef.current = true
      ; (async () => {
        const failed: string[] = []

        await Promise.all([
          ...disabledWorkflows.map(async (item) => {
            try {
              await updateWorkflowMutation.mutateAsync({
                id: item.id,
                data: { enabled: true },
              })
            } catch {
              failed.push(item.name)
            }
          }),
          ...disabledAgents.map(async (item) => {
            try {
              await updateAgentMutation.mutateAsync({
                id: item.id,
                data: { enabled: true },
              })
            } catch {
              failed.push(item.name)
            }
          }),
        ])

        await Promise.all([
          qc.invalidateQueries({ queryKey: ['assistant-workflows'] }),
          qc.invalidateQueries({ queryKey: ['assistant-agents'] }),
        ])

        if (failed.length > 0) {
          toast.error(t('settings.skills.targetNormalizationError'))
        }
      })().finally(() => {
        normalizationInFlightRef.current = false
        normalizationDoneRef.current = true
      })
  }, [agents, isLoading, qc, t, updateAgentMutation, updateWorkflowMutation, workflows])

  return (
    <div className="max-w-5xl mx-auto py-8 px-6 space-y-8">
      {/* Header Section */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/settings')}
              className="p-1.5 -ml-2 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <h1 className="text-3xl font-bold tracking-tight text-foreground">{t('pages.settings.assistantTargets')}</h1>
          </div>
          <p className="text-muted-foreground max-w-2xl text-base">{t('pages.settings.assistantTargetsDesc')}</p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => beginCreate('workflow')}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-xl bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" />
            {t('settings.skills.createWorkflow')}
          </button>
          <button
            onClick={() => beginCreate('agent')}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-xl border bg-background shadow-sm hover:bg-muted/50 transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" />
            {t('settings.skills.createAgent')}
          </button>
        </div>
      </div>

      <div className="space-y-6">
        {/* Creation Form */}
        {createType && (
          <div className="animate-in fade-in slide-in-from-top-4 duration-300">
            <div className="rounded-xl border bg-card/50 p-6 space-y-6 shadow-sm ring-1 ring-border/50">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-lg">
                  {createType === 'workflow' ? t('settings.skills.createWorkflow') : t('settings.skills.createAgent')}
                </h3>
                <button onClick={resetCreateForm} className="text-muted-foreground hover:text-foreground">
                  <ChevronDown className="w-5 h-5" />
                </button>
              </div>

              <div className="grid gap-5">
                <div className="grid gap-2">
                  <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70">
                    {t('settings.skills.name')}
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="flex h-10 w-full rounded-lg border bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
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
                    className="flex h-10 w-full rounded-lg border bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    placeholder={t('settings.skills.description')}
                  />
                </div>

              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  onClick={resetCreateForm}
                  className="px-4 py-2 rounded-lg border bg-background hover:bg-muted/50 transition-colors text-sm font-medium"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={
                    isCreating
                    || !name.trim()
                  }
                  className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors text-sm font-medium disabled:opacity-50 shadow-sm"
                >
                  {isCreating ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                  {t('common.create')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* List */}
        {isLoading ? (
          <div className="flex flex-col items-center justify-center py-20 gap-4 text-muted-foreground">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
            <p className="text-sm">{t('messages.loading')}</p>
          </div>
        ) : (
          <div className="grid gap-4">
            {targets.map((target) => {
              const isWorkflow = target.type === 'workflow'
              const workflow = isWorkflow ? workflowById.get(target.id) : undefined
              const agent = isWorkflow ? undefined : agentById.get(target.id)
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
                  isExpanded={expandedTargetKey === target.key}
                  onToggleExpand={() => setExpandedTargetKey((prev) => (prev === target.key ? null : target.key))}
                  onEdit={() => handleEdit(target)}
                  onDelete={() => setDeleteTarget(target)}
                  isDeleting={isDeleting}
                  disableDelete={disableDelete}
                />
              )
            })}
            {targets.length === 0 && (
              <div className="text-center py-16 border rounded-xl border-dashed bg-muted/10">
                <p className="text-muted-foreground">{t('settings.skills.noTargetsFound')}</p>
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={!!deleteTarget}
        title={t('settings.skills.deleteTargetTitle')}
        description={t('settings.skills.deleteTargetDescription')}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return
          if (deleteTarget.type === 'workflow') {
            deleteWorkflowMutation.mutate(deleteTarget.id, {
              onSuccess: () => setDeleteTarget(null),
              onError: (error) => {
                const message = error instanceof Error ? error.message : t('messages.error')
                toast.error(message)
              },
            })
            return
          }
          deleteAgentMutation.mutate(deleteTarget.id, {
            onSuccess: () => setDeleteTarget(null),
            onError: (error) => {
              const message = error instanceof Error ? error.message : t('messages.error')
              toast.error(message)
            },
          })
        }}
        isLoading={deleteWorkflowMutation.isPending || deleteAgentMutation.isPending}
      />
    </div>
  )
}
