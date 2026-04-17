import { useCallback, useRef, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  MiniMap,
  useReactFlow,
  type OnConnect,
  type OnNodesChange,
  type OnEdgesChange,
  type NodeTypes,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
} from '@xyflow/react'
import { useWorkflowEditorStore, type WfNodeData } from '../../stores/workflow-editor-store'
import { useWorkflowTestRunStore } from '../../stores/workflow-test-run-store'
import type { NodeType } from '../../api/workflow'
import { WorkflowNode } from './WorkflowNode'
import type { CallableWorkflowDefinition, WorkflowToolDefinition } from './types'
import { normalizeIfElseConfig } from './ifElseConfig'
import { WorkflowDeletableEdge } from './WorkflowDeletableEdge'
import { createMainFlowNode } from './nodeFactory'
import type { QuickAddPayload } from './QuickAddPopover'
import {
  CONTAINER_NODE_HANDLE_TOP,
  HUMAN_IN_LOOP_NODE_HEIGHT,
  IF_ELSE_HANDLE_BASE_TOP,
  IF_ELSE_HANDLE_STEP,
  MAIN_NODE_HANDLE_TOP,
  MAIN_NODE_WIDTH,
  estimateMainNodeSize,
} from './workflowGeometry'
import { FlowControls } from './FlowControls'

const nodeTypes: NodeTypes = {
  start: WorkflowNode,
  llm: WorkflowNode,
  agent: WorkflowNode,
  tool: WorkflowNode,
  workflow_call: WorkflowNode,
  if_else: WorkflowNode,
  parameter_extractor: WorkflowNode,
  knowledge_retrieval: WorkflowNode,
  iteration: WorkflowNode,
  loop: WorkflowNode,
  code_executor: WorkflowNode,
  http_request: WorkflowNode,
  variable_assign: WorkflowNode,
  human_in_loop: WorkflowNode,
  output: WorkflowNode,
}

const edgeTypes = {
  workflowBezier: WorkflowDeletableEdge,
}

let nodeCounter = 0

const QUICK_ADD_X_OFFSET = 220
const QUICK_ADD_Y_STEP = 88
const NODE_COLLISION_X = 140
const NODE_COLLISION_Y = 84
const CONTAINER_INPUT_HANDLE_ID = 'container_input'
const CONTAINER_OUTPUT_HANDLE_ID = 'container_output'

function quickAddHandlesForNode(node: Node<WfNodeData>): string[] {
  const outputs = sourceHandlesForNode(node)
  if (node.data.nodeType === 'start') return outputs
  return ['input', ...outputs]
}

function isContainerNodeType(nodeType: WfNodeData['nodeType']): boolean {
  return nodeType === 'iteration' || nodeType === 'loop'
}

function normalizeConnectionSourceHandle(
  sourceNode: Node<WfNodeData> | undefined,
  sourceHandle: string | null | undefined,
): string {
  const raw = String(sourceHandle ?? '').trim()
  if (!sourceNode) return raw || 'output'
  if (sourceNode.data.nodeType === 'if_else' || sourceNode.data.nodeType === 'human_in_loop') {
    const handles = sourceHandlesForNode(sourceNode)
    if (raw && handles.includes(raw)) return raw
    return handles[0] ?? raw ?? 'output'
  }
  if (isContainerNodeType(sourceNode.data.nodeType)) {
    if (!raw || raw === 'output' || raw === CONTAINER_OUTPUT_HANDLE_ID) return 'output'
    return raw
  }
  if (raw === CONTAINER_OUTPUT_HANDLE_ID) return 'output'
  return raw || 'output'
}

function normalizeConnectionTargetHandle(
  targetNode: Node<WfNodeData> | undefined,
  targetHandle: string | null | undefined,
): string {
  const raw = String(targetHandle ?? '').trim()
  if (!targetNode) return raw || 'input'
  if (isContainerNodeType(targetNode.data.nodeType)) {
    if (!raw || raw === 'input' || raw === CONTAINER_INPUT_HANDLE_ID) return 'input'
    return raw
  }
  if (raw === CONTAINER_INPUT_HANDLE_ID) return 'input'
  return raw || 'input'
}

function resolveRenderSourceHandle(edge: Edge, sourceNode: Node<WfNodeData> | undefined): string | undefined {
  if (!sourceNode) return edge.sourceHandle ?? 'output'
  if (sourceNode.data.nodeType === 'if_else' || sourceNode.data.nodeType === 'human_in_loop') {
    return edge.sourceHandle ?? undefined
  }
  if (sourceNode.data.nodeType === 'output') return undefined
  if (isContainerNodeType(sourceNode.data.nodeType)) return CONTAINER_OUTPUT_HANDLE_ID
  return edge.sourceHandle ?? 'output'
}

