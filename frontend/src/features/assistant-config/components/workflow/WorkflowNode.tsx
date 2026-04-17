import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react'
import { useTranslation } from 'react-i18next'
import {
  Play,
  Brain,
  Bot,
  Wrench,
  GitBranch,
  ScanSearch,
  BookOpen,
  RefreshCw,
  Infinity,
  Plus,
  SendHorizontal,
  FileCode2,
  Equal,
  UserCheck,
  Globe,
  Network,
} from 'lucide-react'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { useWorkflowEditorStore } from '../../stores/workflow-editor-store'
import type { ContainerBodyNodeType, NodeType } from '../../api/workflow'
import { normalizeIfElseConfig } from './ifElseConfig'
import { defaultLabelForNodeType } from './labelUtils'
import { ContainerSubflowCanvas } from './ContainerSubflowCanvas'
import { resolveCallableWorkflowVersion } from './nodeFactory'
import type { CallableWorkflowDefinition, WorkflowToolDefinition } from './types'
import { QuickAddPopover, type QuickAddPayload } from './QuickAddPopover'
import { normalizeContainerPreviewBodyNodes } from './autoLayout'
import {
  CONTAINER_NODE_HANDLE_TOP,
  MAIN_NODE_HANDLE_TOP,
  estimateContainerNodeSizeFromConfig,
  getIfElseNodeHeight,
} from './workflowGeometry'

const NODE_STYLES: Record<NodeType, { header: string; icon: typeof Play; iconColor: string }> = {
  start: { header: 'bg-gradient-to-r from-emerald-100/90 to-green-100/90 border-b border-emerald-200', icon: Play, iconColor: 'text-emerald-700' },
  llm: { header: 'bg-gradient-to-r from-violet-100/90 to-purple-100/90 border-b border-violet-200', icon: Brain, iconColor: 'text-violet-700' },
  agent: { header: 'bg-gradient-to-r from-indigo-100/90 to-sky-100/90 border-b border-indigo-200', icon: Bot, iconColor: 'text-indigo-700' },
  tool: { header: 'bg-gradient-to-r from-sky-100/90 to-blue-100/90 border-b border-sky-200', icon: Wrench, iconColor: 'text-sky-700' },
  workflow_call: { header: 'bg-gradient-to-r from-emerald-100/90 to-teal-100/90 border-b border-emerald-200', icon: Network, iconColor: 'text-emerald-700' },
  if_else: { header: 'bg-gradient-to-r from-amber-100/90 to-yellow-100/90 border-b border-amber-200', icon: GitBranch, iconColor: 'text-amber-700' },
  parameter_extractor: { header: 'bg-gradient-to-r from-fuchsia-100/90 to-pink-100/90 border-b border-fuchsia-200', icon: ScanSearch, iconColor: 'text-fuchsia-700' },
  knowledge_retrieval: { header: 'bg-gradient-to-r from-teal-100/90 to-emerald-100/90 border-b border-teal-200', icon: BookOpen, iconColor: 'text-teal-700' },
  iteration: { header: 'bg-gradient-to-r from-cyan-100/90 to-sky-100/90 border-b border-cyan-200', icon: RefreshCw, iconColor: 'text-cyan-700' },
  loop: { header: 'bg-gradient-to-r from-indigo-100/90 to-blue-100/90 border-b border-indigo-200', icon: Infinity, iconColor: 'text-indigo-700' },
  code_executor: { header: 'bg-gradient-to-r from-orange-100/90 to-amber-100/90 border-b border-orange-200', icon: FileCode2, iconColor: 'text-orange-700' },
  http_request: { header: 'bg-gradient-to-r from-blue-100/90 to-indigo-100/90 border-b border-blue-200', icon: Globe, iconColor: 'text-blue-700' },
  variable_assign: { header: 'bg-gradient-to-r from-lime-100/90 to-emerald-100/90 border-b border-lime-200', icon: Equal, iconColor: 'text-lime-700' },
  human_in_loop: { header: 'bg-gradient-to-r from-blue-100/90 to-cyan-100/90 border-b border-blue-200', icon: UserCheck, iconColor: 'text-blue-700' },
  output: { header: 'bg-gradient-to-r from-rose-100/90 to-orange-100/90 border-b border-rose-200', icon: SendHorizontal, iconColor: 'text-rose-700' },
}
const HANDLE_CLICK_THRESHOLD = 5
const CONTAINER_INPUT_HANDLE_ID = 'container_input'
const CONTAINER_OUTPUT_HANDLE_ID = 'container_output'

