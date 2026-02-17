import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react'
import {
  Play,
  Brain,
  Wrench,
  GitBranch,
  ScanSearch,
  BookOpen,
  RefreshCw,
  Infinity,
  Plus,
} from 'lucide-react'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { useWorkflowEditorStore } from '../../stores/workflow-editor-store'
import type { ContainerBodyNodeType, NodeType } from '../../api/workflow'
import { normalizeIfElseConfig } from './ifElseConfig'
import { defaultLabelForNodeType } from './labelUtils'
import { ContainerSubflowCanvas } from './ContainerSubflowCanvas'
import { estimateContainerNodeSizeFromConfig } from './containerLayout'
import type { WorkflowToolDefinition } from './types'
import { QuickAddPopover, type QuickAddPayload } from './QuickAddPopover'

const NODE_STYLES: Record<NodeType, { header: string; icon: typeof Play; iconColor: string }> = {
  start: { header: 'bg-green-50 border-b border-green-100', icon: Play, iconColor: 'text-green-600' },
  llm: { header: 'bg-purple-50 border-b border-purple-100', icon: Brain, iconColor: 'text-purple-600' },
  tool: { header: 'bg-sky-50 border-b border-sky-100', icon: Wrench, iconColor: 'text-sky-600' },
  if_else: { header: 'bg-yellow-50 border-b border-yellow-100', icon: GitBranch, iconColor: 'text-yellow-600' },
  parameter_extractor: { header: 'bg-pink-50 border-b border-pink-100', icon: ScanSearch, iconColor: 'text-pink-600' },
  knowledge_retrieval: { header: 'bg-teal-50 border-b border-teal-100', icon: BookOpen, iconColor: 'text-teal-600' },
  iteration: { header: 'bg-cyan-50 border-b border-cyan-100', icon: RefreshCw, iconColor: 'text-cyan-600' },
  loop: { header: 'bg-blue-50 border-b border-blue-100', icon: Infinity, iconColor: 'text-blue-600' },
}
const HANDLE_TOP_OFFSET = 28
const CONTAINER_HANDLE_TOP = 20
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
  switch (data.nodeType) {
    case 'llm':
      return (cfg.isOutput ? '[Output] ' : '') + truncate(cfg.systemPrompt as string, 50)
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
  const nodeData = data as unknown as WfNodeData
  const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)
  const updateNodeConfig = useWorkflowEditorStore((s) => s.updateNodeConfig)
  const setSelectedSubflowSelection = useWorkflowEditorStore((s) => s.setSelectedSubflowSelection)
  const updateNodeInternals = useUpdateNodeInternals()
  const isSelected = selectedNodeId === id
  const style = NODE_STYLES[nodeData.nodeType] ?? NODE_STYLES.llm
  const Icon = style.icon
  const preview = getPreview(nodeData)
  const previewText = preview || '\u00A0'
  const isStart = nodeData.nodeType === 'start'
  const isIfElse = nodeData.nodeType === 'if_else'
  const isContainer = nodeData.nodeType === 'iteration' || nodeData.nodeType === 'loop'
  const containerConfig = ((nodeData.config ?? {}) as Record<string, unknown>)
  const bodyNodes = isContainer ? normalizeContainerBodyNodes(containerConfig) : []
  const bodyEdges = isContainer ? normalizeContainerBodyEdges(containerConfig) : []
  const containerBodySignature = useMemo(
    () => buildContainerBodySignature(bodyNodes, bodyEdges),
    [bodyEdges, bodyNodes],
  )
  const containerSize = isContainer ? estimateContainerNodeSizeFromConfig(containerConfig) : null
  const quickAddHandles = Array.isArray((nodeData as { quickAddHandles?: unknown }).quickAddHandles)
    ? ((nodeData as { quickAddHandles?: unknown[] }).quickAddHandles ?? [])
      .filter((item): item is string => typeof item === 'string')
    : []
  const quickAddHandleSet = new Set(quickAddHandles)
  const quickAddTools = Array.isArray((nodeData as { quickAddTools?: unknown }).quickAddTools)
    ? ((nodeData as { quickAddTools?: WorkflowToolDefinition[] }).quickAddTools ?? [])
    : []
  const onQuickAdd = typeof (nodeData as { onQuickAdd?: unknown }).onQuickAdd === 'function'
    ? ((nodeData as { onQuickAdd?: (nodeId: string, handleId: string, payload: QuickAddPayload) => void }).onQuickAdd ?? null)
    : null
  const [openQuickAddHandle, setOpenQuickAddHandle] = useState<string | null>(null)
  const pointerDownRef = useRef<{ handleId: string; x: number; y: number } | null>(null)
  const nodeHandleTop = isContainer ? CONTAINER_HANDLE_TOP : HANDLE_TOP_OFFSET
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
    nodeHandleTop,
    updateNodeInternals,
    containerSize?.height,
    containerSize?.width,
  ])

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
            className="pointer-events-none absolute z-[15] flex h-6 w-6 items-center justify-center rounded-full border-2 border-white bg-blue-600 text-white shadow-md opacity-0 group-hover/workflow-node:opacity-100 transition-opacity"
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
        group/workflow-node relative ${isContainer ? '' : 'w-[240px]'} rounded-xl bg-white shadow-sm border transaction-all duration-200
        ${isSelected ? 'ring-2 ring-primary border-primary shadow-md' : 'border-border hover:shadow-md'}
      `}
      style={{
        width: isContainer && containerSize ? `${containerSize.width}px` : undefined,
        minHeight: isIfElse
          ? `${50 + ((normalizeIfElseConfig(nodeData.config as any).branches.length + 1) * 28) + 12}px`
          : isContainer
            ? `${containerSize?.height ?? 248}px`
            : undefined
      }}
    >
      {/* Header */}
      <div className={`flex items-center gap-2 px-3 py-2 rounded-t-xl ${style.header}`}>
        <div className={`p-1 rounded-md bg-white/80 ${style.iconColor}`}>
          <Icon className="w-3.5 h-3.5" />
        </div>
        <span className="text-xs font-semibold text-foreground/80 truncate flex-1">
          {nodeData.label || nodeData.nodeType}
        </span>
      </div>

      {!isContainer && (
        <div className="px-3 py-3 min-h-[50px]">
          <p className={`text-[11px] leading-relaxed text-muted-foreground line-clamp-3 ${preview ? '' : 'italic opacity-50'}`}>
            {previewText || 'No configuration'}
          </p>
        </div>
      )}

      {isContainer && (
        <div className="px-3 pb-3 pt-2">
          <ContainerSubflowCanvas
            bodyNodes={bodyNodes}
            bodyEdges={bodyEdges}
            tools={quickAddTools}
            canvasHeight={containerSize?.canvasHeight ?? 168}
            canvasWidth={containerSize?.width}
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
            style={{ top: `${nodeHandleTop}px` }}
            className="!w-2.5 !h-2.5 !bg-white !border-2 !border-muted-foreground/50 hover:!border-primary transition-colors"
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
      {!isIfElse && (
        <>
          <Handle
            type="source"
            position={Position.Right}
            id={outputHandleId}
            style={{ top: `${nodeHandleTop}px` }}
            className="!w-2.5 !h-2.5 !bg-white !border-2 !border-muted-foreground/50 hover:!border-primary transition-colors"
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
              <div key={handle} className="relative flex items-center justify-center w-2.5 h-2.5">
                <Handle
                  type="source"
                  position={Position.Right}
                  id={handle}
                  style={{ position: 'static', transform: 'none' }}
                  className={`!w-2.5 !h-2.5 !bg-white !border-2 ${handle === (normalized.elseHandle || 'else') ? '!border-stone-400' : '!border-green-500'} hover:!scale-125 transition-all`}
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
    </div>
  )
}

export const WorkflowNode = memo(WorkflowNodeInner)