function resolveRenderTargetHandle(edge: Edge, targetNode: Node<WfNodeData> | undefined): string {
  if (targetNode && isContainerNodeType(targetNode.data.nodeType)) {
    return CONTAINER_INPUT_HANDLE_ID
  }
  return edge.targetHandle ?? 'input'
}

function sourceHandlesForNode(node: Node<WfNodeData>): string[] {
  if (node.data.nodeType === 'output') {
    return []
  }
  if (node.data.nodeType === 'if_else') {
    const normalized = normalizeIfElseConfig((node.data.config ?? {}) as Record<string, unknown>)
    return [...normalized.branches.map((branch) => branch.id), normalized.elseHandle || 'else']
  }
  if (node.data.nodeType === 'human_in_loop') {
    return ['approved', 'rejected']
  }
  return ['output']
}

function resolveHandleRelativeTop(node: Node<WfNodeData>, sourceHandle: string): number {
  if (sourceHandle === 'input') {
    return node.data.nodeType === 'iteration' || node.data.nodeType === 'loop'
      ? CONTAINER_NODE_HANDLE_TOP
      : MAIN_NODE_HANDLE_TOP
  }
  if (node.data.nodeType !== 'if_else' && node.data.nodeType !== 'human_in_loop') {
    return node.data.nodeType === 'iteration' || node.data.nodeType === 'loop'
      ? CONTAINER_NODE_HANDLE_TOP
      : MAIN_NODE_HANDLE_TOP
  }
  const handles = sourceHandlesForNode(node)
  const idx = Math.max(0, handles.indexOf(sourceHandle))
  return IF_ELSE_HANDLE_BASE_TOP + idx * IF_ELSE_HANDLE_STEP
}

function rectsOverlap(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
): boolean {
  return (
    a.x < b.x + b.width + NODE_COLLISION_X * 0.3 &&
    a.x + a.width > b.x - NODE_COLLISION_X * 0.3 &&
    a.y < b.y + b.height + NODE_COLLISION_Y * 0.2 &&
    a.y + a.height > b.y - NODE_COLLISION_Y * 0.2
  )
}

function resolveNonOverlappingPosition(
  initial: { x: number; y: number },
  newNodeSize: { width: number; height: number },
  nodes: Node<WfNodeData>[],
): { x: number; y: number } {
  let y = Math.round(initial.y)
  for (let i = 0; i < 32; i += 1) {
    const candidate = { x: Math.round(initial.x), y, width: newNodeSize.width, height: newNodeSize.height }
    const collided = nodes.some((node) => {
      const size = estimateMainNodeSize(node)
      const existing = { x: node.position.x, y: node.position.y, width: size.width, height: size.height }
      return rectsOverlap(candidate, existing)
    })
    if (!collided) {
      return { x: Math.round(initial.x), y }
    }
    y += QUICK_ADD_Y_STEP
  }
  return { x: Math.round(initial.x), y }
}

function createWorkflowEdge(params: {
  source: string
  target: string
  sourceHandle?: string
  targetHandle?: string
}): Edge {
  return {
    id: `edge_${Date.now()}_${++nodeCounter}`,
    source: params.source,
    target: params.target,
    sourceHandle: params.sourceHandle ?? 'output',
    targetHandle: params.targetHandle ?? 'input',
    type: 'workflowBezier',
  }
}

interface FlowCanvasProps {
  tools: WorkflowToolDefinition[]
  workflows: CallableWorkflowDefinition[]
  workflowDescription?: string
  readOnly?: boolean
  floatingUiEpoch?: number
}

type RuntimeNodeStatus = 'running' | 'success' | 'error'

const RUNTIME_STATUS_PRIORITY: Record<RuntimeNodeStatus, number> = {
  running: 3,
  error: 2,
  success: 1,
}

