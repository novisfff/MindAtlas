import type { Node, Edge, Viewport } from '@xyflow/react'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import type { WorkflowInput as DraftWorkflowInput } from '../../api/workflow'
import type {
  WorkflowInput,
  WorkflowNodeInput,
  WorkflowEdgeInput,
  NodeType,
  ConditionExpression,
} from '../../api/workflow'
import type { AssistantWorkflow } from '../../api/workflows'
import { normalizeIfElseConfig } from './ifElseConfig'
import { buildLabelMaps, ensureWorkflowUniqueLabels, defaultLabelForNodeType } from './labelUtils'
import { toDisplayReferencesFromStored, toStoredReferencesFromDisplay } from './referenceTransform'
import { normalizeStartNodeConfig } from './startNodeConfig'

type EdgeConditionType = 'expression' | 'default' | null | undefined

type SourceNodeLike = {
  nodeType?: string
  config?: Record<string, unknown> | null
} | null | undefined

type TargetNodeLike = {
  nodeType?: string
} | null | undefined

const CONTAINER_INPUT_HANDLE_ID = 'container_input'
const CONTAINER_OUTPUT_HANDLE_ID = 'container_output'

function resolveEdgeSourceHandle(
  sourceNode: SourceNodeLike,
  rawSourceHandle: unknown,
  conditionType: EdgeConditionType,
): string {
  const sourceHandle = String(rawSourceHandle ?? '').trim()
  if (sourceNode?.nodeType === 'iteration' || sourceNode?.nodeType === 'loop') {
    if (!sourceHandle || sourceHandle === 'output' || sourceHandle === CONTAINER_OUTPUT_HANDLE_ID) {
      return 'output'
    }
    return sourceHandle
  }
  if (sourceNode?.nodeType !== 'if_else') {
    if (sourceHandle === CONTAINER_OUTPUT_HANDLE_ID) return 'output'
    return sourceHandle || 'output'
  }
  const normalized = normalizeIfElseConfig((sourceNode.config ?? null) as Record<string, unknown> | null)
  const validHandles = new Set<string>([
    ...normalized.branches.map((branch) => branch.id),
    normalized.elseHandle || 'else',
  ])
  if (sourceHandle && sourceHandle !== 'output' && validHandles.has(sourceHandle)) {
    return sourceHandle
  }
  if (conditionType === 'default') {
    return normalized.elseHandle || 'else'
  }
  return normalized.branches[0]?.id || normalized.elseHandle || 'else'
}

function resolveEdgeTargetHandle(targetNode: TargetNodeLike, rawTargetHandle: unknown): string {
  const targetHandle = String(rawTargetHandle ?? '').trim()
  if (targetNode?.nodeType === 'iteration' || targetNode?.nodeType === 'loop') {
    if (!targetHandle || targetHandle === 'input' || targetHandle === CONTAINER_INPUT_HANDLE_ID) {
      return 'input'
    }
    return targetHandle
  }
  if (targetHandle === CONTAINER_INPUT_HANDLE_ID) return 'input'
  return targetHandle || 'input'
}

/**
 * Convert React Flow nodes/edges to API WorkflowInput format.
 */
export function serializeToWorkflowInput(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  viewport?: Viewport,
): WorkflowInput {
  const { labelToId, nodeIds } = buildLabelMaps(nodes)
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const apiNodes: WorkflowNodeInput[] = nodes.map((n) => ({
    ...(n.data.nodeType === 'if_else'
      ? {
          config: normalizeIfElseConfig(
            (toStoredReferencesFromDisplay(n.data.config ?? null, labelToId, nodeIds) ?? null) as Record<string, unknown> | null,
          ),
        }
      : {
          config: (toStoredReferencesFromDisplay(n.data.config ?? null, labelToId, nodeIds) ?? null) as Record<string, unknown> | null,
        }),
    nodeId: n.id,
    nodeType: n.data.nodeType,
    label: n.data.label || '',
    positionX: n.position.x,
    positionY: n.position.y,
  }))

  const apiEdges: WorkflowEdgeInput[] = edges.map((e: Edge) => ({
    edgeId: e.id,
    sourceNodeId: e.source,
    targetNodeId: e.target,
    sourceHandle: resolveEdgeSourceHandle(
      nodeById.get(e.source)
        ? {
          nodeType: nodeById.get(e.source)!.data.nodeType,
          config: (nodeById.get(e.source)!.data.config ?? null) as Record<string, unknown> | null,
        }
        : null,
      e.sourceHandle,
      (e.data?.conditionType as EdgeConditionType) ?? null,
    ),
    targetHandle: resolveEdgeTargetHandle(
      nodeById.get(e.target)
        ? {
          nodeType: nodeById.get(e.target)!.data.nodeType,
        }
        : null,
      e.targetHandle,
    ),
    conditionType: (e.data?.conditionType as 'expression' | 'default' | null) ?? null,
    conditionExpr: (e.data?.conditionExpr as ConditionExpression | null) ?? null,
    label: typeof e.label === 'string' ? e.label : null,
  }))

  return {
    nodes: apiNodes,
    edges: apiEdges,
    viewport: viewport ? { x: viewport.x, y: viewport.y, zoom: viewport.zoom } : null,
  }
}

/**
 * Convert API skill response (with nodes/edges) to React Flow format.
 */

