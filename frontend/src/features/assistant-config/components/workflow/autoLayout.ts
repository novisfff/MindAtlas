import dagre from 'dagre'
import type { Edge, Node } from '@xyflow/react'
import type { ContainerBodyEdge, ContainerBodyNode } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { estimateContainerNodeSizeFromConfig } from './containerLayout'
import { normalizeIfElseConfig } from './ifElseConfig'

const DEFAULT_NODE_SIZE: Record<string, { width: number; height: number }> = {
  start: { width: 160, height: 64 },
  llm: { width: 260, height: 120 },
  tool: { width: 260, height: 120 },
  if_else: { width: 300, height: 160 },
  parameter_extractor: { width: 260, height: 96 },
  knowledge_retrieval: { width: 260, height: 96 },
  iteration: { width: 320, height: 220 },
  loop: { width: 320, height: 220 },
}

const SUBFLOW_NODE_SIZE: Record<string, { width: number; height: number }> = {
  start: { width: 240, height: 96 },
  llm: { width: 240, height: 112 },
  tool: { width: 240, height: 112 },
  if_else: { width: 240, height: 160 },
  parameter_extractor: { width: 240, height: 112 },
  knowledge_retrieval: { width: 240, height: 112 },
}

function getNodeSize(node: Node<WfNodeData>): { width: number; height: number } {
  if ((node.data.nodeType === 'iteration' || node.data.nodeType === 'loop') && node.data.config) {
    const dynamic = estimateContainerNodeSizeFromConfig(node.data.config)
    return { width: dynamic.width, height: dynamic.height }
  }
  const measured = (node as unknown as { measured?: { width?: number; height?: number } }).measured
  const fallback = DEFAULT_NODE_SIZE[node.data.nodeType] ?? { width: 240, height: 96 }
  const width = node.width ?? measured?.width ?? fallback.width
  const height = node.height ?? measured?.height ?? fallback.height
  return { width, height }
}

function getSubflowNodeSize(node: ContainerBodyNode): { width: number; height: number } {
  const fallback = SUBFLOW_NODE_SIZE[node.nodeType] ?? SUBFLOW_NODE_SIZE.llm
  if (node.nodeType !== 'if_else') return fallback
  const normalized = normalizeIfElseConfig((node.config ?? {}) as Record<string, unknown>)
  const branchCount = Math.max(1, normalized.branches.length + 1)
  const ifElseHeight = Math.max(126, 50 + branchCount * 28 + 24)
  return {
    width: fallback.width,
    height: Math.max(fallback.height, ifElseHeight),
  }
}

function autoLayoutMainGraphNodes(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
): Map<string, { x: number; y: number }> {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({
    rankdir: 'LR',
    ranksep: 170,
    nodesep: 60,
    marginx: 20,
    marginy: 20,
  })

  const nodeMap = new Map(nodes.map((node) => [node.id, node]))
  nodes.forEach((node) => {
    graph.setNode(node.id, getNodeSize(node))
  })

  edges.forEach((edge) => {
    if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) return
    if (edge.source === edge.target) return
    graph.setEdge(edge.source, edge.target)
  })

  dagre.layout(graph)

  const startNode = nodes.find((node) => node.data.nodeType === 'start')
  const anchorNode = startNode ?? nodes[0]
  const anchorTarget = graph.node(anchorNode.id) as { x: number; y: number } | undefined
  const anchorSize = getNodeSize(anchorNode)
  const anchorCurrent = anchorNode.position
  const anchorLaidOut = anchorTarget
    ? { x: anchorTarget.x - anchorSize.width / 2, y: anchorTarget.y - anchorSize.height / 2 }
    : anchorCurrent
  const offsetX = anchorCurrent.x - anchorLaidOut.x
  const offsetY = anchorCurrent.y - anchorLaidOut.y

  const positionMap = new Map<string, { x: number; y: number }>()
  nodes.forEach((node) => {
    const dagreNode = graph.node(node.id) as { x: number; y: number } | undefined
    if (!dagreNode) {
      positionMap.set(node.id, node.position)
      return
    }
    const size = getNodeSize(node)
    positionMap.set(node.id, {
      x: Math.round(dagreNode.x - size.width / 2 + offsetX),
      y: Math.round(dagreNode.y - size.height / 2 + offsetY),
    })
  })
  return positionMap
}

export function autoLayoutWorkflowNodes(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
): Node<WfNodeData>[] {
  if (nodes.length === 0) return nodes

  const positionMap = autoLayoutMainGraphNodes(nodes, edges)
  return nodes.map((node) => {
    const nextPosition = positionMap.get(node.id)
    if (!nextPosition) return node
    return {
      ...node,
      position: nextPosition,
    }
  })
}

