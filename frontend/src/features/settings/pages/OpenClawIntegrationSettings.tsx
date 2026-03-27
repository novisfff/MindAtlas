import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Boxes,
  CheckCircle2,
  ChevronDown,
  Copy,
  KeyRound,
  Loader2,
  Pencil,
  PlugZap,
  Plus,
  RefreshCcw,
  ShieldCheck,
  Trash2,
  Wrench,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import {
  createOpenClawCatalogItem,
  deleteOpenClawCatalogItem,
  getOpenClawCatalogSources,
  getOpenClawIntegrationSettings,
  resetOpenClawSystemPresets,
  rotateOpenClawIntegrationSecret,
  updateOpenClawCatalogItem,
  updateOpenClawIntegrationSettings,
  type OpenClawCatalogItem,
  type OpenClawCatalogItemUpdateRequest,
  type OpenClawCatalogItemUpsertRequest,
  type OpenClawCatalogSource,
  type OpenClawCatalogSourceType,
  type OpenClawToolResponseMode,
} from '../api/openclaw-integration'
import { isApiError } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import { InputField, Label, TEXTAREA_CLASSNAME, TextareaField } from '@/features/system-setup'

const settingsQueryKey = ['openclaw-integration-settings'] as const

type DraftMode = 'create' | 'edit'

interface CatalogItemDraft {
  sourceType: OpenClawCatalogSourceType
  toolName: string
  title: string
  description: string
  enabled: boolean
  inputSummary: string
  outputSummary: string
  inputSchemaText: string
  outputSchemaText: string
  toolResponseMode: OpenClawToolResponseMode
  sourceToolName: string | null
  toolId: string | null
  workflowId: string | null
  agentProfileId: string | null
  schemaEditable: boolean
}

function stringifySchema(value: Record<string, unknown> | null | undefined) {
  return JSON.stringify(value ?? {}, null, 2)
}

function parseSchemaText(text: string, invalidJsonMessage: string, invalidObjectMessage: string) {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error(invalidJsonMessage)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(invalidObjectMessage)
  }
  return parsed as Record<string, unknown>
}

function buildSourceKey(item: Pick<OpenClawCatalogItem, 'sourceType' | 'toolId' | 'workflowId' | 'agentProfileId' | 'sourceToolName'>) {
  if (item.sourceType === 'tool') {
    if (item.toolId) return `tool:${item.toolId}`
    if (item.sourceToolName) return `system:${item.sourceToolName}`
  }
  if (item.sourceType === 'workflow' && item.workflowId) return `workflow:${item.workflowId}`
  if (item.sourceType === 'agent' && item.agentProfileId) return `agent:${item.agentProfileId}`
  return null
}

function createDraftFromItem(item: OpenClawCatalogItem): CatalogItemDraft {
  return {
    sourceType: item.sourceType === 'system_adapter' ? 'tool' : item.sourceType,
    toolName: item.toolName,
    title: item.title,
    description: item.description,
    enabled: item.enabled,
    inputSummary: item.inputSummary,
    outputSummary: item.outputSummary,
    inputSchemaText: stringifySchema(item.inputSchema),
    outputSchemaText: stringifySchema(item.outputSchema),
    toolResponseMode: item.toolResponseMode,
    sourceToolName: item.sourceToolName ?? null,
    toolId: item.toolId ?? null,
    workflowId: item.workflowId ?? null,
    agentProfileId: item.agentProfileId ?? null,
    schemaEditable: item.schemaEditable,
  }
}

function createEmptyDraft(sourceType: OpenClawCatalogSourceType): CatalogItemDraft {
  return {
    sourceType,
    toolName: '',
    title: '',
    description: '',
    enabled: true,
    inputSummary: '',
    outputSummary: '',
    inputSchemaText: stringifySchema({ type: 'object', properties: {}, required: [], additionalProperties: false }),
    outputSchemaText: stringifySchema({ type: 'object', properties: {}, required: [], additionalProperties: false }),
    toolResponseMode: 'json_schema',
    sourceToolName: null,
    toolId: null,
    workflowId: null,
    agentProfileId: null,
    schemaEditable: true,
  }
}

function CapabilityBadge({
  colorClassName,
  children,
}: {
  colorClassName: string
  children: React.ReactNode
}) {
  return (
    <Badge variant="outline" className={cn('rounded-full', colorClassName)}>
      {children}
    </Badge>
  )
}

function SectionHeader({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
      <div className="space-y-1.5">
        <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
        <p className="max-w-3xl text-sm leading-6 text-slate-600">{description}</p>
      </div>
      {action}
    </div>
  )
}