type ContainerBodyNode = {
  nodeId: string
  nodeType: ContainerBodyNodeType
  label: string
  positionX?: number
  positionY?: number
  config?: Record<string, unknown> | null
}

type ContainerBodyEdge = {
  edgeId: string
  sourceNodeId: string
  targetNodeId: string
  sourceHandle?: string
  targetHandle?: string
}

type RuntimeStatus = 'running' | 'success' | 'error'

function normalizeSubflowSourceHandle(
  sourceNode: ContainerBodyNode | undefined,
  rawSourceHandle: string,
  conditionType: string,
): string {
  const sourceHandle = rawSourceHandle.trim()
  if (!sourceNode || sourceNode.nodeType !== 'if_else') {
    if (!sourceHandle || sourceHandle === CONTAINER_OUTPUT_HANDLE_ID) return 'output'
    return sourceHandle
  }
  const normalized = normalizeIfElseConfig((sourceNode.config ?? null) as Record<string, unknown> | null)
  const validHandles = new Set<string>([
    ...normalized.branches.map((branch) => branch.id),
    normalized.elseHandle || 'else',
  ])
  if (sourceHandle && sourceHandle !== 'output' && sourceHandle !== CONTAINER_OUTPUT_HANDLE_ID && validHandles.has(sourceHandle)) {
    return sourceHandle
  }
  if (conditionType === 'default') {
    return normalized.elseHandle || 'else'
  }
  return normalized.branches[0]?.id || normalized.elseHandle || 'else'
}

function normalizeSubflowTargetHandle(rawTargetHandle: string): string {
  const targetHandle = rawTargetHandle.trim()
  if (!targetHandle || targetHandle === CONTAINER_INPUT_HANDLE_ID) return 'input'
  return targetHandle
}

function normalizeContainerBodyNodes(config: Record<string, unknown>): ContainerBodyNode[] {
  const raw = (config.bodyNodes ?? config.body_nodes) as unknown
  if (!Array.isArray(raw)) {
    return [
      {
        nodeId: 'start',
        nodeType: 'start',
        label: defaultLabelForNodeType('start'),
        config: null,
      },
    ]
  }
  const nodes = raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const nodeType = String(item.nodeType ?? item.node_type ?? '').trim() as ContainerBodyNodeType
      const fallbackType: ContainerBodyNodeType = nodeType || 'llm'
      return {
        nodeId: String(item.nodeId ?? item.node_id ?? ''),
        nodeType: fallbackType,
        label: String(item.label ?? defaultLabelForNodeType(fallbackType)),
        positionX: Number.isFinite(Number(item.positionX ?? item.position_x))
          ? Number(item.positionX ?? item.position_x)
          : undefined,
        positionY: Number.isFinite(Number(item.positionY ?? item.position_y))
          ? Number(item.positionY ?? item.position_y)
          : undefined,
        config: item.config && typeof item.config === 'object' ? (item.config as Record<string, unknown>) : null,
      }
    })
    .filter((item) => item.nodeId)

  if (!nodes.some((item) => item.nodeType === 'start')) {
    return [
      {
        nodeId: 'start',
        nodeType: 'start',
        label: defaultLabelForNodeType('start'),
        config: null,
      },
      ...nodes,
    ]
  }
  return nodes
}

