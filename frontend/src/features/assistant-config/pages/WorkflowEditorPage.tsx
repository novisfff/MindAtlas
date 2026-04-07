import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { ArrowLeft, Save, Undo2, Redo2, Loader2, LayoutTemplate, Play, ListChecks, AlertCircle, Send, History, SlidersHorizontal, Sparkles, Wrench } from 'lucide-react'
import { toast } from 'sonner'
import { isApiError } from '@/lib/api/client'
import {
  clearWorkflowVersions,
  deleteWorkflowVersion,
  getWorkflow,
  listWorkflowVersions,
  publishWorkflow,
  rollbackWorkflowVersion,
  saveWorkflowById,
  validateWorkflowById,
} from '../api/workflows'
import type {
  NodeConfig,
  NodeType,
  WorkflowCopilotMode,
  WorkflowCopilotProposal,
  WorkflowCopilotSelection,
  WorkflowCopilotTestRunContext,
  WorkflowCopilotValidationContext,
  WorkflowInput,
} from '../api/workflow'
import { getSystemToolDefinitions, getToolsWithParams } from '../api/tools'
import { useWorkflowEditorStore } from '../stores/workflow-editor-store'
import { FlowCanvas } from '../components/workflow/FlowCanvas'
import { NodePalette } from '../components/workflow/NodePalette'
import {
  PropertyPanel,
  getPropertyPanelSelectionTargetKey,
  resolveSelectionContextFromTarget,
  type PropertyPanelSelectionTarget,
} from '../components/workflow/PropertyPanel'
import { WorkflowCopilotPanel, type WorkflowCopilotLaunchContext } from '../components/workflow/WorkflowCopilotPanel'
import { WorkflowReadonlyCanvas } from '../components/workflow/WorkflowReadonlyCanvas'
import { WorkflowTestRunPanel } from '../components/workflow/WorkflowTestRunPanel'
import { WorkflowValidationChecklistPanel } from '../components/workflow/WorkflowValidationChecklistPanel'
import { WorkflowEnvVarPanel } from '../components/workflow/WorkflowEnvVarPanel'
import { WorkflowEditorSurfaceShell } from '../components/workflow/WorkflowEditorSurfaceShell'
import { WorkflowEditorSurfaceRail, type WorkflowEditorSurfaceRailItem } from '../components/workflow/WorkflowEditorSurfaceRail'
import { serializeToWorkflowInput, deserializeFromWorkflow, deserializeFromWorkflowInput } from '../components/workflow/serialization'
import { PublishVersionDialog } from '../components/versioning/PublishVersionDialog'
import { TargetVersionPanel } from '../components/versioning/TargetVersionPanel'
import {
  buildWorkflowDraftHash,
  buildValidationSignature,
  computeDeadEndWarnings,
  normalizeValidationIssues,
  type WorkflowValidationIssue,
} from '../components/workflow/workflowValidation'
import type { WorkflowToolDefinition } from '../components/workflow/types'
import { autoLayoutWorkflowWithSubflows } from '../components/workflow/autoLayout'
import { NODE_CATALOG_ITEMS } from '../components/workflow/nodeCatalog'
import { normalizeStartNodeConfig } from '../components/workflow/startNodeConfig'
import { getStartNodeFromNodes, getWorkflowEnvVarsFromNodes, toStartConfigWithEnvVars } from '../components/workflow/workflowEnvVars'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '../../../components/ui/hover-card'

import '@xyflow/react/dist/style.css'

type CopilotPreviewMode = 'current' | 'proposed'
type WorkflowEditorSurfaceId = 'copilot' | 'testRun' | 'validation' | 'versionHistory' | 'envVars'
type WorkflowEditorDialogId = 'publish' | 'envVarEdit' | 'confirm'
type WorkbenchPropertyTarget = PropertyPanelSelectionTarget
type WorkflowWorkbenchTabKey = string
type WorkbenchTabRecord =
  | {
      key: WorkflowWorkbenchTabKey
      kind: 'surface'
      surfaceId: WorkflowEditorSurfaceId
    }
  | {
      key: WorkflowWorkbenchTabKey
      kind: 'property'
      target: WorkbenchPropertyTarget
    }

type MaterializedCopilotWorkflow = {
  workflow: WorkflowInput
  nodes: ReturnType<typeof deserializeFromWorkflowInput>['nodes']
  edges: ReturnType<typeof deserializeFromWorkflowInput>['edges']
  viewport?: ReturnType<typeof deserializeFromWorkflowInput>['viewport']
}

const NODE_TYPE_ICON_MAP = Object.fromEntries(
  NODE_CATALOG_ITEMS.map((item) => [item.type, item.icon]),
) as Partial<Record<NodeType, typeof Play>>

function buildCopilotProposalKey(proposal: WorkflowCopilotProposal): string {
  return `${proposal.baseDraftHash}:${proposal.proposedDraftHash}`
}

function shouldNormalizeCopilotLayout(proposal: WorkflowCopilotProposal): boolean {
  if (proposal.layoutRecommendation === 'autolayout') return true
  return proposal.operations.some((operation) => (
    operation.type === 'add_node' ||
    operation.type === 'remove_node' ||
    operation.type === 'add_edge' ||
    operation.type === 'remove_edge'
  ))
}

function materializeCopilotWorkflow(proposal: WorkflowCopilotProposal): MaterializedCopilotWorkflow {
  const restored = deserializeFromWorkflowInput(proposal.proposedWorkflow)
  const nodes = shouldNormalizeCopilotLayout(proposal)
    ? autoLayoutWorkflowWithSubflows(restored.nodes, restored.edges)
    : restored.nodes

  return {
    workflow: serializeToWorkflowInput(nodes, restored.edges, restored.viewport),
    nodes,
    edges: restored.edges,
    viewport: restored.viewport,
  }
}

function getSurfaceTabKey(surfaceId: WorkflowEditorSurfaceId): WorkflowWorkbenchTabKey {
  return `surface:${surfaceId}`
}

function buildSurfaceTab(surfaceId: WorkflowEditorSurfaceId): WorkbenchTabRecord {
  return {
    key: getSurfaceTabKey(surfaceId),
    kind: 'surface',
    surfaceId,
  }
}

function buildPropertyTab(target: WorkbenchPropertyTarget): WorkbenchTabRecord {
  return {
    key: getPropertyPanelSelectionTargetKey(target),
    kind: 'property',
    target,
  }
}

function isSamePropertyTarget(a: WorkbenchPropertyTarget | null, b: WorkbenchPropertyTarget | null): boolean {
  if (!a || !b) return false
  if (a.kind !== b.kind) return false
  if (a.kind === 'main' && b.kind === 'main') {
    return a.nodeId === b.nodeId
  }
  if (a.kind === 'subflow' && b.kind === 'subflow') {
    return a.containerId === b.containerId && a.nodeId === b.nodeId
  }
  return false
}

function getCurrentSelectionPropertyTarget(params: {
  selectedNodeId: string | null
  selectedEdgeId: string | null
  selectedSubflowContainerId: string | null
  selectedSubflowNodeId: string | null
  selectedSubflowEdgeId: string | null
}): WorkbenchPropertyTarget | null {
  if (params.selectedSubflowContainerId && params.selectedSubflowNodeId) {
    return {
      kind: 'subflow',
      containerId: params.selectedSubflowContainerId,
      nodeId: params.selectedSubflowNodeId,
    }
  }
  if (params.selectedEdgeId || params.selectedSubflowEdgeId) return null
  if (!params.selectedNodeId) return null
  return {
    kind: 'main',
    nodeId: params.selectedNodeId,
  }
}

function canAppendCopilotSelection(
  current: WorkflowCopilotSelection | undefined,
  incoming: WorkflowCopilotSelection | undefined,
): boolean {
  if (!current || !incoming) return false
  if (current.scope !== incoming.scope) return false
  return (current.containerId ?? null) === (incoming.containerId ?? null)
}

function mergeCopilotSelection(
  current: WorkflowCopilotSelection,
  incoming: WorkflowCopilotSelection,
): WorkflowCopilotSelection {
  const nextNodeIds = Array.from(new Set([
    ...current.nodeIds.map((item) => String(item || '').trim()).filter(Boolean),
    ...incoming.nodeIds.map((item) => String(item || '').trim()).filter(Boolean),
  ]))
  const nextEdgeIds = Array.from(new Set([
    ...current.edgeIds.map((item) => String(item || '').trim()).filter(Boolean),
    ...incoming.edgeIds.map((item) => String(item || '').trim()).filter(Boolean),
  ]))
  return {
    scope: current.scope,
    nodeIds: nextNodeIds,
    edgeIds: nextEdgeIds,
    containerId: incoming.containerId ?? current.containerId ?? null,
  }
}

