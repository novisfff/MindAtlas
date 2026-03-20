import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Bot, Loader2, SendHorizontal, Sparkles, Wand2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type {
  WorkflowCopilotConversationItem,
  WorkflowCopilotMode,
  WorkflowCopilotOperation,
  WorkflowCopilotProposal,
  WorkflowCopilotRequest,
  WorkflowCopilotSelection,
  WorkflowCopilotTestRunContext,
  WorkflowCopilotValidationContext,
  WorkflowInput,
} from '../../api/workflow'
import { respondWorkflowCopilotById } from '../../api/workflows'
import { WorkflowEditorSurfaceShell } from './WorkflowEditorSurfaceShell'

interface WorkflowCopilotLaunchContext {
  nonce: number
  mode: WorkflowCopilotMode
  instruction?: string
  title?: string
  selection?: WorkflowCopilotSelection
  appendSelection?: boolean
  validationContext?: WorkflowCopilotValidationContext
  testRunContext?: WorkflowCopilotTestRunContext
}

interface WorkflowCopilotPanelProps {
  open: boolean
  workflowId: string | null
  draft: WorkflowInput
  launchContext: WorkflowCopilotLaunchContext | null
  layout?: 'floating' | 'split'
  proposal: WorkflowCopilotProposal | null
  previewVisible: boolean
  previewMode: 'current' | 'proposed'
  isApplyingProposal: boolean
  currentProposalApplyState: 'idle' | 'applied_current' | 'applied_stale'
  onClose: () => void
  onProposalChange: (proposal: WorkflowCopilotProposal | null) => void
  onPreviewVisibleChange: (visible: boolean) => void
  onPreviewModeChange: (mode: 'current' | 'proposed') => void
  onApplyProposal: (proposal: WorkflowCopilotProposal) => void | Promise<void>
  onUndoAppliedProposal: () => void | Promise<void>
}

type CopilotMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  payloadContent?: string
  status?: 'pending' | 'done'
}

type CopilotTargetScope = 'selection' | 'container' | 'multi'

type CopilotTargetSummary = {
  scope: CopilotTargetScope
  displayLabel: string
  nodeType: string | null
  nodeId: string
  containerLabel?: string | null
  missing: boolean
}

const COPILOT_PANEL_MIN_HEIGHT = 440
const COPILOT_PANEL_DEFAULT_HEIGHT = 760
const COPILOT_PANEL_VIEWPORT_OFFSET = 112

function buildMessageId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function modeLabel(mode: WorkflowCopilotMode, t: (key: string, options?: Record<string, unknown>) => string): string {
  return t(`settings.skills.workflowCopilot.modes.${mode}`)
}

function normalizeBodyNodes(config: Record<string, unknown> | null | undefined): Array<{ nodeId: string; nodeType: string; label: string }> {
  const raw = config?.bodyNodes ?? config?.body_nodes
  if (!Array.isArray(raw)) return []
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      nodeId: String(item.nodeId ?? item.node_id ?? '').trim(),
      nodeType: String(item.nodeType ?? item.node_type ?? '').trim(),
      label: String(item.label ?? '').trim(),
    }))
    .filter((item) => item.nodeId)
}

