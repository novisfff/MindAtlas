import dagre from 'dagre'
import type { Edge, Node } from '@xyflow/react'
import type { ContainerBodyEdge, ContainerBodyNode, ContainerBodyNodeType } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import {
  CONTAINER_NODE_HANDLE_TOP,
  MAIN_NODE_HANDLE_TOP,
  SUBFLOW_NODE_HANDLE_TOP,
  estimateMainNodeSizeWithOptions,
  getSubflowNodeFrame,
  isMultiOutputNodeType,
} from './workflowGeometry'

const HORIZONTAL_ALIGN_MAX_CENTER_DELTA = 96
const HORIZONTAL_ALIGN_COLLISION_PADDING = 20
const SUBFLOW_LAYOUT_ORIGIN_X = 40
const SUBFLOW_LAYOUT_ORIGIN_Y = 56

type LinearLayoutNode = {
  id: string
  nodeType: WfNodeData['nodeType'] | ContainerBodyNodeType
  width: number
  height: number
  linearHandleTop: number
}

type LinearLayoutEdge = {
  sourceId: string
  targetId: string
}

type NodeBounds = {
  left: number
  right: number
  top: number
  bottom: number
}

type SubflowLayoutBounds = {
  width: number
  height: number
}

function getNodeSize(node: Node<WfNodeData>): { width: number; height: number } {
  return estimateMainNodeSizeWithOptions(node, { preferMeasured: false })
}

function getSubflowNodeSize(node: ContainerBodyNode): { width: number; height: number } {
  return getSubflowNodeFrame(node.nodeType, (node.config ?? null) as Record<string, unknown> | null)
}

function normalizeSubflowBodyNodePositions(bodyNodes: ContainerBodyNode[]): ContainerBodyNode[] {
  return bodyNodes.map((node, index) => ({
    ...node,
    positionX: Number.isFinite(Number(node.positionX)) ? Number(node.positionX) : 26 + index * 260,
    positionY: Number.isFinite(Number(node.positionY)) ? Number(node.positionY) : 64,
  }))
}

function measureSubflowLayoutBounds(bodyNodes: ContainerBodyNode[]): SubflowLayoutBounds {
  if (bodyNodes.length === 0) {
    return { width: 0, height: 0 }
  }
  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxRight = Number.NEGATIVE_INFINITY
  let maxBottom = Number.NEGATIVE_INFINITY

  bodyNodes.forEach((node) => {
    const x = Number(node.positionX ?? 0)
    const y = Number(node.positionY ?? 0)
    const size = getSubflowNodeSize(node)
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxRight = Math.max(maxRight, x + size.width)
    maxBottom = Math.max(maxBottom, y + size.height)
  })

  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxRight) || !Number.isFinite(maxBottom)) {
    return { width: 0, height: 0 }
  }

  return {
    width: Math.max(0, Math.round(maxRight - minX)),
    height: Math.max(0, Math.round(maxBottom - minY)),
  }
}

function normalizeSubflowLayoutOrigin(nodes: ContainerBodyNode[]): ContainerBodyNode[] {
  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  nodes.forEach((node) => {
    minX = Math.min(minX, Number(node.positionX ?? 0))
    minY = Math.min(minY, Number(node.positionY ?? 0))
  })
  const shiftX = Number.isFinite(minX) ? SUBFLOW_LAYOUT_ORIGIN_X - minX : 0
  const shiftY = Number.isFinite(minY) ? SUBFLOW_LAYOUT_ORIGIN_Y - minY : 0
  return nodes.map((node) => ({
    ...node,
    positionX: Math.round(Number(node.positionX ?? 0) + shiftX),
    positionY: Math.round(Number(node.positionY ?? 0) + shiftY),
  }))
}

function boundsFromPosition(
  position: { x: number; y: number },
  node: LinearLayoutNode,
): NodeBounds {
  return {
    left: position.x,
    right: position.x + node.width,
    top: position.y,
    bottom: position.y + node.height,
  }
}

function hasBoundsCollision(
  a: NodeBounds,
  b: NodeBounds,
  padding: number,
): boolean {
  return (
    a.left < b.right + padding &&
    a.right > b.left - padding &&
    a.top < b.bottom + padding &&
    a.bottom > b.top - padding
  )
}

