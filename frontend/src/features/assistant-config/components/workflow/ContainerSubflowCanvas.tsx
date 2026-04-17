import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import {
  Brain,
  Bot,
  BookOpen,
  GitBranch,
  Infinity,
  Play,
  Plus,
  RefreshCw,
  ScanSearch,
  Wrench,
  FileCode2,
  Equal,
  UserCheck,
  Globe,
  Network,
} from 'lucide-react'
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type Node,
  type NodeProps,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
} from '@xyflow/react'
import type { ContainerBodyEdge, ContainerBodyNode, ContainerBodyNodeType } from '../../api/workflow'
import { defaultLabelForNodeType } from './labelUtils'
import { normalizeIfElseConfig } from './ifElseConfig'
import { WorkflowDeletableEdge } from './WorkflowDeletableEdge'
import type { CallableWorkflowDefinition, WorkflowToolDefinition } from './types'
import { QuickAddPopover, type QuickAddPayload } from './QuickAddPopover'
import { createSubflowNode, resolveCallableWorkflowVersion } from './nodeFactory'
import {
  IF_ELSE_HANDLE_BASE_TOP,
  IF_ELSE_HANDLE_STEP,
  SUBFLOW_NODE_HANDLE_TOP,
  getSubflowNodeFrame,
  getNodeOutputHandleIds,
} from './workflowGeometry'

const edgeTypes = {
  workflowBezier: WorkflowDeletableEdge,
}

const NODE_STYLES: Record<ContainerBodyNodeType, { header: string; icon: typeof Play; iconColor: string }> = {
  start: { header: 'bg-green-50 border-b border-green-100', icon: Play, iconColor: 'text-green-600' },
  llm: { header: 'bg-purple-50 border-b border-purple-100', icon: Brain, iconColor: 'text-purple-600' },
  agent: { header: 'bg-indigo-50 border-b border-indigo-100', icon: Bot, iconColor: 'text-indigo-600' },
  tool: { header: 'bg-sky-50 border-b border-sky-100', icon: Wrench, iconColor: 'text-sky-600' },
  if_else: { header: 'bg-yellow-50 border-b border-yellow-100', icon: GitBranch, iconColor: 'text-yellow-600' },
  parameter_extractor: { header: 'bg-pink-50 border-b border-pink-100', icon: ScanSearch, iconColor: 'text-pink-600' },
  knowledge_retrieval: { header: 'bg-teal-50 border-b border-teal-100', icon: BookOpen, iconColor: 'text-teal-600' },
  code_executor: { header: 'bg-slate-50 border-b border-slate-100', icon: FileCode2, iconColor: 'text-slate-600' },
  http_request: { header: 'bg-blue-50 border-b border-blue-100', icon: Globe, iconColor: 'text-blue-600' },
  variable_assign: { header: 'bg-lime-50 border-b border-lime-100', icon: Equal, iconColor: 'text-lime-600' },
  human_in_loop: { header: 'bg-blue-50 border-b border-blue-100', icon: UserCheck, iconColor: 'text-blue-600' },
  workflow_call: { header: 'bg-emerald-50 border-b border-emerald-100', icon: Network, iconColor: 'text-emerald-600' },
}

const PREVIEW_MAX = 50
const NODE_ICON_MAP: Record<ContainerBodyNodeType, typeof Play> = {
  start: Play,
  llm: Brain,
  agent: Bot,
  tool: Wrench,
  if_else: GitBranch,
  parameter_extractor: ScanSearch,
  knowledge_retrieval: BookOpen,
  code_executor: FileCode2,
  http_request: Globe,
  variable_assign: Equal,
  human_in_loop: UserCheck,
  workflow_call: Network,
}

const IF_ELSE_HANDLE_RIGHT_OFFSET = -5
const HANDLE_CLICK_THRESHOLD = 5
const CONTAINER_INPUT_HANDLE_ID = 'container_input'
const CONTAINER_OUTPUT_HANDLE_ID = 'container_output'
const QUICK_ADD_X_OFFSET = 280
const QUICK_ADD_Y_STEP = 88
const NODE_COLLISION_X = 200
const NODE_COLLISION_Y = 110
const SOFT_BOUNDARY_LEFT = -40
const SOFT_BOUNDARY_TOP = -80
const SOFT_BOUNDARY_MIN_WIDTH = 420
const SOFT_BOUNDARY_MIN_HEIGHT = 260
const SOFT_BOUNDARY_RIGHT_PADDING = 220
const SOFT_BOUNDARY_BOTTOM_PADDING = 180

