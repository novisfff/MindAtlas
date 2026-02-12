import type { Edge, Node } from '@xyflow/react'
import type { InputParam } from '../../api/tools'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import type { WorkflowToolDefinition } from './types'

const SYS_REFERENCE_PARAMS: InputParam[] = [
  {
    name: 'sys.date',
    referencePath: 'sys.date',
    itemLabel: 'date',
    groupKey: 'sys',
    groupLabel: 'System Variables',
    paramType: 'string',
    required: false,
    description: 'System date (YYYY-MM-DD)',
  },
  {
    name: 'sys.datetime',
    referencePath: 'sys.datetime',
    itemLabel: 'datetime',
    groupKey: 'sys',
    groupLabel: 'System Variables',
    paramType: 'string',
    required: false,
    description: 'System datetime (ISO8601)',
  },
  {
    name: 'sys.conversation_id',
    referencePath: 'sys.conversation_id',
    itemLabel: 'conversation_id',
    groupKey: 'sys',
    groupLabel: 'System Variables',
    paramType: 'string',
    required: false,
    description: 'Current conversation id',
  },
]

function normalizeOutputMode(raw: unknown): 'text' | 'structured' {
  const mode = String(raw ?? 'text').trim().toLowerCase()
  if (mode === 'structured' || mode === 'json') return 'structured'
  return 'text'
}

function collectUpstreamNodeIds(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  currentNodeId: string,
): string[] {
  const nodeSet = new Set(nodes.map((n) => n.id))
  const reverseAdj = new Map<string, Set<string>>()

  edges.forEach((edge) => {
    if (!nodeSet.has(edge.source) || !nodeSet.has(edge.target)) return
    if (!reverseAdj.has(edge.target)) reverseAdj.set(edge.target, new Set<string>())
    reverseAdj.get(edge.target)!.add(edge.source)
  })

  const visited = new Set<string>()
  const queue: string[] = [currentNodeId]

  while (queue.length > 0) {
    const nodeId = queue.shift()!
    const sources = reverseAdj.get(nodeId)
    if (!sources) continue
    sources.forEach((sourceId) => {
      if (visited.has(sourceId)) return
      visited.add(sourceId)
      queue.push(sourceId)
    })
  }

  const upstream = nodes
    .map((n) => n.id)
    .filter((id) => visited.has(id) && id !== currentNodeId)

  return upstream
}

function buildNodeOutputFields(
  node: Node<WfNodeData>,
  toolByName: Map<string, WorkflowToolDefinition>,
): string[] {
  const cfg = (node.data.config ?? {}) as Record<string, unknown>
  if (node.data.nodeType === 'start') return ['user_input']

  if (node.data.nodeType === 'llm') {
    const mode = normalizeOutputMode(cfg.outputMode)
    if (mode === 'text') return ['response']

    const fields = Array.isArray(cfg.outputFields)
      ? cfg.outputFields
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '') : ''))
        .filter((name) => !!name)
      : []
    return ['response', ...fields]
  }

  if (node.data.nodeType === 'tool') {
    const toolName = String(cfg.toolName ?? '').trim()
    const tool = toolByName.get(toolName)
    const outputNames = tool?.outputParams?.map((item) => item.name).filter(Boolean) ?? []
    return ['result', ...outputNames]
  }

  if (node.data.nodeType === 'parameter_extractor') {
    const fields = Array.isArray(cfg.outputFields)
      ? cfg.outputFields
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '') : ''))
        .filter((name) => !!name)
      : []
    return fields.length > 0 ? fields : ['text']
  }

  if (node.data.nodeType === 'if_else') return ['handle']
  return ['text']
}

function nodeDisplayLabel(node: Node<WfNodeData>): string {
  const label = String(node.data.label ?? '').trim()
  return label || node.id
}

export function buildWorkflowReferenceParams(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  currentNodeId: string,
  tools: WorkflowToolDefinition[],
): InputParam[] {
  const upstreamIds = collectUpstreamNodeIds(nodes, edges, currentNodeId)
  const upstreamSet = new Set(upstreamIds)
  const toolByName = new Map(tools.map((tool) => [tool.name, tool]))
  const params: InputParam[] = []
  const seen = new Set<string>()

  nodes.forEach((node) => {
    if (!upstreamSet.has(node.id) && node.data.nodeType !== 'start') return
    const fields = buildNodeOutputFields(node, toolByName)
    const groupLabel = nodeDisplayLabel(node)
    const groupKey = `node:${node.id}`
    fields.forEach((field) => {
      const path = `${groupLabel}.${field}`
      if (seen.has(path)) return
      seen.add(path)
      params.push({
        name: path,
        referencePath: path,
        groupKey,
        groupLabel,
        itemLabel: field,
        paramType: field === 'result' ? 'object' : 'string',
        required: false,
        description: `${groupLabel}.${field}`,
      })
    })
  })

  SYS_REFERENCE_PARAMS.forEach((param) => {
    const path = param.referencePath || param.name
    if (seen.has(path)) return
    seen.add(path)
    params.push(param)
  })

  return params
}