function alignHorizontalLinearChains(
  nodesById: Map<string, LinearLayoutNode>,
  edges: LinearLayoutEdge[],
  basePositions: Map<string, { x: number; y: number }>,
): Map<string, { x: number; y: number }> {
  if (nodesById.size === 0 || edges.length === 0) return basePositions

  const positions = new Map(basePositions)
  const validEdges = edges.filter((edge) => (
    edge.sourceId !== edge.targetId &&
    nodesById.has(edge.sourceId) &&
    nodesById.has(edge.targetId)
  ))
  if (validEdges.length === 0) return positions

  const outDegree = new Map<string, number>()
  const inDegree = new Map<string, number>()
  validEdges.forEach((edge) => {
    outDegree.set(edge.sourceId, (outDegree.get(edge.sourceId) ?? 0) + 1)
    inDegree.set(edge.targetId, (inDegree.get(edge.targetId) ?? 0) + 1)
  })

  const eligibleOut = new Map<string, string>()
  const eligibleIn = new Map<string, string>()
  validEdges.forEach((edge) => {
    const sourceNode = nodesById.get(edge.sourceId)
    const targetNode = nodesById.get(edge.targetId)
    if (!sourceNode || !targetNode) return
    if (isMultiOutputNodeType(sourceNode.nodeType) || isMultiOutputNodeType(targetNode.nodeType)) return
    if ((outDegree.get(edge.sourceId) ?? 0) !== 1) return
    if ((inDegree.get(edge.targetId) ?? 0) !== 1) return

    const sourcePos = positions.get(edge.sourceId)
    const targetPos = positions.get(edge.targetId)
    if (!sourcePos || !targetPos) return

    const sourceCenterX = sourcePos.x + sourceNode.width / 2
    const targetCenterX = targetPos.x + targetNode.width / 2
    if (targetCenterX <= sourceCenterX) return

    const sourceHandleY = sourcePos.y + sourceNode.linearHandleTop
    const targetHandleY = targetPos.y + targetNode.linearHandleTop
    if (Math.abs(sourceHandleY - targetHandleY) > HORIZONTAL_ALIGN_MAX_CENTER_DELTA) return

    eligibleOut.set(edge.sourceId, edge.targetId)
    eligibleIn.set(edge.targetId, edge.sourceId)
  })
  if (eligibleOut.size === 0) return positions

  const chainHeads = Array.from(eligibleOut.keys())
    .filter((nodeId) => !eligibleIn.has(nodeId))
    .sort((a, b) => {
      const ax = positions.get(a)?.x ?? 0
      const bx = positions.get(b)?.x ?? 0
      if (ax !== bx) return ax - bx
      return a.localeCompare(b)
    })

  const applyChainAlignment = (chain: string[]): void => {
    if (chain.length < 2) return
    const firstNode = nodesById.get(chain[0])
    const firstPos = positions.get(chain[0])
    if (!firstNode || !firstPos) return

    const targetHandleY = firstPos.y + firstNode.linearHandleTop
    const chainSet = new Set(chain)
    const proposedBounds = new Map<string, NodeBounds>()

    for (const nodeId of chain) {
      const nodeMeta = nodesById.get(nodeId)
      const nodePos = positions.get(nodeId)
      if (!nodeMeta || !nodePos) return
      const nextY = Math.round(targetHandleY - nodeMeta.linearHandleTop)
      proposedBounds.set(nodeId, boundsFromPosition({ x: nodePos.x, y: nextY }, nodeMeta))
    }

    for (const [otherId, otherMeta] of nodesById) {
      if (chainSet.has(otherId)) continue
      const otherPos = positions.get(otherId)
      if (!otherPos) continue
      const otherBounds = boundsFromPosition(otherPos, otherMeta)
      for (const nodeId of chain) {
        const nextBounds = proposedBounds.get(nodeId)
        if (!nextBounds) continue
        if (hasBoundsCollision(nextBounds, otherBounds, HORIZONTAL_ALIGN_COLLISION_PADDING)) {
          return
        }
      }
    }

    for (const nodeId of chain) {
      const nodeMeta = nodesById.get(nodeId)
      const nodePos = positions.get(nodeId)
      if (!nodeMeta || !nodePos) continue
      positions.set(nodeId, {
        x: nodePos.x,
        y: Math.round(targetHandleY - nodeMeta.linearHandleTop),
      })
    }
  }

  for (const headId of chainHeads) {
    const chain: string[] = []
    const localVisited = new Set<string>()
    let currentId: string | undefined = headId
    while (currentId && !localVisited.has(currentId)) {
      chain.push(currentId)
      localVisited.add(currentId)
      const nextId = eligibleOut.get(currentId)
      if (!nextId) break
      if (eligibleIn.get(nextId) !== currentId) break
      currentId = nextId
    }
    applyChainAlignment(chain)
  }

  return positions
}