function normalizeContainerBodyEdges(config: Record<string, unknown>): ContainerBodyEdge[] {
  const raw = (config.bodyEdges ?? config.body_edges) as unknown
  if (!Array.isArray(raw)) return []
  const bodyNodes = normalizeContainerBodyNodes(config)
  const nodeById = new Map(bodyNodes.map((node) => [node.nodeId, node]))
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const sourceNodeId = String(item.sourceNodeId ?? item.source_node_id ?? '')
      const sourceNode = nodeById.get(sourceNodeId)
      const targetNodeId = String(item.targetNodeId ?? item.target_node_id ?? '')
      const conditionType = String(item.conditionType ?? item.condition_type ?? '').trim().toLowerCase()
      const sourceHandle = normalizeSubflowSourceHandle(
        sourceNode,
        String(item.sourceHandle ?? item.source_handle ?? ''),
        conditionType,
      )
      const targetHandle = normalizeSubflowTargetHandle(String(item.targetHandle ?? item.target_handle ?? 'input'))
      return {
        edgeId: String(item.edgeId ?? item.edge_id ?? ''),
        sourceNodeId,
        targetNodeId,
        sourceHandle,
        targetHandle,
      }
    })
    .filter((item) => (
      item.edgeId &&
      item.sourceNodeId &&
      item.targetNodeId &&
      nodeById.has(item.sourceNodeId) &&
      nodeById.has(item.targetNodeId)
    ))
}