function getNodeTypeWorkbenchIcon(nodeType: NodeType): typeof Play {
  if (nodeType === 'start') return Play
  if (nodeType === 'tool') return Wrench
  return NODE_TYPE_ICON_MAP[nodeType] ?? Play
}

function getFallbackWorkbenchTabKey(
  remainingTabs: WorkbenchTabRecord[],
  activationHistory: WorkflowWorkbenchTabKey[],
  closingKey?: WorkflowWorkbenchTabKey | null,
): WorkflowWorkbenchTabKey | null {
  return [...activationHistory]
    .reverse()
    .find((key) => key !== closingKey && remainingTabs.some((tab) => tab.key === key))
    ?? remainingTabs[remainingTabs.length - 1]?.key
    ?? null
}

function getWorkbenchPanelWidthClass(surface: WorkflowEditorSurfaceId | 'property' | null): string {
  switch (surface) {
    case 'property':
    case 'validation':
      return 'w-[420px] max-w-[calc(100vw-2rem)]'
    case 'versionHistory':
    case 'envVars':
      return 'w-[460px] max-w-[calc(100vw-2rem)]'
    case 'testRun':
      return 'w-[520px] max-w-[calc(100vw-2rem)]'
    case 'copilot':
      return 'w-[540px] max-w-[calc(100vw-2rem)]'
    default:
      return 'w-[420px] max-w-[calc(100vw-2rem)]'
  }
}

