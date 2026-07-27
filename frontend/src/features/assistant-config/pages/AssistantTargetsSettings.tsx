import { useEffect, useMemo, useState } from 'react'
import { DndContext, useDroppable, type DragEndEvent } from '@dnd-kit/core'
import { useNavigate } from 'react-router-dom'
import { FolderPlus, Home, Loader2, Plus, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  useAgentProfilesQuery,
  useAgentProfileDetailQuery,
  useCopyAgentProfileMutation,
  useCopyWorkflowMutation,
  useCreateAgentProfileMutation,
  useCreateTargetFolderMutation,
  useCreateWorkflowMutation,
  useDeleteAgentProfileMutation,
  useDeleteTargetFolderMutation,
  useDeleteWorkflowMutation,
  useMoveTargetFolderMutation,
  useMoveTargetToFolderMutation,
  useTargetFoldersQuery,
  useUpdateTargetFolderMutation,
  useWorkflowDetailQuery,
  useWorkflowsQuery,
} from '../queries'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { uiField } from '@/components/ui/styles'
import { buildAssistantExecutableTargets, type AssistantExecutableTarget } from '../components/skillTargetOptions'
import { AssistantTargetCard } from '../components/AssistantTargetCard'
import { AssistantFolderCard, type FolderMoveOption } from '../components/AssistantFolderCard'
import {
  SettingsBadge,
  SettingsEmptyState,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { isApiError } from '@/lib/api/client'
import type { AssistantTargetFolder } from '../api/target-folders'

type CreateTargetType = 'workflow' | 'agent' | null
type DeleteRebindConflict = {
  target: AssistantExecutableTarget
  behaviorKeys: string[]
}
type TargetFilter = 'all' | 'unfiled' | 'workflow' | 'agent'
type FolderDialogState =
  | { mode: 'create'; folder: null }
  | { mode: 'edit'; folder: AssistantTargetFolder }
  | null
type DeleteState =
  | { type: 'target'; target: AssistantExecutableTarget }
  | { type: 'folder'; folder: AssistantTargetFolder }
  | null

function normalizeSearch(value: string) {
  return value.trim().toLowerCase()
}

function matchesSearch(query: string, ...values: Array<string | undefined | null>) {
  const normalized = normalizeSearch(query)
  if (!normalized) return true
  return values.some((value) => String(value || '').toLowerCase().includes(normalized))
}

function folderPathLabel(folder: AssistantTargetFolder | undefined, rootLabel: string) {
  if (!folder) return rootLabel
  return folder.path.map((item) => item.name).join(' / ')
}

function RootDropZone({
  folderId,
  label,
  onClick,
  active,
}: {
  folderId: string | null
  label: string
  onClick: () => void
  active: boolean
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: folderId ? `crumb-drop:${folderId}` : 'crumb-drop:root',
    data: { kind: 'folder-drop', folderId },
  })

  return (
    <button
      ref={setNodeRef}
      type="button"
      onClick={onClick}
      className={[
        'rounded-full px-3 py-1.5 text-sm transition',
        active ? 'bg-foreground text-background' : 'bg-muted/50 text-muted-foreground hover:bg-muted',
        isOver ? 'ring-2 ring-primary/25' : '',
      ].join(' ')}
    >
      {label}
    </button>
  )
}