export function FlowCanvas({
  tools,
  workflows,
  workflowDescription,
  readOnly = false,
  floatingUiEpoch = 0,
}: FlowCanvasProps) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition, setCenter } = useReactFlow()
  const store = useWorkflowEditorStore()
  const [isInteractive, setIsInteractive] = useState(true)

  const nodeTraceMap = useWorkflowTestRunStore((s) => s.nodeTraceMap)
  const nodeMap = useMemo(
    () => new Map(store.nodes.map((node) => [node.id, node])),
    [store.nodes],
  )

  const runtimeStatusByNodeId = useMemo(() => {
    const merged: Record<string, RuntimeNodeStatus> = {}

    const mergeStatus = (nodeId: string, status: RuntimeNodeStatus) => {
      const current = merged[nodeId]
      if (!current || RUNTIME_STATUS_PRIORITY[status] > RUNTIME_STATUS_PRIORITY[current]) {
        merged[nodeId] = status
      }
    }

    Object.values(nodeTraceMap).forEach((trace) => {
      const mappedStatus = trace.status === 'running'
        ? 'running'
        : trace.status === 'error'
          ? 'error'
          : trace.status === 'success'
            ? 'success'
            : null
      if (!mappedStatus) return
      const rawNodeId = trace.nodeId
      const scopeIdx = rawNodeId.indexOf('::')
      if (scopeIdx > 0) {
        const containerId = rawNodeId.slice(0, scopeIdx)
        mergeStatus(containerId, mappedStatus)
        return
      }
      mergeStatus(rawNodeId, mappedStatus)
    })

    return merged
  }, [nodeTraceMap])

  const isEditable = isInteractive && !readOnly

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      if (readOnly) return
      store.removeEdge(edgeId)
    },
    [readOnly, store],
  )

  const handleSelectEdge = useCallback(
    (edgeId: string) => {
      store.setSelectedEdgeId(edgeId)
    },
    [store],
  )

  const edgesWithInteractions: Edge[] = useMemo(
    () =>
      store.edges.map((edge) => ({
        ...edge,
        type: 'workflowBezier',
        sourceHandle: resolveRenderSourceHandle(edge, nodeMap.get(edge.source)),
        targetHandle: resolveRenderTargetHandle(edge, nodeMap.get(edge.target)),
        selected: edge.id === store.selectedEdgeId,
        data: {
          ...(edge.data && typeof edge.data === 'object' ? edge.data : {}),
          onDelete: readOnly ? undefined : handleDeleteEdge,
          onSelect: handleSelectEdge,
        },
      })),
    [handleDeleteEdge, handleSelectEdge, nodeMap, readOnly, store.edges, store.selectedEdgeId],
  )

  const quickAddHandleMap = useMemo(() => {
    const map = new Map<string, string[]>()
    store.nodes.forEach((node) => {
      map.set(node.id, quickAddHandlesForNode(node))
    })
    return map
  }, [store.nodes])

  const handleQuickAdd = useCallback(
    (anchorNodeId: string, anchorHandle: string, payload: QuickAddPayload) => {
      if (readOnly) return
      const anchorNode = store.nodes.find((item) => item.id === anchorNodeId)
      if (!anchorNode) return
      const isInputSide = anchorHandle === 'input'

      const nextNodeId = (() => {
        if (payload.kind === 'tool') return `tool_${Date.now()}_${++nodeCounter}`
        if (payload.kind === 'workflow') return `workflow_call_${Date.now()}_${++nodeCounter}`
        const nodeType = payload.nodeType as NodeType
        if (!nodeType || nodeType === 'start') return null
        return `${nodeType}_${Date.now()}_${++nodeCounter}`
      })()
      if (!nextNodeId) return

      const draftNode = (() => {
        if (payload.kind === 'tool') {
          const toolDef = tools.find((item) => item.name === payload.toolName)
          return createMainFlowNode({
            id: nextNodeId,
            nodeType: 'tool',
            position: { x: 0, y: 0 },
            label: toolDef?.displayName ?? payload.toolName,
            toolName: payload.toolName,
            toolDef,
          })
        }
        if (payload.kind === 'workflow') {
          const workflowDef = workflows.find((item) => item.id === payload.workflowId)
          if (!workflowDef) return null
          return createMainFlowNode({
            id: nextNodeId,
            nodeType: 'workflow_call',
            position: { x: 0, y: 0 },
            label: workflowDef.name,
            workflowDef,
          })
        }
        const nodeType = payload.nodeType as NodeType
        if (!nodeType || nodeType === 'start') return null
        return createMainFlowNode({
          id: nextNodeId,
          nodeType,
          position: { x: 0, y: 0 },
        })
      })()
      if (!draftNode) return

      const anchorSize = estimateMainNodeSize(anchorNode)
      const draftSize = estimateMainNodeSize(draftNode)
      const anchorRelativeTop = resolveHandleRelativeTop(anchorNode, anchorHandle)
      const defaultSourceHandle =
        draftNode.data.nodeType === 'if_else'
          ? sourceHandlesForNode(draftNode)[0] ?? 'else'
          : 'output'
      if (isInputSide && draftNode.data.nodeType === 'output') return
      const draftSourceRelativeTop = resolveHandleRelativeTop(draftNode, defaultSourceHandle)
      const initial = {
        x: isInputSide
          ? anchorNode.position.x - QUICK_ADD_X_OFFSET - draftSize.width
          : anchorNode.position.x + anchorSize.width + QUICK_ADD_X_OFFSET,
        y: anchorNode.position.y + anchorRelativeTop - draftSourceRelativeTop,
      }
      const position = resolveNonOverlappingPosition(initial, draftSize, store.nodes)
      const nextNode: Node<WfNodeData> = {
        ...draftNode,
        position,
      }

      const latestStore = useWorkflowEditorStore.getState()
      latestStore.addNode(nextNode)
      latestStore.addEdge(
        isInputSide
          ? createWorkflowEdge({
            source: nextNodeId,
            target: anchorNodeId,
            sourceHandle: defaultSourceHandle,
            targetHandle: 'input',
          })
          : createWorkflowEdge({
            source: anchorNodeId,
            target: nextNodeId,
            sourceHandle: anchorHandle,
            targetHandle: 'input',
          }),
      )
    },
    [readOnly, store, tools, workflows],
  )

  const nodesWithRuntimeData: Node<WfNodeData>[] = useMemo(
    () =>
      store.nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          workflowDescription,
          runtimeStatus: runtimeStatusByNodeId[node.id],
          quickAddHandles: readOnly ? [] : (quickAddHandleMap.get(node.id) ?? []),
          onQuickAdd: readOnly ? undefined : handleQuickAdd,
          quickAddTools: tools,
          quickAddWorkflows: workflows,
          callableWorkflows: workflows,
          floatingUiEpoch,
          readOnly,
        },
      })),
    [floatingUiEpoch, handleQuickAdd, quickAddHandleMap, readOnly, runtimeStatusByNodeId, store.nodes, tools, workflowDescription, workflows],
  )

  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      store.setSelectedEdgeId(edge.id)
    },
    [store],
  )

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      if (readOnly) return
      const updated = applyNodeChanges(changes, store.nodes)
      store.setNodes(updated as Node<WfNodeData>[])
    },
    [readOnly, store],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      if (readOnly) return
      const updated = applyEdgeChanges(changes, store.edges)
      store.setEdges(updated)
    },
    [readOnly, store],
  )

  const onConnect: OnConnect = useCallback(
    (connection) => {
      if (readOnly) return
      if (!connection.source || !connection.target) return
      const sourceNode = nodeMap.get(connection.source)
      const targetNode = nodeMap.get(connection.target)
      if (sourceNode?.data.nodeType === 'output') return
      const normalizedSourceHandle = normalizeConnectionSourceHandle(sourceNode, connection.sourceHandle)
      const normalizedTargetHandle = normalizeConnectionTargetHandle(targetNode, connection.targetHandle)
      // Prevent duplicate edges
      const exists = store.edges.some(
        (e) =>
          e.source === connection.source &&
          e.target === connection.target &&
          normalizeConnectionSourceHandle(nodeMap.get(e.source), e.sourceHandle) === normalizedSourceHandle &&
          normalizeConnectionTargetHandle(nodeMap.get(e.target), e.targetHandle) === normalizedTargetHandle,
      )
      if (exists) return
      // Prevent self-connection
      if (connection.source === connection.target) return

      const edgeId = `edge_${Date.now()}_${++nodeCounter}`
      store.addEdge({
        id: edgeId,
        source: connection.source,
        target: connection.target,
        sourceHandle: normalizedSourceHandle,
        targetHandle: normalizedTargetHandle,
        type: 'workflowBezier',
      })
    },
    [nodeMap, readOnly, store],
  )

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      store.setSelectedNodeId(node.id)
    },
    [store],
  )

  const onPaneClick = useCallback(() => {
    store.setSelectedNodeId(null)
    store.setSelectedEdgeId(null)
  }, [store])

  const onDragOver = useCallback((e: React.DragEvent) => {
    if (readOnly) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [readOnly])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      if (readOnly) return
      e.preventDefault()

      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
      const toolPayload = e.dataTransfer.getData('application/workflow-tool-item')
      const workflowPayload = e.dataTransfer.getData('application/workflow-call-item')

      if (toolPayload) {
        try {
          const parsed = JSON.parse(toolPayload) as {
            nodeType?: string
            toolName?: string
            label?: string
          }
          if (parsed.nodeType === 'tool' && parsed.toolName) {
            const toolDef = tools.find((item) => item.name === parsed.toolName)
            const nodeId = `tool_${Date.now()}_${++nodeCounter}`
            store.addNode(createMainFlowNode({
              id: nodeId,
              nodeType: 'tool',
              position,
              label: parsed.label || parsed.toolName,
              toolName: parsed.toolName,
              toolDef,
            }))
            return
          }
        } catch {
          // fallthrough to normal node payload parsing
        }
      }

      if (workflowPayload) {
        try {
          const parsed = JSON.parse(workflowPayload) as {
            nodeType?: string
            workflowId?: string
            label?: string
          }
          if (parsed.nodeType === 'workflow_call' && parsed.workflowId) {
            const workflowDef = workflows.find((item) => item.id === parsed.workflowId)
            if (!workflowDef) return
            const nodeId = `workflow_call_${Date.now()}_${++nodeCounter}`
            store.addNode(createMainFlowNode({
              id: nodeId,
              nodeType: 'workflow_call',
              position,
              label: parsed.label || workflowDef.name,
              workflowDef,
            }))
            return
          }
        } catch {
          // fallthrough to normal node payload parsing
        }
      }

      const nodeType = e.dataTransfer.getData('application/workflow-node-type') as NodeType
      if (!nodeType) return
      const nodeId = `${nodeType}_${Date.now()}_${++nodeCounter}`

      store.addNode(createMainFlowNode({ id: nodeId, nodeType, position }))
    },
    [readOnly, screenToFlowPosition, store, tools, workflows],
  )

  const isEditableElement = (target: EventTarget | Element | null): boolean => {
    if (!(target instanceof Element)) return false
    if (target instanceof HTMLElement && target.isContentEditable) return true
    return Boolean(target.closest('input, textarea, select, [contenteditable], [role="textbox"]'))
  }

  const onDelete = useCallback(
    (e: KeyboardEvent) => {
      if (readOnly) return
      const isDeleteKey = e.key === 'Delete' || e.key === 'Backspace'
      if (!isDeleteKey) return

      // Never delete nodes/edges while user is editing any input/editor surface.
      const isEditingContext =
        isEditableElement(e.target) || isEditableElement(document.activeElement)
      if (isEditingContext) return

      if (store.selectedNodeId) {
        // Don't delete start node
        const node = store.nodes.find((n) => n.id === store.selectedNodeId)
        if (node?.data.nodeType === 'start') return
        e.preventDefault()
        store.removeNode(store.selectedNodeId)
      } else if (store.selectedEdgeId) {
        e.preventDefault()
        store.removeEdge(store.selectedEdgeId)
      }
    },
    [readOnly, store],
  )

  // Register delete handler
  useEffect(() => {
    window.addEventListener('keydown', onDelete)
    return () => window.removeEventListener('keydown', onDelete)
  }, [onDelete])

  useEffect(() => {
    const nodeId = store.focusTargetNodeId
    if (!nodeId || store.focusRequestNonce <= 0) return
    const node = store.nodes.find((item) => item.id === nodeId)
    if (!node) return
    const size = estimateMainNodeSize(node)
    void setCenter(
      node.position.x + size.width / 2,
      node.position.y + size.height / 2,
      { duration: 280 },
    )
  }, [setCenter, store.focusRequestNonce, store.focusTargetNodeId, store.nodes])

  return (
    <div ref={reactFlowWrapper} className="flex-1 h-full">
      <ReactFlow
        className="workflow-editor-flow"
        nodes={nodesWithRuntimeData}
        edges={edgesWithInteractions}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        onDragOver={onDragOver}
        onDrop={onDrop}
        defaultEdgeOptions={{
          type: 'workflowBezier',
          animated: false,
          interactionWidth: 28,
          style: { strokeWidth: 3, strokeLinecap: 'round' },
        }}
        snapToGrid
        snapGrid={[16, 16]}
        fitView
        deleteKeyCode={null}
        nodesDraggable={isEditable}
        nodesConnectable={isEditable}
        elementsSelectable
      >
        <Background gap={16} size={1} color="#94a3b8" className="opacity-40" />
        <FlowControls isInteractive={isEditable} onLockChange={setIsInteractive} />
        <MiniMap
          nodeStrokeWidth={3}
          zoomable
          pannable
          className="!bg-card !border !rounded-2xl !shadow-sm !overflow-hidden"
        />
      </ReactFlow>
    </div>
  )
}
