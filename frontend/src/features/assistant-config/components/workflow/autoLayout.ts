import dagre from 'dagre'
import type { Edge, Node } from '@xyflow/react'
import type { WfNodeData } from '../../stores/workflow-editor-store'

const DEFAULT_NODE_SIZE: Record<string, { width: number; height: number }> = {
  start: { width: 160, height: 64 },
  llm: { width: 260, height: 120 },
  tool: { width: 260, height: 120 },
  if_else: { width: 300, height: 160 },
  template: { width: 260, height: 96 },
  parameter_extractor: { width: 260, height: 96 },
  knowledge_retrieval: { width: 260, height: 96 },
  variable_aggregator: { width: 240, height: 84 },
}

function getNodeSize(node: Node<WfNodeData>): { width: number; height: number } {
  const measured = (node as unknown as { measured?: { width?: number; height?: number } }).measured
  const fallback = DEFAULT_NODE_SIZE[node.data.nodeType] ?? { width: 240, height: 96 }
  const width = node.width ?? measured?.width ?? fallback.width
  const height = node.height ?? measured?.height ?? fallback.height
  return { width, height }
}

export function autoLayoutWorkflowNodes(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
): Node<WfNodeData>[] {
  if (nodes.length === 0) return nodes

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
    const size = getNodeSize(node)
    graph.setNode(node.id, size)
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

  return nodes.map((node) => {
    const dagreNode = graph.node(node.id) as { x: number; y: number } | undefined
    if (!dagreNode) return node
    const size = getNodeSize(node)
    return {
      ...node,
      position: {
        x: Math.round(dagreNode.x - size.width / 2 + offsetX),
        y: Math.round(dagreNode.y - size.height / 2 + offsetY),
      },
    }
  })
}
