import { useCallback, useRef, useEffect, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
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
import type { NodeType } from '../../api/workflow'
import { WorkflowNode } from './WorkflowNode'
import type { WorkflowToolDefinition } from './types'
import { createDefaultIfElseConfig } from './ifElseConfig'
import { WorkflowDeletableEdge } from './WorkflowDeletableEdge'
import { defaultLabelForNodeType } from './labelUtils'

const nodeTypes: NodeTypes = {
  start: WorkflowNode,
  llm: WorkflowNode,
  tool: WorkflowNode,
  if_else: WorkflowNode,
  template: WorkflowNode,
  parameter_extractor: WorkflowNode,
  knowledge_retrieval: WorkflowNode,
  variable_aggregator: WorkflowNode,
}

const edgeTypes = {
  workflowBezier: WorkflowDeletableEdge,
}

let nodeCounter = 0

interface FlowCanvasProps {
  tools: WorkflowToolDefinition[]
}

export function FlowCanvas({ tools }: FlowCanvasProps) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition } = useReactFlow()
  const store = useWorkflowEditorStore()

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      store.removeEdge(edgeId)
    },
    [store],
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
        selected: edge.id === store.selectedEdgeId,
        data: {
          ...(edge.data && typeof edge.data === 'object' ? edge.data : {}),
          onDelete: handleDeleteEdge,
          onSelect: handleSelectEdge,
        },
      })),
    [handleDeleteEdge, handleSelectEdge, store.edges, store.selectedEdgeId],
  )

  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      store.setSelectedEdgeId(edge.id)
    },
    [store],
  )

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      const updated = applyNodeChanges(changes, store.nodes)
      store.setNodes(updated as Node<WfNodeData>[])
    },
    [store],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      const updated = applyEdgeChanges(changes, store.edges)
      store.setEdges(updated)
    },
    [store],
  )

  const onConnect: OnConnect = useCallback(
    (connection) => {
      if (!connection.source || !connection.target) return
      // Prevent duplicate edges
      const exists = store.edges.some(
        (e) =>
          e.source === connection.source &&
          e.target === connection.target &&
          e.sourceHandle === connection.sourceHandle &&
          e.targetHandle === connection.targetHandle,
      )
      if (exists) return
      // Prevent self-connection
      if (connection.source === connection.target) return

      const edgeId = `edge_${Date.now()}_${++nodeCounter}`
      store.addEdge({
        id: edgeId,
        source: connection.source,
        target: connection.target,
        sourceHandle: connection.sourceHandle ?? 'output',
        targetHandle: connection.targetHandle ?? 'input',
        type: 'workflowBezier',
      })
    },
    [store],
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
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()

      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
      const toolPayload = e.dataTransfer.getData('application/workflow-tool-item')

      if (toolPayload) {
        try {
          const parsed = JSON.parse(toolPayload) as {
            nodeType?: string
            toolName?: string
            label?: string
          }
          if (parsed.nodeType === 'tool' && parsed.toolName) {
            const toolDef = tools.find((item) => item.name === parsed.toolName)
            const inputBindings = Object.fromEntries(
              (toolDef?.inputParams ?? []).map((item) => [item.name, '']),
            )
            const nodeId = `tool_${Date.now()}_${++nodeCounter}`
            store.addNode({
              id: nodeId,
              type: 'tool',
              position,
              data: {
                nodeType: 'tool',
                label: parsed.label || parsed.toolName || defaultLabelForNodeType('tool'),
                config: {
                  toolName: parsed.toolName,
                  inputBindings,
                },
              },
            })
            return
          }
        } catch {
          // fallthrough to normal node payload parsing
        }
      }

      const nodeType = e.dataTransfer.getData('application/workflow-node-type') as NodeType
      if (!nodeType) return
      const nodeId = `${nodeType}_${Date.now()}_${++nodeCounter}`

      const newNode: Node<WfNodeData> = {
        id: nodeId,
        type: nodeType,
        position,
        data: {
          nodeType,
          label: defaultLabelForNodeType(nodeType),
          config:
            nodeType === 'llm'
              ? {
                  outputMode: 'text',
                  userInput: '{{start.user_input}}',
                  isOutput: false,
                }
              : nodeType === 'tool'
                ? { toolName: '', inputBindings: {} }
                : nodeType === 'if_else'
                  ? createDefaultIfElseConfig()
                : null,
        },
      }
      store.addNode(newNode)
    },
    [screenToFlowPosition, store, tools],
  )

  const isEditableElement = (target: EventTarget | Element | null): boolean => {
    if (!(target instanceof Element)) return false
    if (target instanceof HTMLElement && target.isContentEditable) return true
    return Boolean(target.closest('input, textarea, select, [contenteditable], [role="textbox"]'))
  }

  const onDelete = useCallback(
    (e: KeyboardEvent) => {
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
    [store],
  )

  // Register delete handler
  useEffect(() => {
    window.addEventListener('keydown', onDelete)
    return () => window.removeEventListener('keydown', onDelete)
  }, [onDelete])

  return (
    <div ref={reactFlowWrapper} className="flex-1 h-full">
      <ReactFlow
        nodes={store.nodes}
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
      >
        <Background gap={16} size={1} />
        <Controls />
        <MiniMap
          nodeStrokeWidth={3}
          zoomable
          pannable
          className="!bg-card !border"
        />
      </ReactFlow>
    </div>
  )
}
