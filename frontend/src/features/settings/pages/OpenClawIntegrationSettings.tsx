import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Bot,
  Boxes,
  Check,
  CheckCircle2,
  ChevronDown,
  Copy,
  AlertTriangle,
  KeyRound,
  Loader2,
  Pencil,
  PlugZap,
  Plus,
  RefreshCcw,
  Search,
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
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Switch } from '@/components/ui/switch'
import { uiChrome, uiField } from '@/components/ui/styles'
import {
  createOpenClawCatalogItem,
  deleteOpenClawCatalogItem,
  getOpenClawCatalogSources,
  getOpenClawIntegrationSettings,
  resetOpenClawSystemItems,
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
import {
  SettingsBadge,
  SettingsEmptyState,
  SettingsInset,
  SettingsPageHeader,
  SettingsPageShell,
} from '@/features/settings/components/SettingsShell'

const settingsQueryKey = ['openclaw-integration-settings'] as const

type DraftMode = 'create' | 'edit'
type ContractOrigin = 'pending' | 'source' | 'override'

interface CatalogContractDraft {
  inputSummary: string
  outputSummary: string
  inputSchemaText: string
  outputSchemaText: string
  toolResponseMode: OpenClawToolResponseMode
  schemaEditable: boolean
}

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

function sourceTypeIcon(sourceType: OpenClawCatalogSourceType) {
  if (sourceType === 'tool') return Wrench
  if (sourceType === 'workflow') return Boxes
  return Bot
}

function getCatalogSourceDisplayName(source: Pick<OpenClawCatalogSource, 'sourceName' | 'title'>) {
  return source.sourceName?.trim() || source.title
}

function getCatalogSourceDisplayDescription(source: Pick<OpenClawCatalogSource, 'sourceDescription' | 'description'>) {
  return source.sourceDescription?.trim() || source.description
}

function createDraftFromItem(item: OpenClawCatalogItem): CatalogItemDraft {
  return {
    sourceType: item.sourceType,
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

function createContractDraftFromSource(source: OpenClawCatalogSource): CatalogContractDraft {
  return {
    inputSummary: source.defaultInputSummary || '',
    outputSummary: source.defaultOutputSummary || '',
    inputSchemaText: stringifySchema(source.defaultInputSchema ?? {}),
    outputSchemaText: stringifySchema(source.defaultOutputSchema ?? {}),
    toolResponseMode: source.defaultToolResponseMode ?? 'json_schema',
    schemaEditable: source.schemaMode === 'editable',
  }
}

function pickContractDraft(draft: CatalogItemDraft): CatalogContractDraft {
  return {
    inputSummary: draft.inputSummary,
    outputSummary: draft.outputSummary,
    inputSchemaText: draft.inputSchemaText,
    outputSchemaText: draft.outputSchemaText,
    toolResponseMode: draft.toolResponseMode,
    schemaEditable: draft.schemaEditable,
  }
}

function contractsEqual(left: CatalogContractDraft, right: CatalogContractDraft) {
  return (
    left.inputSummary.trim() === right.inputSummary.trim() &&
    left.outputSummary.trim() === right.outputSummary.trim() &&
    left.inputSchemaText === right.inputSchemaText &&
    left.outputSchemaText === right.outputSchemaText &&
    left.toolResponseMode === right.toolResponseMode &&
    left.schemaEditable === right.schemaEditable
  )
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
    <SettingsBadge className={colorClassName}>
      {children}
    </SettingsBadge>
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
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      {action}
    </div>
  )
}

function SummaryCard({
  label,
  value,
  hint,
  active,
}: {
  label: string
  value: string
  hint: string
  active?: boolean
}) {
  return (
    <div
      className={cn(
        uiChrome.card,
        'group relative overflow-hidden p-5 transition-all duration-300',
        active
          ? 'border-emerald-200/80 bg-emerald-50/50'
          : 'border-border/80 bg-background/96'
      )}
    >
      {active && (
        <div className="absolute inset-x-0 -top-px h-px bg-gradient-to-r from-transparent via-emerald-400 to-transparent opacity-50" />
      )}
      {!active && (
        <div className="absolute inset-x-0 -top-px h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent opacity-50" />
      )}
      <div className="relative">
        <p className={cn('text-sm font-medium', active ? 'text-emerald-700' : 'text-muted-foreground')}>{label}</p>
        <p className={cn('mt-2 text-2xl font-semibold tracking-tight', active ? 'text-emerald-950 dark:text-emerald-100' : 'text-foreground')}>{value}</p>
        <p className={cn('mt-2 text-sm leading-6', active ? 'text-emerald-700/80 dark:text-emerald-200/80' : 'text-muted-foreground')}>{hint}</p>
      </div>
    </div>
  )
}

function QuickStartStep({
  step,
  title,
  description,
  statusLabel,
  statusTone,
  action,
  className,
  children,
}: {
  step: string
  title: string
  description: string
  statusLabel: string
  statusTone: 'success' | 'warning' | 'neutral'
  action?: React.ReactNode
  className?: string
  children?: React.ReactNode
}) {
  const isLast = step === '4'
  return (
    <article className={cn('relative flex gap-4 pb-2 md:gap-6', !isLast && 'pb-8', className)}>
      <div className="flex flex-col items-center">
        <span className={cn(
            "relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold shadow-sm transition-all",
            statusTone === 'success' ? 'bg-emerald-500 text-white shadow-emerald-500/20 ring-4 ring-emerald-50' :
            statusTone === 'warning' ? 'bg-amber-500 text-white shadow-amber-500/20 ring-4 ring-amber-50' : 
            'bg-slate-900 text-white shadow-slate-900/10 ring-4 ring-slate-50'
        )}>
          {statusTone === 'success' ? <CheckCircle2 className="h-5 w-5" /> : step}
        </span>
        {!isLast && <div className="absolute top-10 bottom-0 left-[1.15rem] w-[2px] bg-gradient-to-b from-slate-200 via-slate-100 to-transparent" />}
      </div>
      <div className="flex flex-1 flex-col gap-5 pt-1.5 pb-2">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
              <CapabilityBadge
                colorClassName={
                  statusTone === 'success'
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : statusTone === 'warning'
                      ? 'border-amber-200 bg-amber-50 text-amber-700'
                      : 'border-slate-200 bg-slate-50 text-slate-700'
                }
              >
                {statusLabel}
              </CapabilityBadge>
            </div>
            <p className="max-w-2xl text-sm leading-6 text-slate-600">{description}</p>
          </div>
          {action && <div className="shrink-0 pt-1">{action}</div>}
        </div>
        {children && (
          <div className="mt-2 flex flex-col gap-4">
            {children}
          </div>
        )}
      </div>
    </article>
  )
}

function GuideCallout({
  icon,
  tone = 'info',
  children,
}: {
  icon: React.ReactNode
  tone?: 'info' | 'warning'
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        uiChrome.inset,
        'flex items-start gap-3 px-4 py-3.5 text-sm leading-6 shadow-none',
        tone === 'warning'
          ? 'border-amber-200 bg-amber-50/80 text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-100'
          : 'border-cyan-200 bg-cyan-50/80 text-cyan-950 dark:border-cyan-500/20 dark:bg-cyan-500/10 dark:text-cyan-100'
      )}
    >
      <div
        className={cn(
          'mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-[12px]',
          tone === 'warning' ? 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-100' : 'bg-cyan-100 text-cyan-700 dark:bg-cyan-500/15 dark:text-cyan-100'
        )}
      >
        {icon}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

function CopyableCodeBlock({
  orderLabel,
  title,
  description,
  code,
  copyLabel,
  onCopy,
}: {
  orderLabel?: string
  title: string
  description: string
  code: string
  copyLabel: string
  onCopy: (value: string) => void
}) {
  const singleLine = !code.includes('\n')
  const compactCodeTextClass = 'font-mono text-[13px] leading-6 text-slate-100'

  return (
    <div className={cn(uiChrome.card, 'p-5')}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          {orderLabel ? (
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold tracking-[0.12em] text-slate-700">
                {orderLabel}
              </span>
            </div>
          ) : null}
          <p className="text-base font-semibold text-foreground">{title}</p>
          <p className="text-sm leading-6 text-muted-foreground">{description}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-10 shrink-0 px-4"
          onClick={() => onCopy(code)}
        >
          <Copy className="h-4 w-4" />
          {copyLabel}
        </Button>
      </div>
      {singleLine ? (
        <div className="mt-4">
          <div className="flex w-full overflow-x-auto rounded-[12px] bg-slate-950 px-5 py-4">
            <code className={cn(compactCodeTextClass, 'whitespace-pre')}>{code}</code>
          </div>
        </div>
      ) : (
        <pre className="mt-4 w-full max-h-[24rem] overflow-auto rounded-[12px] bg-slate-950 px-5 py-4">
          <code className={cn(compactCodeTextClass, 'block whitespace-pre')}>{code}</code>
        </pre>
      )}
    </div>
  )
}

function GuideChecklistCard({
  title,
  items,
}: {
  title: string
  items: string[]
}) {
  return (
    <div className={cn(uiChrome.card, 'p-5')}>
      <p className="text-base font-semibold text-foreground">{title}</p>
      <ul className="mt-4 space-y-3 text-sm leading-6 text-foreground/85">
        {items.map((item) => (
          <li key={item} className="flex items-start gap-3">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[12px] bg-muted text-muted-foreground">
              <CheckCircle2 className="h-4 w-4" />
            </div>
            <span>{item}</span>
          </li>
        ))}
      </ul>
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
  retiredLabel,
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
  retiredLabel: string
  availableLabel: string
  unavailableLabel: string
  inputLabel: string
  outputLabel: string
}) {
  return (
    <article className={cn(uiChrome.card, 'group overflow-hidden p-6 transition-all duration-300')}>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold text-foreground">{item.title}</h3>
              <CapabilityBadge colorClassName="border-slate-200 bg-slate-50 text-slate-700">
                {typeLabel}
              </CapabilityBadge>
              <CapabilityBadge
                colorClassName={
                  item.isSystemItem
                    ? 'border-cyan-200 bg-cyan-50 text-cyan-700'
                    : 'border-violet-200 bg-violet-50 text-violet-700'
                }
              >
                {item.isSystemItem ? systemLabel : userLabel}
              </CapabilityBadge>
              {item.retired ? (
                <CapabilityBadge colorClassName="border-amber-200 bg-amber-50 text-amber-700">
                  {retiredLabel}
                </CapabilityBadge>
              ) : (
                <CapabilityBadge
                  colorClassName={
                    item.available
                      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                      : 'border-amber-200 bg-amber-50 text-amber-700'
                  }
                >
                  {item.available ? availableLabel : unavailableLabel}
                </CapabilityBadge>
              )}
            </div>
            <p className="text-sm leading-6 text-muted-foreground">{item.description || '-'}</p>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="rounded-full bg-muted px-2.5 py-1 font-medium text-foreground">
                {item.toolName}
              </span>
              {item.sourceName ? (
                <span className="rounded-full bg-muted px-2.5 py-1">
                  {item.sourceName}
                </span>
              ) : null}
              {item.sourceToolName ? (
                <code className="rounded-full bg-muted px-2.5 py-1 font-medium">
                  {item.sourceToolName}
                </code>
              ) : null}
              <span className="rounded-full bg-muted px-2.5 py-1">
                {item.retired ? retiredLabel : item.enabled ? exposedLabel : hiddenLabel}
              </span>
            </div>
            {item.sourceDescription ? (
              <p className="text-xs leading-5 text-muted-foreground">{item.sourceDescription}</p>
            ) : null}
          </div>

          <div className="flex items-center gap-2 self-start">
            {item.retired ? (
              <div className="whitespace-nowrap rounded-[16px] border border-amber-200/60 bg-amber-50/80 px-4 py-2.5 text-sm font-medium text-amber-800">
                {retiredLabel}
              </div>
            ) : (
              <div className={cn(uiChrome.inset, 'px-4 py-2.5')}>
                <div className="flex items-center gap-3">
                  <Switch checked={item.enabled} onCheckedChange={onToggle} className="data-[state=checked]:bg-emerald-500" />
                  <span className="whitespace-nowrap text-sm font-semibold text-foreground">
                    {item.enabled ? exposedLabel : hiddenLabel}
                  </span>
                </div>
              </div>
            )}
            <Button type="button" variant="outline" onClick={onEdit}>
              <Pencil className="h-4 w-4" />
            </Button>
            {onDelete ? (
              <Button type="button" variant="outline" className="text-rose-600" onClick={onDelete}>
                <Trash2 className="h-4 w-4" />
              </Button>
            ) : null}
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <SettingsInset className="p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
              {inputLabel}
            </p>
            <p className="mt-2.5 text-sm leading-relaxed text-foreground/85">{item.inputSummary || '-'}</p>
          </SettingsInset>
          <SettingsInset className="p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
              {outputLabel}
            </p>
            <p className="mt-2.5 text-sm leading-relaxed text-foreground/85">{item.outputSummary || '-'}</p>
          </SettingsInset>
        </div>

        {item.retired && item.retirementReason ? (
          <div className="rounded-[12px] border border-amber-200 bg-amber-50/80 px-4 py-3 text-sm leading-6 text-amber-800">
            {item.retirementReason}
          </div>
        ) : null}

        {!item.retired && !item.available && item.availabilityReason ? (
          <div className="rounded-[12px] border border-amber-200 bg-amber-50/80 px-4 py-3 text-sm leading-6 text-amber-800">
            {item.availabilityReason}
          </div>
        ) : null}
      </div>
    </article>
  )
}

export function OpenClawIntegrationSettingsPage() {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const localizedSettingsQueryKey = [...settingsQueryKey, i18n.language] as const
  const settingsQuery = useQuery({
    queryKey: localizedSettingsQueryKey,
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
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false)
  const [sourceSearch, setSourceSearch] = useState('')
  const [contractAdvancedOpen, setContractAdvancedOpen] = useState(false)
  const [contractOrigin, setContractOrigin] = useState<ContractOrigin>('source')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [guideOpen, setGuideOpen] = useState(false)

  const integrationMutation = useMutation({
    mutationFn: updateOpenClawIntegrationSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(localizedSettingsQueryKey, data)
    },
  })

  const rotateSecretMutation = useMutation({
    mutationFn: rotateOpenClawIntegrationSecret,
    onSuccess: (data) => {
      queryClient.setQueryData(localizedSettingsQueryKey, data.settings)
    },
  })

  const createItemMutation = useMutation({
    mutationFn: createOpenClawCatalogItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['assistant-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })

  const updateItemMutation = useMutation({
    mutationFn: ({ itemId, payload }: { itemId: string; payload: OpenClawCatalogItemUpdateRequest }) =>
      updateOpenClawCatalogItem(itemId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['assistant-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })

  const deleteItemMutation = useMutation({
    mutationFn: deleteOpenClawCatalogItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: settingsQueryKey })
      void queryClient.invalidateQueries({ queryKey: ['assistant-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })

  const resetMutation = useMutation({
    mutationFn: resetOpenClawSystemItems,
    onSuccess: (data) => {
      queryClient.setQueryData(localizedSettingsQueryKey, data)
      void queryClient.invalidateQueries({ queryKey: ['assistant-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['assistant-agents'] })
    },
  })

  const sourceQuery = useQuery({
    queryKey: ['openclaw-catalog-sources', draft.sourceType, i18n.language],
    queryFn: () => getOpenClawCatalogSources(draft.sourceType),
    enabled: dialogOpen,
  })

  const current = settingsQuery.data ?? null
  const loadErrorMessage =
    settingsQuery.error && isApiError(settingsQuery.error)
      ? settingsQuery.error.message
      : settingsQuery.error instanceof Error
        ? settingsQuery.error.message
        : t('openclawIntegration.messages.loadFailedDescription')
  const catalogItems = useMemo(
    () => current?.catalogItems ?? [],
    [current]
  )
  const exposedAvailableItems = useMemo(
    () => current?.catalogItems.filter((item) => item.enabled && item.available && !item.retired) ?? [],
    [current]
  )
  const currentSources = sourceQuery.data?.items ?? []
  const selectedSource = useMemo(
    () => currentSources.find((item) => item.sourceKey === selectedSourceKey) ?? null,
    [currentSources, selectedSourceKey]
  )
  const sourceDerivedContract = useMemo(
    () => (selectedSource ? createContractDraftFromSource(selectedSource) : null),
    [selectedSource]
  )
  const contractUsesOverride = useMemo(() => {
    if (contractOrigin === 'override') return true
    if (contractOrigin === 'source') return false
    if (!sourceDerivedContract) return true
    return !contractsEqual(pickContractDraft(draft), sourceDerivedContract)
  }, [contractOrigin, draft, sourceDerivedContract])
  const effectiveContract = useMemo(
    () => (contractUsesOverride || !sourceDerivedContract ? pickContractDraft(draft) : sourceDerivedContract),
    [contractUsesOverride, draft, sourceDerivedContract]
  )
  const SelectedSourceIcon = selectedSource ? sourceTypeIcon(selectedSource.sourceType) : Search
  const filteredSources = useMemo(() => {
    const keyword = sourceSearch.trim().toLowerCase()
    if (!keyword) return currentSources
    return currentSources.filter((source) => {
      const haystack = [
        getCatalogSourceDisplayName(source),
        getCatalogSourceDisplayDescription(source),
        source.sourceToolName,
        source.sourceKey,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(keyword)
    })
  }, [currentSources, sourceSearch])
  const editingRetiredItem = dialogMode === 'edit' && editingItem?.retired ? editingItem : null
  useEffect(() => {
    if (!dialogOpen) return
    if (selectedSourceKey) return
    const firstBindable = currentSources.find((item) => item.bindable)
    if (!firstBindable) return
    setSelectedSourceKey(firstBindable.sourceKey)
    applySourceSelection(firstBindable)
  }, [currentSources, dialogOpen, selectedSourceKey])

  useEffect(() => {
    if (!dialogOpen) {
      setSourcePickerOpen(false)
      setSourceSearch('')
      setContractAdvancedOpen(false)
    }
  }, [dialogOpen])

  useEffect(() => {
    if (!dialogOpen || contractOrigin !== 'pending' || !sourceDerivedContract) return
    setContractOrigin(contractsEqual(pickContractDraft(draft), sourceDerivedContract) ? 'source' : 'override')
    setDraft((currentDraft) =>
      currentDraft.schemaEditable === sourceDerivedContract.schemaEditable
        ? currentDraft
        : { ...currentDraft, schemaEditable: sourceDerivedContract.schemaEditable }
    )
  }, [contractOrigin, dialogOpen, draft, sourceDerivedContract])

  useEffect(() => {
    if (!dialogOpen || contractOrigin !== 'source' || !sourceDerivedContract) return
    setDraft((currentDraft) =>
      contractsEqual(pickContractDraft(currentDraft), sourceDerivedContract)
        ? currentDraft
        : { ...currentDraft, ...sourceDerivedContract }
    )
  }, [contractOrigin, dialogOpen, sourceDerivedContract])

  const isBusy =
    settingsQuery.isLoading ||
    integrationMutation.isPending ||
    rotateSecretMutation.isPending ||
    createItemMutation.isPending ||
    updateItemMutation.isPending ||
    deleteItemMutation.isPending ||
    resetMutation.isPending

  const typeLabels: Record<string, string> = {
    tool: t('openclawIntegration.types.tool'),
    workflow: t('openclawIntegration.types.workflow'),
    agent: t('openclawIntegration.types.agent'),
  }
  const guideNotes = [
    t('openclawIntegration.guide.notes.catalogOnly'),
    t('openclawIntegration.guide.notes.skillBoundary'),
    t('openclawIntegration.guide.notes.refresh'),
  ]
  const pluginInstallBlocks = [
    {
      order: '3.1',
      title: t('openclawIntegration.guide.installBlocks.locate.title'),
      description: t('openclawIntegration.guide.installBlocks.locate.description'),
      code: 'cd /path/to/MindAtlas',
    },
    {
      order: '3.2',
      title: t('openclawIntegration.guide.installBlocks.install.title'),
      description: t('openclawIntegration.guide.installBlocks.install.description'),
      code: 'openclaw plugins install ./integrations/openclaw-mindatlas\nnpm --prefix ./integrations/openclaw-mindatlas run configure:skills',
    },
  ]
  const pluginConfigExample = `{
  "plugins": {
    "entries": {
      "openclaw-mindatlas": {
        "enabled": true,
        "config": {
          "baseUrl": "http://your-mindatlas-host",
          "integrationSecret": "paste-the-secret-from-settings",
          "requestTimeoutMs": 15000,
          "catalogRefreshTtlSec": 300
        }
      }
    }
  }
}`
  const pluginConfigExampleBlock = {
    order: '3.4',
    title: t('openclawIntegration.guide.configExampleTitle'),
    description: t('openclawIntegration.guide.configExampleDescription'),
    code: pluginConfigExample,
  }
  const pluginConfigBlocks = [
    {
      order: '3.3',
      title: t('openclawIntegration.guide.configBlocks.findConfig.title'),
      description: t('openclawIntegration.guide.configBlocks.findConfig.description'),
      code: 'openclaw config file',
    },
    {
      order: '3.5',
      title: t('openclawIntegration.guide.configBlocks.validate.title'),
      description: t('openclawIntegration.guide.configBlocks.validate.description'),
      code: 'openclaw config validate',
    },
  ]
  const setupSequenceBlocks = [
    ...pluginInstallBlocks,
    pluginConfigBlocks[0],
    pluginConfigExampleBlock,
    pluginConfigBlocks[1],
  ]
  const orderedSetupCards = setupSequenceBlocks.map((block) => ({
    ...block,
    copyLabel: t('openclawIntegration.guide.actions.copy'),
  }))
  const upgradeCommandCards = [
    {
      order: 'U1',
      title: t('openclawIntegration.guide.upgradeBlocks.standard.title'),
      description: t('openclawIntegration.guide.upgradeBlocks.standard.description'),
      code: 'openclaw plugins update openclaw-mindatlas\nnpm --prefix ./integrations/openclaw-mindatlas run configure:skills',
    },
    {
      order: 'U2',
      title: t('openclawIntegration.guide.upgradeBlocks.reinstall.title'),
      description: t('openclawIntegration.guide.upgradeBlocks.reinstall.description'),
      code: 'openclaw plugins uninstall openclaw-mindatlas --force\nopenclaw plugins install ./integrations/openclaw-mindatlas\nnpm --prefix ./integrations/openclaw-mindatlas run configure:skills',
    },
    {
      order: 'U3',
      title: t('openclawIntegration.guide.upgradeBlocks.skills.title'),
      description: t('openclawIntegration.guide.upgradeBlocks.skills.description'),
      code:
        'BACKUP_DIR=$HOME/.openclaw/skills-backup-$(date +%F-%H%M%S)\nmkdir -p "$BACKUP_DIR"\nfor skill in mindatlas-overview mindatlas-auto-capture mindatlas-retrieval mindatlas-summary mindatlas-dispatcher; do\n  [ -d "$HOME/.openclaw/skills/$skill" ] && mv "$HOME/.openclaw/skills/$skill" "$BACKUP_DIR"/\ndone\nnpm --prefix ./integrations/openclaw-mindatlas run configure:skills',
    },
  ].map((block) => ({
    ...block,
    copyLabel: t('openclawIntegration.guide.actions.copy'),
  }))
  const verificationChecks = [
    t('openclawIntegration.guide.verifyChecks.restart'),
    t('openclawIntegration.guide.verifyChecks.session'),
    t('openclawIntegration.guide.verifyChecks.failure'),
  ]
  const maintenanceChecks = [
    t('openclawIntegration.guide.maintenanceChecks.firstInstall'),
    t('openclawIntegration.guide.maintenanceChecks.catalogChange'),
    t('openclawIntegration.guide.maintenanceChecks.upgrade'),
  ]
  const verificationPrompts = [
    t('openclawIntegration.guide.verifyPrompts.first'),
    t('openclawIntegration.guide.verifyPrompts.second'),
  ]
  const exposedAvailableCount = exposedAvailableItems.length
  const secretReady = Boolean(current?.secretConfigured)
  const catalogReady = exposedAvailableCount > 0

  function patchDraft(patch: Partial<CatalogItemDraft>) {
    setDraft((currentDraft) => ({ ...currentDraft, ...patch }))
  }

  function patchContractDraft(patch: Partial<CatalogContractDraft>) {
    setContractOrigin('override')
    setDraft((currentDraft) => ({ ...currentDraft, ...patch }))
  }

  function applySourceSelection(source: OpenClawCatalogSource) {
    const nextContract = createContractDraftFromSource(source)
    const preserveOverride = contractUsesOverride && nextContract.schemaEditable
    const displayName = getCatalogSourceDisplayName(source)
    const displayDescription = getCatalogSourceDisplayDescription(source)
    const toolNameSeed = source.sourceToolName || displayName
    setContractOrigin(preserveOverride ? 'override' : 'source')
    setDraft((currentDraft) => ({
      ...currentDraft,
      sourceType: source.sourceType,
      title: displayName,
      description: displayDescription,
      sourceToolName: source.sourceToolName ?? null,
      toolId: source.toolId ?? null,
      workflowId: source.workflowId ?? null,
      agentProfileId: source.agentProfileId ?? null,
      schemaEditable: nextContract.schemaEditable,
      toolName:
        currentDraft.toolName.trim() ||
        toolNameSeed
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '_')
          .replace(/^_+|_+$/g, '') ||
        '',
      ...(preserveOverride ? {} : nextContract),
    }))
  }

  function openCreateDialog(sourceType: OpenClawCatalogSourceType = 'tool') {
    setDialogMode('create')
    setEditingItem(null)
    setSelectedSourceKey(null)
    setSourcePickerOpen(false)
    setSourceSearch('')
    setContractOrigin('source')
    setContractAdvancedOpen(false)
    setDraft(createEmptyDraft(sourceType))
    setDialogOpen(true)
  }

  function openEditDialog(item: OpenClawCatalogItem) {
    setDialogMode('edit')
    setEditingItem(item)
    setDraft(createDraftFromItem(item))
    setSelectedSourceKey(buildSourceKey(item))
    setSourcePickerOpen(false)
    setSourceSearch('')
    setContractOrigin('pending')
    setContractAdvancedOpen(false)
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
      showCatalogChangeToast(t('openclawIntegration.messages.saved'))
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
    await copyToClipboard(revealedSecret, t('openclawIntegration.messages.secretCopied'))
  }

  async function copyToClipboard(value: string, successMessage: string) {
    try {
      await navigator.clipboard.writeText(value)
      toast.success(successMessage)
    } catch {
      toast.error(t('messages.error'))
    }
  }

  function scrollToCatalog() {
    document.getElementById('openclaw-catalog-section')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
  }

  function showCatalogChangeToast(message: string) {
    toast.success(`${message} ${t('openclawIntegration.messages.restartRecommended')}`)
  }

  async function handleResetSystemItems() {
    try {
      await resetMutation.mutateAsync()
      setShowResetConfirm(false)
      showCatalogChangeToast(t('openclawIntegration.messages.systemItemsReset'))
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
      showCatalogChangeToast(t('openclawIntegration.messages.deleted'))
    } catch (error) {
      toast.error(isApiError(error) ? error.message : t('messages.error'))
    }
  }

  async function handleSaveDialog() {
    try {
      const inputSchema = parseSchemaText(
        effectiveContract.inputSchemaText,
        t('openclawIntegration.messages.invalidSchemaJson'),
        t('openclawIntegration.messages.invalidSchemaObject')
      )
      const outputSchema = parseSchemaText(
        effectiveContract.outputSchemaText,
        t('openclawIntegration.messages.invalidSchemaJson'),
        t('openclawIntegration.messages.invalidSchemaObject')
      )
      const payload: OpenClawCatalogItemUpsertRequest = {
        sourceType: draft.sourceType,
        toolName: draft.toolName.trim(),
        title: draft.title.trim(),
        description: draft.description.trim(),
        enabled: draft.enabled,
        inputSummary: effectiveContract.inputSummary.trim(),
        outputSummary: effectiveContract.outputSummary.trim(),
        inputSchema,
        outputSchema,
        toolResponseMode: effectiveContract.toolResponseMode,
        sourceToolName: draft.sourceToolName,
        toolId: draft.toolId,
        workflowId: draft.workflowId,
        agentProfileId: draft.agentProfileId,
      }

      if (dialogMode === 'create') {
        await createItemMutation.mutateAsync(payload)
      } else {
        if (!editingItem) return
        await updateItemMutation.mutateAsync({
          itemId: editingItem.id,
          payload,
        })
      }

      setDialogOpen(false)
      showCatalogChangeToast(t('openclawIntegration.messages.saved'))
    } catch (error) {
      toast.error(isApiError(error) ? error.message : error instanceof Error ? error.message : t('messages.error'))
    }
  }

  if (settingsQuery.isLoading) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('pages.settings.openClawIntegration')}
          description={t('pages.settings.openClawIntegrationDesc')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
        <div className={cn(uiChrome.card, 'flex h-64 items-center justify-center')}>
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      </SettingsPageShell>
    )
  }

  if (settingsQuery.isError || !current) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('pages.settings.openClawIntegration')}
          description={t('pages.settings.openClawIntegrationDesc')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
        <div className={cn(uiChrome.card, 'p-6')}>
          <SettingsEmptyState
            title={t('messages.failedToLoad')}
            description={loadErrorMessage}
            action={(
              <Button type="button" variant="outline" onClick={() => void settingsQuery.refetch()}>
                <RefreshCcw className="h-4 w-4" />
                {t('actions.refresh')}
              </Button>
            )}
          />
        </div>
      </SettingsPageShell>
    )
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.openClawIntegration')}
        description={t('pages.settings.openClawIntegrationDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      {current.syncWarning ? (
        <div className="rounded-[16px] border border-amber-200/80 bg-amber-50/90 px-5 py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
            <div className="flex min-w-0 items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
              <div className="min-w-0 space-y-1">
                <p className="text-sm font-semibold text-amber-900">
                  {t('openclawIntegration.messages.syncWarningTitle')}
                </p>
                <p className="text-xs leading-6 text-amber-800/90">
                  {current.syncWarning}
                </p>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="border-amber-200 bg-white/80 text-amber-900 hover:bg-amber-100"
              onClick={() => void settingsQuery.refetch()}
              disabled={settingsQuery.isFetching}
            >
              {settingsQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
              {t('actions.refresh')}
            </Button>
          </div>
        </div>
      ) : null}
      <section className="flex flex-col gap-6">
        <div className={cn(uiChrome.shell, 'p-7')}>
          <div className="flex h-full flex-col gap-8">
            <div className="flex items-start gap-5">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[20px] bg-slate-900 text-white shadow-sm shadow-slate-900/10">
                <PlugZap className="h-6 w-6" />
              </div>
              <div className="flex flex-col gap-1.5 pt-0.5">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-semibold tracking-tight text-foreground">
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
                <p className="text-sm leading-6 text-muted-foreground">
                  {t('openclawIntegration.overview.description')}
                </p>
              </div>
            </div>

            <div className={cn(uiChrome.inset, 'flex items-center justify-between px-5 py-4')}>
              <div className="flex flex-col gap-1">
                <p className="text-sm font-semibold text-foreground">
                  {t('openclawIntegration.overview.switchLabel')}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t('openclawIntegration.overview.switchHint')}
                </p>
              </div>
              <Switch checked={current.enabled} onCheckedChange={handleToggleIntegration} disabled={isBusy} className="data-[state=checked]:bg-emerald-500" />
            </div>

            <div className="mt-auto grid gap-4 sm:grid-cols-3">
              <SummaryCard
                label={t('openclawIntegration.summary.integration')}
                value={current.enabled ? t('openclawIntegration.status.enabled') : t('openclawIntegration.status.disabled')}
                hint={
                  current.enabled
                    ? t('openclawIntegration.summary.integrationHintEnabled')
                    : t('openclawIntegration.summary.integrationHintDisabled')
                }
                active={current.enabled}
              />
              <SummaryCard
                label={t('openclawIntegration.summary.secret')}
                value={secretReady ? t('openclawIntegration.secret.configured') : t('openclawIntegration.secret.notConfigured')}
                hint={
                  secretReady
                    ? t('openclawIntegration.summary.secretHintConfigured')
                    : t('openclawIntegration.summary.secretHintMissing')
                }
                active={secretReady}
              />
              <SummaryCard
                label={t('openclawIntegration.summary.exposedAvailable')}
                value={String(exposedAvailableCount)}
                hint={
                  catalogReady
                    ? t('openclawIntegration.summary.catalogHintReady', { count: exposedAvailableCount })
                    : t('openclawIntegration.summary.catalogHintMissing')
                }
                active={catalogReady}
              />
            </div>

            {!current.enabled && (
              <div className="rounded-[12px] border border-amber-200 bg-amber-50 px-5 py-4">
                <div className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <p className="text-sm text-amber-900">
                    {t('openclawIntegration.summary.integrationDisabledNotice')}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className={cn(uiChrome.shell, 'p-7')}>
          <div className="flex h-full flex-col gap-8">
            <div className="flex items-start gap-5">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[20px] bg-amber-100 text-amber-700 shadow-sm shadow-amber-900/10">
                <KeyRound className="h-6 w-6" />
              </div>
              <div className="flex flex-col gap-1.5 pt-0.5">
                <h2 className="text-xl font-semibold tracking-tight text-slate-900">
                  {t('openclawIntegration.secret.title')}
                </h2>
                <p className="text-sm leading-6 text-slate-600">
                  {t('openclawIntegration.secret.description')}
                </p>
              </div>
            </div>

            <div className={cn(uiChrome.inset, 'mt-auto p-5')}>
              <p className="text-sm font-semibold text-slate-900">
                {current.secretConfigured
                  ? current.secretHint || t('openclawIntegration.secret.configured')
                  : t('openclawIntegration.secret.notConfigured')}
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
                {current.secretConfigured
                  ? t('openclawIntegration.secret.configuredHint')
                  : t('openclawIntegration.secret.missingHint')}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button 
                type="button" 
                variant={current.secretConfigured ? "outline" : "default"}
                className={cn("transition-all", current.secretConfigured ? "bg-white hover:bg-slate-50" : "bg-slate-900 hover:bg-slate-800")}
                onClick={() => setShowRotateConfirm(true)} 
                disabled={isBusy}
              >
                {rotateSecretMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : current.secretConfigured ? <RefreshCcw className="h-4 w-4" /> : <PlugZap className="h-4 w-4" />}
                {current.secretConfigured
                  ? t('openclawIntegration.actions.rotateSecret')
                  : t('openclawIntegration.actions.generateSecret')}
              </Button>
              {revealedSecret && (
                <Button type="button" variant="secondary" className="bg-emerald-100 text-emerald-800 hover:bg-emerald-200" onClick={handleCopySecret}>
                  <Copy className="h-4 w-4" />
                  {t('openclawIntegration.actions.copySecret')}
                </Button>
              )}
            </div>

            {revealedSecret && (
              <div className="animate-in fade-in slide-in-from-top-2 rounded-[20px] border border-emerald-200 bg-emerald-50/50 p-5">
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-2.5 text-emerald-700">
                    <CheckCircle2 className="h-5 w-5 fill-emerald-100" />
                    <p className="text-sm font-semibold tracking-wide">{t('openclawIntegration.secret.revealedTitle')}</p>
                  </div>
                  <div className="relative group">
                    <code className="block break-all rounded-[12px] border border-emerald-100 bg-white px-5 py-4 font-mono text-sm font-medium text-slate-800 transition-all group-hover:border-emerald-300">
                      {revealedSecret}
                    </code>
                    <button 
                      onClick={handleCopySecret}
                      className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-slate-100 p-2 text-slate-500 opacity-0 transition-all hover:bg-emerald-100 hover:text-emerald-700 group-hover:opacity-100"
                    >
                      <Copy className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className={cn(uiChrome.shell, 'overflow-hidden')}>
        <button
          type="button"
          onClick={() => setGuideOpen((open) => !open)}
          className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left"
          aria-expanded={guideOpen}
        >
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <div className="rounded-[16px] bg-cyan-50 p-2.5 text-cyan-700">
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
          <div className={cn(uiChrome.control, 'flex shrink-0 items-center gap-3 self-start px-3 py-1.5 text-sm text-slate-600 shadow-none')}>
            <span>
              {guideOpen
                ? t('actions.collapse')
                : t('actions.expand')}
            </span>
            <ChevronDown className={cn('h-4 w-4 transition-transform', guideOpen && 'rotate-180')} />
          </div>
        </button>

        {guideOpen ? (
          <div className="border-t border-slate-200 px-6 pb-6 pt-8 md:px-10">
            <div className="mx-auto flex w-full max-w-4xl flex-col">
              <QuickStartStep
                step="1"
                title={t('openclawIntegration.guide.steps.secret.title')}
                description={t('openclawIntegration.guide.steps.secret.description')}
                statusLabel={
                  secretReady
                    ? t('openclawIntegration.guide.stepStatus.done')
                    : t('openclawIntegration.guide.stepStatus.actionNeeded')
                }
                statusTone={secretReady ? 'success' : 'warning'}
              >
                <div className={cn(uiChrome.inset, 'px-4 py-3 text-sm leading-6 text-foreground/85')}>
                  {secretReady
                    ? t('openclawIntegration.guide.steps.secret.ready')
                    : t('openclawIntegration.guide.steps.secret.missing')}
                </div>
              </QuickStartStep>

              <QuickStartStep
                step="2"
                title={t('openclawIntegration.guide.steps.catalog.title')}
                description={t('openclawIntegration.guide.steps.catalog.description')}
                statusLabel={
                  catalogReady
                    ? t('openclawIntegration.guide.stepStatus.done')
                    : t('openclawIntegration.guide.stepStatus.actionNeeded')
                }
                statusTone={catalogReady ? 'success' : 'warning'}
                action={
                    <Button type="button" variant="outline" size="sm" onClick={scrollToCatalog}>
                      {t('openclawIntegration.guide.actions.jumpToCatalog')}
                    </Button>
                  }
              >
                <div
                  className={cn(
                    'rounded-[12px] px-4 py-3 text-sm leading-6',
                    catalogReady
                      ? 'border border-emerald-200 bg-emerald-50/80 text-emerald-800'
                      : 'border border-amber-200 bg-amber-50/80 text-amber-900'
                  )}
                >
                  {catalogReady
                    ? t('openclawIntegration.guide.steps.catalog.ready', { count: exposedAvailableCount })
                    : t('openclawIntegration.guide.steps.catalog.missing')}
                </div>
              </QuickStartStep>

              <QuickStartStep
                step="3"
                title={t('openclawIntegration.guide.steps.plugin.title')}
                description={t('openclawIntegration.guide.steps.plugin.description')}
                statusLabel={t('openclawIntegration.guide.stepStatus.manual')}
                statusTone="neutral"
              >
                <GuideCallout icon={<ShieldCheck className="h-4 w-4" />}>
                  {t('openclawIntegration.guide.steps.plugin.hostHint')}
                </GuideCallout>

                <div className="space-y-4">
                  {orderedSetupCards.map((block) => (
                    <CopyableCodeBlock
                      key={block.order}
                      orderLabel={block.order}
                      title={block.title}
                      description={block.description}
                      code={block.code}
                      copyLabel={block.copyLabel}
                      onCopy={(value) => {
                        void copyToClipboard(value, t('openclawIntegration.messages.copied'))
                      }}
                    />
                  ))}
                </div>

                <GuideCallout icon={<Wrench className="h-4 w-4" />}>
                  <p className="font-semibold">{t('openclawIntegration.guide.maintenanceTitle')}</p>
                  <p className="mt-1">{t('openclawIntegration.guide.maintenanceDescription')}</p>
                  <ul className="mt-3 space-y-2">
                    {maintenanceChecks.map((item) => (
                      <li key={item} className="flex items-start gap-2">
                        <span className="mt-2 h-1.5 w-1.5 rounded-full bg-current/60" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </GuideCallout>

                <div className="space-y-4">
                  {upgradeCommandCards.map((block) => (
                    <CopyableCodeBlock
                      key={block.order}
                      orderLabel={block.order}
                      title={block.title}
                      description={block.description}
                      code={block.code}
                      copyLabel={block.copyLabel}
                      onCopy={(value) => {
                        void copyToClipboard(value, t('openclawIntegration.messages.copied'))
                      }}
                    />
                  ))}
                </div>
              </QuickStartStep>

              <QuickStartStep
                step="4"
                title={t('openclawIntegration.guide.steps.verify.title')}
                description={t('openclawIntegration.guide.steps.verify.description')}
                statusLabel={t('openclawIntegration.guide.stepStatus.manual')}
                statusTone="neutral"
              >
                <GuideCallout icon={<AlertTriangle className="h-4 w-4" />} tone="warning">
                  <p className="font-semibold">{t('openclawIntegration.guide.restartTitle')}</p>
                  <p className="mt-1">{t('openclawIntegration.guide.restartDescription')}</p>
                </GuideCallout>

                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.9fr)]">
                  <GuideChecklistCard title={t('openclawIntegration.guide.verifyChecksTitle')} items={verificationChecks} />

                  <CopyableCodeBlock
                    title={t('openclawIntegration.guide.verifyPromptsTitle')}
                    description={t('openclawIntegration.guide.verifyPromptsDescription')}
                    code={verificationPrompts.join('\n')}
                    copyLabel={t('openclawIntegration.guide.actions.copy')}
                    onCopy={(value) => {
                      void copyToClipboard(value, t('openclawIntegration.messages.copied'))
                    }}
                  />
                </div>
              </QuickStartStep>

              <div className={cn(uiChrome.inset, 'mt-4 overflow-hidden p-0')}>
                <button
                  type="button"
                  onClick={() => setAdvancedOpen((open) => !open)}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
                  aria-expanded={advancedOpen}
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-3">
                      <div className="rounded-[16px] bg-slate-100 p-2.5 text-slate-700">
                        <AlertTriangle className="h-5 w-5" />
                      </div>
                      <h3 className="text-base font-semibold text-slate-900">
                        {t('openclawIntegration.guide.advancedTitle')}
                      </h3>
                    </div>
                    <p className="pl-[3.25rem] text-sm leading-6 text-slate-600">
                      {t('openclawIntegration.guide.advancedDescription')}
                    </p>
                  </div>
                  <div className={cn(uiChrome.control, 'flex shrink-0 items-center gap-3 self-start px-3 py-1.5 text-sm text-slate-600 shadow-none')}>
                    <span>
                      {advancedOpen
                        ? t('openclawIntegration.guide.actions.collapse')
                        : t('openclawIntegration.guide.actions.expand')}
                    </span>
                    <ChevronDown className={cn('h-4 w-4 transition-transform', advancedOpen && 'rotate-180')} />
                  </div>
                </button>

                {advancedOpen ? (
                  <div className="border-t border-slate-200 px-5 pb-5 pt-5">
                    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
                      <div className={cn(uiChrome.card, 'p-5')}>
                        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-500">
                          {t('openclawIntegration.guide.endpointsTitle')}
                        </p>
                        <div className="mt-4 space-y-3">
                          <div className={cn(uiChrome.inset, 'px-4 py-3')}>
                            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                              {t('openclawIntegration.guide.endpoints.auth')}
                            </p>
                            <code className="mt-2 block break-all text-sm text-slate-800">
                              Authorization: Bearer {'<integration_secret>'}
                            </code>
                          </div>
                          <div className={cn(uiChrome.inset, 'px-4 py-3')}>
                            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                              {t('openclawIntegration.guide.endpoints.catalog')}
                            </p>
                            <code className="mt-2 block break-all text-sm text-slate-800">
                              GET /api/integrations/openclaw/capabilities
                            </code>
                          </div>
                          <div className={cn(uiChrome.inset, 'px-4 py-3')}>
                            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                              {t('openclawIntegration.guide.endpoints.execute')}
                            </p>
                            <code className="mt-2 block break-all text-sm text-slate-800">
                              POST /api/integrations/openclaw/capabilities/{'{capabilityKey}'}/execute
                            </code>
                          </div>
                        </div>
                      </div>

                      <div className={cn(uiChrome.card, 'p-5')}>
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
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </section>

      <section id="openclaw-catalog-section" className={cn(uiChrome.shell, 'p-6')}>
        <SectionHeader
          title={t('openclawIntegration.catalog.title')}
          description={t('openclawIntegration.catalog.description')}
          action={
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => setShowResetConfirm(true)} disabled={isBusy}>
                <RefreshCcw className="h-4 w-4" />
                {t('openclawIntegration.actions.resetSystemItems')}
              </Button>
              <Button type="button" onClick={() => openCreateDialog('tool')}>
                <Plus className="h-4 w-4" />
                {t('openclawIntegration.actions.addCapability')}
              </Button>
            </div>
          }
        />
        {!catalogReady ? (
          <div className="mt-5 rounded-[12px] border border-amber-200 bg-amber-50/80 px-4 py-4 text-sm leading-6 text-amber-900">
            <p className="font-semibold">{t('openclawIntegration.catalog.blockedTitle')}</p>
            <p className="mt-1">{t('openclawIntegration.catalog.blockedDescription')}</p>
          </div>
        ) : null}
        <div className="mt-5 rounded-[12px] border border-cyan-200 bg-cyan-50/80 px-4 py-4 text-sm leading-6 text-cyan-950">
          <p className="font-semibold">{t('openclawIntegration.catalog.refreshNoticeTitle')}</p>
          <p className="mt-1">{t('openclawIntegration.catalog.refreshNoticeDescription')}</p>
        </div>
        {catalogItems.length ? (
          <div className="mt-5 grid gap-4">
            {catalogItems.map((item) => (
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
                retiredLabel={t('openclawIntegration.status.retired')}
                availableLabel={t('openclawIntegration.status.available')}
                unavailableLabel={t('openclawIntegration.status.unavailable')}
                inputLabel={t('openclawIntegration.labels.input')}
                outputLabel={t('openclawIntegration.labels.output')}
              />
            ))}
          </div>
        ) : (
          <div className="mt-5 rounded-[20px] border border-dashed border-slate-300 bg-slate-50/80 p-10 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-[16px] bg-white shadow-sm">
              <Boxes className="h-6 w-6 text-slate-500" />
            </div>
            <p className="mt-4 text-base font-semibold text-slate-900">
              {t('openclawIntegration.catalog.emptyTitle')}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {t('openclawIntegration.catalog.emptyDescription')}
            </p>
            <Button type="button" className="mt-5" onClick={() => openCreateDialog('tool')}>
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
              {dialogMode === 'create'
                ? t('openclawIntegration.dialog.createTitle')
                : editingItem?.isSystemItem
                  ? t('openclawIntegration.dialog.editSystemItem')
                  : t('openclawIntegration.dialog.editTitle')}
            </DialogTitle>
            <DialogDescription>
              {dialogMode === 'create'
                ? t('openclawIntegration.dialog.createDescription')
                : editingItem?.isSystemItem
                  ? t('openclawIntegration.dialog.editSystemItemDescription')
                  : t('openclawIntegration.dialog.editDescription')}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            <section className="space-y-3">
              <Label>{t('openclawIntegration.form.sourceType')}</Label>
              <div className="grid gap-3 sm:grid-cols-3">
                {(['tool', 'workflow', 'agent'] as const).map((sourceType) => (
                  <button
                    key={sourceType}
                    type="button"
                    className={cn(
                      'rounded-[16px] border px-4 py-4 text-left transition',
                      draft.sourceType === sourceType
                        ? 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/10'
                        : 'border-slate-200 bg-slate-50/70 text-slate-700 hover:border-slate-300'
                    )}
                      onClick={() => {
                        setSelectedSourceKey(null)
                        setSourcePickerOpen(false)
                        setSourceSearch('')
                        setContractOrigin('source')
                        setContractAdvancedOpen(false)
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

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <Label>{t('openclawIntegration.form.source')}</Label>
                {sourceQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
              </div>
              <Popover open={sourcePickerOpen} onOpenChange={setSourcePickerOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className={cn(
                      uiChrome.inset,
                      'group flex min-h-[92px] w-full items-center justify-between gap-4 px-5 py-4 text-left shadow-none transition-all hover:border-slate-300 hover:bg-white'
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-4">
                      <div className={cn(uiChrome.control, 'flex h-12 w-12 shrink-0 items-center justify-center text-slate-600 shadow-none')}>
                        <SelectedSourceIcon className="h-5 w-5" />
                      </div>
                      <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-semibold text-slate-900">
                            {selectedSource?.title || t('openclawIntegration.form.sourcePickerEmpty')}
                          </p>
                          {selectedSource ? (
                            <>
                              <CapabilityBadge
                                colorClassName={
                                  selectedSource.isSystem
                                    ? 'border-cyan-200 bg-cyan-50 text-cyan-700'
                                    : 'border-violet-200 bg-violet-50 text-violet-700'
                                }
                              >
                                {selectedSource.isSystem
                                  ? t('openclawIntegration.labels.system')
                                  : t('openclawIntegration.labels.user')}
                              </CapabilityBadge>
                              {!selectedSource.bindable ? (
                                <CapabilityBadge colorClassName="border-amber-200 bg-amber-50 text-amber-700">
                                  {t('openclawIntegration.status.unavailable')}
                                </CapabilityBadge>
                              ) : null}
                            </>
                          ) : null}
                        </div>
                        <p className="line-clamp-2 text-sm leading-6 text-slate-600">
                          {selectedSource?.description || t('openclawIntegration.form.sourcePickerHint')}
                        </p>
                        {selectedSource?.unavailableReason ? (
                          <p className="text-xs leading-5 text-amber-700">
                            {selectedSource.unavailableReason}
                          </p>
                        ) : null}
                      </div>
                    </div>

                    <div className="flex shrink-0 items-center gap-3">
                      <span className={cn(uiChrome.control, 'px-3 py-1.5 text-sm font-medium text-slate-700 shadow-none transition-colors group-hover:border-slate-300')}>
                        {t('openclawIntegration.form.chooseSource')}
                      </span>
                      <div className={cn(uiChrome.control, 'flex h-9 w-9 items-center justify-center rounded-full text-slate-500 shadow-none transition-all group-hover:border-slate-300 group-hover:text-slate-700')}>
                        <ChevronDown className={cn('h-4 w-4 transition-transform', sourcePickerOpen && 'rotate-180')} />
                      </div>
                    </div>
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  align="start"
                  sideOffset={10}
                  className="w-[min(780px,calc(100vw-3rem))] p-0"
                >
                  <div className="border-b border-slate-200 px-5 py-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="space-y-1">
                        <p className="text-sm font-semibold text-slate-900">
                          {t('openclawIntegration.form.sourcePickerTitle')}
                        </p>
                        <p className="text-sm leading-6 text-slate-600">
                          {t('openclawIntegration.form.sourcePickerDescription')}
                        </p>
                      </div>
                      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                        {filteredSources.length}
                      </span>
                    </div>

                    <div className="relative mt-4">
                      <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                      <input
                        value={sourceSearch}
                        onChange={(event) => setSourceSearch(event.target.value)}
                        placeholder={t('openclawIntegration.form.sourceSearchPlaceholder')}
                        className={cn(uiField.input, 'h-11 bg-slate-50 pl-11 pr-4')}
                      />
                    </div>
                  </div>

                  <div className="max-h-[420px] space-y-2 overflow-y-auto p-3">
                    {filteredSources.length ? filteredSources.map((source) => {
                      const active = selectedSourceKey === source.sourceKey
                      const Icon = sourceTypeIcon(source.sourceType)
                      const displayName = getCatalogSourceDisplayName(source)
                      const displayDescription = getCatalogSourceDisplayDescription(source)
                      return (
                        <button
                          key={source.sourceKey}
                          type="button"
                          onClick={() => {
                            setSelectedSourceKey(source.sourceKey)
                            applySourceSelection(source)
                            setSourcePickerOpen(false)
                          }}
                          className={cn(
                            'flex w-full items-start gap-4 rounded-[16px] border px-4 py-4 text-left transition-all',
                            active
                              ? 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/10'
                              : 'border-slate-200 bg-white text-slate-800 hover:border-slate-300 hover:bg-slate-50',
                            !source.bindable && 'opacity-80'
                          )}
                        >
                          <div
                            className={cn(
                              'mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px] border',
                              active
                                ? 'border-white/15 bg-white/10 text-white'
                                : 'border-slate-200 bg-slate-50 text-slate-600'
                            )}
                          >
                            <Icon className="h-4 w-4" />
                          </div>

                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="text-sm font-semibold">{displayName}</p>
                              {source.sourceToolName ? (
                                <code
                                  className={cn(
                                    'rounded-full px-2 py-0.5 text-[11px] font-medium',
                                    active ? 'bg-white/10 text-white/90' : 'bg-slate-100 text-slate-600',
                                  )}
                                >
                                  {source.sourceToolName}
                                </code>
                              ) : null}
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
                              {displayDescription || '-'}
                            </p>
                            {!source.bindable && source.unavailableReason ? (
                              <p className={cn('mt-2 text-xs leading-5', active ? 'text-white/70' : 'text-amber-700')}>
                                {source.unavailableReason}
                              </p>
                            ) : null}
                          </div>

                          {active ? <Check className="mt-1 h-4 w-4 shrink-0 text-white" /> : null}
                        </button>
                      )
                    }) : (
                      <div className="rounded-[12px] border border-dashed border-slate-300 bg-slate-50/80 px-4 py-10 text-center text-sm text-slate-500">
                        {t('openclawIntegration.form.sourceSearchEmpty')}
                      </div>
                    )}
                  </div>
                </PopoverContent>
              </Popover>
              {selectedSource?.isSystem && (draft.sourceType === 'workflow' || draft.sourceType === 'agent') ? (
                <div className="rounded-[12px] border border-amber-200 bg-amber-50/80 px-4 py-3 text-sm leading-6 text-amber-800">
                  {t('settings.skills.systemTargetBindingHint')}
                </div>
              ) : null}
              {editingRetiredItem?.retirementReason ? (
                <div className="rounded-[12px] border border-amber-200 bg-amber-50/80 px-4 py-3 text-sm leading-6 text-amber-800">
                  {editingRetiredItem.retirementReason}
                </div>
              ) : null}
            </section>

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
                      {editingRetiredItem
                        ? t('openclawIntegration.form.retiredHint')
                        : t('openclawIntegration.form.exposedHint')}
                    </p>
                  </div>
                  <Switch
                    checked={editingRetiredItem ? false : draft.enabled}
                    onCheckedChange={(enabled) => patchDraft({ enabled })}
                    disabled={Boolean(editingRetiredItem)}
                    className="data-[state=checked]:bg-emerald-500"
                  />
                </div>
              </div>
            </section>

            <>
              <section className={cn(uiChrome.inset, 'space-y-4 p-5')}>
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Label>{t('openclawIntegration.form.contractPreviewTitle')}</Label>
                      <CapabilityBadge
                        colorClassName={
                          contractUsesOverride
                            ? 'border-amber-200 bg-amber-50 text-amber-700'
                            : 'border-emerald-200 bg-emerald-50 text-emerald-700'
                        }
                      >
                        {contractUsesOverride
                          ? t('openclawIntegration.form.contractStatusOverride')
                          : t('openclawIntegration.form.contractStatusDerived')}
                      </CapabilityBadge>
                    </div>
                    <p className="max-w-3xl text-sm leading-6 text-slate-600">
                      {contractUsesOverride
                        ? t('openclawIntegration.form.contractManualNote')
                        : draft.sourceType === 'workflow'
                          ? t('openclawIntegration.form.contractWorkflowNote')
                          : draft.sourceType === 'agent'
                            ? t('openclawIntegration.form.agentAutoContractHint')
                            : t('openclawIntegration.form.contractAutoNote')}
                    </p>
                  </div>

                  {contractUsesOverride && sourceDerivedContract && effectiveContract.schemaEditable ? (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setContractOrigin('source')
                        setDraft((currentDraft) => ({ ...currentDraft, ...sourceDerivedContract }))
                      }}
                    >
                      <RefreshCcw className="h-4 w-4" />
                      {t('openclawIntegration.form.restoreSourceContract')}
                    </Button>
                  ) : null}
                </div>

                <div className="grid gap-3 md:grid-cols-3">
                  <SummaryCard
                    label={t('openclawIntegration.form.contractStatus')}
                    value={
                      contractUsesOverride
                        ? t('openclawIntegration.form.contractStatusOverride')
                        : t('openclawIntegration.form.contractStatusDerived')
                    }
                    hint={
                      contractUsesOverride
                        ? t('openclawIntegration.form.contractStatusOverrideHint')
                        : t('openclawIntegration.form.contractStatusDerivedHint')
                    }
                    active={!contractUsesOverride}
                  />
                  <SummaryCard
                    label={t('openclawIntegration.form.contractSourceMode')}
                    value={
                      effectiveContract.schemaEditable
                        ? t('openclawIntegration.form.contractSourceModeEditable')
                        : t('openclawIntegration.form.contractSourceModeReadonly')
                    }
                    hint={
                      effectiveContract.schemaEditable
                        ? t('openclawIntegration.form.contractSourceModeEditableHint')
                        : t('openclawIntegration.form.contractSourceModeReadonlyHint')
                    }
                    active={!effectiveContract.schemaEditable}
                  />
                  <SummaryCard
                    label={t('openclawIntegration.form.toolResponseMode')}
                    value={t(`openclawIntegration.responseModes.${effectiveContract.toolResponseMode}`)}
                    hint={t(`openclawIntegration.responseModeDescriptions.${effectiveContract.toolResponseMode}`)}
                  />
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <div className={cn(uiChrome.card, 'px-5 py-4')}>
                    <p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">
                      {t('openclawIntegration.form.inputSummary')}
                    </p>
                    <p className="mt-3 text-sm leading-6 text-slate-700">
                      {effectiveContract.inputSummary || '-'}
                    </p>
                  </div>
                  <div className={cn(uiChrome.card, 'px-5 py-4')}>
                    <p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">
                      {t('openclawIntegration.form.outputSummary')}
                    </p>
                    <p className="mt-3 text-sm leading-6 text-slate-700">
                      {effectiveContract.outputSummary || '-'}
                    </p>
                  </div>
                </div>
              </section>

              <section className={cn(uiChrome.card, 'overflow-hidden')}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left transition hover:bg-slate-50/80"
                  onClick={() => setContractAdvancedOpen((open) => !open)}
                  aria-expanded={contractAdvancedOpen}
                >
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-slate-900">
                        {t('openclawIntegration.form.advancedContractTitle')}
                      </p>
                      {contractUsesOverride ? (
                        <CapabilityBadge colorClassName="border-amber-200 bg-amber-50 text-amber-700">
                          {t('openclawIntegration.form.contractStatusOverride')}
                        </CapabilityBadge>
                      ) : null}
                    </div>
                    <p className="text-sm leading-6 text-slate-600">
                      {t('openclawIntegration.form.advancedContractDescription')}
                    </p>
                  </div>
                  <div className={cn(uiChrome.control, 'flex items-center gap-3 px-3 py-1.5 text-sm font-medium text-slate-600 shadow-none')}>
                    <span>
                      {contractAdvancedOpen
                        ? t('openclawIntegration.form.advancedContractHide')
                        : t('openclawIntegration.form.advancedContractShow')}
                    </span>
                    <ChevronDown className={cn('h-4 w-4 transition-transform', contractAdvancedOpen && 'rotate-180')} />
                  </div>
                </button>

                {contractAdvancedOpen ? (
                  <div className="space-y-5 border-t border-slate-200 px-5 py-5">
                    <section className="grid gap-4 md:grid-cols-2">
                      <TextareaField
                        label={t('openclawIntegration.form.inputSummary')}
                        value={effectiveContract.inputSummary}
                        onChange={(value) => patchContractDraft({ inputSummary: value })}
                        rows={3}
                        disabled={!effectiveContract.schemaEditable}
                      />
                      <TextareaField
                        label={t('openclawIntegration.form.outputSummary')}
                        value={effectiveContract.outputSummary}
                        onChange={(value) => patchContractDraft({ outputSummary: value })}
                        rows={3}
                        disabled={!effectiveContract.schemaEditable}
                      />
                    </section>

                    {draft.sourceType !== 'workflow' ? (
                      <section className="space-y-3">
                        <Label>{t('openclawIntegration.form.toolResponseMode')}</Label>
                        <div className="grid gap-3 sm:grid-cols-2">
                          {(['json_schema', 'text_field'] as const).map((mode) => (
                            <button
                              key={mode}
                              type="button"
                              className={cn(
                                'rounded-[16px] border px-4 py-4 text-left transition',
                                effectiveContract.toolResponseMode === mode
                                  ? 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/10'
                                  : 'border-slate-200 bg-slate-50/70 text-slate-700 hover:border-slate-300',
                                !effectiveContract.schemaEditable && 'cursor-not-allowed opacity-70'
                              )}
                              onClick={() => patchContractDraft({ toolResponseMode: mode })}
                              disabled={!effectiveContract.schemaEditable}
                            >
                              <p className="text-sm font-semibold">
                                {t(`openclawIntegration.responseModes.${mode}`)}
                              </p>
                              <p className={cn('mt-1 text-xs leading-5', effectiveContract.toolResponseMode === mode ? 'text-white/80' : 'text-slate-500')}>
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
                          value={effectiveContract.inputSchemaText}
                          onChange={(event) => patchContractDraft({ inputSchemaText: event.target.value })}
                          className={TEXTAREA_CLASSNAME}
                          disabled={!effectiveContract.schemaEditable}
                        />
                      </div>
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <ShieldCheck className="h-4 w-4 text-slate-500" />
                          <Label>{t('openclawIntegration.form.outputSchema')}</Label>
                        </div>
                        <textarea
                          rows={12}
                          value={effectiveContract.outputSchemaText}
                          onChange={(event) => patchContractDraft({ outputSchemaText: event.target.value })}
                          className={TEXTAREA_CLASSNAME}
                          disabled={!effectiveContract.schemaEditable}
                        />
                      </div>
                    </section>

                    {!effectiveContract.schemaEditable ? (
                      <div className="rounded-[12px] border border-cyan-200 bg-cyan-50/80 px-4 py-3 text-sm leading-6 text-cyan-800">
                        {t('openclawIntegration.form.readonlySchemaHint')}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </section>
            </>
          </div>

          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="button" onClick={() => void handleSaveDialog()} disabled={isBusy}>
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
        confirmText={t('openclawIntegration.actions.resetSystemItems')}
        cancelText={t('common.cancel')}
        onConfirm={() => {
          void handleResetSystemItems()
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
    </SettingsPageShell>
  )
}