function truncate(s: string | undefined | null, max: number): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max)}...` : s
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

function getSubflowPreview(
  nodeType: ContainerBodyNodeType,
  config: Record<string, unknown> | null | undefined,
  workflows: CallableWorkflowDefinition[] = [],
): string {
  const cfg = (config ?? {}) as Record<string, unknown>
  switch (nodeType) {
    case 'llm':
      return truncate(cfg.systemPrompt as string, PREVIEW_MAX)
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
      const fields = Array.isArray(cfg.fields) ? cfg.fields.length : 0
      if (!instruction) return `fields ${fields}`
      return `${truncate(instruction, 36)} · ${fields} fields`
    }
    case 'workflow_call': {
      const targetWorkflowId = String(cfg.targetWorkflowId ?? cfg.target_workflow_id ?? '').trim()
      const bindingMode = String(cfg.bindingMode ?? cfg.binding_mode ?? 'pinned').trim().toLowerCase() === 'latest'
        ? 'latest'
        : 'pinned'
      const versionId = String(cfg.targetPublishedVersionId ?? cfg.target_published_version_id ?? '').trim()
      const workflow = workflows.find((item) => item.id === targetWorkflowId)
      const resolvedVersion = workflow ? resolveCallableWorkflowVersion(workflow, versionId || null) : undefined
      const outputCount = resolvedVersion?.outputParams?.length ?? workflow?.outputParams?.length ?? 0
      const workflowLabel = workflow?.name ?? 'Workflow'
      const modeLabel = bindingMode === 'latest'
        ? 'latest'
        : resolvedVersion?.versionName || 'pinned'
      return `${workflowLabel} · ${modeLabel} · ${outputCount + 1} outputs`
    }
    default:
      return ''
  }
}

type SubflowNodeData = {
  nodeType: ContainerBodyNodeType
  label: string
  config?: Record<string, unknown> | null
  removable: boolean
  quickAddHandles: string[]
  tools: WorkflowToolDefinition[]
  workflows: CallableWorkflowDefinition[]
  floatingUiEpoch?: number
  onRemove?: (nodeId: string) => void
  onQuickAdd?: (nodeId: string, handleId: string, payload: QuickAddPayload) => void
}

type SubflowNodeComponentProps = NodeProps<Node<SubflowNodeData>>
type SubflowEdgeAnchorOverride = {
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
  sourcePosition: Position
  targetPosition: Position
}
type SubflowNodeExtent = [[number, number], [number, number]]
type SubflowFitReason = 'structure' | 'delete-node'

function sourceHandlesForNode(nodeType: ContainerBodyNodeType, config?: Record<string, unknown> | null): string[] {
  return getNodeOutputHandleIds(nodeType, config)
}

function normalizeSubflowSourceHandle(
  sourceNode: ContainerBodyNode | undefined,
  rawSourceHandle: string | null | undefined,
): string {
  const sourceHandle = String(rawSourceHandle ?? '').trim()
  if (!sourceNode) {
    if (!sourceHandle || sourceHandle === CONTAINER_OUTPUT_HANDLE_ID) return 'output'
    return sourceHandle
  }
  const handles = sourceHandlesForNode(sourceNode.nodeType, sourceNode.config ?? null)
  if (!sourceHandle || sourceHandle === CONTAINER_OUTPUT_HANDLE_ID || sourceHandle === 'output') {
    return handles[0] ?? 'output'
  }
  if (handles.includes(sourceHandle)) {
    return sourceHandle
  }
  return handles[0] ?? 'else'
}

function normalizeSubflowTargetHandle(rawTargetHandle: string | null | undefined): string {
  const targetHandle = String(rawTargetHandle ?? '').trim()
  if (!targetHandle || targetHandle === CONTAINER_INPUT_HANDLE_ID) return 'input'
  return targetHandle
}

function quickAddHandlesForNode(nodeType: ContainerBodyNodeType, config?: Record<string, unknown> | null): string[] {
  const outputs = sourceHandlesForNode(nodeType, config)
  if (nodeType === 'start') return outputs
  return ['input', ...outputs]
}

function resolveSubflowHandleTop(
  nodeType: ContainerBodyNodeType,
  config: Record<string, unknown> | null | undefined,
  handleId: string,
): number {
  if (handleId === 'input') {
    return SUBFLOW_NODE_HANDLE_TOP
  }
  if (nodeType !== 'if_else' && nodeType !== 'human_in_loop') {
    return SUBFLOW_NODE_HANDLE_TOP
  }
  const handles = sourceHandlesForNode(nodeType, config)
  const idx = Math.max(0, handles.indexOf(handleId))
  return IF_ELSE_HANDLE_BASE_TOP + idx * IF_ELSE_HANDLE_STEP
}

function resolveSubflowSourceAnchor(
  sourceNode: ContainerBodyNode,
  sourceHandle: string,
): { x: number; y: number } {
  const baseX = Number(sourceNode.positionX ?? 0)
  const baseY = Number(sourceNode.positionY ?? 0)
  const sourceSize = getSubflowNodeFrame(sourceNode.nodeType, sourceNode.config ?? null)
  const handleTop = resolveSubflowHandleTop(sourceNode.nodeType, sourceNode.config ?? null, sourceHandle)
  if (sourceNode.nodeType === 'if_else') {
    return {
      x: baseX + sourceSize.width - IF_ELSE_HANDLE_RIGHT_OFFSET,
      y: baseY + handleTop,
    }
  }
  return {
    x: baseX + sourceSize.width,
    y: baseY + handleTop,
  }
}

function resolveSubflowTargetAnchor(
  targetNode: ContainerBodyNode,
  targetHandle: string,
): { x: number; y: number } {
  const baseX = Number(targetNode.positionX ?? 0)
  const baseY = Number(targetNode.positionY ?? 0)
  const handleTop = resolveSubflowHandleTop(targetNode.nodeType, targetNode.config ?? null, targetHandle)
  return {
    x: baseX,
    y: baseY + handleTop,
  }
}

function buildSubflowEdgeAnchorOverride(
  sourceNode: ContainerBodyNode,
  targetNode: ContainerBodyNode,
  sourceHandle: string,
  targetHandle: string,
): SubflowEdgeAnchorOverride {
  const sourceAnchor = resolveSubflowSourceAnchor(sourceNode, sourceHandle)
  const targetAnchor = resolveSubflowTargetAnchor(targetNode, targetHandle)
  return {
    sourceX: sourceAnchor.x,
    sourceY: sourceAnchor.y,
    targetX: targetAnchor.x,
    targetY: targetAnchor.y,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  }
}

function resolveNonOverlappingPosition(initial: { x: number; y: number }, nodes: ContainerBodyNode[]): { x: number; y: number } {
  let y = Math.round(initial.y)
  for (let i = 0; i < 32; i += 1) {
    const collided = nodes.some((node) => {
      const x = Number(node.positionX ?? 0)
      const yy = Number(node.positionY ?? 0)
      return Math.abs(x - initial.x) < NODE_COLLISION_X && Math.abs(yy - y) < NODE_COLLISION_Y
    })
    if (!collided) {
      return { x: Math.round(initial.x), y }
    }
    y += QUICK_ADD_Y_STEP
  }
  return { x: Math.round(initial.x), y }
}

function SubflowFitViewSync({
  signature,
  fitNonce,
  fitReason,
}: {
  signature: string
  fitNonce: number
  fitReason: SubflowFitReason
}) {
  const { fitView, getNodes } = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  useEffect(() => {
    let raf1 = 0
    let raf2 = 0
    let raf3 = 0
    let timer120 = 0
    let timer260 = 0

    const refreshNodeInternals = () => {
      getNodes().forEach((node) => {
        updateNodeInternals(node.id)
      })
    }

    const runFitView = (padding: number) => fitView({
      padding,
      includeHiddenNodes: true,
      maxZoom: 1,
    })

    raf1 = requestAnimationFrame(() => {
      refreshNodeInternals()
      raf2 = requestAnimationFrame(() => {
        void runFitView(fitReason === 'delete-node' ? 0.12 : 0.08).finally(() => {
          raf3 = requestAnimationFrame(() => {
            refreshNodeInternals()
          })
        })
      })
    })

    if (fitReason === 'delete-node') {
      timer120 = window.setTimeout(() => {
        refreshNodeInternals()
        void runFitView(0.16)
      }, 120)
      timer260 = window.setTimeout(() => {
        refreshNodeInternals()
        void runFitView(0.16)
      }, 260)
    }

    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      cancelAnimationFrame(raf3)
      window.clearTimeout(timer120)
      window.clearTimeout(timer260)
    }
  }, [fitNonce, fitReason, fitView, getNodes, signature, updateNodeInternals])
  return null
}

function SubflowNodeCard({ id, data, selected }: SubflowNodeComponentProps) {
  const updateNodeInternals = useUpdateNodeInternals()
  const style = NODE_STYLES[data.nodeType] ?? NODE_STYLES.llm
  const Icon = NODE_ICON_MAP[data.nodeType] ?? Play
  const isStart = data.nodeType === 'start'
  const isIfElse = data.nodeType === 'if_else'
  const isHumanInLoop = data.nodeType === 'human_in_loop'
  const preview = getSubflowPreview(data.nodeType, data.config, data.workflows)
  const previewText = preview || '\u00A0'
  const ifElseConfig = isIfElse ? normalizeIfElseConfig((data.config ?? {}) as Record<string, unknown>) : null
  const branchHandles = isIfElse
    ? (ifElseConfig
      ? [...ifElseConfig.branches.map((item) => item.id), ifElseConfig.elseHandle || 'else']
      : [])
    : isHumanInLoop
      ? ['approved', 'rejected']
      : []
  const elseHandleId = ifElseConfig?.elseHandle || 'else'
  const quickAddHandleSet = new Set(data.quickAddHandles ?? [])
  const floatingUiEpoch = Number(data.floatingUiEpoch ?? 0)
  const [openQuickAddHandle, setOpenQuickAddHandle] = useState<string | null>(null)
  const pointerDownRef = useRef<{ handleId: string; x: number; y: number } | null>(null)
  const ifElseHandleStartTop = resolveSubflowHandleTop(data.nodeType, data.config ?? null, branchHandles[0] ?? elseHandleId)
  const ifElseMinHeight = (isIfElse || isHumanInLoop)
    ? Math.max(126, ifElseHandleStartTop + Math.max(1, branchHandles.length) * IF_ELSE_HANDLE_STEP + 24)
    : null

  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      updateNodeInternals(id)
    })
    return () => cancelAnimationFrame(raf)
  }, [id, ifElseMinHeight, branchHandles.length, updateNodeInternals, data.config])

  useEffect(() => {
    setOpenQuickAddHandle(null)
  }, [floatingUiEpoch])

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

  const renderQuickAddPopover = (
    handleId: string,
    side: 'left' | 'right',
    anchorStyle: CSSProperties,
  ) => {
    if (!data.onQuickAdd || !quickAddHandleSet.has(handleId)) return null
    return (
      <QuickAddPopover
        scope="container"
        tools={data.tools}
        workflows={data.workflows}
        open={openQuickAddHandle === handleId}
        onOpenChange={(nextOpen) => setOpenQuickAddHandle(nextOpen ? handleId : null)}
        side={side}
        align="center"
        sideOffset={12}
        onSelect={(payload) => {
          data.onQuickAdd?.(id, handleId, payload)
          setOpenQuickAddHandle(null)
        }}
        anchor={(
          <div
            className="pointer-events-none absolute z-[15] flex h-6 w-6 items-center justify-center rounded-full border border-white/90 bg-blue-600 text-white ring-2 ring-white shadow-[0_2px_8px_rgba(37,99,235,0.22)] opacity-0 group-hover/subflow-node:opacity-100 transition-opacity duration-150"
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
      className={`group/subflow-node relative w-[240px] rounded-xl bg-white shadow-sm border ${selected ? 'ring-2 ring-primary border-primary shadow-md' : 'border-border hover:shadow-md'}`}
      style={{ minHeight: ifElseMinHeight ? `${ifElseMinHeight}px` : undefined }}
    >
      <div className={`flex items-center gap-2 px-3 py-2 rounded-t-xl ${style.header}`}>
        <div className={`p-1 rounded-md bg-white/80 ${style.iconColor}`}>
          <Icon className="w-3.5 h-3.5" />
        </div>
        <span className="text-xs font-semibold text-foreground/80 truncate flex-1">
          {data.label || data.nodeType}
        </span>
      </div>
      <div className="px-3 py-3 min-h-[50px]">
        <p className={`text-[11px] leading-relaxed text-muted-foreground line-clamp-3 ${preview ? '' : 'italic opacity-50'}`}>
          {previewText || 'No configuration'}
        </p>
      </div>

      {!isStart && (
        <>
          <Handle
            type="target"
            position={Position.Left}
            id="input"
            style={{ top: `${SUBFLOW_NODE_HANDLE_TOP}px` }}
            className="!w-2.5 !h-2.5 !bg-background !border !border-slate-300/90 hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150"
            onPointerDown={(event) => handleHandlePointerDown('input', event)}
            onPointerUp={(event) => handleHandlePointerUp('input', event)}
            onPointerCancel={handleHandlePointerCancel}
          />
          {renderQuickAddPopover('input', 'left', {
            left: 0,
            top: `${SUBFLOW_NODE_HANDLE_TOP}px`,
            transform: 'translate(-50%, -50%)',
          })}
        </>
      )}

      {!isIfElse && !isHumanInLoop && (
        <>
          <Handle
            type="source"
            position={Position.Right}
            id="output"
            style={{ top: `${SUBFLOW_NODE_HANDLE_TOP}px` }}
            className="!w-2.5 !h-2.5 !bg-background !border !border-slate-300/90 hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150"
            onPointerDown={(event) => handleHandlePointerDown('output', event)}
            onPointerUp={(event) => handleHandlePointerUp('output', event)}
            onPointerCancel={handleHandlePointerCancel}
          />
          {renderQuickAddPopover('output', 'right', {
            right: 0,
            top: `${SUBFLOW_NODE_HANDLE_TOP}px`,
            transform: 'translate(50%, -50%)',
          })}
        </>
      )}

      {(isIfElse || isHumanInLoop) && (
        <>
          {branchHandles.map((handle) => {
            const handleTop = resolveSubflowHandleTop(data.nodeType, data.config ?? null, handle)
            const isElse = handle === elseHandleId
            const isApproved = handle === 'approved'
            const handleBorderClass = isIfElse
              ? (isElse ? '!border-stone-400' : '!border-green-500')
              : (isApproved ? '!border-green-500' : '!border-rose-500')
            return (
              <Fragment key={handle}>
                <Handle
                  type="source"
                  position={Position.Right}
                  id={handle}
                  style={{
                    top: `${handleTop}px`,
                    right: `${IF_ELSE_HANDLE_RIGHT_OFFSET}px`,
                  }}
                  className={`!w-2.5 !h-2.5 !bg-background !border ${handleBorderClass} hover:!border-blue-500 hover:!bg-blue-50 transition-colors duration-150`}
                  onPointerDown={(event) => handleHandlePointerDown(handle, event)}
                  onPointerUp={(event) => handleHandlePointerUp(handle, event)}
                  onPointerCancel={handleHandlePointerCancel}
                />
                {renderQuickAddPopover(handle, 'right', {
                  right: `${IF_ELSE_HANDLE_RIGHT_OFFSET}px`,
                  top: `${handleTop}px`,
                  transform: 'translate(50%, -50%)',
                })}
              </Fragment>
            )
          })}
        </>
      )}
    </div>
  )
}

const subflowNodeTypes = {
  subflowNode: memo(SubflowNodeCard),
}

function toReactFlowNodes(
  nodes: ContainerBodyNode[],
  quickAddHandleMap: Map<string, string[]>,
  onRemoveNode: (nodeId: string) => void,
  onQuickAddNode: ((nodeId: string, handleId: string, payload: QuickAddPayload) => void) | undefined,
  tools: WorkflowToolDefinition[],
  workflows: CallableWorkflowDefinition[],
  floatingUiEpoch: number,
  readOnly: boolean,
): Node<SubflowNodeData>[] {
  return nodes.map((item, index) => ({
    id: item.nodeId,
    type: 'subflowNode',
    position: {
      x: Number.isFinite(Number(item.positionX)) ? Number(item.positionX) : 26 + index * 260,
      y: Number.isFinite(Number(item.positionY)) ? Number(item.positionY) : 64,
    },
    data: {
      nodeType: item.nodeType,
      label: item.label || defaultLabelForNodeType(item.nodeType),
      config: item.config ?? null,
      removable: item.nodeType !== 'start',
      onRemove: onRemoveNode,
      quickAddHandles: readOnly ? [] : (quickAddHandleMap.get(item.nodeId) ?? []),
      onQuickAdd: onQuickAddNode,
      tools,
      workflows,
      floatingUiEpoch,
    },
    draggable: !readOnly && item.nodeType !== 'start',
  }))
}

function toReactFlowEdges(
  edges: ContainerBodyEdge[],
  bodyNodes: ContainerBodyNode[],
  onDeleteEdge: (edgeId: string) => void,
  onSelectEdge: (edgeId: string) => void,
  selectedEdgeId: string | null,
): Edge[] {
  const nodeById = new Map(bodyNodes.map((node) => [node.nodeId, node]))
  return edges
    .filter((item) => nodeById.has(item.sourceNodeId) && nodeById.has(item.targetNodeId))
    .map((item) => {
      const sourceNode = nodeById.get(item.sourceNodeId)
      const targetNode = nodeById.get(item.targetNodeId)
      const sourceHandle = normalizeSubflowSourceHandle(sourceNode, item.sourceHandle)
      const targetHandle = normalizeSubflowTargetHandle(item.targetHandle)
      return {
        id: item.edgeId,
        source: item.sourceNodeId,
        target: item.targetNodeId,
        sourceHandle,
        targetHandle,
        type: 'workflowBezier',
        selected: item.edgeId === selectedEdgeId,
        data: {
          onDelete: onDeleteEdge,
          onSelect: onSelectEdge,
          anchorOverride: sourceNode && targetNode
            ? buildSubflowEdgeAnchorOverride(sourceNode, targetNode, sourceHandle, targetHandle)
            : undefined,
        },
      }
    })
}

function toBodyNodes(nodes: Node<SubflowNodeData>[]): ContainerBodyNode[] {
  return nodes.map((item) => ({
    nodeId: item.id,
    nodeType: item.data.nodeType,
    label: item.data.label || defaultLabelForNodeType(item.data.nodeType),
    positionX: item.position.x,
    positionY: item.position.y,
    config: item.data.config ?? null,
  }))
}

function toBodyEdges(edges: Edge[], bodyNodes: ContainerBodyNode[]): ContainerBodyEdge[] {
  const nodeById = new Map(bodyNodes.map((node) => [node.nodeId, node]))
  return edges.map((item) => ({
    edgeId: item.id,
    sourceNodeId: item.source,
    targetNodeId: item.target,
    sourceHandle: normalizeSubflowSourceHandle(nodeById.get(item.source), item.sourceHandle),
    targetHandle: normalizeSubflowTargetHandle(item.targetHandle),
  }))
}

function toBodyNodeFromDraftNode(node: Node<SubflowNodeData>): ContainerBodyNode {
  return {
    nodeId: node.id,
    nodeType: node.data.nodeType,
    label: node.data.label || defaultLabelForNodeType(node.data.nodeType),
    positionX: node.position.x,
    positionY: node.position.y,
    config: node.data.config ?? null,
  }
}

function refreshDraftEdgeAnchors(
  edges: Edge[],
  nodes: Node<SubflowNodeData>[],
): Edge[] {
  if (edges.length === 0) return edges
  const nodeById = new Map(nodes.map((node) => [node.id, toBodyNodeFromDraftNode(node)]))
  return edges
    .filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target))
    .map((edge) => {
      const sourceNode = nodeById.get(edge.source)
      const targetNode = nodeById.get(edge.target)
      const sourceHandle = normalizeSubflowSourceHandle(sourceNode, edge.sourceHandle)
      const targetHandle = normalizeSubflowTargetHandle(edge.targetHandle)
      return {
        ...edge,
        sourceHandle,
        targetHandle,
        type: 'workflowBezier',
        data: {
          ...((edge.data ?? {}) as Record<string, unknown>),
          anchorOverride: sourceNode && targetNode
            ? buildSubflowEdgeAnchorOverride(sourceNode, targetNode, sourceHandle, targetHandle)
            : undefined,
        },
      }
    })
}

function estimateSubflowNodeHeight(node: Node<SubflowNodeData>): number {
  return getSubflowNodeFrame(node.data.nodeType, node.data.config ?? null).height
}

function resolveSubflowNodeExtent(
  nodes: Node<SubflowNodeData>[],
  canvasWidth: number | undefined,
  canvasHeight: number,
): SubflowNodeExtent {
  const maxNodeRight = nodes.reduce(
    (max, node) => Math.max(max, node.position.x + getSubflowNodeFrame(node.data.nodeType, node.data.config ?? null).width),
    getSubflowNodeFrame('start').width,
  )
  const maxNodeBottom = nodes.reduce(
    (max, node) => Math.max(max, node.position.y + estimateSubflowNodeHeight(node)),
    120,
  )
  const widthBase = Number.isFinite(Number(canvasWidth))
    ? Math.max(SOFT_BOUNDARY_MIN_WIDTH, Number(canvasWidth) - 64)
    : SOFT_BOUNDARY_MIN_WIDTH
  const heightBase = Math.max(SOFT_BOUNDARY_MIN_HEIGHT, canvasHeight)
  const maxX = Math.max(
    widthBase + SOFT_BOUNDARY_RIGHT_PADDING,
    maxNodeRight + SOFT_BOUNDARY_RIGHT_PADDING,
  )
  const maxY = Math.max(
    heightBase + SOFT_BOUNDARY_BOTTOM_PADDING,
    maxNodeBottom + SOFT_BOUNDARY_BOTTOM_PADDING,
  )
  return [
    [SOFT_BOUNDARY_LEFT, SOFT_BOUNDARY_TOP],
    [Math.round(maxX), Math.round(maxY)],
  ]
}

function clampPositionToExtent(
  position: { x: number; y: number },
  extent: SubflowNodeExtent,
): { x: number; y: number } {
  const [[minX, minY], [maxX, maxY]] = extent
  return {
    x: Math.min(maxX, Math.max(minX, Math.round(position.x))),
    y: Math.min(maxY, Math.max(minY, Math.round(position.y))),
  }
}

function hasStructuralNodeChanges(changes: Parameters<OnNodesChange>[0]): boolean {
  return changes.some((change) => {
    if (!change) return false
    return (
      change.type === 'remove' ||
      change.type === 'add' ||
      change.type === 'replace'
    )
  })
}

function hasPositionCommitChanges(changes: Parameters<OnNodesChange>[0]): boolean {
  return changes.some((change) => (
    change?.type === 'position' &&
    Boolean(change.position) &&
    change.dragging === false
  ))
}

function shouldPersistEdgeChanges(changes: Parameters<OnEdgesChange>[0]): boolean {
  return changes.some((change) => {
    if (!change) return false
    return (
      change.type === 'remove' ||
      change.type === 'add' ||
      change.type === 'replace'
    )
  })
}

function createBodyNodeId(nodeType: ContainerBodyNodeType): string {
  return `${nodeType}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
}