function getPreview(data: WfNodeData): string {
  const cfg = (data.config ?? {}) as Record<string, unknown>
  const callableWorkflows = Array.isArray((data as { callableWorkflows?: unknown }).callableWorkflows)
    ? ((data as { callableWorkflows?: CallableWorkflowDefinition[] }).callableWorkflows ?? [])
    : []
  switch (data.nodeType) {
    case 'start': {
      const desc = data.workflowDescription as string | undefined
      return desc ? truncate(desc, 120) : ''
    }
    case 'llm':
      return truncate(cfg.systemPrompt as string, 50)
    case 'agent': {
      const prompt = truncate(cfg.systemPrompt as string, 38)
      const toolNames = Array.isArray(cfg.toolNames)
        ? cfg.toolNames.map((item) => String(item).trim()).filter(Boolean)
        : []
      const toolSummary = toolNames.length > 0 ? `tools ${toolNames.length}` : 'no tools'
      if (!prompt) return toolSummary
      return `${prompt} · ${toolSummary}`
    }
    case 'tool':
      return (cfg.toolName as string) || ''
    case 'workflow_call': {
      const targetWorkflowId = String(cfg.targetWorkflowId ?? cfg.target_workflow_id ?? '').trim()
      const bindingMode = String(cfg.bindingMode ?? cfg.binding_mode ?? 'pinned').trim().toLowerCase() === 'latest'
        ? 'latest'
        : 'pinned'
      const versionId = String(cfg.targetPublishedVersionId ?? cfg.target_published_version_id ?? '').trim()
      const workflow = callableWorkflows.find((item) => item.id === targetWorkflowId)
      const resolvedVersion = workflow ? resolveCallableWorkflowVersion(workflow, versionId || null) : undefined
      const outputCount = resolvedVersion?.outputParams?.length ?? workflow?.outputParams?.length ?? 0
      if (!workflow) return bindingMode === 'latest' ? 'Latest published workflow' : 'Pinned published workflow'
      const versionSummary = bindingMode === 'latest'
        ? 'latest published'
        : resolvedVersion?.versionName || 'pinned version'
      return `${workflow.name} · ${versionSummary} · ${outputCount + 1} outputs`
    }
    case 'if_else': {
      const normalized = normalizeIfElseConfig(cfg)
      const elifCount = Math.max(0, normalized.branches.length - 1)
      return `IF${elifCount > 0 ? ` + ${elifCount} ELIF` : ''} + ELSE`
    }
    case 'parameter_extractor': {
      const fields = (Array.isArray(cfg.outputFields) ? cfg.outputFields : Array.isArray(cfg.output_fields) ? cfg.output_fields : [])
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '').trim() : ''))
        .filter(Boolean)
      if (fields.length === 0) return ''
      const brief = fields.slice(0, 3).join(', ')
      return fields.length > 3 ? `${brief} +${fields.length - 3}` : brief
    }
    case 'knowledge_retrieval': {
      const query = truncate(formatKnowledgeQuery(cfg.query), 48)
      const rawMode = String(cfg.mode ?? '').trim()
      const topK = typeof cfg.topK === 'number' && Number.isFinite(cfg.topK) ? String(cfg.topK) : ''
      const metaParts = [
        rawMode ? `mode ${rawMode}` : '',
        topK ? `topK ${topK}` : '',
      ].filter(Boolean)
      if (metaParts.length === 0) return query
      return `${query} · ${metaParts.join(' · ')}`
    }
    case 'iteration': {
      const inputSource = String(cfg.inputSource ?? '').trim()
      const outputVariable = String(cfg.outputVariable ?? '').trim()
      if (!inputSource && !outputVariable) return 'Iterate array and aggregate results'
      return `${inputSource || 'array'} -> ${outputVariable || 'results'}`
    }
    case 'loop': {
      const maxIterations = Number.isFinite(Number(cfg.maxIterations)) ? String(cfg.maxIterations) : '10'
      return `最大循环 ${maxIterations} 次`
    }
    case 'code_executor': {
      const language = String(cfg.language ?? 'python').toLowerCase()
      const outputFields = (Array.isArray(cfg.outputFields) ? cfg.outputFields : Array.isArray(cfg.output_fields) ? cfg.output_fields : [])
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '').trim() : ''))
        .filter(Boolean)
      if (outputFields.length === 0) return `${language} · script`
      const brief = outputFields.slice(0, 3).join(', ')
      return `${language} · ${brief}${outputFields.length > 3 ? ` +${outputFields.length - 3}` : ''}`
    }
    case 'http_request': {
      const method = String(cfg.method ?? 'GET').trim().toUpperCase() || 'GET'
      const url = String(cfg.url ?? '').trim()
      if (!url) return `${method} · URL`
      return `${method} · ${truncate(url, 42)}`
    }
    case 'variable_assign': {
      const variableName = String(cfg.variableName ?? cfg.variable_name ?? '').trim()
      const operation = String(cfg.operation ?? 'set').trim().toLowerCase() || 'set'
      if (!variableName) return operation
      return `${operation} ${variableName}`
    }
    case 'human_in_loop': {
      const instruction = String(cfg.instruction ?? '').trim()
      const fields = Array.isArray(cfg.fields) ? cfg.fields : []
      if (!instruction) return `fields ${fields.length}`
      return `${truncate(instruction, 40)} · ${fields.length} fields`
    }
    case 'output': {
      const mode = String(cfg.outputMode ?? 'text').trim().toLowerCase() === 'structured' ? 'structured' : 'text'
      if (mode === 'text') {
        return truncate(formatTemplatePreview(cfg.textTemplate), 50)
      }
      const fields = (Array.isArray(cfg.outputFields) ? cfg.outputFields : [])
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '').trim() : ''))
        .filter(Boolean)
      if (fields.length === 0) return 'Structured output'
      const brief = fields.slice(0, 3).join(', ')
      return fields.length > 3 ? `${brief} +${fields.length - 3}` : brief
    }
    default:
      return ''
  }
}

function formatKnowledgeQuery(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return 'Start input'

  const normalized = text
    .replace(/\{\{\s*/g, '')
    .replace(/\s*\}\}/g, '')
    .trim()

  if (!normalized) return 'Start input'
  if (normalized === 'start.user_input') return 'Start input'
  return normalized
}

function formatTemplatePreview(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''

  return text
    .replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_, expr: string) => formatTemplateExpression(expr))
    .replace(/\s+/g, ' ')
    .trim()
}

