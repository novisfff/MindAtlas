import type { Node } from '@xyflow/react'

import type { ContainerBodyNode, ContainerBodyNodeType, NodeType } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import { normalizeIfElseConfig } from './ifElseConfig'

export const MAIN_NODE_WIDTH = 260
export const SUBFLOW_NODE_WIDTH = 240
export const START_NODE_HEIGHT = 96
export const DEFAULT_NODE_HEIGHT = 112
export const CODE_EXECUTOR_NODE_HEIGHT = 128
export const HTTP_REQUEST_NODE_HEIGHT = 116
export const HUMAN_IN_LOOP_NODE_HEIGHT = 152
export const MAIN_NODE_HANDLE_TOP = 28
export const CONTAINER_NODE_HANDLE_TOP = 20
export const SUBFLOW_NODE_HANDLE_TOP = 20
export const IF_ELSE_HANDLE_BASE_TOP = 50
export const IF_ELSE_HANDLE_STEP = 28

const CONTAINER_MIN_WIDTH = 420
const CONTAINER_MAX_WIDTH = 1120
const CONTAINER_MIN_HEIGHT = 320
const CONTAINER_MIN_CANVAS_WIDTH = 396
const CONTAINER_MIN_CANVAS_HEIGHT = 220
const CONTAINER_OUTER_HORIZONTAL_GAP = 24
const CONTAINER_OUTER_VERTICAL_GAP = 80
const CONTAINER_HORIZONTAL_PADDING = 40
const CONTAINER_TOP_PADDING = 48
const CONTAINER_BOTTOM_PADDING = 104

type LayoutNode = {
  nodeType: ContainerBodyNodeType
  positionX: number
  positionY: number
  config: Record<string, unknown> | null
}

type SupportedNodeType = NodeType | ContainerBodyNodeType

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

export function getNodeOutputHandleIds(
  nodeType: SupportedNodeType,
  config?: Record<string, unknown> | null,
): string[] {
  if (nodeType === 'output') return []
  if (nodeType === 'if_else') {
    const normalized = normalizeIfElseConfig(config ?? {})
    return [...normalized.branches.map((item) => item.id), normalized.elseHandle || 'else']
  }
  if (nodeType === 'human_in_loop') {
    return ['approved', 'rejected']
  }
  return ['output']
}

export function isMultiOutputNodeType(
  nodeType: SupportedNodeType,
  config?: Record<string, unknown> | null,
): boolean {
  return getNodeOutputHandleIds(nodeType, config).length > 1
}

export function getIfElseNodeHeight(config?: Record<string, unknown> | null): number {
  const normalized = normalizeIfElseConfig(config ?? {})
  const branchCount = Math.max(2, normalized.branches.length + 1)
  return Math.max(160, IF_ELSE_HANDLE_BASE_TOP + branchCount * IF_ELSE_HANDLE_STEP + 24)
}

function getNodeHeight(nodeType: SupportedNodeType, config?: Record<string, unknown> | null): number {
  if (nodeType === 'start') return START_NODE_HEIGHT
  if (nodeType === 'if_else') return getIfElseNodeHeight(config)
  if (nodeType === 'code_executor') return CODE_EXECUTOR_NODE_HEIGHT
  if (nodeType === 'http_request') return HTTP_REQUEST_NODE_HEIGHT
  if (nodeType === 'human_in_loop') return HUMAN_IN_LOOP_NODE_HEIGHT
  return DEFAULT_NODE_HEIGHT
}

export function getMainNodeFrame(
  nodeType: SupportedNodeType,
  config?: Record<string, unknown> | null,
): { width: number; height: number } {
  return {
    width: MAIN_NODE_WIDTH,
    height: getNodeHeight(nodeType, config),
  }
}

export function getSubflowNodeFrame(
  nodeType: ContainerBodyNodeType,
  config?: Record<string, unknown> | null,
): { width: number; height: number } {
  return {
    width: SUBFLOW_NODE_WIDTH,
    height: getNodeHeight(nodeType, config),
  }
}

export type ContainerNodeSize = {
  width: number
  height: number
  canvasWidth: number
  canvasHeight: number
}