function resolveCopilotTargetSummary(
  draft: WorkflowInput,
  selection: WorkflowCopilotSelection,
  nodeId: string,
  fallbackTitle?: string,
): CopilotTargetSummary | null {
  const normalizedNodeId = String(nodeId || '').trim()
  const scope: CopilotTargetScope = selection.scope === 'container' ? 'container' : 'selection'
  if (!normalizedNodeId && !fallbackTitle) return null

  if (selection.scope === 'container') {
    const containerNode = draft.nodes.find((node) => node.nodeId === selection.containerId)
    const containerLabel = String(containerNode?.label ?? selection.containerId ?? '').trim() || null
    const bodyNodes = normalizeBodyNodes((containerNode?.config as Record<string, unknown> | null | undefined) ?? undefined)
    const targetNode = bodyNodes.find((node) => node.nodeId === normalizedNodeId)
    if (targetNode) {
      const nodeLabel = targetNode.label || normalizedNodeId
      return {
        scope,
        displayLabel: containerLabel ? `${containerLabel} / ${nodeLabel}` : nodeLabel,
        nodeType: targetNode.nodeType || null,
        nodeId: targetNode.nodeId,
        containerLabel,
        missing: false,
      }
    }
    return {
      scope,
      displayLabel: fallbackTitle || [containerLabel, normalizedNodeId].filter(Boolean).join(' / ') || normalizedNodeId,
      nodeType: null,
      nodeId: normalizedNodeId,
      containerLabel,
      missing: true,
    }
  }

  const targetNode = draft.nodes.find((node) => node.nodeId === normalizedNodeId)
  if (targetNode) {
    const nodeLabel = String(targetNode.label ?? '').trim() || normalizedNodeId
    return {
      scope,
      displayLabel: nodeLabel,
      nodeType: targetNode.nodeType,
      nodeId: targetNode.nodeId,
      missing: false,
    }
  }

  return {
    scope,
    displayLabel: fallbackTitle || normalizedNodeId,
    nodeType: null,
    nodeId: normalizedNodeId,
    missing: true,
  }
}

function resolveCopilotTargetSummaries(
  draft: WorkflowInput,
  selection: WorkflowCopilotSelection | undefined,
  fallbackTitle?: string,
): CopilotTargetSummary[] {
  if (!selection) return []
  const nodeIds = selection.nodeIds.map((item) => String(item || '').trim()).filter(Boolean)
  return nodeIds
    .map((nodeId, index) => resolveCopilotTargetSummary(draft, selection, nodeId, index === 0 ? fallbackTitle : undefined))
    .filter((item): item is CopilotTargetSummary => Boolean(item))
}

function summarizeOperation(operation: WorkflowCopilotOperation): string {
  switch (operation.type) {
    case 'add_node':
      return `Add ${operation.nodeType || 'node'}${operation.nodeId ? ` (${operation.nodeId})` : ''}`
    case 'update_node':
      return `Update ${operation.nodeId || 'node'}`
    case 'remove_node':
      return `Remove ${operation.nodeId || 'node'}`
    case 'add_edge':
      return `Connect ${operation.sourceNodeId || '?'} -> ${operation.targetNodeId || '?'}`
    case 'remove_edge':
      return operation.edgeId ? `Remove edge ${operation.edgeId}` : `Remove edge ${operation.sourceNodeId || '?'} -> ${operation.targetNodeId || '?'}`
    case 'move_node':
      return `Move ${operation.nodeId || 'node'}`
    case 'autolayout':
      return 'Auto layout'
    default:
      return operation.type
  }
}

type OperationMetaItem = {
  label: string
  value: string
}

type OperationSection = {
  title: string
  entries: Array<[string, unknown]>
}

function buildOperationMeta(operation: WorkflowCopilotOperation): OperationMetaItem[] {
  const items: OperationMetaItem[] = []
  const push = (label: string, value: unknown) => {
    const text = String(value ?? '').trim()
    if (!text) return
    items.push({ label, value: text })
  }

  push('节点', operation.nodeId)
  push('类型', operation.nodeType)
  push('容器', operation.containerId)
  push('边', operation.edgeId)
  if (operation.sourceNodeId || operation.targetNodeId) {
    push('连线', `${operation.sourceNodeId || '?'} -> ${operation.targetNodeId || '?'}`)
  }
  if (operation.label) {
    push('标签', operation.label)
  }
  if (operation.positionX != null || operation.positionY != null) {
    push('位置', `${operation.positionX ?? '-'}, ${operation.positionY ?? '-'}`)
  }
  if (operation.replaceConfig) {
    push('配置策略', '整体替换')
  }
  return items
}

function buildOperationSections(operation: WorkflowCopilotOperation): OperationSection[] {
  const sections: OperationSection[] = []
  const appendSection = (title: string, value: unknown) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return
    const entries = Object.entries(value).filter(([, entryValue]) => entryValue !== undefined && entryValue !== null)
    if (entries.length === 0) return
    sections.push({ title, entries })
  }

  appendSection('配置补丁', operation.configPatch)
  appendSection('完整配置', operation.config)
  appendSection('条件表达式', operation.conditionExpr)
  return sections
}