export default function WorkflowEditorPage() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const qc = useQueryClient()
  const initialized = useRef(false)

  const store = useWorkflowEditorStore()
  const [openWorkbenchTabs, setOpenWorkbenchTabs] = useState<WorkbenchTabRecord[]>([])
  const [activeWorkbenchTabKey, setActiveWorkbenchTabKey] = useState<WorkflowWorkbenchTabKey | null>(null)
  const [activationHistory, setActivationHistory] = useState<WorkflowWorkbenchTabKey[]>([])
  const [activeDialog, setActiveDialog] = useState<WorkflowEditorDialogId | null>(null)
  const [floatingUiEpoch, setFloatingUiEpoch] = useState(0)
  const [validationHoverOpen, setValidationHoverOpen] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [validationErrors, setValidationErrors] = useState<WorkflowValidationIssue[]>([])
  const [validationWarnings, setValidationWarnings] = useState<WorkflowValidationIssue[]>([])
  const [validationRequestError, setValidationRequestError] = useState<string | null>(null)
  const [lastValidatedAt, setLastValidatedAt] = useState<number | null>(null)
  const [workflowDescriptionDraft, setWorkflowDescriptionDraft] = useState('')
  const [copilotSessionRetained, setCopilotSessionRetained] = useState(false)
  const [copilotLaunchContext, setCopilotLaunchContext] = useState<WorkflowCopilotLaunchContext | null>(null)
  const [activeCopilotProposal, setActiveCopilotProposal] = useState<WorkflowCopilotProposal | null>(null)
  const [copilotPreviewMode, setCopilotPreviewMode] = useState<CopilotPreviewMode>('proposed')
  const [copilotPreviewVisible, setCopilotPreviewVisible] = useState(false)
  const [isApplyingCopilotProposal, setIsApplyingCopilotProposal] = useState(false)
  const [lastAppliedCopilot, setLastAppliedCopilot] = useState<{
    proposalKey: string
    appliedDraftHash: string
  } | null>(null)
  const validationSeqRef = useRef(0)
  const debounceTimerRef = useRef<number | null>(null)
  const latestSignatureRef = useRef('')
  const workflowSnapshotRef = useRef<WorkflowInput | null>(null)
  const copilotLaunchNonceRef = useRef(0)
  const lastSelectionTargetKeyRef = useRef<string | null>(null)

  const { data: workflowEntity, isLoading } = useQuery({
    queryKey: ['assistant-workflow', workflowId],
    queryFn: () => getWorkflow(workflowId!),
    enabled: !!workflowId,
  })
  const {
    data: workflowVersions,
    isFetching: versionsLoading,
    error: versionsError,
    refetch: refetchVersions,
  } = useQuery({
    queryKey: ['assistant-workflow-versions', workflowId],
    queryFn: () => listWorkflowVersions(workflowId!),
    enabled: !!workflowId && activeWorkbenchTabKey === getSurfaceTabKey('versionHistory'),
  })
  const { data: systemToolDefs = [] } = useQuery({
    queryKey: ['assistant-system-tool-definitions-workflow', i18n.language],
    queryFn: () => getSystemToolDefinitions({ includeDisabled: false, includeSchema: false }),
  })
  const { data: customTools = [] } = useQuery({
    queryKey: ['assistant-tools-workflow'],
    queryFn: () => getToolsWithParams({ includeDisabled: false }),
  })

  const workflowTools = useMemo<WorkflowToolDefinition[]>(() => {
    const merged = new Map<string, WorkflowToolDefinition>()

    systemToolDefs.forEach((tool) => {
      if (!tool.enabled) return
      merged.set(tool.name, {
        name: tool.name,
        displayName: tool.displayName || tool.name,
        description: tool.displayDescription || tool.description,
        inputParams: tool.inputParams ?? [],
        outputParams: tool.outputParams ?? [],
      })
    })

    customTools.forEach((tool) => {
      merged.set(tool.name, {
        name: tool.name,
        displayName: tool.name,
        description: tool.description,
        inputParams: tool.inputParams ?? [],
        outputParams: tool.outputParams ?? [],
      })
    })

    return Array.from(merged.values()).sort((a, b) =>
      (a.displayName ?? a.name).localeCompare(b.displayName ?? b.name),
    )
  }, [customTools, systemToolDefs])

  const workflowInput = useMemo(
    () => serializeToWorkflowInput(store.nodes, store.edges, store.viewport),
    [store.edges, store.nodes, store.viewport],
  )
  const currentDraftHash = useMemo(
    () => buildWorkflowDraftHash(workflowInput),
    [workflowInput],
  )
  const defaultPublishVersionName = useMemo(
    () => new Date().toLocaleString(),
    [activeDialog],
  )
  const validationSignature = useMemo(
    () => buildValidationSignature(workflowInput),
    [workflowInput],
  )
  const startInputMode = useMemo(() => {
    const startNode = store.nodes.find((node) => node.data.nodeType === 'start')
    return normalizeStartNodeConfig(startNode?.data.config ?? null).inputMode
  }, [store.nodes])
  const currentSelectionTarget = useMemo(
    () => getCurrentSelectionPropertyTarget({
      selectedNodeId: store.selectedNodeId,
      selectedEdgeId: store.selectedEdgeId,
      selectedSubflowContainerId: store.selectedSubflowContainerId,
      selectedSubflowNodeId: store.selectedSubflowNodeId,
      selectedSubflowEdgeId: store.selectedSubflowEdgeId,
    }),
    [
      store.selectedEdgeId,
      store.selectedNodeId,
      store.selectedSubflowContainerId,
      store.selectedSubflowEdgeId,
      store.selectedSubflowNodeId,
    ],
  )
  const workflowEnvVars = useMemo(
    () => getWorkflowEnvVarsFromNodes(store.nodes),
    [store.nodes],
  )
  const activeWorkbenchTab = useMemo(
    () => openWorkbenchTabs.find((tab) => tab.key === activeWorkbenchTabKey) ?? null,
    [activeWorkbenchTabKey, openWorkbenchTabs],
  )
  const activePropertyTarget = activeWorkbenchTab?.kind === 'property' ? activeWorkbenchTab.target : null
  const activeSurface = activeWorkbenchTab?.kind === 'surface' ? activeWorkbenchTab.surfaceId : null
  const visibleSurface: WorkflowEditorSurfaceId | 'property' | null = activeWorkbenchTab
    ? (activeWorkbenchTab.kind === 'surface' ? activeWorkbenchTab.surfaceId : 'property')
    : null
  const hasUnsavedChanges = useMemo(
    () => store.isDirty || workflowDescriptionDraft !== (workflowEntity?.description ?? ''),
    [store.isDirty, workflowDescriptionDraft, workflowEntity?.description],
  )
  const testRunPanelOpen = activeSurface === 'testRun'
  const validationPanelOpen = activeSurface === 'validation'
  const versionPanelOpen = activeSurface === 'versionHistory'
  const envPanelOpen = activeSurface === 'envVars'
  const copilotOpen = activeSurface === 'copilot'
  const isSystemWorkflow = Boolean(workflowEntity?.isSystem)
  const editorToolbarLocked = activeDialog !== null || (copilotOpen && copilotPreviewVisible)
  const editorMutationLocked = editorToolbarLocked || isSystemWorkflow
  const activeCopilotProposalKey = useMemo(
    () => activeCopilotProposal ? buildCopilotProposalKey(activeCopilotProposal) : null,
    [activeCopilotProposal],
  )
  const materializedCopilotWorkflow = useMemo(
    () => activeCopilotProposal ? materializeCopilotWorkflow(activeCopilotProposal) : null,
    [activeCopilotProposal],
  )
  const currentReadonlyWorkflow = useMemo(
    () => deserializeFromWorkflowInput(workflowInput),
    [workflowInput],
  )
  const previewReadonlyWorkflow = useMemo(
    () => copilotPreviewMode === 'current' || !materializedCopilotWorkflow
      ? currentReadonlyWorkflow
      : {
          nodes: materializedCopilotWorkflow.nodes,
          edges: materializedCopilotWorkflow.edges,
          viewport: materializedCopilotWorkflow.viewport,
        },
    [copilotPreviewMode, currentReadonlyWorkflow, materializedCopilotWorkflow],
  )
  const currentProposalApplyState = useMemo<'idle' | 'applied_current' | 'applied_stale'>(() => {
    if (!activeCopilotProposalKey || !lastAppliedCopilot || activeCopilotProposalKey !== lastAppliedCopilot.proposalKey) {
      return 'idle'
    }
    return currentDraftHash === lastAppliedCopilot.appliedDraftHash ? 'applied_current' : 'applied_stale'
  }, [activeCopilotProposalKey, currentDraftHash, lastAppliedCopilot])
  const showProposedPreview = Boolean(activeCopilotProposal && copilotPreviewVisible)
  const showCopilotPreviewPane = Boolean(activeSurface === 'copilot' && activeCopilotProposal && copilotPreviewVisible)
  const effectivePreviewMode: CopilotPreviewMode = showProposedPreview ? copilotPreviewMode : 'current'
  const effectivePreviewWorkflow = useMemo(
    () => effectivePreviewMode === 'current' ? currentReadonlyWorkflow : previewReadonlyWorkflow,
    [currentReadonlyWorkflow, effectivePreviewMode, previewReadonlyWorkflow],
  )
  const workbenchTabItems = useMemo<Array<WorkflowEditorSurfaceRailItem<WorkflowWorkbenchTabKey>>>(() => (
    openWorkbenchTabs.flatMap((tab) => {
      if (tab.kind === 'surface') {
        const surfaceMeta: Record<WorkflowEditorSurfaceId, { label: string; icon: ReactNode }> = {
          copilot: {
            label: t('settings.skills.workflowCopilot.title'),
            icon: <Sparkles className="h-4 w-4" />,
          },
          testRun: {
            label: t('settings.skills.workflowActions.testRun'),
            icon: <Play className="h-4 w-4" />,
          },
          validation: {
            label: t('settings.skills.workflowValidationChecklistTitle'),
            icon: <ListChecks className="h-4 w-4" />,
          },
          versionHistory: {
            label: t('settings.skills.workflowActions.versionHistory'),
            icon: <History className="h-4 w-4" />,
          },
          envVars: {
            label: t('settings.skills.workflowActions.env'),
            icon: <SlidersHorizontal className="h-4 w-4" />,
          },
        }
        const meta = surfaceMeta[tab.surfaceId]
        return [{ id: tab.key, label: meta.label, icon: meta.icon }]
      }

      const context = resolveSelectionContextFromTarget(store.nodes, tab.target)
      if (!context) return []

      const nodeType = context.mode === 'main' ? context.node.data.nodeType : context.node.nodeType
      const Icon = getNodeTypeWorkbenchIcon(nodeType)
      const label = context.mode === 'main'
        ? (String(context.node.data.label ?? '').trim() || context.node.id)
        : `${String(context.containerNode.data.label ?? '').trim() || context.containerNode.id} / ${String(context.node.label ?? '').trim() || context.node.nodeId}`

      return [{
        id: tab.key,
        label,
        icon: <Icon className="h-4 w-4" />,
      }]
    })
  ), [openWorkbenchTabs, store.nodes, t])
  const canShowSurfaceRail = workbenchTabItems.length > 1
  const publishDialogOpen = activeDialog === 'publish'
  const rightWorkbenchWidthClass = useMemo(
    () => getWorkbenchPanelWidthClass(visibleSurface),
    [visibleSurface],
  )





  // Load workflow data into store on first fetch
  useEffect(() => {
    if (workflowEntity && !initialized.current) {
      initialized.current = true
      const { nodes, edges, viewport } = deserializeFromWorkflow(workflowEntity)
      store.loadWorkflow(nodes, edges, viewport)
      setWorkflowDescriptionDraft(workflowEntity.description ?? '')
    }
  }, [workflowEntity])

  useEffect(() => {
    if (!workflowEntity) return
    if (store.isDirty) return
    setWorkflowDescriptionDraft(workflowEntity.description ?? '')
  }, [workflowEntity?.id, workflowEntity?.description, store.isDirty])

  useEffect(() => {
    setValidationErrors([])
    setValidationWarnings([])
    setValidationRequestError(null)
    setLastValidatedAt(null)
    setIsValidating(false)
    validationSeqRef.current = 0
    latestSignatureRef.current = ''
    workflowSnapshotRef.current = null
    setOpenWorkbenchTabs([])
    setActiveWorkbenchTabKey(null)
    setActivationHistory([])
    setActiveDialog(null)
    setFloatingUiEpoch(0)
    setValidationHoverOpen(false)
    setCopilotSessionRetained(false)
    setActiveCopilotProposal(null)
    setCopilotPreviewVisible(false)
    setCopilotPreviewMode('proposed')
    setLastAppliedCopilot(null)
    setIsApplyingCopilotProposal(false)
    lastSelectionTargetKeyRef.current = null
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }
  }, [workflowId])

  const runWorkflowValidation = useCallback(
    async (input: WorkflowInput, signature: string, force = false) => {
      if (!workflowId) return
      if (!initialized.current) return
      if (!force && signature === latestSignatureRef.current) return

      if (debounceTimerRef.current) {
        window.clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }

      const seq = validationSeqRef.current + 1
      validationSeqRef.current = seq
      workflowSnapshotRef.current = input

      setIsValidating(true)
      setValidationRequestError(null)

      try {
        const validation = await validateWorkflowById(workflowId, input)
        if (seq !== validationSeqRef.current) return

        const warningMessage = t('settings.skills.workflowValidationDeadEndWarning')
        const reachabilityWarnings = computeDeadEndWarnings(input, warningMessage)
        const normalized = normalizeValidationIssues(validation.errors, reachabilityWarnings)
        setValidationErrors(normalized.errors)
        setValidationWarnings(normalized.warnings)
        setLastValidatedAt(Date.now())
        setValidationRequestError(null)
        latestSignatureRef.current = signature
      } catch (err) {
        if (seq !== validationSeqRef.current) return
        const message = err instanceof Error
          ? err.message
          : t('settings.skills.workflowValidationChecklistRequestFailed')
        setValidationRequestError(message)
      } finally {
        if (seq === validationSeqRef.current) {
          setIsValidating(false)
        }
      }
    },
    [workflowId, t],
  )

  useEffect(() => {
    if (!workflowId) return
    if (!initialized.current) return
    if (validationSignature === latestSignatureRef.current) return
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }
    debounceTimerRef.current = window.setTimeout(() => {
      void runWorkflowValidation(workflowInput, validationSignature)
    }, 500)
    return () => {
      if (debounceTimerRef.current) {
        window.clearTimeout(debounceTimerRef.current)
        debounceTimerRef.current = null
      }
    }
  }, [workflowId, runWorkflowValidation, validationSignature, workflowInput])

  useEffect(() => {
    if (!copilotPreviewVisible) return
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur()
    }
  }, [copilotPreviewVisible])

  const closeFloatingUi = useCallback(() => {
    setValidationHoverOpen(false)
    setFloatingUiEpoch((current) => current + 1)
  }, [])

  const syncSelectionToPropertyTarget = useCallback((target: WorkbenchPropertyTarget) => {
    if (target.kind === 'subflow') {
      const alreadySelected = (
        store.selectedSubflowContainerId === target.containerId &&
        store.selectedSubflowNodeId === target.nodeId &&
        !store.selectedSubflowEdgeId
      )
      if (!alreadySelected) {
        store.setSelectedSubflowSelection(target.containerId, target.nodeId, null)
      }
      store.requestFocusNode(target.containerId)
      return
    }

    const alreadySelected = (
      store.selectedNodeId === target.nodeId &&
      !store.selectedEdgeId &&
      !store.selectedSubflowNodeId &&
      !store.selectedSubflowEdgeId
    )
    if (!alreadySelected) {
      store.setSelectedNodeId(target.nodeId)
    }
    store.requestFocusNode(target.nodeId)
  }, [store])

  const activateWorkbenchTab = useCallback((
    key: WorkflowWorkbenchTabKey,
    options?: { syncSelection?: boolean },
  ) => {
    if (activeWorkbenchTabKey === key) {
      if (options?.syncSelection) {
        const currentTab = openWorkbenchTabs.find((tab) => tab.key === key)
        if (currentTab?.kind === 'property') {
          syncSelectionToPropertyTarget(currentTab.target)
        }
      }
      return
    }

    closeFloatingUi()
    if (activeSurface === 'copilot') {
      setCopilotPreviewVisible(false)
    }
    setActiveWorkbenchTabKey(key)
    setActivationHistory((current) => [...current.filter((item) => item !== key), key])

    if (options?.syncSelection) {
      const nextTab = openWorkbenchTabs.find((tab) => tab.key === key)
      if (nextTab?.kind === 'property') {
        syncSelectionToPropertyTarget(nextTab.target)
      }
    }
  }, [activeSurface, activeWorkbenchTabKey, closeFloatingUi, openWorkbenchTabs, syncSelectionToPropertyTarget])

  const openWorkbenchTab = useCallback((
    tab: WorkbenchTabRecord,
    options?: { syncSelection?: boolean },
  ) => {
    closeFloatingUi()
    if (activeSurface === 'copilot' && activeWorkbenchTabKey !== tab.key) {
      setCopilotPreviewVisible(false)
    }

    setOpenWorkbenchTabs((current) => current.some((item) => item.key === tab.key) ? current : [...current, tab])
    setActiveWorkbenchTabKey(tab.key)
    setActivationHistory((current) => [...current.filter((item) => item !== tab.key), tab.key])

    if (options?.syncSelection && tab.kind === 'property') {
      syncSelectionToPropertyTarget(tab.target)
    }
  }, [activeSurface, activeWorkbenchTabKey, closeFloatingUi, syncSelectionToPropertyTarget])

  const openSurfaceTab = useCallback((surface: WorkflowEditorSurfaceId) => {
    openWorkbenchTab(buildSurfaceTab(surface))
  }, [openWorkbenchTab])

  const openPropertyTab = useCallback((
    target: WorkbenchPropertyTarget,
    options?: { syncSelection?: boolean },
  ) => {
    openWorkbenchTab(buildPropertyTab(target), options)
  }, [openWorkbenchTab])

  const closeWorkbenchTab = useCallback((key: WorkflowWorkbenchTabKey) => {
    const closingTab = openWorkbenchTabs.find((tab) => tab.key === key)
    if (!closingTab) return

    closeFloatingUi()

    if (closingTab.kind === 'surface' && closingTab.surfaceId === 'copilot') {
      setCopilotPreviewVisible(false)
    }

    if (closingTab.kind === 'property') {
      if (closingTab.target.kind === 'subflow') {
        const matchesCurrentSelection = (
          store.selectedSubflowContainerId === closingTab.target.containerId &&
          store.selectedSubflowNodeId === closingTab.target.nodeId
        )
        if (matchesCurrentSelection) {
          store.clearSelectedSubflowSelection()
        }
      } else {
        const matchesCurrentSelection = (
          store.selectedNodeId === closingTab.target.nodeId &&
          !store.selectedSubflowNodeId
        )
        if (matchesCurrentSelection) {
          store.setSelectedNodeId(null)
        }
      }
    }

    const remainingTabs = openWorkbenchTabs.filter((tab) => tab.key !== key)
    const fallbackKey = activeWorkbenchTabKey === key
      ? getFallbackWorkbenchTabKey(remainingTabs, activationHistory, key)
      : activeWorkbenchTabKey

    setOpenWorkbenchTabs(remainingTabs)
    setActivationHistory((current) => current.filter((item) => item !== key))
    setActiveWorkbenchTabKey(fallbackKey)

    if (fallbackKey) {
      const fallbackTab = remainingTabs.find((tab) => tab.key === fallbackKey)
      if (fallbackTab?.kind === 'property') {
        syncSelectionToPropertyTarget(fallbackTab.target)
      }
    }
  }, [
    activeWorkbenchTabKey,
    activationHistory,
    closeFloatingUi,
    openWorkbenchTabs,
    store,
    syncSelectionToPropertyTarget,
  ])

  const closeActiveSurface = useCallback(() => {
    if (!activeWorkbenchTabKey) return
    closeWorkbenchTab(activeWorkbenchTabKey)
  }, [activeWorkbenchTabKey, closeWorkbenchTab])

  const toggleSurface = useCallback((surface: WorkflowEditorSurfaceId) => {
    const tabKey = getSurfaceTabKey(surface)
    if (activeSurface === surface && activeWorkbenchTabKey === tabKey) {
      closeWorkbenchTab(tabKey)
      return
    }
    openSurfaceTab(surface)
  }, [activeSurface, activeWorkbenchTabKey, closeWorkbenchTab, openSurfaceTab])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (event.defaultPrevented) return
      if (!activeWorkbenchTabKey) return
      if (document.querySelector('[data-ui-modal="true"]')) return
      const target = event.target
      if (target instanceof HTMLElement) {
        const isEditable = target.isContentEditable || Boolean(target.closest('input, textarea, select, [contenteditable], [role="textbox"]'))
        if (isEditable) return
      }
      event.preventDefault()
      closeActiveSurface()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [activeWorkbenchTabKey, closeActiveSurface])

  useEffect(() => {
    const nextKey = currentSelectionTarget ? getPropertyPanelSelectionTargetKey(currentSelectionTarget) : null
    if (lastSelectionTargetKeyRef.current === nextKey) return
    lastSelectionTargetKeyRef.current = nextKey
    if (!currentSelectionTarget) return
    const isAlreadyActive = activeWorkbenchTab?.kind === 'property' && isSamePropertyTarget(activeWorkbenchTab.target, currentSelectionTarget)
    if (isAlreadyActive) return
    openPropertyTab(currentSelectionTarget)
  }, [activeWorkbenchTab, currentSelectionTarget, openPropertyTab])

  useEffect(() => {
    const invalidKeys = openWorkbenchTabs
      .filter((tab) => tab.kind === 'property' && !resolveSelectionContextFromTarget(store.nodes, tab.target))
      .map((tab) => tab.key)

    if (invalidKeys.length === 0) return

    const remainingTabs = openWorkbenchTabs.filter((tab) => !invalidKeys.includes(tab.key))
    const nextActiveKey = activeWorkbenchTabKey && !invalidKeys.includes(activeWorkbenchTabKey)
      ? activeWorkbenchTabKey
      : getFallbackWorkbenchTabKey(remainingTabs, activationHistory, activeWorkbenchTabKey)

    setOpenWorkbenchTabs(remainingTabs)
    setActivationHistory((current) => current.filter((key) => !invalidKeys.includes(key)))
    setActiveWorkbenchTabKey(nextActiveKey)

    if (nextActiveKey) {
      const fallbackTab = remainingTabs.find((tab) => tab.key === nextActiveKey)
      if (fallbackTab?.kind === 'property') {
        syncSelectionToPropertyTarget(fallbackTab.target)
      }
    }
  }, [
    activationHistory,
    activeWorkbenchTabKey,
    openWorkbenchTabs,
    store.nodes,
    syncSelectionToPropertyTarget,
  ])

  const handleValidateNow = useCallback(() => {
    toggleSurface('validation')
    void runWorkflowValidation(workflowInput, validationSignature, true)
  }, [runWorkflowValidation, toggleSurface, validationSignature, workflowInput])

  const handleValidationRefresh = useCallback(() => {
    void runWorkflowValidation(workflowInput, validationSignature, true)
  }, [runWorkflowValidation, validationSignature, workflowInput])

  const handleLocateValidationIssue = useCallback((issue: WorkflowValidationIssue) => {
    if (!issue.nodeId) return
    const exists = store.nodes.some((node) => node.id === issue.nodeId)
    if (!exists) return
    if (issue.subflowNodeId) {
      store.setSelectedSubflowSelection(issue.nodeId, issue.subflowNodeId, null)
      store.requestFocusNode(issue.nodeId)
      return
    }
    store.setSelectedNodeId(issue.nodeId)
    store.requestFocusNode(issue.nodeId)
  }, [store])

  const openCopilot = useCallback((payload: {
    mode: WorkflowCopilotMode
    title?: string
    instruction?: string
    selection?: WorkflowCopilotSelection
    validationContext?: WorkflowCopilotValidationContext
    testRunContext?: WorkflowCopilotTestRunContext
    restoreOnClose?: boolean
  }) => {
    const shouldAppendSelection = (
      payload.mode === 'edit_selection' &&
      copilotSessionRetained &&
      copilotLaunchContext?.mode === 'edit_selection' &&
      canAppendCopilotSelection(copilotLaunchContext.selection, payload.selection)
    )
    const nextSelection = shouldAppendSelection && copilotLaunchContext?.selection && payload.selection
      ? mergeCopilotSelection(copilotLaunchContext.selection, payload.selection)
      : payload.selection
    copilotLaunchNonceRef.current += 1
    setCopilotSessionRetained(true)
    setCopilotLaunchContext({
      nonce: copilotLaunchNonceRef.current,
      mode: payload.mode,
      title: shouldAppendSelection ? (copilotLaunchContext?.title ?? payload.title) : payload.title,
      instruction: payload.instruction,
      selection: nextSelection,
      appendSelection: shouldAppendSelection,
      validationContext: payload.validationContext,
      testRunContext: payload.testRunContext,
    })
    openSurfaceTab('copilot')
  }, [copilotLaunchContext, copilotSessionRetained, openSurfaceTab])

  const switchWorkbenchTab = useCallback((key: WorkflowWorkbenchTabKey) => {
    if (activeWorkbenchTabKey === key) return
    const targetTab = openWorkbenchTabs.find((tab) => tab.key === key)
    if (!targetTab) return
    activateWorkbenchTab(key, { syncSelection: targetTab.kind === 'property' })
  }, [activateWorkbenchTab, activeWorkbenchTabKey, openWorkbenchTabs])

  const handleApplyCopilotProposal = useCallback(async (proposal: WorkflowCopilotProposal) => {
    if (isApplyingCopilotProposal) return
    if (currentDraftHash !== proposal.baseDraftHash) {
      toast.error(t('settings.skills.workflowCopilot.hashConflict'))
      return
    }

    setIsApplyingCopilotProposal(true)
    try {
      const materialized = materializeCopilotWorkflow(proposal)
      store.replaceWorkflow(materialized.nodes, materialized.edges, materialized.viewport, { pushHistory: true })

      const focusNodeId = proposal.affectedNodeIds.find((nodeId) => materialized.nodes.some((node) => node.id === nodeId))
      if (focusNodeId) {
        store.requestFocusNode(focusNodeId)
      }

      const nextSignature = buildValidationSignature(materialized.workflow)
      await runWorkflowValidation(materialized.workflow, nextSignature, true)

      setLastAppliedCopilot({
        proposalKey: buildCopilotProposalKey(proposal),
        appliedDraftHash: buildWorkflowDraftHash(materialized.workflow),
      })
      setCopilotPreviewVisible(false)
      toast.success(t('settings.skills.workflowCopilot.applySuccess'))
    } finally {
      setIsApplyingCopilotProposal(false)
    }
  }, [currentDraftHash, isApplyingCopilotProposal, runWorkflowValidation, store, t])

  const handleUndoAppliedCopilot = useCallback(() => {
    if (!lastAppliedCopilot || currentDraftHash !== lastAppliedCopilot.appliedDraftHash) {
      toast.error(t('settings.skills.workflowCopilot.undoUnavailable'))
      return
    }
    store.undo()
    setCopilotPreviewVisible(false)
    toast.success(t('settings.skills.workflowCopilot.undoAppliedSuccess'))
  }, [currentDraftHash, lastAppliedCopilot, store, t])

  const handleCloseCopilot = useCallback(() => {
    closeActiveSurface()
  }, [closeActiveSurface])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const input = serializeToWorkflowInput(store.nodes, store.edges, store.viewport)
      const validation = await validateWorkflowById(workflowId!, input)
      if (!validation.valid) {
        const msg = validation.errors.map((e) => e.message).slice(0, 3).join('; ')
        throw new Error(msg || t('settings.skills.workflowValidationFailed'))
      }
      return saveWorkflowById(workflowId!, {
        workflow: input,
        description: workflowDescriptionDraft,
      })
    },
    onSuccess: () => {
      store.resetDirty()
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      toast.success(t('settings.skills.workflowSaved'))
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('settings.skills.workflowSaveError')
      toast.error(message)
    },
  })

  const publishMutation = useMutation({
    mutationFn: async (versionName: string) => {
      const input = serializeToWorkflowInput(store.nodes, store.edges, store.viewport)
      const validation = await validateWorkflowById(workflowId!, input)
      if (!validation.valid) {
        const msg = validation.errors.map((e) => e.message).slice(0, 3).join('; ')
        throw new Error(msg || t('settings.skills.workflowValidationFailed'))
      }
      return publishWorkflow(workflowId!, {
        workflow: input,
        description: workflowDescriptionDraft,
        versionName: versionName.trim() || undefined,
      })
    },
    onSuccess: () => {
      store.resetDirty()
      setActiveDialog(null)
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflows'] })
      qc.invalidateQueries({ queryKey: ['assistant-skills'] })
      toast.success(t('settings.skills.versioning.publishSuccess'))
    },
    onError: (err) => {
      const message = isApiError(err) && err.code === 42209
        ? t('settings.skills.versioning.publishBlockedByValidation')
        : (err instanceof Error ? err.message : t('settings.skills.workflowSaveError'))
      toast.error(message)
    },
  })

  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => rollbackWorkflowVersion(workflowId!, versionId),
    onSuccess: (payload) => {
      if (payload.workflow) {
        const restored = deserializeFromWorkflow({
          id: workflowId!,
          name: workflowEntity?.name ?? '',
          description: workflowEntity?.description ?? '',
          isSystem: Boolean(workflowEntity?.isSystem),
          enabled: true,
          workflowVersion: workflowEntity?.workflowVersion ?? 1,
          workflowViewport: payload.workflow.viewport ?? null,
          nodes: (payload.workflow.nodes ?? []) as any,
          edges: (payload.workflow.edges ?? []) as any,
          draftVersionId: payload.draftVersionId,
          publishedVersionId: payload.publishedVersionId,
          referencedSkillIds: workflowEntity?.referencedSkillIds ?? [],
          referenceCount: workflowEntity?.referenceCount ?? 0,
          createdAt: workflowEntity?.createdAt ?? new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        } as any)
        store.loadWorkflow(restored.nodes, restored.edges, restored.viewport)
        store.resetDirty()
        setOpenWorkbenchTabs([])
        setActiveWorkbenchTabKey(null)
        setActivationHistory([])
        setCopilotPreviewVisible(false)
        lastSelectionTargetKeyRef.current = null
      }
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      toast.success(t('settings.skills.versioning.restoreSuccess'))
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('messages.error')
      toast.error(message)
    },
  })

  const deleteVersionMutation = useMutation({
    mutationFn: async (versionId: string) => deleteWorkflowVersion(workflowId!, versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      toast.success(t('settings.skills.versioning.deleteSuccess'))
    },
    onError: (err) => {
      const message = isApiError(err) && err.code === 40941
        ? t('settings.skills.versioning.protectedVersionDeleteBlocked')
        : (err instanceof Error ? err.message : t('messages.error'))
      toast.error(message)
    },
  })

  const clearVersionsMutation = useMutation({
    mutationFn: async () => clearWorkflowVersions(workflowId!),
    onSuccess: (payload) => {
      qc.invalidateQueries({ queryKey: ['assistant-workflow', workflowId] })
      qc.invalidateQueries({ queryKey: ['assistant-workflow-versions', workflowId] })
      if (payload.deletedCount > 0) {
        toast.success(t('settings.skills.versioning.clearSuccess', { count: payload.deletedCount }))
      } else {
        toast(t('settings.skills.versioning.clearNoop'))
      }
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : t('messages.error')
      toast.error(message)
    },
  })

  const handleSave = useCallback(() => {
    if (isSystemWorkflow) return
    saveMutation.mutate()
  }, [isSystemWorkflow, saveMutation])

  const handlePublish = useCallback((versionName: string) => {
    if (isSystemWorkflow) return
    publishMutation.mutate(versionName)
  }, [isSystemWorkflow, publishMutation])

  const handleDeleteVersion = useCallback((versionId: string) => {
    if (!window.confirm(t('settings.skills.versioning.deleteConfirm'))) return
    deleteVersionMutation.mutate(versionId)
  }, [deleteVersionMutation, t])

  const handleClearVersions = useCallback(() => {
    clearVersionsMutation.mutate()
  }, [clearVersionsMutation])

  const handleWorkflowEnvVarsChange = useCallback((nextVars: typeof workflowEnvVars) => {
    if (isSystemWorkflow) return
    const startNode = getStartNodeFromNodes(store.nodes)
    if (!startNode) {
      toast.error(t('settings.skills.envVars.startNodeMissing'))
      return
    }
    const nextConfig = toStartConfigWithEnvVars(
      (startNode.data.config ?? null) as Record<string, unknown> | null,
      nextVars,
    )
    store.updateNodeConfig(startNode.id, nextConfig as NodeConfig, { pushHistory: true })
  }, [isSystemWorkflow, store, t, workflowEnvVars])

  const handleBack = useCallback(() => {
    if (hasUnsavedChanges && !window.confirm(t('settings.skills.unsavedChanges'))) return
    navigate('/settings/assistant-targets')
  }, [hasUnsavedChanges, navigate, t])

  const handleAutoLayout = useCallback(() => {
    if (isSystemWorkflow) return
    if (store.nodes.length <= 1) return
    const laidOut = autoLayoutWorkflowWithSubflows(store.nodes, store.edges)
    store.pushHistory()
    store.setNodes(laidOut)
  }, [isSystemWorkflow, store])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (copilotOpen && mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        return
      }
      if (mod && e.key === 's') {
        if (isSystemWorkflow) return
        e.preventDefault()
        handleSave()
      }
      if (mod && e.key === 'z' && !e.shiftKey) {
        if (isSystemWorkflow) return
        e.preventDefault()
        store.undo()
      }
      if (mod && e.key === 'z' && e.shiftKey) {
        if (isSystemWorkflow) return
        e.preventDefault()
        store.redo()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [copilotOpen, handleSave, isSystemWorkflow, store])

  // Warn on page unload
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasUnsavedChanges])

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        <span>{t('messages.loading')}</span>
      </div>
    )
  }

  return (
    <ReactFlowProvider>
      <div className="relative w-screen h-screen overflow-hidden bg-slate-50">
        <div className="absolute inset-0 z-0">
          <FlowCanvas
            tools={workflowTools}
            workflowDescription={workflowDescriptionDraft}
            readOnly={editorMutationLocked}
            floatingUiEpoch={floatingUiEpoch}
          />
        </div>

        <div className="absolute top-4 left-4 right-4 z-10 flex items-start justify-between pointer-events-none">
          <div className="pointer-events-auto flex items-center gap-3 rounded-2xl border border-white/50 bg-white/70 p-2 pr-4 shadow-sm backdrop-blur-md">
            <button
              onClick={handleBack}
              className="rounded-xl p-2 transition-colors hover:bg-white/60"
              title={t('settings.skills.workflowActions.back')}
            >
              <ArrowLeft className="h-4 w-4 text-foreground/80" />
            </button>
            <div className="flex flex-col">
              <h1 className="text-sm font-semibold leading-none">{workflowEntity?.name ?? ''}</h1>
              <span className="mt-0.5 text-[10px] text-muted-foreground">{t('settings.skills.workflowEditor')}</span>
            </div>
          </div>

          <div className="pointer-events-auto flex items-center gap-1.5 rounded-2xl border border-white/50 bg-white/70 p-2 shadow-sm backdrop-blur-md">
            <button
              onClick={() => store.undo()}
              disabled={!store.canUndo() || editorMutationLocked}
              className="rounded-xl p-2 transition-colors hover:bg-white/60 disabled:opacity-30"
              title={t('settings.skills.workflowActions.undo')}
            >
              <Undo2 className="h-4 w-4 text-foreground/80" />
            </button>
            <button
              onClick={() => store.redo()}
              disabled={!store.canRedo() || editorMutationLocked}
              className="rounded-xl p-2 transition-colors hover:bg-white/60 disabled:opacity-30"
              title={t('settings.skills.workflowActions.redo')}
            >
              <Redo2 className="h-4 w-4 text-foreground/80" />
            </button>
            <button
              onClick={handleAutoLayout}
              disabled={store.nodes.length <= 1 || editorMutationLocked}
              className="rounded-xl p-2 transition-colors hover:bg-white/60 disabled:opacity-30"
              title={t('settings.skills.workflowActions.autoLayout')}
            >
              <LayoutTemplate className="h-4 w-4 text-foreground/80" />
            </button>
            <button
              onClick={() => toggleSurface('testRun')}
              disabled={editorToolbarLocked}
              className={`
                flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium shadow-sm transition-all
                ${testRunPanelOpen
                  ? 'border-blue-200 bg-blue-100 text-blue-700 hover:bg-blue-200'
                  : 'border-slate-200 bg-white/90 text-slate-700 hover:border-slate-300 hover:bg-white'
                }
                disabled:cursor-not-allowed disabled:opacity-40
              `}
              title={t('settings.skills.workflowActions.testRun')}
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              {t('settings.skills.workflowActions.testRun')}
            </button>
            <HoverCard open={validationHoverOpen} onOpenChange={setValidationHoverOpen} openDelay={200} closeDelay={100}>
              <HoverCardTrigger asChild>
                <button
                  onClick={handleValidateNow}
                  disabled={editorToolbarLocked}
                  className={`
                    relative flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium shadow-sm transition-all
                    ${validationPanelOpen
                      ? 'border-blue-200 bg-blue-100 text-blue-700 hover:bg-blue-200'
                      : 'border-slate-200 bg-white/90 text-slate-700 hover:border-slate-300 hover:bg-white'
                    }
                    disabled:cursor-not-allowed disabled:opacity-40
                  `}
                >
                  <ListChecks className={`h-3.5 w-3.5 ${isValidating ? 'animate-pulse text-blue-600' : ''}`} />
                  {t('settings.skills.workflowActions.validate')}
                  {validationErrors.length > 0 ? (
                    <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
                    </span>
                  ) : null}
                  {!isValidating && lastValidatedAt && validationErrors.length === 0 ? (
                    <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-500" />
                    </span>
                  ) : null}
                </button>
              </HoverCardTrigger>
              <HoverCardContent side="bottom" align="end" className="w-60 p-3">
                <div
                  className="flex cursor-pointer flex-col gap-2"
                  onClick={() => openSurfaceTab('validation')}
                >
                  <div className="flex items-center gap-2">
                    {isValidating ? (
                      <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                    ) : validationErrors.length > 0 ? (
                      <AlertCircle className="h-4 w-4 text-red-600" />
                    ) : validationWarnings.length > 0 ? (
                      <AlertCircle className="h-4 w-4 text-amber-600" />
                    ) : lastValidatedAt ? (
                      <ListChecks className="h-4 w-4 text-green-600" />
                    ) : (
                      <ListChecks className="h-4 w-4 text-muted-foreground" />
                    )}
                    <span className={`text-sm font-medium ${
                      isValidating ? 'text-blue-700'
                        : validationErrors.length > 0 ? 'text-red-700'
                          : validationWarnings.length > 0 ? 'text-amber-700'
                            : lastValidatedAt ? 'text-green-700' : 'text-foreground'
                    }`}>
                      {isValidating ? t('settings.skills.workflowValidationPopup.running')
                        : validationErrors.length > 0 ? t('settings.skills.workflowValidationPopup.failed')
                          : validationWarnings.length > 0 ? t('settings.skills.workflowValidationPopup.warnings')
                            : lastValidatedAt ? t('settings.skills.workflowValidationPopup.success')
                              : t('settings.skills.workflowValidationPopup.ready')}
                    </span>
                  </div>

                  {(validationErrors.length > 0 || validationWarnings.length > 0) ? (
                    <div className="flex items-center gap-3 pl-6 text-xs">
                      {validationErrors.length > 0 ? (
                        <span className="font-medium text-red-600">
                          {validationErrors.length} {t('settings.skills.workflowValidationPopup.errors')}
                        </span>
                      ) : null}
                      {validationWarnings.length > 0 ? (
                        <span className="font-medium text-amber-600">
                          {validationWarnings.length} {t('settings.skills.workflowValidationPopup.warns')}
                        </span>
                      ) : null}
                    </div>
                  ) : null}

                  {lastValidatedAt ? (
                    <div className="mt-1 flex items-center justify-between border-t pt-2 text-[10px] text-muted-foreground">
                      <span>{t('settings.skills.workflowValidationPopup.lastValidated', { time: new Date(lastValidatedAt).toLocaleTimeString() })}</span>
                      <ArrowLeft className="h-3 w-3 rotate-180 opacity-50" />
                    </div>
                  ) : null}
                </div>
              </HoverCardContent>
            </HoverCard>
            <button
              onClick={() => {
                if (isSystemWorkflow) return
                if (copilotOpen) {
                  handleCloseCopilot()
                  return
                }
                if (copilotSessionRetained) {
                  openSurfaceTab('copilot')
                  return
                }
                openCopilot({
                  mode: 'generate',
                  title: t('settings.skills.workflowCopilot.title'),
                  instruction: '',
                })
              }}
              disabled={editorMutationLocked}
              className={`
                flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium shadow-sm transition-all
                ${copilotOpen
                  ? 'border-blue-200 bg-blue-100 text-blue-700 hover:bg-blue-200'
                  : 'border-slate-200 bg-white/90 text-slate-700 hover:border-slate-300 hover:bg-white'
                }
                disabled:cursor-not-allowed disabled:opacity-40
              `}
              title={t('settings.skills.workflowCopilot.title')}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {t('settings.skills.workflowCopilot.button')}
            </button>
            <div className="mx-1 h-5 w-px bg-border" />
            <button
              onClick={() => toggleSurface('versionHistory')}
              disabled={editorToolbarLocked}
              className={`
                flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium shadow-sm transition-all
                ${versionPanelOpen
                  ? 'border-blue-200 bg-blue-100 text-blue-700 hover:bg-blue-200'
                  : 'border-slate-200 bg-white/90 text-slate-700 hover:border-slate-300 hover:bg-white'
                }
                disabled:cursor-not-allowed disabled:opacity-40
              `}
              title={t('settings.skills.workflowActions.versionHistory')}
            >
              <History className="h-3.5 w-3.5" />
              {t('settings.skills.workflowActions.versionHistory')}
            </button>
            <button
              onClick={() => toggleSurface('envVars')}
              disabled={editorMutationLocked}
              className={`
                flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium shadow-sm transition-all
                ${envPanelOpen
                  ? 'border-blue-200 bg-blue-100 text-blue-700 hover:bg-blue-200'
                  : 'border-slate-200 bg-white/90 text-slate-700 hover:border-slate-300 hover:bg-white'
                }
                disabled:cursor-not-allowed disabled:opacity-40
              `}
              title={t('settings.skills.workflowActions.env')}
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              {t('settings.skills.workflowActions.env')}
            </button>
            <button
              onClick={() => {
                closeFloatingUi()
                setActiveDialog('publish')
              }}
              disabled={publishMutation.isPending || editorMutationLocked}
              className="flex items-center gap-2 rounded-xl border border-blue-700 bg-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 disabled:opacity-50"
              title={t('settings.skills.workflowActions.saveAndPublish')}
            >
              {publishMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              {t('settings.skills.workflowActions.saveAndPublish')}
            </button>
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !hasUnsavedChanges || editorMutationLocked}
              className={`
                flex items-center gap-2 rounded-xl border px-4 py-1.5 text-sm font-medium shadow-sm transition-all
                ${hasUnsavedChanges
                  ? 'border-primary bg-primary text-primary-foreground hover:bg-primary/90'
                  : 'border-slate-200 bg-white/50 text-muted-foreground hover:bg-white/60'
                }
              `}
            >
              {saveMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {t('settings.skills.workflowActions.save')}
            </button>
          </div>
        </div>

        {isSystemWorkflow ? (
          <div className="absolute left-1/2 top-[5rem] z-10 w-[min(960px,calc(100vw-2rem))] -translate-x-1/2 pointer-events-none">
            <div className="pointer-events-auto rounded-2xl border border-amber-200 bg-amber-50/95 px-4 py-3 shadow-sm backdrop-blur">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="space-y-1">
                  <p className="text-sm font-semibold text-amber-900">
                    {t('settings.skills.systemTargetReadonlyBannerTitle')}
                  </p>
                  <p className="text-sm leading-6 text-amber-800">
                    {t('settings.skills.systemWorkflowReadonlyDescription')}
                  </p>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <div className="absolute left-4 top-[4.75rem] bottom-24 z-10 flex w-fit min-h-0 flex-col justify-start pointer-events-none">
          <div className={`min-h-0 flex flex-col ${editorMutationLocked ? 'pointer-events-none opacity-60' : 'pointer-events-auto'}`}>
            <NodePalette tools={workflowTools} />
          </div>
        </div>

        {visibleSurface && visibleSurface !== 'copilot' ? (
          <div className="absolute right-4 top-[4.75rem] bottom-4 z-20 flex flex-col items-end justify-start pointer-events-none">
            <div className={`relative flex h-full min-h-0 items-start ${canShowSurfaceRail ? 'pl-11' : ''}`}>
              {canShowSurfaceRail ? (
                <WorkflowEditorSurfaceRail
                  items={workbenchTabItems}
                  activeItem={activeWorkbenchTabKey}
                  onSelect={switchWorkbenchTab}
                  className="absolute left-0 top-5"
                />
              ) : null}
              <div
                className={`pointer-events-auto h-full min-h-0 flex flex-col transition-[width,max-width] duration-200 ease-out will-change-[width] ${rightWorkbenchWidthClass}`}
              >
                <div
                  key={activeWorkbenchTabKey ?? 'workbench-empty'}
                  className="h-full min-h-0 animate-in fade-in-50 slide-in-from-right-1 duration-150"
                >
                  {visibleSurface === 'property' ? (
                    <PropertyPanel
                      tools={workflowTools}
                      workflowDescription={workflowDescriptionDraft}
                      onWorkflowDescriptionChange={setWorkflowDescriptionDraft}
                      selectionTarget={activePropertyTarget}
                      onClose={closeActiveSurface}
                      readOnly={isSystemWorkflow}
                      onAskAiEdit={({ title, instruction, selection }) => openCopilot({
                        mode: 'edit_selection',
                        title,
                        instruction,
                        selection,
                        restoreOnClose: true,
                      })}
                    />
                  ) : null}
                  {visibleSurface === 'validation' ? (
                    <WorkflowValidationChecklistPanel
                      open
                      isValidating={isValidating}
                      errors={validationErrors}
                      warnings={validationWarnings}
                      requestError={validationRequestError}
                      lastValidatedAt={lastValidatedAt}
                      onClose={closeActiveSurface}
                      onLocate={handleLocateValidationIssue}
                      onRefresh={handleValidationRefresh}
                      onAskAiFix={() => openCopilot({
                        mode: 'fix_validation',
                        title: t('settings.skills.workflowCopilot.fixWithAi'),
                        instruction: t('settings.skills.workflowCopilot.defaultFixInstruction'),
                        validationContext: {
                          errors: validationErrors.map((issue) => ({
                            severity: issue.severity,
                            nodeId: issue.nodeId,
                            subflowNodeId: issue.subflowNodeId ?? null,
                            message: issue.message,
                            source: issue.source,
                          })),
                          warnings: validationWarnings.map((issue) => ({
                            severity: issue.severity,
                            nodeId: issue.nodeId,
                            subflowNodeId: issue.subflowNodeId ?? null,
                            message: issue.message,
                            source: issue.source,
                          })),
                        },
                        restoreOnClose: true,
                      })}
                    />
                  ) : null}
                  {visibleSurface === 'testRun' && workflowId ? (
                    <WorkflowTestRunPanel
                      open
                      workflowId={workflowId}
                      startInputMode={startInputMode}
                      onClose={closeActiveSurface}
                      onAnalyzeWithAi={(context) => openCopilot({
                        mode: 'analyze_test_run',
                        title: t('settings.skills.workflowCopilot.analyzeWithAi'),
                        instruction: t('settings.skills.workflowCopilot.defaultAnalyzeInstruction'),
                        testRunContext: context,
                        restoreOnClose: true,
                      })}
                    />
                  ) : null}
                  {visibleSurface === 'versionHistory' ? (
                    <TargetVersionPanel
                      open
                      loading={versionsLoading}
                      loadError={versionsError instanceof Error ? versionsError.message : null}
                      isSystemTarget={Boolean(workflowEntity?.isSystem)}
                      draftVersionId={workflowVersions?.draftVersionId}
                      publishedVersionId={workflowVersions?.publishedVersionId}
                      versions={workflowVersions?.versions ?? []}
                      clearing={clearVersionsMutation.isPending}
                      deletingVersionId={deleteVersionMutation.isPending ? deleteVersionMutation.variables : null}
                      restoringVersionId={rollbackMutation.isPending ? rollbackMutation.variables : null}
                      onClose={closeActiveSurface}
                      onRefresh={() => { void refetchVersions() }}
                      onClear={handleClearVersions}
                      onDelete={handleDeleteVersion}
                      onRestore={(versionId) => rollbackMutation.mutate(versionId)}
                    />
                  ) : null}
                  {visibleSurface === 'envVars' ? (
                    <WorkflowEnvVarPanel
                      open
                      envVars={workflowEnvVars}
                      onClose={closeActiveSurface}
                      onChange={handleWorkflowEnvVarsChange}
                    />
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {workflowId && copilotSessionRetained ? (
          <div className={`absolute inset-4 top-[4.75rem] z-30 pointer-events-none ${copilotOpen ? '' : 'hidden'}`}>
            <div className={`h-full min-h-0 gap-3 ${showCopilotPreviewPane ? 'grid grid-cols-[minmax(0,1fr)_minmax(380px,540px)]' : 'flex justify-end'}`}>
              {showCopilotPreviewPane ? (
                <div className="pointer-events-auto min-h-0 min-w-0">
                  <WorkflowEditorSurfaceShell
                    size="full"
                    density="compact"
                    icon={<Sparkles className="h-4 w-4" />}
                    title={activeCopilotProposal?.title || t('settings.skills.workflowCopilot.previewDraftTitle')}
                    subtitle={!activeCopilotProposal
                      ? t('settings.skills.workflowCopilot.previewDraftHint')
                      : effectivePreviewMode === 'current'
                        ? t('settings.skills.workflowCopilot.previewCurrentHint')
                        : t('settings.skills.workflowCopilot.previewProposedHint')}
                    headerActions={(
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        <div className="inline-flex rounded-2xl border border-slate-200 bg-slate-50 p-1">
                          <button
                            type="button"
                            onClick={() => {
                              setCopilotPreviewMode('current')
                              setCopilotPreviewVisible(true)
                            }}
                            className={`rounded-xl px-3 py-1.5 text-xs font-medium transition-colors ${
                              effectivePreviewMode === 'current'
                                ? 'bg-white text-slate-900 shadow-sm'
                                : 'text-slate-600 hover:text-slate-900'
                            }`}
                          >
                            {t('settings.skills.workflowCopilot.previewCurrent')}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              if (!activeCopilotProposal) return
                              setCopilotPreviewMode('proposed')
                              setCopilotPreviewVisible(true)
                            }}
                            disabled={!activeCopilotProposal}
                            className={`rounded-xl px-3 py-1.5 text-xs font-medium transition-colors ${
                              effectivePreviewMode === 'proposed'
                                ? 'bg-white text-slate-900 shadow-sm'
                                : 'text-slate-600 hover:text-slate-900'
                            } disabled:cursor-not-allowed disabled:opacity-40`}
                          >
                            {t('settings.skills.workflowCopilot.previewProposed')}
                          </button>
                        </div>
                        {activeCopilotProposal ? (
                          <>
                            <span className={`inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs font-medium ${
                              activeCopilotProposal.validation.valid
                                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                                : 'border-amber-200 bg-amber-50 text-amber-700'
                            }`}>
                              {activeCopilotProposal.validation.valid
                                ? t('settings.skills.workflowCopilot.validationOk')
                                : t('settings.skills.workflowCopilot.validationIssues', { count: activeCopilotProposal.validation.errors.length })}
                            </span>
                            <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700">
                              {t('settings.skills.workflowCopilot.affectedNodes', { count: activeCopilotProposal.affectedNodeIds.length })}
                            </span>
                          </>
                        ) : null}
                      </div>
                    )}
                    bodyClassName="flex min-h-0 flex-1 overflow-hidden bg-slate-50/70 p-3.5"
                    footerClassName="pt-2.5"
                    footer={activeCopilotProposal ? (
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          {activeCopilotProposal.layoutRecommendation === 'autolayout' ? (
                            <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700">
                              <Sparkles className="h-3.5 w-3.5" />
                              {t('settings.skills.workflowCopilot.autolayoutSuggested')}
                            </span>
                          ) : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setCopilotPreviewVisible(false)}
                            className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
                          >
                            {t('settings.skills.workflowCopilot.closePreview')}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleApplyCopilotProposal(activeCopilotProposal)}
                            disabled={isApplyingCopilotProposal}
                            className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
                          >
                            {isApplyingCopilotProposal ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                            {t('settings.skills.workflowCopilot.applyProposal')}
                          </button>
                          {currentProposalApplyState === 'applied_current' ? (
                            <button
                              type="button"
                              onClick={() => void handleUndoAppliedCopilot()}
                              className="inline-flex items-center gap-2 rounded-2xl border border-emerald-300 bg-white px-4 py-2 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-50"
                            >
                              {t('settings.skills.workflowCopilot.undoApplied')}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ) : (
                      <div className="text-xs text-muted-foreground">
                        {t('settings.skills.workflowCopilot.previewEmptyHint')}
                      </div>
                    )}
                  >
                    <WorkflowReadonlyCanvas
                      key={`${effectivePreviewMode}:${effectivePreviewMode === 'current' ? currentDraftHash : activeCopilotProposalKey ?? 'proposal'}`}
                      className="h-full min-h-0 overflow-hidden rounded-[24px] border border-slate-200 bg-slate-50"
                      nodes={effectivePreviewWorkflow.nodes}
                      edges={effectivePreviewWorkflow.edges}
                      highlightedNodeIds={activeCopilotProposal?.affectedNodeIds ?? []}
                      showFitViewControl
                    />
                  </WorkflowEditorSurfaceShell>
                </div>
              ) : null}

              <div className={`min-h-0 ${showCopilotPreviewPane ? 'min-w-0' : 'w-full max-w-[540px]'}`}>
                <div className={`relative flex h-full min-h-0 items-start ${canShowSurfaceRail ? 'pl-11' : ''}`}>
                  {canShowSurfaceRail ? (
                    <WorkflowEditorSurfaceRail
                      items={workbenchTabItems}
                      activeItem={activeWorkbenchTabKey}
                      onSelect={switchWorkbenchTab}
                      className="absolute left-0 top-5"
                    />
                  ) : null}
                  <div
                    className={`pointer-events-auto h-full min-h-0 flex-1 transition-[width,max-width] duration-300 ease-out will-change-[width] ${
                      showCopilotPreviewPane ? 'w-full min-w-0 max-w-none' : rightWorkbenchWidthClass
                    }`}
                  >
                    <WorkflowCopilotPanel
                      open={copilotOpen}
                      workflowId={workflowId}
                      draft={workflowInput}
                      launchContext={copilotLaunchContext}
                      layout="split"
                      proposal={activeCopilotProposal}
                      previewVisible={copilotPreviewVisible}
                      previewMode={copilotPreviewMode}
                      isApplyingProposal={isApplyingCopilotProposal}
                      currentProposalApplyState={currentProposalApplyState}
                      onClose={handleCloseCopilot}
                      onProposalChange={setActiveCopilotProposal}
                      onPreviewVisibleChange={setCopilotPreviewVisible}
                      onPreviewModeChange={setCopilotPreviewMode}
                      onApplyProposal={handleApplyCopilotProposal}
                      onUndoAppliedProposal={handleUndoAppliedCopilot}
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <PublishVersionDialog
          open={publishDialogOpen}
          defaultName={defaultPublishVersionName}
          submitting={publishMutation.isPending}
          onOpenChange={(nextOpen) => setActiveDialog(nextOpen ? 'publish' : null)}
          onConfirm={handlePublish}
        />
      </div>
    </ReactFlowProvider>
  )
}