type EstimateMainNodeSizeOptions = {
  preferMeasured?: boolean
}

export function estimateContainerNodeSizeFromConfig(config: unknown): ContainerNodeSize {
  const nodes = normalizeLayoutNodes(config)
  if (nodes.length === 0) {
    const width = CONTAINER_MIN_WIDTH
    return {
      width,
      height: CONTAINER_MIN_HEIGHT,
      canvasWidth: Math.max(CONTAINER_MIN_CANVAS_WIDTH, width - CONTAINER_OUTER_HORIZONTAL_GAP),
      canvasHeight: CONTAINER_MIN_CANVAS_HEIGHT,
    }
  }

  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY

  nodes.forEach((node) => {
    const size = getSubflowNodeFrame(node.nodeType, node.config)
    minX = Math.min(minX, node.positionX)
    minY = Math.min(minY, node.positionY)
    maxX = Math.max(maxX, node.positionX + size.width)
    maxY = Math.max(maxY, node.positionY + size.height)
  })

  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
    const width = CONTAINER_MIN_WIDTH
    return {
      width,
      height: CONTAINER_MIN_HEIGHT,
      canvasWidth: Math.max(CONTAINER_MIN_CANVAS_WIDTH, width - CONTAINER_OUTER_HORIZONTAL_GAP),
      canvasHeight: CONTAINER_MIN_CANVAS_HEIGHT,
    }
  }

  const contentWidth = Math.max(0, maxX - minX)
  const contentHeight = Math.max(0, maxY - minY)
  const desiredCanvasWidth = Math.ceil(contentWidth + CONTAINER_HORIZONTAL_PADDING * 2)
  const desiredCanvasHeight = Math.ceil(contentHeight + CONTAINER_TOP_PADDING + CONTAINER_BOTTOM_PADDING)
  const canvasWidth = Math.min(
    CONTAINER_MAX_WIDTH - CONTAINER_OUTER_HORIZONTAL_GAP,
    Math.max(CONTAINER_MIN_CANVAS_WIDTH, desiredCanvasWidth),
  )
  const canvasHeight = Math.max(CONTAINER_MIN_CANVAS_HEIGHT, desiredCanvasHeight)
  const width = Math.min(
    CONTAINER_MAX_WIDTH,
    Math.max(CONTAINER_MIN_WIDTH, canvasWidth + CONTAINER_OUTER_HORIZONTAL_GAP),
  )
  const height = Math.max(CONTAINER_MIN_HEIGHT, canvasHeight + CONTAINER_OUTER_VERTICAL_GAP)

  return { width, height, canvasWidth, canvasHeight }
}

export function estimateMainNodeSize(node: Node<WfNodeData>): { width: number; height: number } {
  return estimateMainNodeSizeWithOptions(node)
}

export function estimateMainNodeSizeWithOptions(
  node: Node<WfNodeData>,
  options?: EstimateMainNodeSizeOptions,
): { width: number; height: number } {
  const preferMeasured = options?.preferMeasured ?? true
  if (node.data.nodeType === 'iteration' || node.data.nodeType === 'loop') {
    const size = estimateContainerNodeSizeFromConfig(node.data.config ?? null)
    if (!preferMeasured) {
      return { width: size.width, height: size.height }
    }
  }

  const measured = node as Node<WfNodeData> & {
    measured?: { width?: number; height?: number }
    width?: number
    height?: number
  }
  const measuredWidth = Number(measured.measured?.width ?? measured.width)
  const measuredHeight = Number(measured.measured?.height ?? measured.height)
  if (
    preferMeasured &&
    Number.isFinite(measuredWidth) &&
    Number.isFinite(measuredHeight) &&
    measuredWidth > 0 &&
    measuredHeight > 0
  ) {
    return { width: measuredWidth, height: measuredHeight }
  }

  if (node.data.nodeType === 'iteration' || node.data.nodeType === 'loop') {
    const size = estimateContainerNodeSizeFromConfig(node.data.config ?? null)
    return { width: size.width, height: size.height }
  }

  return getMainNodeFrame(node.data.nodeType, (node.data.config ?? null) as Record<string, unknown> | null)
}
