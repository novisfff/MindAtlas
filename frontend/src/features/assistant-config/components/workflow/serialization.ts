import type { Node, Edge, Viewport } from '@xyflow/react'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import type {
  WorkflowInput,
  WorkflowNodeInput,
  WorkflowEdgeInput,
  NodeType,
  ConditionExpression,
} from '../../api/workflow'
import type { AssistantSkill } from '../../api/skills'
import { normalizeIfElseConfig } from './ifElseConfig'
import { buildLabelMaps, ensureWorkflowUniqueLabels } from './labelUtils'
import { toDisplayReferencesFromStored, toStoredReferencesFromDisplay } from './referenceTransform'

/**
 * Convert React Flow nodes/edges to API WorkflowInput format.
 */
export function serializeToWorkflowInput(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  viewport?: Viewport,
): WorkflowInput {
  const { labelToId, nodeIds } = buildLabelMaps(nodes)
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
    sourceHandle: e.sourceHandle || 'output',
    targetHandle: e.targetHandle || 'input',
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
export function deserializeFromSkill(skill: AssistantSkill): {
  nodes: Node<WfNodeData>[]
  edges: Edge[]
  viewport?: Viewport
} {
  const apiNodes = skill.nodes ?? []
  const apiEdges = skill.edges ?? []

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
    sourceHandle: e.sourceHandle || 'output',
    targetHandle: e.targetHandle || 'input',
    type: 'workflowBezier',
    animated: false,
    data: {
      conditionType: e.conditionType,
      conditionExpr: e.conditionExpr,
    },
  }))

  const viewport = skill.workflowViewport
    ? { x: skill.workflowViewport.x, y: skill.workflowViewport.y, zoom: skill.workflowViewport.zoom }
    : undefined

  // If no nodes exist, create a default start + llm(output) layout
  if (nodes.length === 0) {
    nodes.push(
      {
        id: 'start',
        type: 'start',
        position: { x: 120, y: 220 },
        data: { nodeType: 'start', label: 'Start', config: null },
      },
      {
        id: 'llm_1',
        type: 'llm',
        position: { x: 460, y: 220 },
        data: {
          nodeType: 'llm',
          label: 'LLM',
          config: {
            outputMode: 'text',
            userInput: '{{start.user_input}}',
            isOutput: true,
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