function createBodyEdge(params: {
  sourceNodeId: string
  targetNodeId: string
  sourceHandle?: string
  targetHandle?: string
}): ContainerBodyEdge {
  return {
    edgeId: `edge_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    sourceNodeId: params.sourceNodeId,
    targetNodeId: params.targetNodeId,
    sourceHandle: params.sourceHandle || 'output',
    targetHandle: params.targetHandle || 'input',
  }
}

export function ContainerSubflowCanvas({
  bodyNodes,
  bodyEdges,
  tools,
  workflows,
  floatingUiEpoch = 0,
  canvasHeight = 168,
  canvasWidth,
  readOnly = false,
  onSelectionChange,
  onChange,
}: {
  bodyNodes: ContainerBodyNode[]
  bodyEdges: ContainerBodyEdge[]
  tools: WorkflowToolDefinition[]
  workflows: CallableWorkflowDefinition[]
  floatingUiEpoch?: number
  canvasHeight?: number
  canvasWidth?: number
  readOnly?: boolean
  onSelectionChange?: (selection: { nodeId: string | null; edgeId: string | null }) => void
  onChange: (nodes: ContainerBodyNode[], edges: ContainerBodyEdge[]) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [fitNonce, setFitNonce] = useState(0)
  const fitReasonRef = useRef<SubflowFitReason>('structure')
  const requestFit = useCallback((reason: SubflowFitReason) => {
    fitReasonRef.current = reason
    setFitNonce((current) => current + 1)
  }, [])

  const persist = useCallback(
    (nodes: Node<SubflowNodeData>[], edges: Edge[]) => {
      if (readOnly) return
      const nextBodyNodes = toBodyNodes(nodes)
      onChange(nextBodyNodes, toBodyEdges(edges, nextBodyNodes))
    },
    [onChange, readOnly],
  )

  const quickAddHandleMap = useMemo(() => {
    const map = new Map<string, string[]>()
    bodyNodes.forEach((node) => {
      map.set(node.nodeId, quickAddHandlesForNode(node.nodeType, (node.config ?? {}) as Record<string, unknown>))
    })
    return map
  }, [bodyNodes])

  const handleQuickAdd = useCallback(
    (anchorNodeId: string, anchorHandle: string, payload: QuickAddPayload) => {
      if (readOnly) return
      const currentBodyNodes = toBodyNodes(draftNodesRef.current)
      const currentBodyEdges = toBodyEdges(draftEdgesRef.current, currentBodyNodes)
      const anchorNode = currentBodyNodes.find((node) => node.nodeId === anchorNodeId)
      if (!anchorNode) return
      const isInputSide = anchorHandle === 'input'

      const sourceX = Number(anchorNode.positionX ?? 0)
      const sourceY = Number(anchorNode.positionY ?? 0)
      const relativeTop = resolveSubflowHandleTop(
        anchorNode.nodeType,
        (anchorNode.config ?? {}) as Record<string, unknown>,
        anchorHandle,
      )
      const initial = {
        x: sourceX + (isInputSide ? -QUICK_ADD_X_OFFSET : QUICK_ADD_X_OFFSET),
        y: sourceY + relativeTop - 24,
      }
      const position = resolveNonOverlappingPosition(initial, currentBodyNodes)

      const nextNodeType = payload.kind === 'tool'
        ? 'tool'
        : payload.kind === 'workflow'
          ? 'workflow_call'
          : (payload.nodeType as ContainerBodyNodeType)
      if (!['llm', 'tool', 'if_else', 'parameter_extractor', 'knowledge_retrieval', 'code_executor', 'http_request', 'variable_assign', 'human_in_loop', 'workflow_call'].includes(nextNodeType)) {
        return
      }
      const nodeId = createBodyNodeId(nextNodeType)

      const newNode = (() => {
        if (payload.kind === 'tool') {
          const toolDef = tools.find((item) => item.name === payload.toolName)
          return createSubflowNode({
            nodeId,
            nodeType: 'tool',
            positionX: position.x,
            positionY: position.y,
            label: toolDef?.displayName ?? payload.toolName,
            toolName: payload.toolName,
            toolDef,
          })
        }
        if (payload.kind === 'workflow') {
          const workflowDef = workflows.find((item) => item.id === payload.workflowId)
          if (!workflowDef) return null
          return createSubflowNode({
            nodeId,
            nodeType: 'workflow_call',
            positionX: position.x,
            positionY: position.y,
            label: workflowDef.name,
            workflowDef,
          })
        }
        return createSubflowNode({
          nodeId,
          nodeType: nextNodeType,
          positionX: position.x,
          positionY: position.y,
        })
      })()
      if (!newNode) return

      onChange(
        [...currentBodyNodes, newNode],
        [
          ...currentBodyEdges,
          isInputSide
            ? createBodyEdge({
              sourceNodeId: nodeId,
              targetNodeId: anchorNodeId,
              sourceHandle: 'output',
              targetHandle: 'input',
            })
            : createBodyEdge({
              sourceNodeId: anchorNodeId,
              targetNodeId: nodeId,
              sourceHandle: anchorHandle,
              targetHandle: 'input',
            }),
        ],
      )
    },
    [onChange, readOnly, tools, workflows],
  )

  const handleRemoveNode = useCallback(
    (nodeId: string) => {
      if (readOnly) return
      if (nodeId === 'start') return
      const nextNodes = draftNodesRef.current.filter((item) => item.id !== nodeId)
      const nextEdges = refreshDraftEdgeAnchors(
        draftEdgesRef.current.filter((item) => item.source !== nodeId && item.target !== nodeId),
        nextNodes,
      )
      setDraftNodes(nextNodes)
      setDraftEdges(nextEdges)
      draftNodesRef.current = nextNodes
      draftEdgesRef.current = nextEdges
      setSelectedNodeId((prev) => (prev && nextNodes.some((node) => node.id === prev) ? prev : null))
      setSelectedEdgeId((prev) => (prev && !nextEdges.some((edge) => edge.id === prev) ? null : prev))
      requestFit('delete-node')
      persist(nextNodes, nextEdges)
    },
    [persist, readOnly, requestFit],
  )

  const flowNodes = useMemo(
    () => toReactFlowNodes(
      bodyNodes,
      quickAddHandleMap,
      handleRemoveNode,
      readOnly ? undefined : handleQuickAdd,
      tools,
      workflows,
      floatingUiEpoch,
      readOnly,
    ),
    [quickAddHandleMap, bodyNodes, floatingUiEpoch, handleQuickAdd, handleRemoveNode, readOnly, tools, workflows],
  )

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      if (readOnly) return
      const nextEdges = refreshDraftEdgeAnchors(
        draftEdgesRef.current.filter((item) => item.id !== edgeId),
        draftNodesRef.current,
      )
      setDraftEdges(nextEdges)
      draftEdgesRef.current = nextEdges
      requestFit('structure')
      persist(draftNodesRef.current, nextEdges)
      setSelectedEdgeId((prev) => (prev === edgeId ? null : prev))
    },
    [persist, readOnly, requestFit],
  )

  const flowEdges = useMemo(
    () => toReactFlowEdges(
      bodyEdges,
      bodyNodes,
      readOnly ? () => {} : handleDeleteEdge,
      setSelectedEdgeId,
      selectedEdgeId,
    ).map((edge) => ({
      ...edge,
      data: {
        ...(edge.data && typeof edge.data === 'object' ? edge.data : {}),
        onDelete: readOnly ? undefined : (edge.data as { onDelete?: (edgeId: string) => void } | undefined)?.onDelete,
      },
    })),
    [bodyEdges, bodyNodes, handleDeleteEdge, readOnly, selectedEdgeId],
  )

  const syncSignature = useMemo(
    () => [
      ...flowNodes.map((node) => `${node.id}:${Math.round(node.position.x)}:${Math.round(node.position.y)}`),
      ...flowEdges.map((edge) => `${edge.id}:${edge.source}:${edge.sourceHandle ?? 'output'}:${edge.target}:${edge.targetHandle ?? 'input'}`),
    ].join('|'),
    [flowEdges, flowNodes],
  )
  const [draftNodes, setDraftNodes] = useState<Node<SubflowNodeData>[]>(flowNodes)
  const [draftEdges, setDraftEdges] = useState<Edge[]>(flowEdges)
  const draftNodesRef = useRef<Node<SubflowNodeData>[]>(flowNodes)
  const draftEdgesRef = useRef<Edge[]>(flowEdges)
  const syncedSignatureRef = useRef<string>('')

  useEffect(() => {
    if (syncedSignatureRef.current === syncSignature) return
    syncedSignatureRef.current = syncSignature
    setDraftNodes(flowNodes)
    setDraftEdges(flowEdges)
    draftNodesRef.current = flowNodes
    draftEdgesRef.current = flowEdges
  }, [flowEdges, flowNodes, syncSignature])

  useEffect(() => {
    draftNodesRef.current = draftNodes
  }, [draftNodes])

  useEffect(() => {
    draftEdgesRef.current = draftEdges
  }, [draftEdges])

  useEffect(() => {
    setSelectedNodeId((prev) => (prev && draftNodes.some((node) => node.id === prev) ? prev : null))
  }, [draftNodes])

  useEffect(() => {
    setSelectedEdgeId((prev) => (prev && draftEdges.some((edge) => edge.id === prev) ? prev : null))
  }, [draftEdges])

  useEffect(() => {
    onSelectionChange?.({
      nodeId: selectedNodeId,
      edgeId: selectedEdgeId,
    })
  }, [onSelectionChange, selectedEdgeId, selectedNodeId])

  const nodeExtent = useMemo(
    () => resolveSubflowNodeExtent(draftNodes, canvasWidth, canvasHeight),
    [canvasHeight, canvasWidth, draftNodes],
  )

  const fitSignature = useMemo(
    () => [
      fitNonce,
      canvasHeight,
      Math.round(Number(canvasWidth) || 0),
      ...draftNodes.map((node) => `${node.id}:${Math.round(node.position.x)}:${Math.round(node.position.y)}`),
      ...draftEdges.map((edge) => `${edge.id}:${edge.source}:${edge.sourceHandle ?? 'output'}:${edge.target}:${edge.targetHandle ?? 'input'}`),
    ].join('|'),
    [canvasHeight, canvasWidth, draftEdges, draftNodes, fitNonce],
  )

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      if (readOnly) return
      const normalizedChanges = changes.map((change) => {
        if (change.type !== 'position' || !change.position) return change
        const position = clampPositionToExtent(change.position, nodeExtent)
        return {
          ...change,
          position,
          positionAbsolute: change.positionAbsolute
            ? clampPositionToExtent(change.positionAbsolute, nodeExtent)
            : change.positionAbsolute,
        }
      })
      const next = applyNodeChanges(normalizedChanges, draftNodesRef.current)
      const nextNodes = next as Node<SubflowNodeData>[]
      const nextEdges = refreshDraftEdgeAnchors(draftEdgesRef.current, nextNodes)
      setDraftNodes(nextNodes)
      setDraftEdges(nextEdges)
      draftNodesRef.current = nextNodes
      draftEdgesRef.current = nextEdges
      const hasStructural = hasStructuralNodeChanges(normalizedChanges)
      const hasPositionCommit = hasPositionCommitChanges(normalizedChanges)
      if (!hasStructural && !hasPositionCommit) return
      if (hasStructural) {
        requestFit('structure')
      }
      persist(nextNodes, nextEdges)
    },
    [nodeExtent, persist, readOnly, requestFit],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      if (readOnly) return
      const next = applyEdgeChanges(changes, draftEdgesRef.current)
      const nextEdges = refreshDraftEdgeAnchors(next, draftNodesRef.current)
      setDraftEdges(nextEdges)
      draftEdgesRef.current = nextEdges
      if (!shouldPersistEdgeChanges(changes)) return
      requestFit('structure')
      persist(draftNodesRef.current, nextEdges)
    },
    [persist, readOnly, requestFit],
  )

  const onConnect: OnConnect = useCallback(
    (params) => {
      if (readOnly) return
      if (!params.source || !params.target) return
      const currentBodyNodes = toBodyNodes(draftNodesRef.current)
      const sourceNode = currentBodyNodes.find((node) => node.nodeId === params.source)
      const edge: Edge = {
        id: `edge_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
        source: params.source,
        target: params.target,
        sourceHandle: normalizeSubflowSourceHandle(sourceNode, params.sourceHandle),
        targetHandle: normalizeSubflowTargetHandle(params.targetHandle),
        type: 'workflowBezier',
        data: {
          onDelete: handleDeleteEdge,
          onSelect: setSelectedEdgeId,
        },
      }
      const next = addEdge(edge, draftEdgesRef.current)
      const nextEdges = refreshDraftEdgeAnchors(next, draftNodesRef.current)
      setDraftEdges(nextEdges)
      draftEdgesRef.current = nextEdges
      persist(draftNodesRef.current, nextEdges)
    },
    [handleDeleteEdge, persist, readOnly],
  )

  const isEditableElement = useCallback((target: EventTarget | Element | null): boolean => {
    if (!(target instanceof Element)) return false
    if (target instanceof HTMLElement && target.isContentEditable) return true
    return Boolean(target.closest('input, textarea, select, [contenteditable], [role="textbox"]'))
  }, [])

  const handleSubflowDeleteKey = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (readOnly) return
      const isDeleteKey = event.key === 'Delete' || event.key === 'Backspace'
      if (!isDeleteKey) return
      if (isEditableElement(event.target) || isEditableElement(document.activeElement)) return

      if (selectedNodeId) {
        const selectedNode = draftNodesRef.current.find((node) => node.id === selectedNodeId)
        if (selectedNode) {
          event.preventDefault()
          event.stopPropagation()
          if (selectedNode.data.nodeType === 'start') return
          handleRemoveNode(selectedNodeId)
          return
        }
      }

      if (selectedEdgeId && draftEdgesRef.current.some((edge) => edge.id === selectedEdgeId)) {
        event.preventDefault()
        event.stopPropagation()
        handleDeleteEdge(selectedEdgeId)
      }
    },
    [handleDeleteEdge, handleRemoveNode, isEditableElement, readOnly, selectedEdgeId, selectedNodeId],
  )

  const handleContainerPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.stopPropagation()
    containerRef.current?.focus({ preventScroll: true })
  }, [])

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      className="rounded-xl border bg-card/50 p-2.5 outline-none"
      onPointerDown={handleContainerPointerDown}
      onClick={(event) => event.stopPropagation()}
      onKeyDownCapture={handleSubflowDeleteKey}
    >
      <div className="rounded-xl border bg-background overflow-hidden" style={{ height: `${canvasHeight}px` }}>
        <ReactFlowProvider>
          <ReactFlow
            nodes={draftNodes}
            edges={draftEdges}
            nodeTypes={subflowNodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(event, node) => {
              event.stopPropagation()
              setSelectedNodeId(node.id)
              setSelectedEdgeId(null)
            }}
            onEdgeClick={(event, edge) => {
              event.stopPropagation()
              setSelectedEdgeId(edge.id)
              setSelectedNodeId(null)
            }}
            onPaneClick={(event) => {
              event.stopPropagation()
              setSelectedNodeId(null)
              setSelectedEdgeId(null)
            }}
            defaultEdgeOptions={{
              type: 'workflowBezier',
              animated: false,
              style: { strokeWidth: 2.5, strokeLinecap: 'round' },
            }}
            nodesConnectable={!readOnly}
            nodesDraggable={!readOnly}
            elementsSelectable
            nodeExtent={nodeExtent}
            panOnDrag={false}
            zoomOnScroll={false}
            zoomOnPinch={false}
            zoomOnDoubleClick={false}
            preventScrolling={false}
            minZoom={0.2}
            maxZoom={1}
            deleteKeyCode={null}
            proOptions={{ hideAttribution: true }}
          >
            <SubflowFitViewSync
              signature={fitSignature}
              fitNonce={fitNonce}
              fitReason={fitReasonRef.current}
            />
            <Background gap={24} size={1} />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </div>
  )
}
