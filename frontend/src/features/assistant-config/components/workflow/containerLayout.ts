import type { ContainerBodyNodeType } from '../../api/workflow'
import { normalizeIfElseConfig } from './ifElseConfig'

const BODY_NODE_SIZE: Record<ContainerBodyNodeType, { width: number; height: number }> = {
  start: { width: 240, height: 96 },
  llm: { width: 240, height: 112 },
  agent: { width: 240, height: 112 },
  tool: { width: 240, height: 112 },
  workflow_call: { width: 240, height: 112 },
  if_else: { width: 240, height: 160 },
  parameter_extractor: { width: 240, height: 112 },
  knowledge_retrieval: { width: 240, height: 112 },
  code_executor: { width: 240, height: 128 },
  http_request: { width: 240, height: 116 },
  variable_assign: { width: 240, height: 112 },
  human_in_loop: { width: 240, height: 152 },
}

const MIN_WIDTH = 420
const MIN_HEIGHT = 320
const MIN_CANVAS_HEIGHT = 220
const LEFT_PADDING = 64
const RIGHT_PADDING = 260
const TOP_PADDING = 48
const BOTTOM_PADDING = 124
const IF_ELSE_HANDLE_BASE_TOP = 50
const IF_ELSE_HANDLE_STEP = 28

type LayoutNode = {
  nodeType: ContainerBodyNodeType
  positionX: number
  positionY: number
  config: Record<string, unknown> | null
}

function normalizeLayoutNodes(config: unknown): LayoutNode[] {
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    return []
  }
  const bodyNodes = (config as Record<string, unknown>).bodyNodes ?? (config as Record<string, unknown>).body_nodes
  if (!Array.isArray(bodyNodes)) return []
  return bodyNodes
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      nodeType: String(item.nodeType ?? item.node_type ?? 'llm') as ContainerBodyNodeType,
      positionX: Number.isFinite(Number(item.positionX ?? item.position_x)) ? Number(item.positionX ?? item.position_x) : 0,
      positionY: Number.isFinite(Number(item.positionY ?? item.position_y)) ? Number(item.positionY ?? item.position_y) : 0,
      config: item.config && typeof item.config === 'object' && !Array.isArray(item.config)
        ? (item.config as Record<string, unknown>)
        : null,
    }))
}

function resolveBodyNodeSize(node: LayoutNode): { width: number; height: number } {
  const fallback = BODY_NODE_SIZE[node.nodeType] ?? BODY_NODE_SIZE.llm
  if (node.nodeType !== 'if_else') return fallback
  const normalized = normalizeIfElseConfig(node.config ?? {})
  const branchCount = Math.max(2, normalized.branches.length + 1)
  const minHeight = IF_ELSE_HANDLE_BASE_TOP + (branchCount * IF_ELSE_HANDLE_STEP) + 24
  return {
    width: fallback.width,
    height: Math.max(fallback.height, minHeight),
  }
}

export function estimateContainerNodeSizeFromConfig(config: unknown): { width: number; height: number; canvasHeight: number } {
  const nodes = normalizeLayoutNodes(config)
  if (nodes.length === 0) {
    return { width: MIN_WIDTH, height: MIN_HEIGHT, canvasHeight: MIN_CANVAS_HEIGHT }
  }

  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY

  nodes.forEach((node) => {
    const size = resolveBodyNodeSize(node)
    minX = Math.min(minX, node.positionX)
    minY = Math.min(minY, node.positionY)
    maxX = Math.max(maxX, node.positionX + size.width)
    maxY = Math.max(maxY, node.positionY + size.height)
  })

  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
    return { width: MIN_WIDTH, height: MIN_HEIGHT, canvasHeight: MIN_CANVAS_HEIGHT }
  }

  const contentWidth = Math.max(0, maxX - minX)
  const contentHeight = Math.max(0, maxY - minY)
  const canvasHeight = Math.max(MIN_CANVAS_HEIGHT, Math.ceil(contentHeight + TOP_PADDING + BOTTOM_PADDING))
  const width = Math.max(MIN_WIDTH, Math.ceil(contentWidth + LEFT_PADDING + RIGHT_PADDING))
  const height = Math.max(MIN_HEIGHT, Math.ceil(56 + canvasHeight + 24))
  return { width, height, canvasHeight }
}