function CatalogItemCard({
  item,
  onToggle,
  onEdit,
  onDelete,
  typeLabel,
  systemLabel,
  userLabel,
  exposedLabel,
  hiddenLabel,
  availableLabel,
  unavailableLabel,
  inputLabel,
  outputLabel,
}: {
  item: OpenClawCatalogItem
  onToggle: (enabled: boolean) => void
  onEdit: () => void
  onDelete?: () => void
  typeLabel: string
  systemLabel: string
  userLabel: string
  exposedLabel: string
  hiddenLabel: string
  availableLabel: string
  unavailableLabel: string
  inputLabel: string
  outputLabel: string
}) {
  return (
    <article className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold text-slate-900">{item.title}</h3>
              <CapabilityBadge colorClassName="border-slate-200 bg-slate-50 text-slate-700">
                {typeLabel}
              </CapabilityBadge>
              <CapabilityBadge
                colorClassName={
                  item.sourceIsSystem
                    ? 'border-cyan-200 bg-cyan-50 text-cyan-700'
                    : 'border-violet-200 bg-violet-50 text-violet-700'
                }
              >
                {item.sourceIsSystem ? systemLabel : userLabel}
              </CapabilityBadge>
              <CapabilityBadge
                colorClassName={
                  item.available
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : 'border-amber-200 bg-amber-50 text-amber-700'
                }
              >
                {item.available ? availableLabel : unavailableLabel}
              </CapabilityBadge>
            </div>
            <p className="text-sm leading-6 text-slate-600">{item.description || '-'}</p>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
                {item.toolName}
              </span>
              {item.sourceName ? (
                <span className="rounded-full bg-slate-100 px-2.5 py-1">
                  {item.sourceName}
                </span>
              ) : null}
              <span className="rounded-full bg-slate-100 px-2.5 py-1">
                {item.enabled ? exposedLabel : hiddenLabel}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 self-start">
            <div className="rounded-[22px] border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="flex items-center gap-3">
                <Switch checked={item.enabled} onCheckedChange={onToggle} />
                <span className="text-sm font-medium text-slate-800">
                  {item.enabled ? exposedLabel : hiddenLabel}
                </span>
              </div>
            </div>
            <Button type="button" variant="outline" className="rounded-2xl" onClick={onEdit}>
              <Pencil className="h-4 w-4" />
            </Button>
            {onDelete ? (
              <Button type="button" variant="outline" className="rounded-2xl text-rose-600" onClick={onDelete}>
                <Trash2 className="h-4 w-4" />
              </Button>
            ) : null}
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              {inputLabel}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-700">{item.inputSummary || '-'}</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              {outputLabel}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-700">{item.outputSummary || '-'}</p>
          </div>
        </div>

        {!item.available && item.availabilityReason ? (
          <div className="rounded-[22px] border border-amber-200 bg-amber-50/80 px-4 py-3 text-sm leading-6 text-amber-800">
            {item.availabilityReason}
          </div>
        ) : null}
      </div>
    </article>
  )
}

export function OpenClawIntegrationSettingsPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: getOpenClawIntegrationSettings,
  })

  const [revealedSecret, setRevealedSecret] = useState<string | null>(null)
  const [showRotateConfirm, setShowRotateConfirm] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<OpenClawCatalogItem | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogMode, setDialogMode] = useState<DraftMode>('create')
  const [editingItem, setEditingItem] = useState<OpenClawCatalogItem | null>(null)
  const [draft, setDraft] = useState<CatalogItemDraft>(createEmptyDraft('tool'))
  const [selectedSourceKey, setSelectedSourceKey] = useState<string | null>(null)
  const [guideOpen, setGuideOpen] = useState(false)

  const integrationMutation = useMutation({
    mutationFn: updateOpenClawIntegrationSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(settingsQueryKey, data)
    },
  })

  const rotateSecretMutation = useMutation({
    mutationFn: rotateOpenClawIntegrationSecret,
    onSuccess: (data) => {
      queryClient.setQueryData(settingsQueryKey, data.settings)
    },
  })

  const createItemMutation = useMutation({
    mutationFn: createOpenClawCatalogItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey })
    },
  })

  const updateItemMutation = useMutation({
    mutationFn: ({ itemId, payload }: { itemId: string; payload: OpenClawCatalogItemUpdateRequest }) =>
      updateOpenClawCatalogItem(itemId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey })
    },
  })

  const deleteItemMutation = useMutation({
    mutationFn: deleteOpenClawCatalogItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey })
    },
  })

  const resetMutation = useMutation({
    mutationFn: resetOpenClawSystemPresets,
    onSuccess: (data) => {
      queryClient.setQueryData(settingsQueryKey, data)
    },
  })

  const sourceQuery = useQuery({
    queryKey: ['openclaw-catalog-sources', draft.sourceType],
    queryFn: () => getOpenClawCatalogSources(draft.sourceType),
    enabled: dialogOpen && !editingItem?.isSystemPreset,
  })

  const current = settingsQuery.data ?? null
  const systemItems = useMemo(
    () => current?.catalogItems.filter((item) => item.isSystemPreset) ?? [],
    [current]
  )
  const customItems = useMemo(
    () => current?.catalogItems.filter((item) => !item.isSystemPreset) ?? [],
    [current]
  )
  const currentSources = sourceQuery.data?.items ?? []
  useEffect(() => {
    if (!dialogOpen || editingItem?.isSystemPreset) return
    if (selectedSourceKey) return
    const firstBindable = currentSources.find((item) => item.bindable)
    if (!firstBindable) return
    setSelectedSourceKey(firstBindable.sourceKey)
    patchDraftFromSource(firstBindable)
  }, [currentSources, dialogOpen, editingItem?.isSystemPreset, selectedSourceKey])

  const isBusy =
    settingsQuery.isLoading ||
    integrationMutation.isPending ||
    rotateSecretMutation.isPending ||
    createItemMutation.isPending ||
    updateItemMutation.isPending ||
    deleteItemMutation.isPending ||
    resetMutation.isPending

  const typeLabels: Record<string, string> = {
    system_adapter: t('openclawIntegration.types.systemAdapter'),
    tool: t('openclawIntegration.types.tool'),
    workflow: t('openclawIntegration.types.workflow'),
    agent: t('openclawIntegration.types.agent'),
  }
  const guideSteps = [
    {
      title: t('openclawIntegration.guide.steps.secret.title'),
      description: t('openclawIntegration.guide.steps.secret.description'),
    },
    {
      title: t('openclawIntegration.guide.steps.catalog.title'),
      description: t('openclawIntegration.guide.steps.catalog.description'),
    },
    {
      title: t('openclawIntegration.guide.steps.plugin.title'),
      description: t('openclawIntegration.guide.steps.plugin.description'),
      details: [
        t('openclawIntegration.guide.steps.plugin.details.install'),
        t('openclawIntegration.guide.steps.plugin.details.baseUrl'),
        t('openclawIntegration.guide.steps.plugin.details.refresh'),
      ],
    },
    {
      title: t('openclawIntegration.guide.steps.verify.title'),
      description: t('openclawIntegration.guide.steps.verify.description'),
    },
  ]
  const guideNotes = [
    t('openclawIntegration.guide.notes.catalogOnly'),
    t('openclawIntegration.guide.notes.skillBoundary'),
    t('openclawIntegration.guide.notes.refresh'),
  ]
  const pluginInstallBlocks = [
    {
      title: t('openclawIntegration.guide.installBlocks.locate.title'),
      description: t('openclawIntegration.guide.installBlocks.locate.description'),
      code: 'cd /path/to/MindAtlas',
    },
    {
      title: t('openclawIntegration.guide.installBlocks.install.title'),
      description: t('openclawIntegration.guide.installBlocks.install.description'),
      code: 'openclaw plugins install ./integrations/openclaw-mindatlas',
    },
    {
      title: t('openclawIntegration.guide.installBlocks.verify.title'),
      description: t('openclawIntegration.guide.installBlocks.verify.description'),
      code: 'openclaw plugins list',
    },
  ]
  const pluginConfigExample = `{
  "plugins": {
    "entries": {
      "openclaw-mindatlas": {
        "enabled": true,
        "config": {
          "baseUrl": "http://your-mindatlas-host:8000",
          "integrationSecret": "paste-the-secret-from-settings",
          "requestTimeoutMs": 15000,
          "catalogRefreshTtlSec": 300
        }
      }
    }
  }
}`
  const pluginConfigBlocks = [
    {
      title: t('openclawIntegration.guide.configBlocks.findConfig.title'),
      description: t('openclawIntegration.guide.configBlocks.findConfig.description'),
      code: 'openclaw config file',
    },
    {
      title: t('openclawIntegration.guide.configBlocks.validate.title'),
      description: t('openclawIntegration.guide.configBlocks.validate.description'),
      code: 'openclaw config validate',
    },
  ]
  const pluginVerifyChecklist = [
    t('openclawIntegration.guide.verifyChecklist.secret'),
    t('openclawIntegration.guide.verifyChecklist.catalog'),
    t('openclawIntegration.guide.verifyChecklist.plugin'),
    t('openclawIntegration.guide.verifyChecklist.call'),
  ]

  function patchDraft(patch: Partial<CatalogItemDraft>) {
    setDraft((currentDraft) => ({ ...currentDraft, ...patch }))
  }

  function patchDraftFromSource(source: OpenClawCatalogSource) {
    patchDraft({
      sourceType: source.sourceType,
      title: source.title,
      description: source.description,
      inputSummary: source.defaultInputSummary || '',
      outputSummary: source.defaultOutputSummary || '',
      inputSchemaText: stringifySchema(source.defaultInputSchema ?? {}),
      outputSchemaText: stringifySchema(source.defaultOutputSchema ?? {}),
      toolResponseMode: source.defaultToolResponseMode ?? 'json_schema',
      sourceToolName: source.sourceToolName ?? null,
      toolId: source.toolId ?? null,
      workflowId: source.workflowId ?? null,
      agentProfileId: source.agentProfileId ?? null,
      schemaEditable: source.schemaMode === 'editable',
      toolName:
        draft.toolName.trim() ||
        source.title
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '_')
          .replace(/^_+|_+$/g, '') ||
        '',
    })
  }

  function openCreateDialog(sourceType: OpenClawCatalogSourceType = 'tool') {
    setDialogMode('create')
    setEditingItem(null)
    setSelectedSourceKey(null)
    setDraft(createEmptyDraft(sourceType))
    setDialogOpen(true)
  }

  function openEditDialog(item: OpenClawCatalogItem) {
    setDialogMode('edit')
    setEditingItem(item)
    setDraft(createDraftFromItem(item))
    setSelectedSourceKey(buildSourceKey(item))
    setDialogOpen(true)
  }

  async function handleToggleIntegration(enabled: boolean) {
    try {
      await integrationMutation.mutateAsync({ enabled })
      toast.success(t('openclawIntegration.messages.saved'))
    } catch (error) {
      toast.error(isApiError(error) ? error.message : t('messages.error'))
    }
  }

  async function handleToggleItem(item: OpenClawCatalogItem, enabled: boolean) {
    try {
      await updateItemMutation.mutateAsync({
        itemId: item.id,
        payload: { enabled },
      })
      toast.success(t('openclawIntegration.messages.saved'))
    } catch (error) {
      toast.error(isApiError(error) ? error.message : t('messages.error'))
    }
  }

  async function handleRotateSecret() {
    try {
      const response = await rotateSecretMutation.mutateAsync()
      setRevealedSecret(response.secret)
      setShowRotateConfirm(false)
      toast.success(t('openclawIntegration.messages.secretGenerated'))
    } catch (error) {
      setShowRotateConfirm(false)
      toast.error(isApiError(error) ? error.message : t('messages.error'))
    }
  }

  async function handleCopySecret() {
    if (!revealedSecret) return
    try {
      await navigator.clipboard.writeText(revealedSecret)
      toast.success(t('openclawIntegration.messages.secretCopied'))
    } catch {
      toast.error(t('messages.error'))
    }
  }

  async function handleResetSystemPresets() {
    try {
      await resetMutation.mutateAsync()
      setShowResetConfirm(false)
      toast.success(t('openclawIntegration.messages.systemPresetsReset'))
    } catch (error) {
      setShowResetConfirm(false)
      toast.error(isApiError(error) ? error.message : t('messages.error'))
    }
  }

  async function handleDeleteItem() {
    if (!deleteTarget) return
    try {
      await deleteItemMutation.mutateAsync(deleteTarget.id)
      setDeleteTarget(null)
      toast.success(t('openclawIntegration.messages.deleted'))
    } catch (error) {
      toast.error(isApiError(error) ? error.message : t('messages.error'))
    }
  }

  async function handleSaveDialog() {
    try {
      const payload: OpenClawCatalogItemUpsertRequest = {
        sourceType: draft.sourceType,
        toolName: draft.toolName.trim(),
        title: draft.title.trim(),
        description: draft.description.trim(),
        enabled: draft.enabled,
        sourceToolName: draft.sourceToolName,
        toolId: draft.toolId,
        workflowId: draft.workflowId,
        agentProfileId: draft.agentProfileId,
      }

      if (editingItem?.isSystemPreset) {
        await updateItemMutation.mutateAsync({
          itemId: editingItem.id,
          payload: {
            enabled: draft.enabled,
            toolName: draft.toolName.trim(),
            title: draft.title.trim(),
            description: draft.description.trim(),
          },
        })
      } else {
        const inputSchema = parseSchemaText(
          draft.inputSchemaText,
          t('openclawIntegration.messages.invalidSchemaJson'),
          t('openclawIntegration.messages.invalidSchemaObject')
        )
        const outputSchema = parseSchemaText(
          draft.outputSchemaText,
          t('openclawIntegration.messages.invalidSchemaJson'),
          t('openclawIntegration.messages.invalidSchemaObject')
        )
        payload.inputSummary = draft.inputSummary.trim()
        payload.outputSummary = draft.outputSummary.trim()
        payload.inputSchema = inputSchema
        payload.outputSchema = outputSchema
        payload.toolResponseMode = draft.toolResponseMode

        if (dialogMode === 'create') {
          await createItemMutation.mutateAsync(payload)
        } else if (editingItem) {
          await updateItemMutation.mutateAsync({
            itemId: editingItem.id,
            payload: {
              ...payload,
              sourceToolName: draft.sourceToolName,
              toolId: draft.toolId,
              workflowId: draft.workflowId,
              agentProfileId: draft.agentProfileId,
            },
          })
        }
      }

      setDialogOpen(false)
      toast.success(t('openclawIntegration.messages.saved'))
    } catch (error) {
      toast.error(isApiError(error) ? error.message : error instanceof Error ? error.message : t('messages.error'))
    }
  }

  if (settingsQuery.isLoading || !current) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-col gap-4">
        <button
          type="button"
          onClick={() => navigate('/settings')}
          className="inline-flex items-center gap-2 text-sm text-slate-500 transition hover:text-slate-900"
        >
          <ArrowLeft className="h-4 w-4" />
          {t('common.back')}
        </button>
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
            {t('pages.settings.openClawIntegration')}
          </h1>
          <p className="max-w-3xl text-sm leading-7 text-slate-600">
            {t('pages.settings.openClawIntegrationDesc')}
          </p>
        </div>
      </div>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.9fr)]">
        <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
              <div className="flex items-start gap-4">
                <div className="rounded-[24px] bg-slate-900 p-3 text-white shadow-lg shadow-slate-900/10">
                  <PlugZap className="h-6 w-6" />
                </div>
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-xl font-semibold text-slate-900">
                      {t('openclawIntegration.overview.title')}
                    </h2>
                    <CapabilityBadge
                      colorClassName={
                        current.enabled
                          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : 'border-slate-200 bg-slate-50 text-slate-600'
                      }
                    >
                      {current.enabled
                        ? t('openclawIntegration.status.enabled')
                        : t('openclawIntegration.status.disabled')}
                    </CapabilityBadge>
                  </div>
                  <p className="max-w-2xl text-sm leading-6 text-slate-600">
                    {t('openclawIntegration.overview.description')}
                  </p>
                </div>
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-slate-50 px-4 py-3">
                <div className="flex items-center gap-3">
                  <Switch checked={current.enabled} onCheckedChange={handleToggleIntegration} disabled={isBusy} />
                  <div className="space-y-1">
                    <p className="text-sm font-medium text-slate-900">
                      {t('openclawIntegration.overview.switchLabel')}
                    </p>
                    <p className="text-xs leading-5 text-slate-500">
                      {t('openclawIntegration.overview.switchHint')}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 p-4">
                <p className="text-sm text-slate-500">{t('openclawIntegration.summary.systemPresets')}</p>
                <p className="mt-2 text-2xl font-semibold text-slate-900">{systemItems.length}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 p-4">
                <p className="text-sm text-slate-500">{t('openclawIntegration.summary.customItems')}</p>
                <p className="mt-2 text-2xl font-semibold text-slate-900">{customItems.length}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 p-4">
                <p className="text-sm text-slate-500">{t('openclawIntegration.summary.runtimeApi')}</p>
                <p className="mt-2 text-sm font-medium text-slate-900">/api/integrations/openclaw/*</p>
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="space-y-4">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl bg-amber-50 p-3 text-amber-700">
                <KeyRound className="h-5 w-5" />
              </div>
              <div className="space-y-1">
                <h2 className="text-lg font-semibold text-slate-900">
                  {t('openclawIntegration.secret.title')}
                </h2>
                <p className="text-sm leading-6 text-slate-600">
                  {t('openclawIntegration.secret.description')}
                </p>
              </div>
            </div>

            <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 p-4">
              <p className="text-sm font-medium text-slate-900">
                {current.secretConfigured
                  ? current.secretHint || t('openclawIntegration.secret.configured')
                  : t('openclawIntegration.secret.notConfigured')}
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {current.secretConfigured
                  ? t('openclawIntegration.secret.configuredHint')
                  : t('openclawIntegration.secret.missingHint')}
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button type="button" className="rounded-2xl" onClick={() => setShowRotateConfirm(true)} disabled={isBusy}>
                {rotateSecretMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                {current.secretConfigured
                  ? t('openclawIntegration.actions.rotateSecret')
                  : t('openclawIntegration.actions.generateSecret')}
              </Button>
              {revealedSecret ? (
                <Button type="button" variant="outline" className="rounded-2xl" onClick={handleCopySecret}>
                  <Copy className="h-4 w-4" />
                  {t('openclawIntegration.actions.copySecret')}
                </Button>
              ) : null}
            </div>

            {revealedSecret ? (
              <div className="rounded-[22px] border border-emerald-200 bg-emerald-50/80 p-4">
                <div className="flex items-center gap-2 text-emerald-700">
                  <CheckCircle2 className="h-4 w-4" />
                  <p className="text-sm font-semibold">{t('openclawIntegration.secret.revealedTitle')}</p>
                </div>
                <code className="mt-3 block break-all rounded-2xl bg-white/80 px-4 py-3 text-xs text-slate-700">
                  {revealedSecret}
                </code>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section className="rounded-[32px] border border-slate-200 bg-white shadow-sm">
        <button
          type="button"
          onClick={() => setGuideOpen((open) => !open)}
          className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left"
          aria-expanded={guideOpen}
        >
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <div className="rounded-2xl bg-cyan-50 p-2.5 text-cyan-700">
                <PlugZap className="h-5 w-5" />
              </div>
              <h2 className="text-lg font-semibold text-slate-900">
                {t('openclawIntegration.guide.title')}
              </h2>
            </div>
            <p className="pl-[3.25rem] text-sm leading-6 text-slate-600">
              {t('openclawIntegration.guide.description')}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3 self-start rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-600">
            <span>
              {guideOpen
                ? t('openclawIntegration.guide.actions.collapse')
                : t('openclawIntegration.guide.actions.expand')}
            </span>
            <ChevronDown className={cn('h-4 w-4 transition-transform', guideOpen && 'rotate-180')} />
          </div>
        </button>

        {guideOpen ? (
          <div className="border-t border-slate-200 px-6 pb-6 pt-5">
            <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-5">
              <p className="text-sm leading-7 text-slate-700">
                {t('openclawIntegration.guide.intro')}
              </p>
            </div>

            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              {guideSteps.map((step) => (
                <div key={step.title} className="rounded-[24px] border border-slate-200 bg-white p-5">
                  <p className="text-base font-semibold text-slate-900">{step.title}</p>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{step.description}</p>
                  {step.details?.length ? (
                    <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
                      {step.details.map((detail) => (
                        <li key={detail} className="flex items-start gap-3">
                          <span className="mt-2 h-1.5 w-1.5 rounded-full bg-slate-400" />
                          <span>{detail}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-2">
              <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-5">
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-500">
                  {t('openclawIntegration.guide.installTitle')}
                </p>
                <p className="mt-3 text-sm leading-6 text-slate-700">
                  {t('openclawIntegration.guide.installDescription')}
                </p>
                <div className="mt-4 space-y-4">
                  {pluginInstallBlocks.map((block) => (
                    <div key={block.title} className="rounded-2xl border border-slate-200 bg-white p-4">
                      <p className="text-sm font-semibold text-slate-900">{block.title}</p>
                      <p className="mt-1 text-sm leading-6 text-slate-600">{block.description}</p>
                      <pre className="mt-3 overflow-x-auto rounded-2xl bg-slate-950 px-4 py-3 text-xs leading-6 text-slate-100">
                        <code>{block.code}</code>
                      </pre>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-5">
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-500">
                  {t('openclawIntegration.guide.configTitle')}
                </p>
                <p className="mt-3 text-sm leading-6 text-slate-700">
                  {t('openclawIntegration.guide.configDescription')}
                </p>

                <div className="mt-4 space-y-4">
                  {pluginConfigBlocks.map((block) => (
                    <div key={block.title} className="rounded-2xl border border-slate-200 bg-white p-4">
                      <p className="text-sm font-semibold text-slate-900">{block.title}</p>
                      <p className="mt-1 text-sm leading-6 text-slate-600">{block.description}</p>
                      <pre className="mt-3 overflow-x-auto rounded-2xl bg-slate-950 px-4 py-3 text-xs leading-6 text-slate-100">
                        <code>{block.code}</code>
                      </pre>
                    </div>
                  ))}

                  <div className="rounded-2xl border border-slate-200 bg-white p-4">
                    <p className="text-sm font-semibold text-slate-900">
                      {t('openclawIntegration.guide.configExampleTitle')}
                    </p>
                    <p className="mt-1 text-sm leading-6 text-slate-600">
                      {t('openclawIntegration.guide.configExampleDescription')}
                    </p>
                    <pre className="mt-3 overflow-x-auto rounded-2xl bg-slate-950 px-4 py-3 text-xs leading-6 text-slate-100">
                      <code>{pluginConfigExample}</code>
                    </pre>
                  </div>

                  <div className="rounded-2xl border border-amber-200 bg-amber-50/80 p-4 text-sm leading-6 text-amber-900">
                    <p className="font-semibold">{t('openclawIntegration.guide.restartTitle')}</p>
                    <p className="mt-1">{t('openclawIntegration.guide.restartDescription')}</p>
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
              <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-5">
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-500">
                  {t('openclawIntegration.guide.endpointsTitle')}
                </p>
                <div className="mt-4 space-y-3">
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                      {t('openclawIntegration.guide.endpoints.auth')}
                    </p>
                    <code className="mt-2 block break-all text-sm text-slate-800">
                      Authorization: Bearer {'<integration_secret>'}
                    </code>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                      {t('openclawIntegration.guide.endpoints.catalog')}
                    </p>
                    <code className="mt-2 block break-all text-sm text-slate-800">
                      GET /api/integrations/openclaw/capabilities
                    </code>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                      {t('openclawIntegration.guide.endpoints.execute')}
                    </p>
                    <code className="mt-2 block break-all text-sm text-slate-800">
                      POST /api/integrations/openclaw/capabilities/{'{capabilityKey}'}/execute
                    </code>
                  </div>
                </div>
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-5">
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-500">
                  {t('openclawIntegration.guide.notesTitle')}
                </p>
                <ul className="mt-4 space-y-3 text-sm leading-6 text-slate-700">
                  {guideNotes.map((note) => (
                    <li key={note} className="flex items-start gap-3">
                      <span className="mt-2 h-1.5 w-1.5 rounded-full bg-slate-400" />
                      <span>{note}</span>
                    </li>
                  ))}
                </ul>

                <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="text-sm font-semibold text-slate-900">
                    {t('openclawIntegration.guide.verifyChecklistTitle')}
                  </p>
                  <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
                    {pluginVerifyChecklist.map((item) => (
                      <li key={item} className="flex items-start gap-3">
                        <span className="mt-2 h-1.5 w-1.5 rounded-full bg-slate-400" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </section>

      <section className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
        <SectionHeader
          title={t('openclawIntegration.systemPresets.title')}
          description={t('openclawIntegration.systemPresets.description')}
          action={
            <Button type="button" variant="outline" className="rounded-2xl" onClick={() => setShowResetConfirm(true)} disabled={isBusy}>
              <RefreshCcw className="h-4 w-4" />
              {t('openclawIntegration.actions.resetSystemPresets')}
            </Button>
          }
        />
        <div className="mt-5 grid gap-4">
          {systemItems.map((item) => (
            <CatalogItemCard
              key={item.id}
              item={item}
              onToggle={(enabled) => {
                void handleToggleItem(item, enabled)
              }}
              onEdit={() => openEditDialog(item)}
              typeLabel={typeLabels[item.sourceType] ?? item.sourceType}
              systemLabel={t('openclawIntegration.labels.system')}
              userLabel={t('openclawIntegration.labels.user')}
              exposedLabel={t('openclawIntegration.status.exposed')}
              hiddenLabel={t('openclawIntegration.status.hidden')}
              availableLabel={t('openclawIntegration.status.available')}
              unavailableLabel={t('openclawIntegration.status.unavailable')}
              inputLabel={t('openclawIntegration.labels.input')}
              outputLabel={t('openclawIntegration.labels.output')}
            />
          ))}
        </div>
      </section>

      <section className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm">
        <SectionHeader
          title={t('openclawIntegration.customItems.title')}
          description={t('openclawIntegration.customItems.description')}
          action={
            <Button type="button" className="rounded-2xl" onClick={() => openCreateDialog('tool')}>
              <Plus className="h-4 w-4" />
              {t('openclawIntegration.actions.addCapability')}
            </Button>
          }
        />
        {customItems.length ? (
          <div className="mt-5 grid gap-4">
            {customItems.map((item) => (
              <CatalogItemCard
                key={item.id}
                item={item}
                onToggle={(enabled) => {
                  void handleToggleItem(item, enabled)
                }}
                onEdit={() => openEditDialog(item)}
                onDelete={() => setDeleteTarget(item)}
                typeLabel={typeLabels[item.sourceType] ?? item.sourceType}
                systemLabel={t('openclawIntegration.labels.system')}
                userLabel={t('openclawIntegration.labels.user')}
                exposedLabel={t('openclawIntegration.status.exposed')}
                hiddenLabel={t('openclawIntegration.status.hidden')}
                availableLabel={t('openclawIntegration.status.available')}
                unavailableLabel={t('openclawIntegration.status.unavailable')}
                inputLabel={t('openclawIntegration.labels.input')}
                outputLabel={t('openclawIntegration.labels.output')}
              />
            ))}
          </div>
        ) : (
          <div className="mt-5 rounded-[28px] border border-dashed border-slate-300 bg-slate-50/80 p-10 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-white shadow-sm">
              <Boxes className="h-6 w-6 text-slate-500" />
            </div>
            <p className="mt-4 text-base font-semibold text-slate-900">
              {t('openclawIntegration.customItems.emptyTitle')}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {t('openclawIntegration.customItems.emptyDescription')}
            </p>
            <Button type="button" className="mt-5 rounded-2xl" onClick={() => openCreateDialog('tool')}>
              <Plus className="h-4 w-4" />
              {t('openclawIntegration.actions.addCapability')}
            </Button>
          </div>
        )}
      </section>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="w-[min(100%,58rem)]">
          <DialogHeader>
            <DialogTitle>
              {editingItem?.isSystemPreset
                ? t('openclawIntegration.dialog.editSystemPreset')
                : dialogMode === 'create'
                  ? t('openclawIntegration.dialog.createTitle')
                  : t('openclawIntegration.dialog.editTitle')}
            </DialogTitle>
            <DialogDescription>
              {editingItem?.isSystemPreset
                ? t('openclawIntegration.dialog.editSystemPresetDescription')
                : dialogMode === 'create'
                  ? t('openclawIntegration.dialog.createDescription')
                  : t('openclawIntegration.dialog.editDescription')}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            {!editingItem?.isSystemPreset ? (
              <section className="space-y-3">
                <Label>{t('openclawIntegration.form.sourceType')}</Label>
                <div className="grid gap-3 sm:grid-cols-3">
                  {(['tool', 'workflow', 'agent'] as const).map((sourceType) => (
                    <button
                      key={sourceType}
                      type="button"
                      className={cn(
                        'rounded-[22px] border px-4 py-4 text-left transition',
                        draft.sourceType === sourceType
                          ? 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/10'
                          : 'border-slate-200 bg-slate-50/70 text-slate-700 hover:border-slate-300'
                      )}
                      onClick={() => {
                        setSelectedSourceKey(null)
                        setDraft(createEmptyDraft(sourceType))
                      }}
                    >
                      <p className="text-sm font-semibold">
                        {sourceType === 'tool'
                          ? t('openclawIntegration.types.tool')
                          : sourceType === 'workflow'
                            ? t('openclawIntegration.types.workflow')
                            : t('openclawIntegration.types.agent')}
                      </p>
                      <p className={cn('mt-1 text-xs leading-5', draft.sourceType === sourceType ? 'text-white/80' : 'text-slate-500')}>
                        {t(`openclawIntegration.sourceTypeDescriptions.${sourceType}`)}
                      </p>
                    </button>
                  ))}
                </div>
              </section>
            ) : null}

            {!editingItem?.isSystemPreset ? (
              <section className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <Label>{t('openclawIntegration.form.source')}</Label>
                  {sourceQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
                </div>
                <div className="grid max-h-72 gap-3 overflow-y-auto pr-1">
                  {currentSources.map((source) => {
                    const active = selectedSourceKey === source.sourceKey
                    return (
                      <button
                        key={source.sourceKey}
                        type="button"
                        onClick={() => {
                          setSelectedSourceKey(source.sourceKey)
                          patchDraftFromSource(source)
                        }}
                        className={cn(
                          'rounded-[22px] border px-4 py-4 text-left transition',
                          active
                            ? 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/10'
                            : 'border-slate-200 bg-slate-50/70 text-slate-800 hover:border-slate-300',
                          !source.bindable && 'opacity-70'
                        )}
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold">{source.title}</p>
                          <CapabilityBadge
                            colorClassName={
                              active
                                ? 'border-white/25 bg-white/10 text-white'
                                : source.isSystem
                                  ? 'border-cyan-200 bg-cyan-50 text-cyan-700'
                                  : 'border-violet-200 bg-violet-50 text-violet-700'
                            }
                          >
                            {source.isSystem
                              ? t('openclawIntegration.labels.system')
                              : t('openclawIntegration.labels.user')}
                          </CapabilityBadge>
                          {!source.bindable ? (
                            <CapabilityBadge
                              colorClassName={active ? 'border-white/25 bg-white/10 text-white' : 'border-amber-200 bg-amber-50 text-amber-700'}
                            >
                              {t('openclawIntegration.status.unavailable')}
                            </CapabilityBadge>
                          ) : null}
                        </div>
                        <p className={cn('mt-2 text-sm leading-6', active ? 'text-white/80' : 'text-slate-600')}>
                          {source.description || '-'}
                        </p>
                        {!source.bindable && source.unavailableReason ? (
                          <p className={cn('mt-2 text-xs leading-5', active ? 'text-white/70' : 'text-amber-700')}>
                            {source.unavailableReason}
                          </p>
                        ) : null}
                      </button>
                    )
                  })}
                </div>
              </section>
            ) : null}

            <section className="grid gap-4 md:grid-cols-2">
              <InputField
                label={t('openclawIntegration.form.toolName')}
                value={draft.toolName}
                onChange={(value) => patchDraft({ toolName: value })}
                placeholder="mindatlas_example_tool"
              />
              <InputField
                label={t('openclawIntegration.form.title')}
                value={draft.title}
                onChange={(value) => patchDraft({ title: value })}
              />
            </section>

            <section className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
              <TextareaField
                label={t('openclawIntegration.form.description')}
                value={draft.description}
                onChange={(value) => patchDraft({ description: value })}
                rows={3}
              />
              <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 px-4 py-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-slate-900">
                      {t('openclawIntegration.form.exposed')}
                    </p>
                    <p className="text-sm leading-6 text-slate-600">
                      {t('openclawIntegration.form.exposedHint')}
                    </p>
                  </div>
                  <Switch checked={draft.enabled} onCheckedChange={(enabled) => patchDraft({ enabled })} />
                </div>
              </div>
            </section>

            {!editingItem?.isSystemPreset ? (
              <>
                <section className="grid gap-4 md:grid-cols-2">
                  <TextareaField
                    label={t('openclawIntegration.form.inputSummary')}
                    value={draft.inputSummary}
                    onChange={(value) => patchDraft({ inputSummary: value })}
                    rows={3}
                    disabled={!draft.schemaEditable}
                  />
                  <TextareaField
                    label={t('openclawIntegration.form.outputSummary')}
                    value={draft.outputSummary}
                    onChange={(value) => patchDraft({ outputSummary: value })}
                    rows={3}
                    disabled={!draft.schemaEditable}
                  />
                </section>

                {draft.sourceType === 'tool' ? (
                  <section className="space-y-3">
                    <Label>{t('openclawIntegration.form.toolResponseMode')}</Label>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {(['json_schema', 'text_field'] as const).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          className={cn(
                            'rounded-[22px] border px-4 py-4 text-left transition',
                            draft.toolResponseMode === mode
                              ? 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/10'
                              : 'border-slate-200 bg-slate-50/70 text-slate-700 hover:border-slate-300'
                          )}
                          onClick={() => patchDraft({ toolResponseMode: mode })}
                        >
                          <p className="text-sm font-semibold">
                            {mode === 'json_schema'
                              ? t('openclawIntegration.responseModes.jsonSchema')
                              : t('openclawIntegration.responseModes.textField')}
                          </p>
                          <p className={cn('mt-1 text-xs leading-5', draft.toolResponseMode === mode ? 'text-white/80' : 'text-slate-500')}>
                            {t(`openclawIntegration.responseModeDescriptions.${mode}`)}
                          </p>
                        </button>
                      ))}
                    </div>
                  </section>
                ) : null}

                <section className="grid gap-4 lg:grid-cols-2">
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <Wrench className="h-4 w-4 text-slate-500" />
                      <Label>{t('openclawIntegration.form.inputSchema')}</Label>
                    </div>
                    <textarea
                      rows={12}
                      value={draft.inputSchemaText}
                      onChange={(event) => patchDraft({ inputSchemaText: event.target.value })}
                      className={TEXTAREA_CLASSNAME}
                      disabled={!draft.schemaEditable}
                    />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="h-4 w-4 text-slate-500" />
                      <Label>{t('openclawIntegration.form.outputSchema')}</Label>
                    </div>
                    <textarea
                      rows={12}
                      value={draft.outputSchemaText}
                      onChange={(event) => patchDraft({ outputSchemaText: event.target.value })}
                      className={TEXTAREA_CLASSNAME}
                      disabled={!draft.schemaEditable}
                    />
                  </div>
                </section>

                {!draft.schemaEditable ? (
                  <div className="rounded-[22px] border border-cyan-200 bg-cyan-50/80 px-4 py-3 text-sm leading-6 text-cyan-800">
                    {t('openclawIntegration.form.readonlySchemaHint')}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>

          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" className="rounded-2xl" onClick={() => setDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="button" className="rounded-2xl" onClick={() => void handleSaveDialog()} disabled={isBusy}>
              {(createItemMutation.isPending || updateItemMutation.isPending) ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        isOpen={showRotateConfirm}
        title={t('openclawIntegration.confirmRotate.title')}
        description={t('openclawIntegration.confirmRotate.description')}
        confirmText={t('openclawIntegration.actions.rotateSecret')}
        cancelText={t('common.cancel')}
        onConfirm={() => {
          void handleRotateSecret()
        }}
        onCancel={() => setShowRotateConfirm(false)}
      />

      <ConfirmDialog
        isOpen={showResetConfirm}
        title={t('openclawIntegration.confirmReset.title')}
        description={t('openclawIntegration.confirmReset.description')}
        confirmText={t('openclawIntegration.actions.resetSystemPresets')}
        cancelText={t('common.cancel')}
        onConfirm={() => {
          void handleResetSystemPresets()
        }}
        onCancel={() => setShowResetConfirm(false)}
      />

      <ConfirmDialog
        isOpen={Boolean(deleteTarget)}
        title={t('openclawIntegration.confirmDelete.title')}
        description={t('openclawIntegration.confirmDelete.description', {
          title: deleteTarget?.title || '',
        })}
        confirmText={t('common.delete')}
        cancelText={t('common.cancel')}
        onConfirm={() => {
          void handleDeleteItem()
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