export function autoLayoutContainerBodyNodes(
  bodyNodes: ContainerBodyNode[],
  bodyEdges: ContainerBodyEdge[],
): ContainerBodyNode[] {
  if (bodyNodes.length === 0) return bodyNodes

  const normalizedNodes = bodyNodes.map((node, index) => ({
    ...node,
    positionX: Number.isFinite(Number(node.positionX)) ? Number(node.positionX) : 26 + index * 260,
    positionY: Number.isFinite(Number(node.positionY)) ? Number(node.positionY) : 64,
  }))

  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({
    rankdir: 'LR',
    ranksep: 130,
    nodesep: 52,
    marginx: 20,
    marginy: 20,
  })

  const nodeMap = new Map(normalizedNodes.map((node) => [node.nodeId, node]))
  normalizedNodes.forEach((node) => {
    graph.setNode(node.nodeId, getSubflowNodeSize(node))
  })

  bodyEdges.forEach((edge) => {
    if (!nodeMap.has(edge.sourceNodeId) || !nodeMap.has(edge.targetNodeId)) return
    if (edge.sourceNodeId === edge.targetNodeId) return
    graph.setEdge(edge.sourceNodeId, edge.targetNodeId)
  })

  dagre.layout(graph)

  const startNode = normalizedNodes.find((node) => node.nodeType === 'start')
  const anchorNode = startNode ?? normalizedNodes[0]
  const anchorTarget = graph.node(anchorNode.nodeId) as { x: number; y: number } | undefined
  const anchorSize = getSubflowNodeSize(anchorNode)
  const anchorCurrent = {
    x: Number(anchorNode.positionX ?? 0),
    y: Number(anchorNode.positionY ?? 0),
  }
  const anchorLaidOut = anchorTarget
    ? { x: anchorTarget.x - anchorSize.width / 2, y: anchorTarget.y - anchorSize.height / 2 }
    : anchorCurrent
  const offsetX = anchorCurrent.x - anchorLaidOut.x
  const offsetY = anchorCurrent.y - anchorLaidOut.y

  return normalizedNodes.map((node) => {
    const dagreNode = graph.node(node.nodeId) as { x: number; y: number } | undefined
    if (!dagreNode) return node
    const size = getSubflowNodeSize(node)
    return {
      ...node,
      positionX: Math.round(dagreNode.x - size.width / 2 + offsetX),
      positionY: Math.round(dagreNode.y - size.height / 2 + offsetY),
    }
  })
}

function isContainerNodeType(nodeType: WfNodeData['nodeType']): boolean {
  return nodeType === 'iteration' || nodeType === 'loop'
}

export function autoLayoutWorkflowWithSubflows(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
): Node<WfNodeData>[] {
  const mainLaidOut = autoLayoutWorkflowNodes(nodes, edges)
  return mainLaidOut.map((node) => {
    if (!isContainerNodeType(node.data.nodeType)) return node
    const config = node.data.config
    if (!config || typeof config !== 'object' || Array.isArray(config)) return node

    const configRecord = config as Record<string, unknown>
    const rawBodyNodes = Array.isArray(configRecord.bodyNodes)
      ? configRecord.bodyNodes
      : Array.isArray(configRecord.body_nodes)
        ? configRecord.body_nodes
        : null
    const rawBodyEdges = Array.isArray(configRecord.bodyEdges)
      ? configRecord.bodyEdges
      : Array.isArray(configRecord.body_edges)
        ? configRecord.body_edges
        : null
    if (!rawBodyNodes || !rawBodyEdges) return node

    const bodyNodes = rawBodyNodes
      .filter((item): item is ContainerBodyNode => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
      .map((item) => item as ContainerBodyNode)
    const bodyEdges = rawBodyEdges
      .filter((item): item is ContainerBodyEdge => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
      .map((item) => item as ContainerBodyEdge)
    if (bodyNodes.length === 0) return node

    const nextBodyNodes = autoLayoutContainerBodyNodes(bodyNodes, bodyEdges)
    const nextConfig: Record<string, unknown> = {
      ...configRecord,
      bodyNodes: nextBodyNodes,
      bodyEdges,
    }
    if (Array.isArray(configRecord.body_nodes)) nextConfig.body_nodes = nextBodyNodes
    if (Array.isArray(configRecord.body_edges)) nextConfig.body_edges = bodyEdges

    return {
      ...node,
      data: {
        ...node.data,
        config: nextConfig,
      },
    }
  })
}