function formatTemplateExpression(expr: string): string {
  const normalized = expr.trim()
  if (!normalized) return ''
  if (normalized === 'start.user_input') return 'Start input'
  return normalized
}

function truncate(s: string | undefined | null, max: number): string {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

function stableSerialize(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map((item) => stableSerialize(item)).join(',')}]`
  const record = value as Record<string, unknown>
  const keys = Object.keys(record).sort()
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`).join(',')}}`
}

function normalizeSignatureNumber(value: unknown): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return ''
  return String(parsed)
}

function buildContainerBodySignature(
  nodes: ContainerBodyNode[],
  edges: ContainerBodyEdge[],
): string {
  const nodePart = nodes
    .map((node) => [
      node.nodeId,
      node.nodeType,
      node.label,
      normalizeSignatureNumber(node.positionX),
      normalizeSignatureNumber(node.positionY),
      stableSerialize(node.config ?? null),
    ].join('~'))
    .join('|')
  const edgePart = edges
    .map((edge) => [
      edge.edgeId,
      edge.sourceNodeId,
      edge.targetNodeId,
      String(edge.sourceHandle ?? ''),
      String(edge.targetHandle ?? ''),
    ].join('~'))
    .join('|')
  return `${nodePart}||${edgePart}`
}

function WorkflowNodeInner({ id, data }: NodeProps) {
  const { t } = useTranslation()
  const nodeData = data as unknown as WfNodeData
  const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)
  const updateNodeConfig = useWorkflowEditorStore((s) => s.updateNodeConfig)
  const setSelectedSubflowSelection = useWorkflowEditorStore((s) => s.setSelectedSubflowSelection)
  const updateNodeInternals = useUpdateNodeInternals()
  const isSelected = selectedNodeId === id
  const style = NODE_STYLES[nodeData.nodeType] ?? NODE_STYLES.llm
  const Icon = style.icon
  const runtimeStatusRaw = (nodeData as { runtimeStatus?: unknown }).runtimeStatus
  const runtimeStatus = runtimeStatusRaw === 'running' || runtimeStatusRaw === 'success' || runtimeStatusRaw === 'error'
    ? (runtimeStatusRaw as RuntimeStatus)
    : null
  const preview = getPreview(nodeData)
  const previewText = preview || '\u00A0'
  const isStart = nodeData.nodeType === 'start'
  const isIfElse = nodeData.nodeType === 'if_else'
  const isHumanInLoop = nodeData.nodeType === 'human_in_loop'
  const isOutputNode = nodeData.nodeType === 'output'
  const isContainer = nodeData.nodeType === 'iteration' || nodeData.nodeType === 'loop'
  const containerConfig = ((nodeData.config ?? {}) as Record<string, unknown>)
  const bodyNodes = isContainer ? normalizeContainerBodyNodes(containerConfig) : []
  const bodyEdges = isContainer ? normalizeContainerBodyEdges(containerConfig) : []
  const previewBodyNodes = useMemo(
    () => (isContainer ? normalizeContainerPreviewBodyNodes(bodyNodes, bodyEdges) : bodyNodes),
    [bodyEdges, bodyNodes, isContainer],
  )
  const containerBodySignature = useMemo(
    () => buildContainerBodySignature(bodyNodes, bodyEdges),
    [bodyEdges, bodyNodes],
  )
  const containerSize = isContainer
    ? estimateContainerNodeSizeFromConfig({
      ...containerConfig,
      bodyNodes: previewBodyNodes,
      bodyEdges,
    })
    : null
  const quickAddHandles = Array.isArray((nodeData as { quickAddHandles?: unknown }).quickAddHandles)
    ? ((nodeData as { quickAddHandles?: unknown[] }).quickAddHandles ?? [])
      .filter((item): item is string => typeof item === 'string')
    : []
  const quickAddHandleSet = new Set(quickAddHandles)
  const quickAddTools = Array.isArray((nodeData as { quickAddTools?: unknown }).quickAddTools)
    ? ((nodeData as { quickAddTools?: WorkflowToolDefinition[] }).quickAddTools ?? [])
    : []
  const quickAddWorkflows = Array.isArray((nodeData as { quickAddWorkflows?: unknown }).quickAddWorkflows)
    ? ((nodeData as { quickAddWorkflows?: CallableWorkflowDefinition[] }).quickAddWorkflows ?? [])
    : []
  const floatingUiEpoch = Number((nodeData as { floatingUiEpoch?: unknown }).floatingUiEpoch ?? 0)
  const onQuickAdd = typeof (nodeData as { onQuickAdd?: unknown }).onQuickAdd === 'function'
    ? ((nodeData as { onQuickAdd?: (nodeId: string, handleId: string, payload: QuickAddPayload) => void }).onQuickAdd ?? null)
    : null
  const isReadOnly = Boolean((nodeData as { readOnly?: unknown }).readOnly)
  const [openQuickAddHandle, setOpenQuickAddHandle] = useState<string | null>(null)
  const pointerDownRef = useRef<{ handleId: string; x: number; y: number } | null>(null)
  const nodeHandleTop = isContainer ? CONTAINER_NODE_HANDLE_TOP : MAIN_NODE_HANDLE_TOP
  const inputHandleId = isContainer ? CONTAINER_INPUT_HANDLE_ID : 'input'
  const outputHandleId = isContainer ? CONTAINER_OUTPUT_HANDLE_ID : 'output'
  const ifElseHandleCount = useMemo(() => {
    if (!isIfElse) return 0
    const normalized = normalizeIfElseConfig((nodeData.config ?? {}) as Record<string, unknown>)
    return normalized.branches.length + 1
  }, [isIfElse, nodeData.config])

  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      updateNodeInternals(id)
    })
    return () => cancelAnimationFrame(raf)
  }, [
    id,
    ifElseHandleCount,
    isContainer,
    isHumanInLoop,
    nodeHandleTop,
    updateNodeInternals,
    containerSize?.height,
    containerSize?.width,
  ])

  useEffect(() => {
    setOpenQuickAddHandle(null)
  }, [floatingUiEpoch])

  const persistContainerBody = useCallback((nextNodes: ContainerBodyNode[], nextEdges: ContainerBodyEdge[]) => {
    const nextBodySignature = buildContainerBodySignature(nextNodes, nextEdges)
    if (nextBodySignature === containerBodySignature) return
    updateNodeConfig(id, {
      ...containerConfig,
      bodyNodes: nextNodes,
      bodyEdges: nextEdges,
    } as Record<string, unknown>, { pushHistory: true })
  }, [containerBodySignature, containerConfig, id, updateNodeConfig])

  const handleSubflowSelectionChange = useCallback((selection: { nodeId: string | null; edgeId: string | null }) => {
    setSelectedSubflowSelection(id, selection.nodeId, selection.edgeId)
  }, [id, setSelectedSubflowSelection])

  const handleHandlePointerDown = (handleId: string, event: ReactPointerEvent) => {
    if (!quickAddHandleSet.has(handleId)) return
    pointerDownRef.current = {
      handleId,
      x: event.clientX,
      y: event.clientY,
    }
  }

  const handleHandlePointerUp = (handleId: string, event: ReactPointerEvent) => {
    if (!quickAddHandleSet.has(handleId)) return
    const down = pointerDownRef.current
    pointerDownRef.current = null
    if (!down || down.handleId !== handleId) return
    const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y) > HANDLE_CLICK_THRESHOLD
    if (moved) return
    event.stopPropagation()
    setOpenQuickAddHandle((prev) => (prev === handleId ? null : handleId))
  }

  const handleHandlePointerCancel = () => {
    pointerDownRef.current = null
  }

  const runtimeCardClass = runtimeStatus === 'running'
    ? 'ring-2 ring-amber-300 border-amber-400 shadow-md'
    : runtimeStatus === 'success'
      ? 'ring-2 ring-emerald-300 border-emerald-400 shadow-md'
      : runtimeStatus === 'error'
        ? 'ring-2 ring-red-300 border-red-400 shadow-md'
        : ''

  const renderQuickAddPopover = (
    handleId: string,
    side: 'left' | 'right',
    anchorStyle: CSSProperties,
  ) => {
    if (!onQuickAdd || !quickAddHandleSet.has(handleId)) return null
    return (
      <QuickAddPopover
        scope="main"
        tools={quickAddTools}
        workflows={quickAddWorkflows}
        open={openQuickAddHandle === handleId}
        onOpenChange={(nextOpen) => setOpenQuickAddHandle(nextOpen ? handleId : null)}
        side={side}
        align="center"
        sideOffset={12}
        onSelect={(payload) => {
          onQuickAdd(id, handleId, payload)
          setOpenQuickAddHandle(null)
        }}
        anchor={(
          <div
            className="pointer-events-none absolute z-[15] flex h-6 w-6 items-center justify-center rounded-full border border-white/90 bg-blue-600 text-white ring-2 ring-white shadow-[0_2px_8px_rgba(37,99,235,0.22)] opacity-0 group-hover/workflow-node:opacity-100 transition-opacity duration-150"
            style={anchorStyle}
          >
            <Plus className="h-3 w-3" />
          </div>
        )}
      />
    )
  }

  return (
    <div
      className={`
        group/workflow-node relative ${isContainer ? '' : 'w-[260px]'} rounded-xl bg-white shadow-sm border transition-all duration-200
        ${isSelected ? 'ring-2 ring-indigo-500 border-indigo-500 shadow-lg shadow-indigo-500/20 z-10' : runtimeCardClass || 'border-border hover:shadow-md hover:border-indigo-500/30'}
      `}
      style={{
        width: isContainer && containerSize ? `${containerSize.width}px` : undefined,
        minHeight: isIfElse
          ? `${getIfElseNodeHeight((nodeData.config ?? null) as Record<string, unknown> | null)}px`
          : isContainer
            ? `${containerSize?.height ?? 248}px`
            : undefined
      }}
    >
      {/* Header */}
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-t-xl ${style.header}`}>
        <div className={`p-1 rounded-md bg-white/80 shadow-sm ${style.iconColor}`}>
          <Icon className="w-3.5 h-3.5" />
        </div>
        <span className="text-xs font-semibold text-foreground/90 truncate flex-1">
          {isStart ? t('settings.skills.nodeTypes.start') : (nodeData.label || nodeData.nodeType)}
        </span>
        {runtimeStatus && (
          <span
            className={`inline-flex h-2.5 w-2.5 rounded-full ${runtimeStatus === 'running'
              ? 'bg-amber-500'
              : runtimeStatus === 'success'
                ? 'bg-emerald-500'
                : 'bg-red-500'
              }`}
            title={`runtime-${runtimeStatus}`}
          />
        )}
      </div>

      {!isContainer && (
        <div className="px-3 py-3 min-h-[50px] flex flex-col justify-center">
          <div className="bg-slate-50 border border-slate-100 rounded-md p-2 w-full">
            <p className={`text-[12px] leading-relaxed text-muted-foreground break-all whitespace-pre-wrap line-clamp-3 ${preview ? '' : 'italic opacity-50'}`}>
              {previewText || 'No configuration'}
            </p>
          </div>
        </div>
      )}

      {isContainer && (
        <div className="px-3 pb-3 pt-2">
          <ContainerSubflowCanvas
            bodyNodes={previewBodyNodes}
            bodyEdges={bodyEdges}
            tools={quickAddTools}
            workflows={quickAddWorkflows}
            floatingUiEpoch={floatingUiEpoch}
            canvasHeight={containerSize?.canvasHeight ?? 168}
            canvasWidth={containerSize?.canvasWidth}
            readOnly={isReadOnly}
            onSelectionChange={handleSubflowSelectionChange}
            onChange={(nextNodes, nextEdges) => persistContainerBody(nextNodes, nextEdges)}
          />
        </div>
      )}

      {/* Input handle */}
      {!isStart && (
        <>
          <Handle
            type="target"
            position={Position.Left}
            id={inputHandleId}
            style={{ top: `${nodeHandleTop}px`, left: '-5px' }}
            className="!w-2.5 !h-2.5 !bg-background !border !border-slate-300/90 hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150 z-10"
            onPointerDown={(event) => handleHandlePointerDown('input', event)}
            onPointerUp={(event) => handleHandlePointerUp('input', event)}
            onPointerCancel={handleHandlePointerCancel}
          />
          {renderQuickAddPopover('input', 'left', {
            left: 0,
            top: `${nodeHandleTop}px`,
            transform: 'translate(-50%, -50%)',
          })}
        </>
      )}

      {/* Output handle(s) */}
      {!isIfElse && !isHumanInLoop && !isOutputNode && (
        <>
          <Handle
            type="source"
            position={Position.Right}
            id={outputHandleId}
            style={{ top: `${nodeHandleTop}px`, right: '-5px' }}
            className="!w-2.5 !h-2.5 !bg-background !border !border-slate-300/90 hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150 z-10"
            onPointerDown={(event) => handleHandlePointerDown('output', event)}
            onPointerUp={(event) => handleHandlePointerUp('output', event)}
            onPointerCancel={handleHandlePointerCancel}
          />
          {renderQuickAddPopover('output', 'right', {
            right: 0,
            top: `${nodeHandleTop}px`,
            transform: 'translate(50%, -50%)',
          })}
        </>
      )}

      {/* IF/ELSE: dynamic output handles */}
      {isIfElse && (
        <div className="absolute -right-[5px] top-[50px] flex flex-col gap-3 py-1">
          {(() => {
            const cfg = nodeData.config as Record<string, unknown> | null
            const normalized = normalizeIfElseConfig(cfg)
            const handles = normalized.branches.map((branch) => branch.id)
            handles.push(normalized.elseHandle || 'else')

            return handles.map((handle) => (
              <div key={handle} className="relative flex items-center justify-center w-3 h-3">
                <Handle
                  type="source"
                  position={Position.Right}
                  id={handle}
                  style={{ position: 'static', transform: 'none' }}
                  className={`!w-2.5 !h-2.5 !bg-background !border ${handle === (normalized.elseHandle || 'else') ? '!border-stone-400' : '!border-green-500'} hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150 z-10`}
                  onPointerDown={(event) => handleHandlePointerDown(handle, event)}
                  onPointerUp={(event) => handleHandlePointerUp(handle, event)}
                  onPointerCancel={handleHandlePointerCancel}
                />
                {renderQuickAddPopover(handle, 'right', {
                  left: '50%',
                  top: '50%',
                  transform: 'translate(-50%, -50%)',
                })}
              </div>
            ))
          })()}
        </div>
      )}

      {/* Human-in-loop: approved/rejected output handles */}
      {isHumanInLoop && (
        <div className="absolute -right-[5px] top-[50px] flex flex-col gap-3 py-1">
          {(['approved', 'rejected'] as const).map((handle) => (
            <div key={handle} className="relative flex items-center justify-center w-3 h-3">
              <Handle
                type="source"
                position={Position.Right}
                id={handle}
                style={{ position: 'static', transform: 'none' }}
                className={`!w-2.5 !h-2.5 !bg-background !border ${handle === 'approved' ? '!border-green-500' : '!border-rose-500'} hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150 z-10`}
                onPointerDown={(event) => handleHandlePointerDown(handle, event)}
                onPointerUp={(event) => handleHandlePointerUp(handle, event)}
                onPointerCancel={handleHandlePointerCancel}
              />
              {renderQuickAddPopover(handle, 'right', {
                left: '50%',
                top: '50%',
                transform: 'translate(-50%, -50%)',
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export const WorkflowNode = memo(WorkflowNodeInner)