export function AssistantTargetsSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { data: workflows = [], isLoading: isLoadingWorkflows } = useWorkflowsQuery()
  const { data: agents = [], isLoading: isLoadingAgents } = useAgentProfilesQuery()
  const { data: folders = [], isLoading: isLoadingFolders } = useTargetFoldersQuery()

  const createWorkflowMutation = useCreateWorkflowMutation()
  const createAgentMutation = useCreateAgentProfileMutation()
  const createFolderMutation = useCreateTargetFolderMutation()
  const updateFolderMutation = useUpdateTargetFolderMutation()
  const deleteFolderMutation = useDeleteTargetFolderMutation()
  const moveTargetMutation = useMoveTargetToFolderMutation()
  const moveFolderMutation = useMoveTargetFolderMutation()
  const copyWorkflowMutation = useCopyWorkflowMutation()
  const copyAgentMutation = useCopyAgentProfileMutation()
  const deleteWorkflowMutation = useDeleteWorkflowMutation()
  const deleteAgentMutation = useDeleteAgentProfileMutation()

  const [createType, setCreateType] = useState<CreateTargetType>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<TargetFilter>('all')
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null)
  const [deleteState, setDeleteState] = useState<DeleteState>(null)
  const [deleteRebindConflict, setDeleteRebindConflict] = useState<DeleteRebindConflict | null>(null)
  const [expandedTargetKey, setExpandedTargetKey] = useState<string | null>(null)
  const [copyingTargetKey, setCopyingTargetKey] = useState<string | null>(null)
  const [folderDialog, setFolderDialog] = useState<FolderDialogState>(null)
  const [folderName, setFolderName] = useState('')
  const [folderDescription, setFolderDescription] = useState('')
  const [folderColor, setFolderColor] = useState('slate')

  // Infer system default via general_chat__* target names — legacy skills GET is gone.
  const targets = useMemo(
    () => buildAssistantExecutableTargets(workflows, agents),
    [agents, workflows],
  )

  const workflowById = useMemo(
    () => new Map(workflows.map((item) => [item.id, item])),
    [workflows],
  )
  const agentById = useMemo(
    () => new Map(agents.map((item) => [item.id, item])),
    [agents],
  )
  const folderById = useMemo(
    () => new Map(folders.map((item) => [item.id, item])),
    [folders],
  )

  useEffect(() => {
    if (currentFolderId && !folderById.has(currentFolderId)) {
      setCurrentFolderId(null)
    }
  }, [currentFolderId, folderById])

  const currentFolder = currentFolderId ? folderById.get(currentFolderId) ?? null : null
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

  const openFolderDialog = (mode: 'create' | 'edit', folder?: AssistantTargetFolder) => {
    if (mode === 'create') {
      setFolderDialog({ mode, folder: null })
      setFolderName('')
      setFolderDescription('')
      setFolderColor('slate')
      return
    }
    if (!folder) return
    setFolderDialog({ mode, folder })
    setFolderName(folder.name)
    setFolderDescription(folder.description)
    setFolderColor(folder.colorToken)
  }

  const resetCreateForm = () => {
    setCreateType(null)
    setName('')
    setDescription('')
  }

  const rootLabel = t('settings.skills.rootFolder')
  const searchActive = normalizeSearch(searchQuery).length > 0

  const moveOptions = useMemo<FolderMoveOption[]>(() => ([
    { id: null, label: rootLabel },
    ...folders
      .slice()
      .sort((a, b) => a.path.length - b.path.length || a.name.localeCompare(b.name))
      .map((folder) => ({
        id: folder.id,
        label: folder.path.map((item) => item.name).join(' / '),
      })),
  ]), [folders, rootLabel])

  const visibleFolders = useMemo(() => {
    return folders
      .filter((folder) => {
        if (searchActive) {
          if (!matchesSearch(searchQuery, folder.name, folder.description, folder.path.map((item) => item.name).join(' / '))) {
            return false
          }
        } else if (folder.parentId !== currentFolderId) {
          return false
        }
        if (typeFilter === 'workflow' || typeFilter === 'agent' || typeFilter === 'unfiled') {
          return false
        }
        return true
      })
      .map((folder) => ({
        kind: 'folder' as const,
        date: new Date(folder.lastActivityAt).getTime(),
        folder,
      }))
  }, [folders, currentFolderId, searchActive, searchQuery, typeFilter])

  const visibleTargets = useMemo(() => {
    return targets
      .filter((target) => {
        if (searchActive) {
          const folder = target.folderId ? folderById.get(target.folderId) : undefined
          return matchesSearch(
            searchQuery,
            target.name,
            target.description,
            folder ? folder.path.map((item) => item.name).join(' / ') : rootLabel,
          )
        }

        if (typeFilter === 'unfiled') {
          return currentFolderId === null && (target.folderId ?? null) === null
        }

        return (target.folderId ?? null) === currentFolderId
      })
      .filter((target) => typeFilter === 'all' || typeFilter === 'unfiled' || target.type === typeFilter)
      .map((target) => {
        const workflow = workflowById.get(target.id)
        const agent = agentById.get(target.id)
        const updatedAt = target.type === 'workflow' ? workflow?.updatedAt : agent?.updatedAt
        return {
          kind: 'target' as const,
          date: updatedAt ? new Date(updatedAt).getTime() : 0,
          target,
        }
      })
  }, [targets, searchActive, searchQuery, folderById, rootLabel, typeFilter, currentFolderId, workflowById, agentById])

  const entries = useMemo(
    () => [...visibleFolders, ...visibleTargets].sort((a, b) => b.date - a.date),
    [visibleFolders, visibleTargets],
  )

  const breadcrumbs = useMemo(() => {
    if (!currentFolder) return []
    return currentFolder.path
      .map((item) => folderById.get(item.id))
      .filter((item): item is AssistantTargetFolder => Boolean(item))
  }, [currentFolder, folderById])

  const currentFolderMoveOptions = useMemo(
    () => moveOptions,
    [moveOptions],
  )

  const folderMoveOptionsFor = (folder: AssistantTargetFolder): FolderMoveOption[] => moveOptions.map((option) => {
    if (option.id === null) return option
    if (option.id === folder.id) return { ...option, disabled: true }
    const candidate = folderById.get(option.id)
    const isDescendant = Boolean(candidate?.path.some((pathItem) => pathItem.id === folder.id))
    return isDescendant ? { ...option, disabled: true } : option
  })

  const handleCreate = async () => {
    const trimmedName = name.trim()
    if (!trimmedName) return

    try {
      if (createType === 'workflow') {
        const created = await createWorkflowMutation.mutateAsync({
          name: trimmedName,
          description: description.trim(),
          folderId: currentFolderId,
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
          folderId: currentFolderId,
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

  const handleSaveFolder = async () => {
    const trimmedName = folderName.trim()
    if (!trimmedName) return

    try {
      if (folderDialog?.mode === 'create') {
        await createFolderMutation.mutateAsync({
          name: trimmedName,
          description: folderDescription.trim(),
          parentId: currentFolderId,
          colorToken: folderColor,
        })
        toast.success(t('settings.skills.folderCreated'))
      } else if (folderDialog?.mode === 'edit') {
        await updateFolderMutation.mutateAsync({
          id: folderDialog.folder.id,
          data: {
            name: trimmedName,
            description: folderDescription.trim(),
            colorToken: folderColor,
          },
        })
        toast.success(t('settings.skills.folderUpdated'))
      }
      setFolderDialog(null)
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

  const executeDeleteTarget = (
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
          setDeleteState(null)
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
            setDeleteState(null)
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

  const handleMoveTarget = async (target: AssistantExecutableTarget, folderId: string | null) => {
    try {
      await moveTargetMutation.mutateAsync({
        targetType: target.type,
        targetId: target.id,
        folderId,
      })
      toast.success(t('settings.skills.movedToFolder', { name: folderId ? folderById.get(folderId)?.name ?? rootLabel : rootLabel }))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  const handleMoveFolder = async (folder: AssistantTargetFolder, parentId: string | null) => {
    try {
      await moveFolderMutation.mutateAsync({
        folderId: folder.id,
        parentId,
      })
      toast.success(t('settings.skills.folderMoved'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('messages.error')
      toast.error(message)
    }
  }

  const handleOpenFolder = (folderId: string | null) => {
    setCurrentFolderId(folderId)
    setSearchQuery('')
  }

  const isLoading = isLoadingWorkflows || isLoadingAgents || isLoadingFolders
  const isCreating = createWorkflowMutation.isPending || createAgentMutation.isPending
  const isCopyingAny = copyWorkflowMutation.isPending || copyAgentMutation.isPending

  const handleDragEnd = async ({ active, over }: DragEndEvent) => {
    if (!over || !active.data.current) return
    const overData = over.data.current as { kind?: string; folderId?: string | null } | undefined
    if (overData?.kind !== 'folder-drop') return

    const activeData = active.data.current as {
      kind?: 'target' | 'folder'
      targetType?: 'workflow' | 'agent'
      targetId?: string
      folderId?: string
    }

    if (activeData.kind === 'target' && activeData.targetType && activeData.targetId) {
      const target = targets.find((item) => item.id === activeData.targetId && item.type === activeData.targetType)
      if (!target || (target.folderId ?? null) === (overData.folderId ?? null)) return
      await handleMoveTarget(target, overData.folderId ?? null)
      return
    }

    if (activeData.kind === 'folder' && activeData.folderId) {
      const folder = folderById.get(activeData.folderId)
      if (!folder || folder.parentId === (overData.folderId ?? null)) return
      if (overData.folderId && folderById.get(overData.folderId)?.path.some((item) => item.id === folder.id)) {
        toast.error(t('settings.skills.folderCycleError'))
        return
      }
      await handleMoveFolder(folder, overData.folderId ?? null)
    }
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.assistantTargets')}
        description={t('pages.settings.assistantTargetsDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => openFolderDialog('create')} variant="outline">
              <FolderPlus className="h-4 w-4" />
              {t('settings.skills.createFolder')}
            </Button>
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
          <SettingsSection className="space-y-6">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-foreground">
                {createType === 'workflow' ? t('settings.skills.createWorkflow') : t('settings.skills.createAgent')}
              </h3>
              {currentFolder ? <SettingsBadge>{folderPathLabel(currentFolder, rootLabel)}</SettingsBadge> : null}
            </div>

            <div className="grid gap-5 md:grid-cols-2">
              <div className="grid gap-2">
                <label className="text-sm font-medium">{t('settings.skills.name')}</label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className={uiField.input}
                  placeholder={t('settings.skills.name')}
                  autoFocus
                />
              </div>

              <div className="grid gap-2">
                <label className="text-sm font-medium">{t('settings.skills.description')}</label>
                <input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className={uiField.input}
                  placeholder={t('settings.skills.description')}
                />
              </div>
            </div>

            <div className="flex justify-end gap-3">
              <Button type="button" onClick={resetCreateForm} variant="outline">
                {t('common.cancel')}
              </Button>
              <Button type="button" onClick={handleCreate} disabled={isCreating || !name.trim()}>
                {isCreating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                {t('common.create')}
              </Button>
            </div>
          </SettingsSection>
        )}

        <SettingsSection className="space-y-5 overflow-hidden">
          <SettingsSectionHeader
            title={
              <span className="flex items-center gap-3">
                <span>{t('pages.settings.assistantTargets')}</span>
                <SettingsBadge>{folders.length + targets.length}</SettingsBadge>
              </span>
            }
            description={t('settings.skills.folderWorkspaceDescription')}
          />

          <DndContext onDragEnd={(event) => void handleDragEnd(event)}>
          <div className="rounded-[22px] border border-border/70 bg-[radial-gradient(circle_at_top_left,rgba(148,163,184,0.12),transparent_28%),linear-gradient(180deg,rgba(255,255,255,0.92),rgba(248,250,252,0.88))] p-4 dark:bg-[radial-gradient(circle_at_top_left,rgba(71,85,105,0.16),transparent_30%),linear-gradient(180deg,rgba(15,23,42,0.92),rgba(15,23,42,0.84))]">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                <RootDropZone
                  folderId={null}
                  label={rootLabel}
                  active={currentFolderId === null}
                  onClick={() => handleOpenFolder(null)}
                />
                {breadcrumbs.map((folder) => (
                  <RootDropZone
                    key={folder.id}
                    folderId={folder.id}
                    label={folder.name}
                    active={currentFolderId === folder.id}
                    onClick={() => handleOpenFolder(folder.id)}
                  />
                ))}
              </div>

              <div className="flex flex-col gap-3 sm:flex-row">
                <div className="relative min-w-[260px] flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className={`${uiField.input} pl-10`}
                    placeholder={t('settings.skills.searchTargets')}
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  {(['all', 'unfiled', 'workflow', 'agent'] as TargetFilter[]).map((item) => {
                    if (item === 'unfiled' && currentFolderId !== null) return null
                    return (
                      <button
                        key={item}
                        type="button"
                        onClick={() => setTypeFilter(item)}
                        className={[
                          'rounded-full px-3 py-1.5 text-sm transition',
                          typeFilter === item
                            ? 'bg-foreground text-background'
                            : 'bg-background/80 text-muted-foreground hover:bg-background',
                        ].join(' ')}
                      >
                        {t(`settings.skills.targetFilter.${item}`)}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-2">
                <Home className="h-4 w-4" />
                {currentFolder ? folderPathLabel(currentFolder, rootLabel) : rootLabel}
              </span>
              <span>{t('settings.skills.workspaceSummary', { count: entries.length })}</span>
              {searchActive ? <SettingsBadge>{t('settings.skills.searchMode')}</SettingsBadge> : null}
            </div>
          </div>

          {isLoading ? (
            <div className="flex flex-col items-center justify-center gap-4 py-20 text-muted-foreground">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm">{t('messages.loading')}</p>
            </div>
          ) : entries.length === 0 ? (
            <SettingsEmptyState
              title={currentFolder ? t('settings.skills.emptyFolderTitle') : t('settings.skills.emptyWorkspaceTitle')}
              description={currentFolder ? t('settings.skills.emptyFolderDescription') : t('settings.skills.emptyWorkspaceDescription')}
              action={
                <div className="flex gap-3">
                  <Button type="button" variant="outline" onClick={() => openFolderDialog('create')}>
                    {t('settings.skills.createFolder')}
                  </Button>
                  <Button type="button" onClick={() => beginCreate('workflow')}>
                    {t('settings.skills.createWorkflow')}
                  </Button>
                </div>
              }
            />
          ) : (
              <div className="grid gap-4">
                {entries.map((entry) => {
                  if (entry.kind === 'folder') {
                    return (
                      <AssistantFolderCard
                        key={entry.folder.id}
                        folder={entry.folder}
                        pathLabel={searchActive ? folderPathLabel(entry.folder, rootLabel) : undefined}
                        moveOptions={folderMoveOptionsFor(entry.folder)}
                        onOpen={() => handleOpenFolder(entry.folder.id)}
                        onEdit={() => openFolderDialog('edit', entry.folder)}
                        onDelete={() => setDeleteState({ type: 'folder', folder: entry.folder })}
                        onMove={(parentId) => void handleMoveFolder(entry.folder, parentId)}
                        disableActions={deleteFolderMutation.isPending || moveFolderMutation.isPending}
                      />
                    )
                  }

                  const target = entry.target
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
                  ) && deleteState?.type === 'target' && deleteState.target.key === target.key
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
                      onDelete={() => setDeleteState({ type: 'target', target })}
                      onMove={(folderId) => void handleMoveTarget(target, folderId)}
                      isCopying={copyingTargetKey === target.key}
                      disableCopy={isCopyingAny}
                      isDeleting={isDeleting}
                      disableDelete={disableDelete}
                      disableMove={moveTargetMutation.isPending}
                      isDetailLoading={isExpanded ? (isWorkflow ? isLoadingExpandedWorkflow : isLoadingExpandedAgent) : false}
                      pathLabel={searchActive ? folderPathLabel(target.folderId ? folderById.get(target.folderId) : undefined, rootLabel) : undefined}
                      moveOptions={currentFolderMoveOptions}
                    />
                  )
                })}
              </div>
          )}
          </DndContext>
        </SettingsSection>
      </div>

      <Dialog open={!!folderDialog} onOpenChange={(open) => !open && setFolderDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {folderDialog?.mode === 'edit' ? t('settings.skills.editFolder') : t('settings.skills.createFolder')}
            </DialogTitle>
            <DialogDescription>{t('settings.skills.folderDialogDescription')}</DialogDescription>
          </DialogHeader>

          <div className="grid gap-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium">{t('settings.skills.name')}</label>
              <input value={folderName} onChange={(e) => setFolderName(e.target.value)} className={uiField.input} />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">{t('settings.skills.description')}</label>
              <input value={folderDescription} onChange={(e) => setFolderDescription(e.target.value)} className={uiField.input} />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">{t('settings.skills.folderColor')}</label>
              <select value={folderColor} onChange={(e) => setFolderColor(e.target.value)} className={uiField.select}>
                {['slate', 'amber', 'emerald', 'sky', 'rose'].map((color) => (
                  <option key={color} value={color}>{t(`settings.skills.folderColors.${color}`)}</option>
                ))}
              </select>
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setFolderDialog(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              onClick={() => void handleSaveFolder()}
              disabled={!folderName.trim() || createFolderMutation.isPending || updateFolderMutation.isPending}
            >
              {createFolderMutation.isPending || updateFolderMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {folderDialog?.mode === 'edit' ? t('common.save') : t('common.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        isOpen={deleteState?.type === 'target'}
        title={t('settings.skills.deleteTargetTitle')}
        description={t('settings.skills.deleteTargetDescription')}
        onCancel={() => setDeleteState(null)}
        onConfirm={() => {
          if (deleteState?.type !== 'target') return
          executeDeleteTarget(deleteState.target, false)
        }}
        isLoading={deleteWorkflowMutation.isPending || deleteAgentMutation.isPending}
      />

      <ConfirmDialog
        isOpen={deleteState?.type === 'folder'}
        title={t('settings.skills.deleteFolderTitle')}
        description={t('settings.skills.deleteFolderDescription')}
        onCancel={() => setDeleteState(null)}
        onConfirm={() => {
          if (deleteState?.type !== 'folder') return
          deleteFolderMutation.mutate(deleteState.folder.id, {
            onSuccess: () => {
              if (currentFolderId === deleteState.folder.id) {
                setCurrentFolderId(deleteState.folder.parentId ?? null)
              }
              setDeleteState(null)
            },
            onError: (error) => {
              const message = error instanceof Error ? error.message : t('messages.error')
              toast.error(message)
            },
          })
        }}
        isLoading={deleteFolderMutation.isPending}
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
          executeDeleteTarget(deleteRebindConflict.target, true)
        }}
        isLoading={deleteWorkflowMutation.isPending || deleteAgentMutation.isPending}
        confirmText={t('settings.systemBehaviors.confirmRebindDelete')}
      />
    </SettingsPageShell>
  )
}