function formatOperationValue(value: unknown): { kind: 'text' | 'json'; text: string } {
  if (typeof value === 'string') {
    return { kind: 'text', text: value }
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return { kind: 'text', text: String(value) }
  }
  if (Array.isArray(value) || (value && typeof value === 'object')) {
    return { kind: 'json', text: JSON.stringify(value, null, 2) }
  }
  return { kind: 'text', text: String(value ?? '') }
}

function buildInjectedEditInstruction(
  targets: CopilotTargetSummary[],
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const primaryTarget = targets[0]
  if (!primaryTarget) return ''
  if (targets.length === 1) {
    const key = primaryTarget.scope === 'container'
      ? 'settings.skills.workflowCopilot.defaultEditInstructionSubflow'
      : 'settings.skills.workflowCopilot.defaultEditInstruction'
    return t(key, {
      targetLabel: primaryTarget.displayLabel,
      nodeType: primaryTarget.nodeType || '-',
      nodeId: primaryTarget.nodeId,
    })
  }
  return t('settings.skills.workflowCopilot.defaultMultiEditInstruction', {
    count: targets.length,
    targets: targets.map((target) => `${target.displayLabel}（${target.nodeType || '-'}/${target.nodeId}）`).join('；'),
  })
}

export function WorkflowCopilotPanel({
  open,
  workflowId,
  draft,
  launchContext,
  layout = 'floating',
  proposal,
  previewVisible,
  previewMode,
  isApplyingProposal,
  currentProposalApplyState,
  onClose,
  onProposalChange,
  onPreviewVisibleChange,
  onPreviewModeChange,
  onApplyProposal,
  onUndoAppliedProposal,
}: WorkflowCopilotPanelProps) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<WorkflowCopilotMode>('generate')
  const [title, setTitle] = useState('')
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<CopilotMessage[]>([])
  const [selection, setSelection] = useState<WorkflowCopilotSelection | undefined>(undefined)
  const [validationContext, setValidationContext] = useState<WorkflowCopilotValidationContext | undefined>(undefined)
  const [testRunContext, setTestRunContext] = useState<WorkflowCopilotTestRunContext | undefined>(undefined)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [panelHeight, setPanelHeight] = useState(COPILOT_PANEL_DEFAULT_HEIGHT)
  const resizeStateRef = useRef<{ startY: number; startHeight: number } | null>(null)

  const getMaxPanelHeight = useCallback(() => {
    if (typeof window === 'undefined') return COPILOT_PANEL_DEFAULT_HEIGHT
    return Math.max(COPILOT_PANEL_MIN_HEIGHT, window.innerHeight - COPILOT_PANEL_VIEWPORT_OFFSET)
  }, [])

  const clampPanelHeight = useCallback((height: number) => {
    const maxHeight = getMaxPanelHeight()
    return Math.min(Math.max(height, COPILOT_PANEL_MIN_HEIGHT), maxHeight)
  }, [getMaxPanelHeight])

  const selectedTargets = useMemo(
    () => (mode === 'edit_selection' ? resolveCopilotTargetSummaries(draft, selection, title) : []),
    [draft, mode, selection, title],
  )
  const primarySelectedTarget = selectedTargets[0] ?? null
  const injectedInstruction = useMemo(
    () => (mode === 'edit_selection' ? buildInjectedEditInstruction(selectedTargets, t) : ''),
    [mode, selectedTargets, t],
  )

  useEffect(() => {
    if (!launchContext) return
    if (launchContext.appendSelection && launchContext.mode === 'edit_selection') {
      setMode('edit_selection')
      setTitle(launchContext.title ?? '')
      setSelection(launchContext.selection)
      setValidationContext(launchContext.validationContext)
      setTestRunContext(launchContext.testRunContext)
      onProposalChange(null)
      onPreviewVisibleChange(false)
      setIsSubmitting(false)
      return
    }
    setMode(launchContext.mode)
    setTitle(launchContext.title ?? '')
    setInput(launchContext.mode === 'edit_selection' ? '' : (launchContext.instruction ?? ''))
    setSelection(launchContext.selection)
    setValidationContext(launchContext.validationContext)
    setTestRunContext(launchContext.testRunContext)
    setMessages([])
    onProposalChange(null)
    onPreviewVisibleChange(false)
    setIsSubmitting(false)
  }, [launchContext?.nonce, onPreviewVisibleChange, onProposalChange])

  useEffect(() => {
    setPanelHeight((current) => clampPanelHeight(current))
  }, [clampPanelHeight])

  useEffect(() => {
    const handleResize = () => {
      setPanelHeight((current) => clampPanelHeight(current))
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [clampPanelHeight])

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent) => {
      const state = resizeStateRef.current
      if (!state) return
      const deltaY = event.clientY - state.startY
      setPanelHeight(clampPanelHeight(state.startHeight + deltaY))
    }

    const handlePointerUp = () => {
      resizeStateRef.current = null
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerUp)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [clampPanelHeight])

  const handleResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    resizeStateRef.current = {
      startY: event.clientY,
      startHeight: panelHeight,
    }
  }

  const promptPlaceholder = useMemo(() => {
    switch (mode) {
      case 'edit_selection':
        return t('settings.skills.workflowCopilot.placeholders.edit_selection', {
          targetLabel: primarySelectedTarget?.displayLabel || title || '',
        })
      case 'fix_validation':
        return t('settings.skills.workflowCopilot.placeholders.fix_validation')
      case 'analyze_test_run':
        return t('settings.skills.workflowCopilot.placeholders.analyze_test_run')
      default:
        return t('settings.skills.workflowCopilot.placeholders.generate')
    }
  }, [mode, primarySelectedTarget?.displayLabel, t, title])

  const scopeHint = useMemo(() => {
    if (mode !== 'edit_selection') {
      return t('settings.skills.workflowCopilot.scopeHint')
    }
    if (selectedTargets.length > 1) {
      return t('settings.skills.workflowCopilot.editMultiSelectionScopeHint', {
        count: selectedTargets.length,
      })
    }
    return t('settings.skills.workflowCopilot.editSelectionScopeHint', {
      targetLabel: primarySelectedTarget?.displayLabel || title || '',
    })
  }, [mode, primarySelectedTarget?.displayLabel, selectedTargets.length, t, title])

  const conversationPayload = useMemo<WorkflowCopilotConversationItem[]>(() => {
    return messages
      .filter((message) => message.status !== 'pending')
      .map((message) => ({ role: message.role, content: message.payloadContent ?? message.content }))
  }, [messages])
  const isSplitLayout = layout === 'split'

  if (!open) return null

  const trimmedInput = input.trim()
  const trimmedInjectedInstruction = injectedInstruction.trim()
  const effectiveInstruction = [trimmedInjectedInstruction, trimmedInput].filter(Boolean).join('\n\n').trim()
  const canSend = Boolean(workflowId && effectiveInstruction && !isSubmitting)

  const handleRemoveSelectedTarget = (nodeId: string) => {
    setSelection((current) => {
      if (!current) return current
      const nextNodeIds = current.nodeIds.filter((item) => item !== nodeId)
      if (nextNodeIds.length === 0) {
        setMode('generate')
        setTitle('')
        return undefined
      }
      return {
        ...current,
        nodeIds: nextNodeIds,
      }
    })
    onProposalChange(null)
    onPreviewVisibleChange(false)
  }

  const handleSend = async () => {
    if (!workflowId || !effectiveInstruction || isSubmitting) return

    const userMessage: CopilotMessage = {
      id: buildMessageId('copilot_user'),
      role: 'user',
      content: trimmedInput,
      payloadContent: effectiveInstruction,
      status: 'done',
    }
    const assistantMessageId = buildMessageId('copilot_assistant')

    setMessages((current) => [
      ...current,
      userMessage,
      { id: assistantMessageId, role: 'assistant', content: '', status: 'pending' },
    ])
    setInput('')
    onProposalChange(null)
    onPreviewVisibleChange(false)
    setIsSubmitting(true)

    const payload: WorkflowCopilotRequest = {
      mode,
      instruction: effectiveInstruction,
      draft,
      conversation: [...conversationPayload, { role: 'user', content: effectiveInstruction }],
    }
    if (selection) payload.selection = selection
    if (validationContext) payload.validationContext = validationContext
    if (testRunContext) payload.testRunContext = testRunContext

    try {
      const response = await respondWorkflowCopilotById(workflowId, payload)
      setMessages((current) => current.map((message) => (
        message.id === assistantMessageId
          ? {
              ...message,
              content: response.message || t('settings.skills.workflowCopilot.emptyResponse'),
              status: 'done',
            }
          : message
      )))
      onProposalChange(response.proposal ?? null)
      if (response.proposal) {
        onPreviewModeChange('proposed')
        onPreviewVisibleChange(true)
      } else {
        onPreviewVisibleChange(false)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : t('settings.skills.workflowCopilot.requestFailed')
      setMessages((current) => current.map((item) => (
        item.id === assistantMessageId
          ? { ...item, content: message, status: 'done' }
          : item
      )))
      toast.error(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleApply = async () => {
    if (!proposal || isApplyingProposal) return
    await onApplyProposal(proposal)
  }

  const handleOpenPreview = (mode: 'current' | 'proposed') => {
    onPreviewModeChange(mode)
    onPreviewVisibleChange(true)
  }

  const wrapperClassName = isSplitLayout
    ? 'h-full w-full pointer-events-auto'
    : 'absolute top-24 right-4 xl:right-[26rem] z-40 pointer-events-auto w-[440px] max-w-[calc(100vw-2rem)]'

  const wrapperStyle = isSplitLayout
    ? undefined
    : { height: `${panelHeight}px`, maxHeight: 'calc(100vh - 7rem)' }

  const panelFooter = (
    <div className="space-y-2.5">
      {selectedTargets.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {selectedTargets.map((target) => (
            <div
              key={`${target.scope}:${target.nodeId}`}
              className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] leading-4 ${
                target.missing
                  ? 'border-amber-200 bg-amber-50 text-amber-800'
                  : 'border-slate-200 bg-white text-slate-700'
              }`}
            >
              <span className="truncate font-medium text-slate-900">{target.displayLabel}</span>
              <span className="truncate text-slate-500">{target.nodeType || '-'} / {target.nodeId}</span>
              <button
                type="button"
                onClick={() => handleRemoveSelectedTarget(target.nodeId)}
                className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
                aria-label={t('settings.skills.workflowCopilot.dismissSelectedTarget')}
                title={t('settings.skills.workflowCopilot.dismissSelectedTarget')}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <textarea
        value={input}
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.repeat) return
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            void handleSend()
          }
        }}
        disabled={isSubmitting}
        placeholder={promptPlaceholder}
        className={`w-full resize-none rounded-[18px] border bg-background px-3.5 py-2.5 text-[13px] leading-6 placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/20 ${
          isSplitLayout ? 'min-h-[104px]' : 'min-h-[88px]'
        }`}
      />
      <div className="flex items-center justify-between gap-3">
        <div className="text-[11px] leading-[18px] text-muted-foreground">
          {scopeHint}
        </div>
        <div className="flex items-center gap-2">
          {proposal ? (
            <button
              onClick={handleApply}
              disabled={isApplyingProposal}
              className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-3.5 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            >
              {isApplyingProposal ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
              {t('settings.skills.workflowCopilot.applyProposal')}
            </button>
          ) : null}
          <button
            onClick={() => void handleSend()}
            disabled={!canSend}
            className="inline-flex items-center gap-1.5 rounded-xl bg-slate-900 px-3.5 py-2 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {isSubmitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <SendHorizontal className="w-3.5 h-3.5" />}
            {t('settings.skills.workflowCopilot.send')}
          </button>
        </div>
      </div>
    </div>
  )

  const proposalSummaryCard = proposal ? (
    <div className="rounded-[22px] border border-slate-200/80 bg-white/90 p-3.5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-slate-900">{proposal.title}</div>
          <div className="mt-1 text-[11px] leading-5 text-muted-foreground">{proposal.summary}</div>
        </div>
        <div className={`shrink-0 rounded-2xl border px-2.5 py-1 text-[11px] font-medium ${
          proposal.validation.valid
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-amber-200 bg-amber-50 text-amber-700'
        }`}>
          {proposal.validation.valid
            ? t('settings.skills.workflowCopilot.validationOk')
            : t('settings.skills.workflowCopilot.validationIssues', { count: proposal.validation.errors.length })}
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[11px]">
        {proposal.affectedNodeIds.length > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-slate-700">
            {t('settings.skills.workflowCopilot.affectedNodes', { count: proposal.affectedNodeIds.length })}
          </span>
        )}
        {proposal.layoutRecommendation === 'autolayout' && (
          <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-blue-700">
            <Sparkles className="h-3.5 w-3.5" />
            {t('settings.skills.workflowCopilot.autolayoutSuggested')}
          </span>
        )}
        <button
          type="button"
          onClick={() => handleOpenPreview('proposed')}
          className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-medium transition-colors ${
            previewVisible && previewMode === 'proposed'
              ? 'border-blue-200 bg-blue-50 text-blue-700'
              : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
          }`}
        >
          {t('settings.skills.workflowCopilot.previewProposed')}
        </button>
        <button
          type="button"
          onClick={() => handleOpenPreview('current')}
          className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-medium transition-colors ${
            previewVisible && previewMode === 'current'
              ? 'border-blue-200 bg-blue-50 text-blue-700'
              : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
          }`}
        >
          {t('settings.skills.workflowCopilot.previewCurrent')}
        </button>
        <button
          type="button"
          onClick={() => {
            if (previewVisible) {
              onPreviewVisibleChange(false)
              return
            }
            handleOpenPreview('proposed')
          }}
          className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-medium transition-colors ${
            previewVisible
              ? 'border-slate-200 bg-slate-50 text-slate-700 hover:bg-white'
              : 'border-blue-200 bg-blue-50 text-blue-700'
          }`}
        >
          {previewVisible
            ? t('settings.skills.workflowCopilot.closePreview')
            : t('settings.skills.workflowCopilot.openPreview')}
        </button>
      </div>

      <details className="mt-3.5 group">
        <summary className="cursor-pointer list-none rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] font-semibold text-slate-700 transition-colors group-open:bg-white">
          {t('settings.skills.workflowCopilot.operations')}
        </summary>
        <div className="mt-2.5 space-y-2">
          {proposal.operations.map((operation, index) => (
            <div key={`${operation.type}_${index}`} className="rounded-2xl border border-slate-200 bg-slate-50/80 px-3 py-2.5 text-xs text-slate-700">
              <div className="font-medium">{index + 1}. {summarizeOperation(operation)}</div>

              {buildOperationMeta(operation).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {buildOperationMeta(operation).map((item) => (
                    <div
                      key={`${item.label}_${item.value}`}
                      className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-600"
                    >
                      <span className="text-slate-400">{item.label}:</span> {item.value}
                    </div>
                  ))}
                </div>
              )}

              {buildOperationSections(operation).map((section) => (
                <div key={section.title} className="mt-2.5 space-y-2">
                  <div className="text-[11px] font-semibold tracking-wide text-slate-500">
                    {section.title}
                  </div>
                  <div className="space-y-2">
                    {section.entries.map(([key, rawValue]) => {
                      const formatted = formatOperationValue(rawValue)
                      return (
                        <div key={`${section.title}_${key}`} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                          <div className="mb-1 text-[11px] font-medium text-slate-500">{key}</div>
                          {formatted.kind === 'json' ? (
                            <pre className="overflow-x-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-700">
                              {formatted.text}
                            </pre>
                          ) : (
                            <div className="whitespace-pre-wrap break-words text-[12px] leading-5 text-slate-700">
                              {formatted.text}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      </details>

      {proposal.warnings.length > 0 && (
        <div className="mt-3.5 space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t('settings.skills.workflowCopilot.warnings')}
          </div>
          <div className="space-y-2">
            {proposal.warnings.map((warning, index) => (
              <div key={`${warning}_${index}`} className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {warning}
              </div>
            ))}
          </div>
        </div>
      )}

      {currentProposalApplyState !== 'idle' && (
        <div className={`mt-3.5 rounded-2xl border px-3.5 py-3 text-xs ${
          currentProposalApplyState === 'applied_current'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
            : 'border-amber-200 bg-amber-50 text-amber-800'
        }`}>
          <div className="font-semibold">
            {currentProposalApplyState === 'applied_current'
              ? t('settings.skills.workflowCopilot.appliedStateCurrent')
              : t('settings.skills.workflowCopilot.appliedStateStale')}
          </div>
          <div className="mt-1 leading-relaxed">
            {currentProposalApplyState === 'applied_current'
              ? t('settings.skills.workflowCopilot.appliedStateCurrentHint')
              : t('settings.skills.workflowCopilot.appliedStateStaleHint')}
          </div>
          {currentProposalApplyState === 'applied_current' ? (
            <div className="mt-3">
              <button
                type="button"
                onClick={() => void onUndoAppliedProposal()}
                className="inline-flex items-center gap-2 rounded-xl border border-emerald-300 bg-white px-3 py-2 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
              >
                {t('settings.skills.workflowCopilot.undoApplied')}
              </button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  ) : null

  return (
    <div className={wrapperClassName} style={wrapperStyle}>
      <WorkflowEditorSurfaceShell
        size={isSplitLayout ? 'full' : 'wide'}
        density="compact"
        fluid
        icon={<Sparkles className="w-4 h-4" />}
        title={t('settings.skills.workflowCopilot.title')}
        subtitle={isSplitLayout ? t('settings.skills.workflowCopilot.splitLayoutHint') : (title || modeLabel(mode, t))}
        onClose={onClose}
        bodyClassName={`min-h-0 flex-1 overflow-auto bg-slate-50/70 ${isSplitLayout ? 'px-4 py-3.5' : 'px-3.5 py-3.5'}`}
        footer={panelFooter}
        footerClassName="pt-2.5"
      >

        {!isSplitLayout && (
          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize Copilot panel"
            onPointerDown={handleResizeStart}
            className="shrink-0 cursor-row-resize select-none border-b bg-slate-50/80 px-4 py-2"
          >
            <div className="mx-auto h-1.5 w-14 rounded-full bg-slate-300" />
          </div>
        )}

        <div className="space-y-3">
          {proposalSummaryCard}

          {messages.length === 0 ? (
            <div className="rounded-[22px] border border-dashed bg-white/70 p-4 text-[13px] leading-6 text-muted-foreground">
              {t('settings.skills.workflowCopilot.emptyState')}
            </div>
          ) : messages.map((message) => {
            if (message.role === 'user' && !message.content.trim()) {
              return null
            }
            return (
              <div
                key={message.id}
                className={message.role === 'user' ? 'flex justify-end' : 'flex justify-start'}
              >
                <div className={`max-w-[90%] rounded-[18px] px-3.5 py-2.5 shadow-sm ${message.role === 'user'
                  ? 'bg-primary text-primary-foreground rounded-tr-[6px]'
                  : 'border bg-white rounded-tl-[6px] text-slate-700'
                }`}>
                  <div className="mb-1 flex items-center gap-1.5 text-[10px] opacity-80">
                    {message.role === 'assistant' ? <Bot className="w-3.5 h-3.5" /> : <Wand2 className="w-3.5 h-3.5" />}
                    <span>{message.role === 'assistant' ? 'Copilot' : t('common.you', { defaultValue: 'You' })}</span>
                    {message.status === 'pending' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  </div>
                  <div className="whitespace-pre-wrap break-words text-[13px] leading-6">
                    {message.status === 'pending' && !message.content
                      ? t('settings.skills.workflowCopilot.generating')
                      : message.content}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </WorkflowEditorSurfaceShell>
    </div>
  )
}

export type { WorkflowCopilotLaunchContext }
