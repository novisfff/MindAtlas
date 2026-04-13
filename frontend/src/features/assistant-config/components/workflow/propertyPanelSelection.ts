import type { Edge, Node } from '@xyflow/react'
import type {
  ContainerBodyEdge,
  ContainerBodyNode,
  ContainerBodyNodeType,
  NodeConfig,
  NodeType,
} from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { normalizeIfElseConfig } from './ifElseConfig'
import { defaultLabelForNodeType } from './labelUtils'

export type PropertyPanelSelectionTarget =
  | {
      kind: 'main'
      nodeId: string
    }
  | {
      kind: 'subflow'
      containerId: string
      nodeId: string
    }

export type PropertyPanelSelectionContext =
  | {
      mode: 'main'
      node: Node<WfNodeData>
    }
  | {
      mode: 'subflow'
      containerNode: Node<WfNodeData>
      containerConfig: Record<string, unknown>
      bodyNodes: ContainerBodyNode[]
      bodyEdges: ContainerBodyEdge[]
      node: ContainerBodyNode
    }

function sourceHandlesForSubflowNode(
  nodeType: ContainerBodyNodeType,
  config?: Record<string, unknown> | null,
): string[] {
  if (nodeType === 'if_else') {
    const normalized = normalizeIfElseConfig(config ?? {})
    return [...normalized.branches.map((item) => item.id), normalized.elseHandle || 'else']
  }
  if (nodeType === 'human_in_loop') {
    return ['approved', 'rejected']
  }
  return ['output']
}

function normalizeSubflowSourceHandle(
  sourceNode: ContainerBodyNode | undefined,
  rawSourceHandle: string | null | undefined,
): string {
  const sourceHandle = String(rawSourceHandle ?? '').trim()
  if (!sourceNode) {
    if (!sourceHandle) return 'output'
    return sourceHandle
  }
  const handles = sourceHandlesForSubflowNode(sourceNode.nodeType, sourceNode.config ?? null)
  if (!sourceHandle || sourceHandle === 'output') return handles[0] ?? 'output'
  if (handles.includes(sourceHandle)) return sourceHandle
  return handles[0] ?? 'else'
}

function normalizeSubflowTargetHandle(rawTargetHandle: string | null | undefined): string {
  const targetHandle = String(rawTargetHandle ?? '').trim()
  if (!targetHandle) return 'input'
  return targetHandle
}

export function normalizeContainerBodyNodes(config: Record<string, unknown>): ContainerBodyNode[] {
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
        label: String(item.label ?? defaultLabelForNodeType(fallbackType as NodeType)),
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

export function normalizeContainerBodyEdges(
  config: Record<string, unknown>,
  bodyNodes: ContainerBodyNode[],
): ContainerBodyEdge[] {
  const raw = (config.bodyEdges ?? config.body_edges) as unknown
  if (!Array.isArray(raw)) return []
  const nodeById = new Map(bodyNodes.map((node) => [node.nodeId, node]))

  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const sourceNodeId = String(item.sourceNodeId ?? item.source_node_id ?? '')
      const sourceNode = nodeById.get(sourceNodeId)
      const targetNodeId = String(item.targetNodeId ?? item.target_node_id ?? '')
      return {
        edgeId: String(item.edgeId ?? item.edge_id ?? ''),
        sourceNodeId,
        targetNodeId,
        sourceHandle: normalizeSubflowSourceHandle(
          sourceNode,
          (item.sourceHandle ?? item.source_handle) as string | null | undefined,
        ),
        targetHandle: normalizeSubflowTargetHandle(
          (item.targetHandle ?? item.target_handle) as string | null | undefined,
        ),
        conditionType: (item.conditionType ?? item.condition_type ?? null) as ContainerBodyEdge['conditionType'],
        conditionExpr: (item.conditionExpr ?? item.condition_expr ?? null) as ContainerBodyEdge['conditionExpr'],
        label: (item.label ?? null) as ContainerBodyEdge['label'],
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

export function getPropertyPanelSelectionTargetKey(target: PropertyPanelSelectionTarget): string {
  if (target.kind === 'main') {
    return `property:main:${target.nodeId}`
  }
  return `property:subflow:${target.containerId}:${target.nodeId}`
}

export function resolveSelectionContextFromTarget(
  nodes: Node<WfNodeData>[],
  target: PropertyPanelSelectionTarget,
): PropertyPanelSelectionContext | null {
  if (target.kind === 'subflow') {
    const containerNode = nodes.find((node) => node.id === target.containerId)
    if (containerNode && (containerNode.data.nodeType === 'iteration' || containerNode.data.nodeType === 'loop')) {
      const containerConfig = (containerNode.data.config ?? {}) as Record<string, unknown>
      const bodyNodes = normalizeContainerBodyNodes(containerConfig)
      const bodyEdges = normalizeContainerBodyEdges(containerConfig, bodyNodes)
      const selectedBodyNode = bodyNodes.find((node) => node.nodeId === target.nodeId)
      if (selectedBodyNode) {
        return {
          mode: 'subflow',
          containerNode,
          containerConfig,
          bodyNodes,
          bodyEdges,
          node: selectedBodyNode,
        }
      }
    }
    return null
  }

  const selectedNode = nodes.find((node) => node.id === target.nodeId)
  if (!selectedNode) return null
  return {
    mode: 'main',
    node: selectedNode,
  }
}

export function resolveSelectionContext(
  nodes: Node<WfNodeData>[],
  selectedNodeId: string | null,
  selectedSubflowContainerId: string | null,
  selectedSubflowNodeId: string | null,
): PropertyPanelSelectionContext | null {
  if (selectedSubflowContainerId && selectedSubflowNodeId) {
    const containerNode = nodes.find((node) => node.id === selectedSubflowContainerId)
    if (containerNode && (containerNode.data.nodeType === 'iteration' || containerNode.data.nodeType === 'loop')) {
      const containerConfig = (containerNode.data.config ?? {}) as Record<string, unknown>
      const bodyNodes = normalizeContainerBodyNodes(containerConfig)
      const bodyEdges = normalizeContainerBodyEdges(containerConfig, bodyNodes)
      const selectedBodyNode = bodyNodes.find((node) => node.nodeId === selectedSubflowNodeId)
      if (selectedBodyNode) {
        return {
          mode: 'subflow',
          containerNode,
          containerConfig,
          bodyNodes,
          bodyEdges,
          node: selectedBodyNode,
        }
      }
    }
  }

  if (!selectedNodeId) return null
  const selectedNode = nodes.find((node) => node.id === selectedNodeId)
  if (!selectedNode) return null
  return {
    mode: 'main',
    node: selectedNode,
  }
}

export function toSubflowGraphNodes(bodyNodes: ContainerBodyNode[]): Node<WfNodeData>[] {
  return bodyNodes.map((node) => ({
    id: node.nodeId,
    position: {
      x: Number(node.positionX ?? 0),
      y: Number(node.positionY ?? 0),
    },
    data: {
      nodeType: node.nodeType as NodeType,
      label: node.label || defaultLabelForNodeType(node.nodeType as NodeType),
      config: (node.config ?? null) as NodeConfig | null,
    },
  }))
}

export function toSubflowGraphEdges(bodyEdges: ContainerBodyEdge[]): Edge[] {
  return bodyEdges.map((edge) => ({
    id: edge.edgeId,
    source: edge.sourceNodeId,
    target: edge.targetNodeId,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
  }))
}