function resolveMainLinearHandleTop(nodeType: WfNodeData['nodeType']): number {
  if (nodeType === 'iteration' || nodeType === 'loop') {
    return CONTAINER_NODE_HANDLE_TOP
  }
  return MAIN_NODE_HANDLE_TOP
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

  const layoutNodes = new Map<string, LinearLayoutNode>()
  nodes.forEach((node) => {
    const size = getNodeSize(node)
    layoutNodes.set(node.id, {
      id: node.id,
      nodeType: node.data.nodeType,
      width: size.width,
      height: size.height,
      linearHandleTop: resolveMainLinearHandleTop(node.data.nodeType),
    })
  })
  const layoutEdges = edges.map((edge) => ({
    sourceId: edge.source,
    targetId: edge.target,
  }))
  return alignHorizontalLinearChains(layoutNodes, layoutEdges, positionMap)
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

  const normalizedNodes = normalizeSubflowBodyNodePositions(bodyNodes)

  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({
    rankdir: 'LR',
    ranksep: 84,
    nodesep: 40,
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

  const positionMap = new Map<string, { x: number; y: number }>()
  normalizedNodes.forEach((node) => {
    const dagreNode = graph.node(node.nodeId) as { x: number; y: number } | undefined
    if (!dagreNode) {
      positionMap.set(node.nodeId, {
        x: Math.round(Number(node.positionX ?? 0)),
        y: Math.round(Number(node.positionY ?? 0)),
      })
      return
    }
    const size = getSubflowNodeSize(node)
    positionMap.set(node.nodeId, {
      x: Math.round(dagreNode.x - size.width / 2),
      y: Math.round(dagreNode.y - size.height / 2),
    })
  })

  const layoutNodes = new Map<string, LinearLayoutNode>()
  normalizedNodes.forEach((node) => {
    const size = getSubflowNodeSize(node)
    layoutNodes.set(node.nodeId, {
      id: node.nodeId,
      nodeType: node.nodeType,
      width: size.width,
      height: size.height,
      linearHandleTop: SUBFLOW_NODE_HANDLE_TOP,
    })
  })
  const layoutEdges = bodyEdges.map((edge) => ({
    sourceId: edge.sourceNodeId,
    targetId: edge.targetNodeId,
  }))
  const alignedPositionMap = alignHorizontalLinearChains(layoutNodes, layoutEdges, positionMap)

  return normalizeSubflowLayoutOrigin(normalizedNodes.map((node) => {
    const nextPosition = alignedPositionMap.get(node.nodeId)
    if (!nextPosition) return node
    return {
      ...node,
      positionX: nextPosition.x,
      positionY: nextPosition.y,
    }
  }))
}

export function normalizeContainerPreviewBodyNodes(
  bodyNodes: ContainerBodyNode[],
  bodyEdges: ContainerBodyEdge[],
): ContainerBodyNode[] {
  if (bodyNodes.length === 0) return bodyNodes

  const normalizedNodes = normalizeSubflowBodyNodePositions(bodyNodes)
  const hasMissingPositions = bodyNodes.some((node) => (
    !Number.isFinite(Number(node.positionX)) || !Number.isFinite(Number(node.positionY))
  ))
  if (hasMissingPositions) {
    return autoLayoutContainerBodyNodes(bodyNodes, bodyEdges)
  }
  if (bodyEdges.length === 0) {
    return normalizedNodes
  }

  const currentBounds = measureSubflowLayoutBounds(normalizedNodes)
  const compactNodes = autoLayoutContainerBodyNodes(bodyNodes, bodyEdges)
  const compactBounds = measureSubflowLayoutBounds(compactNodes)
  if (compactBounds.width <= 0 || compactBounds.height <= 0) {
    return normalizedNodes
  }

  const widthTooSparse = currentBounds.width > compactBounds.width * 1.35
  const heightTooSparse = currentBounds.height > compactBounds.height * 1.35
  return widthTooSparse || heightTooSparse ? compactNodes : normalizedNodes
}

function isContainerNodeType(nodeType: WfNodeData['nodeType']): boolean {
  return nodeType === 'iteration' || nodeType === 'loop'
}

export function autoLayoutWorkflowWithSubflows(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
): Node<WfNodeData>[] {
  const nodesWithLaidOutSubflows = nodes.map((node) => {
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
  return autoLayoutWorkflowNodes(nodesWithLaidOutSubflows, edges)
}