export function deserializeFromWorkflow(workflow: AssistantWorkflow): {
  nodes: Node<WfNodeData>[]
  edges: Edge[]
  viewport?: Viewport
} {
  return deserializeFromWorkflowLike({
    nodes: workflow.nodes,
    edges: workflow.edges,
    workflowViewport: workflow.workflowViewport,
  })
}

export function deserializeFromWorkflowInput(workflow: DraftWorkflowInput): {
  nodes: Node<WfNodeData>[]
  edges: Edge[]
  viewport?: Viewport
} {
  return deserializeFromWorkflowLike({
    nodes: (workflow.nodes ?? []) as WorkflowLike['nodes'],
    edges: (workflow.edges ?? []) as WorkflowLike['edges'],
    workflowViewport: workflow.viewport ?? null,
  })
}

type WorkflowLike = {
  nodes?: Array<{
    nodeId: string
    nodeType: string
    label: string
    positionX: number
    positionY: number
    config: unknown
  }>
  edges?: Array<{
    edgeId: string
    sourceNodeId: string
    targetNodeId: string
    sourceHandle: string
    targetHandle: string
    conditionType: string | null
    conditionExpr: unknown
    label: string | null
  }>
  workflowViewport?: { x: number; y: number; zoom: number } | null
}

function deserializeFromWorkflowLike(source: WorkflowLike): {
  nodes: Node<WfNodeData>[]
  edges: Edge[]
  viewport?: Viewport
} {
  const apiNodes = source.nodes ?? []
  const apiEdges = source.edges ?? []
  const nodeById = new Map(apiNodes.map((node) => [node.nodeId, node]))

  type SkillNode = (typeof apiNodes)[number]
  type SkillEdge = (typeof apiEdges)[number]

  const nodes: Node<WfNodeData>[] = apiNodes.map((n: SkillNode) => ({
    ...(n.nodeType === 'if_else'
      ? {
          data: {
            nodeType: n.nodeType as NodeType,
            label: n.label || '',
            config: normalizeIfElseConfig((n.config as Record<string, unknown>) ?? null),
          },
        }
      : n.nodeType === 'start'
        ? {
            data: {
              nodeType: n.nodeType as NodeType,
              label: n.label || '',
              config: normalizeStartNodeConfig(n.config),
            },
          }
        : {
            data: {
              nodeType: n.nodeType as NodeType,
              label: n.label || '',
              config: (n.config as Record<string, unknown>) ?? null,
            },
          }),
    id: n.nodeId,
    type: n.nodeType,
    position: { x: n.positionX, y: n.positionY },
  }))

  const edges: Edge[] = apiEdges.map((e: SkillEdge) => ({
    id: e.edgeId,
    source: e.sourceNodeId,
    target: e.targetNodeId,
    sourceHandle: resolveEdgeSourceHandle(
      nodeById.get(e.sourceNodeId)
        ? {
          nodeType: nodeById.get(e.sourceNodeId)!.nodeType,
          config: (nodeById.get(e.sourceNodeId)!.config as Record<string, unknown>) ?? null,
        }
        : null,
      e.sourceHandle,
      (e.conditionType as EdgeConditionType) ?? null,
    ),
    targetHandle: resolveEdgeTargetHandle(
      nodeById.get(e.targetNodeId)
        ? {
          nodeType: nodeById.get(e.targetNodeId)!.nodeType,
        }
        : null,
      e.targetHandle,
    ),
    type: 'workflowBezier',
    animated: false,
    data: {
      conditionType: e.conditionType,
      conditionExpr: e.conditionExpr,
    },
  }))

  const viewport = source.workflowViewport
    ? { x: source.workflowViewport.x, y: source.workflowViewport.y, zoom: source.workflowViewport.zoom }
    : undefined

  // If no nodes exist, create a default start -> llm -> output layout.
  if (nodes.length === 0) {
    nodes.push(
      {
        id: 'start',
        type: 'start',
        position: { x: 120, y: 220 },
        data: {
          nodeType: 'start',
          label: defaultLabelForNodeType('start'),
          config: normalizeStartNodeConfig(null),
        },
      },
      {
        id: 'llm_1',
        type: 'llm',
        position: { x: 460, y: 220 },
        data: {
          nodeType: 'llm',
          label: defaultLabelForNodeType('llm'),
          config: {
            outputMode: 'text',
            userInput: '{{start.user_input}}',
            modelSource: 'default',
          },
        },
      },
      {
        id: 'output_1',
        type: 'output',
        position: { x: 800, y: 220 },
        data: {
          nodeType: 'output',
          label: defaultLabelForNodeType('output'),
          config: {
            outputMode: 'text',
            textTemplate: '{{llm_1.response}}',
          },
        },
      },
    )
    edges.push({
      id: 'edge_start_llm',
      source: 'start',
      target: 'llm_1',
      sourceHandle: 'output',
      targetHandle: 'input',
      type: 'workflowBezier',
    })
    edges.push({
      id: 'edge_llm_output',
      source: 'llm_1',
      target: 'output_1',
      sourceHandle: 'output',
      targetHandle: 'input',
      type: 'workflowBezier',
    })
  }

  const uniqueNodes = ensureWorkflowUniqueLabels(nodes)
  const { idToLabel } = buildLabelMaps(uniqueNodes)
  const displayNodes: Node<WfNodeData>[] = uniqueNodes.map((node) => ({
    ...node,
    data: {
      ...node.data,
      config: (toDisplayReferencesFromStored(node.data.config ?? null, idToLabel) ?? null) as Record<string, unknown> | null,
    },
  }))

  return { nodes: displayNodes, edges, viewport }
}
