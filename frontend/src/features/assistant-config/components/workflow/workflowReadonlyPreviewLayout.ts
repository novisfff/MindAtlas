import dagre from 'dagre'
import type { Edge, Node } from '@xyflow/react'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { getNodeOutputHandleIds } from './workflowGeometry'

export type ThumbnailPreviewDensity = 'regular' | 'compact' | 'dense'

export type ThumbnailPreviewNode = {
  id: string
  label: string
  nodeType: string
  position: { x: number; y: number }
  frame: { width: number; height: number }
  outputHandles: string[]
}

export type ThumbnailPreviewScene = {
  density: ThumbnailPreviewDensity
  fitScale: number
  nodes: ThumbnailPreviewNode[]
  edges: Edge[]
}

type LayoutNode = {
  id: string
  label: string
  nodeType: WfNodeData['nodeType']
  config: Record<string, unknown> | null
}

type SceneBounds = {
  width: number
  height: number
}

const THUMBNAIL_ORIGIN_X = 24
const THUMBNAIL_ORIGIN_Y = 24
const THUMBNAIL_FIT_THRESHOLDS = {
  regular: 0.78,
  compact: 0.52,
}
const THUMBNAIL_LAYOUT_RULES: Record<ThumbnailPreviewDensity, {
  ranksep: number
  nodesep: number
  marginx: number
  marginy: number
  fitPadding: number
}> = {
  regular: { ranksep: 44, nodesep: 26, marginx: 8, marginy: 8, fitPadding: 20 },
  compact: { ranksep: 26, nodesep: 16, marginx: 6, marginy: 6, fitPadding: 14 },
  dense: { ranksep: 8, nodesep: 6, marginx: 4, marginy: 4, fitPadding: 6 },
}

function getThumbnailNodeFrame(
  nodeType: string,
  density: ThumbnailPreviewDensity,
): { width: number; height: number } {
  const isBoundary = nodeType === 'start' || nodeType === 'output'
  if (density === 'dense') {
    return {
      width: isBoundary ? 22 : 14,
      height: 10,
    }
  }
  if (density === 'compact') {
    return {
      width: isBoundary ? 96 : 108,
      height: isBoundary ? 34 : 38,
    }
  }
  return {
    width: isBoundary ? 128 : 152,
    height: isBoundary ? 52 : 58,
  }
}

function measureSceneBounds(nodes: ThumbnailPreviewNode[]): SceneBounds {
  if (nodes.length === 0) return { width: 0, height: 0 }
  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxRight = Number.NEGATIVE_INFINITY
  let maxBottom = Number.NEGATIVE_INFINITY

  nodes.forEach((node) => {
    minX = Math.min(minX, node.position.x)
    minY = Math.min(minY, node.position.y)
    maxRight = Math.max(maxRight, node.position.x + node.frame.width)
    maxBottom = Math.max(maxBottom, node.position.y + node.frame.height)
  })

  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxRight) || !Number.isFinite(maxBottom)) {
    return { width: 0, height: 0 }
  }

  return {
    width: Math.max(0, Math.round(maxRight - minX)),
    height: Math.max(0, Math.round(maxBottom - minY)),
  }
}

function normalizeSceneOrigin(nodes: ThumbnailPreviewNode[]): ThumbnailPreviewNode[] {
  if (nodes.length === 0) return nodes
  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  nodes.forEach((node) => {
    minX = Math.min(minX, node.position.x)
    minY = Math.min(minY, node.position.y)
  })
  const shiftX = Number.isFinite(minX) ? THUMBNAIL_ORIGIN_X - minX : 0
  const shiftY = Number.isFinite(minY) ? THUMBNAIL_ORIGIN_Y - minY : 0

  return nodes.map((node) => ({
    ...node,
    position: {
      x: Math.round(node.position.x + shiftX),
      y: Math.round(node.position.y + shiftY),
    },
  }))
}

function calculateFitScale(
  bounds: SceneBounds,
  width: number,
  height: number,
  density: ThumbnailPreviewDensity,
): number {
  if (bounds.width <= 0 || bounds.height <= 0) return 1
  const fitPadding = THUMBNAIL_LAYOUT_RULES[density].fitPadding
  const availableWidth = Math.max(1, width - fitPadding * 2)
  const availableHeight = Math.max(1, height - fitPadding * 2)
  return Math.min(1, availableWidth / bounds.width, availableHeight / bounds.height)
}

function buildSceneForDensity(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  density: ThumbnailPreviewDensity,
  viewportSize: { width: number; height: number },
): ThumbnailPreviewScene {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({
    rankdir: 'LR',
    ranksep: THUMBNAIL_LAYOUT_RULES[density].ranksep,
    nodesep: THUMBNAIL_LAYOUT_RULES[density].nodesep,
    marginx: THUMBNAIL_LAYOUT_RULES[density].marginx,
    marginy: THUMBNAIL_LAYOUT_RULES[density].marginy,
  })

  const layoutNodes: LayoutNode[] = nodes.map((node) => ({
    id: node.id,
    label: String(node.data.label ?? ''),
    nodeType: (node.data.nodeType ?? node.type ?? 'llm') as WfNodeData['nodeType'],
    config: (node.data.config ?? null) as Record<string, unknown> | null,
  }))
  const nodeIds = new Set(layoutNodes.map((node) => node.id))

  layoutNodes.forEach((node) => {
    graph.setNode(node.id, getThumbnailNodeFrame(node.nodeType, density))
  })
  edges.forEach((edge) => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return
    if (edge.source === edge.target) return
    graph.setEdge(edge.source, edge.target)
  })

  dagre.layout(graph)

  const sceneNodes = normalizeSceneOrigin(layoutNodes.map((node) => {
    const frame = getThumbnailNodeFrame(node.nodeType, density)
    const dagreNode = graph.node(node.id) as { x: number; y: number } | undefined
    return {
      id: node.id,
      label: node.label,
      nodeType: node.nodeType,
      frame,
      outputHandles: getNodeOutputHandleIds(
        node.nodeType,
        node.config,
      ),
      position: dagreNode
        ? {
          x: Math.round(dagreNode.x - frame.width / 2),
          y: Math.round(dagreNode.y - frame.height / 2),
        }
        : { x: THUMBNAIL_ORIGIN_X, y: THUMBNAIL_ORIGIN_Y },
    }
  }))
  const bounds = measureSceneBounds(sceneNodes)

  return {
    density,
    fitScale: calculateFitScale(bounds, viewportSize.width, viewportSize.height, density),
    nodes: sceneNodes,
    edges,
  }
}

export function buildThumbnailPreviewScene(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  viewportSize: { width: number; height: number },
): ThumbnailPreviewScene {
  if (nodes.length === 0) {
    return {
      density: 'regular',
      fitScale: 1,
      nodes: [],
      edges: [],
    }
  }

  const safeViewport = {
    width: Math.max(320, Math.round(viewportSize.width || 0)),
    height: Math.max(160, Math.round(viewportSize.height || 0)),
  }

  const regularScene = buildSceneForDensity(nodes, edges, 'regular', safeViewport)
  if (regularScene.fitScale >= THUMBNAIL_FIT_THRESHOLDS.regular) {
    return regularScene
  }

  const compactScene = buildSceneForDensity(nodes, edges, 'compact', safeViewport)
  if (compactScene.fitScale >= THUMBNAIL_FIT_THRESHOLDS.compact) {
    return compactScene
  }

  return buildSceneForDensity(nodes, edges, 'dense', safeViewport)
}
